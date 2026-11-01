"""Operator receipt negative controls; no Docker/model/service invocation."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest

WORKER = Path(__file__).resolve().parents[2]/'worker'
sys.path[:0] = [str(WORKER), str(Path(__file__).parent)]


def load():
    spec = importlib.util.spec_from_file_location('evaluation_trials', Path(__file__).with_name('evaluation_trials.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.fixture
def collection(tmp_path, monkeypatch):
    mod = load()
    monkeypatch.setitem(sys.modules, 'evaluation_events', types.SimpleNamespace(audit_events=lambda *a: {'truncations': 0, 'full_completed': True, 'diagnostic_messages': 0}))
    binding = {'worker_id': 'a'*64, 'proxy_id': 'b'*64}
    monkeypatch.setattr(mod, 'trial_binding', lambda *a: binding)
    monkeypatch.setattr(mod, '_stop_original', lambda *a: ({'status': 'stopped-failed'}, 'cid', 'allocation'))
    monkeypatch.setattr(mod, 'post', lambda *a: {'stage': 'failed'})
    info = {'HostConfig': {'NetworkMode': 'none'}, 'Mounts': [{'Destination': '/gateway'}, {'Destination': '/workspace'}]}
    monkeypatch.setattr(mod, 'command', lambda *a, **k: json.dumps([info]))
    final = {'ledger': {'budgets': [['unit:0', 'auto', 12, 1, 'f'*64]],
                       'monetary_budgets': [['unit:0', 2000000, 100000, 'policy', 2000000000, 100000]]},
             'telemetry': {'requests_observed': 1, 'complete_responses': 1, 'input_tokens': 10,
                           'cache_read_input_tokens': 80, 'cache_creation_input_tokens': 5,
                           'output_tokens': 2, 'estimated_reserved_microdollars': 100000}}
    monkeypatch.setattr(mod, 'final_accounting', lambda *a: final)
    gateway = [{'id': 'request1', 'provider': 'lane', 'model': 'model', 'served_model': None,
                'status': 'success', 'prompt_tokens': 95, 'completion_tokens': 2, 'cached_read_tokens': 80}]
    monkeypatch.setattr(mod, '_gateway', lambda *a: gateway)
    (tmp_path/'dispatch/unit').mkdir(parents=True)
    config = {'root': str(tmp_path), 'setup': str(tmp_path/'setup')}
    entry = {'unit': 'unit', 'task_id': 'task', 'profile': 'stall-v1', 'allowed_paths': ['x']}
    runtime = {'repo': str(tmp_path/'workspace'), 'baseline': 'base'}
    return mod, config, entry, runtime, final, gateway


def collect(fixture):
    mod, config, entry, runtime, *_ = fixture
    return mod.collect(config, entry, runtime, 1, 11)


def test_primary_prompt_tokens_include_cached_reads_and_creation(collection):
    result = collect(collection)
    assert result['input_tokens'] == 95
    assert result['estimated_reserved_usd'] == .1


def test_missing_cache_coverage_is_unknown_not_zero(collection):
    collection[4]['telemetry']['cache_read_input_tokens'] = None
    assert collect(collection)['input_tokens'] is None


def test_delivery_status_cannot_be_misreported_as_zero_false_positives(collection):
    root = Path(collection[1]['root']); path = root/'attempt-anchor/stalls/allocation.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'effect': 'intervention_sent', 'delivery_status': 'sent'}))
    result = collect(collection)
    assert result['stall_false_positives'] != 0


def test_request_count_parity_cannot_hide_gateway_token_mismatch(collection):
    collection[5][0]['prompt_tokens'] = 12345
    assert collect(collection)['accounting_complete'] is False


def test_request_count_parity_cannot_hide_reservation_mismatch(collection):
    collection[4]['telemetry']['estimated_reserved_microdollars'] = 999999
    assert collect(collection)['accounting_complete'] is False


def test_isolation_failure_stops_before_cleanup_and_candidate_execution(collection, monkeypatch):
    mod = collection[0]
    monkeypatch.setattr(mod, 'command', lambda *a, **k: json.dumps([{'HostConfig': {'NetworkMode': 'host'}, 'Mounts': []}]))
    def unexpected(*a): raise AssertionError('cleanup reached after failed boundary observation')
    monkeypatch.setattr(mod, '_stop_original', unexpected)
    with pytest.raises(ValueError, match='isolation'): collect(collection)


def test_checker_timeout_removes_only_exact_owned_process(tmp_path, monkeypatch):
    mod = load(); calls = []
    def timeout(*a, **k): raise subprocess.TimeoutExpired('docker', 20)
    monkeypatch.setattr(mod.subprocess, 'run', timeout)
    def command(*args, **kwargs):
        calls.append(args)
        return json.dumps([{'Id': 'check-full-id', 'Image': 'image', 'Config': {'Labels': {'factory.evaluation.unit': 'unit'}}}])
    monkeypatch.setattr(mod, 'command', command)
    result = mod.check_candidate(tmp_path, {'unit': 'unit', 'task_id': 'task'}, {'provision': {'helper_image': 'image'}}, tmp_path/'candidate')
    assert result == {'passed': False, 'timed_out': True}
    assert calls == [('docker', 'inspect', 'factory-eval-check-unit'), ('docker', 'rm', '-f', 'check-full-id')]


def test_checker_timeout_refuses_foreign_process_removal(tmp_path, monkeypatch):
    mod = load(); calls = []
    def timeout(*a, **k): raise subprocess.TimeoutExpired('docker', 20)
    monkeypatch.setattr(mod.subprocess, 'run', timeout)
    def command(*args, **kwargs):
        calls.append(args)
        return json.dumps([{'Id': 'foreign', 'Image': 'image', 'Config': {'Labels': {'factory.evaluation.unit': 'other'}}}])
    monkeypatch.setattr(mod, 'command', command)
    with pytest.raises(ValueError, match='ownership'):
        mod.check_candidate(tmp_path, {'unit': 'unit', 'task_id': 'task'}, {'provision': {'helper_image': 'image'}}, tmp_path/'candidate')
    assert calls == [('docker', 'inspect', 'factory-eval-check-unit')]


def test_diagnostic_without_journal_cannot_be_assumed_no_intervention(collection, monkeypatch):
    monkeypatch.setitem(sys.modules, 'evaluation_events', types.SimpleNamespace(
        audit_events=lambda *a: {'truncations': 0, 'full_completed': True, 'diagnostic_messages': 1}))
    assert collect(collection)['stall_false_positives'] != 0


@pytest.mark.parametrize('diagnostics,expected', [(0,False),(1,True),(2,False),(None,False)])
def test_independent_oracle_pass_requires_exactly_one_executed_dependency_diagnostic(collection, monkeypatch, diagnostics, expected):
    import revision
    mod, config, entry, runtime, *_ = collection
    entry['diagnostic_dependency'] = 'heldout_dependency'
    monkeypatch.setitem(sys.modules, 'evaluation_events', types.SimpleNamespace(audit_events=lambda *a: {
        'truncations': 0, 'full_completed': True, 'diagnostic_messages': 0,
        'dependency_diagnostic_commands': diagnostics, 'dependency_error_observations': 1, 'install_commands': 0}))
    monkeypatch.setattr(mod, 'post', lambda *a: {'stage':'needs_validation','build':{'git_revision':'revision'}})
    monkeypatch.setattr(revision, 'verify_revision', lambda *a:'revision')
    original = mod.command
    monkeypatch.setattr(mod, 'command', lambda *a, **k:'x' if a[0]=='git' else original(*a,**k))
    monkeypatch.setattr(mod, 'freeze_candidate', lambda *a:'archive-hash')
    monkeypatch.setattr(mod, 'check_candidate', lambda *a:{'passed':True})
    result = collect(collection)
    assert result['candidate_check']['passed'] is True
    assert result['execution_contract_passed'] is expected
    assert result['accepted'] is expected


def test_live_event_metadata_is_saved_before_stop_and_reused_after_collection_failure(collection, monkeypatch):
    from contextlib import contextmanager
    import guarded_store
    mod, config, entry, runtime, *_ = collection
    entry['profile'] = 'baseline'; calls = []
    class DB:
        def execute(self,*a):return self
        def fetchone(self):return ('cid',)
    class Store:
        def __init__(self,*a):pass
        @contextmanager
        def _transaction(self):yield DB(),None
    monkeypatch.setattr(guarded_store,'GuardedAttemptStore',Store)
    events={'truncations':None,'full_completed':True,'diagnostic_messages':0}
    monkeypatch.setattr(mod,'_live_events',lambda *a:calls.append('live-events') or events)
    def stop(*a):
        calls.append('stop-original')
        assert (Path(config['root'])/'dispatch/unit/evaluation.observed.json').exists()
        raise TimeoutError('after original observation persisted')
    monkeypatch.setattr(mod,'_stop_original',stop)
    with pytest.raises(TimeoutError):collect(collection)
    monkeypatch.setattr(mod,'_live_events',lambda *a:(_ for _ in ()).throw(AssertionError('never requery stopped SDK')))
    monkeypatch.setattr(mod,'_stop_original',lambda *a:({'status':'stopped-failed'},'cid','allocation'))
    result=collect(collection)
    assert calls==['live-events','stop-original']
    assert result['events']==events and result['elapsed_s']==10
