#!/usr/bin/env python3
"""Synthetic-only Restate control-plane probe: no OpenHands, network egress, or billing.

Requires a built spine, node, restate-server, and lsof. Starts isolated loopback
processes and a temporary Restate database. The test copy alone changes the
hard-coded preflight and replaces python3 with a synthetic worker. Never use its
unsigned endpoint or modified build for production.
"""
import json
import os
from pathlib import Path
import shutil
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
    with urllib.request.urlopen(req, timeout=10) as response:
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
        root = Path(tmp)
        app, bindir = root / 'app', root / 'bin'
        app.mkdir(); bindir.mkdir(); (root / 'anchor').mkdir()
        (root / 'db').touch(); (root / 'anchor' / 'file').touch()
        shutil.copytree(SPINE / 'dist', app / 'dist')
        shutil.copy(SPINE / 'package.json', app / 'package.json')
        (app / 'node_modules').symlink_to(SPINE / 'node_modules', target_is_directory=True)
        build = app / 'dist' / 'build.js'
        source = build.read_text()
        before = 'export function successorPreflight() {\n    return false;\n}'
        assert source.count(before) == 1, 'production preflight changed; review probe before proceeding'
        build.write_text(source.replace(before, before.replace('false', 'true')))
        (app / 'server.mjs').write_text("""import * as restate from '@restatedev/restate-sdk';
import {createServer} from 'node:http2';
import {workUnit} from './dist/state.js';
import {orchestrate,reconcile,reconciliation} from './dist/orchestrate.js';
createServer(restate.createEndpointHandler({services:[workUnit,orchestrate,reconcile,reconciliation]})).listen(Number(process.env.TEST_PORT),'127.0.0.1');
""")
        cid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'stapled-factory:unit:0'))
        successor = str(uuid.uuid5(uuid.NAMESPACE_URL, 'stapled-factory:unit:0:successor'))
        fake = bindir / 'python3'
        fake.write_text(f'''#!/bin/sh
for arg in "$@"; do
  case "$arg" in
    --successor-only) printf 'successor\\n' >> "$TEST_CAPTURE"; printf '{{"status":"completed","git_revision":"{'b' * 40}","conversation_id":"{successor}"}}\\n'; exit;;
    --reconcile-only) printf '{{"status":"blocked","git_revision":""}}\\n'; exit;;
  esac
done
printf '{{"status":"unknown","git_revision":"","conversation_id":"{cid}"}}\\n'
''')
        fake.chmod(0o700)
        env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'],
                   FACTORY_ATTEMPT_DB=str(root / 'db'), FACTORY_ATTEMPT_ANCHOR=str(root / 'anchor' / 'file'),
                   TEST_CAPTURE=str(root / 'calls'))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); env['TEST_PORT'] = str(sock.getsockname()[1])
        server = endpoint = None
        try:
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
                'scenarios':'/test', 'taskFile':'/task', 'targetRepo':'/repo'}) is None
            assert request(ingress + '/Reconciliation/unit/restart', {}) == 'needs_validation'
            state = request(ingress + '/WorkUnit/unit/getState', {})
            assert state['stage'] == 'needs_validation' and state['build']['status'] == 'unknown'
            assert state['successor']['attempt_id'] == 'unit:0:successor'
            assert state['successor']['conversation_id'] == successor
            assert state['successor']['git_revision'] == 'b' * 40
            assert (root / 'calls').read_text().splitlines() == ['successor']
            print('PASS: synthetic Restate successor transition; no runtime, budget or billing verification')
        finally:
            stop(endpoint); stop(server)


if __name__ == '__main__':
    main()
