"""Operator-configured OpenHands coordinator.

The service operator owns the configuration and Docker daemon. The local $2
reservation is best effort; it does not cap provider charges. A compromised
operator or Docker daemon is outside this boundary.
"""
import copy
import json
import os
import stat
import subprocess
import time
from pathlib import Path

from docker_transport import DockerTransport
from guarded_store import GuardedAttemptStore
from openhands_adapter import OpenHandsAdapter
from pricing_policy import BUDGET_MICRODOLLARS, OUTPUT_TOKENS, POLICY_ID, request_limit, reservation_microdollars
from revision import verify_revision


def _private_file(path):
    if not isinstance(path, str) or not os.path.isabs(path):
        raise ValueError('absolute private file required')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('private file ownership or mode invalid')
        with os.fdopen(fd, 'rb') as stream:
            fd = -1
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError('private file too large')
        return data
    finally:
        if fd >= 0:
            os.close(fd)


def _option(command, name):
    if command.count(name) != 1:
        raise ValueError('proxy option missing or duplicated')
    i = command.index(name)
    if i + 1 >= len(command):
        raise ValueError('proxy option missing value')
    return command[i + 1]


def load_config(args):
    path = os.environ.get('FACTORY_RUNTIME_CONFIG', '')
    data = json.loads(_private_file(path))
    if not isinstance(data, dict) or set(data) - {'deployment_scope', 'image_approval'} != {'attempt_id', 'task_file', 'repo', 'baseline', 'successor_workspace', 'payload', 'provision'}:
        raise ValueError('invalid operator configuration')
    # Content IDs and container isolation do not establish publisher provenance
    # or mediate privileged tool effects. No production override exists here.
    if data.get('deployment_scope', 'sandbox') != 'sandbox':
        raise ValueError('production scope unavailable: tool mediation and image provenance unverified')
    if (data['attempt_id'] != args.attempt_id or data['task_file'] != args.task_file or
            data['repo'] != args.repo or data['provision'].get('workspace') != args.repo):
        raise ValueError('request differs from operator configuration')
    if os.environ.get('FACTORY_IMAGE_APPROVAL_KEY') and 'image_approval' not in data:
        raise ValueError('signed image approval required by operator')
    if 'image_approval' in data:
        from image_approval import verify_approval
        approval = data['image_approval']
        if not isinstance(approval, dict) or set(approval) != {'document', 'signature'}:
            raise ValueError('invalid image approval configuration')
        # Trust root comes from operator service environment, never the request.
        verify_approval(_private_file(approval['document']), _private_file(approval['signature']),
                        _private_file(os.environ.get('FACTORY_IMAGE_APPROVAL_KEY', '')), data['provision'])
    command = data['provision']['proxy_command']
    model = _option(command, '--model')
    if (_option(command, '--attempt-id') != args.attempt_id or
            _option(command, '--pricing-policy-id') != POLICY_ID or
            int(_option(command, '--ceiling-microdollars')) != BUDGET_MICRODOLLARS or
            int(_option(command, '--worst-case-request-microdollars')) < reservation_microdollars(model) or
            int(_option(command, '--worst-case-request-microdollars')) > BUDGET_MICRODOLLARS or
            not 1 <= int(_option(command, '--max-requests')) <= request_limit(model)):
        raise ValueError('proxy best-effort reservation policy invalid')
    payload = copy.deepcopy(data['payload'])
    llm = payload['agent_settings']['llm']
    if (llm['model'] != 'anthropic/' + model or llm['base_url'] != 'http://127.0.0.1:8123' or
            llm.get('api_key') != 'dummy' or not 1 <= llm['max_output_tokens'] <= OUTPUT_TOKENS):
        raise ValueError('agent model route or limit invalid')
    task = Path(args.task_file).read_text(encoding='utf-8')
    if not task or len(task.encode()) > 1024 * 1024:
        raise ValueError('task is empty or too large')
    payload['initial_message'] = {'role': 'user', 'content': [{'type': 'text', 'text': task}], 'run': True}
    return data, payload


def _credential():
    path = str(Path.home() / '.dsh' / '.credentials.yaml')
    # Validate using an already-open descriptor, then feed its contents directly
    # to a safe YAML parser; never expose the credential in argv or environment.
    raw = _private_file(path)
    result = subprocess.run(['ruby', '-ryaml', '-e',
        'x=YAML.safe_load(STDIN.read); k=x.fetch("refs").fetch("BIFROST_API_KEY"); abort unless k.is_a?(String); STDOUT.write(k)'],
        input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=10)
    if not result.stdout or b'\n' in result.stdout or b'\r' in result.stdout:
        raise ValueError('gateway credential unavailable')
    return result.stdout


def run(args):
    from provision_runtime import _check_baseline
    from worker import execute_bound_attempt, execute_bound_successor
    config, request = load_config(args)
    provision = config['provision']
    baseline = config['baseline']
    if args.successor_preflight_only:
        _check_baseline(config['successor_workspace'], baseline)
        if not Path(args.attempt_db).is_file() or not Path(args.attempt_anchor).is_file():
            raise ValueError('attempt store unavailable')
        return {'status': 'ready', 'git_revision': ''}
    store = GuardedAttemptStore(Path(args.attempt_db), Path(args.attempt_anchor))
    successor = args.successor_only or args.successor_reconcile_only
    workspace = config['successor_workspace'] if successor else config['repo']

    def adapter(container):
        # Readiness checks are read-only; never retry POST after uncertainty.
        for _ in range(60):
            result = subprocess.run(['docker', 'exec', container, 'python3', '-c',
                'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/openapi.json",timeout=2)'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('agent server unavailable')
        return OpenHandsAdapter('http://127.0.0.1:8000', 'dummy',
            lambda *_: verify_revision(workspace, baseline), transport=DockerTransport(container, 'dummy'))

    if successor:
        return execute_bound_successor(store, args.attempt_id, request, provision, adapter,
            workspace=workspace, baseline=baseline, reconcile_only=args.successor_reconcile_only)
    source_config = {**provision, 'workspace_baseline': baseline}
    if not args.reconcile_only:
        source_config['gateway_key'] = _credential()
    return execute_bound_attempt(store, args.attempt_id, request, source_config, adapter,
                                 reconcile_only=args.reconcile_only)
