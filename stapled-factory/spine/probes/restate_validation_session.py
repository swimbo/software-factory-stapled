#!/usr/bin/env python3
"""Isolated real-Restate receipt acceptance and client-crash recovery proof.

Uses an explicitly fixture-seeded completed WorkUnit and a fresh fixture HMAC
key. The supplied private candidate checks are data; this probe does not prove
their provenance, run a target, build code, dispatch a worker, or activate a
runtime. Its unsigned test endpoint binds loopback and exposes only WorkUnit.
Never use that endpoint wrapper for a deployed factory.

Requires a locally built spine, its locked node_modules, restate-server and
lsof. --output must not exist; all evidence and Restate data are retained there.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

SPINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPINE))
from prebuilt_validation import record
from validation_session import canonical, evidence, validate_binding, validate_session


def request(url, value):
    req = urllib.request.Request(url, json.dumps(value).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def stop(child, *, crash=False):
    if child is not None and child.poll() is None:
        os.killpg(child.pid, signal.SIGKILL if crash else signal.SIGTERM)
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


def wait_for(predicate, description, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.1)
    raise TimeoutError(description)


def discover(server):
    def health():
        if server.poll() is not None:
            raise RuntimeError('owned Restate exited; inspect retained logs')
        children = subprocess.run(['pgrep', '-P', str(server.pid)],
                                  capture_output=True, text=True, timeout=5).stdout.split()
        for pid in [str(server.pid), *children]:
            listeners = subprocess.run(['lsof', '-nP', '-a', '-p', pid,
                '-iTCP', '-sTCP:LISTEN', '-Fn'], capture_output=True, text=True, timeout=5).stdout
            for line in listeners.splitlines():
                if not line.startswith('n127.0.0.1:'):
                    continue
                try:
                    with urllib.request.urlopen('http://' + line[1:] + '/health', timeout=1) as response:
                        result = json.load(response)
                    if result.get('admin_status') == 'Ready':
                        addresses = {a['name']: 'http://' + a['address']
                                     for a in result['bound_addresses'] if a['kind'] == 'tcp'}
                        return addresses['admin-api-server'], addresses['http-ingress-server']
                except (OSError, ValueError, KeyError):
                    pass
        return None
    return wait_for(health, 'owned Restate did not become ready')


def run(args):
    if sys.flags.optimize:
        raise ValueError('enabled Python assertions required')
    checks = json.loads(args.checks.read_text())
    manifest_raw = args.build_manifest.read_bytes()
    manifest = json.loads(manifest_raw)
    unit = 'receipt-probe-' + uuid.uuid4().hex
    binding = dict(unit_id=unit, attempt_id=unit + ':0', git_revision=checks['git_revision'],
        baseline=args.baseline, artifact_digest=checks['artifact_digest'],
        source_archive_digest=checks['source_archive_digest'], scenario_digest=checks['scenario_digest'],
        build_manifest_digest=hashlib.sha256(manifest_raw).hexdigest())
    validate_binding(binding)
    if (manifest.get('revision') != binding['git_revision'] or manifest.get('baseline') != args.baseline or
            manifest.get('archive_sha256') != binding['source_archive_digest']):
        raise ValueError('candidate checks and build manifest differ')
    proof = evidence(checks, binding)
    if not proof['passed']:
        raise ValueError('passing candidate checks required')
    if not (SPINE / 'dist/state.js').is_file() or not (SPINE / 'node_modules/@restatedev/restate-sdk').is_dir():
        raise ValueError('build spine and locked dependencies locally first')
    # Do not resolve a pre-existing final symlink before exclusive mkdir.
    root = args.output.absolute()
    root.mkdir(mode=0o700)
    if root.stat().st_uid != os.getuid():
        raise ValueError('probe output must be operator owned')
    key = secrets.token_bytes(32)
    key_path = root / 'fixture-validation.key'
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(key)
        stream.flush()
        os.fsync(stream.fileno())
    record(root / 'intent.json', {'fixture_unit': unit, 'binding': binding,
        'checks_sha256': hashlib.sha256(args.checks.read_bytes()).hexdigest(),
        'compiled_state_sha256': hashlib.sha256((SPINE / 'dist/state.js').read_bytes()).hexdigest(),
        'compiled_validation_sha256': hashlib.sha256((SPINE / 'dist/validation.js').read_bytes()).hexdigest(),
        'scope': __doc__})
    record(root / 'input-checks.json', checks)
    app = root / 'app';app.mkdir(mode=0o700)
    shutil.copytree(SPINE / 'dist', app / 'dist')
    shutil.copyfile(SPINE / 'package.json', app / 'package.json')
    (app / 'node_modules').symlink_to(SPINE / 'node_modules', target_is_directory=True)
    (app / 'server.mjs').write_text("""import * as restate from '@restatedev/restate-sdk';
import {createServer} from 'node:http2';
import {workUnit} from './dist/state.js';
createServer(restate.createEndpointHandler({services:[workUnit]})).listen(Number(process.env.PROBE_PORT),'127.0.0.1');
""")
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0));port = sock.getsockname()[1]
    env = {name: value for name, value in os.environ.items() if not name.startswith('FACTORY_')}
    env.update(FACTORY_VALIDATION_KEY_FILE=str(key_path), PROBE_PORT=str(port))
    server = endpoint = None
    child_pid = None
    with (root / 'restate.log').open('xb') as restate_log, (root / 'endpoint.log').open('xb') as endpoint_log:
        def start_endpoint():
            process = subprocess.Popen(['node', str(app / 'server.mjs')], env=env,
                stdout=endpoint_log, stderr=subprocess.STDOUT, start_new_session=True)
            def listening():
                if process.poll() is not None:
                    raise RuntimeError('owned fixture endpoint exited; inspect retained logs')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        return True
                except OSError:
                    return False
            try:
                wait_for(listening, 'owned fixture endpoint did not listen')
            except BaseException:
                stop(process)
                raise
            return process
        try:
            server = subprocess.Popen(['restate-server', '--base-dir', str(root / 'data'),
                '--use-random-ports', 'true', '--bind-ip', '127.0.0.1', '--listen-mode', 'tcp', '--no-logo'],
                stdout=restate_log, stderr=subprocess.STDOUT, start_new_session=True)
            admin, ingress = discover(server)
            endpoint = start_endpoint()
            record(root / 'owned-processes.json', {'restate_pid': server.pid,
                'endpoint_pid': endpoint.pid, 'admin': admin, 'ingress': ingress, 'endpoint_port': port})
            request(admin + '/deployments', {'uri': 'http://127.0.0.1:' + str(port)})
            def call(method, value):
                return request(ingress + '/WorkUnit/' + unit + '/' + method, value)
            assert call('claim', 'isolated-receipt-probe') is True
            call('transition', 'building')
            call('recordRecoveryContext', {'request': {'unitId': unit}, 'attemptDb': str(root / 'unused-attempts.db')})
            call('recordBuild', {'attempt_id': binding['attempt_id'], 'status': 'completed',
                                 'git_revision': binding['git_revision']})
            call('transition', 'needs_validation')
            def sign(value):
                payload = canonical(value)
                return {'payload': payload, 'signature': hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()}
            for field, replacement in [('unit_id', 'other'), ('attempt_id', unit + ':0:successor'),
                                        ('git_revision', '0' * 40)]:
                stale = {**proof, field: replacement}
                if field == 'git_revision':
                    stale['receipt'] = {**proof['receipt'], 'git_revision': replacement}
                assert call('acceptValidation', sign(stale)) is False
            tampered = sign(proof)
            changed_id = ('0' if proof['deployment_id'][0] != '0' else '1') + proof['deployment_id'][1:]
            tampered['payload'] = canonical({**proof, 'deployment_id': changed_id})
            tamper_started = time.monotonic()
            try:
                call('acceptValidation', tampered)
            except urllib.error.HTTPError as error:
                rejection = json.loads(error.read())
                if (error.code != 500 or rejection.get('message') !=
                        'Validation authority or signed evidence invalid' or
                        time.monotonic() - tamper_started >= 10):
                    raise AssertionError('tamper did not reach the expected terminal signature rejection')
                record(root / 'tamper-rejection.json', {'http_status': error.code, 'response': rejection})
            else:
                raise AssertionError('tampered signature accepted')
            assert call('getState', None)['stage'] == 'needs_validation'
            journal = root / 'validation-session'
            child_pid = os.fork()
            if child_pid == 0:
                try:
                    def accept_then_crash(envelope):
                        record(root / 'sole-submit-intent.json', {'unit': unit})
                        if call('acceptValidation', envelope) is not True:
                            raise AssertionError('original signed acceptance refused')
                        record(root / 'acceptance-committed-before-client-crash.json', {'unit': unit})
                        os._exit(0)  # No accepted.json or local acknowledgment can be written.
                    validate_session(journal, binding, lambda: call('getState', None),
                        lambda: checks, accept_then_crash, key)
                    os._exit(3)
                except BaseException as error:
                    record(root / 'client-error.json', {'error_class': type(error).__name__})
                    os._exit(2)
            def child_exited():
                return os.waitpid(child_pid, os.WNOHANG)
            end = time.monotonic() + 30
            while True:
                pid, status = child_exited()
                if pid:
                    child_pid = None
                    assert os.waitstatus_to_exitcode(status) == 0, 'client failed before intended crash'
                    break
                if time.monotonic() >= end:
                    raise TimeoutError('owned acceptance client did not exit')
                time.sleep(.1)
            assert (journal / 'submission.json').is_file() and not (journal / 'accepted.json').exists()
            stop(endpoint, crash=True);endpoint = None
            endpoint = start_endpoint()
            record(root / 'restarted-endpoint.json', {'pid': endpoint.pid, 'port': port})
            def forbidden(*unused):
                raise AssertionError('observation must not reevaluate or resubmit')
            accepted = validate_session(journal, binding, lambda: call('getState', None),
                forbidden, forbidden, key, observe_only=True)
            assert validate_session(journal, binding, lambda: call('getState', None),
                forbidden, forbidden, key, observe_only=True) == accepted
            state = call('getState', None)
            assert state['stage'] == 'validated' and state['validation'] == proof and state['receipt'] == proof['receipt']
            assert call('acceptValidation', sign(proof)) is False
            record(root / 'result.json', {'passed': True, 'fixture_unit': unit,
                'real_restate': True, 'signed_receipt_stored': True, 'stale_bindings_rejected': True,
                'tamper_rejected': True, 'client_crash_after_acceptance': True,
                'endpoint_crash_recovery': True, 'original_journal_reconciled_without_resubmit': True,
                'repeated_observation_unchanged': True, 'duplicate_acceptance_rejected': True,
                'model_calls': 0, 'factory_dispatches': 0, 'production_runtime_activated': False,
                'boundary': 'Fixture-seeded WorkUnit and fresh fixture key on isolated unsigned loopback endpoint; checks provenance and actual build/deployment are not established by this probe.'})
            print(json.dumps({'passed': True, 'evidence': str(root), 'fixture_unit': unit,
                              'model_calls': 0, 'factory_dispatches': 0}), flush=True)
        finally:
            if child_pid is not None:
                os.kill(child_pid, signal.SIGKILL)
                os.waitpid(child_pid, 0)
            stop(endpoint);stop(server)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checks', type=Path, required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--build-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    run(args)


if __name__ == '__main__':
    main()
