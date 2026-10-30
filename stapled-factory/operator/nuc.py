"""Trusted one-shot NUC operations, invoked by the operator CLI over SSH stdin.
No provider credentials leave the host, and no runtime source is deployed here.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import tempfile
import urllib.error
import urllib.request

ROOT = Path('/home/bo/.local/share/stapled-factory-control')


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
    req = urllib.request.Request('http://127.0.0.1:19081/' + route,
        json.dumps(data).encode(), headers={'Content-Type': 'application/json',
                                          'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def release():
    pid = command('systemctl', '--user', 'show', 'factory-endpoint', '-p', 'MainPID', '--value')
    path = Path('/proc', pid, 'cwd').resolve().parents[1]
    if path.parent != ROOT / 'releases':
        raise ValueError('endpoint is not a frozen release')
    manifest = json.loads((path / 'release-manifest.json').read_text())
    for name, digest in manifest['files'].items():
        if hashlib.sha256((path / name).read_bytes()).hexdigest() != digest:
            raise ValueError('frozen release changed')
    return path




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
    if action == 'mkdir':
        approval.mkdir(mode=0o700)
        return {'approval': str(approval)}
    if action == 'prepare':
        rel = release()
        if str(rel) != data['release']:
            raise ValueError('deployed release changed since preparation')
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
            req = urllib.request.Request('http://127.0.0.1:18080/WorkUnit/' + unit + '/abandon',
                json.dumps(data['envelope']).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=45) as response:
                if json.load(response) is not True:raise ValueError('abandonment refused by durable state')
            result = post('WorkUnit/' + unit + '/getState', {})
            if result.get('stage') != 'abandoned':raise ValueError('abandonment not committed')
            return {'status': 'ABANDONED', 'state': result, 'completed': False, 'artifacts_retained': True}
    if action != 'run':
        raise ValueError('unsupported operation')
    with (ROOT / 'operator.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        active = ROOT / 'operator-active.json'
        cached = approval / 'result.json'
        if cached.exists() and json.loads(cached.read_text()).get('binding_restored'):
            if active.exists():
                service = Path.home() / '.config/systemd/user/factory-endpoint.service'
                if json.loads(active.read_text())['unit'] != unit or service.read_text() != (approval / 'previous.service').read_text():
                    raise ValueError('stale active marker requires reconciliation')
                active.unlink()
            return json.loads(cached.read_text())
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
        intent = approval / 'dispatch-intent.json'
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
                revision = state['build']['git_revision']
                with sqlite3.connect('file:' + str(ROOT / 'attempt-store/attempts.db') + '?mode=ro', uri=True) as db:
                    binding = json.loads(db.execute('SELECT binding FROM allocation_bindings WHERE attempt_id=?',
                                                   (unit + ':0',)).fetchone()[0])
                worker, proxy = binding['worker_id'], binding['proxy_id']
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
                result = {'state': state, 'revision': revision, 'changed': changed, 'budget': ledger,
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
        result = main(req['action'], req['data'])
        print(json.dumps({'result': result}), flush=True)
    except Exception as exc:
        print(json.dumps({'error': str(exc), 'requires_intervention': True}), flush=True)
        sys.exit(1)
