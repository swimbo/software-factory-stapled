import multiprocessing
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from attempt_store import AttemptStore


def _reserve_and_wait(path, ready):
    store = AttemptStore(Path(path))
    assert store.reserve('unit:1', 'hash', 'conversation') == ('new', None)
    ready.set()
    import time
    time.sleep(60)


def test_process_death_does_not_authorize_repeat_dispatch(tmp_path):
    path = tmp_path / 'attempts.db'
    ready = multiprocessing.Event()
    proc = multiprocessing.Process(target=_reserve_and_wait, args=(str(path), ready))
    proc.start()
    assert ready.wait(10)
    proc.kill()
    proc.join(10)
    store = AttemptStore(path)
    assert store.reserve('unit:1', 'hash', 'conversation') == ('unknown', None)
    store.complete('unit:1', '{"status":"completed"}')
    assert store.reserve('unit:1', 'hash', 'conversation') == ('completed', '{"status":"completed"}')


def test_attempt_identity_cannot_be_reused_for_different_input(tmp_path):
    store = AttemptStore(tmp_path / 'attempts.db')
    assert store.reserve('unit:1', 'hash', 'conversation') == ('new', None)
    with pytest.raises(ValueError):
        store.reserve('unit:1', 'different', 'conversation')
    with pytest.raises(ValueError):
        store.reserve('unit:1', 'hash', 'different')
    with pytest.raises(ValueError):
        store.complete('missing', '{}')


def _race_reserve(path, gate, results):
    store = AttemptStore(Path(path))
    gate.wait()
    results.put(store.reserve('race:1', 'hash', 'conversation')[0])


def test_only_one_concurrent_caller_can_dispatch(tmp_path):
    path = tmp_path / 'attempts.db'
    AttemptStore(path)
    gate = multiprocessing.Event()
    results = multiprocessing.Queue()
    procs = [multiprocessing.Process(target=_race_reserve, args=(str(path), gate, results)) for _ in range(2)]
    for proc in procs:
        proc.start()
    gate.set()
    verdicts = [results.get(timeout=10) for _ in procs]
    for proc in procs:
        proc.join(10)
        assert proc.exitcode == 0
    assert sorted(verdicts) == ['new', 'unknown']


def test_completed_result_cannot_be_overwritten(tmp_path):
    store = AttemptStore(tmp_path / 'attempts.db')
    store.reserve('unit:1', 'hash', 'conversation')
    store.complete('unit:1', 'first')
    with pytest.raises(ValueError):
        store.complete('unit:1', 'second')
    assert store.reserve('unit:1', 'hash', 'conversation') == ('completed', 'first')
