import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_docker_official import inspect, INDEX, SOURCE_REV

INFO = f'docker pull python@{INDEX}\n### `python:3.11-slim` - linux; amd64\n' + 'docker pull python@sha256:' + 'a'*64 + '\nImage ID: `sha256:' + 'b'*64 + '`'
POLICY = f'Tags: 3.11.15-slim-trixie, 3.11-slim-trixie, 3.11.15-slim, 3.11-slim\nGitCommit: {SOURCE_REV}\nDirectory: 3.11/slim-trixie'


def test_exact_official_source_and_platform_mapping():
    assert inspect(INFO, POLICY)['publisher_signature_verified'] is False


@pytest.mark.parametrize('info,policy', [(INFO.replace(INDEX, 'sha256:'+'c'*64), POLICY),
    (INFO, POLICY.replace(SOURCE_REV, 'c'*40)), (INFO.replace('Image ID:', 'wrong:'), POLICY),
    (INFO.replace('amd64', 'arm64'), POLICY)])
def test_substitutions_fail_closed(info, policy):
    with pytest.raises(ValueError):
        inspect(info, policy)
