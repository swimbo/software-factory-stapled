#!/usr/bin/env python3
"""Disposable Restate plus real OpenHands synthetic-gateway integration probe.

Requires a built spine, node, restate-server, Docker, and lsof. Starts isolated
loopback processes and a temporary Restate database. Restate invokes the real
worker CLI with a private operator configuration and a synthetic gateway. The
unsigned endpoint and synthetic images are for this disposable probe only.
"""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
SPINE = ROOT / 'spine'


def request(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=300) as response:
        return json.load(response)


def stop(proc):
    if proc and proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


def main():
    assert (SPINE / 'dist' / 'build.js').is_file(), 'run npm run build in spine first'
    with tempfile.TemporaryDirectory(prefix='factory-restate-synthetic-') as tmp:
        root = Path(tmp).resolve()
        app, bindir = root / 'app', root / 'bin'
        app.mkdir(); bindir.mkdir(); (root / 'anchor').mkdir()
        (root / 'worker').symlink_to(ROOT / 'worker', target_is_directory=True)
        (root / 'home' / '.dsh').mkdir(parents=True)
        credential = root / 'home' / '.dsh' / '.credentials.yaml'
        credential.write_text('refs:\n  BIFROST_API_KEY: dummy-probe-key\n')
        credential.chmod(0o600)
        shutil.copytree(SPINE / 'dist', app / 'dist')
        shutil.copy(SPINE / 'package.json', app / 'package.json')
        (app / 'node_modules').symlink_to(SPINE / 'node_modules', target_is_directory=True)
        (app / 'server.mjs').write_text("""import * as restate from '@restatedev/restate-sdk';
import {createServer} from 'node:http2';
import {workUnit} from './dist/state.js';
import {orchestrate,reconcile,reconciliation} from './dist/orchestrate.js';
createServer(restate.createEndpointHandler({services:[workUnit,orchestrate,reconcile,reconciliation]})).listen(Number(process.env.TEST_PORT),'127.0.0.1');
""")
        cid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'stapled-factory:unit:0'))
        successor = str(uuid.uuid5(uuid.NAMESPACE_URL, 'stapled-factory:unit:0:successor'))
        probe = ROOT / 'worker' / 'probes' / 'successor_provisioned_agent.py'
        env = dict(os.environ, FACTORY_ATTEMPT_DB=str(root / 'db'),
                   FACTORY_ATTEMPT_ANCHOR=str(root / 'anchor' / 'file'),
                   FACTORY_RUNTIME_CONFIG=str(root / 'runtime.json'), HOME=str(root / 'home'),
                   FACTORY_PROBE_SYNC=str(root), FACTORY_PROBE_TOOL='1', FACTORY_PROBE_REAL_CLI='1')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); env['TEST_PORT'] = str(sock.getsockname()[1])
        server = endpoint = fixture = None
        try:
            probe_log = open(root / 'probe.log', 'w')
            fixture = subprocess.Popen([os.sys.executable, str(probe)], env=env, stdout=probe_log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            for _ in range(300):
                if (root / 'configured').exists(): break
                if fixture.poll() is not None: raise RuntimeError('probe setup failed: ' + (root / 'probe.log').read_text())
                time.sleep(1)
            else: raise RuntimeError('probe setup timed out')
            server = subprocess.Popen(['restate-server', '--base-dir', str(root / 'data'), '--use-random-ports', 'true',
                                       '--bind-ip', '127.0.0.1', '--listen-mode', 'tcp', '--no-logo'],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            # The npm launcher creates a child binary. Discover only descendants of our own process.
            admin = ingress = None
            for _ in range(100):
                if server.poll() is not None: raise RuntimeError('Restate exited before becoming ready')
                children = subprocess.run(['pgrep', '-P', str(server.pid)], capture_output=True, text=True).stdout.split()
                for pid in [str(server.pid), *children]:
                    out = subprocess.run(['lsof', '-nP', '-a', '-p', pid, '-iTCP', '-sTCP:LISTEN', '-Fn'], capture_output=True, text=True).stdout
                    for line in out.splitlines():
                        if not line.startswith('n127.0.0.1:'): continue
                        url = 'http://' + line[1:]
                        try:
                            health = request(url + '/health')
                            if health.get('admin_status') == 'Ready':
                                addresses = {a['name']: a['address'] for a in health['bound_addresses'] if a['kind'] == 'tcp'}
                                admin = 'http://' + addresses['admin-api-server']
                                ingress = 'http://' + addresses['http-ingress-server']
                                break
                        except (OSError, ValueError, urllib.error.HTTPError):
                            pass
                if admin: break
                time.sleep(.1)
            if not admin: raise RuntimeError('isolated Restate did not become ready')
            endpoint = subprocess.Popen(['node', str(app / 'server.mjs')], env=env, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, start_new_session=True)
            uri = 'http://127.0.0.1:' + env['TEST_PORT']
            for _ in range(100):
                if endpoint.poll() is not None: raise RuntimeError('synthetic endpoint exited')
                try:
                    request(admin + '/deployments', {'uri': uri})
                    break
                except (OSError, urllib.error.HTTPError):
                    time.sleep(.1)
            else: raise RuntimeError('synthetic endpoint registration failed')
            assert request(ingress + '/orchestrate/unit/run', {'unitId':'unit', 'target':'http://example.test',
                'scenarios':'/test', 'taskFile':str(root / 'task'), 'targetRepo':str(root / 'workspace')}) is None
            with sqlite3.connect(root / 'db') as db:
                row = db.execute('select allocation_id from allocations where attempt_id=?', ('unit:0',)).fetchone()
            if not row: raise RuntimeError('real worker did not allocate; state: ' + str(request(ingress + '/WorkUnit/unit/getState', {})))
            prefix = 'factory-' + row[0]
            proxy = prefix + '-proxy'
            for _ in range(90):
                exists = subprocess.run(['docker','exec',proxy,'test','-f','/tmp/first-inflight'], capture_output=True).returncode == 0
                if exists: break
                time.sleep(1)
            else: raise RuntimeError('original model call was not in flight')
            result = request(ingress + '/Reconciliation/unit/restart', {})
            assert result in ('unknown', 'needs_validation'), result
            if result == 'unknown':
                for _ in range(90):
                    result = request(ingress + '/Reconciliation/unit/observe', {})
                    if result == 'needs_validation': break
                    time.sleep(1)
            assert result == 'needs_validation', result
            state = request(ingress + '/WorkUnit/unit/getState', {})
            assert state['stage'] == 'needs_validation' and state['build']['status'] == 'unknown'
            assert state['successor']['attempt_id'] == 'unit:0:successor'
            assert state['successor']['conversation_id'] == successor
            revision = state['successor']['git_revision']
            assert len(revision) == 40
            assert (root / 'successor-workspace' / 'agent-effect.txt').read_text() == 'agent effect\n'
            assert subprocess.run(['docker','inspect','--format','{{.State.Running}}',prefix+'-worker'],
                capture_output=True, text=True, check=True).stdout.strip() == 'false'
            ledger = subprocess.run(['docker','exec',proxy,'python3','-c',
                "import sqlite3; d=sqlite3.connect('/budget/b.db'); print(*d.execute('select used, spent from budgets join monetary_budgets using (attempt)').fetchone())"],
                capture_output=True, text=True, check=True).stdout.strip()
            assert ledger == '5 2000000', ledger
            try:
                duplicate = request(ingress + '/Reconciliation/unit/restart', {})
                assert duplicate != 'needs_validation'
            except urllib.error.HTTPError as exc:
                assert exc.code == 500
            print('PASS: real Restate invoked the real worker CLI; fenced source, reconciled successor commit, shared synthetic $2 ledger; no real provider charges')

        finally:
            stop(endpoint); stop(server)
            (root / 'done').touch()
            if fixture:
                try: fixture.wait(timeout=90)
                except subprocess.TimeoutExpired: stop(fixture)
                probe_log.close()


if __name__ == '__main__':
    main()
