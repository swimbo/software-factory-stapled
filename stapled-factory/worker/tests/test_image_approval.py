import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from image_approval import verify_approval


def test_real_signed_approval_and_negative_controls(tmp_path):
    private, public = tmp_path / 'key', tmp_path / 'public'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', str(private)], check=True, capture_output=True)
    subprocess.run(['openssl', 'pkey', '-in', str(private), '-pubout', '-out', str(public)], check=True, capture_output=True)
    source = tmp_path / 'Dockerfile'
    source.write_text('FROM scratch\n')
    provision = {role: 'sha256:' + 'a' * 64 for role in ('worker_image', 'proxy_image', 'helper_image')}
    data = {'version': 1, 'scope': 'sandbox', 'expires_at': 200, 'images': provision,
            'build_inputs': {str(source): hashlib.sha256(source.read_bytes()).hexdigest()}}
    document = json.dumps(data).encode()
    path, sig = tmp_path / 'document', tmp_path / 'signature'
    path.write_bytes(document)
    subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(private), '-out', str(sig), str(path)], check=True)
    args = (document, sig.read_bytes(), public.read_bytes(), provision)
    assert verify_approval(*args, now=100)['scope'] == 'sandbox'
    with pytest.raises(ValueError, match='expiration'):
        verify_approval(*args, now=201)
    with pytest.raises(ValueError, match='signature'):
        verify_approval(document + b' ', *args[1:], now=100)
    changed = {**provision, 'worker_image': 'sha256:' + 'b' * 64}
    with pytest.raises(ValueError, match='identity'):
        verify_approval(*args[:3], changed, now=100)
    source.write_text('changed')
    with pytest.raises(ValueError, match='input changed'):
        verify_approval(*args, now=100)
