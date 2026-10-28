import argparse
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import operator_cli
import worker
from pricing_policy import POLICY_ID, reservation_microdollars


def configured(tmp_path, monkeypatch):
    task = tmp_path / 'task'
    task.write_text('Implement the task')
    repo = tmp_path / 'repo'
    repo.mkdir()
    successor = tmp_path / 'successor'
    successor.mkdir()
    model = 'deepseek-v4.1-flash:1017'
    config = {'attempt_id': 'unit:0', 'task_file': str(task), 'repo': str(repo),
        'baseline': 'a' * 40, 'successor_workspace': str(successor),
        'payload': {'agent_settings': {'llm': {'model': 'anthropic/' + model,
            'base_url': 'http://127.0.0.1:8123', 'api_key': 'dummy', 'max_output_tokens': 1024}}},
        'provision': {'workspace': str(repo), 'proxy_command': ['python3', 'proxy.py',
            '--model', model, '--attempt-id', 'unit:0', '--pricing-policy-id', POLICY_ID,
            '--ceiling-microdollars', '2000000', '--worst-case-request-microdollars',
            str(reservation_microdollars(model)), '--max-requests', '6']}}
    config_path = tmp_path / 'runtime.json'
    config_path.write_text(json.dumps(config))
    config_path.chmod(0o600)
    monkeypatch.setenv('FACTORY_RUNTIME_CONFIG', str(config_path))
    (tmp_path/'state').mkdir()
    (tmp_path/'anchor').mkdir()
    args = argparse.Namespace(task_file=str(task), repo=str(repo), attempt_id='unit:0',
        attempt_db=str(tmp_path/'state/attempt.db'), attempt_anchor=str(tmp_path/'anchor/mark'),
        successor_only=False, successor_reconcile_only=False, successor_preflight_only=False,
        reconcile_only=False)
    return args, config, config_path


def test_real_entrypoint_passes_bound_request_and_credential_to_coordinator(tmp_path, monkeypatch):
    args, config, path = configured(tmp_path, monkeypatch)
    monkeypatch.setattr(operator_cli, '_credential', lambda: b'private')
    observed = {}
    def execute(store, attempt, request, configuration, adapter, *, reconcile_only):
        observed.update(attempt=attempt, request=request, configuration=configuration, reconcile=reconcile_only)
        return {'status': 'unknown', 'git_revision': '', 'conversation_id': 'cid'}
    monkeypatch.setattr(worker, 'execute_bound_attempt', execute)
    assert worker.run_task(args)['status'] == 'unknown'
    assert observed['attempt'] == 'unit:0'
    assert observed['request']['initial_message']['content'][0]['text'] == 'Implement the task'
    assert observed['configuration']['gateway_key'] == b'private'
    assert observed['configuration']['workspace_baseline'] == 'a' * 40
    assert not observed['reconcile']


def test_successor_uses_same_request_and_never_loads_gateway_key(tmp_path, monkeypatch):
    args, config, path = configured(tmp_path, monkeypatch)
    args.successor_only = True
    monkeypatch.setattr(operator_cli, '_credential', lambda: pytest.fail('successor must reuse proxy'))
    observed = {}
    def execute(store, attempt, request, configuration, adapter, **kwargs):
        observed.update(request=request, configuration=configuration, **kwargs)
        return {'status': 'unknown', 'git_revision': '', 'conversation_id': 'cid'}
    monkeypatch.setattr(worker, 'execute_bound_successor', execute)
    assert worker.run_task(args)['status'] == 'unknown'
    assert observed['workspace'] == config['successor_workspace']
    assert observed['baseline'] == config['baseline']
    assert 'gateway_key' not in observed['configuration']


def test_untrusted_config_and_underreservation_fail_closed(tmp_path, monkeypatch):
    args, config, path = configured(tmp_path, monkeypatch)
    path.chmod(0o644)
    assert worker.run_task(args)['status'] == 'blocked'
    path.chmod(0o600)
    config['provision']['proxy_command'][-3] = '1'
    path.write_text(json.dumps(config))
    assert worker.run_task(args)['status'] == 'blocked'
    assert not Path(args.attempt_db).exists()


@pytest.mark.parametrize('scope', ['production', '', None, 'Sandbox'])
def test_non_sandbox_scope_blocks_before_credential_or_dispatch(tmp_path, monkeypatch, scope):
    args, config, path = configured(tmp_path, monkeypatch)
    config['deployment_scope'] = scope
    path.write_text(json.dumps(config))
    monkeypatch.setattr(operator_cli, '_credential', lambda: pytest.fail('credential accessed'))
    monkeypatch.setattr(worker, 'execute_bound_attempt', lambda *a, **kw: pytest.fail('dispatched'))
    with pytest.raises(ValueError, match='production scope unavailable'):
        operator_cli.run(args)


def test_nuc_private_key_reference_and_negative_controls(tmp_path, monkeypatch):
    key = tmp_path / 'gateway.key'
    key.write_bytes(b'fixture-private-key\n')
    key.chmod(0o600)
    monkeypatch.setenv('FACTORY_GATEWAY_KEY_FILE', str(key))
    monkeypatch.setattr(operator_cli.subprocess, 'run', lambda *a, **kw: pytest.fail('no YAML parser needed'))
    assert operator_cli._credential() == b'fixture-private-key'
    key.chmod(0o644)
    with pytest.raises(ValueError, match='ownership or mode'):
        operator_cli._credential()
    key.chmod(0o600)
    key.write_bytes(b'bad\nkey')
    with pytest.raises(ValueError, match='unavailable'):
        operator_cli._credential()
    link = tmp_path / 'link'
    link.symlink_to(key)
    monkeypatch.setenv('FACTORY_GATEWAY_KEY_FILE', str(link))
    with pytest.raises(OSError):
        operator_cli._credential()
