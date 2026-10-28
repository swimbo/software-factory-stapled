import shutil
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guarded_store import GuardedAttemptStore, StoreQuarantined


def setup(tmp_path):
    ledger = tmp_path / 'ledger'; anchor = tmp_path / 'anchor'
    ledger.mkdir(); anchor.mkdir()
    db, marker = ledger / 'attempts.db', anchor / 'watermark.json'
    return GuardedAttemptStore.initialize(db, marker), db, marker


def test_restored_older_ledger_is_quarantined(tmp_path):
    store, db, marker = setup(tmp_path)
    assert store.reserve('a', 'hash', 'conversation') == ('new', None)
    snapshot = tmp_path / 'snapshot.db'; shutil.copy2(db, snapshot)
    store.complete('a', '{"status":"completed"}')
    shutil.copy2(snapshot, db)
    with pytest.raises(StoreQuarantined):
        store.reserve('a', 'hash', 'conversation')


def test_replaced_or_missing_anchor_and_database_fail_closed(tmp_path):
    store, db, anchor = setup(tmp_path)
    db.unlink()
    with pytest.raises(StoreQuarantined): store.reserve('a', 'hash', 'conversation')
    with pytest.raises(StoreQuarantined): GuardedAttemptStore.initialize(db, anchor)


def test_incomplete_anchor_update_quarantines_after_commit(tmp_path, monkeypatch):
    store, db, anchor = setup(tmp_path)
    def fail(*args): raise OSError('injected failure')
    monkeypatch.setattr(store, '_write_anchor', fail)
    with pytest.raises(StoreQuarantined): store.reserve('a', 'hash', 'conversation')
    reopened = GuardedAttemptStore(db, anchor)
    with pytest.raises(StoreQuarantined): reopened.reserve('a', 'hash', 'conversation')


def test_reopen_retains_identity_and_prevents_duplicate(tmp_path):
    store, db, anchor = setup(tmp_path)
    assert store.reserve('a', 'hash', 'conversation') == ('new', None)
    reopened = GuardedAttemptStore(db, anchor)
    assert reopened.reserve('a', 'hash', 'conversation') == ('unknown', None)
    reopened.complete('a', 'result')
    assert store.reserve('a', 'hash', 'conversation') == ('completed', 'result')


def test_fence_intent_persists_and_rejects_late_completion(tmp_path):
    store, db, anchor = setup(tmp_path)
    store.reserve('a', 'hash', 'conversation')
    _, allocation = store.reserve_allocation('a', 'hash', 'conversation', 'spec')
    store.bind_allocation('a', 'hash', 'conversation', 'spec', allocation, {'worker_id': 'b' * 64})
    store.begin_fencing('a', 'hash', 'conversation', 'spec', allocation, 'b' * 64)
    reopened = GuardedAttemptStore(db, anchor)
    reopened.begin_fencing('a', 'hash', 'conversation', 'spec', allocation, 'b' * 64)
    with pytest.raises(StoreQuarantined):
        reopened.complete('a', 'late')
    assert reopened.lookup('a', 'hash', 'conversation') == ('unknown', None)


def test_fencing_rejects_changed_binding_and_completed_attempt(tmp_path):
    store, _, _ = setup(tmp_path)
    store.reserve('a', 'hash', 'conversation')
    _, allocation = store.reserve_allocation('a', 'hash', 'conversation', 'spec')
    store.bind_allocation('a', 'hash', 'conversation', 'spec', allocation, {'worker_id': 'b' * 64})
    with pytest.raises(StoreQuarantined):
        store.begin_fencing('a', 'hash', 'conversation', 'spec', allocation, 'c' * 64)
    store.complete('a', 'verified')
    with pytest.raises(StoreQuarantined):
        store.begin_fencing('a', 'hash', 'conversation', 'spec', allocation, 'b' * 64)


def _completion_fence_race(db, anchor, allocation, gate, out, action):
    store = GuardedAttemptStore(Path(db), Path(anchor))
    gate.wait()
    try:
        if action == 'complete':
            store.complete('a', 'result')
        else:
            store.begin_fencing('a', 'hash', 'conversation', 'spec', allocation, 'b' * 64)
        out.put((action, 'ok'))
    except StoreQuarantined:
        out.put((action, 'rejected'))


def test_fence_and_completion_are_serialized_across_processes(tmp_path):
    import multiprocessing
    store, db, anchor = setup(tmp_path)
    store.reserve('a', 'hash', 'conversation')
    _, allocation = store.reserve_allocation('a', 'hash', 'conversation', 'spec')
    store.bind_allocation('a', 'hash', 'conversation', 'spec', allocation, {'worker_id': 'b' * 64})
    gate = multiprocessing.Event(); out = multiprocessing.Queue()
    processes = [multiprocessing.Process(target=_completion_fence_race,
                 args=(str(db), str(anchor), allocation, gate, out, action))
                 for action in ('complete', 'fence')]
    for process in processes: process.start()
    gate.set()
    results = dict(out.get(timeout=10) for _ in processes)
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert sorted(results.values()) == ['ok', 'rejected']
    assert store.lookup('a', 'hash', 'conversation')[0] == ('completed' if results['complete'] == 'ok' else 'unknown')


def _reserve_race(db, anchor, gate, out):
    gate.wait()
    out.put(GuardedAttemptStore(Path(db), Path(anchor)).reserve('same', 'hash', 'conversation')[0])


def test_concurrent_reservations_are_serialized(tmp_path):
    import multiprocessing
    _, db, anchor = setup(tmp_path)
    gate = multiprocessing.Event(); out = multiprocessing.Queue()
    procs = [multiprocessing.Process(target=_reserve_race, args=(str(db), str(anchor), gate, out)) for _ in range(2)]
    for p in procs: p.start()
    gate.set()
    results = [out.get(timeout=10) for _ in procs]
    for p in procs:
        p.join(10)
        assert p.exitcode == 0
    assert sorted(results) == ['new', 'unknown']


def test_attempt_store_identity_replacement_is_rejected(tmp_path):
    original, db, anchor = setup(tmp_path)
    other_root = tmp_path / 'other'; other_root.mkdir()
    other, other_db, _ = setup(other_root)
    shutil.copy2(other_db, db)
    with pytest.raises(StoreQuarantined): original.reserve('x', 'hash', 'conversation')


def test_worker_attempt_reconciles_without_redispatch_using_guarded_store(tmp_path):
    from worker import execute_attempt
    store, db, anchor = setup(tmp_path)
    calls = []
    def dispatch(cid, request):
        calls.append(cid)
        raise TimeoutError('unknown outcome')
    first = execute_attempt(store, 'unit:0', {'task':'example'}, dispatch, lambda *_: None)
    second = execute_attempt(GuardedAttemptStore(db, anchor), 'unit:0', {'task':'example'}, dispatch, lambda *_: None)
    assert first['status'] == second['status'] == 'unknown'
    assert first['conversation_id'] == second['conversation_id']
    assert len(calls) == 1


def test_reconcile_only_never_reserves_or_dispatches_missing_attempt(tmp_path):
    from worker import execute_attempt
    store, db, anchor = setup(tmp_path)
    calls = []
    result = execute_attempt(store, 'unit:0', {'task': 'example'},
                             lambda *_: calls.append('post'), lambda *_: calls.append('get'),
                             reconcile_only=True)
    assert result['status'] == 'unknown' and calls == []
    # A later real dispatch must still be able to reserve the attempt.
    result = execute_attempt(GuardedAttemptStore(db, anchor), 'unit:0', {'task': 'example'},
                             lambda *_: calls.append('post'), lambda *_: calls.append('get'))
    assert result['status'] == 'unknown' and calls == ['post']


def test_reconcile_only_rejects_changed_request_without_contacting_server(tmp_path):
    from worker import execute_attempt
    store, db, anchor = setup(tmp_path)
    execute_attempt(store, 'unit:0', {'task': 'original'}, lambda *_: None, lambda *_: None)
    calls = []
    result = execute_attempt(GuardedAttemptStore(db, anchor), 'unit:0', {'task': 'changed'},
                             lambda *_: calls.append('post'), lambda *_: calls.append('get'),
                             reconcile_only=True)
    assert result['status'] == 'unknown' and calls == []


def _allocation_race(db, anchor, gate, out):
    gate.wait()
    out.put(GuardedAttemptStore(Path(db), Path(anchor)).reserve_allocation('a', 'hash', 'conversation', 'spec'))


def test_concurrent_allocation_reservations_preserve_one_identity(tmp_path):
    import multiprocessing
    store, db, marker = setup(tmp_path)
    store.reserve('a', 'hash', 'conversation')
    gate = multiprocessing.Event(); out = multiprocessing.Queue()
    processes = [multiprocessing.Process(target=_allocation_race, args=(str(db), str(marker), gate, out)) for _ in range(2)]
    for process in processes: process.start()
    gate.set()
    results = [out.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert {result[0] for result in results} == {'new', 'existing'}
    assert len({result[1] for result in results}) == 1

