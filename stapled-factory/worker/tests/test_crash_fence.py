import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import crash_fence


class Store:
    def __init__(self, worker='b' * 64):
        self.worker = worker
    def lookup_allocation(self, *args):
        return 'a' * 32, {'worker_id': self.worker}
    def begin_fencing(self, *args):
        pass
    def confirm_fencing(self, *args):
        pass


def record(running=True, **kwargs):
    obj = {'Id': 'b' * 64, 'Config': {'Labels': {'factory.allocation': 'a' * 32, 'factory.role': 'worker'}},
           'HostConfig': {'RestartPolicy': {'Name': 'no'}}, 'State': {'Running': running, 'Restarting': False}}
    obj.update(kwargs)
    return obj


def test_fence_kills_only_bound_full_id_and_verifies_stop(monkeypatch):
    states = iter([record(), record(False)])
    calls = []
    monkeypatch.setattr(crash_fence, 'inspect', lambda *args: next(states))
    monkeypatch.setattr(crash_fence, '_run', calls.append)
    assert crash_fence.fence_bound_worker(Store(), 'attempt', 'hash', 'cid', 'spec')['status'] == 'fenced'
    assert calls == [['kill', '--signal', 'KILL', 'b' * 64]]


def test_fence_is_idempotent_when_stopped(monkeypatch):
    monkeypatch.setattr(crash_fence, 'inspect', lambda *args: record(False))
    monkeypatch.setattr(crash_fence, '_run', lambda *_: pytest.fail('should not kill'))
    assert crash_fence.fence_bound_worker(Store(), 'attempt', 'hash', 'cid', 'spec')['status'] == 'fenced'


@pytest.mark.parametrize('invalid', [record(Id='c' * 64), record(HostConfig={'RestartPolicy': {'Name': 'always'}}),
                                     record(State={'Running': True, 'Restarting': True}), record(Config={'Labels': {}})])
def test_fence_rejects_changed_or_restarting_worker(monkeypatch, invalid):
    monkeypatch.setattr(crash_fence, 'inspect', lambda *args: invalid)
    monkeypatch.setattr(crash_fence, '_run', lambda *_: pytest.fail('must not kill'))
    with pytest.raises(crash_fence.FenceError):
        crash_fence.fence_bound_worker(Store(), 'attempt', 'hash', 'cid', 'spec')


def test_fence_refuses_unconfirmed_stop(monkeypatch):
    monkeypatch.setattr(crash_fence, 'inspect', lambda *args: record())
    monkeypatch.setattr(crash_fence, '_run', lambda *_: None)
    with pytest.raises(crash_fence.FenceError):
        crash_fence.fence_bound_worker(Store(), 'attempt', 'hash', 'cid', 'spec')


def test_fence_refuses_missing_binding():
    class Missing:
        def lookup_allocation(self, *args): raise ValueError('missing')
    with pytest.raises(crash_fence.FenceError):
        crash_fence.fence_bound_worker(Missing(), 'attempt', 'hash', 'cid', 'spec')


def test_fence_contains_worker_but_reports_failure_if_durable_intent_fails(monkeypatch):
    class Unavailable(Store):
        def begin_fencing(self, *args):
            raise OSError('ledger unavailable')
    monkeypatch.setattr(crash_fence, 'inspect', lambda *args: record())
    calls = []
    monkeypatch.setattr(crash_fence, '_run', calls.append)
    with pytest.raises(crash_fence.FenceError):
        crash_fence.fence_bound_worker(Unavailable(), 'attempt', 'hash', 'cid', 'spec')
    assert calls == [['kill', '--signal', 'KILL', 'b' * 64]]
