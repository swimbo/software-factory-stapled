#!/usr/bin/env python3
"""Disposable cold Restate + SQLite coordinated restore and tamper controls.

No shared services or paid requests. Requires an already quiesced source; never
use this filesystem-copy procedure against a running Restate server.
"""
import hashlib
import json
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from restate_synthetic_successor import stop
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guarded_store import GuardedAttemptStore, StoreQuarantined
from egress_proxy import RequestBudget
import urllib.request

def request(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def digest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def validate(root, manifest):
    if digest(root) != manifest['files']:
        raise ValueError('incomplete, altered or mixed recovery generation')


def boot(data):
    proc = subprocess.Popen(['restate-server', '--base-dir', str(data), '--use-random-ports', 'true',
                             '--bind-ip', '127.0.0.1', '--listen-mode', 'tcp', '--no-logo'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        for _ in range(150):
            if proc.poll() is not None:
                raise RuntimeError('Restate exited')
            children = subprocess.run(['pgrep', '-P', str(proc.pid)], capture_output=True, text=True).stdout.split()
            for pid in [str(proc.pid), *children]:
                out = subprocess.run(['lsof', '-nP', '-a', '-p', pid, '-iTCP', '-sTCP:LISTEN', '-Fn'], capture_output=True, text=True).stdout
                for line in out.splitlines():
                    if line.startswith('n127.0.0.1:'):
                        try:
                            health = request('http://' + line[1:] + '/health')
                            if health.get('admin_status') == 'Ready':
                                addresses = {a['name']: a['address'] for a in health['bound_addresses'] if a['kind'] == 'tcp'}
                                proc.admin = 'http://' + addresses['admin-api-server']
                                proc.ingress = 'http://' + addresses['http-ingress-server']
                                return proc
                        except Exception:
                            pass
            time.sleep(.1)
        raise RuntimeError('Restate readiness timeout')
    except BaseException:
        stop(proc)
        raise


def main():
    with tempfile.TemporaryDirectory(prefix='factory-cold-restore-') as tmp:
        root = Path(tmp)
        source = root / 'source'; source.mkdir()
        import socket
        spine = Path(__file__).resolve().parents[2] / 'spine'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        endpoint_file = root / 'endpoint.mjs'
        endpoint_file.write_text(f"import * as r from '{spine}/node_modules/@restatedev/restate-sdk/dist/index.js'; import {{createServer}} from 'node:http2'; import {{workUnit}} from '{spine}/dist/state.js'; createServer(r.createEndpointHandler({{services:[workUnit]}})).listen({port},'127.0.0.1');")
        endpoint = subprocess.Popen(['node', str(endpoint_file)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        server = boot(source / 'restate')
        try:
            for _ in range(100):
                try:
                    request(server.admin + '/deployments', {'uri': f'http://127.0.0.1:{port}'})
                    break
                except Exception:
                    time.sleep(.1)
            else:
                raise RuntimeError('endpoint registration failed')
            states = {}
            for unit, status in [('complete', 'completed'), ('uncertain', 'unknown')]:
                call = lambda method, payload: request(server.ingress + f'/WorkUnit/{unit}/' + method, payload)
                assert call('claim', 'restore-drill') is True
                call('transition', 'building')
                call('recordRecoveryContext', {'request': {'unitId': unit}})
                import uuid
                build = {'attempt_id': unit + ':0', 'conversation_id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'stapled-factory:' + unit + ':0')), 'status': status}
                if status == 'completed': build['git_revision'] = 'a' * 40
                call('recordBuild', build)
                call('transition', 'needs_validation' if status == 'completed' else 'needs_reconciliation')
                states[unit] = call('getState', {})
        except BaseException:
            stop(server); stop(endpoint)
            raise
        stop(server)  # Dedicated backend only: clean stop before taking cold copy.
        for directory in ('attempts', 'attempt-anchor', 'budget', 'budget-anchor'):
            (source / directory).mkdir()
        attempts = GuardedAttemptStore.initialize(source / 'attempts/state.sqlite', source / 'attempt-anchor/state.json')
        for unit in ('completed', 'original', 'successor'):
            assert attempts.reserve(unit, 'hash-' + unit, 'conversation-' + unit) == ('new', None)
        old_attempt_db = (source / 'attempts/state.sqlite').read_bytes()
        attempts.complete('completed', '{"status":"completed"}')
        attempts.complete('successor', '{"status":"completed"}')
        GuardedAttemptStore.initialize(source / 'budget/state.sqlite', source / 'budget-anchor/state.json')
        budget_policy = dict(attempt='shared', model='flash', limit=6, fingerprint='a'*64,
                             ceiling_microdollars=2_000_000, reserve_microdollars=315802,
                             policy_id='restore-drill-estimate', expires_at=int(time.time()) + 3600)
        budget = RequestBudget(str(source / 'budget/state.sqlite'), anchor_path=str(source / 'budget-anchor/state.json'), **budget_policy)
        for _ in range(5):
            assert budget.reserve()
        (source / 'runtime.json').write_text(json.dumps({'paid_gateway': False, 'dispatch_enabled': False}))
        workspace = source / 'workspace'; workspace.mkdir()
        subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
        (workspace / 'result.txt').write_text('verified recovery fixture\n')
        subprocess.run(['git', '-C', str(workspace), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(workspace), '-c', 'user.name=Restore Drill', '-c', 'user.email=drill@example.invalid', 'commit', '-qm', 'fixture'], check=True)
        manifest = {'generation': 1, 'files': digest(source)}
        backup = root / 'backup'; shutil.copytree(source, backup)
        restored = root / 'restored'; shutil.copytree(backup, restored)
        validate(restored, manifest)
        for component in ('budget/state.sqlite', 'attempt-anchor/state.json', 'runtime.json'):
            saved = (restored / component).read_bytes()
            (restored / component).write_bytes(b'older generation')
            try:
                validate(restored, manifest)
            except ValueError:
                pass
            else:
                raise AssertionError('mixed generation accepted')
            (restored / component).write_bytes(saved)
        validate(restored, manifest)
        server = boot(restored / 'restate')
        try:
            for unit, expected in states.items():
                assert request(server.ingress + f'/WorkUnit/{unit}/getState', {}) == expected
                assert request(server.ingress + f'/WorkUnit/{unit}/claim', 'new-worker') is False
                assert request(server.ingress + f'/WorkUnit/{unit}/getState', {}) == expected
            attempts = GuardedAttemptStore(restored / 'attempts/state.sqlite', restored / 'attempt-anchor/state.json')
            assert attempts.reserve('original', 'hash-original', 'conversation-original') == ('unknown', None)
            for unit in ('completed', 'successor'):
                assert attempts.reserve(unit, 'hash-' + unit, 'conversation-' + unit) == ('completed', '{"status":"completed"}')
            current = (restored / 'attempts/state.sqlite').read_bytes()
            (restored / 'attempts/state.sqlite').write_bytes(old_attempt_db)
            try:
                attempts.lookup('completed', 'hash-completed', 'conversation-completed')
            except StoreQuarantined:
                pass
            else:
                raise AssertionError('actual store accepted rollback behind anchor')
            (restored / 'attempts/state.sqlite').write_bytes(current)
            budget = RequestBudget(str(restored / 'budget/state.sqlite'), anchor_path=str(restored / 'budget-anchor/state.json'), **budget_policy)
            with sqlite3.connect(restored / 'budget/state.sqlite') as db:
                assert db.execute('SELECT spent FROM monetary_budgets').fetchone()[0] == 1579010
            assert budget.reserve()
            assert not budget.reserve()
            subprocess.run(['git', '-C', str(restored / 'workspace'), 'fsck', '--no-progress'], check=True, capture_output=True)
        finally:
            stop(server)
            stop(endpoint)
        print(json.dumps({'status':'passed','actual_restate_cold_restore':True,'mixed_generation_controls':3,'actual_store_rollback_rejected':True,'reservation_microdollars':1579010,'paid_requests':0,'scope':'actual WorkUnit, guarded attempt and reservation stores; local cold copy; no independent protected backup authority'}))

if __name__ == '__main__':
    main()
