#!/usr/bin/env python3
"""Exercise a real server crash against the guarded attempt and adapter.

The networkless pinned server uses a local hanging mock model and a synthetic
revision verifier. This proves no redispatch by this controller path; it does
not establish completion, safe tool replay, production billing or isolation.
"""
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'probes'))
from crash_recovery import cmd, IMAGE, MOCK
from docker_transport import DockerTransport
from guarded_store import GuardedAttemptStore
from openhands_adapter import OpenHandsAdapter
from worker import execute_openhands_attempt


def main():
    name = 'factory-server-guard-probe-' + uuid.uuid4().hex[:12]
    created = False
    try:
        container_id = cmd('run', '-d', '--name', name, '--label', 'factory.probe=server-guard',
                           '--network', 'none', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                           '-e', 'OH_ENABLE_VSCODE=false', IMAGE, timeout=60).stdout.strip()
        created = True
        def inside(*args, input=None, check=True):
            return cmd('exec', '-i', container_id, *args, input=input, check=check)
        def ready():
            for _ in range(90):
                if inside('python', '-c', 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/openapi.json",timeout=2)', check=False).returncode == 0:
                    return
                time.sleep(1)
            raise RuntimeError('server did not become ready')
        ready()
        inside('python', '-c', 'import sys; open("/tmp/mock.py","w").write(sys.stdin.read())', input=MOCK)
        cmd('exec', '-d', container_id, 'python', '/tmp/mock.py')
        request = {'agent_settings': {'llm': {'usage_id': 'server-crash-probe', 'model': 'openai/gpt-4o',
                   'base_url': 'http://127.0.0.1:18080/v1', 'api_key': 'dummy', 'max_output_tokens': 128}},
                   'workspace': {'working_dir': '/tmp'},
                   'initial_message': {'role': 'user', 'content': [{'type': 'text', 'text': 'Say hello.'}], 'run': True},
                   'max_iterations': 3}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'anchor').mkdir()
            db, anchor = root / 'attempt.db', root / 'anchor' / 'watermark'
            store = GuardedAttemptStore.initialize(db, anchor)
            adapter = OpenHandsAdapter('http://127.0.0.1:8000', 'dummy', lambda *_: 'a' * 40,
                                       transport=DockerTransport(container_id, 'dummy'))
            initial = execute_openhands_attempt(store, 'server-crash', request, adapter)
            if initial['status'] != 'unknown':
                raise RuntimeError('initial request was not in flight')
            for _ in range(60):
                if inside('test', '-f', '/tmp/mock-arrived', check=False).returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError('mock model did not receive request')
            cmd('kill', '--signal', 'KILL', container_id)
            cmd('start', container_id)
            ready()
            for _ in range(3):
                # Reopen both storage and controller to model process loss.
                result = execute_openhands_attempt(GuardedAttemptStore(db, anchor), 'server-crash', request,
                                                   adapter, reconcile_only=True)
                if result['status'] != 'unknown' or result['conversation_id'] != initial['conversation_id']:
                    raise RuntimeError('unproven server crash produced a terminal outcome')
            count = int(inside('sh', '-c', 'wc -l < /tmp/mock-arrived').stdout.strip())
            if count != 1:
                raise RuntimeError('unexpected repeated model call')
            print('Real container crash: same attempt reconciled three times as unknown; model requests:', count)
    finally:
        if created:
            cmd('rm', '-f', name, check=False, timeout=60)

if __name__ == '__main__':
    main()
