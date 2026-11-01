import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deadline_watchdog as watch
from guarded_store import GuardedAttemptStore, StoreQuarantined


def fixture(tmp_path, monkeypatch):
    db = tmp_path/'db'; anchor = tmp_path/'anchor'
    db.mkdir(mode=0o700); anchor.mkdir(mode=0o700)
    store = GuardedAttemptStore.initialize(db/'attempts.db', anchor/'watermark.json')
    identity = ('attempt:0', '1'*64, 'conversation', '2'*64)
    store.reserve(*identity[:3])
    _, alloc = store.reserve_allocation(*identity)
    binding = {'worker_id':'a'*64, 'proxy_id':'b'*64,
        'worker_lifecycle':{'Pid':101,'StartedAt':'2026-10-30T00:00:00Z'},
        'proxy_lifecycle':{'Pid':102,'StartedAt':'2026-10-30T00:00:00Z'}, 'volumes':{'kept':{'Name':'kept'}}}
    store.bind_allocation(*identity, alloc, binding)
    containers = {}
    for index, role in enumerate(('worker','proxy'),1):
        containers[binding[role+'_id']] = {'Id':binding[role+'_id'], 'Image':'sha256:'+str(index)*64,
            'Config':{'Labels':{'factory.allocation':alloc,'factory.role':role}},
            'HostConfig':{'RestartPolicy':{'Name':'no'}},
            'State':{'Running':True,'Restarting':False,**binding[role+'_lifecycle']}}
    commands=[]
    def docker(args):
        commands.append(list(args))
        if args[0]=='inspect':return json.dumps([copy.deepcopy(containers[args[1]])])
        assert args[:3]==['kill','--signal','KILL']
        containers[args[3]]['State'].update(Running=False,Pid=0)
        return ''
    monkeypatch.setattr(watch,'_docker',docker)
    monkeypatch.setattr(watch.time,'time',lambda:1900000000.)
    monkeypatch.setattr(watch.time,'monotonic',lambda:10000.)
    monkeypatch.setattr(watch,'_boot_id',lambda:'11111111-1111-1111-1111-111111111111')
    monkeypatch.setattr(watch,'_system',lambda args: SimpleNamespace(returncode=0,stdout='active\ninactive\n'))
    import crash_fence
    monkeypatch.setattr(crash_fence,'inspect',lambda kind, cid: json.loads(docker(['inspect',cid]))[0])
    monkeypatch.setattr(crash_fence,'_run',docker)
    args=(store,*identity,alloc,binding)
    return args, containers, commands


def arm(args):
    return watch.arm_deadline(*args,'sha256:'+'1'*64,'sha256:'+'2'*64,seconds=2)


def test_arm_is_private_durable_and_never_extends_existing_allocation(tmp_path,monkeypatch):
    args,_,_=fixture(tmp_path,monkeypatch);calls=[]
    monkeypatch.setattr(watch,'_system',lambda argv: calls.append(argv) or SimpleNamespace(returncode=0,stdout=''))
    path=arm(args)
    assert path.stat().st_mode&0o777==0o600
    assert path.parent.stat().st_mode&0o777==0o700
    record=json.loads(path.read_text())
    assert record['binding']==args[-1] and record['deadline_unix']==1900000002
    assert calls[0][:2]==['systemd-run','--user'] and '--on-active=2s' in calls[0]
    with pytest.raises(FileExistsError):arm(args)
    assert len(calls)==1 and json.loads(path.read_text())==record


def test_failed_arm_keeps_record_and_cannot_be_retried_with_fresh_deadline(tmp_path,monkeypatch):
    args,_,_=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(watch,'_system',lambda args: SimpleNamespace(returncode=1,stdout=''))
    with pytest.raises(watch.DeadlineError):arm(args)
    assert list((args[0].anchor_path.parent/'deadlines').glob('*.json'))
    with pytest.raises(FileExistsError):arm(args)


def test_validation_requires_original_record_and_live_timer(tmp_path,monkeypatch):
    args,_,_=fixture(tmp_path,monkeypatch);arm(args)
    assert watch.validate_deadline(*args)['seconds']==2
    monkeypatch.setattr(watch,'_system',lambda args: SimpleNamespace(returncode=3,stdout='inactive\ninactive\n'))
    with pytest.raises(watch.DeadlineError):watch.validate_deadline(*args)


def test_expiry_fences_partial_effects_without_refund_or_redispatch(tmp_path,monkeypatch):
    args,containers,calls=fixture(tmp_path,monkeypatch);path=arm(args)
    with pytest.raises(watch.DeadlineError):watch.expire(path)
    monkeypatch.setattr(watch.time,'time',lambda:1900000003.)
    monkeypatch.setattr(watch.time,'monotonic',lambda:10003.)
    result=watch.expire(path)
    assert result['status']=='fenced' and all(not c['State']['Running'] for c in containers.values())
    assert [c for c in calls if c[0]=='kill']==[['kill','--signal','KILL','a'*64],['kill','--signal','KILL','b'*64]]
    assert args[0].lookup(*args[1:4])==('unknown',None)
    with pytest.raises(StoreQuarantined):args[0].complete(args[1],'{"status":"completed"}')
    watch.expire(path)
    assert len([c for c in calls if c[0]=='kill'])==2


@pytest.mark.parametrize('change',[{'Image':'changed'},{'StartedAt':'different'},{'Restarting':True}])
def test_restarted_or_substituted_container_is_never_killed(tmp_path,monkeypatch,change):
    args,containers,calls=fixture(tmp_path,monkeypatch);path=arm(args)
    if 'Image' in change:containers['a'*64].update(change)
    else:containers['a'*64]['State'].update(change)
    monkeypatch.setattr(watch.time,'time',lambda:1900000003.)
    monkeypatch.setattr(watch.time,'monotonic',lambda:10003.)
    with pytest.raises(watch.DeadlineError):watch.expire(path)
    assert not [c for c in calls if c[0]=='kill']


def test_completed_result_is_preserved_while_owned_containers_stop(tmp_path,monkeypatch):
    args,containers,calls=fixture(tmp_path,monkeypatch);path=arm(args)
    args[0].complete(args[1],'{"status":"completed"}')
    monkeypatch.setattr(watch.time,'time',lambda:1900000003.)
    monkeypatch.setattr(watch.time,'monotonic',lambda:10003.)
    assert watch.expire(path)['status']=='stopped-completed'
    assert args[0].lookup(*args[1:4])==('completed','{"status":"completed"}')
    assert all(not c['State']['Running'] for c in containers.values())


def test_explicit_cancel_is_early_fencing_not_rollback(tmp_path,monkeypatch):
    args,containers,_=fixture(tmp_path,monkeypatch);path=arm(args)
    assert watch.expire(path,cancel=True)['status']=='fenced'
    assert all(not c['State']['Running'] for c in containers.values())


def test_proxy_is_stopped_even_when_fence_storage_fails(tmp_path,monkeypatch):
    args,containers,_=fixture(tmp_path,monkeypatch);path=arm(args)
    def unavailable(*args):raise RuntimeError('storage unavailable')
    monkeypatch.setattr(GuardedAttemptStore,'begin_fencing',unavailable)
    monkeypatch.setattr(watch.time,'time',lambda:1900000003.)
    monkeypatch.setattr(watch.time,'monotonic',lambda:10003.)
    with pytest.raises(watch.DeadlineError):watch.expire(path)
    assert all(not c['State']['Running'] for c in containers.values())


def test_wall_clock_rollback_cannot_extend_monotonic_deadline(tmp_path,monkeypatch):
    args,containers,_=fixture(tmp_path,monkeypatch);path=arm(args)
    monkeypatch.setattr(watch.time,'time',lambda:1800000000.)
    monkeypatch.setattr(watch.time,'monotonic',lambda:10003.)
    assert watch.expire(path)['status']=='fenced'
    assert all(not c['State']['Running'] for c in containers.values())


def test_boot_change_does_not_rearm_or_adopt_old_record(tmp_path,monkeypatch):
    args,_,calls=fixture(tmp_path,monkeypatch);path=arm(args)
    monkeypatch.setattr(watch,'_boot_id',lambda:'22222222-2222-2222-2222-222222222222')
    with pytest.raises(watch.DeadlineError,match='boot changed'):watch.validate_deadline(*args)
    with pytest.raises(watch.DeadlineError,match='boot changed'):watch.expire(path)
    assert not [c for c in calls if c[0]=='kill']


def test_deadline_stops_namespaces_before_potentially_blocking_ledger_access(tmp_path,monkeypatch):
    args,containers,calls=fixture(tmp_path,monkeypatch);path=arm(args)
    def blocked(*args):
        assert all(not c['State']['Running'] for c in containers.values())
        raise StoreQuarantined('ledger unavailable')
    monkeypatch.setattr(watch,'_bound',blocked)
    monkeypatch.setattr(watch.time,'monotonic',lambda:10003.)
    with pytest.raises(watch.DeadlineError,match='fencing incomplete'):
        watch.expire(path)
    assert len([c for c in calls if c[0]=='kill'])==2


def test_quarantined_ledger_does_not_disable_namespace_deadline(tmp_path,monkeypatch):
    args,containers,_=fixture(tmp_path,monkeypatch);path=arm(args)
    args[0].anchor_path.write_text('{}')
    monkeypatch.setattr(watch.time,'monotonic',lambda:10003.)
    with pytest.raises(watch.DeadlineError,match='fencing incomplete'):
        watch.expire(path)
    assert all(not c['State']['Running'] for c in containers.values())
