import json
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_upstream_image import verify

IMAGE = 'ghcr.io/example/runtime@sha256:' + 'a' * 64


def test_verified_digest_and_exact_publisher_policy():
    def runner(command, **kwargs):
        assert command[command.index('--certificate-identity') + 1] == 'publisher'
        assert command[command.index('--certificate-oidc-issuer') + 1] == 'issuer'
        return subprocess.CompletedProcess(command, 0, json.dumps([{'critical': {'image': {'docker-manifest-digest': 'sha256:' + 'a' * 64}}}]))
    assert verify(IMAGE, 'publisher', 'issuer', runner=runner)['build_provenance_verified'] is False


@pytest.mark.parametrize('output', ['[]', '[{}]'])
def test_empty_or_wrong_digest_rejected(output):
    with pytest.raises(ValueError):
        verify(IMAGE, 'publisher', 'issuer', runner=lambda *a, **k: subprocess.CompletedProcess(a, 0, output))


def test_unsigned_unavailable_or_bad_policy_fail_closed():
    def unavailable(*a, **k):
        raise subprocess.CalledProcessError(1, a)
    with pytest.raises(subprocess.CalledProcessError):
        verify(IMAGE, 'publisher', 'issuer', runner=unavailable)
    with pytest.raises(ValueError):
        verify('runtime:latest', 'publisher', 'issuer')
    with pytest.raises(ValueError):
        verify(IMAGE, '', '')
