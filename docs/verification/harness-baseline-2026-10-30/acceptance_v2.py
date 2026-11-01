"""Operator-only development checks, contract-corrected v2. Keep outside each generator workspace.

Usage: python check.py TASK_ID CANDIDATE_DIRECTORY
Never use on unreviewed code on a trusted host; run in a disposable sandbox.
"""
import json
from pathlib import Path
import subprocess
import sys


def check(task, candidate):
    root = Path(candidate)
    if task in ('discovery', 'noisy-output', 'stalled-run'):
        filename, expected = {
            'discovery': ('discovery.json', {'version_source': 'catalog.VERSION', 'entrypoint': 'app.py'}),
            'noisy-output': ('noisy-result.json', {'target_index': 6789}),
            'stalled-run': ('blocker.json', {'status': 'blocked', 'dependency': 'factory_missing_dependency'}),
        }[task]
        actual = json.loads((root/filename).read_text())
        return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())
    assertions = {
        'small-edit': "from catalog import normalize; assert normalize(' A b ') == 'a b'; assert normalize('') == ''",
        'multi-file': "import catalog,app; assert catalog.VERSION == '0.2.0'; assert app.version() == {'version':'0.2.0'}",
        'failing-tests': "from catalog import total; assert total([])==0; assert total([2,3])==5; assert total([-2,2])==0",
    }
    if task not in assertions:
        raise ValueError('unknown development task')
    return subprocess.run([sys.executable, '-B', '-c', assertions[task]], cwd=root,
                          timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


if __name__ == '__main__':
    sys.exit(0 if check(sys.argv[1], sys.argv[2]) else 1)
