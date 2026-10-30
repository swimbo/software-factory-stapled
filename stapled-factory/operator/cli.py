#!/usr/bin/env python3
"""Operator-controlled prepare, run/observe, and publish. Never merges or deploys."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

HOST = 'hermes-control'
CONTROL = '/home/bo/.local/share/stapled-factory-control'
SIGNER = Path.home() / '.local/share/stapled-factory-release/keys/image-approval.pem'
RUNS = Path.home() / '.local/share/stapled-factory-operator/runs'


def command(*args, cwd=None):
    return subprocess.check_output(list(map(str, args)), cwd=cwd, text=True).strip()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n');path.chmod(0o600)


def remote(action, data):
    code = Path(__file__).with_name('nuc.py').read_text()
    proc = subprocess.Popen(['ssh', '-o', 'BatchMode=yes', '-o', 'ForwardAgent=no', HOST,
        'python3 -c ' + shlex.quote(code)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    proc.stdin.write(json.dumps({'action': action, 'data': data}));proc.stdin.close()
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
        if status.get('status') == 'PR_OPEN':
            return status  # Re-observation must not erase the publication result.
    plan = json.loads((run / 'operator.json').read_text())
    result = remote('run', {'unit': plan['unit']})
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


def publish(run):
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
    branch = 'codex/' + plan['unit']
    command('git', '-C', repo, 'push', 'origin', result['revision'] + ':refs/heads/' + branch)
    command('gh', 'api', 'repos/' + plan['github'] + '/statuses/' + result['revision'],
            '-f', 'state=success', '-f', 'context=local-ci/verified',
            '-f', 'description=Operator acceptance and exact local CI passed', '--silent')
    prs = json.loads(command('gh', 'pr', 'list', '-R', plan['github'], '--head', branch,
                            '--state', 'open', '--json', 'url,headRefOid'))
    if prs:
        if len(prs) != 1 or prs[0]['headRefOid'] != result['revision']:raise ValueError('ambiguous existing PR')
        url = prs[0]['url']
    else:
        (run / 'pr.md').write_text('## Outcome\n\n' + plan['task'] + '\n\n## Verification\n\n'
            'Produced by the real NUC Restate/OpenHands factory. Operator acceptance checks and exact local CI passed. '
            'No GitHub runners. Reservations are estimates; actual charges unknown.\n\n'
            'Restate state: ' + result['runtime_validation'] + '. Local repository checks do not establish signed app validation.\n\n'
            '## Release gate\n\nMerge and deployment remain explicit operator actions. Neither was performed.\n')
        url = command('gh', 'pr', 'create', '-R', plan['github'], '--base', 'main', '--head', branch,
                      '--title', plan['task'].splitlines()[0][:70], '--body-file', run / 'pr.md')
    save(run / 'status.json', {'status': 'PR_OPEN', 'url': url, 'revision': result['revision'],
                              'budget': result['budget'], 'spending': budget_summary(result['budget']), 'actual_charges': 'unknown',
                              'runtime_validation': result['runtime_validation'], 'merged': False, 'deployed': False})
    return json.loads((run / 'status.json').read_text())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'run', 'publish', 'status'])
    p.add_argument('path', type=Path, help='task JSON for prepare; private run directory otherwise')
    a = p.parse_args()
    if a.action == 'prepare':result = prepare(a.path)
    elif a.action == 'run':
        try:result = run_task(a.path.resolve())
        except Exception:
            save(a.path / 'status.json', {'status': 'NEEDS_INTERVENTION',
                 'next': 'Inspect preserved run and NUC binding; rerun run only to observe same intent'})
            raise
    elif a.action == 'publish':result = publish(a.path.resolve())
    else:result = json.loads((a.path / 'status.json').read_text())
    print(json.dumps(result, indent=2))


if __name__ == '__main__':main()
