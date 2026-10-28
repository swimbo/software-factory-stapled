"""Verify operator-signed, expiring image approvals before credential access.

The public key must be provisioned independently by the operator. A signature
attests operator review, not upstream publisher identity or reproducibility.
"""
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path


def verify_approval(document, signature, public_key, provision, *, now=None):
    if len(document) > 65536 or len(signature) > 8192:
        raise ValueError('image approval too large')
    with tempfile.TemporaryDirectory(prefix='factory-approval-') as directory:
        root = Path(directory)
        for name, value in [('document', document), ('signature', signature), ('public.pem', public_key)]:
            (root / name).write_bytes(value)
        result = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(root / 'public.pem'),
            '-signature', str(root / 'signature'), str(root / 'document')],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if result.returncode:
            raise ValueError('image approval signature invalid')
    data = json.loads(document)
    current = time.time() if now is None else now
    if (set(data) != {'version', 'scope', 'expires_at', 'images', 'build_inputs'} or
            data['version'] != 1 or data['scope'] != 'sandbox' or
            type(data['expires_at']) is not int or data['expires_at'] <= current):
        raise ValueError('image approval scope or expiration invalid')
    expected = {role: provision[role] for role in ('worker_image', 'proxy_image', 'helper_image')}
    if provision.get('firewall'):
        expected['firewall_image'] = provision['firewall']['helper_image']
    if data['images'] != expected or any(not re.fullmatch(r'sha256:[0-9a-f]{64}', x) for x in expected.values()):
        raise ValueError('image approval identity mismatch')
    inputs = data['build_inputs']
    if not isinstance(inputs, dict) or not inputs:
        raise ValueError('image approval build inputs missing')
    # Compare the reviewed build inputs with local content. Paths come only from
    # the independently signed approval, never from the agent workspace.
    import hashlib
    for path, digest in inputs.items():
        if not isinstance(path, str) or not Path(path).is_absolute() or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('invalid image approval input')
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError('reviewed build input changed')
    return data
