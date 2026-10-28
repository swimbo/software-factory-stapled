import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import crash_fence
import successor_runtime
from worker import execute_bound_successor
from guarded_store import GuardedAttemptStore


def test_successor_dispatch_claim_once_then_reconcile(monkeypatch, tmp_path):
    (tmp_path / 'db').mkdir()
    (tmp_path / 'anchor').mkdir()
    store = GuardedAttemptStore.initialize(tmp_path / 'db/store', tmp_path / 'anchor/mark')
    calls = []
    def reserve(source, digest, cid, spec, restart, restart_hash, restart_cid):
        with store._transaction() as (db, state):
            db.execute('CREATE TABLE IF NOT EXISTS restart_reservations (source_attempt TEXT PRIMARY KEY, restart_id TEXT, restart_hash TEXT, restart_conversation_id TEXT, allocation_id TEXT)')
            db.execute('INSERT INTO attempts VALUES (?,?,?,\'restart_blocked\',NULL)', (restart, restart_hash, restart_cid))
            db.execute('INSERT INTO restart_reservations VALUES (?,?,?,?,?)', (source, restart, restart_hash, restart_cid, 'a' * 32))
            state['changed'] = True
    def provision(store, source, digest, cid, restart, restart_hash, restart_cid, **kw):
        calls.append('provision')
        store.claim_restart(source, restart, restart_hash, restart_cid, kw['workspace'], kw['baseline'])
        store.bind_restart_worker(source, restart, kw['workspace'], kw['baseline'], 'a' * 64, {'StartedAt': 't'})
    monkeypatch.setattr(store, 'reserve_fenced_restart', reserve)
    monkeypatch.setattr(crash_fence, 'fence_bound_worker', lambda *args: calls.append('fence'))
    monkeypatch.setattr(successor_runtime, 'provision_successor', provision)
    monkeypatch.setattr(successor_runtime, 'inspect_successor', lambda *args, **kw: {'worker_id': 'a' * 64})
    class Adapter:
        def create(self, cid, request):
            calls.append('create')
            raise TimeoutError('accepted before disconnect')
        def read(self, cid, request):
            calls.append('read')
            return {'status': 'completed', 'git_revision': 'b' * 40}
    kwargs = dict(workspace='/tmp/successor', baseline='c' * 40)
    first = execute_bound_successor(store, 'unit:0', {'task': 'one'}, {}, lambda _: Adapter(), **kwargs)
    second = execute_bound_successor(store, 'unit:0', {'task': 'one'}, {}, lambda _: Adapter(), reconcile_only=True, **kwargs)
    third = execute_bound_successor(store, 'unit:0', {'task': 'one'}, {}, lambda _: Adapter(), reconcile_only=True, **kwargs)
    assert first['status'] == 'unknown'
    assert second['status'] == 'completed' and third == second
    assert calls == ['fence', 'provision', 'create', 'read']
    assert store.begin_restart_dispatch('unit:0', 'unit:0:successor',
        __import__('hashlib').sha256(b'{"task":"one"}').hexdigest(), first['conversation_id']) is False


def test_preflight_is_blocked_and_side_effect_free(tmp_path):
    worker = Path(__file__).resolve().parents[1] / 'worker.py'
    db = tmp_path / 'missing.db'
    proc = subprocess.run([sys.executable, str(worker), '--task-file', str(tmp_path / 'task'),
        '--repo', str(tmp_path / 'repo'), '--attempt-id', 'unit:0', '--attempt-db', str(db),
        '--attempt-anchor', str(tmp_path / 'anchor'), '--successor-preflight-only'],
        check=True, capture_output=True, text=True)
    assert json.loads(proc.stdout)['status'] == 'blocked'
    assert not db.exists()
