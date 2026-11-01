import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import successor_runtime as runtime


def test_successor_claims_and_binds_before_start(monkeypatch):
    events = []
    allocation, worker_id = 'a' * 32, 'd' * 64
    class Store:
        def claim_restart(self, *args): events.append('claim'); return allocation
        def bind_restart_worker(self, *args): events.append(('bind', args[-1]))
    config = {'workspace': '/old', 'worker_command': ['server'], 'proxy_command': ['proxy'],
              'worker_image': 'worker', 'proxy_image': 'proxy', 'worker_environment': [], 'uid': 1000}
    source = (allocation, {'proxy_id': 'p'}, {}, {'Id': 'n'}, {}, 'socket', 'budget', 'anchor')
    monkeypatch.setattr(runtime, '_source', lambda *_: source)
    monkeypatch.setattr(runtime, '_workspace', lambda *_: None)
    monkeypatch.setattr(runtime, 'validate_identity', lambda *args, **kwargs: events.append('validate'))
    monkeypatch.setattr(runtime, '_running_lifecycle', lambda _: {'Pid': 1, 'StartedAt': 'then'})
    monkeypatch.setattr(runtime, 'inspect', lambda *_: {'Id': worker_id, 'State': {'Running': False},
                                                        'HostConfig': {'RestartPolicy': {'Name': 'no'}}})
    def run(args):
        events.append(args)
        return worker_id if args[0] == 'create' else ''
    monkeypatch.setattr(runtime, '_run', run)
    result = runtime.provision_successor(Store(), 'old', 'hash', 'cid', 'new', 'new-hash', 'new-cid',
                                         configuration=config, workspace='/new', baseline='a' * 40)
    assert result['proxy_id'] == 'p'
    start = next(i for i, value in enumerate(events) if isinstance(value, list) and value[0] == 'start')
    create = next(i for i, value in enumerate(events) if isinstance(value, list) and value[0] == 'create')
    assert events.index('claim') < create < events.index(('bind', None)) < start
    create_args = events[create]
    assert '--network' in create_args and create_args[create_args.index('--network') + 1] == 'none'
    assert 'type=volume,src=socket,dst=/gateway,readonly' in create_args
    assert all('src=budget' not in value and 'src=anchor' not in value for value in create_args)
    assert not any(isinstance(value, list) and value[0] in ('volume', 'network') for value in events)


def test_claim_failure_prevents_creation(monkeypatch):
    class Store:
        def claim_restart(self, *args): raise ValueError('already claimed')
    monkeypatch.setattr(runtime, '_workspace', lambda *_: None)
    monkeypatch.setattr(runtime, '_source', lambda *_: ('a'*32, {}, {}, {}, {}, '', '', ''))
    monkeypatch.setattr(runtime, '_run', lambda *_: pytest.fail('docker side effect before claim'))
    with pytest.raises(ValueError):
        runtime.provision_successor(Store(), 'old', 'h', 'c', 'new', 'h2', 'c2', configuration={'workspace': '/old'}, workspace='/new', baseline='a'*40)


def test_workspace_requires_standalone_clean_nonhardlinked_clone(tmp_path):
    old = tmp_path / 'old'; new = tmp_path / 'new'
    old.mkdir()
    subprocess.run(['git', 'init', '-q', str(old)], check=True)
    (old / 'data').write_text('base')
    subprocess.run(['git', '-C', str(old), 'add', 'data'], check=True)
    subprocess.run(['git', '-C', str(old), '-c', 'user.name=Probe', '-c', 'user.email=probe@example.invalid', 'commit', '-qm', 'base'], check=True)
    baseline = subprocess.run(['git', '-C', str(old), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(['git', 'clone', '--no-hardlinks', '-q', str(old), str(new)], check=True)
    os.chmod(new, 0o700)
    runtime._workspace(str(new), str(old), baseline)
    (new / 'data').unlink()
    os.link(old / 'data', new / 'data')
    with pytest.raises(runtime.ProvisionError, match='hardlinked'):
        runtime._workspace(str(new), str(old), baseline)


def test_workspace_rejects_ignored_files_before_successor(tmp_path):
    old = tmp_path / 'old'; new = tmp_path / 'new'
    old.mkdir()
    subprocess.run(['git', 'init', '-q', str(old)], check=True)
    (old / '.gitignore').write_text('local-state/\n')
    subprocess.run(['git', '-C', str(old), 'add', '.gitignore'], check=True)
    subprocess.run(['git', '-C', str(old), '-c', 'user.name=Probe', '-c', 'user.email=probe@example.invalid', 'commit', '-qm', 'base'], check=True)
    baseline = subprocess.run(['git', '-C', str(old), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(['git', 'clone', '--no-hardlinks', '-q', str(old), str(new)], check=True)
    os.chmod(new, 0o700)
    (new / 'local-state').mkdir()
    (new / 'local-state' / 'state').write_text('leftover')
    with pytest.raises(runtime.ProvisionError, match='dirty'):
        runtime._workspace(str(new), str(old), baseline)


def test_source_reuses_and_revalidates_original_private_telemetry_volume(monkeypatch, tmp_path):
    import copy
    from test_provision_check import telemetry_fixture
    w,p,k,telemetry = telemetry_fixture(tmp_path)
    allocation=k['allocation_id'];prefix='factory-'+allocation
    names={k['socket_volume']:prefix+'-socket',k['budget_volume']:prefix+'-budget',k['anchor_volume']:prefix+'-anchor',telemetry:telemetry}
    for container in (w,p):
        for mount in container['Mounts']:
            if mount.get('Name') in names:mount['Name']=names[mount['Name']]
    volumes={names[name]:dict(record,Name=names[name]) for name,record in k['volume_records'].items()}
    w['Id']='1'*64;p['Id']='2'*64
    w['State'].update(Running=False,Restarting=False,StartedAt='original')
    w['HostConfig']['RestartPolicy']={'Name':'no'}
    p['State'].update(Running=True,Pid=42,StartedAt='proxy-original')
    command=['proxy','--attempt-id','attempt','--gateway-ip','100.92.236.42',
             '--budget-db','/budget/b.db','--budget-anchor','/anchor/b.json','--telemetry-dir','/telemetry']
    p['Config']['Entrypoint']=['proxy'];p['Config']['Cmd']=command[1:]
    config={'workspace':str(tmp_path),'worker_image':w['Image'],'proxy_image':p['Image'],
            'worker_command':k['worker_command'],'proxy_command':command,'worker_environment':[],
            'firewall':{'helper_image':'sha256:'+'3'*64,'gateway_ip':'100.92.236.42'}}
    fields=('Name','CreatedAt','Driver','Scope','Labels','Options','Mountpoint')
    binding={'worker_id':w['Id'],'proxy_id':p['Id'],'network_id':k['proxy_network'],
             'workspace_baseline':'a'*40,'worker_lifecycle':{'StartedAt':'original'},
             'proxy_lifecycle':{'Pid':42,'StartedAt':'proxy-original'},
             'volumes':{name:{field:record.get(field) for field in fields} for name,record in volumes.items()}}
    class Store:
        def lookup_allocation(self,*args):return allocation,copy.deepcopy(binding)
    def inspect(kind,name):
        if kind=='container':return w if name==w['Id'] else p
        if kind=='network':return {'Id':k['proxy_network']}
        return volumes[name]
    monkeypatch.setattr(runtime,'inspect',inspect)
    monkeypatch.setattr(runtime,'verify_proxy_firewall',lambda *args:None)
    result=runtime._source(Store(),'attempt','request','conversation',config,'a'*40)
    assert telemetry in result[4]
    volumes[telemetry]['CreatedAt']='replacement'
    with pytest.raises(runtime.ProvisionError,match='allocation changed'):
        runtime._source(Store(),'attempt','request','conversation',config,'a'*40)
