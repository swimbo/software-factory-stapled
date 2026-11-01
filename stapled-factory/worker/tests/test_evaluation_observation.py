"""Read-only evaluation polling retains original dispatch and completion rules."""
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation_observation import EvaluationObserver
from guarded_store import GuardedAttemptStore
from openhands_adapter import OpenHandsAdapter
from worker import execute_openhands_attempt


class Transport:
    def __init__(self):
        self.calls = []
        self.finished = False

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        cid = payload['conversation_id'] if method == 'POST' else path.rsplit('/', 1)[-1]
        return {'id': cid, 'execution_status': 'finished' if self.finished else 'running'}


def observer(cid, monkeypatch, *, validate=lambda cid: None, revision='a'*40):
    import evaluation_observation
    sleeps = []
    monkeypatch.setattr(evaluation_observation.time, 'sleep', sleeps.append)
    transport = Transport()
    base = OpenHandsAdapter('http://127.0.0.1:8000', 'dummy', lambda *_: revision,
                            transport=transport)
    return EvaluationObserver(base, cid, validate=validate), transport, sleeps


def test_timeout_and_reconciliation_reuse_original_without_second_post(tmp_path, monkeypatch):
    attempt = 'trial:0'
    cid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'stapled-factory:' + attempt))
    validated = []
    adapter, transport, sleeps = observer(cid, monkeypatch, validate=validated.append)
    ledger = tmp_path/'ledger'; anchor = tmp_path/'anchor'
    ledger.mkdir(mode=0o700); anchor.mkdir(mode=0o700)
    store = GuardedAttemptStore.initialize(ledger/'attempts.db', anchor/'mark')
    request = {'initial_message': {'role': 'user', 'content': [{'type':'text','text':'task'}], 'run': True}}
    first = execute_openhands_attempt(store, attempt, request, adapter)
    assert first['status'] == 'unknown'
    assert len(sleeps) == 120 and set(sleeps) == {2}
    assert len([c for c in transport.calls if c[0]=='GET']) == 120
    assert validated == [cid]*121  # Initial dispatch and every observation.
    transport.finished = True
    completed = execute_openhands_attempt(store, attempt, request, adapter, reconcile_only=True)
    assert completed['status'] == 'completed' and completed['git_revision'] == 'a'*40
    assert [c[1] for c in transport.calls if c[0]=='POST'] == ['/api/conversations']
    assert all(c[1]=='/api/conversations/'+cid for c in transport.calls if c[0]=='GET')


def test_finished_without_verified_revision_remains_unknown(monkeypatch):
    cid = str(uuid.uuid4()); adapter, transport, sleeps = observer(cid, monkeypatch, revision=None)
    transport.finished = True
    assert adapter.read(cid, None) is None
    assert len(sleeps) == 120
    assert all(method == 'GET' for method, _, _ in transport.calls)


def test_wrong_conversation_is_refused_before_transport(monkeypatch):
    cid = str(uuid.uuid4()); adapter, transport, _ = observer(cid, monkeypatch)
    with pytest.raises(ValueError, match='original conversation'):
        adapter.read(str(uuid.uuid4()), None)
    assert not transport.calls


def test_validation_drift_stops_observation_before_next_get(monkeypatch):
    cid = str(uuid.uuid4()); seen = []
    def validate(value):
        seen.append(value)
        if len(seen)>1: raise ValueError('binding changed')
    adapter, transport, sleeps = observer(cid, monkeypatch, validate=validate)
    with pytest.raises(ValueError, match='binding changed'):
        adapter.read(cid, None)
    assert len(transport.calls) == 1 and transport.calls[0][0] == 'GET'
    assert sleeps == [2]


def test_observation_error_preserves_original_reservation_for_read_only_reconciliation(tmp_path, monkeypatch):
    from docker_transport import DockerTransportError
    attempt = 'failed-read:0'
    cid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'stapled-factory:' + attempt))
    adapter, transport, _ = observer(cid, monkeypatch)
    original = transport.request
    def unavailable(method, path, payload=None):
        if method == 'GET': raise DockerTransportError('uncertain')
        return original(method, path, payload)
    transport.request = unavailable
    ledger = tmp_path/'ledger'; anchor = tmp_path/'anchor'
    ledger.mkdir(mode=0o700); anchor.mkdir(mode=0o700)
    store = GuardedAttemptStore.initialize(ledger/'attempts.db', anchor/'mark')
    request = {'initial_message': {'role':'user','content':[{'type':'text','text':'task'}],'run':True}}
    assert execute_openhands_attempt(store, attempt, request, adapter)['status'] == 'unknown'
    with store._transaction() as (db, _):
        assert db.execute('SELECT status FROM attempts').fetchone() == ('reserved',)
    transport.request = original; transport.finished = True
    assert execute_openhands_attempt(store, attempt, request, adapter, reconcile_only=True)['status'] == 'completed'
    assert len([c for c in transport.calls if c[0]=='POST']) == 1
