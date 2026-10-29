"""The NUC signing authority must not expose the older unattested build path."""
from pathlib import Path
import subprocess
import sys


def test_legacy_invocation_rejected_before_key_or_docker_access(tmp_path):
    script = Path(__file__).with_name('validate.py')
    args = [sys.executable, str(script)]
    for option in ('repo', 'revision', 'baseline', 'scenarios', 'entrypoint', 'runtime-image', 'unit', 'attempt'):
        args.extend(['--' + option, 'unused'])
    args.extend(['--key-file', str(tmp_path / 'missing-key')])
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 2
    assert '--prebuilt-image' in result.stderr
    assert '--build-signature' in result.stderr
    assert 'No such file' not in result.stderr
