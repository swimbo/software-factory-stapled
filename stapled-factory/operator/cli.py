#!/usr/bin/env python3
"""Operator-controlled prepare, run/observe, and publish. Never merges or deploys."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

HOST = CONTROL = SIGNER = RUNS = None
INGRESS_PORT = RAW_INGRESS_PORT = None


def load_config(path):
    path = Path(path).expanduser()
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('operator config must be an owner-only regular file (chmod 600)')
    config = json.loads(path.read_text())
    fields = {'ssh_host', 'control_dir', 'signer', 'runs_dir', 'ingress_port', 'raw_ingress_port'}
    if not isinstance(config, dict) or set(config) != fields:
        raise ValueError('operator config must contain exactly the example template fields')
    if not isinstance(config['ssh_host'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]*', config['ssh_host']):
        raise ValueError('ssh_host must be an SSH alias or user@host, not command options')
    for field in ('control_dir', 'signer', 'runs_dir'):
        value = config[field]
        if not isinstance(value, str) or not value or not Path(value).expanduser().is_absolute() or '..' in Path(value).parts:
            raise ValueError(field + ' must be an absolute path without parent traversal')
        config[field] = str(Path(value).expanduser())
    # This path is also an scp remote operand; reject shell/glob syntax.
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', config['control_dir']):
        raise ValueError('control_dir must be an absolute remote path without shell metacharacters')
    for field in ('ingress_port', 'raw_ingress_port'):
        if type(config[field]) is not int or not 1 <= config[field] <= 65535:
            raise ValueError(field + ' must be a port between 1 and 65535')
    return config


def load_controlled_config(path):
    """Read an owner-only, fully specified controlled-build configuration."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                st.st_mode & 0o077 or st.st_size > 65536):
            raise ValueError('controlled config must be an owner-only regular file')
        raw = stream.read(65537)
    if len(raw) > 65536:raise ValueError('controlled config too large')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:raise ValueError('duplicate controlled config key')
            result[key] = value
        return result
    try:config = json.loads(raw, object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:raise ValueError('invalid controlled config') from exc
    required = {'profile', 'materials_archive', 'materials_sha256', 'base_layout', 'base_manifest',
                'builder_name', 'builder_container_id', 'builder_image_id', 'docker_host', 'docker_config',
                'docker_bin_sha256', 'buildx_bin_sha256'}
    if not isinstance(config, dict):raise ValueError('controlled config has missing or unexpected fields')
    runtime = {'npm_entrypoint'} if config.get('profile') == 'npm' else {'cargo_binary'}
    required |= runtime
    if set(config) not in (required, required | {'docker_bin'}):
        raise ValueError('controlled config has missing or unexpected fields')
    if config['profile'] not in ('npm', 'cargo'):
        raise ValueError('unsupported controlled profile')
    for name in ('materials_archive','base_layout','docker_config','docker_bin'):
        value = config.get(name, '/usr/local/bin/docker' if name == 'docker_bin' else None)
        if (not isinstance(value,str) or not Path(value).is_absolute() or '..' in Path(value).parts or
                any(c in value for c in ('\n','\r',','))):
            raise ValueError('controlled config requires safe absolute paths')
        config[name] = value
    for name, pattern in [('materials_sha256',r'[a-f0-9]{64}'),('base_manifest',r'sha256:[a-f0-9]{64}'),
                          ('builder_container_id',r'[a-f0-9]{64}'),('builder_image_id',r'sha256:[a-f0-9]{64}'),
                          ('builder_name',r'[a-z0-9][a-z0-9_-]{0,50}'),('docker_host',r'unix:///[^\s,]+'),
                          ('docker_bin_sha256',r'[a-f0-9]{64}'),('buildx_bin_sha256',r'[a-f0-9]{64}')]:
        if not isinstance(config[name],str) or not re.fullmatch(pattern,config[name]):
            raise ValueError('invalid controlled config '+name)
    if config['profile'] == 'npm':
        entry=config['npm_entrypoint']
        if (not isinstance(entry,str) or not re.fullmatch(r'[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\.(?:js|cjs|mjs)',entry) or
                any(part in ('.','..','.git','node_modules') for part in entry.split('/'))):
            raise ValueError('invalid npm entrypoint')
    elif not isinstance(config['cargo_binary'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',config['cargo_binary']):
        raise ValueError('invalid Cargo binary')
    return config, hashlib.sha256(raw).hexdigest()


def controlled_preflight(config, repo, revision, archive, archive_sha256):
    """Inspect frozen inputs and the selected builder before a one-shot intent."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'security'))
    from controlled_preflight import preflight_controlled
    with tempfile.TemporaryDirectory(prefix='factory-control-preflight-') as temp:
        source = Path(temp) / 'source.tar'
        source.write_bytes(archive)
        return preflight_controlled(profile=config['profile'], repo=repo, revision=revision,
            source_tar=source, source_sha256=archive_sha256, materials_archive=config['materials_archive'],
            materials_sha256=config['materials_sha256'], base_layout=config['base_layout'],
            base_manifest=config['base_manifest'], docker_bin=config['docker_bin'], docker_bin_sha256=config['docker_bin_sha256'],
            buildx_bin_sha256=config['buildx_bin_sha256'], docker_config=config['docker_config'],
            docker_host=config['docker_host'], builder_name=config['builder_name'],
            builder_container_id=config['builder_container_id'], builder_image_id=config['builder_image_id'],
            npm_entrypoint=config.get('npm_entrypoint'), cargo_binary=config.get('cargo_binary'))


def validate_controlled_preflight(config, report, source_sha256):
    common = {'profile','coverage','source_sha256','materials_sha256','lock_sha256','base_manifest','base_config',
              'builder_container_id','builder_image_id','builder_config_sha256','recipe_sha256','docker_bin_sha256','buildx_bin_sha256'}
    fields = common | ({'materials_manifest_sha256','cache_recipe_sha256','npm_entrypoint'} if config['profile'] == 'npm' else {'cargo_binary'})
    if not isinstance(report,dict) or set(report) != fields:
        raise ValueError('controlled preflight report incomplete')
    for name, value in [('profile',config['profile']),('coverage','incomplete'),('source_sha256',source_sha256),
                        ('materials_sha256',config['materials_sha256']),('base_manifest',config['base_manifest']),
                        ('builder_container_id',config['builder_container_id']),('builder_image_id',config['builder_image_id']),
                        ('docker_bin_sha256',config['docker_bin_sha256']),('buildx_bin_sha256',config['buildx_bin_sha256']),
                        ('npm_entrypoint' if config['profile']=='npm' else 'cargo_binary',
                         config['npm_entrypoint' if config['profile']=='npm' else 'cargo_binary'])]:
        if report.get(name) != value:raise ValueError('controlled preflight identity differs')
    digests = ['source_sha256','materials_sha256','lock_sha256','builder_container_id','builder_config_sha256','recipe_sha256','docker_bin_sha256','buildx_bin_sha256']
    if config['profile'] == 'npm':digests += ['materials_manifest_sha256','cache_recipe_sha256']
    if (any(not isinstance(report.get(name),str) or not re.fullmatch(r'[a-f0-9]{64}',report[name]) for name in digests) or
            not isinstance(report.get('base_config'),str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',report['base_config'])):
        raise ValueError('controlled preflight digest invalid')
    return report


def configure(config):
    global HOST, CONTROL, SIGNER, RUNS, INGRESS_PORT, RAW_INGRESS_PORT
    HOST, CONTROL = config['ssh_host'], config['control_dir']
    SIGNER, RUNS = Path(config['signer']), Path(config['runs_dir'])
    INGRESS_PORT, RAW_INGRESS_PORT = config['ingress_port'], config['raw_ingress_port']


def command(*args, cwd=None):
    return subprocess.check_output(list(map(str, args)), cwd=cwd, text=True).strip()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n');path.chmod(0o600)


def remote(action, data):
    code = Path(__file__).with_name('nuc.py').read_text()
    proc = subprocess.Popen(['ssh', '-o', 'BatchMode=yes', '-o', 'ForwardAgent=no', HOST,
        'python3 -c ' + shlex.quote(code)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    proc.stdin.write(json.dumps({'action': action, 'data': data, 'config': {
        'control_dir': CONTROL, 'ingress_port': INGRESS_PORT,
        'raw_ingress_port': RAW_INGRESS_PORT}}));proc.stdin.close()
    result = None
    for line in proc.stdout:
        message = json.loads(line)
        if 'result' in message:result = message['result']
        else:print(json.dumps(message), flush=True)
    if proc.wait() or result is None:
        raise ValueError('NUC operation incomplete; retain run and observe, never redispatch')
    return result


def copy(local, unit, name=None, receive=False):
    remote_path = HOST + ':' + CONTROL + '/approvals/' + unit + '/' + (name or Path(local).name)
    command('scp', '-q', *( [remote_path, str(local)] if receive else [str(local), remote_path]))


def sign(run):
    command('openssl', 'dgst', '-sha256', '-sign', SIGNER, '-out', run / 'image-approval.sig', run / 'image-approval.json')
    (run / 'image-approval.sig').chmod(0o600)


def validate_spec(spec):
    required = {'unit', 'repo', 'baseline', 'task', 'acceptance', 'allowed_paths', 'check_script'}
    if not isinstance(spec, dict) or set(spec) != required:
        raise ValueError('invalid task fields or unit')
    scalars = ('unit', 'repo', 'baseline', 'task', 'check_script')
    if any(not isinstance(spec[x], str) or not spec[x].strip() for x in scalars) or not re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', spec['unit']):
        raise ValueError('invalid task fields or unit')
    acceptance = spec['acceptance']
    if not isinstance(acceptance, list) or not acceptance or any(
            not isinstance(x, str) or not x.strip() for x in acceptance):
        raise ValueError('task and acceptance criteria required')
    paths = spec['allowed_paths']
    if not isinstance(paths, list) or not paths or any(not isinstance(x, str) or x.startswith('/') or
            any(p in ('', '.', '..', '.git') for p in x.split('/')) for x in paths):
        raise ValueError('explicit safe repository-relative allowed_paths required')
    return spec



MAX_SOURCE_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_ARCHIVE_ENTRIES = 20000
SOURCE_ARCHIVE_TIMEOUT = 60


def _preflight_archive(repo, revision, env):
    args = ['git', '--no-replace-objects', '--no-lazy-fetch', '--no-optional-locks',
            '-c', 'core.attributesFile=' + os.devnull, '-c', 'tar.umask=0002',
            '-C', str(repo), 'archive', '--format=tar', revision]
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    output = bytearray();deadline = time.monotonic() + SOURCE_ARCHIVE_TIMEOUT
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ValueError('source archive timed out')
                data = os.read(process.stdout.fileno(), min(1024 * 1024, MAX_SOURCE_ARCHIVE_BYTES - len(output) + 1))
                if not data:break
                output.extend(data)
                if len(output) > MAX_SOURCE_ARCHIVE_BYTES:
                    raise ValueError('source archive exceeds byte limit')
        remaining = deadline - time.monotonic()
        if remaining <= 0:raise ValueError('source archive timed out')
        try:code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:raise ValueError('source archive timed out') from exc
        if code:raise ValueError('source archive failed')
        return bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()


def controlled_source_archive(repo, revision):
    """Bounded, noninteractive source archive for controlled preflight."""
    env = {key:value for key,value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_NO_REPLACE_OBJECTS='1',GIT_NO_LAZY_FETCH='1',GIT_OPTIONAL_LOCKS='0',
               GIT_TERMINAL_PROMPT='0',GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,
               GIT_ATTR_NOSYSTEM='1')
    return _preflight_archive(repo,revision,env)


def source_preflight(repo, revision):
    """Inspect one committed source archive without remote calls or side effects."""
    import importlib.util
    repo = Path(repo).resolve()
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1', GIT_OPTIONAL_LOCKS='0',
               GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_ATTR_NOSYSTEM='1')
    prefix = ['git', '--no-replace-objects', '--no-lazy-fetch', '--no-optional-locks',
              '-c', 'core.attributesFile=' + os.devnull, '-C', str(repo)]
    info_path = subprocess.check_output(prefix + ['rev-parse', '--git-path', 'info/attributes'],
                                        env=env, text=True, stderr=subprocess.DEVNULL, timeout=15).rstrip('\n')
    info = Path(info_path)
    if not info.is_absolute():info = repo / info
    try:info.lstat()
    except FileNotFoundError:pass
    else:raise ValueError('repository info attributes must be absent')
    args = prefix + ['rev-parse', '--verify', '--end-of-options', str(revision) + '^{commit}']
    selected = subprocess.check_output(args, env=env, text=True, stderr=subprocess.DEVNULL, timeout=15).strip()
    if not re.fullmatch(r'[a-f0-9]{40}', selected):
        raise ValueError('exact committed revision required')
    archive = _preflight_archive(repo, selected, env)
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as tar:
        for count, _ in enumerate(tar, 1):
            if count > MAX_SOURCE_ARCHIVE_ENTRIES:
                raise ValueError('source archive has too many entries')
    path = Path(__file__).resolve().parents[1] / 'spine/prebuilt_validation.py'
    spec = importlib.util.spec_from_file_location('factory_prebuilt_preflight', path)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    inventory = module.archive_dependency_inventory(archive)
    return {'status': 'held', 'revision': selected,
            'source_archive_sha256': hashlib.sha256(archive).hexdigest(),
            'source_inventory': inventory,
            'reason': 'Source inspection alone cannot establish complete dependency provenance.'}


def prepare(spec_file, *, controlled_config=None):
    spec = validate_spec(json.loads(Path(spec_file).read_text()))
    repo = Path(spec['repo']).resolve()
    baseline = command('git', '-C', repo, 'rev-parse', spec['baseline'] + '^{commit}')
    if command('git', '-C', repo, 'status', '--porcelain'):
        raise ValueError('source repository must be clean')
    origin = command('git', '-C', repo, 'remote', 'get-url', 'origin')
    main = command('git', '-C', repo, 'ls-remote', origin, 'refs/heads/main').split()
    if len(main) != 2 or baseline != main[0]:
        raise ValueError('baseline must be the current GitHub main commit; feature-branch baselines are not publishable')
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?', origin)
    if not match:raise ValueError('GitHub origin required')
    baseline_control = None
    if controlled_config is not None:
        config_path = Path(controlled_config)
        if config_path.resolve().is_relative_to(repo):raise ValueError('controlled config must stay outside source repository')
        config, config_sha = load_controlled_config(config_path)
        source = controlled_source_archive(repo,baseline)
        source_sha = hashlib.sha256(source).hexdigest()
        report = controlled_preflight(config,repo,baseline,source,source_sha)
        validate_controlled_preflight(config,report,source_sha)
        if load_controlled_config(config_path) != (config,config_sha):
            raise ValueError('controlled config changed during baseline preflight')
        baseline_control = {'config':config,'config_sha256':config_sha,'report':report}
    run = RUNS / spec['unit'];run.mkdir(mode=0o700, parents=True, exist_ok=False)
    check = Path(spec['check_script']).absolute()
    if check.is_symlink() or not check.is_file():raise ValueError('regular check script required')
    check = check.resolve()
    if check.is_relative_to(repo):raise ValueError('operator check must stay outside generator repository')
    (run / 'check.py').write_bytes(check.read_bytes());(run / 'check.py').chmod(0o600)
    # Verify code is operator-owned before it can later execute after diff review.
    if check.stat().st_uid != __import__('os').getuid() or check.is_symlink():
        raise ValueError('operator-owned regular check script required')
    command('git', '-C', repo, 'bundle', 'create', run / 'baseline.bundle', spec['baseline'])
    heads = command('git', 'bundle', 'list-heads', run / 'baseline.bundle').splitlines()
    refs = [x.split(' ', 1)[1] for x in heads if x.split(' ', 1)[0] == baseline]
    if len(refs) != 1:raise ValueError('baseline must name one Git ref, not a bare SHA')
    describe = remote('describe', {})
    plan = dict(spec, baseline=baseline, bundle_ref=refs[0], release=describe['release'],
                service_sha256=describe['service_sha256'], github=match[1], origin=origin,
                check_sha256=hashlib.sha256((run / 'check.py').read_bytes()).hexdigest())
    if baseline_control is not None:plan['controlled_baseline'] = baseline_control
    save(run / 'operator.json', plan)
    save(run / 'images.json', describe['images'])
    task = spec['task'] + '\n\nAcceptance criteria:\n' + '\n'.join('- ' + x for x in spec['acceptance'])
    task += '\nChange only: ' + ', '.join(spec['allowed_paths']) + '. Commit the fix and finish with a clean worktree. Do not publish, merge or deploy.\n'
    task += ('\nThe worker has no network access. Do not install dependencies. '
             'If a test runner is unavailable, do not search for replacements, probe unrelated packages, or write custom test harnesses. '
             'Complete the scoped edit, run git diff --check, commit, and finish immediately without optional checks. '
             'Report skipped tests; the operator runs external acceptance checks and local CI after collection.\n')
    task += ('\nReduce model round trips by batching independent file reads and searches into one terminal call. '
             'After edits, combine available checks, git diff --check, the scoped commit, and final git status in one terminal call, '
             'stopping on any failure. Keep required inspection and verification; do not skip them to save calls.\n')
    (run / 'task.txt').write_text(task);(run / 'task.txt').chmod(0o600)
    remote('mkdir', {'unit': spec['unit']})
    inputs = describe['build_inputs']
    for name in ('operator.json', 'images.json', 'task.txt', 'baseline.bundle'):
        copy(run / name, spec['unit'])
        inputs[CONTROL + '/approvals/' + spec['unit'] + '/' + name] = hashlib.sha256((run / name).read_bytes()).hexdigest()
    approval = {'version': 1, 'scope': 'sandbox', 'expires_at': int(time.time()) + 86400,
                'images': {k: v for k, v in describe['images'].items() if k != 'worker_environment'},
                'build_inputs': inputs}
    save(run / 'image-approval.json', approval);sign(run)
    for name in ('image-approval.json', 'image-approval.sig'):copy(run / name, spec['unit'])
    save(run / 'image-approval.json', remote('prepare', plan));sign(run)
    for name in ('image-approval.json', 'image-approval.sig'):copy(run / name, spec['unit'])
    save(run / 'status.json', {'status': 'PREPARED', 'unit': spec['unit'], 'baseline': baseline,
                              'budget': '$2 best-effort estimate; actual charges not capped'})
    return {'run': str(run), 'status': 'PREPARED', 'next': 'Review operator.json and task.txt, then run'}



def budget_summary(ledger):
    requests = ledger.get('budgets')
    money = ledger.get('monetary_budgets')
    return {'requests_used': sum(row[3] for row in requests) if requests else None,
            'request_limit': sum(row[2] for row in requests) if requests else None,
            'estimated_reserved_usd': sum(row[-1] for row in money) / 1000000 if money else None,
            'estimated_target_usd': sum(row[1] for row in money) / 1000000 if money else None,
            'actual_charges': 'unknown; estimates are not actual charges or a hard cap'}

def run_task(run):
    status_path = run / 'status.json'
    if status_path.exists():
        status = json.loads(status_path.read_text())
        if status.get('status') in ('PR_OPEN', 'ABANDONED'):
            return status  # Re-observation must not erase the publication result.
    plan = json.loads((run / 'operator.json').read_text())
    request = {'unit': plan['unit']}
    if 'controlled_baseline' in plan:
        original = plan['controlled_baseline'];marker = run/'controlled-dispatch-intent.json'
        if not isinstance(original,dict) or set(original) != {'config','config_sha256','report'}:
            raise ValueError('invalid controlled baseline preflight')
        expected = {'unit':plan['unit'],'baseline':plan['baseline'],
                    'config_sha256':original['config_sha256'],'report':original['report']}
        if marker.exists():
            st = marker.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise ValueError('unsafe controlled dispatch intent')
            if json.loads(marker.read_text()) != expected:
                raise ValueError('controlled original dispatch intent differs')
            request['observe_only'] = True
        else:
            source = controlled_source_archive(plan['repo'],plan['baseline'])
            source_sha = hashlib.sha256(source).hexdigest()
            report = controlled_preflight(original['config'],plan['repo'],plan['baseline'],source,source_sha)
            validate_controlled_preflight(original['config'],report,source_sha)
            if report != original['report']:
                raise ValueError('controlled baseline preflight changed')
            fd = os.open(marker,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:
                json.dump(expected,stream,sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
            fd = os.open(run,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
            request['observe_only'] = False
    result = remote('run', request)
    if request.get('observe_only') is True and result.get('status') == 'unresolved':
        return result
    if result.get('abandoned'):
        prior = json.loads(status_path.read_text()) if status_path.exists() else {}
        save(status_path, {**prior, 'status': 'ABANDONED', 'completed': False, 'state': result['state']})
        return result
    save(run / 'result.json', result)
    if result.get('requires_intervention'):
        save(run / 'status.json', {'status': 'NEEDS_INTERVENTION', 'state': result['state']})
        return result
    bundle = run / 'result.bundle'
    if not bundle.exists():copy(bundle, plan['unit'], receive=True)
    if hashlib.sha256(bundle.read_bytes()).hexdigest() != result['bundle_sha256']:
        raise ValueError('result bundle differs from trusted export')
    repo = run / 'candidate'
    if not repo.exists():command('git', 'clone', '-q', bundle, repo)
    if command('git', '-C', repo, 'rev-parse', 'HEAD') != result['revision']:
        raise ValueError('candidate revision mismatch')
    changed = command('git', '-C', repo, 'diff', '--name-only', plan['baseline'], result['revision']).splitlines()
    if set(changed) != set(result['changed']) or not set(changed) <= set(plan['allowed_paths']):
        raise ValueError('candidate path scope mismatch')
    (run / 'candidate.diff').write_text(command('git', '-C', repo, 'diff', plan['baseline'], result['revision']) + '\n')
    save(run / 'status.json', {'status': 'AWAITING_OPERATOR_DIFF_REVIEW', 'revision': result['revision'],
                              'changed': changed, 'budget': result['budget'], 'spending': budget_summary(result['budget']), 'actual_charges': 'unknown',
                              'runtime_validation': result['runtime_validation']})
    return {'status': 'AWAITING_OPERATOR_DIFF_REVIEW', 'diff': str(run / 'candidate.diff'),
            'next': 'Review source and tests before publish executes candidate CI'}


def build_candidate(run, dockerfile, signing_key, *, controlled_config=None):
    """Build and attest the collected original once; never redispatch a worker."""
    plan = json.loads((run / 'operator.json').read_text())
    result = json.loads((run / 'result.json').read_text())
    unit, revision, attempt = plan['unit'], result['revision'], result['attempt_id']
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', unit) or
            not re.fullmatch(r'[a-f0-9]{40}', revision) or
            attempt not in (unit + ':0', unit + ':0:successor') or
            not re.fullmatch(r'[a-f0-9]{40}', plan['baseline'])):
        raise ValueError('invalid collected build identity')
    intent = run / 'build-intent.json'
    output = run / 'attested-build'
    if intent.exists() or output.exists():
        raise ValueError('original image build exists; inspect retained evidence, never repeat')
    repo = run / 'candidate'
    bundle = run / 'result.bundle'
    if bundle.is_symlink() or not bundle.is_file() or hashlib.sha256(bundle.read_bytes()).hexdigest() != result['bundle_sha256']:
        raise ValueError('collected bundle differs')
    if command('git', '-C', repo, 'rev-parse', 'HEAD') != revision or command('git', '-C', repo, 'status', '--porcelain'):
        raise ValueError('candidate changed since collection')
    selected = remote('validation_status', {'unit': unit})
    if (selected.get('accepted') is not False or selected.get('stage') != 'needs_validation' or
            selected.get('attempt_id') != attempt or selected.get('revision') != revision):
        raise ValueError('selected build differs or is no longer awaiting validation')
    if (dockerfile is None) == (controlled_config is None):
        raise ValueError('choose exactly one legacy recipe or controlled config')
    if ('controlled_baseline' in plan) != (controlled_config is not None):
        raise ValueError('controlled build requires its original pre-dispatch baseline preflight')
    if controlled_config is not None:
        original = plan['controlled_baseline'];marker = run/'controlled-dispatch-intent.json'
        if not isinstance(original,dict) or set(original) != {'config','config_sha256','report'}:
            raise ValueError('invalid controlled baseline preflight')
        try:st = marker.lstat()
        except FileNotFoundError as exc:raise ValueError('original controlled dispatch intent unavailable') from exc
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ValueError('unsafe controlled dispatch intent')
        expected = {'unit':unit,'baseline':plan['baseline'],
                    'config_sha256':original['config_sha256'],'report':original['report']}
        if json.loads(marker.read_text()) != expected:
            raise ValueError('controlled original dispatch intent differs')
        if not isinstance(original['config_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',original['config_sha256']):
            raise ValueError('invalid original controlled config digest')
        source_sha = original['report'].get('source_sha256') if isinstance(original['report'],dict) else None
        validate_controlled_preflight(original['config'],original['report'],source_sha)
    signing_key = Path(signing_key)
    inputs = [signing_key] + ([Path(dockerfile)] if dockerfile is not None else [Path(controlled_config)])
    for path in inputs:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or path.resolve().is_relative_to(repo.resolve()):
            raise ValueError('operator-owned inputs outside candidate required')
    if signing_key.stat().st_mode & 0o077:
        raise ValueError('owner-only signing key required')
    archive = (controlled_source_archive(repo,revision) if controlled_config is not None else
               subprocess.check_output(['git', '-C', str(repo), 'archive', revision]))
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    record = {'unit': unit, 'attempt_id': attempt, 'revision': revision, 'baseline': plan['baseline'],
              'bundle_sha256': result['bundle_sha256'], 'archive_sha256': archive_sha256}
    if controlled_config is None:
        record['dockerfile_sha256'] = hashlib.sha256(Path(dockerfile).read_bytes()).hexdigest()
    else:
        config, config_sha256 = load_controlled_config(controlled_config)
        baseline_control = plan['controlled_baseline']
        baseline_config = baseline_control.get('config') if isinstance(baseline_control,dict) else None
        stable = set(config) - {'materials_archive','materials_sha256'}
        if not isinstance(baseline_config,dict) or any(baseline_config.get(name) != config[name] for name in stable):
            raise ValueError('controlled builder differs from pre-dispatch baseline')
        key = signing_key.resolve()
        forbidden = [output.resolve(), Path(config['base_layout']).resolve(), Path(config['docker_config']).resolve()]
        if any(key.is_relative_to(path) for path in forbidden) or key == Path(config['materials_archive']).resolve():
            raise ValueError('signing key must remain outside controlled inputs and contexts')
        report = controlled_preflight(config, repo, revision, archive, archive_sha256)
        validate_controlled_preflight(config, report, archive_sha256)
        report = {**report,'unit':unit,'tag':'factory-candidate-'+unit.lower()+':'+revision}
        record.update(dockerfile_sha256=report['recipe_sha256'], controlled=report,
                      controlled_config=config, controlled_config_sha256=config_sha256)
        if load_controlled_config(controlled_config) != (config, config_sha256):
            raise ValueError('controlled config changed during preflight')
        selected = remote('validation_status', {'unit':unit})
        if (selected.get('accepted') is not False or selected.get('stage') != 'needs_validation' or
                selected.get('attempt_id') != attempt or selected.get('revision') != revision):
            raise ValueError('selected build changed during controlled preflight')
    fd = os.open(intent, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(record, stream, sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    fd = os.open(run, os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    if controlled_config is None:
        script = Path(__file__).resolve().parents[1] / 'deployment/nuc/build_candidate.py'
        subprocess.run([sys.executable, str(script), '--repo', str(repo), '--revision', revision,
                        '--baseline', plan['baseline'], '--dockerfile', str(dockerfile),
                        '--tag', 'factory-candidate-' + unit.lower() + ':' + revision,
                        '--output', str(output), '--signing-key', str(signing_key)], check=True, timeout=900)
    else:
        script = Path(__file__).resolve().parents[1] / 'deployment/nuc/build_controlled.py'
        args = [sys.executable, str(script), '--repo', str(repo), '--revision', revision,
                '--baseline', plan['baseline'], '--unit', unit, '--output-dir', str(output),
                '--signing-key', str(signing_key)]
        for name in ('profile','materials_archive','materials_sha256','base_layout','base_manifest',
                     'builder_name','builder_container_id','builder_image_id','docker_host','docker_config','docker_bin',
                     'docker_bin_sha256','buildx_bin_sha256',
                     'npm_entrypoint' if config['profile']=='npm' else 'cargo_binary'):
            args.extend(['--'+name.replace('_','-'), config[name]])
        subprocess.run(args, check=True, timeout=2100)
    return observe_candidate_build(run)


def observe_candidate_build(run):
    """Read original build output only; no build, signature or submission replay."""
    record = json.loads((run / 'build-intent.json').read_text())
    output = run / 'attested-build'
    for name in ('build.json', 'build.sig', 'image.tar', 'source.tar', 'diff', 'Dockerfile'):
        path = output / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError('attested build output incomplete')
    manifest = json.loads((output / 'build.json').read_text())
    if (manifest.get('revision') != record['revision'] or manifest.get('baseline') != record['baseline'] or
            manifest.get('archive_sha256') != record['archive_sha256'] or
            manifest.get('dockerfile_sha256') != record['dockerfile_sha256'] or
            hashlib.sha256((output / 'diff').read_bytes()).hexdigest() != manifest.get('diff_sha256') or
            hashlib.sha256((output / 'source.tar').read_bytes()).hexdigest() != record['archive_sha256'] or
            hashlib.sha256((output / 'Dockerfile').read_bytes()).hexdigest() != record['dockerfile_sha256']):
        raise ValueError('attested build differs from original intent')
    if 'controlled' in record:
        if manifest.get('schema') != 'factory.build.v2' or manifest.get('controlled') != record['controlled']:
            raise ValueError('controlled build differs from original preflight intent')
    elif 'controlled' in manifest:
        raise ValueError('controlled manifest lacks matching original intent')
    if manifest.get('schema') == 'factory.build.v2':
        with (output / 'image.tar').open('rb') as stream:
            image_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
        if manifest.get('image_tar_sha256') != image_sha256:
            raise ValueError('attested image archive differs from signed build')
    elif manifest.get('schema') is not None:
        raise ValueError('unsupported build manifest schema')
    return {'status': 'BUILD_OUTPUT_PRESENT', 'unit': record['unit'], 'attempt_id': record['attempt_id'],
            'revision': record['revision'], 'output': str(output), 'manifest': manifest,
            'signature_verified': False, 'image_verified': False}


def transfer_validation_inputs(run):
    """Transfer one retained build without retrying an uncertain original."""
    intent = run / 'transfer-intent.json'
    if intent.exists():
        raise ValueError('original transfer exists; inspect it without repeating')
    build = observe_candidate_build(run)
    plan = json.loads((run / 'operator.json').read_text())
    result = json.loads((run / 'result.json').read_text())
    if any(build[k] != result[k] for k in ('revision', 'attempt_id')) or build['unit'] != plan['unit']:
        raise ValueError('build differs from collected attempt')
    selected = remote('validation_status', {'unit': plan['unit']})
    if (selected.get('accepted') is not False or selected.get('stage') != 'needs_validation' or
            selected.get('attempt_id') != result['attempt_id'] or selected.get('revision') != result['revision']):
        raise ValueError('selected build differs or is no longer awaiting validation')
    names = ('image.tar', 'source.tar', 'diff', 'Dockerfile', 'build.json', 'build.sig', 'result.bundle')
    paths = {name: (run / 'result.bundle' if name == 'result.bundle' else run / 'attested-build' / name) for name in names}
    build_intent = {}
    if 'controlled' in build.get('manifest',{}):
        build_intent = json.loads((run / 'build-intent.json').read_text())
        if build_intent.get('controlled') != build['manifest']['controlled']:
            raise ValueError('original controlled build binding differs')
        config = build_intent.get('controlled_config')
        if not isinstance(config,dict) or not isinstance(config.get('materials_archive'),str):
            raise ValueError('original controlled material path unavailable')
        paths['materials.tar'] = Path(config['materials_archive'])
    hashes = {}
    for name, path in paths.items():
        if path.is_symlink() or not path.is_file():
            raise ValueError('unsafe original transfer file')
        with path.open('rb') as stream:hashes[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    if hashes['result.bundle'] != result['bundle_sha256']:
        raise ValueError('collected bundle differs')
    if 'controlled' in build_intent and hashes['materials.tar'] != build_intent['controlled'].get('materials_sha256'):
        raise ValueError('controlled materials differ from original build intent')
    data = {'unit': plan['unit'], 'attempt_id': result['attempt_id'], 'revision': result['revision'],
            'baseline': plan['baseline'], 'files': hashes}
    if 'controlled' in build_intent:
        preflight = remote('validation_transfer_preflight', {'unit':plan['unit']})
        verifier = preflight.get('material_verifier') if isinstance(preflight,dict) else None
        if (not isinstance(preflight,dict) or any(preflight.get(k) != data[k] for k in ('unit','attempt_id','revision','baseline')) or
                not isinstance(verifier,dict) or set(verifier) != {'release','manifest_sha256'} or
                not isinstance(verifier['release'],str) or not Path(verifier['release']).is_absolute() or
                not isinstance(verifier['manifest_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',verifier['manifest_sha256'])):
            raise ValueError('controlled transfer verifier preflight differs')
        data['material_verifier'] = verifier
    fd = os.open(intent, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    fd = os.open(run, os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    remote('validation_transfer_begin', data)
    for name, path in paths.items():copy(path, plan['unit'], name='validation-inputs/' + name)
    return observe_validation_transfer(run)


def observe_validation_transfer(run):
    intent = json.loads((run / 'transfer-intent.json').read_text())
    status = remote('validation_transfer_status', {'unit': intent['unit']})
    if (status.get('revision') != intent['revision'] or status.get('attempt_id') != intent['attempt_id'] or
            status.get('expected') != intent['files'] or
            not isinstance(status.get('files'), dict) or set(status['files']) != set(intent['files']) or
            type(status.get('complete')) is not bool or
            any(type(value) is not bool for value in status['files'].values()) or
            status['complete'] != all(status['files'].values()) or
            ('material_verifier' in intent and status.get('material_verifier') != intent['material_verifier'])):
        raise ValueError('original transfer observation differs')
    return status


def verify_validation_transfer(run):
    intent = json.loads((run / 'transfer-intent.json').read_text())
    build = observe_candidate_build(run)
    result = remote('validation_verify_transfer', {'unit': intent['unit']})
    if (result.get('verified') is not True or result.get('loaded') is not False or
            result.get('attempt_id') != intent['attempt_id'] or result.get('revision') != intent['revision'] or
            result.get('image_tar_sha256') != intent['files']['image.tar'] or
            result.get('manifest_sha256') != intent['files']['build.json'] or
            result.get('config_digest') != build['manifest'].get('config_digest')):
        raise ValueError('verified transfer differs from original local build')
    return result


def validation_image_load(run, *, observe_only=False):
    intent = json.loads((run / 'transfer-intent.json').read_text())
    verified = verify_validation_transfer(run)
    action = 'validation_load_status' if observe_only else 'validation_load'
    result = remote(action, {'unit': intent['unit']})
    if (type(result.get('loaded')) is not bool or result.get('revision') != intent['revision'] or
            result.get('attempt_id') != intent['attempt_id'] or result.get('tag') != verified['tag']):
        raise ValueError('loaded image observation differs from original transfer')
    if result['loaded']:
        if (not isinstance(result.get('image_id'), str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', result['image_id']) or
                result.get('config_digest') != verified['config_digest']):
            raise ValueError('loaded image identity differs')
    elif result.get('uncertain') is not True:
        raise ValueError('original image load outcome unavailable')
    return result


def prepare_validation_source(run, *, observe_only=False):
    intent = json.loads((run/'transfer-intent.json').read_text())
    loaded = validation_image_load(run, observe_only=True)
    if loaded.get('loaded') is not True:
        raise ValueError('original image load is unresolved')
    action = 'validation_prepare_status' if observe_only else 'validation_prepare'
    result = remote(action, {'unit': intent['unit']})
    if (result.get('prepared') is not True or result.get('unit') != intent['unit'] or
            result.get('attempt_id') != intent['attempt_id'] or result.get('revision') != intent['revision'] or
            result.get('baseline') != intent['baseline'] or result.get('image_id') != loaded['image_id']):
        raise ValueError('prepared validation source differs from original transfer')
    return result


def transfer_holdout(run, source):
    """Send one operator-owned holdout; never repeat an uncertain transfer."""
    intent = run/'holdout-intent.json'
    if intent.exists():raise ValueError('original holdout transfer exists; observe without repeating')
    plan = json.loads((run/'operator.json').read_text())
    result = json.loads((run/'result.json').read_text())
    source = Path(source)
    st = source.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or
            source.resolve().is_relative_to((run/'candidate').resolve()) or
            ('repo' in plan and source.resolve().is_relative_to(Path(plan['repo']).resolve()))):
        raise ValueError('private operator holdout outside candidate required')
    raw = source.read_bytes()
    document = json.loads(raw)
    if not isinstance(document, dict) or not isinstance(document.get('scenarios'), list) or not document['scenarios']:
        raise ValueError('nonempty holdout scenarios required')
    selected = remote('validation_status', {'unit': plan['unit']})
    if (selected.get('accepted') is not False or selected.get('stage') != 'needs_validation' or
            selected.get('attempt_id') != result['attempt_id'] or selected.get('revision') != result['revision']):
        raise ValueError('selected build differs or is no longer awaiting validation')
    data = {'unit': plan['unit'], 'attempt_id': result['attempt_id'], 'revision': result['revision'],
            'sha256': hashlib.sha256(raw).hexdigest()}
    fd = os.open(intent, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    fd = os.open(run, os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    remote('validation_holdout_begin', data)
    copy(source, plan['unit'], name='holdouts/scenarios.json')
    return observe_holdout(run)


def observe_holdout(run):
    intent = json.loads((run/'holdout-intent.json').read_text())
    status = remote('validation_holdout_status', {'unit': intent['unit']})
    if (type(status.get('complete')) is not bool or status.get('sha256') != intent['sha256'] or
            status.get('attempt_id') != intent['attempt_id'] or status.get('revision') != intent['revision']):
        raise ValueError('original holdout observation differs')
    return status


def check_validation_manifest(run, proof):
    """Join v2 signed evidence to the original transferred manifest."""
    manifest_path = run / 'attested-build' / 'build.json'
    claimed = proof.get('build_manifest_digest')
    if not os.path.lexists(manifest_path):
        if claimed is not None:
            raise ValueError('signed validation manifest unavailable')
        return  # Existing legacy receipts did not bind a manifest.
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError('unsafe validation manifest')
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    schema = manifest.get('schema')
    if schema == 'factory.build.v2':
        transfer = json.loads((run / 'transfer-intent.json').read_text())
        digest = hashlib.sha256(raw).hexdigest()
        if transfer.get('files', {}).get('build.json') != digest or claimed != digest:
            raise ValueError('signed validation manifest differs from original transfer')
    elif schema is not None or claimed is not None:
        raise ValueError('unsupported or mismatched validation manifest')


def execute_validation(run, *, observe_only=False, resume_only=False):
    if observe_only and resume_only:raise ValueError('observation and continuation are mutually exclusive')
    intent = json.loads((run/'transfer-intent.json').read_text())
    prepared = prepare_validation_source(run, observe_only=True)
    action = 'validation_execute_status' if observe_only else 'validation_execute_resume' if resume_only else 'validation_execute'
    data = {'unit': intent['unit']}
    if not observe_only and not resume_only:
        script = Path(__file__).resolve().parents[1]/'deployment/nuc/validate.py'
        data['validator_sha256'] = hashlib.sha256(script.read_bytes()).hexdigest()
    result = remote(action, data)
    if result.get('status') == 'unresolved' or (result.get('accepted') is False and
            result.get('reason') == 'candidate validation rejected'):
        return result
    proof = result.get('evidence') or {}
    if (result.get('accepted') is not True or proof.get('unit_id') != intent['unit'] or
            proof.get('attempt_id') != intent['attempt_id'] or proof.get('git_revision') != intent['revision'] or
            proof.get('artifact_digest') != prepared['image_id'] or
            proof.get('source_archive_digest') != intent['files']['source.tar']):
        raise ValueError('accepted validation differs from original prepared build')
    check_validation_manifest(run, proof)
    return result


def application_validation(run, plan, result):
    observed = remote('validation_status', {'unit': plan['unit']})
    proof = observed.get('validation') or {}
    if (observed.get('accepted') is not True or observed.get('revision') != result['revision'] or
            observed.get('attempt_id') != result.get('attempt_id') or
            proof.get('unit_id') != plan['unit'] or proof.get('attempt_id') != result.get('attempt_id') or
            proof.get('git_revision') != result['revision']):
        raise ValueError('matching accepted application validation required')
    check_validation_manifest(run, proof)
    archive = subprocess.check_output(['git', '-C', str(run / 'candidate'), 'archive', result['revision']])
    if hashlib.sha256(archive).hexdigest() != proof.get('source_archive_digest'):
        raise ValueError('validation source archive differs from collected revision')
    engines = proof.get('security_engines') or {}
    if any(engines.get(k) != v for k, v in {'sast': 'semgrep+diff-regex',
            'secret': 'gitleaks+diff-regex', 'dependency': 'osv-scanner',
            'image_dependency': 'osv-scanner-image'}.items()):
        raise ValueError('complete application security engines required')
    return proof


def publish(run, *, repository_only=False):
    plan = json.loads((run / 'operator.json').read_text());repo = run / 'candidate'
    result = json.loads((run / 'result.json').read_text())
    if command('git', '-C', repo, 'rev-parse', 'HEAD') != result['revision'] or command('git', '-C', repo, 'status', '--porcelain'):
        raise ValueError('candidate changed since collection')
    if hashlib.sha256((run / 'check.py').read_bytes()).hexdigest() != plan['check_sha256']:
        raise ValueError('operator acceptance script changed')
    command('git', '-C', repo, 'remote', 'set-url', 'origin', plan['origin'])
    command('git', '-C', repo, 'fetch', '--no-tags', 'origin', 'main')
    command('git', '-C', repo, 'merge-base', '--is-ancestor', plan['baseline'], 'FETCH_HEAD')
    subprocess.run([sys.executable, str(run / 'check.py'), str(repo)], check=True)
    # This repository's trusted local gate is required; a worker cannot replace it.
    path = 'scripts/verify-local.py'
    original = command('git', '-C', repo, 'show', plan['baseline'] + ':' + path)
    if (repo / path).read_text().strip() != original.strip():
        raise ValueError('local CI runner changed; manual verification required')
    subprocess.run([sys.executable, str(repo / path)], check=True)
    proof = None if repository_only else application_validation(run, plan, result)
    branch = 'codex/' + plan['unit']
    prs = json.loads(command('gh', 'pr', 'list', '-R', plan['github'], '--head', branch,
                            '--state', 'open', '--json', 'url,headRefOid,body'))
    if prs:
        if len(prs) != 1 or prs[0]['headRefOid'] != result['revision']:
            raise ValueError('ambiguous existing PR')
        if proof and any(proof[k] not in (prs[0].get('body') or '')
                         for k in ('artifact_digest', 'checks_digest', 'scenario_digest')):
            raise ValueError('existing PR lacks matching application validation evidence')
    command('git', '-C', repo, 'push', 'origin', result['revision'] + ':refs/heads/' + branch)
    command('gh', 'api', 'repos/' + plan['github'] + '/statuses/' + result['revision'],
            '-f', 'state=success', '-f', 'context=local-ci/verified',
            '-f', 'description=Operator acceptance and exact local CI passed', '--silent')
    if prs:
        url = prs[0]['url']
    else:
        validation_note = ('Repository-only publication; signed application validation was not established.' if repository_only else
            'Signed application validation: artifact ' + proof['artifact_digest'] +
            ', checks SHA-256 ' + proof['checks_digest'] + ', scenario SHA-256 ' + proof['scenario_digest'] + '.')
        (run / 'pr.md').write_text('## Outcome\n\n' + plan['task'] + '\n\n## Verification\n\n'
            'Produced by the real NUC Restate/OpenHands factory. Operator acceptance checks and exact local CI passed. '
            'No GitHub runners. Reservations are estimates; actual charges unknown.\n\n'
            'Restate state: ' + result['runtime_validation'] + '. ' + validation_note + '\n\n'
            '## Release gate\n\nMerge and deployment remain explicit operator actions. Neither was performed.\n')
        url = command('gh', 'pr', 'create', '-R', plan['github'], '--base', 'main', '--head', branch,
                      '--title', plan['task'].splitlines()[0][:70], '--body-file', run / 'pr.md')
    save(run / 'status.json', {'status': 'PR_OPEN', 'url': url, 'revision': result['revision'],
                              'budget': result['budget'], 'spending': budget_summary(result['budget']), 'actual_charges': 'unknown',
                              'runtime_validation': result['runtime_validation'], 'merged': False, 'deployed': False})
    return json.loads((run / 'status.json').read_text())


def abandon(run, reason):
    plan = json.loads((run / 'operator.json').read_text())
    payload = remote('prepare_abandon', {'unit': plan['unit'], 'reason': reason})['payload']
    document, signature = run / 'abandonment.json', run / 'abandonment.sig'
    document.write_text(payload);document.chmod(0o600)
    command('openssl', 'dgst', '-sha256', '-sign', SIGNER, '-out', signature, document)
    signature.chmod(0o600)
    result = remote('abandon', {'unit': plan['unit'], 'envelope': {
        'payload': payload, 'signature': base64.b64encode(signature.read_bytes()).decode()}})
    save(run / 'abandonment-result.json', result)
    prior = json.loads((run / 'status.json').read_text()) if (run / 'status.json').exists() else {}
    save(run / 'status.json', {**prior, 'status': 'ABANDONED', 'unit': plan['unit'],
                              'reason': reason, 'completed': False, 'artifacts_retained': True})
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=os.environ.get('FACTORY_OPERATOR_CONFIG',
        str(Path(__file__).resolve().parents[2] / 'operator.local.json')),
        help='private operator settings (default: FACTORY_OPERATOR_CONFIG or repository operator.local.json)')
    p.add_argument('action', choices=['preflight-source', 'prepare', 'run', 'build-candidate', 'build-status', 'transfer-validation', 'transfer-status', 'verify-transfer', 'load-validation', 'load-status', 'prepare-validation', 'prepare-validation-status', 'transfer-holdout', 'holdout-status', 'validate', 'validate-status', 'validate-resume', 'publish', 'publish-repository', 'status', 'validation-status', 'abandon'])
    p.add_argument('path', type=Path, help='task JSON for prepare; private run directory otherwise')
    p.add_argument('--revision', help='committed revision for preflight-source')
    p.add_argument('--reason', help='required for abandon; recorded in durable evidence')
    recipes = p.add_mutually_exclusive_group()
    recipes.add_argument('--dockerfile', type=Path, help='operator-owned image recipe for build-candidate')
    recipes.add_argument('--controlled-config', type=Path, help='owner-only controlled build settings')
    p.add_argument('--build-signing-key', type=Path, help='owner-only build attestation key')
    p.add_argument('--holdout', type=Path, help='owner-only scenarios outside the candidate repository')
    a = p.parse_args()
    if a.controlled_config and a.action not in ('prepare','build-candidate'):
        p.error('--controlled-config applies only to prepare or build-candidate')
    if a.action == 'preflight-source':
        if not a.revision:p.error('preflight-source requires --revision')
        print(json.dumps(source_preflight(a.path, a.revision), indent=2))
        return
    try:configure(load_config(a.config))
    except (OSError, ValueError) as exc:p.error('Cannot load operator config: ' + str(exc))
    if a.action == 'prepare':result = prepare(a.path, controlled_config=a.controlled_config)
    elif a.action == 'run':
        try:result = run_task(a.path.resolve())
        except Exception:
            save(a.path / 'status.json', {'status': 'NEEDS_INTERVENTION',
                 'next': 'Inspect preserved run and NUC binding; rerun run only to observe same intent'})
            raise
    elif a.action == 'publish':result = publish(a.path.resolve())
    elif a.action == 'build-candidate':
        if (not a.dockerfile and not a.controlled_config) or not a.build_signing_key:
            p.error('build-candidate requires one recipe or controlled config and --build-signing-key')
        result = build_candidate(a.path.resolve(), a.dockerfile, a.build_signing_key, controlled_config=a.controlled_config)
    elif a.action == 'build-status':result = observe_candidate_build(a.path.resolve())
    elif a.action == 'transfer-validation':result = transfer_validation_inputs(a.path.resolve())
    elif a.action == 'transfer-status':result = observe_validation_transfer(a.path.resolve())
    elif a.action == 'verify-transfer':result = verify_validation_transfer(a.path.resolve())
    elif a.action == 'load-validation':result = validation_image_load(a.path.resolve())
    elif a.action == 'load-status':result = validation_image_load(a.path.resolve(), observe_only=True)
    elif a.action == 'prepare-validation':result = prepare_validation_source(a.path.resolve())
    elif a.action == 'prepare-validation-status':result = prepare_validation_source(a.path.resolve(), observe_only=True)
    elif a.action == 'transfer-holdout':
        if not a.holdout:p.error('transfer-holdout requires --holdout')
        result = transfer_holdout(a.path.resolve(), a.holdout)
    elif a.action == 'holdout-status':result = observe_holdout(a.path.resolve())
    elif a.action == 'validate':result = execute_validation(a.path.resolve())
    elif a.action == 'validate-status':result = execute_validation(a.path.resolve(), observe_only=True)
    elif a.action == 'validate-resume':result = execute_validation(a.path.resolve(), resume_only=True)
    elif a.action == 'publish-repository':result = publish(a.path.resolve(), repository_only=True)
    elif a.action == 'validation-status':
        plan = json.loads((a.path.resolve() / 'operator.json').read_text())
        result = remote('validation_status', {'unit': plan['unit']})
    elif a.action == 'abandon':
        if not a.reason or not a.reason.strip() or len(a.reason) > 500:p.error('abandon requires a nonblank --reason of at most 500 characters')
        result = abandon(a.path.resolve(), a.reason)
    else:result = json.loads((a.path / 'status.json').read_text())
    print(json.dumps(result, indent=2))


if __name__ == '__main__':main()
