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


@pytest.mark.parametrize('version', [1, 2])
def test_verify_transferred_image_checks_real_signature_and_image_config(tmp_path, monkeypatch, version):
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
    if version == 2:
        binding.update(schema='factory.build.v2', image_tar_sha256=hashlib.sha256((target/'image.tar').read_bytes()).hexdigest())
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
    with pytest.raises(ValueError, match='signed image archive' if version == 2 else 'tag'):
        nuc.main('validation_verify_transfer', {'unit': unit})
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


def test_prepare_validation_repo_binds_bundle_without_recloning(tmp_path, monkeypatch):
    import hashlib
    import subprocess
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    target = tmp_path/'approvals/unit-1/validation-inputs';target.mkdir(parents=True)
    source = tmp_path/'source';source.mkdir()
    def git(*args):return subprocess.check_output(['git', '-C', str(source), *args]).strip()
    git('init', '-q');git('config', 'user.name', 'Fixture');git('config', 'user.email', 'fixture@example.invalid')
    (source/'app').write_text('before');git('add', 'app');git('commit', '-qm', 'base')
    baseline = git('rev-parse', 'HEAD').decode()
    (source/'app').write_text('after');git('commit', '-qam', 'candidate');revision = git('rev-parse', 'HEAD').decode()
    git('bundle', 'create', str(target/'result.bundle'), '--all')
    (target/'source.tar').write_bytes(subprocess.check_output(['git', '-C', str(source), 'archive', revision]))
    (target/'diff').write_bytes(subprocess.check_output(['git', '-C', str(source), 'diff', baseline, revision]))
    intent = {'unit': 'unit-1', 'attempt_id': 'unit-1:0', 'revision': revision, 'baseline': baseline,
              'files': {name: hashlib.sha256((target/name).read_bytes()).hexdigest() for name in ('result.bundle', 'source.tar', 'diff')}}
    (target/'transfer-intent.json').write_text(json.dumps(intent))
    verified = {'verified': True, 'attempt_id': 'unit-1:0', 'revision': revision,
                'config_digest': 'sha256:'+'b'*64, 'tag': 'fixture:tag'}
    loaded = {'loaded': True, 'attempt_id': 'unit-1:0', 'revision': revision,
              'config_digest': verified['config_digest'], 'tag': verified['tag'], 'image_id': 'sha256:'+'c'*64}
    original = nuc.main
    def route(action, data):
        if action == 'validation_verify_transfer':return verified
        if action == 'validation_load_status':return loaded
        return original(action, data)
    monkeypatch.setattr(nuc, 'main', route)
    prepared = nuc.main('validation_prepare', {'unit': 'unit-1'})
    assert prepared['prepared'] and prepared['revision'] == revision
    assert nuc.main('validation_prepare_status', {'unit': 'unit-1'}) == prepared
    with pytest.raises(ValueError, match='original preparation'):
        nuc.main('validation_prepare', {'unit': 'unit-1'})
    (target/'diff').write_bytes(b'changed')
    with pytest.raises(ValueError, match='diff'):
        nuc.main('validation_prepare_status', {'unit': 'unit-1'})


def test_operator_prepare_observation_does_not_reclone_or_accept_other_image(tmp_path, monkeypatch):
    intent = {'unit': 'unit-1', 'revision': 'a'*40, 'attempt_id': 'unit-1:0', 'baseline': 'b'*40}
    (tmp_path/'transfer-intent.json').write_text(json.dumps(intent))
    monkeypatch.setattr(cli, 'validation_image_load', lambda run, **kw: {'loaded': True, 'image_id': 'sha256:'+'c'*64})
    prepared = {'prepared': True, **intent, 'image_id': 'sha256:'+'c'*64}
    calls = []
    monkeypatch.setattr(cli, 'remote', lambda action, data: calls.append(action) or prepared)
    assert cli.prepare_validation_source(tmp_path, observe_only=True) == prepared
    assert calls == ['validation_prepare_status']
    prepared['image_id'] = 'sha256:'+'d'*64
    with pytest.raises(ValueError, match='differs'):
        cli.prepare_validation_source(tmp_path, observe_only=True)


def test_scanner_bundle_verifies_exact_files_and_rejects_tampering(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    root = tmp_path/'scanners';root.mkdir(mode=0o700)
    package = root/'fixture';package.mkdir(mode=0o700)
    scan = package/'scanner';(scan/'bin').mkdir(parents=True)
    for name in ('semgrep', 'pysemgrep', 'gitleaks', 'osv-scanner', 'cosign'):
        path = scan/'bin'/name;path.write_bytes(b'fixture-scanner');path.chmod(0o755)
    manifest = package/'files-final.json'
    manifest.write_text(json.dumps({'bin/'+name: hashlib.sha256(b'fixture-scanner').hexdigest()
                                    for name in ('semgrep', 'pysemgrep', 'gitleaks', 'osv-scanner', 'cosign')}))
    selection = root/'selection.json'
    selection.write_text(json.dumps({'bundle': 'fixture', 'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}))
    selection.chmod(0o600)
    result = nuc.scanner_bundle()
    assert result['bin'] == str(scan/'bin')
    (scan/'bin'/'semgrep').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='scanner'):
        nuc.scanner_bundle()
    (scan/'bin'/'semgrep').write_bytes(b'fixture-scanner')
    (scan/'untracked.py').write_text('unexpected code')
    with pytest.raises(ValueError, match='scanner'):
        nuc.scanner_bundle()


@pytest.mark.parametrize('capable', [False, True])
def test_evaluation_preflight_and_lost_response_never_repeat(tmp_path, monkeypatch, capable):
    import subprocess
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    approval = tmp_path/'approvals/unit-1';target = approval/'validation-inputs';target.mkdir(parents=True)
    holdout = approval/'holdouts';holdout.mkdir(mode=0o700);(holdout/'scenarios.json').write_text('{}');(holdout/'scenarios.json').chmod(0o600)
    release = tmp_path/'releases/reviewed';release.mkdir(parents=True)
    script = release/'stapled-factory/deployment/nuc/validate.py';script.parent.mkdir(parents=True);script.write_text('# fixture')
    required = ['stapled-factory/deployment/nuc/validate.py', 'stapled-factory/spine/validation_session.py',
                'stapled-factory/spine/prebuilt_validation.py', 'stapled-factory/spine/validation_transport.py',
                'stapled-factory/security/pipeline_cli.py', 'stapled-factory/security/pipeline.py',
                'stapled-factory/wall/evaluator/cli.py', 'stapled-factory/wall/evaluator/config.py',
                'stapled-factory/wall/evaluator/evaluate.py', 'stapled-factory/wall/evaluator/gate.py',
                'stapled-factory/wall/evaluator/receipt.py']
    import hashlib
    validator_sha256 = hashlib.sha256(script.read_bytes()).hexdigest()
    (release/'release-manifest.json').write_text(json.dumps({'files': {k: validator_sha256 if k == required[0] else 'fixture' for k in required}}))
    (approval/'operator.json').write_text(json.dumps({'release': str(release)}))
    monkeypatch.setattr(nuc, 'release', lambda: release)
    monkeypatch.setattr(nuc, 'verify_release', lambda path: Path(path))
    monkeypatch.setattr(nuc, 'scanner_bundle', lambda selected=None: {'bundle': 'fixture', 'manifest_sha256': 'f'*64, 'bin': '/fixture/bin'})
    monkeypatch.setattr(nuc, 'validation_capabilities', lambda unit: {'receipt_storage': 'atomic-v1'} if capable else {})
    prepared = {'prepared': True, 'unit': 'unit-1', 'attempt_id': 'unit-1:0', 'revision': 'a'*40,
                'baseline': 'b'*40, 'image_id': 'sha256:'+'c'*64}
    original = nuc.main
    def route(action, data):
        if action == 'validation_prepare_status':return prepared
        if action == 'validation_holdout_status':return {'complete': True, 'attempt_id': 'unit-1:0', 'revision': 'a'*40,
                                                         'sha256': hashlib.sha256(b'{}').hexdigest()}
        return original(action, data)
    monkeypatch.setattr(nuc, 'main', route)
    calls = []
    def execute(args, **kwargs):
        calls.append(args)
        assert (target/'evaluation-intent.json').is_file()
        raise subprocess.TimeoutExpired(args, 900)
    monkeypatch.setattr(nuc.subprocess, 'run', execute)
    if not capable:
        with pytest.raises(ValueError, match='receipt capability'):nuc.main('validation_execute', {'unit': 'unit-1', 'validator_sha256': validator_sha256})
        assert not calls and not (target/'evaluation-intent.json').exists()
    else:
        with pytest.raises(subprocess.TimeoutExpired):nuc.main('validation_execute', {'unit': 'unit-1', 'validator_sha256': validator_sha256})
        assert nuc.main('validation_execute_status', {'unit': 'unit-1'})['status'] == 'unresolved'
        with pytest.raises(ValueError, match='original evaluation'):
            nuc.main('validation_execute', {'unit': 'unit-1', 'validator_sha256': validator_sha256})
        assert len(calls) == 1
        # Model a crash between the durable outer intent and frozen holdout.
        (target/'frozen-scenarios.json').write_bytes(b'partial')
        proof = {'unit_id': 'unit-1', 'checks_digest': 'd'*64}
        def resumed_route(action, data):
            if action == 'validation_status':return {'accepted': True, 'validation': proof}
            return route(action, data)
        monkeypatch.setattr(nuc, 'main', resumed_route)
        def resume(args, **kwargs):
            assert '--resume-only' in args and '--resume-unstarted' in args
            assert (target/'frozen-scenarios.json').read_bytes() == b'partial'
            assert Path(args[args.index('--scenarios')+1]).read_bytes() == b'{}'
            return subprocess.CompletedProcess(args, 0, json.dumps({'accepted': True, 'evidence': proof}), '')
        monkeypatch.setattr(nuc.subprocess, 'run', resume)
        assert nuc.main('validation_execute_resume', {'unit': 'unit-1'})['accepted'] is True


def test_evaluation_uses_frozen_holdout_and_observes_same_receipt(tmp_path, monkeypatch):
    import hashlib
    import subprocess
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    approval = tmp_path/'approvals/unit-1';target = approval/'validation-inputs';target.mkdir(parents=True)
    holdout = approval/'holdouts';holdout.mkdir(mode=0o700);scenario = holdout/'scenarios.json'
    scenario.write_bytes(b'original holdout');scenario.chmod(0o600)
    release = tmp_path/'releases/reviewed';release.mkdir(parents=True)
    script = release/'stapled-factory/deployment/nuc/validate.py';script.parent.mkdir(parents=True);script.write_text('# fixture')
    validator = hashlib.sha256(script.read_bytes()).hexdigest()
    required = ['stapled-factory/deployment/nuc/validate.py', 'stapled-factory/spine/validation_session.py',
                'stapled-factory/spine/prebuilt_validation.py', 'stapled-factory/spine/validation_transport.py',
                'stapled-factory/security/pipeline_cli.py', 'stapled-factory/security/pipeline.py',
                'stapled-factory/wall/evaluator/cli.py', 'stapled-factory/wall/evaluator/config.py',
                'stapled-factory/wall/evaluator/evaluate.py', 'stapled-factory/wall/evaluator/gate.py',
                'stapled-factory/wall/evaluator/receipt.py']
    (release/'release-manifest.json').write_text(json.dumps({'files': {k: validator for k in required}}))
    (approval/'operator.json').write_text(json.dumps({'release': str(tmp_path/'releases/old-build')}))
    monkeypatch.setattr(nuc, 'release', lambda: release)
    verified = []
    monkeypatch.setattr(nuc, 'verify_release', lambda path: verified.append(path) or Path(path))
    monkeypatch.setattr(nuc, 'scanner_bundle', lambda selected=None: {'bundle': 'fixture', 'manifest_sha256': 'f'*64, 'bin': '/fixture/bin'})
    monkeypatch.setattr(nuc, 'validation_capabilities', lambda unit: {'receipt_storage': 'atomic-v1'})
    prepared = {'prepared': True, 'unit': 'unit-1', 'attempt_id': 'unit-1:0', 'revision': 'a'*40,
                'baseline': 'b'*40, 'image_id': 'sha256:'+'c'*64}
    proof = {'unit_id': 'unit-1', 'checks_digest': 'd'*64}
    receipt = {'accepted': True, 'evidence': proof}
    original = nuc.main
    def route(action, data):
        if action == 'validation_prepare_status':return prepared
        if action == 'validation_status':return {'accepted': True, 'validation': proof}
        if action == 'validation_holdout_status':return {'complete': True, 'attempt_id': 'unit-1:0', 'revision': 'a'*40,
                                                         'sha256': hashlib.sha256(b'original holdout').hexdigest()}
        return original(action, data)
    monkeypatch.setattr(nuc, 'main', route)
    calls = []
    def execute(args, **kwargs):
        calls.append(args)
        frozen = Path(args[args.index('--scenarios')+1])
        assert frozen.read_bytes() == b'original holdout'
        assert args[args.index('--expected-scenario-digest')+1] == hashlib.sha256(b'original holdout').hexdigest()
        journal = Path(args[args.index('--session-dir')+1]);journal.mkdir(exist_ok=True)
        (journal/'intent.json').write_text('{}')
        scenario.write_bytes(b'changed outside frozen journal')
        return subprocess.CompletedProcess(args, 0, json.dumps(receipt), '')
    monkeypatch.setattr(nuc.subprocess, 'run', execute)
    assert nuc.main('validation_execute', {'unit': 'unit-1', 'validator_sha256': validator}) == receipt
    recorded = json.loads((target/'evaluation-intent.json').read_text())
    assert recorded['build_release'] == str(tmp_path/'releases/old-build')
    assert recorded['release'] == str(release)
    monkeypatch.setattr(nuc, 'release', lambda: (_ for _ in ()).throw(AssertionError('active runtime must not replace the original validator')))
    assert nuc.main('validation_execute_status', {'unit': 'unit-1'}) == receipt
    assert verified == [str(release)]
    manifest = release/'release-manifest.json'
    original_manifest = manifest.read_bytes()
    manifest.write_bytes(original_manifest + b' ')
    with pytest.raises(ValueError, match='original evaluation binding differs'):
        nuc.main('validation_execute_resume', {'unit': 'unit-1'})
    manifest.write_bytes(original_manifest)
    assert nuc.main('validation_execute_resume', {'unit': 'unit-1'}) == receipt
    assert len(calls) == 3 and '--observe-only' not in calls[0] and '--observe-only' in calls[1]
    assert '--resume-only' in calls[2] and '--observe-only' not in calls[2]


def test_operator_validation_observation_checks_original_prepared_identity(tmp_path, monkeypatch):
    intent = {'unit': 'unit-1', 'attempt_id': 'unit-1:0', 'revision': 'a'*40,
              'files': {'source.tar': 'b'*64}}
    (tmp_path/'transfer-intent.json').write_text(json.dumps(intent))
    monkeypatch.setattr(cli, 'prepare_validation_source', lambda run, **kw: {'image_id': 'sha256:'+'c'*64})
    proof = {'unit_id': 'unit-1', 'attempt_id': 'unit-1:0', 'git_revision': 'a'*40,
             'artifact_digest': 'sha256:'+'c'*64, 'source_archive_digest': 'b'*64}
    result = {'accepted': True, 'evidence': proof};calls = []
    monkeypatch.setattr(cli, 'remote', lambda action, data: calls.append(action) or result)
    assert cli.execute_validation(tmp_path, observe_only=True) == result
    assert calls == ['validation_execute_status']
    proof['artifact_digest'] = 'sha256:'+'d'*64
    with pytest.raises(ValueError, match='differs'):
        cli.execute_validation(tmp_path, observe_only=True)


def test_private_holdout_transfer_rejects_changes_and_repetition(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    approval = tmp_path/'approvals/unit-1';approval.mkdir(parents=True)
    result = {'revision': 'a'*40, 'attempt_id': 'unit-1:0', 'binding_restored': True}
    (approval/'result.json').write_text(json.dumps(result))
    monkeypatch.setattr(nuc, 'post', lambda route, body: {'stage': 'needs_validation',
        'build': {'status': 'completed', 'attempt_id': 'unit-1:0', 'git_revision': 'a'*40}})
    raw = b'{"scenarios":[{"id":"private"}]}'
    data = {'unit': 'unit-1', 'attempt_id': 'unit-1:0', 'revision': 'a'*40,
            'sha256': hashlib.sha256(raw).hexdigest()}
    assert nuc.main('validation_holdout_begin', data)['created']
    holdout = approval/'holdouts/scenarios.json';holdout.write_bytes(raw);holdout.chmod(0o600)
    observed = nuc.main('validation_holdout_status', {'unit': 'unit-1'})
    assert observed['complete'] and observed['sha256'] == data['sha256']
    assert 'private' not in json.dumps(observed)
    holdout.write_bytes(b'changed')
    assert not nuc.main('validation_holdout_status', {'unit': 'unit-1'})['complete']
    with pytest.raises(FileExistsError):nuc.main('validation_holdout_begin', data)


def test_lost_holdout_begin_never_repeats_or_copies(tmp_path, monkeypatch):
    (tmp_path/'operator.json').write_text(json.dumps({'unit': 'unit-1'}))
    (tmp_path/'result.json').write_text(json.dumps({'revision': 'a'*40, 'attempt_id': 'unit-1:0'}))
    (tmp_path/'candidate').mkdir()
    holdout = tmp_path/'private-scenarios';holdout.write_text('{"scenarios":[{"id":"hidden"}]}');holdout.chmod(0o600)
    calls = []
    def remote(action, data):
        calls.append(action)
        if action == 'validation_status':return {'accepted': False, 'stage': 'needs_validation', 'attempt_id': 'unit-1:0', 'revision': 'a'*40}
        raise OSError('response lost')
    monkeypatch.setattr(cli, 'remote', remote)
    monkeypatch.setattr(cli, 'copy', lambda *a, **k: pytest.fail('uncertain begin must not copy'))
    with pytest.raises(OSError):cli.transfer_holdout(tmp_path, holdout)
    with pytest.raises(ValueError, match='original holdout'):cli.transfer_holdout(tmp_path, holdout)
    assert calls.count('validation_holdout_begin') == 1


def test_holdout_transfer_joins_local_intent_to_private_nuc_file(tmp_path, monkeypatch):
    import shutil
    local = tmp_path/'local';local.mkdir();(local/'candidate').mkdir()
    root = tmp_path/'nuc';approval = root/'approvals/unit-1';approval.mkdir(parents=True)
    plan = {'unit': 'unit-1'};result = {'attempt_id': 'unit-1:0', 'revision': 'a'*40, 'binding_restored': True}
    for directory in (local, approval):
        (directory/'operator.json').write_text(json.dumps(plan));(directory/'result.json').write_text(json.dumps(result))
    holdout = tmp_path/'holdout.json';holdout.write_text('{"scenarios":[{"id":"fixture-private"}]}');holdout.chmod(0o600)
    monkeypatch.setattr(nuc, 'ROOT', root)
    monkeypatch.setattr(nuc, 'post', lambda route, body: {'stage': 'needs_validation',
        'build': {'status': 'completed', 'attempt_id': 'unit-1:0', 'git_revision': 'a'*40}})
    monkeypatch.setattr(cli, 'remote', lambda action, data: nuc.main(action, data))
    def copy(source, unit, name=None, **kwargs):
        shutil.copyfile(source, approval/name);(approval/name).chmod(0o600)
    monkeypatch.setattr(cli, 'copy', copy)
    result = cli.transfer_holdout(local, holdout)
    assert result['complete'] and 'fixture-private' not in json.dumps(result)
    assert cli.observe_holdout(local) == result
    with pytest.raises(ValueError, match='original holdout'):cli.transfer_holdout(local, holdout)


def test_application_publication_requires_exact_accepted_artifact_and_security(tmp_path, monkeypatch):
    import hashlib
    archive = b'exact candidate archive'
    digest = hashlib.sha256(archive).hexdigest()
    plan = {'unit': 'unit-1'}
    result = {'revision': 'a'*40, 'attempt_id': 'unit-1:0'}
    proof = {'unit_id': 'unit-1', 'attempt_id': 'unit-1:0', 'git_revision': 'a'*40,
             'source_archive_digest': digest,
             'security_engines': {'sast': 'semgrep+diff-regex', 'secret': 'gitleaks+diff-regex',
                                  'dependency': 'osv-scanner', 'image_dependency': 'osv-scanner-image'}}
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
                          {**proof, 'security_engines': {'sast': 'regex-fallback'}},
                          {**proof, 'security_engines': {k: v for k, v in proof['security_engines'].items() if k != 'image_dependency'}}):
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
        'security_engines': {'sast': 'semgrep+diff-regex', 'secret': 'gitleaks+diff-regex', 'dependency': 'osv-scanner', 'image_dependency': 'osv-scanner-image'}}
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


def make_preflight_repo(tmp_path):
    import subprocess
    repo = tmp_path / 'source';repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    git('init', '-q');git('config', 'user.email', 'fixture@example.invalid');git('config', 'user.name', 'Fixture')
    (repo/'package.json').write_text('{"name":"fixture"}\n')
    git('add', 'package.json');git('commit', '-qm', 'fixture')
    return repo, git


def test_source_preflight_is_read_only_and_reports_missing_locks(tmp_path, monkeypatch):
    import hashlib
    import subprocess
    repo, git = make_preflight_repo(tmp_path)
    revision = git('rev-parse', 'HEAD')
    expected = hashlib.sha256(subprocess.check_output(['git', '-C', str(repo), 'archive', revision])).hexdigest()
    monkeypatch.setattr(cli, 'remote', lambda *args: pytest.fail('preflight must not contact NUC'))
    monkeypatch.setattr(cli, 'sign', lambda *args: pytest.fail('preflight must not sign'))
    result = cli.source_preflight(repo, revision)
    assert result['status'] == 'held' and result['revision'] == revision
    assert result['source_archive_sha256'] == expected
    assert result['source_inventory']['missing_locks'] == ['package.json']
    assert result['source_inventory']['coverage'] == 'incomplete'
    (repo/'package-lock.json').write_text('{}')
    result = cli.source_preflight(repo, revision)
    assert result['source_inventory']['missing_locks'] == ['package.json']
    git('add', 'package-lock.json');git('commit', '-qm', 'lock')
    complete_looking = cli.source_preflight(repo, 'HEAD')
    assert complete_looking['source_inventory']['missing_locks'] == []
    assert complete_looking['status'] == 'held'


def test_source_preflight_does_not_execute_git_fsmonitor(tmp_path):
    repo, git = make_preflight_repo(tmp_path)
    marker = tmp_path/'marker';hook = tmp_path/'hook'
    hook.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n');hook.chmod(0o700)
    git('config', 'core.fsmonitor', str(hook))
    cli.source_preflight(repo, 'HEAD')
    assert not marker.exists()


def test_source_preflight_ignores_git_replacement_refs(tmp_path):
    repo, git = make_preflight_repo(tmp_path)
    first = git('rev-parse', 'HEAD')
    (repo/'package-lock.json').write_text('{}');git('add', 'package-lock.json');git('commit', '-qm', 'replacement')
    second = git('rev-parse', 'HEAD');git('replace', first, second)
    result = cli.source_preflight(repo, first)
    assert result['revision'] == first
    assert result['source_inventory']['missing_locks'] == ['package.json']


def test_source_preflight_rejects_over_limit_archive(tmp_path, monkeypatch):
    repo, _ = make_preflight_repo(tmp_path)
    monkeypatch.setattr(cli, 'MAX_SOURCE_ARCHIVE_BYTES', 1024, raising=False)
    with pytest.raises(ValueError, match='archive.*limit'):
        cli.source_preflight(repo, 'HEAD')


def test_source_preflight_rejects_too_many_archive_members(tmp_path, monkeypatch):
    repo, git = make_preflight_repo(tmp_path)
    (repo/'second').write_text('x');git('add', 'second');git('commit', '-qm', 'second')
    monkeypatch.setattr(cli, 'MAX_SOURCE_ARCHIVE_ENTRIES', 1, raising=False)
    with pytest.raises(ValueError, match='archive.*entries'):
        cli.source_preflight(repo, 'HEAD')


def test_source_preflight_cli_does_not_load_config_or_contact_nuc(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'source_preflight', lambda path, revision: {'status': 'held', 'revision': revision})
    monkeypatch.setattr(cli, 'load_config', lambda *args: pytest.fail('preflight is local'))
    monkeypatch.setattr(sys, 'argv', ['cli.py', 'preflight-source', str(tmp_path), '--revision', 'HEAD'])
    cli.main()
    assert json.loads(capsys.readouterr().out) == {'status': 'held', 'revision': 'HEAD'}


def test_source_preflight_does_not_lazily_fetch_missing_objects(tmp_path):
    import subprocess
    repo, git = make_preflight_repo(tmp_path)
    origin = tmp_path/'origin.git'
    subprocess.run(['git', 'clone', '--bare', '-q', str(repo), str(origin)], check=True)
    subprocess.run(['git', '-C', str(origin), 'config', 'uploadpack.allowFilter', 'true'], check=True)
    partial = tmp_path/'partial'
    subprocess.run(['git', 'clone', '-q', '--filter=blob:none', '--no-checkout', origin.as_uri(), str(partial)], check=True)
    marker = tmp_path/'upload-marker';hook = tmp_path/'upload-hook'
    hook.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n');hook.chmod(0o700)
    subprocess.run(['git', '-C', str(partial), 'config', 'remote.origin.uploadpack', str(hook)], check=True)
    with pytest.raises(ValueError, match='archive failed'):
        cli.source_preflight(partial, 'HEAD')
    assert not marker.exists()


def test_source_preflight_ignores_external_attributes_and_rejects_info_attributes(tmp_path):
    repo, git = make_preflight_repo(tmp_path)
    external = tmp_path/'attributes';external.write_text('package.json export-ignore\n')
    git('config', 'core.attributesFile', str(external))
    result = cli.source_preflight(repo, 'HEAD')
    assert result['source_inventory']['missing_locks'] == ['package.json']
    info = repo/'.git/info/attributes';info.write_text('package.json export-ignore\n')
    with pytest.raises(ValueError, match='info attributes'):
        cli.source_preflight(repo, 'HEAD')
    with pytest.raises(ValueError, match='info attributes'):
        cli.controlled_source_archive(repo, 'HEAD')


def test_source_preflight_never_promotes_export_ignored_manifests(tmp_path):
    repo, git = make_preflight_repo(tmp_path)
    (repo/'.gitattributes').write_text('package.json export-ignore\n')
    (repo/'keep.txt').write_text('fixture\n')
    git('add', '.gitattributes', 'keep.txt');git('commit', '-qm', 'attributes')
    result = cli.source_preflight(repo, 'HEAD')
    assert result['status'] == 'held'
    assert result['source_inventory']['coverage'] == 'incomplete'


def test_v2_validation_evidence_must_match_original_manifest(tmp_path):
    import hashlib
    directory = tmp_path/'attested-build'; directory.mkdir()
    manifest = directory/'build.json'; manifest.write_text(json.dumps({'schema': 'factory.build.v2'}))
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    (tmp_path/'transfer-intent.json').write_text(json.dumps({'files': {'build.json': digest}}))
    cli.check_validation_manifest(tmp_path, {'build_manifest_digest': digest})
    for proof in ({}, {'build_manifest_digest': '0'*64}):
        with pytest.raises(ValueError, match='manifest differs'):
            cli.check_validation_manifest(tmp_path, proof)
    manifest.write_text(json.dumps({'schema': 'factory.build.v2', 'changed': True}))
    with pytest.raises(ValueError, match='manifest differs'):
        cli.check_validation_manifest(tmp_path, {'build_manifest_digest': digest})


def controlled_build_inputs(tmp_path, monkeypatch):
    import hashlib
    bundle, archive = b'original bundle', b'frozen source archive'
    plan={'unit':'unit-1','baseline':'a'*40};result={'revision':'b'*40,'attempt_id':'unit-1:0',
        'bundle_sha256':hashlib.sha256(bundle).hexdigest()}
    (tmp_path/'operator.json').write_text(json.dumps(plan));(tmp_path/'result.json').write_text(json.dumps(result))
    (tmp_path/'result.bundle').write_bytes(bundle);(tmp_path/'candidate').mkdir()
    key=tmp_path/'fixture-key';key.write_bytes(b'fixture');key.chmod(0o600)
    config={'profile':'npm','materials_archive':str(tmp_path/'materials.tar'),'materials_sha256':'c'*64,
        'base_layout':str(tmp_path/'base'),'base_manifest':'sha256:'+'d'*64,'builder_name':'factory-builder',
        'builder_container_id':'e'*64,'builder_image_id':'sha256:'+'f'*64,
        'docker_host':'unix:///tmp/fixture.sock','docker_config':str(tmp_path/'docker'),
        'docker_bin_sha256':'6'*64,'buildx_bin_sha256':'7'*64,'npm_entrypoint':'server.js'}
    path=tmp_path/'controlled.json';path.write_text(json.dumps(config));path.chmod(0o600)
    normalized=dict(config,docker_bin='/usr/local/bin/docker')
    plan['controlled_baseline']={'config':normalized,'config_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'report':{}}
    (tmp_path/'operator.json').write_text(json.dumps(plan))
    recipe=b'FROM base\n';report={'profile':'npm','coverage':'incomplete',
        'source_sha256':hashlib.sha256(archive).hexdigest(),'materials_sha256':config['materials_sha256'],
        'lock_sha256':'1'*64,'base_manifest':config['base_manifest'],'base_config':'sha256:'+'2'*64,
        'builder_container_id':config['builder_container_id'],'builder_image_id':config['builder_image_id'],
        'builder_config_sha256':'3'*64,'recipe_sha256':hashlib.sha256(recipe).hexdigest(),
        'materials_manifest_sha256':'4'*64,'cache_recipe_sha256':'5'*64,'npm_entrypoint':'server.js',
        'docker_bin_sha256':'6'*64,'buildx_bin_sha256':'7'*64}
    plan['controlled_baseline']['report']=report
    (tmp_path/'operator.json').write_text(json.dumps(plan))
    marker=tmp_path/'controlled-dispatch-intent.json'
    marker.write_text(json.dumps({'unit':plan['unit'],'baseline':plan['baseline'],
        'config_sha256':plan['controlled_baseline']['config_sha256'],'report':report}));marker.chmod(0o600)
    monkeypatch.setattr(cli,'command',lambda *a,**k:result['revision'] if 'rev-parse' in a else '')
    monkeypatch.setattr(cli,'remote',lambda action,data:{'accepted':False,'stage':'needs_validation',
        'attempt_id':result['attempt_id'],'revision':result['revision']})
    monkeypatch.setattr(cli.subprocess,'check_output',lambda args:archive)
    monkeypatch.setattr(cli,'controlled_source_archive',lambda repo,revision:archive)
    return plan,result,key,path,archive,recipe,report


def test_controlled_build_freezes_preflight_and_reconciles_original(tmp_path,monkeypatch):
    import hashlib
    plan,result,key,path,archive,recipe,report=controlled_build_inputs(tmp_path,monkeypatch)
    preflights=[];builds=[]
    def preflight(config,repo,revision,source,source_sha):
        assert not (tmp_path/'build-intent.json').exists()
        assert source == archive and source_sha == report['source_sha256']
        preflights.append(config);return report
    monkeypatch.setattr(cli,'controlled_preflight',preflight)
    def build(args,**kwargs):
        builds.append(args);intent=json.loads((tmp_path/'build-intent.json').read_text())
        assert args[1].endswith('build_controlled.py') and '--dockerfile' not in args
        out=Path(args[args.index('--output-dir')+1]);out.mkdir()
        (out/'source.tar').write_bytes(archive);(out/'Dockerfile').write_bytes(recipe)
        (out/'diff').write_bytes(b'diff');(out/'image.tar').write_bytes(b'image');(out/'build.sig').write_bytes(b'signature')
        (out/'build.json').write_text(json.dumps({'schema':'factory.build.v2','revision':result['revision'],
            'baseline':plan['baseline'],'archive_sha256':hashlib.sha256(archive).hexdigest(),
            'dockerfile_sha256':hashlib.sha256(recipe).hexdigest(),'diff_sha256':hashlib.sha256(b'diff').hexdigest(),
            'image_tar_sha256':hashlib.sha256(b'image').hexdigest(),'controlled':intent['controlled']}))
    monkeypatch.setattr(cli.subprocess,'run',build)
    observed=cli.build_candidate(tmp_path,None,key,controlled_config=path)
    assert observed['status']=='BUILD_OUTPUT_PRESENT' and len(preflights)==len(builds)==1
    intent=json.loads((tmp_path/'build-intent.json').read_text())
    assert intent['controlled_config_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert intent['controlled']['coverage']=='incomplete'
    assert cli.observe_candidate_build(tmp_path)==observed
    manifest_path=tmp_path/'attested-build/build.json';manifest=json.loads(manifest_path.read_text())
    manifest['controlled']['lock_sha256']='0'*64;manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='preflight intent'):cli.observe_candidate_build(tmp_path)
    with pytest.raises(ValueError,match='never repeat'):cli.build_candidate(tmp_path,None,key,controlled_config=path)
    assert len(preflights)==len(builds)==1


def test_controlled_npm_config_binds_registry_policy_and_evidence(tmp_path, monkeypatch):
    _,_,_,path,_,_,report=controlled_build_inputs(tmp_path,monkeypatch)
    keyid='SHA256:jl3bwswu80PjjokCgh0o2w5c2U4LhQAE57gj9cz1kzA'
    public_der=('MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE1Olb3zMAFFxXKHiIkQO5cJ3Yh'
                'l5i6UPp+IhuteBJbuHcA5UogKo0EWtlWwW6KSaKoTNEYL7JlCQiVnkhBktUgg==')
    data=json.loads(path.read_text());data['registry_policy']={keyid:public_der};path.write_text(json.dumps(data))
    config,_=cli.load_controlled_config(path)
    bound={**report,'registry_policy':data['registry_policy'],'registry_evidence_sha256':'a'*64}
    assert cli.validate_controlled_preflight(config,bound,report['source_sha256'])==bound
    with pytest.raises(ValueError,match='policy'):
        cli.validate_controlled_preflight(config,{**bound,'registry_policy':{}},report['source_sha256'])
    data['registry_policy']={'SHA256:wrong':public_der};path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='fingerprint'):
        cli.load_controlled_config(path)


def test_controlled_npm_config_binds_attestation_preflight(tmp_path, monkeypatch):
    import hashlib
    _,_,_,path,_,_,report=controlled_build_inputs(tmp_path,monkeypatch)
    config=json.loads(path.read_text())
    policy={'node_modules/example':{'workflow':{'identity':'https://example.invalid/workflow','issuer':'https://example.invalid'}}}
    att={'bundle':'/private/bundle','bundle_sha256':'a'*64,'policy':policy,'trusted_root':'/private/root',
         'trusted_root_sha256':'b'*64,'cosign':'/private/cosign','cosign_sha256':'c'*64}
    config['npm_attestation']=att;path.write_text(json.dumps(config));loaded,_=cli.load_controlled_config(path)
    digest=hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    bound={**report,'npm_attestation':{'bundle_sha256':'a'*64,'policy_sha256':digest,'policy':policy,
             'trusted_root_sha256':'b'*64,'cosign_sha256':'c'*64}}
    assert cli.validate_controlled_preflight(loaded,bound,report['source_sha256'])==bound
    with pytest.raises(ValueError,match='attestation preflight'):
        cli.validate_controlled_preflight(loaded,{**bound,'npm_attestation':{**bound['npm_attestation'],'bundle_sha256':'0'*64}},report['source_sha256'])
    config['npm_attestation']['cosign']='relative';path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='absolute'):
        cli.load_controlled_config(path)


def test_attestation_preflight_requires_matching_frozen_materials(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'security'))
    import controlled_preflight as preflight_module
    import npm_attestation_bundle as bundles
    _,_,_,path,archive,_,report=controlled_build_inputs(tmp_path,monkeypatch)
    config=json.loads(path.read_text())
    policy={'node_modules/example':{'workflow':{'identity':'https://example.invalid/workflow','issuer':'https://example.invalid'}}}
    att={'bundle':'/private/bundle','bundle_sha256':'a'*64,'policy':policy,'trusted_root':'/private/root',
         'trusted_root_sha256':'b'*64,'cosign':'/private/cosign','cosign_sha256':'c'*64}
    config['npm_attestation']=att;path.write_text(json.dumps(config));loaded,_=cli.load_controlled_config(path)
    policy_digest=hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    monkeypatch.setattr(preflight_module,'preflight_controlled',lambda **kwargs:dict(report))
    checked={'status':'verified-selected-frozen-npm-attestations','coverage':'incomplete',
             'archive_sha256':loaded['materials_sha256'],'lock_sha256':report['lock_sha256'],
             'manifest_sha256':report['materials_manifest_sha256'],'attestation_bundle_sha256':'a'*64,
             'attestation_policy_sha256':policy_digest}
    monkeypatch.setattr(bundles,'verify_attestation_bundle',lambda *args,**kwargs:dict(checked))
    result=cli.controlled_preflight(loaded,tmp_path,'a'*40,archive,report['source_sha256'])
    assert cli.validate_controlled_preflight(loaded,result,report['source_sha256'])==result
    checked['lock_sha256']='0'*64
    with pytest.raises(ValueError,match='differs from controlled preflight'):
        cli.controlled_preflight(loaded,tmp_path,'a'*40,archive,report['source_sha256'])


def test_controlled_preflight_failure_never_creates_intent(tmp_path,monkeypatch):
    _,_,key,path,_,_,_=controlled_build_inputs(tmp_path,monkeypatch)
    def preflight(*args):raise ValueError('frozen material mismatch')
    monkeypatch.setattr(cli,'controlled_preflight',preflight)
    monkeypatch.setattr(cli.subprocess,'run',lambda *a,**k:pytest.fail('preflight must stop build'))
    with pytest.raises(ValueError,match='frozen material mismatch'):
        cli.build_candidate(tmp_path,None,key,controlled_config=path)
    assert not (tmp_path/'build-intent.json').exists()


def test_controlled_config_must_be_private_and_have_no_duplicate_keys(tmp_path):
    path=tmp_path/'controlled.json';path.write_text('{"profile":"npm","profile":"cargo"}');path.chmod(0o600)
    with pytest.raises(ValueError,match='duplicate'):cli.load_controlled_config(path)
    path.chmod(0o644)
    with pytest.raises(ValueError,match='owner-only'):cli.load_controlled_config(path)


def test_controlled_dispatch_recovery_never_repeats_original_start(tmp_path,monkeypatch):
    plan,_,_,_,archive,_,report=controlled_build_inputs(tmp_path,monkeypatch)
    plan['repo']=str(tmp_path/'candidate');plan['controlled_baseline']['report']=report
    (tmp_path/'operator.json').write_text(json.dumps(plan))
    (tmp_path/'controlled-dispatch-intent.json').unlink()
    preflights=[];requests=[];original_result=(tmp_path/'result.json').read_bytes()
    monkeypatch.setattr(cli,'controlled_preflight',lambda *args:preflights.append(args) or report)
    def remote(action,data):
        assert action=='run';requests.append(data)
        if len(requests)==1:raise TimeoutError('unknown original start')
        if len(requests)==2:return {'status':'unresolved','reason':'original dispatch intent not established'}
        return {'abandoned':True,'state':{'stage':'abandoned'}}
    monkeypatch.setattr(cli,'remote',remote)
    with pytest.raises(TimeoutError):cli.run_task(tmp_path)
    assert (tmp_path/'controlled-dispatch-intent.json').exists()
    assert cli.run_task(tmp_path)['status']=='unresolved'
    assert (tmp_path/'result.json').read_bytes()==original_result
    assert cli.run_task(tmp_path)['abandoned'] is True
    assert [r['observe_only'] for r in requests]==[False,True,True] and len(preflights)==1


def test_controlled_transfer_binds_original_material_and_never_resends(tmp_path,monkeypatch):
    import hashlib
    material=tmp_path/'materials';material.write_bytes(b'fixed material')
    controlled={'materials_sha256':hashlib.sha256(material.read_bytes()).hexdigest()}
    build={'unit':'unit-1','attempt_id':'unit-1:0','revision':'b'*40,'manifest':{'controlled':controlled}}
    (tmp_path/'operator.json').write_text(json.dumps({'unit':'unit-1','baseline':'a'*40}))
    (tmp_path/'result.json').write_text(json.dumps({'revision':'b'*40,'attempt_id':'unit-1:0',
        'bundle_sha256':hashlib.sha256(b'bundle').hexdigest()}))
    (tmp_path/'result.bundle').write_bytes(b'bundle')
    (tmp_path/'build-intent.json').write_text(json.dumps({'controlled':controlled,
        'controlled_config':{'materials_archive':str(material)}}))
    out=tmp_path/'attested-build';out.mkdir()
    for name in ('image.tar','source.tar','diff','Dockerfile','build.json','build.sig'):(out/name).write_bytes(name.encode())
    monkeypatch.setattr(cli,'observe_candidate_build',lambda run:build)
    def remote(action,data):
        if action == 'validation_status':return {'accepted':False,'stage':'needs_validation','attempt_id':'unit-1:0','revision':'b'*40}
        if action == 'validation_transfer_preflight':return {'unit':'unit-1','attempt_id':'unit-1:0','revision':'b'*40,'baseline':'a'*40,
            'material_verifier':{'release':'/fixture/release','manifest_sha256':'a'*64}}
        return {}
    monkeypatch.setattr(cli,'remote',remote)
    copies=[];monkeypatch.setattr(cli,'copy',lambda source,unit,name=None,receive=False:copies.append((source,name)))
    monkeypatch.setattr(cli,'observe_validation_transfer',lambda run:{'complete':True})
    assert cli.transfer_validation_inputs(tmp_path)=={'complete':True}
    intent=json.loads((tmp_path/'transfer-intent.json').read_text())
    assert intent['files']['materials.tar']==controlled['materials_sha256']
    assert intent['material_verifier']=={'release':'/fixture/release','manifest_sha256':'a'*64}
    assert (material,'validation-inputs/materials.tar') in copies
    with pytest.raises(ValueError,match='original transfer'):cli.transfer_validation_inputs(tmp_path)


def test_controlled_material_substitution_blocks_transfer_before_intent(tmp_path,monkeypatch):
    import hashlib
    material=tmp_path/'materials';material.write_bytes(b'changed')
    controlled={'materials_sha256':hashlib.sha256(b'original').hexdigest()}
    build={'unit':'unit-1','attempt_id':'unit-1:0','revision':'b'*40,'manifest':{'controlled':controlled}}
    (tmp_path/'operator.json').write_text(json.dumps({'unit':'unit-1','baseline':'a'*40}))
    (tmp_path/'result.json').write_text(json.dumps({'revision':'b'*40,'attempt_id':'unit-1:0',
        'bundle_sha256':hashlib.sha256(b'bundle').hexdigest()}))
    (tmp_path/'result.bundle').write_bytes(b'bundle')
    (tmp_path/'build-intent.json').write_text(json.dumps({'controlled':controlled,
        'controlled_config':{'materials_archive':str(material)}}))
    out=tmp_path/'attested-build';out.mkdir()
    for name in ('image.tar','source.tar','diff','Dockerfile','build.json','build.sig'):(out/name).write_bytes(name.encode())
    monkeypatch.setattr(cli,'observe_candidate_build',lambda run:build)
    monkeypatch.setattr(cli,'remote',lambda action,data:{'accepted':False,'stage':'needs_validation',
        'attempt_id':'unit-1:0','revision':'b'*40})
    with pytest.raises(ValueError,match='materials differ'):cli.transfer_validation_inputs(tmp_path)
    assert not (tmp_path/'transfer-intent.json').exists()


def test_controlled_baseline_preflight_precedes_run_creation_and_remote_calls(tmp_path,monkeypatch):
    _,_,_,config,_,_,_=controlled_build_inputs(tmp_path,monkeypatch)
    task={'unit':'new-unit','repo':str(tmp_path/'candidate'),'baseline':'main','task':'fix',
          'acceptance':['checked'],'allowed_paths':['file'],'check_script':str(tmp_path/'check.py')}
    spec=tmp_path/'task.json';spec.write_text(json.dumps(task))
    monkeypatch.setattr(cli,'RUNS',tmp_path/'runs')
    def command(*args,**kwargs):
        if 'rev-parse' in args:return 'a'*40
        if 'status' in args:return ''
        if 'get-url' in args:return 'https://github.com/example/repo'
        if 'ls-remote' in args:return 'a'*40+' refs/heads/main'
        pytest.fail('unexpected command before preflight')
    monkeypatch.setattr(cli,'command',command)
    monkeypatch.setattr(cli,'remote',lambda *args:pytest.fail('preflight failure must precede remote'))
    def preflight(*args):raise ValueError('baseline lock mismatch')
    monkeypatch.setattr(cli,'controlled_preflight',preflight)
    with pytest.raises(ValueError,match='baseline lock mismatch'):
        cli.prepare(spec,controlled_config=config)
    assert not (tmp_path/'runs').exists()


def test_controlled_build_requires_original_dispatch_intent(tmp_path,monkeypatch):
    _,_,key,path,_,_,_=controlled_build_inputs(tmp_path,monkeypatch)
    (tmp_path/'controlled-dispatch-intent.json').unlink()
    monkeypatch.setattr(cli,'controlled_preflight',lambda *args:pytest.fail('missing original must not build'))
    with pytest.raises(ValueError,match='original controlled dispatch intent unavailable'):
        cli.build_candidate(tmp_path,None,key,controlled_config=path)
    assert not (tmp_path/'build-intent.json').exists()
