import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
import provision_runtime as runtime


def _inspect_volume(kind, name):
    if kind == 'volume':
        parts = name.split('-')
        return {'Name': name, 'Driver': 'local', 'Scope': 'local', 'Options': None,
                'Labels': {'factory.allocation': parts[1], 'factory.role': parts[-1]}}
    if kind=='container':return {'Id':name,'State':{'Running':True}}
    return {'Id': name, 'Config': {'Env': []}}


def test_invalid_image_fails_before_docker(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, 'inspect', lambda *_: pytest.fail('must not inspect'))
    with pytest.raises(runtime.ProvisionError):
        runtime.provision(worker_image='latest', proxy_image='bad', helper_image='bad', worker_command=['x'], proxy_command=['y'], workspace=str(tmp_path), uid=1000, worker_environment=[], gateway_key=b'key')


def test_failure_cleans_only_created_resources_and_retains_budget(monkeypatch, tmp_path):
    calls = []
    image = 'sha256:' + '1' * 64
    monkeypatch.setattr(runtime, 'inspect', _inspect_volume)
    def fake_run(args, **kwargs):
        calls.append(args)
        if args[0] == 'run':
            raise runtime.ProvisionError('initializer failed')
        return ''
    monkeypatch.setattr(runtime, '_run', fake_run)
    with pytest.raises(runtime.ProvisionError, match='budget and anchor volumes retained'):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image, worker_command=['x'], proxy_command=['y'], workspace=str(tmp_path), uid=1000, worker_environment=[], gateway_key=b'secret-value')
    assert not any('secret-value' in str(call) for call in calls)
    removed = [call for call in calls if len(call) >= 2 and call[1] == 'rm']
    assert any(c[0] == 'network' for c in removed)
    assert any(c[0] == 'volume' and c[-1].endswith('-socket') for c in removed)
    assert not any(c[0] == 'volume' and c[-1].endswith('-budget') for c in removed)


def test_credential_is_not_released_when_preflight_fails(monkeypatch, tmp_path):
    import io
    image = 'sha256:' + '1' * 64
    class FakeProcess:
        def __init__(self): self.stdin = io.BytesIO(); self.terminated = False
        def terminate(self): self.terminated = True
        def poll(self): return None
    process = FakeProcess()
    monkeypatch.setattr(runtime, 'inspect', _inspect_volume)
    monkeypatch.setattr(runtime, '_run', lambda *args, **kwargs: '')
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *args, **kwargs: process)
    def reject(*args, **kwargs): raise ValueError('unsafe topology')
    monkeypatch.setattr(runtime, 'validate_identity', reject)
    with pytest.raises(runtime.ProvisionError):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy'], workspace=str(tmp_path),
                          uid=1000, worker_environment=[], gateway_key=b'test-secret')
    assert process.stdin.getvalue() == b''
    assert process.terminated


def test_provision_crash_never_recreates_budget_for_same_attempt(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from guarded_store import GuardedAttemptStore
    ledger, anchor = tmp_path / 'ledger', tmp_path / 'anchor'
    ledger.mkdir(); anchor.mkdir()
    db, marker = ledger / 'attempts', anchor / 'watermark'
    store = GuardedAttemptStore.initialize(db, marker)
    store.reserve('unit:0', 'request-hash', 'conversation')
    calls = []
    def crash(**kwargs):
        calls.append(kwargs['allocation_id'])
        raise runtime.ProvisionError('crash after possible resource creation')
    monkeypatch.setattr(runtime, 'provision', crash)
    monkeypatch.setattr(runtime.subprocess, 'run', lambda args, **kwargs: SimpleNamespace(stdout=(str(tmp_path) + '\n') if '--show-toplevel' in args else ('a' * 40 + '\n') if '--verify' in args else ''))
    config = {'worker_image': 'sha256:' + '1' * 64, 'gateway_key': b'secret', 'workspace': str(tmp_path), 'workspace_baseline': 'a' * 40}
    with pytest.raises(runtime.ProvisionError):
        runtime.provision_once(store, 'unit:0', 'request-hash', 'conversation', **config)
    with pytest.raises(runtime.ProvisionError, match='already reserved'):
        runtime.provision_once(GuardedAttemptStore(db, marker), 'unit:0', 'request-hash', 'conversation', **config)
    assert len(calls) == 1
    with pytest.raises(runtime.ProvisionError, match='reservation failed'):
        runtime.provision_once(store, 'unit:0', 'request-hash', 'conversation', worker_image='changed', gateway_key=b'secret', workspace=str(tmp_path), workspace_baseline='a' * 40)
    assert len(calls) == 1


def test_unreserved_attempt_cannot_provision(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from guarded_store import GuardedAttemptStore
    ledger, anchor = tmp_path / 'ledger', tmp_path / 'anchor'
    ledger.mkdir(); anchor.mkdir()
    store = GuardedAttemptStore.initialize(ledger / 'attempts', anchor / 'watermark')
    monkeypatch.setattr(runtime, 'provision', lambda **_: pytest.fail('must not provision'))
    monkeypatch.setattr(runtime.subprocess, 'run', lambda args, **kwargs: SimpleNamespace(stdout=(str(tmp_path) + '\n') if '--show-toplevel' in args else ('a' * 40 + '\n') if '--verify' in args else ''))
    with pytest.raises(runtime.ProvisionError, match='reservation failed'):
        runtime.provision_once(store, 'missing', 'hash', 'conversation', gateway_key=b'secret', workspace=str(tmp_path), workspace_baseline='a' * 40)


def test_allocation_binding_is_immutable_and_requires_original_specification(tmp_path):
    from guarded_store import GuardedAttemptStore, StoreQuarantined
    ledger, anchor = tmp_path / 'ledger', tmp_path / 'anchor'
    ledger.mkdir(); anchor.mkdir()
    store = GuardedAttemptStore.initialize(ledger / 'db', anchor / 'watermark')
    store.reserve('unit:0', 'request', 'conversation')
    _, allocation = store.reserve_allocation('unit:0', 'request', 'conversation', 'spec')
    with pytest.raises(StoreQuarantined):
        store.lookup_allocation('unit:0', 'request', 'conversation', 'spec')
    binding = {'worker_id': 'a' * 64, 'workspace_baseline': 'b' * 40}
    store.bind_allocation('unit:0', 'request', 'conversation', 'spec', allocation, binding)
    assert store.lookup_allocation('unit:0', 'request', 'conversation', 'spec') == (allocation, binding)
    with pytest.raises(StoreQuarantined):
        store.bind_allocation('unit:0', 'request', 'conversation', 'spec', allocation, {'worker_id': 'c' * 64})
    with pytest.raises(StoreQuarantined):
        store.lookup_allocation('unit:0', 'request', 'conversation', 'changed')


def test_reconciliation_rejects_replaced_container(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from guarded_store import GuardedAttemptStore
    ledger, anchor = tmp_path / 'ledger', tmp_path / 'anchor'
    ledger.mkdir(); anchor.mkdir()
    store = GuardedAttemptStore.initialize(ledger / 'db', anchor / 'watermark')
    store.reserve('unit:0', 'request', 'conversation')
    baseline = 'a' * 40
    config = {'worker_image': 'sha256:' + '1' * 64, 'proxy_image': 'sha256:' + '2' * 64,
              'helper_image': 'sha256:' + '3' * 64, 'worker_command': ['worker'], 'proxy_command': ['proxy'],
              'workspace': str(tmp_path), 'uid': 1000, 'worker_environment': []}
    monkeypatch.setattr(runtime.subprocess, 'run', lambda args, **kwargs: SimpleNamespace(stdout=(str(tmp_path) + '\n') if '--show-toplevel' in args else (baseline + '\n') if '--verify' in args else ''))
    resources = {}
    def fake_provision(**kwargs):
        allocation = kwargs['allocation_id']; prefix = 'factory-' + allocation
        volumes = {prefix + '-' + role: {'Name': prefix + '-' + role, 'CreatedAt': 'original'} for role in ('socket', 'budget', 'anchor')}
        worker = {'Id': '1' * 64, 'State': {'Running': True, 'Pid': 123, 'StartedAt': 'first'}}; proxy = {'Id': '2' * 64}; network = {'Id': '3' * 64}
        resources.update({('container', prefix + '-worker'): worker, ('container', prefix + '-proxy'): proxy,
                          ('network', prefix + '-network'): network, **{('volume', name): value for name, value in volumes.items()}})
        kwargs['before_credential'](worker, proxy, volumes, network)
        return allocation
    monkeypatch.setattr(runtime, 'provision', fake_provision)
    monkeypatch.setattr(runtime, 'inspect', lambda kind, name: resources[(kind, name)])
    monkeypatch.setattr(runtime, 'validate_identity', lambda *args, **kwargs: None)
    allocation = runtime.provision_once(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, gateway_key=b'secret', **config)
    result = runtime.inspect_bound_allocation(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, **config)
    assert result['worker_id'] == '1' * 64
    resources[('container', 'factory-' + allocation + '-worker')]['State']['StartedAt'] = 'restarted'
    with pytest.raises(runtime.ProvisionError, match='unavailable or changed'):
        runtime.inspect_bound_allocation(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, **config)
    resources[('container', 'factory-' + allocation + '-worker')] = {'Id': '4' * 64}
    with pytest.raises(runtime.ProvisionError, match='unavailable or changed'):
        runtime.inspect_bound_allocation(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, **config)


@pytest.mark.parametrize('telemetry', [False, True])
@pytest.mark.parametrize('bounded', [False, True])
def test_reinspection_uses_real_topology_validator(monkeypatch, tmp_path, telemetry, bounded):
    from types import SimpleNamespace
    from guarded_store import GuardedAttemptStore
    from test_provision_check import fixture, telemetry_fixture
    ledger, anchor = tmp_path / 'ledger', tmp_path / 'anchor'
    ledger.mkdir(); anchor.mkdir()
    store = GuardedAttemptStore.initialize(ledger / 'db', anchor / 'watermark')
    store.reserve('unit:0', 'request', 'conversation')
    baseline = 'a' * 40
    if telemetry:
        w, p, k, telemetry_name = telemetry_fixture(tmp_path)
    else:
        w, p, k = fixture(tmp_path)
    config = {'worker_image': w['Image'], 'proxy_image': p['Image'], 'helper_image': 'sha256:' + '3' * 64,
              'worker_command': k['worker_command'], 'proxy_command': k['proxy_command'],
              'workspace': str(tmp_path), 'uid': 1000, 'worker_environment': []}
    monkeypatch.setattr(runtime.subprocess, 'run', lambda args, **kwargs: SimpleNamespace(stdout=(str(tmp_path) + '\n') if '--show-toplevel' in args else (baseline + '\n') if '--verify' in args else ''))
    events=[]
    if bounded:
        import bounded_profile as profile
        import deadline_watchdog
        config['execution_profile']=profile.PROFILE
        config['firewall']={'helper_image':'sha256:'+'4'*64,'gateway_ip':'192.0.2.1'}
        monkeypatch.setattr(runtime,'verify_proxy_firewall',lambda *a:None)
        quota={'device':1,'inode':2,'mount_id':'3','filesystem':'tmpfs','capacity_bytes':67108864}
        monkeypatch.setattr(profile,'validate_workspace',lambda *args:dict(quota))
        w['HostConfig'].update(ReadonlyRootfs=True,Tmpfs={'/tmp':profile.temporary_option(1000)},ShmSize=67108864)
        w['Config']['Env']=profile.effective_environment([],profile.PROFILE)
        def arm(current,attempt,request,cid,spec,allocation,binding,*images):
            assert current.lookup_allocation(attempt,request,cid,spec)==(allocation,binding)
            events.append('armed')
        monkeypatch.setattr(deadline_watchdog,'arm_deadline',arm)
        monkeypatch.setattr(deadline_watchdog,'validate_deadline',lambda *args:events.append('validated'))
    resources = {}
    def fake_provision(**kwargs):
        allocation = kwargs['allocation_id']; prefix = 'factory-' + allocation
        names = {k['socket_volume']: prefix + '-socket', k['budget_volume']: prefix + '-budget', 'anchor': prefix + '-anchor'}
        if telemetry:
            names[telemetry_name] = prefix + '-telemetry'
        for container in (w, p):
            container['Config']['Labels']['factory.allocation'] = allocation
            for mount in container['Mounts']:
                if mount.get('Name') in names: mount['Name'] = names[mount['Name']]
        volumes = {names[name]: {**record, 'Name': names[name], 'Labels': {**record['Labels'], 'factory.allocation': allocation}} for name, record in k['volume_records'].items()}
        w['Id'] = '1' * 64; p['Id'] = '2' * 64
        w['State'].update({'Running': True, 'Pid': 123, 'StartedAt': 'first'})
        network = {'Id': k['proxy_network']}
        # The fixture's network ID is not necessarily a full Docker ID.
        network['Id'] = '3' * 64
        p['NetworkSettings']['Networks'] = {'factory': {'NetworkID': network['Id']}}
        resources.update({('container', prefix + '-worker'): w, ('container', prefix + '-proxy'): p,
                          ('network', prefix + '-network'): network, **{('volume', name): value for name, value in volumes.items()}})
        if bounded:
            p['State'].update(Running=True,Pid=456,StartedAt='proxy-first')
            # Normal bound operation requires the firewall; the fake provisioner
            # exercises the binding/deadline seam while firewall execution is elsewhere.
        kwargs['before_credential'](w, p, volumes, network)
        events.append('credential-release')
        return allocation
    monkeypatch.setattr(runtime, 'provision', fake_provision)
    monkeypatch.setattr(runtime, 'inspect', lambda kind, name: resources[(kind, name)])
    allocation = runtime.provision_once(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, gateway_key=b'secret', **config)
    assert runtime.inspect_bound_allocation(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, **config)['allocation_id'] == allocation
    if bounded:
        assert events[:4]==['armed','validated','credential-release','validated']
        quota['mount_id']='replaced-mount'
        with pytest.raises(runtime.ProvisionError):runtime.inspect_bound_allocation(store,'unit:0','request','conversation',workspace_baseline=baseline,**config)
        quota['mount_id']='3'
    if telemetry:
        record = resources[('volume', 'factory-' + allocation + '-telemetry')]
        record['CreatedAt'] = 'replaced-volume'
        with pytest.raises(runtime.ProvisionError, match='unavailable or changed'):
            runtime.inspect_bound_allocation(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, **config)
        record.pop('CreatedAt')
    w['HostConfig']['NetworkMode'] = 'bridge'
    with pytest.raises(runtime.ProvisionError):
        runtime.inspect_bound_allocation(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, **config)


@pytest.mark.parametrize('failure', ['install', 'verify', 'restart'])
def test_opt_in_firewall_fails_before_credential(monkeypatch, tmp_path, failure):
    import io
    image = 'sha256:' + '1' * 64
    class FakeProcess:
        def __init__(self): self.stdin = io.BytesIO(); self.terminated = False
        def terminate(self): self.terminated = True
        def poll(self): return None
    process = FakeProcess()
    events = []
    counter = {'proxy': 0}
    def inspect(kind, name):
        if kind == 'image': return {'Id': name, 'Config': {'Env': []}}
        if kind == 'network': return {'Id': 'a'*64}
        if kind == 'volume': return _inspect_volume(kind, name)
        if name.endswith('-proxy'):
            counter['proxy'] += 1
            started = 'new' if failure == 'restart' and events else 'old'
            return {'Id': 'b'*64, 'State': {'Pid': 42, 'StartedAt': started, 'Running': True}}
        return {'Id': 'c'*64}
    def install(*args):
        events.append('install')
        if failure == 'install': raise ValueError('firewall failed')
    def verify(*args):
        events.append('verify')
        if failure == 'verify': raise ValueError('verification failed')
    monkeypatch.setattr(runtime, 'inspect', inspect)
    monkeypatch.setattr(runtime, '_run', lambda *args, **kwargs: '')
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(runtime, 'validate_identity', lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, 'install_proxy_firewall', install)
    monkeypatch.setattr(runtime, 'verify_proxy_firewall', verify)
    with pytest.raises(runtime.ProvisionError):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy','--gateway-ip','100.92.236.42'],
                          workspace=str(tmp_path), uid=1000, worker_environment=[], gateway_key=b'secret',
                          firewall={'helper_image':image, 'gateway_ip':'100.92.236.42'})
    assert events[0] == 'install'
    assert process.stdin.getvalue() == b''
    assert process.terminated


def test_opt_in_firewall_rejects_proxy_ip_mismatch(monkeypatch, tmp_path):
    image = 'sha256:' + '1'*64
    monkeypatch.setattr(runtime, 'inspect', _inspect_volume)
    monkeypatch.setattr(runtime, '_run', lambda *args, **kwargs: pytest.fail('must not create resources'))
    with pytest.raises(runtime.ProvisionError, match='gateway IP differ'):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy','--gateway-ip','100.92.236.1'],
                          workspace=str(tmp_path), uid=1000, worker_environment=[], gateway_key=b'secret',
                          firewall={'helper_image':image, 'gateway_ip':'100.92.236.42'})


def test_firewall_helper_must_be_an_inspected_immutable_image(monkeypatch, tmp_path):
    image = 'sha256:' + '1' * 64
    monkeypatch.setattr(runtime, 'inspect', _inspect_volume)
    monkeypatch.setattr(runtime, '_run', lambda *args, **kwargs: pytest.fail('must not create resources'))
    with pytest.raises(runtime.ProvisionError, match='immutable firewall helper'):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy', '--gateway-ip', '100.92.236.42'],
                          workspace=str(tmp_path), uid=1000, worker_environment=[], gateway_key=b'secret',
                          firewall={'helper_image': 'mutable-tag', 'gateway_ip': '100.92.236.42'})


def test_firewall_helper_inspection_must_match_its_image_id(monkeypatch, tmp_path):
    image = 'sha256:' + '1' * 64
    firewall_image = 'sha256:' + '2' * 64
    monkeypatch.setattr(runtime, 'inspect', lambda kind, name: {'Id': image, 'Config': {'Env': []}})
    monkeypatch.setattr(runtime, '_run', lambda *args, **kwargs: pytest.fail('must not create resources'))
    with pytest.raises(runtime.ProvisionError, match='firewall helper image ID mismatch'):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy', '--gateway-ip', '100.92.236.42'],
                          workspace=str(tmp_path), uid=1000, worker_environment=[], gateway_key=b'secret',
                          firewall={'helper_image': firewall_image, 'gateway_ip': '100.92.236.42'})


def test_rejects_bind_backed_volume_before_privileged_initializer(monkeypatch, tmp_path):
    image = 'sha256:' + '1' * 64
    calls = []
    def inspect(kind, name):
        record = _inspect_volume(kind, name)
        if kind == 'volume':
            record['Options'] = {'type': 'none', 'o': 'bind', 'device': '/host/other'}
        return record
    monkeypatch.setattr(runtime, 'inspect', inspect)
    monkeypatch.setattr(runtime, '_run', lambda args, **kwargs: calls.append(args) or '')
    with pytest.raises(runtime.ProvisionError):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy'], workspace=str(tmp_path),
                          uid=1000, worker_environment=[], gateway_key=b'secret')
    assert not any(call[0] == 'run' for call in calls)
    assert not any(call[:2] == ['volume', 'rm'] for call in calls)


@pytest.mark.parametrize('option', [
    ['--telemetry-dir'], ['--telemetry-dir','/budget'],
    ['--telemetry-dir','/telemetry','--telemetry-dir','/telemetry'],
    ['--telemetry-dir=/telemetry'],
])
def test_invalid_telemetry_refused_before_docker(monkeypatch, tmp_path, option):
    monkeypatch.setattr(runtime, 'inspect', lambda *_: pytest.fail('Docker inspection before telemetry validation'))
    monkeypatch.setattr(runtime, '_run', lambda *_: pytest.fail('Docker mutation before telemetry validation'))
    image = 'sha256:'+'1'*64
    with pytest.raises(ValueError, match='telemetry'):
        runtime.provision(worker_image=image,proxy_image=image,helper_image=image,
            worker_command=['worker'],proxy_command=['proxy',*option],workspace=str(tmp_path),
            uid=1000,worker_environment=[],gateway_key=b'key')


def test_telemetry_is_private_proxy_only_and_retained_after_failure(monkeypatch,tmp_path):
    image='sha256:'+'1'*64
    calls=[]
    monkeypatch.setattr(runtime,'inspect',_inspect_volume)
    def run(args,**kwargs):
        calls.append(args)
        if args[0]=='run':raise runtime.ProvisionError('initializer failure')
        return ''
    monkeypatch.setattr(runtime,'_run',run)
    with pytest.raises(runtime.ProvisionError):
        runtime.provision(worker_image=image,proxy_image=image,helper_image=image,
            worker_command=['worker'],proxy_command=['proxy','--telemetry-dir','/telemetry'],
            workspace=str(tmp_path),uid=1000,worker_environment=[],gateway_key=b'key',allocation_id='a'*32)
    assert any(c[:2]==['volume','create'] and c[-1].endswith('-telemetry') for c in calls)
    assert not any(c[:2]==['volume','rm'] and c[-1].endswith('-telemetry') for c in calls)


@pytest.mark.parametrize('startup',['ready','delayed'])
def test_telemetry_mount_initialized_private_and_not_exposed_to_worker(monkeypatch,tmp_path,startup):
    import io
    from test_provision_check import telemetry_fixture
    w,p,k,old_telemetry=telemetry_fixture(tmp_path)
    allocation='a'*32
    names={k['socket_volume']:'factory-'+allocation+'-socket',
           k['budget_volume']:'factory-'+allocation+'-budget',
           k['anchor_volume']:'factory-'+allocation+'-anchor',old_telemetry:'factory-'+allocation+'-telemetry'}
    for container in (w,p):
        container['Config']['Labels']['factory.allocation']=allocation
        for mount in container['Mounts']:
            if mount.get('Name') in names:mount['Name']=names[mount['Name']]
    class Process:
        def __init__(self):self.stdin=io.BytesIO()
        def terminate(self):pass
        def poll(self):return None
    process=Process();calls=[];proxy_reads=[0];clock=[0.]
    from types import SimpleNamespace
    monkeypatch.setattr(runtime,'time',SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda n:clock.__setitem__(0,clock[0]+n)),raising=False)
    def inspect(kind,name):
        if kind=='container':
            if name.endswith('-worker'):return w
            proxy_reads[0]+=1
            if startup=='delayed' and proxy_reads[0]<3:return {**p,'State':{'Running':False,'Restarting':False}}
            return p
        if kind=='network':return {'Id':k['proxy_network']}
        return _inspect_volume(kind,name)
    monkeypatch.setattr(runtime,'inspect',inspect)
    monkeypatch.setattr(runtime,'_run',lambda args,**kwargs:calls.append(args) or '')
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda *a,**kw:process)
    runtime.provision(worker_image=w['Image'],proxy_image=p['Image'],helper_image='sha256:'+'3'*64,
        worker_command=k['worker_command'],proxy_command=k['proxy_command'],workspace=str(tmp_path),
        uid=1000,worker_environment=[],gateway_key=b'key',allocation_id=allocation)
    assert process.stdin.getvalue()==b'key\n'
    proxy_create=next(c for c in calls if c[0]=='create' and c[c.index('--name')+1].endswith('-proxy'))
    worker_create=next(c for c in calls if c[0]=='create' and c[c.index('--name')+1].endswith('-worker'))
    mount='type=volume,src=factory-'+allocation+'-telemetry,dst=/telemetry'
    assert mount in proxy_create
    assert not any(value.startswith('type=volume,src=') and value.endswith(',dst=/telemetry') for value in worker_create)
    assert any(c[0]=='run' and '/bin/chmod' in c and c[-2:]==['0700','/telemetry'] for c in calls)


def test_bounded_profile_refuses_unquotaed_workspace_before_docker(monkeypatch,tmp_path):
    monkeypatch.setattr(runtime,'inspect',lambda *_:pytest.fail('Docker reached before bounded preflight'))
    with pytest.raises(runtime.ProvisionError):
        runtime.provision(worker_image='sha256:'+'1'*64,proxy_image='sha256:'+'2'*64,
            helper_image='sha256:'+'3'*64,worker_command=['x'],proxy_command=['y'],workspace=str(tmp_path),
            uid=1000,worker_environment=[],gateway_key=b'dummy',execution_profile='bounded-native-v1')


@pytest.mark.parametrize('exited',[False,True])
def test_proxy_startup_never_releases_key_after_timeout_or_controller_exit(monkeypatch,tmp_path,exited):
    import io
    from types import SimpleNamespace
    image='sha256:'+'1'*64;clock=[0.];stream=io.BytesIO();terminated=[]
    process=SimpleNamespace(stdin=stream,poll=lambda:0 if exited else None,terminate=lambda:terminated.append(True))
    def inspect(kind,name):
        value=_inspect_volume(kind,name)
        if kind=='container':value['State']={'Running':False,'Restarting':False}
        return value
    monkeypatch.setattr(runtime,'inspect',inspect)
    monkeypatch.setattr(runtime,'time',SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda n:clock.__setitem__(0,clock[0]+n)))
    monkeypatch.setattr(runtime,'_run',lambda *a,**k:'')
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda *a,**k:process)
    with pytest.raises(runtime.ProvisionError):
        runtime.provision(worker_image=image,proxy_image=image,helper_image=image,worker_command=['worker'],
            proxy_command=['proxy'],workspace=str(tmp_path),uid=1000,worker_environment=[],gateway_key=b'never-release')
    assert stream.getvalue()==b'' and terminated
    assert clock[0]==0 if exited else 5<=clock[0]<5.2
