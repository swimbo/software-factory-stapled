"""Trusted one-shot NUC operations, invoked by the operator CLI over SSH stdin.
No provider credentials leave the host, and no runtime source is deployed here.
"""
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
import tempfile
import urllib.error
import urllib.request

ROOT = None
INGRESS_PORT = RAW_INGRESS_PORT = None


def configure(config):
    global ROOT, INGRESS_PORT, RAW_INGRESS_PORT
    ROOT = Path(config['control_dir'])
    INGRESS_PORT, RAW_INGRESS_PORT = config['ingress_port'], config['raw_ingress_port']


def command(*args):
    return subprocess.check_output(list(map(str, args)), text=True).strip()


def private(path, data):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.')
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(data, indent=2) + '\n');stream.flush();os.fsync(stream.fileno())
    os.replace(name, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)



def bundle_hash(path):
    # docker cp can preserve an untrusted worker symlink. Never dereference it
    # on the operator host, even while only computing an artifact digest.
    if path.is_symlink() or not path.is_file():
        raise ValueError('exported bundle must be a regular file, not a worker symlink')
    return hashlib.sha256(path.read_bytes()).hexdigest()

def post(route, data):
    token = (ROOT / 'ingress.token').read_text().strip()
    req = urllib.request.Request(f'http://127.0.0.1:{INGRESS_PORT}/' + route,
        json.dumps(data).encode(), headers={'Content-Type': 'application/json',
                                          'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def validation_capabilities(unit):
    req = urllib.request.Request(f'http://127.0.0.1:{RAW_INGRESS_PORT}/WorkUnit/{unit}/getValidationCapabilities',
                                 b'{}', headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def verify_release(path):
    path = Path(path)
    if path.parent != ROOT / 'releases':
        raise ValueError('runtime is not a frozen release')
    manifest = json.loads((path / 'release-manifest.json').read_text())
    for name, digest in manifest['files'].items():
        if hashlib.sha256((path / name).read_bytes()).hexdigest() != digest:
            raise ValueError('frozen release changed')
    return path


def scanner_bundle(selected=None):
    """Bind validation to an owner-selected, fully verified scanner tree."""
    root = ROOT/'scanners'
    if selected is None:
        selection = root/'selection.json'
        st = selection.lstat()
        if selection.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ValueError('owner-only scanner selection required')
        selected = json.loads(selection.read_text())
    if (not isinstance(selected, dict) or set(selected) != {'bundle', 'manifest_sha256'} or
            not isinstance(selected['bundle'], str) or not re.fullmatch(r'[A-Za-z0-9_-]+', selected['bundle']) or
            not isinstance(selected['manifest_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', selected['manifest_sha256'])):
        raise ValueError('invalid scanner selection')
    package = root/selected['bundle'];tree = package/'scanner';manifest = package/'files-final.json'
    for directory in (root, package, tree):
        st = directory.lstat()
        if directory.is_symlink() or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise ValueError('unsafe scanner directory')
    st = manifest.lstat()
    if manifest.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise ValueError('unsafe scanner manifest')
    data = manifest.read_bytes()
    if hashlib.sha256(data).hexdigest() != selected['manifest_sha256']:
        raise ValueError('scanner manifest differs')
    expected = json.loads(data)
    if not isinstance(expected, dict) or not all('bin/'+n in expected for n in ('semgrep', 'pysemgrep', 'gitleaks', 'osv-scanner', 'cosign')):
        raise ValueError('incomplete scanner manifest')
    actual = {}
    for path in tree.rglob('*'):
        st = path.lstat()
        if path.is_symlink() or st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise ValueError('unsafe scanner file')
        if stat.S_ISREG(st.st_mode):
            with path.open('rb') as stream:actual[str(path.relative_to(tree))] = hashlib.file_digest(stream, 'sha256').hexdigest()
        elif not stat.S_ISDIR(st.st_mode):raise ValueError('unsafe scanner file')
    if actual != expected or any(not os.access(tree/'bin'/n, os.X_OK) for n in ('semgrep', 'pysemgrep', 'gitleaks', 'osv-scanner', 'cosign')):
        raise ValueError('scanner files differ')
    return {'bundle': selected['bundle'], 'manifest_sha256': selected['manifest_sha256'], 'bin': str(tree/'bin')}


def release():
    pid = command('systemctl', '--user', 'show', 'factory-endpoint', '-p', 'MainPID', '--value')
    return verify_release(Path('/proc', pid, 'cwd').resolve().parents[1])


MATERIAL_MODULES = (
    'stapled-factory/security/material_store.py',
    'stapled-factory/security/npm_materials.py',
    'stapled-factory/security/controlled_npm.py',
    'stapled-factory/security/cargo_materials.py',
    'stapled-factory/security/cargo_vendor.py',
    'stapled-factory/security/controlled_cargo.py',
)


def material_verifier_release(path, manifest_sha256=None):
    """Validate every verifier module against one original frozen release."""
    path = verify_release(Path(path))
    for directory in (path, path/'stapled-factory', path/'stapled-factory/security'):
        st = directory.lstat()
        if directory.is_symlink() or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise ValueError('unsafe material verifier release directory')
    manifest_path = path/'release-manifest.json'
    st = manifest_path.lstat()
    if manifest_path.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise ValueError('unsafe material verifier release manifest')
    raw = manifest_path.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
    if manifest_sha256 is not None and digest != manifest_sha256:
        raise ValueError('original material verifier release changed')
    manifest = json.loads(raw); files = manifest.get('files')
    if not isinstance(files, dict) or not set(MATERIAL_MODULES).issubset(files):
        raise ValueError('frozen release lacks material verifier modules')
    for name in MATERIAL_MODULES:
        module = path/name; st = module.lstat()
        if (module.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                st.st_mode & 0o022 or not isinstance(files[name], str) or
                not re.fullmatch(r'[a-f0-9]{64}', files[name])):
            raise ValueError('unsafe material verifier module')
        with module.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != files[name]:
                raise ValueError('material verifier module changed')
    return {'release': str(path), 'manifest_sha256': digest}


def verify_controlled_materials(target, intent, binding, unit):
    """Check signed material, source lock and pinned release without executing a build."""
    controlled = binding.get('controlled')
    names = {'profile', 'coverage', 'source_sha256', 'materials_sha256', 'lock_sha256',
             'base_manifest', 'base_config', 'builder_container_id', 'builder_image_id',
             'builder_config_sha256', 'recipe_sha256', 'docker_bin_sha256', 'buildx_bin_sha256', 'unit', 'tag'}
    if not isinstance(controlled, dict) or controlled.get('profile') not in ('npm', 'cargo'):
        raise ValueError('unsupported controlled material profile')
    if controlled['profile'] == 'npm':names |= {'materials_manifest_sha256', 'cache_recipe_sha256', 'npm_entrypoint'}
    else:names.add('cargo_binary')
    sha_fields = {'source_sha256', 'materials_sha256', 'lock_sha256', 'builder_container_id',
                  'builder_config_sha256', 'recipe_sha256', 'docker_bin_sha256', 'buildx_bin_sha256'}
    if controlled['profile'] == 'npm':sha_fields |= {'materials_manifest_sha256', 'cache_recipe_sha256'}
    digest_fields = {'base_manifest', 'base_config', 'builder_image_id'}
    if (set(controlled) != names or controlled.get('coverage') != 'incomplete' or
            any(not isinstance(controlled.get(k), str) or not re.fullmatch(r'[a-f0-9]{64}', controlled[k]) for k in sha_fields) or
            any(not isinstance(controlled.get(k), str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', controlled[k]) for k in digest_fields) or
            binding.get('schema') != 'factory.build.v2' or controlled['unit'] != unit or
            controlled['tag'] != 'factory-candidate-' + unit.lower() + ':' + intent['revision'] or
            controlled['source_sha256'] != binding['archive_sha256'] or
            controlled['recipe_sha256'] != binding['dockerfile_sha256'] or
            controlled['materials_sha256'] != intent['files'].get('materials.tar')):
        raise ValueError('controlled build binding differs from signed manifest')
    if controlled['profile'] == 'cargo':
        binary = controlled['cargo_binary']
        if not isinstance(binary, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', binary):
            raise ValueError('invalid signed Cargo binary')
    else:
        entry = controlled['npm_entrypoint']
        if not isinstance(entry, str) or len(entry) > 4096:
            raise ValueError('invalid signed npm entrypoint')
        parts = entry.split('/')
        if (any(not re.fullmatch(r'[A-Za-z0-9._-]+', p) or p in ('.', '..', 'node_modules', '.git') for p in parts) or
                not entry.endswith(('.js', '.cjs', '.mjs'))):
            raise ValueError('invalid signed npm entrypoint')
    pin = intent.get('material_verifier')
    if (not isinstance(pin, dict) or set(pin) != {'release', 'manifest_sha256'} or
            not isinstance(pin['release'], str) or not isinstance(pin['manifest_sha256'], str) or
            not re.fullmatch(r'[a-f0-9]{64}', pin['manifest_sha256'])):
        raise ValueError('original material verifier release unavailable')
    material_verifier_release(pin['release'], pin['manifest_sha256'])
    # -I ignores current working directory and PYTHONPATH. Disable bytecode to
    # avoid writes to the frozen release, then import only from that release.
    program = r'''import hashlib, importlib, json, sys, tarfile
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
profile, archive, expected, source = sys.argv[2:]
module = importlib.import_module('controlled_npm' if profile == 'npm' else 'controlled_cargo')
verify = module.verify_frozen_npm if profile == 'npm' else module.verify_frozen_cargo
report = verify(archive, expected)
name = 'package-lock.json' if profile == 'npm' else 'Cargo.lock'
lock = None
with open(source, 'rb') as stream, tarfile.open(fileobj=module._BoundedTarInput(stream), mode='r:') as tar:
    for count, item in enumerate(tar, 1):
        if count > 100000: raise ValueError('too many source entries')
        if item.name == name:
            if lock is not None or not item.isfile() or item.size > 16 * 1024 * 1024:
                raise ValueError('unsafe source lock')
            lock = hashlib.file_digest(tar.extractfile(item), 'sha256').hexdigest()
if lock is None: raise ValueError('source lock missing')
print(json.dumps({'report': report, 'source_lock_sha256': lock}, sort_keys=True))
'''
    security = Path(pin['release'])/'stapled-factory/security'
    result = subprocess.run([sys.executable, '-I', '-c', program, str(security), controlled['profile'],
                             str(target/'materials.tar'), controlled['materials_sha256'], str(target/'source.tar')],
                            capture_output=True, text=True, check=True, timeout=600)
    verified = json.loads(result.stdout); report = verified.get('report') or {}
    if (report.get('coverage') != 'incomplete' or report.get('archive_sha256') != controlled['materials_sha256'] or
            report.get('lock_sha256') != controlled['lock_sha256'] or
            verified.get('source_lock_sha256') != controlled['lock_sha256'] or
            controlled['profile'] == 'npm' and report.get('manifest_sha256') != controlled['materials_manifest_sha256']):
        raise ValueError('verified controlled materials differ from signed inputs')
    material_verifier_release(pin['release'], pin['manifest_sha256'])
    return {'controlled_profile': controlled['profile'], 'controlled_materials_sha256': controlled['materials_sha256'],
            'controlled_lock_sha256': controlled['lock_sha256'], 'material_verifier': pin}




def assert_idle(service):
    line = next(x for x in service.splitlines() if x.startswith('Environment=FACTORY_RUNTIME_CONFIG='))
    cfg = json.loads(Path(line.split('=', 2)[2]).read_text())
    unit = cfg['attempt_id'].rsplit(':', 1)[0]
    if post('WorkUnit/' + unit + '/getState', {}).get('stage') not in ('unclaimed', 'needs_validation', 'validated', 'failed', 'abandoned'):
        raise ValueError('existing binding has unresolved work; observe it before a new task')

def endpoint_config():
    pid = command('systemctl', '--user', 'show', 'factory-endpoint', '-p', 'MainPID', '--value')
    environment = Path('/proc', pid, 'environ').read_bytes().split(b'\0')
    return next((x.split(b'=', 1)[1].decode() for x in environment
                 if x.startswith(b'FACTORY_RUNTIME_CONFIG=')), '')


def ensure_binding(service, previous, desired, config, intent):
    current = service.read_text()
    if intent.exists():
        if current != desired or endpoint_config() != str(config):
            raise ValueError('uncertain dispatch binding changed; manual reconciliation required')
        return
    if current not in (previous, desired):
        raise ValueError('endpoint changed during binding; manual intervention required')
    service.write_text(desired)
    command('systemctl', '--user', 'daemon-reload')
    command('systemctl', '--user', 'restart', 'factory-endpoint')
    for _ in range(30):
        try:
            if endpoint_config() == str(config):return
        except OSError:pass
        time.sleep(1)
    raise ValueError('intended endpoint binding not established; no dispatch made')


def restore_binding(service, previous, desired):
    if service.read_text() not in (previous, desired):
        raise ValueError('endpoint changed before restore; manual intervention required')
    service.write_text(previous)
    command('systemctl', '--user', 'daemon-reload')
    command('systemctl', '--user', 'restart', 'factory-endpoint')


def observation_lock():
    """Open the existing operator lock without creating a marker."""
    try:
        fd = os.open(ROOT/'operator.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ValueError('operator lock unavailable for read-only observation') from exc
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        os.close(fd)
        raise ValueError('unsafe operator lock')
    return os.fdopen(fd, 'r')


def original_file_exists(path):
    try:st = path.lstat()
    except FileNotFoundError:return False
    if not stat.S_ISREG(st.st_mode):
        raise ValueError('unsafe original dispatch evidence')
    return True


def main(action, data):
    if action == 'describe':
        rel = release()
        service = (Path.home() / '.config/systemd/user/factory-endpoint.service').read_text()
        line = next(x for x in service.splitlines() if x.startswith('Environment=FACTORY_RUNTIME_CONFIG='))
        cfg = json.loads(Path(line.split('=', 2)[2]).read_text())
        assert_idle(service)
        images = {k: cfg['provision'][k] for k in ('worker_image', 'proxy_image', 'helper_image', 'worker_environment')}
        images['firewall_image'] = cfg['provision']['firewall']['helper_image']
        paths = [rel / 'release-manifest.json', rel / 'local-ci.json']
        paths += list((rel / 'stapled-factory/worker').glob('*.py'))
        paths += list((rel / 'stapled-factory/spine/dist').glob('*.js'))
        return {'release': str(rel), 'images': images,
                'service_sha256': hashlib.sha256(service.encode()).hexdigest(),
                'build_inputs': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    unit = data['unit']
    import re
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', unit):
        raise ValueError('invalid unit')
    approval = ROOT / 'approvals' / unit
    run = ROOT / 'dispatch' / unit
    if action == 'dispatch_status':
        with observation_lock() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return {'intent_exists': original_file_exists(approval/'dispatch-intent.json'),
                    'result_exists': original_file_exists(approval/'result.json')}
    if action in ('validation_holdout_begin', 'validation_holdout_status'):
        folder = approval/'holdouts'
        if action == 'validation_holdout_begin':
            selected = main('validation_status', {'unit': unit})
            if (selected.get('accepted') is not False or selected.get('stage') != 'needs_validation' or
                    data.get('attempt_id') != selected.get('attempt_id') or data.get('revision') != selected.get('revision') or
                    not isinstance(data.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', data['sha256'])):
                raise ValueError('holdout binding differs from selected build')
            folder.mkdir(mode=0o700)
            private(folder/'holdout-intent.json', data)
            return {'created': True}
        intent = json.loads((folder/'holdout-intent.json').read_text())
        if intent.get('unit') != unit or not re.fullmatch(r'[a-f0-9]{64}', intent.get('sha256', '')):
            raise ValueError('original holdout binding differs')
        path = folder/'scenarios.json'
        complete = False
        if (not folder.is_symlink() and folder.is_dir() and folder.stat().st_uid == os.getuid() and
                not folder.stat().st_mode & 0o077 and not path.is_symlink() and path.is_file() and
                path.stat().st_uid == os.getuid() and not path.stat().st_mode & 0o077):
            with path.open('rb') as stream:complete = hashlib.file_digest(stream, 'sha256').hexdigest() == intent['sha256']
        return {'complete': complete, 'sha256': intent['sha256'], 'attempt_id': intent['attempt_id'], 'revision': intent['revision']}
    if action in ('validation_execute', 'validation_execute_status', 'validation_execute_resume'):
        target = approval/'validation-inputs'
        intent_path, session = target/'evaluation-intent.json', target/'validation-session'
        if action == 'validation_execute' and (intent_path.exists() or session.exists()):
            raise ValueError('original evaluation exists; observe without repeating')
        prepared = main('validation_prepare_status', {'unit': unit})
        plan = json.loads((approval/'operator.json').read_text())
        if action == 'validation_execute':
            rel = release()
            scanners = scanner_bundle()
        else:
            recorded = json.loads(intent_path.read_text())
            rel = verify_release(recorded['release'])
            scanners = scanner_bundle({k: recorded['scanners'][k] for k in ('bundle', 'manifest_sha256')})
        manifest_bytes = (rel/'release-manifest.json').read_bytes()
        manifest = json.loads(manifest_bytes)
        required = ('stapled-factory/deployment/nuc/validate.py', 'stapled-factory/spine/validation_session.py',
                    'stapled-factory/spine/prebuilt_validation.py', 'stapled-factory/spine/validation_transport.py',
                    'stapled-factory/security/pipeline_cli.py', 'stapled-factory/security/pipeline.py',
                    'stapled-factory/wall/evaluator/cli.py', 'stapled-factory/wall/evaluator/config.py',
                    'stapled-factory/wall/evaluator/evaluate.py', 'stapled-factory/wall/evaluator/gate.py',
                    'stapled-factory/wall/evaluator/receipt.py')
        if not all(name in manifest.get('files', {}) for name in required):
            raise ValueError('pinned runtime lacks frozen validation components')
        script = rel/'stapled-factory/deployment/nuc/validate.py'
        script_sha256 = hashlib.sha256(script.read_bytes()).hexdigest()
        if manifest['files'][required[0]] != script_sha256:
            raise ValueError('pinned validator differs from release manifest')
        if action == 'validation_execute' and data.get('validator_sha256') != script_sha256:
            raise ValueError('active validator differs from operator-reviewed source')
        scenarios = target/'frozen-scenarios.json'
        if action == 'validation_execute':
            holdouts, source = approval/'holdouts', approval/'holdouts/scenarios.json'
            holdout_status = main('validation_holdout_status', {'unit': unit})
            if (holdout_status.get('complete') is not True or
                    holdout_status.get('attempt_id') != prepared['attempt_id'] or
                    holdout_status.get('revision') != prepared['revision']):
                raise ValueError('original private holdout transfer incomplete')
            if (holdouts.is_symlink() or source.is_symlink() or not holdouts.is_dir() or not source.is_file() or
                    holdouts.stat().st_uid != os.getuid() or holdouts.stat().st_mode & 0o077 or
                    source.stat().st_uid != os.getuid() or source.stat().st_mode & 0o077):
                raise ValueError('private operator holdout unavailable')
            scenario_bytes = source.read_bytes()
            scenario_sha256 = hashlib.sha256(scenario_bytes).hexdigest()
            if scenario_sha256 != holdout_status['sha256']:
                raise ValueError('original private holdout changed')
        else:
            scenario_sha256 = recorded['scenario_sha256']
        original = {'unit': unit, 'attempt_id': prepared['attempt_id'], 'revision': prepared['revision'],
                    'baseline': prepared['baseline'], 'image_id': prepared['image_id'],
                    'release': str(rel), 'build_release': plan['release'],
                    'release_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                    'scanners': scanners,
                    'scenario_sha256': scenario_sha256, 'validator_sha256': script_sha256}
        if action == 'validation_execute':
            try:capabilities = validation_capabilities(unit)
            except (OSError, ValueError) as error:raise ValueError('runtime receipt capability unavailable') from error
            if capabilities != {'receipt_storage': 'atomic-v1'}:
                raise ValueError('runtime receipt capability unavailable')
            fd = os.open(intent_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(original, stream, sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
            fd = os.open(target, os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
            fd = os.open(scenarios, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(scenario_bytes);stream.flush();os.fsync(stream.fileno())
            fd = os.open(target, os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        else:
            if recorded != original:
                raise ValueError('original evaluation binding differs')
            scenario_matches = (not scenarios.is_symlink() and scenarios.is_file() and
                                hashlib.sha256(scenarios.read_bytes()).hexdigest() == scenario_sha256)
            if action == 'validation_execute_resume' and not scenario_matches:
                source = approval/'holdouts/scenarios.json'
                holdout = main('validation_holdout_status', {'unit': unit})
                if (holdout.get('complete') is not True or holdout.get('sha256') != scenario_sha256 or
                        holdout.get('attempt_id') != original['attempt_id'] or holdout.get('revision') != original['revision']):
                    raise ValueError('original private holdout unavailable')
                scenario_bytes = source.read_bytes()
                if hashlib.sha256(scenario_bytes).hexdigest() != scenario_sha256:
                    raise ValueError('original private holdout changed')
                scenarios = target/'recovery-scenarios.json'
                try:
                    fd = os.open(scenarios, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                except FileExistsError:
                    pass  # A previous continuation may have frozen this holdout.
                else:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(scenario_bytes);stream.flush();os.fsync(stream.fileno())
                    fd = os.open(target, os.O_RDONLY)
                    try:os.fsync(fd)
                    finally:os.close(fd)
            if scenarios.is_symlink() or not scenarios.is_file():
                return {'status': 'unresolved', 'reason': 'original frozen holdout unavailable'}
            with scenarios.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != scenario_sha256:
                    raise ValueError('original frozen holdout differs')
            if action == 'validation_execute_status' and not (session/'intent.json').is_file():
                return {'status': 'unresolved', 'reason': 'original evaluation journal not established'}
        args = ['python3', str(script), '--repo', str(target/'source-repo'), '--revision', original['revision'],
                '--baseline', original['baseline'], '--scenarios', str(scenarios), '--unit', unit,
                '--expected-scenario-digest', original['scenario_sha256'],
                '--attempt', original['attempt_id'], '--key-file', str(ROOT/'validation.key'),
                '--prebuilt-image', original['image_id'], '--source-archive', str(target/'source.tar'),
                '--diff-file', str(target/'diff'), '--build-manifest', str(target/'build.json'),
                '--build-signature', str(target/'build.sig'), '--session-dir', str(session),
                '--ingress', f'http://127.0.0.1:{RAW_INGRESS_PORT}']
        if action == 'validation_execute_status':args.append('--observe-only')
        if action == 'validation_execute_resume':args.extend(['--resume-only', '--resume-unstarted'])
        env = {k: v for k, v in os.environ.items() if not k.startswith('FACTORY_')}
        env['PATH'] = scanners['bin'] + os.pathsep + env.get('PATH', os.defpath)
        env['FACTORY_BUILD_APPROVAL_PUBLIC_KEY'] = str(ROOT/'operator-build-public.pem')
        completed = subprocess.run(args, capture_output=True, text=True, timeout=900, env=env)
        try:result = json.loads(completed.stdout)
        except ValueError as error:raise ValueError('original evaluation unresolved; inspect retained journal') from error
        if completed.returncode == 1 and result.get('accepted') is False and result.get('reason') == 'candidate validation rejected':
            return result
        if completed.returncode != 0 or result.get('accepted') is not True:
            raise ValueError('original evaluation unresolved; inspect retained journal')
        status = main('validation_status', {'unit': unit})
        if status.get('accepted') is not True or status.get('validation') != result.get('evidence'):
            raise ValueError('accepted receipt differs from durable WorkUnit')
        return result
    if action in ('validation_prepare', 'validation_prepare_status'):
        target = approval / 'validation-inputs'
        intent_path, repo = target / 'prepare-intent.json', target / 'source-repo'
        if action == 'validation_prepare' and (intent_path.exists() or repo.exists()):
            raise ValueError('original preparation exists; observe without repeating')
        verified = main('validation_verify_transfer', {'unit': unit})
        loaded = main('validation_load_status', {'unit': unit})
        transfer = json.loads((target/'transfer-intent.json').read_text())
        if (loaded.get('loaded') is not True or loaded.get('attempt_id') != verified['attempt_id'] or
                loaded.get('revision') != verified['revision'] or loaded.get('config_digest') != verified['config_digest']):
            raise ValueError('original verified image is not loaded')
        original = {'unit': unit, 'attempt_id': verified['attempt_id'], 'revision': verified['revision'],
                    'baseline': transfer['baseline'], 'image_id': loaded['image_id'],
                    'source_sha256': transfer['files']['source.tar'], 'diff_sha256': transfer['files']['diff'],
                    'bundle_sha256': transfer['files']['result.bundle']}
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0')
        if action == 'validation_prepare':
            fd = os.open(intent_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(original, stream, sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
            fd = os.open(target, os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
            subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', 'clone', '--no-checkout', '--',
                            str(target/'result.bundle'), str(repo)], check=True, capture_output=True, timeout=120, env=env)
        elif json.loads(intent_path.read_text()) != original:
            raise ValueError('original preparation binding differs')
        for key in ('revision', 'baseline'):
            actual = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', original[key]+'^{commit}'],
                                             timeout=30, env=env).decode().strip()
            if actual != original[key]:raise ValueError('prepared Git revision differs')
        subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', original['baseline'], original['revision']],
                       check=True, capture_output=True, timeout=30, env=env)
        archive = subprocess.check_output(['git', '-C', str(repo), 'archive', original['revision']], timeout=120, env=env)
        diff = subprocess.check_output(['git', '-C', str(repo), 'diff', '--no-ext-diff', '--no-textconv',
                                        original['baseline'], original['revision']], timeout=120, env=env)
        for name, raw, expected in [('source.tar', archive, original['source_sha256']), ('diff', diff, original['diff_sha256'])]:
            with (target/name).open('rb') as stream:transferred_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
            if hashlib.sha256(raw).hexdigest() != expected or transferred_hash != expected:
                raise ValueError('prepared ' + name + ' differs')
        return {'prepared': True, 'unit': unit, 'attempt_id': original['attempt_id'],
                'revision': original['revision'], 'baseline': original['baseline'], 'image_id': original['image_id']}
    if action in ('validation_load', 'validation_load_status'):
        target = approval / 'validation-inputs'
        intent_path = target / 'load-intent.json'
        if action == 'validation_load' and intent_path.exists():
            raise ValueError('original image load exists; observe without repeating')
        verified = main('validation_verify_transfer', {'unit': unit})
        if action == 'validation_load':
            selected = main('validation_status', {'unit': unit})
            if selected.get('accepted') is not False or selected.get('stage') != 'needs_validation':
                raise ValueError('selected build no longer awaiting validation')
        else:
            original = json.loads(intent_path.read_text())
            if original != verified:
                raise ValueError('original image load binding differs')
        listed = subprocess.run(['docker', 'image', 'ls', '--format', '{{.Repository}}:{{.Tag}}'],
                                check=True, capture_output=True, text=True, timeout=30).stdout.splitlines()
        if action == 'validation_load':
            if verified['tag'] in listed:
                raise ValueError('image tag already exists; do not overwrite')
            fd = os.open(intent_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(verified, stream, sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
            fd = os.open(target, os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
            subprocess.run(['docker', 'load', '--input', str(target/'image.tar')], check=True,
                           capture_output=True, timeout=180)
            return main('validation_load_status', {'unit': unit})
        if verified['tag'] not in listed:
            return {'loaded': False, 'uncertain': True, 'tag': verified['tag'],
                    'revision': verified['revision'], 'attempt_id': verified['attempt_id']}
        metadata = json.loads(subprocess.check_output(['docker', 'image', 'inspect', verified['tag']], timeout=30))
        if len(metadata) != 1 or not re.fullmatch(r'sha256:[a-f0-9]{64}', metadata[0].get('Id', '')):
            raise ValueError('loaded image identity unavailable')
        with tempfile.TemporaryDirectory(prefix='factory-loaded-image-') as temp:
            saved = Path(temp)/'image.tar'
            subprocess.run(['docker', 'save', '-o', str(saved), metadata[0]['Id']], check=True, timeout=180)
            with tarfile.open(saved) as tar:
                entries = json.load(tar.extractfile('manifest.json'))
                if len(entries) != 1:raise ValueError('loaded image metadata ambiguous')
                config = tar.extractfile(entries[0]['Config']).read()
        if 'sha256:' + hashlib.sha256(config).hexdigest() != verified['config_digest']:
            raise ValueError('loaded image configuration differs from signed original')
        return {'loaded': True, 'image_id': metadata[0]['Id'], 'tag': verified['tag'],
                'revision': verified['revision'], 'attempt_id': verified['attempt_id'],
                'config_digest': verified['config_digest']}
    if action == 'validation_verify_transfer':
        target = approval / 'validation-inputs'
        transfer = main('validation_transfer_status', {'unit': unit})
        selected = main('validation_status', {'unit': unit})
        if (not transfer['complete'] or selected.get('accepted') not in (True, False) or
                selected.get('stage') not in ('needs_validation', 'validated', 'gated', 'merged', 'deployed') or
                selected.get('attempt_id') != transfer['attempt_id'] or
                selected.get('revision') != transfer['revision']):
            raise ValueError('original transfer or selected build incomplete')
        intent = json.loads((target/'transfer-intent.json').read_text())
        subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-inkey', str(ROOT/'operator-build-public.pem'),
                        '-rawin', '-in', str(target/'build.json'), '-sigfile', str(target/'build.sig')],
                       check=True, capture_output=True, timeout=15)
        binding = json.loads((target/'build.json').read_text())
        if (binding.get('revision') != intent['revision'] or binding.get('baseline') != intent['baseline'] or
                any(binding.get(field) != intent['files'][name] for field, name in
                    [('archive_sha256', 'source.tar'), ('diff_sha256', 'diff'), ('dockerfile_sha256', 'Dockerfile')])):
            raise ValueError('signed build source differs from original transfer')
        if binding.get('schema') == 'factory.build.v2':
            if binding.get('image_tar_sha256') != intent['files']['image.tar']:
                raise ValueError('signed image archive differs from original transfer')
        elif binding.get('schema') is not None:
            raise ValueError('unsupported build manifest schema')
        if 'controlled' in binding:
            if 'materials.tar' not in intent['files']:
                raise ValueError('signed controlled materials missing from original transfer')
            controlled_verified = verify_controlled_materials(target, intent, binding, unit)
        elif 'materials.tar' in intent['files']:
            raise ValueError('legacy build cannot include controlled materials')
        else:
            controlled_verified = {}
        with tarfile.open(target/'image.tar') as tar:
            def member(name, limit):
                matches = [entry for entry in tar.getmembers() if entry.name == name]
                if len(matches) != 1 or not matches[0].isfile() or matches[0].size > limit:
                    raise ValueError('unsafe image metadata')
                return tar.extractfile(matches[0]).read()
            images = json.loads(member('manifest.json', 1024*1024))
            tag = 'factory-candidate-' + unit.lower() + ':' + intent['revision']
            if not isinstance(images, list) or len(images) != 1 or images[0].get('RepoTags') != [tag]:
                raise ValueError('image tag differs from original build')
            config = member(images[0]['Config'], 8*1024*1024)
        if 'sha256:' + hashlib.sha256(config).hexdigest() != binding.get('config_digest'):
            raise ValueError('signed image configuration differs')
        image = json.loads(config)
        labels = (image.get('config') or {}).get('Labels') or {}
        if (image.get('os') != 'linux' or image.get('architecture') != 'amd64' or
                labels.get('factory.git-revision') != intent['revision'] or
                labels.get('factory.source-archive-sha256') != binding['archive_sha256']):
            raise ValueError('signed image labels or platform differ')
        if controlled_verified:
            controlled = binding['controlled']
            if controlled['profile'] == 'cargo':
                expected_cmd = ['/factory/source/target/release/' + controlled['cargo_binary']]
            else:
                expected_cmd = ['node', '/workspace/' + controlled['npm_entrypoint']]
            if ((image.get('config') or {}).get('Cmd') != expected_cmd or
                    (image.get('config') or {}).get('Entrypoint') not in (None, [])):
                raise ValueError('signed controlled image command differs')
        return {'verified': True, 'attempt_id': intent['attempt_id'], 'revision': intent['revision'],
                'config_digest': binding['config_digest'], 'image_tar_sha256': intent['files']['image.tar'],
                'manifest_sha256': intent['files']['build.json'], 'tag': tag, 'loaded': False,
                **controlled_verified}
    if action == 'validation_transfer_preflight':
        selected = main('validation_status', {'unit': unit})
        if selected.get('accepted') is not False or selected.get('stage') != 'needs_validation':
            raise ValueError('selected build no longer awaiting validation')
        plan = json.loads((approval/'operator.json').read_text())
        baseline = plan.get('baseline')
        if not isinstance(baseline, str) or not re.fullmatch(r'[a-f0-9]{40}', baseline):
            raise ValueError('original validation baseline unavailable')
        pin = material_verifier_release(release())
        return {'unit': unit, 'attempt_id': selected['attempt_id'], 'revision': selected['revision'],
                'baseline': baseline, 'material_verifier': pin}
    if action in ('validation_transfer_begin', 'validation_transfer_status'):
        target = approval / 'validation-inputs'
        names = {'image.tar', 'source.tar', 'diff', 'Dockerfile', 'build.json', 'build.sig', 'result.bundle'}
        controlled_names = names | {'materials.tar'}
        if action == 'validation_transfer_begin':
            files = data.get('files')
            result = json.loads((approval / 'result.json').read_text())
            plan = json.loads((approval / 'operator.json').read_text())
            state = post('WorkUnit/' + unit + '/getState', {})
            selected = state.get('successor') or state.get('build') or {}
            if (not isinstance(files, dict) or set(files) not in (names, controlled_names) or
                    any(not isinstance(v, str) or not re.fullmatch(r'[a-f0-9]{64}', v) for v in files.values()) or
                    not result.get('binding_restored') or state.get('stage') != 'needs_validation' or
                    selected.get('status') != 'completed' or
                    selected.get('attempt_id') != result.get('attempt_id') or
                    selected.get('git_revision') != result.get('revision') or
                    data.get('attempt_id') != result.get('attempt_id') or
                    data.get('revision') != result.get('revision') or data.get('baseline') != plan.get('baseline')):
                raise ValueError('validation transfer binding differs')
            if set(files) == controlled_names:
                # Revalidate the exact preflight release, even if a different
                # release is now active; never substitute the active release.
                requested = data.get('material_verifier')
                if (not isinstance(requested, dict) or set(requested) != {'release', 'manifest_sha256'} or
                        not isinstance(requested['release'], str) or not isinstance(requested['manifest_sha256'], str) or
                        not re.fullmatch(r'[a-f0-9]{64}', requested['manifest_sha256'])):
                    raise ValueError('original material verifier release unavailable')
                if requested != material_verifier_release(requested['release'], requested['manifest_sha256']):
                    raise ValueError('original material verifier release differs')
                data = dict(data)
            target.mkdir(mode=0o700)
            private(target / 'transfer-intent.json', data)
            return {'created': True}
        intent = json.loads((target / 'transfer-intent.json').read_text())
        if (intent.get('unit') != unit or not isinstance(intent.get('files'), dict) or
                set(intent['files']) not in (names, controlled_names) or
                set(intent['files']) == controlled_names and 'material_verifier' not in intent or
                set(intent['files']) == names and 'material_verifier' in intent):
            raise ValueError('original transfer intent differs')
        matches = {}
        for name, expected in intent['files'].items():
            path = target / name
            if path.is_symlink() or not path.is_file():
                matches[name] = False
            else:
                with path.open('rb') as stream:
                    matches[name] = hashlib.file_digest(stream, 'sha256').hexdigest() == expected
        result = {'complete': all(matches.values()), 'files': matches, 'expected': intent['files'],
                  'attempt_id': intent['attempt_id'], 'revision': intent['revision']}
        if set(intent['files']) == controlled_names:
            result['material_verifier'] = intent['material_verifier']
        return result
    if action == 'validation_status':
        result = json.loads((approval / 'result.json').read_text())
        if not result.get('binding_restored'):
            raise ValueError('collected result binding unresolved')
        state = post('WorkUnit/' + unit + '/getState', {})
        selected = state.get('successor') or state.get('build') or {}
        if (selected.get('status') != 'completed' or
                selected.get('attempt_id') != result.get('attempt_id') or
                selected.get('git_revision') != result.get('revision')):
            raise ValueError('selected build differs from collected validation target')
        if state.get('stage') == 'needs_validation' and not state.get('validation'):
            return {'accepted': False, 'stage': 'needs_validation', 'attempt_id': result['attempt_id'],
                    'revision': result['revision']}
        proof = state.get('validation') or {}
        receipt = proof.get('receipt')
        if (state.get('stage') not in ('validated', 'gated', 'merged', 'deployed') or
                proof.get('unit_id') != unit or proof.get('attempt_id') != result['attempt_id'] or
                proof.get('git_revision') != result['revision'] or proof.get('passed') is not True or
                proof.get('scope') != 'disposable-candidate' or
                not isinstance(proof.get('artifact_digest'), str) or
                not re.fullmatch(r'sha256:[a-f0-9]{64}', proof['artifact_digest']) or
                not isinstance(proof.get('deployment_id'), str) or
                not re.fullmatch(r'[a-f0-9]{64}', proof['deployment_id']) or
                not isinstance(receipt, dict) or receipt.get('git_revision') != result['revision'] or
                state.get('receipt') != receipt or
                not all(isinstance(proof.get(k), str) and re.fullmatch(r'[a-f0-9]{64}', proof[k])
                        for k in ('checks_digest', 'source_archive_digest', 'scenario_digest'))):
            raise ValueError('accepted validation receipt unavailable or mismatched')
        return {'accepted': True, 'stage': state['stage'], 'attempt_id': result['attempt_id'],
                'revision': result['revision'], 'validation': proof}
    if action == 'mkdir':
        approval.mkdir(mode=0o700)
        return {'approval': str(approval)}
    if action == 'prepare':
        rel = release()
        if str(rel) != data['release']:
            raise ValueError('deployed release changed since preparation')
        # Preparation establishes the shared lock so future status requests can
        # remain read-only even before the first dispatch.
        try:fd = os.open(ROOT/'operator.lock', os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:pass
        else:os.close(fd)
        for p in approval.iterdir():
            p.chmod(0o600)
        sys.path.insert(0, str(rel / 'stapled-factory/deployment/nuc'))
        from prepare_dispatch import prepare
        prepare(root=ROOT / 'dispatch', unit=unit, manifest=approval / 'images.json',
                release=rel, approval=approval / 'image-approval.json',
                signature=approval / 'image-approval.sig', trust_root=ROOT / 'image-approval-public.pem',
                attempt_db=ROOT / 'attempt-store/attempts.db', attempt_anchor=ROOT / 'attempt-anchor/watermark.json',
                task_text=(approval / 'task.txt').read_text())
        baseline = data['baseline']
        for folder in ('workspace', 'successor'):
            repo = run / folder
            command('git', '-C', repo, 'fetch', '-q', approval / 'baseline.bundle', data['bundle_ref'])
            command('git', '-C', repo, 'checkout', '-q', '-B', 'factory-task', baseline)
        cfg_path = run / 'runtime.json'
        cfg = json.loads(cfg_path.read_text()); cfg['baseline'] = baseline
        cmd = cfg['provision']['proxy_command']
        cmd[cmd.index('--attempt-fingerprint') + 1] = hashlib.sha256(
            (unit + '\n' + baseline + '\n' + (run / 'task.txt').read_text()).encode()).hexdigest()
        private(cfg_path, cfg)
        private(approval / 'baseline.json', {'revision': baseline,
            'archive_sha256': hashlib.sha256(subprocess.check_output(
                ['git', '-C', str(run / 'workspace'), 'archive', baseline])).hexdigest()})
        doc = json.loads((approval / 'image-approval.json').read_text())
        for p in (cfg_path, run / 'task.txt', run / 'invocation.json', approval / 'baseline.json'):
            doc['build_inputs'][str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        private(approval / 'image-approval.json', doc)
        return doc
    if action in ('prepare_abandon', 'abandon'):
        with (ROOT / 'operator.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if (ROOT / 'operator-active.json').exists():
                raise ValueError('active observer/binding must be safely fenced and restored first')
            state = post('WorkUnit/' + unit + '/getState', {})
            if state.get('stage') not in ('needs_reconciliation', 'abandoned') or state.get('successor'):
                raise ValueError('unit is not an abandoned or unresolved original attempt')
            rel = release()
            helper = rel / 'stapled-factory/worker/abandon_attempt.py'
            if not helper.is_file():raise ValueError('deployed runtime does not support abandonment')
            if action == 'prepare_abandon':
                args = ['python3', str(helper), '--db', str(ROOT / 'attempt-store/attempts.db'),
                        '--anchor', str(ROOT / 'attempt-anchor/watermark.json'), '--unit', unit, '--reason=' + data['reason']]
                return {'payload': command(*args)}
            req = urllib.request.Request(f'http://127.0.0.1:{RAW_INGRESS_PORT}/WorkUnit/' + unit + '/abandon',
                json.dumps(data['envelope']).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=45) as response:
                if json.load(response) is not True:raise ValueError('abandonment refused by durable state')
            result = post('WorkUnit/' + unit + '/getState', {})
            if result.get('stage') != 'abandoned':raise ValueError('abandonment not committed')
            return {'status': 'ABANDONED', 'state': result, 'completed': False, 'artifacts_retained': True}
    if action != 'run':
        raise ValueError('unsupported operation')
    observe_only = data.get('observe_only', False)
    if type(observe_only) is not bool:
        raise ValueError('observe_only must be a boolean')
    if observe_only and not (ROOT/'operator.lock').exists():
        return {'status': 'unresolved', 'reason': 'operator lock unavailable for read-only observation'}
    with (observation_lock() if observe_only else (ROOT / 'operator.lock').open('a')) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        active = ROOT / 'operator-active.json'
        cached = approval / 'result.json'
        intent = approval / 'dispatch-intent.json'
        if observe_only and not original_file_exists(intent):
            return {'status': 'unresolved', 'reason': 'original dispatch intent not established'}
        if cached.exists() and json.loads(cached.read_text()).get('binding_restored'):
            if active.exists():
                if observe_only:
                    return {'status': 'unresolved', 'reason': 'restored result has an active marker requiring reconciliation'}
                service = Path.home() / '.config/systemd/user/factory-endpoint.service'
                if json.loads(active.read_text())['unit'] != unit or service.read_text() != (approval / 'previous.service').read_text():
                    raise ValueError('stale active marker requires reconciliation')
                active.unlink()
            return json.loads(cached.read_text())
        if observe_only and not original_file_exists(active):
            return {'status': 'unresolved', 'reason': 'original active binding unavailable for read-only observation'}
        if active.exists() and json.loads(active.read_text())['unit'] != unit:
            raise ValueError('another task needs observation; do not rebind')
        plan = json.loads((approval / 'operator.json').read_text())
        service = Path.home() / '.config/systemd/user/factory-endpoint.service'
        if not active.exists():
            if not cached.exists():
                terminal = post('WorkUnit/' + unit + '/getState', {})
                if terminal.get('stage') == 'abandoned':
                    return {'abandoned': True, 'state': terminal, 'completed': False}
            old = service.read_text()
            assert_idle(old)
            if hashlib.sha256(old.encode()).hexdigest() != plan['service_sha256']:
                raise ValueError('endpoint binding changed; prepare again')
            if str(release()) != plan['release']:
                raise ValueError('runtime changed; prepare again')
            (approval / 'previous.service').write_text(old)
            (approval / 'previous.service').chmod(0o600)
            private(active, {'unit': unit})
        previous = (approval / 'previous.service').read_text()
        desired = '\n'.join('Environment=FACTORY_RUNTIME_CONFIG=' + str(run / 'runtime.json')
                            if x.startswith('Environment=FACTORY_RUNTIME_CONFIG=') else x
                            for x in previous.splitlines()) + '\n'
        # A recorded result survives stopped containers and needs only cleanup.
        completed = json.loads(cached.read_text()) if cached.exists() else None
        if completed is None:
            ensure_binding(service, previous, desired, run / 'runtime.json', intent)
            for _ in range(30):
                try:
                    state = post('WorkUnit/' + unit + '/getState', {})
                    break
                except (OSError, ValueError):
                    time.sleep(1)
            else:
                raise ValueError('endpoint unavailable; binding retained for observation')
            if not intent.exists():
                if observe_only:
                    raise ValueError('original dispatch intent disappeared during observation')
                if state.get('stage') != 'unclaimed':
                    raise ValueError('unit already used')
                private(intent, {'unit': unit, 'baseline': plan['baseline']})
                cfg = json.loads((run / 'runtime.json').read_text())
                try:
                    post('orchestrate/' + unit + '/run', {'unitId': unit,
                        'target': 'http://127.0.0.1:1', 'scenarios': 'operator-local-checks',
                        'targetRepo': cfg['repo'], 'taskFile': cfg['task_file']})
                except (OSError, ValueError):
                    pass
            last = None
            for _ in range(data.get('polls', 120)):
                state = post('WorkUnit/' + unit + '/getState', {})
                if state.get('stage') != last:
                    last = state.get('stage')
                    print(json.dumps({'stage': last}), flush=True)
                if last in ('needs_validation', 'validated', 'failed'):
                    break
                time.sleep(5)
            else:
                raise ValueError('outcome unresolved; same binding retained, rerun run to observe')
            if last == 'failed':
                result = {'state': state, 'requires_intervention': True}
                private(approval / 'result.json', result)
            else:
                selected = state.get('successor') or state.get('build') or {}
                attempt = selected.get('attempt_id')
                revision = selected.get('git_revision')
                if (selected.get('status') != 'completed' or
                        attempt not in (unit + ':0', unit + ':0:successor') or
                        not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{40}', revision)):
                    raise ValueError('selected completed build required before collection')
                with sqlite3.connect('file:' + str(ROOT / 'attempt-store/attempts.db') + '?mode=ro', uri=True) as db:
                    row = db.execute('SELECT binding FROM allocation_bindings WHERE attempt_id=?',
                                     (unit + ':0',)).fetchone()
                    if not row:
                        raise ValueError('original allocation binding unavailable')
                    binding = json.loads(row[0])
                    worker, proxy = binding['worker_id'], binding['proxy_id']
                    if attempt.endswith(':successor'):
                        # Successors keep the original proxy/budget allocation;
                        # their worker is recorded in the consumed restart claim.
                        restart = db.execute('SELECT c.workspace, c.baseline, c.worker_id, c.lifecycle '
                            'FROM restart_reservations r JOIN restart_claims c ON r.source_attempt=c.source_attempt '
                            'WHERE r.source_attempt=? AND r.restart_id=?', (unit + ':0', attempt)).fetchone()
                        if (not restart or restart[:2] != (str(run / 'successor'), plan['baseline']) or
                                not isinstance(restart[2], str) or not re.fullmatch(r'[a-f0-9]{64}', restart[2]) or
                                not restart[3] or not isinstance(json.loads(restart[3]), dict)):
                            raise ValueError('selected successor binding unavailable')
                        worker = restart[2]
                def git(*args):return command('docker', 'exec', worker, 'git', '-C', '/workspace', *args)
                if git('rev-parse', 'HEAD') != revision or git('status', '--porcelain'):
                    raise ValueError('worker did not leave exact clean committed result')
                changed = git('diff', '--name-only', plan['baseline'], revision).splitlines()
                if not changed or not set(changed) <= set(plan['allowed_paths']):
                    raise ValueError('changes outside operator scope; binding retained')
                bundle = approval / 'result.bundle'
                if not bundle.exists():
                    git('bundle', 'create', '/tmp/operator-result.bundle', '--all')
                    command('docker', 'cp', worker + ':/tmp/operator-result.bundle', bundle)
                code = "import sqlite3,json;d=sqlite3.connect('file:/budget/b.db?mode=ro',uri=True);print(json.dumps({t:d.execute('SELECT * FROM '+t).fetchall() for t in ('budgets','monetary_budgets')}))"
                ledger = json.loads(command('docker', 'exec', proxy, 'python3', '-c', code))
                result = {'state': state, 'attempt_id': attempt, 'revision': revision, 'changed': changed, 'budget': ledger,
                          'bundle_sha256': bundle_hash(bundle),
                          'actual_charges': 'unknown', 'runtime_validation': last,
                          'worker': worker, 'proxy': proxy}
                private(approval / 'result.json', result)
        else:
            result = completed
            if result.get('revision') and bundle_hash(approval / 'result.bundle') != result['bundle_sha256']:
                raise ValueError('saved result bundle changed')
        if result.get('revision'):
            worker, proxy = result['worker'], result['proxy']
            # Keep ledgers/containers for audit; only stop this completed allocation.
            allocation = json.loads(command('docker', 'inspect', worker))[0]['Config']['Labels']['factory.allocation']
            if json.loads(command('docker', 'inspect', proxy))[0]['Config']['Labels']['factory.allocation'] != allocation:
                raise ValueError('allocation mismatch')
            command('docker', 'stop', worker, proxy)
        restore_binding(service, previous, desired)
        result['binding_restored'] = True
        private(approval / 'result.json', result)
        active.unlink()
        return result


if __name__ == '__main__':
    req = json.load(sys.stdin)
    try:
        configure(req['config'])
        result = main(req['action'], req['data'])
        print(json.dumps({'result': result}), flush=True)
    except Exception as exc:
        print(json.dumps({'error': str(exc), 'requires_intervention': True}), flush=True)
        sys.exit(1)
