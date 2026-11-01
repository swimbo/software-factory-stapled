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


def test_validation_status_reads_original_without_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    approval = tmp_path / 'approvals/unit-1';approval.mkdir(parents=True)
    result = {'revision': 'a'*40, 'attempt_id': 'unit-1:0', 'binding_restored': True}
    (approval / 'result.json').write_text(json.dumps(result))
    proof = {'unit_id': 'unit-1', 'attempt_id': 'unit-1:0', 'git_revision': 'a'*40,
             'passed': True, 'receipt': {'git_revision': 'a'*40},
             'checks_digest': 'b'*64, 'source_archive_digest': 'c'*64,
             'scenario_digest': 'd'*64, 'artifact_digest': 'sha256:'+'e'*64,
             'deployment_id': 'f'*64, 'scope': 'disposable-candidate'}
    state = {'stage': 'validated', 'build': {'attempt_id': 'unit-1:0', 'status': 'completed',
             'git_revision': 'a'*40}, 'validation': proof, 'receipt': proof['receipt']}
    monkeypatch.setattr(nuc, 'post', lambda route, body: state)
    monkeypatch.setattr(nuc, 'command', lambda *a: pytest.fail('observation must not mutate runtime'))
    observed = nuc.main('validation_status', {'unit': 'unit-1'})
    assert observed['accepted'] is True and observed['validation'] == proof
    for mutation in ('revision', 'receipt', 'successor'):
        changed = dict(state)
        if mutation == 'revision':changed['validation'] = {**proof, 'git_revision': 'd'*40}
        if mutation == 'receipt':changed['receipt'] = {'git_revision': 'd'*40}
        if mutation == 'successor':changed['successor'] = {'attempt_id': 'unit-1:0:successor', 'status': 'completed', 'git_revision': 'a'*40}
        monkeypatch.setattr(nuc, 'post', lambda route, body: changed)
        with pytest.raises(ValueError, match='validation'):
            nuc.main('validation_status', {'unit': 'unit-1'})


def test_validation_status_requires_full_receipt_and_selected_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    approval = tmp_path / 'approvals/unit-1';approval.mkdir(parents=True)
    (approval / 'result.json').write_text(json.dumps({'revision': 'a'*40, 'attempt_id': 'unit-1:0', 'binding_restored': True}))
    state = {'stage': 'needs_validation', 'build': {'attempt_id': 'unit-1:0', 'status': 'completed', 'git_revision': 'a'*40}}
    monkeypatch.setattr(nuc, 'post', lambda route, body: state)
    assert nuc.main('validation_status', {'unit': 'unit-1'})['accepted'] is False
    state.update(stage='validated', validation={'unit_id': 'unit-1', 'attempt_id': 'unit-1:0',
                 'git_revision': 'a'*40, 'passed': True})
    with pytest.raises(ValueError, match='validation'):
        nuc.main('validation_status', {'unit': 'unit-1'})


def test_cli_validation_status_observes_configured_unit(tmp_path, monkeypatch, capsys):
    (tmp_path / 'operator.json').write_text(json.dumps({'unit': 'unit-1'}))
    monkeypatch.setattr(sys, 'argv', ['cli.py', 'validation-status', str(tmp_path)])
    monkeypatch.setattr(cli, 'load_config', lambda path: {})
    monkeypatch.setattr(cli, 'configure', lambda config: None)
    calls = []
    monkeypatch.setattr(cli, 'remote', lambda action, data: calls.append((action, data)) or {'accepted': False})
    cli.main()
    assert calls == [('validation_status', {'unit': 'unit-1'})]
    assert json.loads(capsys.readouterr().out) == {'accepted': False}


def test_attested_build_is_bound_to_collected_attempt_and_never_repeated(tmp_path, monkeypatch):
    import hashlib
    plan = {'unit': 'unit-1', 'baseline': 'a'*40}
    bundle = b'original bundle'
    result = {'revision': 'b'*40, 'attempt_id': 'unit-1:0', 'bundle_sha256': hashlib.sha256(bundle).hexdigest()}
    (tmp_path/'operator.json').write_text(json.dumps(plan));(tmp_path/'result.json').write_text(json.dumps(result))
    (tmp_path/'result.bundle').write_bytes(bundle);(tmp_path/'candidate').mkdir()
    dockerfile = tmp_path/'recipe';dockerfile.write_text('FROM scratch\n')
    key = tmp_path/'fixture-key';key.write_bytes(b'fixture');key.chmod(0o600)
    monkeypatch.setattr(cli, 'command', lambda *a, **k: 'b'*40 if 'rev-parse' in a else '')
    monkeypatch.setattr(cli, 'remote', lambda action, data: {'accepted': False, 'stage': 'needs_validation',
                         'attempt_id': 'unit-1:0', 'revision': 'b'*40})
    monkeypatch.setattr(cli.subprocess, 'check_output', lambda args: b'exact archive')
    calls = []
    def build(args, **kwargs):
        calls.append(args)
        assert (tmp_path/'build-intent.json').is_file()
        raise __import__('subprocess').CalledProcessError(1, args)
    monkeypatch.setattr(cli.subprocess, 'run', build)
    for _ in range(2):
        with pytest.raises((ValueError, __import__('subprocess').CalledProcessError)):
            cli.build_candidate(tmp_path, dockerfile, key)
    assert len(calls) == 1
    assert calls[0][calls[0].index('--revision')+1] == result['revision']
    assert calls[0][calls[0].index('--baseline')+1] == plan['baseline']


def test_attested_build_rejects_changed_selection_before_any_build(tmp_path, monkeypatch):
    import hashlib
    bundle = b'original bundle'
    (tmp_path/'operator.json').write_text(json.dumps({'unit': 'unit-1', 'baseline': 'a'*40}))
    (tmp_path/'result.json').write_text(json.dumps({'revision': 'b'*40, 'attempt_id': 'unit-1:0',
                                                   'bundle_sha256': hashlib.sha256(bundle).hexdigest()}))
    (tmp_path/'result.bundle').write_bytes(bundle);(tmp_path/'candidate').mkdir()
    dockerfile = tmp_path/'recipe';dockerfile.write_text('FROM scratch\n')
    key = tmp_path/'fixture-key';key.write_bytes(b'fixture');key.chmod(0o600)
    monkeypatch.setattr(cli, 'command', lambda *a, **k: 'b'*40 if 'rev-parse' in a else '')
    monkeypatch.setattr(cli, 'remote', lambda action, data: {'accepted': False, 'stage': 'needs_validation',
                         'attempt_id': 'unit-1:0:successor', 'revision': 'c'*40})
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **k: pytest.fail('must not build stale attempt'))
    with pytest.raises(ValueError, match='selected'):
        cli.build_candidate(tmp_path, dockerfile, key)
    assert not (tmp_path/'build-intent.json').exists()


def test_attested_build_returns_verified_original_manifest(tmp_path, monkeypatch):
    import hashlib
    bundle, archive, recipe = b'bundle', b'archive', b'FROM scratch\n'
    (tmp_path/'operator.json').write_text(json.dumps({'unit': 'unit-1', 'baseline': 'a'*40}))
    (tmp_path/'result.json').write_text(json.dumps({'revision': 'b'*40, 'attempt_id': 'unit-1:0',
                                                   'bundle_sha256': hashlib.sha256(bundle).hexdigest()}))
    (tmp_path/'result.bundle').write_bytes(bundle);(tmp_path/'candidate').mkdir()
    dockerfile = tmp_path/'recipe';dockerfile.write_bytes(recipe)
    key = tmp_path/'fixture-key';key.write_bytes(b'fixture');key.chmod(0o600)
    monkeypatch.setattr(cli, 'command', lambda *a, **k: 'b'*40 if 'rev-parse' in a else '')
    monkeypatch.setattr(cli, 'remote', lambda action, data: {'accepted': False, 'stage': 'needs_validation',
                         'attempt_id': 'unit-1:0', 'revision': 'b'*40})
    monkeypatch.setattr(cli.subprocess, 'check_output', lambda args: archive)
    def build(args, **kwargs):
        out = Path(args[args.index('--output')+1]);out.mkdir()
        (out/'source.tar').write_bytes(archive);(out/'Dockerfile').write_bytes(recipe)
        (out/'diff').write_bytes(b'diff');(out/'build.sig').write_bytes(b'fixture signature');(out/'image.tar').write_bytes(b'fixture image')
        (out/'build.json').write_text(json.dumps({'revision': 'b'*40, 'baseline': 'a'*40,
            'archive_sha256': hashlib.sha256(archive).hexdigest(), 'dockerfile_sha256': hashlib.sha256(recipe).hexdigest(),
            'diff_sha256': hashlib.sha256(b'diff').hexdigest()}))
    monkeypatch.setattr(cli.subprocess, 'run', build)
    receipt = cli.build_candidate(tmp_path, dockerfile, key)
    assert receipt['status'] == 'BUILD_OUTPUT_PRESENT' and receipt['attempt_id'] == 'unit-1:0'
    assert receipt['signature_verified'] is False and receipt['image_verified'] is False
    assert receipt['manifest']['archive_sha256'] == hashlib.sha256(archive).hexdigest()
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **k: pytest.fail('observation must not rebuild'))
    assert cli.observe_candidate_build(tmp_path) == receipt
    (tmp_path/'attested-build/source.tar').write_bytes(b'changed')
    with pytest.raises(ValueError, match='differs'):
        cli.observe_candidate_build(tmp_path)


def test_nuc_transfer_status_checks_retained_original_files(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    approval = tmp_path/'approvals/unit-1';approval.mkdir(parents=True)
    names = ('image.tar', 'source.tar', 'diff', 'Dockerfile', 'build.json', 'build.sig', 'result.bundle')
    expected = {name: hashlib.sha256(name.encode()).hexdigest() for name in names}
    state = {'unit': 'unit-1', 'attempt_id': 'unit-1:0', 'revision': 'a'*40,
             'baseline': 'b'*40, 'files': expected}
    (approval/'result.json').write_text(json.dumps({'revision': 'a'*40, 'attempt_id': 'unit-1:0', 'binding_restored': True}))
    (approval/'operator.json').write_text(json.dumps({'baseline': 'b'*40}))
    monkeypatch.setattr(nuc, 'post', lambda route, body: {'stage': 'needs_validation',
        'build': {'status': 'completed', 'attempt_id': 'unit-1:0', 'git_revision': 'a'*40}})
    nuc.main('validation_transfer_begin', state)
    target = approval/'validation-inputs'
    for name in names:(target/name).write_text(name)
    assert nuc.main('validation_transfer_status', {'unit': 'unit-1'})['complete'] is True
    (target/'image.tar').write_text('changed')
    assert nuc.main('validation_transfer_status', {'unit': 'unit-1'})['complete'] is False
    with pytest.raises(FileExistsError):nuc.main('validation_transfer_begin', state)
    assert (target/'image.tar').read_text() == 'changed'


def test_transfer_lost_response_cannot_repeat_or_copy(tmp_path, monkeypatch):
    import hashlib
    names = ('image.tar', 'source.tar', 'diff', 'Dockerfile', 'build.json', 'build.sig')
    output = tmp_path/'attested-build';output.mkdir()
    for name in names:(output/name).write_text(name)
    (tmp_path/'result.bundle').write_text('bundle')
    (tmp_path/'operator.json').write_text(json.dumps({'unit': 'unit-1', 'baseline': 'b'*40}))
    (tmp_path/'result.json').write_text(json.dumps({'revision': 'a'*40, 'attempt_id': 'unit-1:0',
        'bundle_sha256': hashlib.sha256(b'bundle').hexdigest()}))
    monkeypatch.setattr(cli, 'observe_candidate_build', lambda run: {'unit': 'unit-1', 'attempt_id': 'unit-1:0', 'revision': 'a'*40})
    calls = []
    def remote(action, data):
        calls.append(action)
        if action == 'validation_status':return {'accepted': False, 'stage': 'needs_validation', 'attempt_id': 'unit-1:0', 'revision': 'a'*40}
        raise OSError('response lost')
    monkeypatch.setattr(cli, 'remote', remote)
    monkeypatch.setattr(cli, 'copy', lambda *a, **k: pytest.fail('uncertain begin must not send files'))
    with pytest.raises(OSError):cli.transfer_validation_inputs(tmp_path)
    with pytest.raises(ValueError, match='original transfer'):cli.transfer_validation_inputs(tmp_path)
    assert calls.count('validation_transfer_begin') == 1 and (tmp_path/'transfer-intent.json').exists()


def test_transfer_observation_rejects_different_remote_file_binding(tmp_path, monkeypatch):
    intent = {'unit': 'unit-1', 'revision': 'a'*40, 'attempt_id': 'unit-1:0', 'files': {'image.tar': 'b'*64}}
    (tmp_path/'transfer-intent.json').write_text(json.dumps(intent))
    status = {'revision': intent['revision'], 'attempt_id': intent['attempt_id'],
              'complete': True, 'files': {'image.tar': True}, 'expected': {'image.tar': 'c'*64}}
    monkeypatch.setattr(cli, 'remote', lambda action, data: status)
    with pytest.raises(ValueError, match='observation differs'):
        cli.observe_validation_transfer(tmp_path)


def test_verify_transfer_compares_original_local_image(tmp_path, monkeypatch):
    intent = {'unit': 'unit-1', 'revision': 'a'*40, 'attempt_id': 'unit-1:0',
              'files': {'image.tar': 'b'*64, 'build.json': 'c'*64}}
    (tmp_path/'transfer-intent.json').write_text(json.dumps(intent))
    monkeypatch.setattr(cli, 'observe_candidate_build', lambda run: {'manifest': {'config_digest': 'sha256:'+'d'*64}})
    verified = {'verified': True, 'loaded': False, 'revision': 'a'*40, 'attempt_id': 'unit-1:0',
                'image_tar_sha256': 'b'*64, 'manifest_sha256': 'c'*64, 'config_digest': 'sha256:'+'d'*64}
    monkeypatch.setattr(cli, 'remote', lambda action, data: verified)
    assert cli.verify_validation_transfer(tmp_path) == verified
    verified['image_tar_sha256'] = 'e'*64
    with pytest.raises(ValueError, match='differs'):
        cli.verify_validation_transfer(tmp_path)


def test_transfer_joins_local_and_nuc_original_files(tmp_path, monkeypatch):
    import hashlib
    import shutil
    local = tmp_path/'local';local.mkdir()
    remote_root = tmp_path/'nuc';approval = remote_root/'approvals/unit-1';approval.mkdir(parents=True)
    output = local/'attested-build';output.mkdir()
    for name in ('image.tar', 'source.tar', 'diff', 'Dockerfile', 'build.json', 'build.sig'):
        (output/name).write_text(name)
    (local/'result.bundle').write_text('bundle')
    plan = {'unit': 'unit-1', 'baseline': 'b'*40}
    result = {'revision': 'a'*40, 'attempt_id': 'unit-1:0', 'binding_restored': True,
              'bundle_sha256': hashlib.sha256(b'bundle').hexdigest()}
    for directory in (local, approval):
        (directory/'operator.json').write_text(json.dumps(plan));(directory/'result.json').write_text(json.dumps(result))
    monkeypatch.setattr(nuc, 'ROOT', remote_root)
    monkeypatch.setattr(nuc, 'post', lambda route, body: {'stage': 'needs_validation',
        'build': {'status': 'completed', 'attempt_id': 'unit-1:0', 'git_revision': 'a'*40}})
    monkeypatch.setattr(cli, 'observe_candidate_build', lambda run: {'unit': 'unit-1', 'attempt_id': 'unit-1:0', 'revision': 'a'*40})
    monkeypatch.setattr(cli, 'remote', lambda action, data: nuc.main(action, data))
    monkeypatch.setattr(cli, 'copy', lambda path, unit, name=None, **kw: shutil.copyfile(path, approval/name))
    status = cli.transfer_validation_inputs(local)
    assert status['complete'] and all(status['files'].values())
    (approval/'validation-inputs/image.tar').write_text('changed')
    assert cli.observe_validation_transfer(local)['complete'] is False
    with pytest.raises(ValueError, match='original transfer'):cli.transfer_validation_inputs(local)


def test_verify_transferred_image_checks_real_signature_and_image_config(tmp_path, monkeypatch):
    import hashlib
    import io
    import tarfile
    import subprocess
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    approval = tmp_path/'approvals/unit-1';approval.mkdir(parents=True)
    target = approval/'validation-inputs';target.mkdir()
    unit, revision, baseline = 'unit-1', 'a'*40, 'b'*40
    files = {'source.tar': b'source', 'diff': b'diff', 'Dockerfile': b'FROM scratch', 'result.bundle': b'bundle'}
    for name, raw in files.items():(target/name).write_bytes(raw)
    config = json.dumps({'os': 'linux', 'architecture': 'amd64', 'config': {'Labels': {
        'factory.git-revision': revision, 'factory.source-archive-sha256': hashlib.sha256(files['source.tar']).hexdigest()}}}).encode()
    image_manifest = json.dumps([{'Config': 'config.json', 'RepoTags': ['factory-candidate-unit-1:'+revision], 'Layers': []}]).encode()
    with tarfile.open(target/'image.tar', 'w') as tar:
        for name, raw in [('manifest.json', image_manifest), ('config.json', config)]:
            info = tarfile.TarInfo(name);info.size = len(raw);tar.addfile(info, io.BytesIO(raw))
    binding = {'revision': revision, 'baseline': baseline, 'archive_sha256': hashlib.sha256(files['source.tar']).hexdigest(),
        'diff_sha256': hashlib.sha256(files['diff']).hexdigest(), 'dockerfile_sha256': hashlib.sha256(files['Dockerfile']).hexdigest(),
        'config_digest': 'sha256:'+hashlib.sha256(config).hexdigest()}
    (target/'build.json').write_text(json.dumps(binding))
    key = tmp_path/'fixture-key';public = tmp_path/'operator-build-public.pem'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'ed25519', '-out', str(key)], check=True)
    subprocess.run(['openssl', 'pkey', '-in', str(key), '-pubout', '-out', str(public)], check=True)
    subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(key), '-rawin', '-in', str(target/'build.json'), '-out', str(target/'build.sig')], check=True)
    names = ('image.tar', 'source.tar', 'diff', 'Dockerfile', 'build.json', 'build.sig', 'result.bundle')
    intent = {'unit': unit, 'revision': revision, 'attempt_id': unit+':0', 'baseline': baseline,
              'files': {name: hashlib.sha256((target/name).read_bytes()).hexdigest() for name in names}}
    (target/'transfer-intent.json').write_text(json.dumps(intent))
    (approval/'result.json').write_text(json.dumps({'revision': revision, 'attempt_id': unit+':0', 'binding_restored': True}))
    monkeypatch.setattr(nuc, 'post', lambda route, body: {'stage': 'needs_validation',
        'build': {'status': 'completed', 'attempt_id': unit+':0', 'git_revision': revision}})
    result = nuc.main('validation_verify_transfer', {'unit': unit})
    assert result['verified'] and result['config_digest'] == binding['config_digest']
    import shutil
    real_run = subprocess.run
    loaded = []
    def docker_run(args, **kwargs):
        if args[:3] == ['docker', 'image', 'ls']:
            return subprocess.CompletedProcess(args, 0, ('factory-candidate-unit-1:'+revision+'\n') if loaded else '', '')
        if args[:2] == ['docker', 'load']:
            loaded.append(True);return subprocess.CompletedProcess(args, 0, b'', b'')
        if args[:2] == ['docker', 'save']:
            shutil.copyfile(target/'image.tar', args[args.index('-o')+1]);return subprocess.CompletedProcess(args, 0)
        return real_run(args, **kwargs)
    monkeypatch.setattr(nuc.subprocess, 'run', docker_run)
    monkeypatch.setattr(nuc.subprocess, 'check_output', lambda args, **kwargs: json.dumps([{'Id': 'sha256:'+'f'*64}]).encode())
    loaded_result = nuc.main('validation_load', {'unit': unit})
    assert loaded_result['loaded'] and loaded_result['config_digest'] == binding['config_digest']
    assert nuc.main('validation_load_status', {'unit': unit}) == loaded_result and len(loaded) == 1
    with pytest.raises(ValueError, match='original image load'):nuc.main('validation_load', {'unit': unit})
    wrong_tag = json.dumps([{'Config': 'config.json', 'RepoTags': ['existing:tag'], 'Layers': []}]).encode()
    with tarfile.open(target/'image.tar', 'w') as tar:
        for name, raw in [('manifest.json', wrong_tag), ('config.json', config)]:
            info = tarfile.TarInfo(name);info.size = len(raw);tar.addfile(info, io.BytesIO(raw))
    intent['files']['image.tar'] = hashlib.sha256((target/'image.tar').read_bytes()).hexdigest()
    (target/'transfer-intent.json').write_text(json.dumps(intent))
    with pytest.raises(ValueError, match='tag'):nuc.main('validation_verify_transfer', {'unit': unit})
    (target/'build.sig').write_bytes(b'forged')
    intent['files']['build.sig'] = hashlib.sha256(b'forged').hexdigest()
    (target/'transfer-intent.json').write_text(json.dumps(intent))
    with pytest.raises(subprocess.CalledProcessError):nuc.main('validation_verify_transfer', {'unit': unit})


def test_image_load_uncertainty_never_repeats_original(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    target = tmp_path/'approvals/unit-1/validation-inputs';target.mkdir(parents=True)
    verified = {'verified': True, 'loaded': False, 'attempt_id': 'unit-1:0', 'revision': 'a'*40,
                'config_digest': 'sha256:'+'b'*64, 'image_tar_sha256': 'c'*64,
                'manifest_sha256': 'd'*64, 'tag': 'factory-candidate-unit-1:'+'a'*40}
    original = nuc.main
    def route(action, data):
        if action == 'validation_verify_transfer':return verified
        if action == 'validation_status':return {'accepted': False, 'stage': 'needs_validation'}
        return original(action, data)
    monkeypatch.setattr(nuc, 'main', route)
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ['docker', 'image', 'ls']:
            return subprocess.CompletedProcess(args, 0, '', '')
        assert (target/'load-intent.json').is_file()
        raise subprocess.TimeoutExpired(args, 180)
    monkeypatch.setattr(nuc.subprocess, 'run', run)
    with pytest.raises(subprocess.TimeoutExpired):nuc.main('validation_load', {'unit': 'unit-1'})
    observation = nuc.main('validation_load_status', {'unit': 'unit-1'})
    assert observation['loaded'] is False and observation['uncertain'] is True
    with pytest.raises(ValueError, match='original image load'):
        nuc.main('validation_load', {'unit': 'unit-1'})
    assert sum(args[:2] == ['docker', 'load'] for args in calls) == 1


def test_operator_load_status_never_sends_load_and_checks_image_identity(tmp_path, monkeypatch):
    intent = {'unit': 'unit-1', 'revision': 'a'*40, 'attempt_id': 'unit-1:0'}
    (tmp_path/'transfer-intent.json').write_text(json.dumps(intent))
    verified = {'tag': 'factory-candidate-unit-1:'+'a'*40, 'config_digest': 'sha256:'+'b'*64}
    monkeypatch.setattr(cli, 'verify_validation_transfer', lambda run: verified)
    result = {'loaded': True, 'image_id': 'sha256:'+'c'*64, 'config_digest': verified['config_digest'],
              'revision': intent['revision'], 'attempt_id': intent['attempt_id'], 'tag': verified['tag']}
    calls = []
    monkeypatch.setattr(cli, 'remote', lambda action, data: calls.append(action) or result)
    assert cli.validation_image_load(tmp_path, observe_only=True) == result
    assert calls == ['validation_load_status']
    result['config_digest'] = 'sha256:'+'d'*64
    with pytest.raises(ValueError, match='identity differs'):
        cli.validation_image_load(tmp_path, observe_only=True)


def test_application_publication_requires_exact_accepted_artifact_and_security(tmp_path, monkeypatch):
    import hashlib
    archive = b'exact candidate archive'
    digest = hashlib.sha256(archive).hexdigest()
    plan = {'unit': 'unit-1'}
    result = {'revision': 'a'*40, 'attempt_id': 'unit-1:0'}
    proof = {'unit_id': 'unit-1', 'attempt_id': 'unit-1:0', 'git_revision': 'a'*40,
             'source_archive_digest': digest,
             'security_engines': {'sast': 'semgrep+diff-regex', 'secret': 'gitleaks+diff-regex',
                                  'dependency': 'osv-scanner'}}
    observed = {'accepted': True, 'revision': 'a'*40, 'attempt_id': 'unit-1:0', 'validation': proof}
    monkeypatch.setattr(cli, 'remote', lambda action, data: observed)
    monkeypatch.setattr(cli.subprocess, 'check_output', lambda args: archive)
    assert cli.application_validation(tmp_path, plan, result) == proof
    for field, value in [('accepted', False), ('revision', 'b'*40), ('attempt_id', 'unit-1:0:successor')]:
        changed = {**observed, field: value}
        monkeypatch.setattr(cli, 'remote', lambda action, data: changed)
        with pytest.raises(ValueError, match='validation'):
            cli.application_validation(tmp_path, plan, result)
    for changed_proof in ({**proof, 'source_archive_digest': 'c'*64},
                          {**proof, 'security_engines': {'sast': 'regex-fallback'}}):
        monkeypatch.setattr(cli, 'remote', lambda action, data: {**observed, 'validation': changed_proof})
        with pytest.raises(ValueError, match='validation|security'):
            cli.application_validation(tmp_path, plan, result)


def test_publish_does_not_push_without_application_validation(tmp_path, monkeypatch):
    import hashlib
    repo = tmp_path / 'candidate';(repo / 'scripts').mkdir(parents=True)
    (repo / 'scripts/verify-local.py').write_text('trusted runner')
    check = tmp_path / 'check.py';check.write_text('operator check')
    (tmp_path / 'operator.json').write_text(json.dumps({'unit': 'unit-1', 'baseline': 'a'*40,
        'origin': 'https://github.com/example/repo', 'check_sha256': hashlib.sha256(check.read_bytes()).hexdigest()}))
    (tmp_path / 'result.json').write_text(json.dumps({'revision': 'b'*40, 'attempt_id': 'unit-1:0'}))
    def command(*args, **kwargs):
        assert 'push' not in args and args[0] != 'gh'
        if 'rev-parse' in args:return 'b'*40
        if 'show' in args:return 'trusted runner'
        return ''
    monkeypatch.setattr(cli, 'command', command)
    monkeypatch.setattr(cli.subprocess, 'run', lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, 'remote', lambda action, data: {'accepted': False})
    with pytest.raises(ValueError, match='validation'):
        cli.publish(tmp_path)


def test_publish_includes_matching_receipt_in_pr(tmp_path, monkeypatch):
    import hashlib
    repo = tmp_path / 'candidate';(repo / 'scripts').mkdir(parents=True)
    (repo / 'scripts/verify-local.py').write_text('trusted runner')
    check = tmp_path / 'check.py';check.write_text('operator check')
    plan = {'unit': 'unit-1', 'baseline': 'a'*40, 'origin': 'https://github.com/example/repo',
            'github': 'example/repo', 'task': 'Add a feature', 'check_sha256': hashlib.sha256(check.read_bytes()).hexdigest()}
    result = {'revision': 'b'*40, 'attempt_id': 'unit-1:0', 'budget': {}, 'runtime_validation': 'needs_validation'}
    (tmp_path / 'operator.json').write_text(json.dumps(plan))
    (tmp_path / 'result.json').write_text(json.dumps(result))
    archive = b'archive'; proof = {'unit_id': 'unit-1', 'attempt_id': 'unit-1:0', 'git_revision': 'b'*40,
        'source_archive_digest': hashlib.sha256(archive).hexdigest(), 'artifact_digest': 'sha256:'+'c'*64,
        'checks_digest': 'd'*64, 'scenario_digest': 'e'*64,
        'security_engines': {'sast': 'semgrep+diff-regex', 'secret': 'gitleaks+diff-regex', 'dependency': 'osv-scanner'}}
    monkeypatch.setattr(cli, 'remote', lambda action, data: {'accepted': True, 'revision': 'b'*40,
                        'attempt_id': 'unit-1:0', 'validation': proof})
    monkeypatch.setattr(cli.subprocess, 'check_output', lambda args: archive)
    monkeypatch.setattr(cli.subprocess, 'run', lambda *args, **kwargs: None)
    def command(*args, **kwargs):
        if 'rev-parse' in args:return 'b'*40
        if 'show' in args:return 'trusted runner'
        if args[:3] == ('gh', 'pr', 'list'):return '[]'
        if args[:3] == ('gh', 'pr', 'create'):
            body = (tmp_path / 'pr.md').read_text()
            assert all(proof[k] in body for k in ('artifact_digest', 'checks_digest', 'scenario_digest'))
            return 'https://github.com/example/repo/pull/1'
        return ''
    monkeypatch.setattr(cli, 'command', command)
    assert cli.publish(tmp_path)['status'] == 'PR_OPEN'


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
