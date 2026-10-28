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

