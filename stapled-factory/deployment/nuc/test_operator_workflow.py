"""Operator workflow scope, uncertain-send behavior and publication boundaries."""
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

BASE = Path(__file__).resolve().parents[2] / 'operator'
def load(name):
    spec = importlib.util.spec_from_file_location(name, BASE / (name + '.py'))
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

cli = load('cli')
nuc = load('nuc')


def spec():
    return {'unit': 'sample-1', 'repo': '/repo', 'baseline': 'main', 'task': 'Fix docs',
            'acceptance': ['Current policy is accurate'], 'allowed_paths': ['README.md'],
            'check_script': '/private/check.py'}


@pytest.mark.parametrize('path', ['/etc/passwd', '../README', 'docs/../../x', '.git/config', 'docs//x'])
def test_task_cannot_select_unsafe_paths(path):
    data = spec();data['allowed_paths'] = [path]
    with pytest.raises(ValueError):cli.validate_spec(data)


def test_task_requires_acceptance_and_rejects_unknown_authority():
    data = spec();data['acceptance'] = []
    with pytest.raises(ValueError):cli.validate_spec(data)
    data = spec();data['merge'] = True
    with pytest.raises(ValueError):cli.validate_spec(data)
    assert cli.validate_spec(spec())['unit'] == 'sample-1'


def test_completed_run_is_observed_without_rebinding_or_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    approval = tmp_path / 'approvals/unit-1';approval.mkdir(parents=True)
    result = {'binding_restored': True, 'revision': 'a' * 40, 'state': {'stage': 'needs_validation'}}
    (approval / 'result.json').write_text(json.dumps(result))
    monkeypatch.setattr(nuc, 'post', lambda *a: pytest.fail('unexpected paid dispatch'))
    monkeypatch.setattr(nuc, 'command', lambda *a: pytest.fail('unexpected service/container mutation'))
    assert nuc.main('run', {'unit': 'unit-1'}) == result


def test_run_does_not_collect_uncertain_result(tmp_path, monkeypatch):
    (tmp_path / 'operator.json').write_text(json.dumps({'unit': 'unit-1'}))
    monkeypatch.setattr(cli, 'remote', lambda *a: (_ for _ in ()).throw(ValueError('uncertain')))
    monkeypatch.setattr(cli, 'copy', lambda *a, **k: pytest.fail('must not collect or publish unknown result'))
    with pytest.raises(ValueError, match='uncertain'):cli.run_task(tmp_path)
    assert not (tmp_path / 'candidate').exists()


def test_publish_rejects_changed_candidate_before_check_or_push(tmp_path, monkeypatch):
    (tmp_path / 'operator.json').write_text('{}')
    (tmp_path / 'result.json').write_text(json.dumps({'revision': 'a' * 40}))
    monkeypatch.setattr(cli, 'command', lambda *a, **k: 'b' * 40)
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **k: pytest.fail('unreviewed code must not execute'))
    with pytest.raises(ValueError, match='changed'):cli.publish(tmp_path)


def test_other_uncertain_unit_blocks_endpoint_rebinding(tmp_path, monkeypatch):
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    (tmp_path / 'operator-active.json').write_text(json.dumps({'unit': 'other'}))
    monkeypatch.setattr(nuc, 'command', lambda *a: pytest.fail('must not mutate endpoint'))
    with pytest.raises(ValueError, match='another task'):nuc.main('run', {'unit': 'unit-1'})


def test_crash_before_bind_reestablishes_config_before_any_dispatch(tmp_path, monkeypatch):
    service = tmp_path / 'service';service.write_text('old')
    calls = []
    def command(*args):
        assert service.read_text() == 'desired'
        calls.append(args)
    monkeypatch.setattr(nuc, 'command', command)
    monkeypatch.setattr(nuc, 'endpoint_config', lambda: '/unit/runtime.json')
    nuc.ensure_binding(service, 'old', 'desired', '/unit/runtime.json', tmp_path / 'no-intent')
    assert calls[-1] == ('systemctl', '--user', 'restart', 'factory-endpoint')


def test_uncertain_intent_never_rebinds_to_send_again(tmp_path, monkeypatch):
    service = tmp_path / 'service';service.write_text('old')
    intent = tmp_path / 'intent';intent.write_text('{}')
    monkeypatch.setattr(nuc, 'command', lambda *a: pytest.fail('must not restart uncertain worker binding'))
    with pytest.raises(ValueError, match='uncertain dispatch'):
        nuc.ensure_binding(service, 'old', 'desired', '/unit/runtime.json', intent)
    assert service.read_text() == 'old'


def test_resume_after_container_stop_uses_saved_export_and_restores(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    monkeypatch.setattr(nuc.Path, 'home', lambda: tmp_path)
    approval = tmp_path / 'approvals/unit-1';approval.mkdir(parents=True)
    service = tmp_path / '.config/systemd/user/factory-endpoint.service';service.parent.mkdir(parents=True)
    previous = 'Environment=FACTORY_RUNTIME_CONFIG=/previous/runtime.json\n'
    desired = 'Environment=FACTORY_RUNTIME_CONFIG=' + str(tmp_path / 'dispatch/unit-1/runtime.json') + '\n'
    service.write_text(desired);(approval / 'previous.service').write_text(previous)
    (approval / 'operator.json').write_text('{}')
    (tmp_path / 'operator-active.json').write_text(json.dumps({'unit': 'unit-1'}))
    (approval / 'result.bundle').write_bytes(b'verified export')
    result = {'revision': 'a'*40, 'worker': 'worker', 'proxy': 'proxy',
              'bundle_sha256': hashlib.sha256(b'verified export').hexdigest()}
    (approval / 'result.json').write_text(json.dumps(result))
    calls = []
    def command(*args):
        assert args[:2] != ('docker', 'exec'), 'stopped containers cannot be queried'
        calls.append(args)
        return json.dumps([{'Config': {'Labels': {'factory.allocation': 'owned'}}}]) if args[:2] == ('docker', 'inspect') else ''
    monkeypatch.setattr(nuc, 'command', command)
    monkeypatch.setattr(nuc, 'post', lambda *a: pytest.fail('completed result must not dispatch'))
    resumed = nuc.main('run', {'unit': 'unit-1'})
    assert resumed['binding_restored']
    assert service.read_text() == previous
    assert not (tmp_path / 'operator-active.json').exists()
    assert ('docker', 'stop', 'worker', 'proxy') in calls


def test_uncertain_existing_work_blocks_new_task(tmp_path, monkeypatch):
    cfg = tmp_path / 'runtime.json';cfg.write_text(json.dumps({'attempt_id': 'other:0'}))
    monkeypatch.setattr(nuc, 'post', lambda *a: {'stage': 'needs_reconciliation'})
    with pytest.raises(ValueError, match='unresolved work'):
        nuc.assert_idle('Environment=FACTORY_RUNTIME_CONFIG=' + str(cfg))


def test_lost_dispatch_response_is_never_resent_on_observation(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    monkeypatch.setattr(nuc.Path, 'home', lambda: tmp_path)
    approval = tmp_path / 'approvals/unit-1';approval.mkdir(parents=True)
    task = tmp_path / 'dispatch/unit-1';task.mkdir(parents=True)
    service = tmp_path / '.config/systemd/user/factory-endpoint.service';service.parent.mkdir(parents=True)
    oldcfg = tmp_path / 'old.json';oldcfg.write_text(json.dumps({'attempt_id': 'previous:0'}))
    old = 'Environment=FACTORY_RUNTIME_CONFIG=' + str(oldcfg) + '\n';service.write_text(old)
    (task / 'runtime.json').write_text(json.dumps({'repo': '/workspace', 'task_file': '/task'}))
    (approval / 'operator.json').write_text(json.dumps({'service_sha256': hashlib.sha256(old.encode()).hexdigest(),
                                                      'release': '/frozen', 'baseline': 'a'*40}))
    sends = []
    def post(route, body):
        if route.startswith('orchestrate/'):
            sends.append(body)
            raise OSError('response lost after acceptance')
        return {'stage': 'validated' if 'previous/' in route else ('building' if sends else 'unclaimed')}
    monkeypatch.setattr(nuc, 'post', post)
    monkeypatch.setattr(nuc, 'release', lambda: '/frozen')
    monkeypatch.setattr(nuc, 'command', lambda *a: '')
    monkeypatch.setattr(nuc, 'endpoint_config', lambda: str(task / 'runtime.json'))
    for _ in range(2):
        with pytest.raises(ValueError, match='unresolved'):
            nuc.main('run', {'unit': 'unit-1', 'polls': 0})
    assert len(sends) == 1
    assert (approval / 'dispatch-intent.json').exists()
    assert (tmp_path / 'operator-active.json').exists()


def test_feature_baseline_is_rejected_before_remote_prepare(tmp_path, monkeypatch):
    data = spec();file = tmp_path / 'spec.json';file.write_text(json.dumps(data))
    def command(*args, **kwargs):
        if 'rev-parse' in args:return 'a'*40
        if 'status' in args:return ''
        if 'remote' in args:return 'https://github.com/example/repo'
        if 'ls-remote' in args:return 'b'*40 + '\trefs/heads/main'
        pytest.fail(str(args))
    monkeypatch.setattr(cli, 'command', command)
    monkeypatch.setattr(cli, 'remote', lambda *a: pytest.fail('must not prepare unreviewed branch history'))
    with pytest.raises(ValueError, match='current GitHub main'):cli.prepare(file)


@pytest.mark.parametrize('failed_step', ['acceptance', 'ci'])
def test_failed_checks_never_push_or_open_pr(tmp_path, monkeypatch, failed_step):
    import hashlib
    repo = tmp_path / 'candidate';(repo / 'scripts').mkdir(parents=True)
    (repo / 'scripts/verify-local.py').write_text('trusted runner')
    check = tmp_path / 'check.py';check.write_text('operator check')
    plan = {'baseline': 'a'*40, 'origin': 'https://github.com/example/repo',
            'check_sha256': hashlib.sha256(check.read_bytes()).hexdigest()}
    (tmp_path / 'operator.json').write_text(json.dumps(plan))
    (tmp_path / 'result.json').write_text(json.dumps({'revision': 'b'*40}))
    def command(*args, **kwargs):
        assert 'push' not in args and args[0] != 'gh', 'failed checks must not publish'
        if 'rev-parse' in args:return 'b'*40
        if 'show' in args:return 'trusted runner'
        return ''
    calls = []
    def execute(args, **kwargs):
        calls.append(args)
        if len(calls) == (1 if failed_step == 'acceptance' else 2):
            raise __import__('subprocess').CalledProcessError(1, args)
    monkeypatch.setattr(cli, 'command', command)
    monkeypatch.setattr(cli.subprocess, 'run', execute)
    with pytest.raises(__import__('subprocess').CalledProcessError):cli.publish(tmp_path)


def test_budget_summary_labels_reservations_without_inventing_actual_cost():
    report = cli.budget_summary({'budgets': [['unit:0', 'auto', 12, 3, 'fingerprint']],
                                'monetary_budgets': [['unit:0', 2000000, 1000000, 'policy', 123, 250000]]})
    assert report['requests_used'] == 3 and report['request_limit'] == 12
    assert report['estimated_reserved_usd'] == .25 and report['estimated_target_usd'] == 2
    assert report['actual_charges'].startswith('unknown')
    assert cli.budget_summary({})['estimated_reserved_usd'] is None
