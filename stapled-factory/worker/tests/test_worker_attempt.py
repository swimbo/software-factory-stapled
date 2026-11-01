import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from attempt_store import AttemptStore
from worker import execute_attempt, run_task


def test_ambiguous_dispatch_never_repeats_and_reconciles(tmp_path):
    store = AttemptStore(tmp_path / 'attempts.db')
    calls = []
    def dispatch(cid, request):
        calls.append(cid)
        raise TimeoutError('possibly accepted')
    def unresolved(cid, request):
        return None
    first = execute_attempt(store, 'workflow:1', {'task': 'one'}, dispatch, unresolved)
    second = execute_attempt(store, 'workflow:1', {'task': 'one'}, dispatch, unresolved)
    assert first['status'] == second['status'] == 'unknown'
    assert len(calls) == 1
    assert first['conversation_id'] == second['conversation_id']
    result = execute_attempt(store, 'workflow:1', {'task': 'one'}, dispatch,
                             lambda cid, req: {'status': 'completed', 'git_revision': 'abc'})
    assert result['status'] == 'completed'
    replay = execute_attempt(store, 'workflow:1', {'task': 'one'}, dispatch, unresolved)
    assert replay == result
    assert len(calls) == 1


def test_attempt_reuse_with_different_request_does_not_dispatch(tmp_path):
    store = AttemptStore(tmp_path / 'attempts.db')
    calls = []
    execute_attempt(store, 'one', {'task': 'a'}, lambda *args: calls.append(1), lambda *args: None)
    result = execute_attempt(store, 'one', {'task': 'b'}, lambda *args: calls.append(2), lambda *args: None)
    assert result['status'] == 'unknown'
    assert calls == [1]


def test_production_dispatch_is_blocked():
    assert run_task(SimpleNamespace())['status'] == 'blocked'



def test_docker_failure_code_survives_cleanup_without_stderr_or_credential_leak(tmp_path,monkeypatch):
    import subprocess
    import provision_runtime as runtime
    from test_provision_runtime import _inspect_volume
    secret='private-fixture-never-log'
    def command(args,**kwargs):
        if args[:3]==['docker','network','create']:
            raise subprocess.CalledProcessError(1,args,stderr='all predefined address pools have been fully subnetted '+secret)
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(runtime,'inspect',_inspect_volume)
    monkeypatch.setattr(runtime.subprocess,'run',command)
    image='sha256:'+'1'*64
    def dispatch(*args):
        return runtime.provision(worker_image=image,proxy_image=image,helper_image=image,
            worker_command=['worker'],proxy_command=['proxy'],workspace=str(tmp_path),uid=1000,
            worker_environment=[],gateway_key=secret.encode())
    result=execute_attempt(AttemptStore(tmp_path/'attempts.db'),'safe:0',{'task':'x'},dispatch,lambda *_:None)
    assert result['status']=='unknown' and 'docker.network.address_pool_exhausted' in result['error']
    import json
    assert secret not in json.dumps(result)


def test_unregistered_exception_diagnostic_cannot_expose_arbitrary_text(tmp_path):
    def dispatch(*args):
        error=RuntimeError('private-fixture-never-log');error.diagnostic_code='private-fixture-never-log';raise error
    result=execute_attempt(AttemptStore(tmp_path/'attempts.db'),'safe:0',{},dispatch,lambda *_:None)
    assert result['status']=='unknown' and 'private-fixture' not in result['error']


def test_stall_fencing_is_unknown_with_original_identity_and_no_result_publication(tmp_path):
    from stall_runtime import StallIntervention
    calls=[]
    def dispatch(*_):calls.append('original');raise StallIntervention('stopped')
    store=AttemptStore(tmp_path/'attempts.db')
    result=execute_attempt(store,'stall:0',{'task':'x'},dispatch,lambda *_:None)
    assert result['status']=='unknown' and result['intervention_status']=='stopped' and result['git_revision']==''
    replay=execute_attempt(store,'stall:0',{'task':'x'},dispatch,lambda *_:None)
    assert replay['status']=='unknown' and calls==['original']
    assert replay['conversation_id']==result['conversation_id']
