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


def test_router_config_requires_its_own_policy_and_full_body_bound(tmp_path, monkeypatch):
    from pricing_policy import policy_id
    args, config, path = configured(tmp_path, monkeypatch)
    command=config['provision']['proxy_command']
    command[command.index('--model')+1]='auto'
    config['payload']['agent_settings']['llm']['model']='anthropic/auto'
    command[command.index('--pricing-policy-id')+1]=policy_id('auto')
    command[command.index('--worst-case-request-microdollars')+1]=str(reservation_microdollars('auto'))
    path.write_text(json.dumps(config))
    assert operator_cli.load_config(args)[1]['agent_settings']['llm']['model']=='anthropic/auto'
    command[command.index('--pricing-policy-id')+1]=POLICY_ID
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='reservation policy'):operator_cli.load_config(args)
    command[command.index('--pricing-policy-id')+1]=policy_id('auto')
    command[command.index('--worst-case-request-microdollars')+1]='315802'
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='reservation policy'):operator_cli.load_config(args)


def test_bounded_execution_refuses_unquotaed_workspace_before_credential(tmp_path,monkeypatch):
    args,config,path=configured(tmp_path,monkeypatch)
    config['provision']['execution_profile']='bounded-native-v1';path.write_text(json.dumps(config))
    monkeypatch.setattr(operator_cli,'_credential',lambda:pytest.fail('credential read before quota preflight'))
    with pytest.raises(ValueError):operator_cli.run(args)


def test_stall_capability_options_fail_before_credentials_or_dispatch(tmp_path,monkeypatch):
    args,config,path=configured(tmp_path,monkeypatch)
    config['stall_profile']='stall-v1';config['provision']['execution_profile']='bounded-native-v1'
    config['payload']['agent_settings']['llm']['reasoning_effort']='high'
    path.write_text(json.dumps(config))
    monkeypatch.setattr(operator_cli,'_credential',lambda:pytest.fail('no credential before capability validation'))
    assert worker.run_task(args)['status']=='blocked'
    assert not Path(args.attempt_db).exists()


def test_evaluation_observation_is_explicit_and_strict(tmp_path, monkeypatch):
    args, config, path = configured(tmp_path, monkeypatch)
    config['evaluation_observation'] = 'sdk-poll-v1'
    path.write_text(json.dumps(config))
    assert operator_cli.load_config(args)[0]['evaluation_observation'] == 'sdk-poll-v1'
    for value in (None, '', 'sdk-poll-v2', True, {}):
        config['evaluation_observation'] = value; path.write_text(json.dumps(config))
        with pytest.raises(ValueError, match='evaluation observation'):
            operator_cli.load_config(args)


@pytest.mark.parametrize('change', ['none', 'runtime', 'binding'])
def test_evaluation_entrypoint_revalidates_runtime_and_original_binding(tmp_path, monkeypatch, change):
    import evaluation_observation
    import provision_runtime
    args, config, path = configured(tmp_path, monkeypatch)
    config['evaluation_observation'] = 'sdk-poll-v1'
    path.write_text(json.dumps(config))
    monkeypatch.setattr(operator_cli, '_credential', lambda: b'private')
    monkeypatch.setattr(operator_cli.subprocess, 'run', lambda *a, **kw: argparse.Namespace(returncode=0))
    monkeypatch.setattr(evaluation_observation.time, 'sleep', lambda n: pytest.fail('finished observation must return immediately'))
    cid = str(__import__('uuid').uuid5(__import__('uuid').NAMESPACE_URL, 'stapled-factory:' + args.attempt_id))
    gets, bound = [], []
    class Transport:
        def request(self, method, route, payload=None):
            assert method == 'GET'
            gets.append(route)
            return {'id': cid, 'execution_status': 'finished'}
    monkeypatch.setattr(operator_cli, 'DockerTransport', lambda *a: Transport())
    monkeypatch.setattr(operator_cli, 'verify_revision', lambda *a: 'b'*40)
    def inspect(store, attempt, request_hash, conversation, **kwargs):
        bound.append((attempt, request_hash, conversation, kwargs))
        return {'worker_id': ('e' if change=='binding' else 'd')*64}
    monkeypatch.setattr(provision_runtime, 'inspect_bound_allocation', inspect)
    def execute(store, attempt, request, configuration, make_adapter, **kwargs):
        adapter = make_adapter('d'*64)
        assert isinstance(adapter, evaluation_observation.EvaluationObserver)
        if change=='runtime': path.write_text(path.read_text()+' ')
        return adapter.read(cid, None)
    monkeypatch.setattr(worker, 'execute_bound_attempt', execute)
    if change == 'none':
        assert operator_cli.run(args) == {'status':'completed','git_revision':'b'*40}
        assert gets == ['/api/conversations/'+cid]
        assert bound[0][0] == args.attempt_id and bound[0][2] == cid
        canonical = operator_cli.load_config(args)[1]
        expected = __import__('hashlib').sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        assert bound[0][1] == expected
        assert bound[0][3]['workspace_baseline'] == config['baseline']
    else:
        with pytest.raises(ValueError, match='runtime changed|original worker changed'):
            operator_cli.run(args)
        assert not gets


def test_default_entrypoint_does_not_add_observation_loop(tmp_path, monkeypatch):
    args, config, path = configured(tmp_path, monkeypatch)
    monkeypatch.setattr(operator_cli, '_credential', lambda: b'private')
    monkeypatch.setattr(operator_cli.subprocess, 'run', lambda *a, **kw: argparse.Namespace(returncode=0))
    def execute(store, attempt, request, configuration, make_adapter, **kwargs):
        assert type(make_adapter('d'*64)) is operator_cli.OpenHandsAdapter
        return {'status':'unknown'}
    monkeypatch.setattr(worker, 'execute_bound_attempt', execute)
    assert operator_cli.run(args) == {'status':'unknown'}


def test_stall_observer_keeps_its_own_polling_when_evaluation_flag_is_present(tmp_path, monkeypatch):
    import bounded_profile
    import stall_runtime
    from pricing_policy import policy_id
    args, config, path = configured(tmp_path, monkeypatch)
    config['evaluation_observation'] = 'sdk-poll-v1'
    config['stall_profile'] = 'stall-v1'
    config['provision']['execution_profile'] = 'bounded-native-v1'
    config['payload']['agent_settings']['llm']['model'] = 'anthropic/auto'
    command = config['provision']['proxy_command']
    command[command.index('--model')+1] = 'auto'
    command[command.index('--pricing-policy-id')+1] = policy_id('auto')
    command[command.index('--worst-case-request-microdollars')+1] = str(reservation_microdollars('auto'))
    path.write_text(json.dumps(config))
    monkeypatch.setattr(bounded_profile, 'validate_workspace', lambda *a: None)
    monkeypatch.setattr(operator_cli, '_credential', lambda: b'private')
    monkeypatch.setattr(operator_cli.subprocess, 'run', lambda *a, **kw: argparse.Namespace(returncode=0))
    sentinel = object()
    monkeypatch.setattr(stall_runtime, 'StallAdapter', lambda *a: sentinel)
    def execute(store, attempt, request, configuration, make_adapter, **kwargs):
        assert make_adapter('d'*64) is sentinel
        return {'status':'unknown'}
    monkeypatch.setattr(worker, 'execute_bound_attempt', execute)
    assert operator_cli.run(args) == {'status':'unknown'}
