#!/usr/bin/env python3
"""Operator-controlled prepare, run/observe, and publish. Never merges or deploys."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
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


def prepare(spec_file):
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
    result = remote('run', {'unit': plan['unit']})
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


def build_candidate(run, dockerfile, signing_key):
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
    dockerfile, signing_key = Path(dockerfile), Path(signing_key)
    for path in (dockerfile, signing_key):
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or path.resolve().is_relative_to(repo.resolve()):
            raise ValueError('operator-owned inputs outside candidate required')
    if signing_key.stat().st_mode & 0o077:
        raise ValueError('owner-only signing key required')
    archive = subprocess.check_output(['git', '-C', str(repo), 'archive', revision])
    record = {'unit': unit, 'attempt_id': attempt, 'revision': revision, 'baseline': plan['baseline'],
              'bundle_sha256': result['bundle_sha256'], 'archive_sha256': hashlib.sha256(archive).hexdigest(),
              'dockerfile_sha256': hashlib.sha256(dockerfile.read_bytes()).hexdigest()}
    fd = os.open(intent, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(record, stream, sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    fd = os.open(run, os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    script = Path(__file__).resolve().parents[1] / 'deployment/nuc/build_candidate.py'
    subprocess.run([sys.executable, str(script), '--repo', str(repo), '--revision', revision,
                    '--baseline', plan['baseline'], '--dockerfile', str(dockerfile),
                    '--tag', 'factory-candidate-' + unit.lower() + ':' + revision,
                    '--output', str(output), '--signing-key', str(signing_key)], check=True, timeout=900)
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
    hashes = {}
    for name, path in paths.items():
        if path.is_symlink() or not path.is_file():
            raise ValueError('unsafe original transfer file')
        with path.open('rb') as stream:hashes[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    if hashes['result.bundle'] != result['bundle_sha256']:
        raise ValueError('collected bundle differs')
    data = {'unit': plan['unit'], 'attempt_id': result['attempt_id'], 'revision': result['revision'],
            'baseline': plan['baseline'], 'files': hashes}
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
            status['complete'] != all(status['files'].values())):
        raise ValueError('original transfer observation differs')
    return status


def application_validation(run, plan, result):
    observed = remote('validation_status', {'unit': plan['unit']})
    proof = observed.get('validation') or {}
    if (observed.get('accepted') is not True or observed.get('revision') != result['revision'] or
            observed.get('attempt_id') != result.get('attempt_id') or
            proof.get('unit_id') != plan['unit'] or proof.get('attempt_id') != result.get('attempt_id') or
            proof.get('git_revision') != result['revision']):
        raise ValueError('matching accepted application validation required')
    archive = subprocess.check_output(['git', '-C', str(run / 'candidate'), 'archive', result['revision']])
    if hashlib.sha256(archive).hexdigest() != proof.get('source_archive_digest'):
        raise ValueError('validation source archive differs from collected revision')
    engines = proof.get('security_engines') or {}
    if any(engines.get(k) != v for k, v in {'sast': 'semgrep+diff-regex',
            'secret': 'gitleaks+diff-regex', 'dependency': 'osv-scanner'}.items()):
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
    p.add_argument('action', choices=['prepare', 'run', 'build-candidate', 'build-status', 'transfer-validation', 'transfer-status', 'publish', 'publish-repository', 'status', 'validation-status', 'abandon'])
    p.add_argument('path', type=Path, help='task JSON for prepare; private run directory otherwise')
    p.add_argument('--reason', help='required for abandon; recorded in durable evidence')
    p.add_argument('--dockerfile', type=Path, help='operator-owned image recipe for build-candidate')
    p.add_argument('--build-signing-key', type=Path, help='owner-only build attestation key')
    a = p.parse_args()
    try:configure(load_config(a.config))
    except (OSError, ValueError) as exc:p.error('Cannot load operator config: ' + str(exc))
    if a.action == 'prepare':result = prepare(a.path)
    elif a.action == 'run':
        try:result = run_task(a.path.resolve())
        except Exception:
            save(a.path / 'status.json', {'status': 'NEEDS_INTERVENTION',
                 'next': 'Inspect preserved run and NUC binding; rerun run only to observe same intent'})
            raise
    elif a.action == 'publish':result = publish(a.path.resolve())
    elif a.action == 'build-candidate':
        if not a.dockerfile or not a.build_signing_key:p.error('build-candidate requires --dockerfile and --build-signing-key')
        result = build_candidate(a.path.resolve(), a.dockerfile, a.build_signing_key)
    elif a.action == 'build-status':result = observe_candidate_build(a.path.resolve())
    elif a.action == 'transfer-validation':result = transfer_validation_inputs(a.path.resolve())
    elif a.action == 'transfer-status':result = observe_validation_transfer(a.path.resolve())
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
