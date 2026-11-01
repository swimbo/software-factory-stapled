"""Pre-binding failures are counted only with independent zero-activity proof."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

WORKER = Path(__file__).resolve().parents[2]/'worker'
sys.path[:0] = [str(WORKER), str(Path(__file__).parent)]
from guarded_store import GuardedAttemptStore, StoreQuarantined


def load():
    spec = importlib.util.spec_from_file_location('evaluation_unbound_subject', Path(__file__).with_name('evaluation_trials.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def original(tmp_path, monkeypatch):
    mod = load()
    (tmp_path/'attempt-store').mkdir()
    (tmp_path/'attempt-anchor').mkdir()
    store = GuardedAttemptStore.initialize(tmp_path/'attempt-store/attempts.db', tmp_path/'attempt-anchor/watermark.json')
    store.reserve('unit:0', 'request', 'conversation')
    trial = tmp_path/'dispatch/unit'; trial.mkdir(parents=True)
    intent = trial/'baseline.dispatch-intent.json'
    intent.write_text(json.dumps({'request': {'unitId': 'unit'}, 'started_at_unix': 10}))
    monkeypatch.setattr(mod, 'post', lambda *args: {'stage': 'failed'})
    monkeypatch.setattr(mod, '_gateway', lambda *args: [])
    calls = []
    def command(*args, **kwargs):
        calls.append(args)
        return ''
    monkeypatch.setattr(mod, 'command', command)
    return mod, {'root': str(tmp_path)}, {'unit': 'unit', 'task_id': 'task', 'profile': 'baseline'}, store, intent, calls


@pytest.mark.parametrize('stage', ['failed', 'needs_reconciliation'])
@pytest.mark.parametrize('reserved_allocation', [False, True])
def test_proven_prebinding_failure_is_zero_and_preserves_original_reservation(original, monkeypatch, stage, reserved_allocation):
    mod, config, entry, store, intent, calls = original
    allocation = None
    if reserved_allocation:
        _, allocation = store.reserve_allocation('unit:0', 'request', 'conversation', 'spec')
    before = (store.db_path.read_bytes(), store.anchor_path.read_bytes(), intent.read_bytes())
    monkeypatch.setattr(mod, 'post', lambda *args: {'stage': stage})
    result = mod._collect_unbound(config, entry, 10, 12.5)
    assert result['accepted'] is False
    assert result['execution_contract_passed'] is False
    assert result['requests'] == result['input_tokens'] == result['output_tokens'] == 0
    assert result['estimated_reserved_usd'] == 0
    assert result['accounting_complete'] is True and result['prebinding_zero_proven'] is True
    assert result['root_reservation_retained'] is True
    assert result['state']['stage'] == stage and result['allocation_id'] == allocation
    assert result['elapsed_s'] == 2.5
    assert store.lookup('unit:0', 'request', 'conversation') == ('unknown', None)
    assert before == (store.db_path.read_bytes(), store.anchor_path.read_bytes(), intent.read_bytes())
    if allocation:
        assert calls == [('docker', 'ps', '-aq', '--filter', 'label=factory.allocation=' + allocation)]
    else:
        assert calls == []


def test_gateway_activity_without_binding_cannot_be_reported_zero(original, monkeypatch):
    mod, config, entry, *_ = original
    monkeypatch.setattr(mod, '_gateway', lambda *args: [{'id': 'actual-call'}])
    with pytest.raises((ValueError, StoreQuarantined)):
        mod._collect_unbound(config, entry, 10, 12)


def test_partial_original_namespace_cannot_be_reported_zero(original, monkeypatch):
    mod, config, entry, store, *_ = original
    store.reserve_allocation('unit:0', 'request', 'conversation', 'spec')
    monkeypatch.setattr(mod, 'command', lambda *args, **kwargs: 'partial-container-id')
    with pytest.raises((ValueError, StoreQuarantined)):
        mod._collect_unbound(config, entry, 10, 12)


@pytest.mark.parametrize('stage', ['unclaimed', 'working', 'needs_validation', 'validated'])
def test_live_or_successful_original_is_not_reclassified_as_zero_failure(original, monkeypatch, stage):
    mod, config, entry, *_ = original
    monkeypatch.setattr(mod, 'post', lambda *args: {'stage': stage})
    with pytest.raises(ValueError, match='uncertain'):
        mod._collect_unbound(config, entry, 10, 12)


def test_wrong_dispatch_identity_blocks_collection(original):
    mod, config, entry, store, intent, _ = original
    intent.write_text(json.dumps({'request': {'unitId': 'different-unit'}}))
    with pytest.raises(ValueError, match='identity'):
        mod._collect_unbound(config, entry, 10, 12)


def test_binding_requires_original_namespace_collection(original):
    mod, config, entry, store, *_ = original
    _, allocation = store.reserve_allocation('unit:0', 'request', 'conversation', 'spec')
    store.bind_allocation('unit:0', 'request', 'conversation', 'spec', allocation, {'worker_id': 'a'*64})
    with pytest.raises((ValueError, StoreQuarantined)):
        mod._collect_unbound(config, entry, 10, 12)
