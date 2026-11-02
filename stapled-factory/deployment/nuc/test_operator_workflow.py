"""Operator workflow scope, uncertain-send behavior and publication boundaries."""
import copy
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import types

import pytest

BASE = Path(__file__).resolve().parents[2] / 'operator'
def load(name):
    spec = importlib.util.spec_from_file_location(name, BASE / (name + '.py'))
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

cli = load('cli')
nuc = load('nuc')
workflow_spec = importlib.util.spec_from_file_location(
    'operator_workflow', Path(__file__).with_name('operator_workflow.py'))
workflow = importlib.util.module_from_spec(workflow_spec)
workflow_spec.loader.exec_module(workflow)
worker_base = Path(__file__).resolve().parents[2] / 'worker'
def load_worker(name):
    spec = importlib.util.spec_from_file_location(name, worker_base / (name + '.py'))
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

upstream_workflow = load_worker('verify_upstream_workflow')
upstream_image = load_worker('verify_upstream_image')
PROVENANCE_FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures/frozen-provenance-operator-join.json'
PROVENANCE_REPORT = Path(__file__).resolve().parents[3] / 'docs/verification/frozen-provenance-operator-join.json'


def provenance_fixture():
    return json.loads(PROVENANCE_FIXTURE.read_text())


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


@pytest.mark.parametrize('data', [None, [], 'spec', 42, {}, {'unit': 'sample-1'}])
def test_spec_must_be_object_with_exact_fields(data):
    with pytest.raises(ValueError):cli.validate_spec(data)


@pytest.mark.parametrize('field,value', [
    ('unit', None), ('unit', 1), ('unit', ''), ('unit', '  '), ('unit', 'bad unit!'),
    ('repo', None), ('repo', ''), ('repo', '   '), ('baseline', []), ('baseline', ''),
    ('task', {}), ('task', '\n'), ('check_script', 3), ('check_script', ''),
    ('acceptance', 'criterion'), ('acceptance', []), ('acceptance', ['']),
    ('acceptance', [' ', 1]), ('allowed_paths', 'README.md'),
])
def test_spec_rejects_invalid_field_values(field, value):
    data = spec();data[field] = value
    with pytest.raises(ValueError):cli.validate_spec(data)


def test_spec_accepts_unicode_text():
    data = spec();data['task'] = 'Dokumentation prüfen — 日本語'
    data['acceptance'] = ['Grüße ✓']
    assert cli.validate_spec(data) is data


def test_completed_run_is_observed_without_rebinding_or_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    approval = tmp_path / 'approvals/unit-1';approval.mkdir(parents=True)
    result = {'binding_restored': True, 'revision': 'a' * 40, 'state': {'stage': 'needs_validation'}}
    (approval / 'result.json').write_text(json.dumps(result))
    monkeypatch.setattr(nuc, 'post', lambda *a: pytest.fail('unexpected paid dispatch'))
    monkeypatch.setattr(nuc, 'command', lambda *a: pytest.fail('unexpected service/container mutation'))
    assert nuc.main('run', {'unit': 'unit-1'}) == result


def checked_provenance(monkeypatch):
    manifest = json.dumps({'manifests': [{'digest': 'sha256:' + 'c'*64}]})
    digest = 'sha256:' + __import__('hashlib').sha256(manifest.encode()).hexdigest()
    image = 'ghcr.io/example/runtime@' + digest
    reviewed = {'repo': 'example/runtime', 'workflow': '.github/workflows/build.yml',
                'revision': 'b'*40, 'run_id': 1, 'event': 'push', 'actor': 'maintainer',
                'job_id': 2, 'job_name': 'Publish', 'step_number': 3,
                'step_name': 'Push image', 'reference': 'ghcr.io/example/runtime:release',
                'publication_style': 'pushing-digest-to-reference'}
    monkeypatch.setitem(upstream_workflow.POLICIES, image, reviewed)
    meta = {'head_repository': {'full_name': 'example/runtime'},
            'repository': {'full_name': 'example/runtime'}, 'head_sha': 'b'*40,
            'path': '.github/workflows/build.yml', 'status': 'completed',
            'conclusion': 'success', 'html_url': 'https://example/run/1',
            'head_branch': 'main', 'event': 'push', 'actor': {'login': 'maintainer'},
            'triggering_actor': {'login': 'maintainer'}}
    job = {'run_id': 1, 'name': 'Publish', 'head_sha': 'b'*40, 'conclusion': 'success',
           'steps': [{'number': 3, 'name': 'Push image', 'conclusion': 'success',
                      'started_at': '2026-10-27T00:00:00Z',
                      'completed_at': '2026-10-27T00:01:00Z'}]}
    meta_json = json.dumps(meta);job_json = json.dumps(job)
    log = ('Publish\tUNKNOWN STEP\t2026-10-27T00:00:30.123Z '
           f'#1 1.2 pushing {digest} to ghcr.io/example/runtime:release')
    def workflow_runner(command, **kwargs):
        assert command[0] in ('gh', 'docker')
        output = (manifest if command[0] == 'docker' else
                  job_json if '/jobs/' in command[-1] else meta_json if command[1] == 'api' else log)
        return subprocess.CompletedProcess(command, 0, output)
    workflow_evidence = upstream_workflow.verify(
        image, reviewed['repo'], reviewed['workflow'], reviewed['revision'], 1,
        runner=workflow_runner)
    identity = 'https://github.com/example/runtime/.github/workflows/build.yml@refs/heads/main'
    issuer = 'https://token.actions.githubusercontent.com'
    def signature_runner(command, **kwargs):
        assert command[:2] == ['cosign', 'verify']
        assert command[command.index('--certificate-identity') + 1] == identity
        assert command[command.index('--certificate-oidc-issuer') + 1] == issuer
        signature = {'critical': {'image': {'docker-manifest-digest': digest}}}
        return subprocess.CompletedProcess(command, 0, json.dumps([signature]))
    registry_evidence = upstream_image.verify(image, identity, issuer, runner=signature_runner)
    trust = {'schema_version': 1, 'publisher_identity': identity, 'issuer': issuer,
             'workflow_repositories': ['example/runtime']}
    workflow_attestation = {'subject_digest': digest,
                            'evidence_sha256': workflow.evidence_digest(workflow_evidence),
                            'predicate_type': 'https://example/factory/workflow-publication/v1'}
    registry_attestation = {'subject_digest': digest,
                            'evidence_sha256': workflow.evidence_digest(registry_evidence),
                            'predicate_type': 'https://example/factory/registry-signature/v1'}
    bundle = {'schema_version': 1, 'image': image,
              'workflow_evidence': workflow_evidence,
              'registry_attestation_evidence': registry_evidence,
              'workflow_attestation': workflow_attestation,
              'registry_attestation': registry_attestation,
              'workflow_attestation_sha256': workflow.evidence_digest(workflow_attestation),
              'registry_attestation_sha256': workflow.evidence_digest(registry_attestation)}
    return workflow.verify_dependency_provenance(bundle, trust), bundle, trust


def test_offline_workflow_and_registry_evidence_bind_distinct_attestation_digests(monkeypatch):
    result, bundle, _ = checked_provenance(monkeypatch)
    assert result['accepted'] is True
    assert result['artifact_digest'] == bundle['image'].rsplit('@', 1)[1]
    assert result['workflow_evidence_sha256'] == workflow.evidence_digest(
        bundle['workflow_evidence'])
    assert result['registry_attestation_evidence_sha256'] == workflow.evidence_digest(
        bundle['registry_attestation_evidence'])
    assert result['workflow_attestation_sha256'] != result['registry_attestation_sha256']
    assert result['workflow_signature_trust_claimed'] is False
    assert result['build_provenance_verified'] is False


def test_trust_policy_stays_separate_from_workflow_and_registry_evidence(monkeypatch):
    result, bundle, trust = checked_provenance(monkeypatch)
    assert result['trust_policy_separated'] is True
    changed_bundle = copy.deepcopy(bundle)
    changed_bundle['workflow_evidence']['publisher_identity'] = trust['publisher_identity']
    with pytest.raises(ValueError, match='trust authority'):
        workflow.verify_dependency_provenance(changed_bundle, trust)
    with pytest.raises(ValueError, match='issuer'):
        workflow.verify_dependency_provenance(bundle, dict(trust, issuer='https://untrusted.example'))
    with pytest.raises(ValueError, match='repository'):
        workflow.verify_dependency_provenance(bundle, dict(trust, workflow_repositories=['attacker/runtime']))


@pytest.mark.parametrize('path,value', [
    ('workflow_evidence.run_url', 'https://attacker.example/run'),
    ('registry_attestation_evidence.publisher_identity', 'attacker'),
    ('workflow_attestation.evidence_sha256', 'a'*64),
    ('registry_attestation.subject_digest', 'sha256:' + 'b'*64),
    ('workflow_attestation_sha256', 'c'*64),
    ('registry_attestation_sha256', 'd'*64),
    ('image', 'ghcr.io/example/runtime@sha256:' + 'e'*64),
])
def test_dependency_digest_or_authority_substitutions_are_rejected(path, value):
    data = provenance_fixture();bundle = data['dependency_provenance_bundle']
    parts = path.split('.');target = bundle
    for part in parts[:-1]:target = target[part]
    target[parts[-1]] = value
    with pytest.raises(ValueError):workflow.verify_dependency_provenance(bundle, data['trust_policy'])


def test_operator_output_joins_exact_validation_receipt_and_provenance():
    data = provenance_fixture()
    provenance = workflow.verify_dependency_provenance(
        data['dependency_provenance_bundle'], data['trust_policy'])
    joined = workflow.verify_operator_receipt_join(
        data['operator_output'], data['validation_receipt'], provenance)
    assert joined['accepted'] is True
    assert joined['unit_id'] == data['operator_output']['unit']
    assert joined['source_bundle_sha256'] == data['validation_receipt']['evidence']['source_archive_digest']
    assert joined['artifact_digest'] == provenance['artifact_digest']
    assert joined['dependency_provenance_bound'] is True


@pytest.mark.parametrize('path,value', [
    ('operator_output.unit', 'other-unit'), ('operator_output.attempt_id', 'unit-1:0:successor'),
    ('operator_output.git_revision', 'c'*40), ('operator_output.bundle_sha256', 'a'*64),
    ('operator_output.artifact_digest', 'sha256:' + 'b'*64),
    ('operator_output.envelope_digest', '0'*64),
    ('validation_receipt.accepted', False),
    ('validation_receipt.envelope_digest', 'c'*64),
    ('validation_receipt.evidence.unit_id', 'other-unit'),
    ('validation_receipt.evidence.attempt_id', 'unit-1:0:successor'),
    ('validation_receipt.evidence.git_revision', 'd'*40),
    ('validation_receipt.evidence.source_archive_digest', 'e'*64),
    ('validation_receipt.evidence.artifact_digest', 'sha256:' + 'f'*64),
    ('validation_receipt.evidence.scope', 'repository-only'),
])
def test_receipt_join_rejects_every_selected_output_substitution(path, value):
    data = provenance_fixture();parts = path.split('.');target = data
    for part in parts[:-1]:target = target[part]
    target[parts[-1]] = value
    provenance = workflow.verify_dependency_provenance(
        data['dependency_provenance_bundle'], data['trust_policy'])
    with pytest.raises(ValueError, match='receipt|operator|provenance|validation'):
        workflow.verify_operator_receipt_join(data['operator_output'], data['validation_receipt'], provenance)


def test_fixture_report_is_in_process_and_never_touches_network_or_services(monkeypatch, capsys):
    def forbidden(*args, **kwargs):pytest.fail('verification must stay in-process')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    for name in ('run', 'Popen', 'check_output', 'call', 'check_call'):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(sys, 'argv', ['operator_workflow.py', str(PROVENANCE_FIXTURE)])
    report = workflow.main()
    assert report['accepted'] is True
    assert report['boundary'] == {'execution': 'local-in-process-fixture',
                                  'network_calls': 0, 'service_restarts': 0,
                                  'production_authorization': False}
    printed = capsys.readouterr().out
    assert json.loads(printed)['fixture_sha256'] == report['fixture_sha256']
    assert printed == PROVENANCE_REPORT.read_text()


@pytest.mark.parametrize('successor_status', [None, 'completed', 'unknown'])
def test_collection_exports_only_selected_completed_attempt(tmp_path, monkeypatch, successor_status):
    import sqlite3
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    monkeypatch.setattr(nuc.Path, 'home', lambda: tmp_path)
    approval = tmp_path / 'approvals/unit-1';approval.mkdir(parents=True)
    run = tmp_path / 'dispatch/unit-1';run.mkdir(parents=True)
    service = tmp_path / '.config/systemd/user/factory-endpoint.service';service.parent.mkdir(parents=True)
    previous = 'Environment=FACTORY_RUNTIME_CONFIG=/prior/runtime.json\n'
    desired = 'Environment=FACTORY_RUNTIME_CONFIG=' + str(run / 'runtime.json') + '\n'
    service.write_text(desired);(approval / 'previous.service').write_text(previous)
    (tmp_path / 'operator-active.json').write_text(json.dumps({'unit': 'unit-1'}))
    (approval / 'dispatch-intent.json').write_text('{}')
    (approval / 'operator.json').write_text(json.dumps({'baseline': 'c'*40, 'allowed_paths': ['app.py']}))
    state = {'stage': 'needs_validation', 'build': {'attempt_id': 'unit-1:0',
             'status': 'completed', 'git_revision': 'a'*40}}
    attempt, revision, worker, proxy = 'unit-1:0', 'a'*40, 'a'*64, 'c'*64
    if successor_status:
        attempt, revision, worker = 'unit-1:0:successor', 'b'*40, 'b'*64
        state['successor'] = {'attempt_id': attempt, 'status': successor_status, 'git_revision': revision}
    store = tmp_path / 'attempt-store';store.mkdir()
    with sqlite3.connect(store / 'attempts.db') as db:
        db.execute('CREATE TABLE allocation_bindings(attempt_id TEXT PRIMARY KEY, binding TEXT)')
        db.execute('INSERT INTO allocation_bindings VALUES (?,?)', ('unit-1:0',
            json.dumps({'worker_id': 'a'*64, 'proxy_id': proxy})))
        if successor_status:
            db.execute('CREATE TABLE restart_reservations(source_attempt TEXT PRIMARY KEY, restart_id TEXT, restart_hash TEXT, restart_conversation_id TEXT, allocation_id TEXT)')
            db.execute('CREATE TABLE restart_claims(source_attempt TEXT PRIMARY KEY, workspace TEXT, baseline TEXT, worker_id TEXT, lifecycle TEXT)')
            db.execute('INSERT INTO restart_reservations VALUES (?,?,?,?,?)',
                ('unit-1:0', attempt, 'request-hash', 'conversation', 'owned'))
            db.execute('INSERT INTO restart_claims VALUES (?,?,?,?,?)',
                ('unit-1:0', str(run / 'successor'), 'c'*40, worker, json.dumps({'started_at': 'original-start'})))
    monkeypatch.setattr(nuc, 'endpoint_config', lambda: str(run / 'runtime.json'))
    monkeypatch.setattr(nuc, 'post', lambda route, body: state)
    effects = []
    def command(*args):
        if args[:2] == ('docker', 'exec'):
            effects.append(args)
            assert args[2] in (worker, proxy), 'must not export another attempt'
            if args[2] == proxy:return '{}'
            if 'rev-parse' in args:return revision
            if 'status' in args:return ''
            if 'diff' in args:return 'app.py'
        if args[:2] == ('docker', 'cp'):
            Path(args[-1]).write_bytes(b'fixture result bundle')
        if args[:2] == ('docker', 'inspect'):
            return json.dumps([{'Config': {'Labels': {'factory.allocation': 'owned'}}}])
        return ''
    monkeypatch.setattr(nuc, 'command', command)
    if successor_status == 'unknown':
        with pytest.raises(ValueError, match='selected completed'):
            nuc.main('run', {'unit': 'unit-1', 'polls': 1})
        assert not effects and not (approval / 'result.bundle').exists()
    else:
        result = nuc.main('run', {'unit': 'unit-1', 'polls': 1})
        assert result['revision'] == revision and result['attempt_id'] == attempt
        assert result['worker'] == worker and result['proxy'] == proxy
        assert result['binding_restored']


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


def test_export_does_not_dereference_worker_symlink_on_host(tmp_path):
    secret = tmp_path / 'operator-private';secret.write_text('synthetic host fixture')
    export = tmp_path / 'result.bundle';export.symlink_to(secret)
    with pytest.raises(ValueError, match='worker symlink'):nuc.bundle_hash(export)
    assert secret.read_text() == 'synthetic host fixture'


def test_observing_published_task_preserves_pr_link_without_redispatch(tmp_path, monkeypatch):
    status = {'status': 'PR_OPEN', 'url': 'https://github.com/example/repo/pull/1', 'revision': 'a'*40}
    (tmp_path / 'status.json').write_text(json.dumps(status))
    monkeypatch.setattr(cli, 'remote', lambda *a: pytest.fail('published result must not dispatch or rebind'))
    assert cli.run_task(tmp_path) == status
    assert json.loads((tmp_path / 'status.json').read_text()) == status


def test_prepare_guides_offline_worker_past_missing_test_tools(tmp_path, monkeypatch):
    """The actual transmitted task must prevent the missing-pytest detour."""
    repo = tmp_path / 'source';repo.mkdir()
    check = tmp_path / 'check.py';check.write_text('pass\n')
    data = spec();data.update(repo=str(repo), check_script=str(check))
    task_file = tmp_path / 'task.json';task_file.write_text(json.dumps(data))
    monkeypatch.setattr(cli, 'RUNS', tmp_path / 'runs')
    monkeypatch.setattr(cli, 'CONTROL', '/test/control')
    revision = 'a' * 40
    def command(*args, **kwargs):
        if 'rev-parse' in args:return revision
        if 'status' in args:return ''
        if 'remote' in args:return 'https://github.com/example/repo'
        if 'ls-remote' in args:return revision + '\trefs/heads/main'
        if 'bundle' in args and 'create' in args:
            Path(args[-2]).write_bytes(b'baseline bundle');return ''
        if 'list-heads' in args:return revision + ' refs/heads/main'
        pytest.fail(str(args))
    monkeypatch.setattr(cli, 'command', command)
    def sign(run):(run / 'image-approval.sig').write_bytes(b'signature fixture')
    monkeypatch.setattr(cli, 'sign', sign)
    transmitted = {}
    def copy(path, unit, **kwargs):transmitted[Path(path).name] = Path(path).read_bytes()
    monkeypatch.setattr(cli, 'copy', copy)
    def remote(action, value):
        if action == 'describe':return {'release': '/frozen', 'service_sha256': 'b'*64,
                                       'images': {}, 'build_inputs': {}}
        if action == 'mkdir':return {}
        if action == 'prepare':return json.loads(transmitted['image-approval.json'])
        pytest.fail(action)
    monkeypatch.setattr(cli, 'remote', remote)
    cli.prepare(task_file)
    task = transmitted['task.txt'].decode()
    assert data['task'] in task and data['acceptance'][0] in task
    assert 'Do not install dependencies' in task
    assert 'If a test runner is unavailable' in task
    assert 'write custom test harnesses' in task
    assert 'commit' in task and 'external' in task
    assert 'batching independent file reads and searches into one terminal call' in task
    assert 'stopping on any failure' in task
    assert 'do not skip them to save calls' in task


def test_abandoned_run_never_observes_or_rebinds(tmp_path, monkeypatch):
    status = {'status': 'ABANDONED', 'completed': False, 'budget': {'retained': True}}
    (tmp_path / 'status.json').write_text(json.dumps(status))
    monkeypatch.setattr(cli, 'remote', lambda *a: pytest.fail('abandoned unit must not rebind'))
    assert cli.run_task(tmp_path) == status


def test_abandon_preserves_status_evidence_and_signs_only_operator_payload(tmp_path, monkeypatch):
    (tmp_path / 'operator.json').write_text(json.dumps({'unit': 'unit'}))
    (tmp_path / 'status.json').write_text(json.dumps({'status': 'NEEDS_INTERVENTION', 'budget': {'used': 12}}))
    calls = []
    def remote(action, data):
        calls.append(action)
        if action == 'prepare_abandon':return {'payload': '{"kind":"operator-abandon-v1"}'}
        assert action == 'abandon' and data['envelope']['signature'] == 'c2ln'
        return {'status': 'ABANDONED', 'completed': False}
    def command(*args, **kwargs):
        assert args[:3] == ('openssl', 'dgst', '-sha256')
        Path(args[args.index('-out') + 1]).write_bytes(b'sig')
    monkeypatch.setattr(cli, 'remote', remote);monkeypatch.setattr(cli, 'command', command)
    assert cli.abandon(tmp_path, 'explicit closure')['completed'] is False
    status = json.loads((tmp_path / 'status.json').read_text())
    assert status['status'] == 'ABANDONED' and status['budget'] == {'used': 12}
    assert calls == ['prepare_abandon', 'abandon']


def test_lost_abandon_response_is_observed_without_creating_active_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    (tmp_path / 'approvals/unit').mkdir(parents=True)
    (tmp_path / 'approvals/unit/operator.json').write_text('{}')
    monkeypatch.setattr(nuc, 'post', lambda *a: {'stage': 'abandoned'})
    monkeypatch.setattr(nuc, 'command', lambda *a: pytest.fail('terminal unit must not touch runtime'))
    assert nuc.main('run', {'unit': 'unit'})['abandoned'] is True
    assert not (tmp_path / 'operator-active.json').exists()
    (tmp_path / 'operator.json').write_text(json.dumps({'unit': 'unit'}))
    (tmp_path / 'status.json').write_text(json.dumps({'status': 'NEEDS_INTERVENTION', 'budget': {'used': 12}}))
    monkeypatch.setattr(cli, 'remote', lambda *a: {'abandoned': True, 'state': {'stage': 'abandoned'}})
    monkeypatch.setattr(cli, 'copy', lambda *a, **k: pytest.fail('no result export after abandonment'))
    assert cli.run_task(tmp_path)['abandoned']
    assert json.loads((tmp_path / 'status.json').read_text())['budget'] == {'used': 12}
    assert not (tmp_path / 'result.json').exists()


def test_operator_config_selects_local_and_remote_installation(tmp_path):
    path = tmp_path / 'operator.json'
    settings = {'ssh_host': 'operator@example-host', 'control_dir': '/srv/factory',
                'signer': str(tmp_path / 'key.pem'), 'runs_dir': str(tmp_path / 'runs'),
                'ingress_port': 29081, 'raw_ingress_port': 28080}
    path.write_text(json.dumps(settings));path.chmod(0o600)
    assert cli.load_config(path) == settings


@pytest.mark.parametrize('mutation', ['missing', 'public', 'symlink', 'unknown', 'relative', 'host_option', 'port'])
def test_operator_config_rejects_unsafe_or_incomplete_settings(tmp_path, mutation):
    path = tmp_path / 'operator.json'
    settings = {'ssh_host': 'factory-control', 'control_dir': '/srv/factory',
                'signer': str(tmp_path / 'key.pem'), 'runs_dir': str(tmp_path / 'runs'),
                'ingress_port': 29081, 'raw_ingress_port': 28080}
    if mutation == 'unknown':settings['api_key'] = 'not-a-real-key'
    if mutation == 'relative':settings['control_dir'] = 'relative/path'
    if mutation == 'host_option':settings['ssh_host'] = '-oProxyCommand=bad'
    if mutation == 'port':settings['ingress_port'] = True
    path.write_text(json.dumps(settings));path.chmod(0o644 if mutation == 'public' else 0o600)
    if mutation == 'missing':path.unlink()
    if mutation == 'symlink':
        target = tmp_path / 'target.json';path.rename(target);path.symlink_to(target)
    with pytest.raises((ValueError, FileNotFoundError)):cli.load_config(path)


def test_remote_helper_uses_configured_root_and_ports(tmp_path, monkeypatch):
    config = {'control_dir': str(tmp_path), 'ingress_port': 29081, 'raw_ingress_port': 28080}
    monkeypatch.setattr(nuc, 'ROOT', tmp_path / 'old')
    monkeypatch.setattr(nuc, 'INGRESS_PORT', 19081)
    monkeypatch.setattr(nuc, 'RAW_INGRESS_PORT', 18080)
    nuc.configure(config)
    assert nuc.ROOT == tmp_path
    assert nuc.INGRESS_PORT == 29081 and nuc.RAW_INGRESS_PORT == 28080
    (tmp_path / 'ingress.token').write_text('synthetic-token')
    class Response:
        def __enter__(self):return self
        def __exit__(self, *args):pass
        def read(self):return b'{}'
    def open_request(req, **kwargs):
        assert req.full_url == 'http://127.0.0.1:29081/WorkUnit/unit/getState'
        return Response()
    monkeypatch.setattr(nuc.urllib.request, 'urlopen', open_request)
    assert nuc.post('WorkUnit/unit/getState', {}) == {}


def test_cli_config_flag_overrides_environment_without_contacting_host(tmp_path):
    import os
    import subprocess
    run = tmp_path / 'run';run.mkdir()
    (run / 'status.json').write_text(json.dumps({'status': 'PREPARED'}))
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'ssh_host': 'unused-host', 'control_dir': '/srv/factory',
        'signer': '/private/key.pem', 'runs_dir': '/private/runs',
        'ingress_port': 29081, 'raw_ingress_port': 28080}));config.chmod(0o600)
    env = dict(os.environ, FACTORY_OPERATOR_CONFIG=str(tmp_path / 'missing.json'))
    result = subprocess.run([sys.executable, str(BASE / 'cli.py'), '--config', str(config),
        'status', str(run)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'status': 'PREPARED'}
    missing = subprocess.run([sys.executable, str(BASE / 'cli.py'), 'status', str(run)],
        env=env, capture_output=True, text=True)
    assert missing.returncode == 2 and 'Cannot load operator config' in missing.stderr


def test_remote_transport_forwards_only_remote_settings(tmp_path, monkeypatch):
    import io
    settings = {'ssh_host': 'operator@other-host', 'control_dir': '/srv/other-factory',
                'signer': str(tmp_path / 'private-key.pem'), 'runs_dir': str(tmp_path / 'private-runs'),
                'ingress_port': 29081, 'raw_ingress_port': 28080}
    path = tmp_path / 'config.json';path.write_text(json.dumps(settings));path.chmod(0o600)
    for name in ('HOST', 'CONTROL', 'SIGNER', 'RUNS', 'INGRESS_PORT', 'RAW_INGRESS_PORT'):
        monkeypatch.setattr(cli, name, getattr(cli, name))
    cli.configure(cli.load_config(path))
    sent = {}
    class Input(io.StringIO):
        def close(self):
            sent['request'] = json.loads(self.getvalue())
            super().close()
    class Process:
        stdin = Input()
        stdout = io.StringIO('{"result":{"observed":true}}\n')
        def wait(self):return 0
    def launch(argv, **kwargs):
        sent['argv'] = argv
        return Process()
    monkeypatch.setattr(cli.subprocess, 'Popen', launch)
    assert cli.remote('describe', {}) == {'observed': True}
    assert sent['argv'][:6] == ['ssh', '-o', 'BatchMode=yes', '-o', 'ForwardAgent=no', 'operator@other-host']
    assert sent['request'] == {'action': 'describe', 'data': {}, 'config': {
        'control_dir': '/srv/other-factory', 'ingress_port': 29081, 'raw_ingress_port': 28080}}
    assert settings['signer'] not in json.dumps(sent)
    assert settings['runs_dir'] not in json.dumps(sent)
