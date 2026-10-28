#!/usr/bin/env python3
"""Verify an exact clean revision locally; retain a machine-readable receipt."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
def output(args):
    return subprocess.check_output(args, cwd=root, text=True).strip()
revision = output(['git', 'rev-parse', 'HEAD'])
receipt_dir = root / '.local-ci' / revision
receipt_dir.mkdir(parents=True, exist_ok=True)
receipt = {'revision': revision, 'tree': output(['git', 'rev-parse', 'HEAD^{tree}']),
           'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'runner': 'local', 'steps': [], 'passed': False}
steps = [
    ('secrets', ['gitleaks', 'dir', '.', '--redact', '--no-banner'], '.'),
    ('python', ['python3', '-m', 'pytest', 'worker/tests', 'security/tests', 'wall/tests', 'wall/isolation', 'spine/python_tests', 'deployment/nuc', '-q'], 'stapled-factory'),
    ('dependencies', ['npm', 'ci', '--ignore-scripts'], 'stapled-factory/spine'),
    ('audit', ['npm', 'audit', '--audit-level=high'], 'stapled-factory/spine'),
    ('build', ['npm', 'run', 'build'], 'stapled-factory/spine'),
    ('node', ['node', '--test', 'tests/acceptance.test.mjs'], 'stapled-factory/spine'),
]
try:
    with tempfile.TemporaryDirectory(prefix='stapled-local-ci-') as temp:
        checkout = Path(temp) / 'source'
        subprocess.run(['git', 'clone', '--quiet', '--no-hardlinks', str(root), str(checkout)], check=True)
        subprocess.run(['git', 'checkout', '--quiet', '--detach', revision], cwd=checkout, check=True)
        steps[-1] = ('node', ['node', '--test', *[str(p.relative_to(checkout / 'stapled-factory/spine')) for p in sorted((checkout / 'stapled-factory/spine/tests').glob('*.test.mjs'))]], 'stapled-factory/spine')
        for name, command, directory in steps:
            print(f'Running {name}', flush=True)
            log = receipt_dir / f'{name}.log'
            with log.open('wb') as stream:
                result = subprocess.run(command, cwd=checkout / directory, stdout=stream, stderr=subprocess.STDOUT)
            receipt['steps'].append({'name': name, 'command': command, 'exit_code': result.returncode,
                                     'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest()})
            if result.returncode:
                raise SystemExit(f'{name} failed; see {log}')
        receipt['passed'] = True
finally:
    receipt['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    (receipt_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(f'Passed {revision}; receipt: {receipt_dir / "receipt.json"}')
