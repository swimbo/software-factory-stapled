#!/usr/bin/env python3
"""Disposable paid Flash smoke; requires explicit --run-paid-smoke.

Uses the service account's DSH credential via worker.py. The $2 reservation is
best effort, not a charge guarantee. Creates and removes only unique resources.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'worker'))
from guarded_store import GuardedAttemptStore
from pricing_policy import POLICY_ID, reservation_microdollars


def run(args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, **kwargs).stdout.strip()


def main():
    if sys.argv[1:] != ['--run-paid-smoke']:
        raise SystemExit('Pass --run-paid-smoke to authorize an actual provider request.')
    root = Path(tempfile.mkdtemp(prefix='factory-live-cli-')).resolve()
    identity = uuid.uuid4().hex
    tags = {role: 'factory-live-cli-' + role + '-' + identity for role in ('worker', 'proxy', 'firewall')}
    allocation = None
    def docker(*args):
        return run(['docker', *args])
    def build(role, name, *args):
        docker('build', *args, '-f', str(ROOT/'worker'/name), '-t', tags[role], str(ROOT/'worker'))
        return docker('image', 'inspect', tags[role], '--format', '{{.Id}}')
    try:
        worker = build('worker', 'Dockerfile.agent', '--network', 'none', '--build-arg', 'WORKER_UID='+str(os.getuid()))
        proxy = build('proxy', 'Dockerfile.proxy', '--network', 'none')
        firewall = build('firewall', 'Dockerfile.firewall')
        helper = docker('image', 'inspect', 'python:3.11-slim', '--format', '{{.Id}}')
        worker_env = json.loads(docker('image', 'inspect', worker, '--format', '{{json .Config.Env}}'))
        workspace = root/'workspace'; workspace.mkdir(mode=0o700)
        run(['git', 'init', '-q', str(workspace)])
        for key, value in [('user.name', 'Factory Smoke'), ('user.email', 'factory-smoke@example.invalid')]:
            run(['git', '-C', str(workspace), 'config', key, value])
        run(['git', '-C', str(workspace), 'commit', '-q', '--allow-empty', '-m', 'baseline'])
        baseline = run(['git', '-C', str(workspace), 'rev-parse', 'HEAD'])
        successor = root/'successor'
        run(['git', 'clone', '--no-hardlinks', '-q', str(workspace), str(successor)])
        successor.chmod(0o700)
        task = root/'task'
        task.write_text('In /workspace create a file named smoke.txt containing exactly the line hello from the worker. Commit that file using git, then finish. Do not modify anything else.\n')
        (root/'state').mkdir(); (root/'anchor').mkdir()
        db, anchor = root/'state'/'attempt.db', root/'anchor'/'mark'
        GuardedAttemptStore.initialize(db, anchor)
        model = 'ollama-cloud/deepseek-v4.1-flash:1017'
        gateway_ip = socket.gethostbyname('hermes-control.tail4296e6.ts.net')
        attempt = 'live:' + identity
        provision = {'worker_image': worker, 'proxy_image': proxy, 'helper_image': helper,
            'worker_command': ['tini', '--', 'python', '/opt/factory/agent_entrypoint.py'],
            'proxy_command': ['python3', '/opt/factory/egress_proxy.py', '--socket', '/gateway/api.sock',
                '--model', model, '--gateway-ip', gateway_ip, '--max-requests', '6',
                '--budget-db', '/budget/b.db', '--budget-anchor', '/anchor/b.json', '--initialize-budget-store',
                '--attempt-id', attempt, '--attempt-fingerprint', identity*2, '--ceiling-microdollars', '2000000',
                '--worst-case-request-microdollars', str(reservation_microdollars(model)),
                '--pricing-policy-id', POLICY_ID, '--pricing-policy-expires-at', str(int(time.time())+3600)],
            'workspace': str(workspace), 'uid': os.getuid(), 'worker_environment': worker_env,
            'firewall': {'helper_image': firewall, 'gateway_ip': gateway_ip}}
        payload = {'agent_settings': {'llm': {'usage_id': attempt, 'model': 'anthropic/'+model,
            'base_url': 'http://127.0.0.1:8123', 'api_key': 'dummy', 'max_output_tokens': 512}},
            'workspace': {'working_dir': '/workspace'}, 'max_iterations': 6}
        config = {'attempt_id': attempt, 'task_file': str(task), 'repo': str(workspace), 'baseline': baseline,
                  'successor_workspace': str(successor), 'payload': payload, 'provision': provision}
        config_path = root/'config.json'; config_path.write_text(json.dumps(config)); config_path.chmod(0o600)
        env = dict(os.environ, FACTORY_RUNTIME_CONFIG=str(config_path))
        command = [sys.executable, str(ROOT/'worker'/'worker.py'), '--task-file', str(task), '--repo', str(workspace),
                   '--attempt-id', attempt, '--attempt-db', str(db), '--attempt-anchor', str(anchor)]
        result = json.loads(run(command, env=env, timeout=120))
        print('initial status:', result.get('status'), flush=True)
        for _ in range(40):
            if result.get('status') != 'unknown': break
            time.sleep(3)
            result = json.loads(run(command+['--reconcile-only'], env=env, timeout=45))
        print('terminal status:', result.get('status'), flush=True)
        with sqlite3.connect(db) as conn:
            row = conn.execute('select allocation_id from allocations where attempt_id=?', (attempt,)).fetchone()
        if row:
            allocation = row[0]
            try:
                ledger = docker('exec', 'factory-'+allocation+'-proxy', 'python3', '-c',
                    "import sqlite3; d=sqlite3.connect('/budget/b.db'); print(*d.execute('select used, spent from budgets join monetary_budgets using (attempt)').fetchone())")
                print('best-effort reservations (requests, microdollars):', ledger, flush=True)
            except subprocess.CalledProcessError:
                print('reservation ledger unavailable', flush=True)
        file = workspace/'smoke.txt'
        expected = file.exists() and file.read_text().strip() == 'hello from the worker'
        print('expected file:', expected, flush=True)
        print('verified commit:', result.get('status') == 'completed' and bool(result.get('git_revision')), flush=True)
        if result.get('status') != 'completed' or not expected:
            raise RuntimeError('live smoke did not complete')
        print('LIVE_SMOKE_PASS', flush=True)
    finally:
        if allocation is None:
            try:
                with sqlite3.connect(root/'state'/'attempt.db') as conn:
                    row = conn.execute('select allocation_id from allocations').fetchone()
                    allocation = row[0] if row else None
            except sqlite3.Error:
                pass
        if allocation and len(allocation) == 32 and all(c in '0123456789abcdef' for c in allocation):
            prefix = 'factory-'+allocation
            for role in ('successor', 'worker', 'proxy'):
                subprocess.run(['docker', 'rm', '-f', prefix+'-'+role], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['docker', 'network', 'rm', prefix+'-network'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for role in ('socket', 'budget', 'anchor'):
                subprocess.run(['docker', 'volume', 'rm', prefix+'-'+role], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for tag in tags.values():
            subprocess.run(['docker', 'image', 'rm', tag], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
