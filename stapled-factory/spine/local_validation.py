"""Operator-only disposable validation adapter. Never accepts a caller target/receipt.

This verifies a local Docker artifact, not a production provider deployment.
The Docker daemon, entrypoint and runtime image are operator trust inputs.
"""
import hashlib
import io
import json
import re
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def validate(repo, revision, baseline, scenarios, entrypoint, runtime_image):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('exact commit required')
    if not re.fullmatch(r'[a-zA-Z0-9_./-]+\.py', entrypoint) or '..' in Path(entrypoint).parts:
        raise ValueError('invalid operator entrypoint')
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', runtime_image):
        raise ValueError('digest-pinned runtime required')
    actual = command('git', '-C', str(repo), 'rev-parse', revision + '^{commit}')
    if actual != revision:
        raise ValueError('revision mismatch')
    command('git', '-C', str(repo), 'merge-base', '--is-ancestor', baseline, revision)
    archive = subprocess.check_output(['git', '-C', str(repo), 'archive', revision])
    scenario_digest = hashlib.sha256(Path(scenarios).read_bytes()).hexdigest()
    container = None
    image = None
    with tempfile.TemporaryDirectory(prefix='trusted-validation-') as temp:
        folder = Path(temp)
        source = folder / 'source'
        source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(source, filter='data')
        if not (source / entrypoint).is_file():
            raise ValueError('entrypoint absent from selected commit')
        (folder / 'Dockerfile').write_text(f'FROM {runtime_image}\nWORKDIR /app\nCOPY source/ /app/\nUSER 65534:65534\nENTRYPOINT ["python3", "{entrypoint}"]\n')
        try:
            image = command('docker', 'build', '--quiet', str(folder)).splitlines()[-1]
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
                raise ValueError('invalid immutable artifact identity')
            container = command('docker', 'run', '-d', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=64', '--memory=128m', '--cpus=1', '-p', '127.0.0.1::8000', image)
            def identity():
                state = json.loads(command('docker', 'inspect', container))[0]
                if state['Id'] != container or state['Image'] != image or not state['State']['Running']:
                    raise ValueError('target identity changed')
                return state
            state = identity()
            port = state['NetworkSettings']['Ports']['8000/tcp'][0]['HostPort']
            target = 'http://127.0.0.1:' + port
            diff = command('git', '-C', str(repo), 'diff', baseline, revision)
            diff_file = folder / 'diff'
            diff_file.write_text(diff)
            security = json.loads(command('python3', str(ROOT / 'security/pipeline_cli.py'), '--diff-file', str(diff_file), '--target', str(source)))
            time.sleep(.5)
            wall = json.loads(command('python3', str(ROOT / 'wall/evaluator/cli.py'), '--target', target, '--scenarios', str(scenarios), '--runs', '3'))
            identity()
            if hashlib.sha256(Path(scenarios).read_bytes()).hexdigest() != scenario_digest:
                raise ValueError('scenario set changed')
            return dict(scope='trusted-local-only', git_revision=revision, artifact_digest=image, deployment_id=container, scenario_digest=scenario_digest, security=security, wall=wall, passed=security['verdict'] == 'pass' and wall['verdict']['passed'])
        finally:
            if container:
                subprocess.run(['docker', 'rm', '-f', container], check=False, stdout=subprocess.DEVNULL)
            if image:
                subprocess.run(['docker', 'image', 'rm', image], check=False, stdout=subprocess.DEVNULL)
