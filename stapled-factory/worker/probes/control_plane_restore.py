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
        with sqlite3.connect(source / 'attempt.sqlite') as db:
            db.execute('CREATE TABLE attempts(id TEXT PRIMARY KEY,status TEXT)')
            db.executemany('INSERT INTO attempts VALUES (?,?)', [('completed','completed'), ('original','fenced'), ('successor','completed')])
        with sqlite3.connect(source / 'ledger.sqlite') as db:
            db.execute('CREATE TABLE reservations(id TEXT PRIMARY KEY,microdollars INTEGER)')
            db.execute('INSERT INTO reservations VALUES (?,?)', ('shared', 1579010))
        (source / 'anchor.json').write_text(json.dumps({'identity': 'restore-drill', 'generation': 1}))
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
        for component in ('ledger.sqlite', 'anchor.json', 'runtime.json'):
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
            with sqlite3.connect(restored / 'attempt.sqlite') as db:
                assert db.execute('SELECT * FROM attempts ORDER BY id').fetchall() == [('completed','completed'), ('original','fenced'), ('successor','completed')]
            with sqlite3.connect(restored / 'ledger.sqlite') as db:
                assert db.execute('SELECT SUM(microdollars) FROM reservations').fetchone()[0] == 1579010
            subprocess.run(['git', '-C', str(restored / 'workspace'), 'fsck', '--no-progress'], check=True, capture_output=True)
        finally:
            stop(server)
            stop(endpoint)
        print(json.dumps({'status':'passed','actual_restate_cold_restore':True,'mixed_generation_controls':3,'reservation_microdollars':1579010,'paid_requests':0,'scope':'actual completed and unresolved WorkUnit state; fixture SQLite stores; no independent protected backup authority'}))

if __name__ == '__main__':
    main()
