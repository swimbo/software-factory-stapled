import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guarded_store import GuardedAttemptStore, StoreQuarantined


def initialized(tmp_path):
    (tmp_path / 'db').mkdir()
    (tmp_path / 'anchor').mkdir()
    store = GuardedAttemptStore.initialize(tmp_path / 'db/ledger', tmp_path / 'anchor/mark')
    store.reserve('old', 'old-hash', 'old-cid')
    _, allocation = store.reserve_allocation('old', 'old-hash', 'old-cid', 'spec')
    prefix = 'factory-' + allocation
    volumes = {}
    for role in ('budget', 'anchor'):
        name = prefix + '-' + role
        volumes[name] = {'Name': name, 'Driver': 'local',
                         'Labels': {'factory.allocation': allocation, 'factory.role': role}}
    binding = {'worker_id': 'b' * 64, 'proxy_id': 'c' * 64, 'volumes': volumes}
    store.bind_allocation('old', 'old-hash', 'old-cid', 'spec', allocation, binding)
    return store, allocation


def reserve(store, restart='new', conversation='new-cid'):
    return store.reserve_fenced_restart('old', 'old-hash', 'old-cid', 'spec', restart, 'old-hash', conversation)


def test_restart_requires_completed_fence_and_remains_blocked(tmp_path):
    store, allocation = initialized(tmp_path)
    with pytest.raises(StoreQuarantined): reserve(store)
    store.begin_fencing('old', 'old-hash', 'old-cid', 'spec', allocation, 'b' * 64)
    with pytest.raises(StoreQuarantined): reserve(store)
    store.confirm_fencing('old', 'old-hash', 'old-cid', 'spec', allocation, 'b' * 64)
    assert reserve(store) == 'new'
    reopened = GuardedAttemptStore(store.db_path, store.anchor_path)
    assert reserve(reopened) == 'existing'
    assert reopened.reserve('new', 'old-hash', 'new-cid') == ('unknown', None)
    with pytest.raises(StoreQuarantined): reopened.reserve_allocation('new', 'old-hash', 'new-cid', 'new-spec')
    with pytest.raises(StoreQuarantined): reopened.complete('new', 'result')
    with pytest.raises(StoreQuarantined): reserve(reopened, 'another', 'another-cid')


def test_unbound_proxy_cannot_be_reserved_for_restart(tmp_path):
    store, allocation = initialized(tmp_path)
    store.begin_fencing('old', 'old-hash', 'old-cid', 'spec', allocation, 'b' * 64)
    store.confirm_fencing('old', 'old-hash', 'old-cid', 'spec', allocation, 'b' * 64)
    with pytest.raises(StoreQuarantined):
        store.reserve_fenced_restart('old', 'old-hash', 'old-cid', 'other', 'new', 'old-hash', 'new-cid')


def competing_restart(db, anchor, gate, output, suffix):
    store = GuardedAttemptStore(Path(db), Path(anchor))
    gate.wait()
    try:
        output.put(reserve(store, 'new-' + suffix, 'conversation-' + suffix))
    except StoreQuarantined:
        output.put('rejected')


def test_only_one_competing_restart_can_be_reserved(tmp_path):
    import multiprocessing
    store, allocation = initialized(tmp_path)
    store.begin_fencing('old', 'old-hash', 'old-cid', 'spec', allocation, 'b' * 64)
    store.confirm_fencing('old', 'old-hash', 'old-cid', 'spec', allocation, 'b' * 64)
    gate = multiprocessing.Event(); output = multiprocessing.Queue()
    processes = [multiprocessing.Process(target=competing_restart,
                 args=(str(store.db_path), str(store.anchor_path), gate, output, suffix))
                 for suffix in ('a', 'b')]
    for process in processes: process.start()
    gate.set()
    assert sorted(output.get(timeout=10) for _ in processes) == ['new', 'rejected']
    for process in processes:
        process.join(10)
        assert process.exitcode == 0


def test_claim_once_and_immutable_worker_binding(tmp_path):
    store, allocation = initialized(tmp_path)
    store.begin_fencing('old', 'old-hash', 'old-cid', 'spec', allocation, 'b' * 64)
    store.confirm_fencing('old', 'old-hash', 'old-cid', 'spec', allocation, 'b' * 64)
    assert reserve(store) == 'new'
    baseline = 'a' * 40
    assert store.claim_restart('old', 'new', 'old-hash', 'new-cid', '/tmp/new-workspace', baseline) == allocation
    with pytest.raises(StoreQuarantined):
        store.claim_restart('old', 'new', 'old-hash', 'new-cid', '/tmp/new-workspace', baseline)
    with pytest.raises(StoreQuarantined): store.lookup_restart_worker('old', 'new')
    lifecycle = {'Pid': 12, 'StartedAt': 'start'}
    store.bind_restart_worker('old', 'new', '/tmp/new-workspace', baseline, 'd' * 64, lifecycle)
    assert store.lookup_restart_worker('old', 'new')['lifecycle'] == lifecycle
    with pytest.raises(StoreQuarantined):
        store.bind_restart_worker('old', 'new', '/tmp/new-workspace', baseline, 'e' * 64, lifecycle)


def test_restart_rejects_changed_task_hash(tmp_path):
    store, allocation = initialized(tmp_path)
    store.begin_fencing('old', 'old-hash', 'old-cid', 'spec', allocation, 'b' * 64)
    store.confirm_fencing('old', 'old-hash', 'old-cid', 'spec', allocation, 'b' * 64)
    with pytest.raises(StoreQuarantined):
        store.reserve_fenced_restart('old', 'old-hash', 'old-cid', 'spec', 'new', 'different', 'new-cid')
