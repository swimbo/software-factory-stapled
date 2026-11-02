"""Verify downloaded npm material bytes without trusting their filenames or metadata."""
import base64
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from npm_materials import verify_materials


def fixture(tmp_path):
    content = b'fixture package archive bytes'
    digest = hashlib.sha512(content).digest()
    integrity = 'sha512-' + base64.b64encode(digest).decode()
    lock = {'lockfileVersion': 3, 'packages': {'': {'name': 'app'},
        'node_modules/example': {'version': '1.0.0', 'resolved': 'https://registry.npmjs.org/example/-/example-1.0.0.tgz', 'integrity': integrity}}}
    store = tmp_path / 'materials'; store.mkdir(mode=0o700)
    (store / digest.hex()).write_bytes(content)
    return lock, store, digest.hex()


def test_verified_materials_bind_raw_bytes_to_lock_integrity(tmp_path):
    lock, store, digest = fixture(tmp_path)
    report = verify_materials(json.dumps(lock).encode(), store)
    assert report['status'] == 'verified-material-bytes-only'
    assert report['packages'] == [{'path': 'node_modules/example', 'version': '1.0.0', 'sha512': digest}]
    assert report['coverage'] == 'incomplete'


def test_changed_or_missing_material_is_rejected(tmp_path):
    lock, store, digest = fixture(tmp_path)
    (store / digest).write_bytes(b'changed bytes')
    with pytest.raises(ValueError, match='digest'):
        verify_materials(json.dumps(lock).encode(), store)
    (store / digest).unlink()
    with pytest.raises(ValueError, match='missing'):
        verify_materials(json.dumps(lock).encode(), store)


def test_symlink_material_is_rejected(tmp_path):
    lock, store, digest = fixture(tmp_path)
    (store / digest).unlink()
    (store / digest).symlink_to(tmp_path / 'elsewhere')
    with pytest.raises(ValueError, match='material'):
        verify_materials(json.dumps(lock).encode(), store)


@pytest.mark.parametrize('change', ['missing-integrity', 'wrong-source', 'link', 'unsupported-lock', 'duplicate-key'])
def test_unverifiable_lock_entries_are_rejected(tmp_path, change):
    lock, store, _ = fixture(tmp_path)
    entry = lock['packages']['node_modules/example']
    if change == 'missing-integrity': entry.pop('integrity')
    if change == 'wrong-source': entry['resolved'] = 'https://evil.invalid/example.tgz'
    if change == 'link': entry['link'] = True
    if change == 'unsupported-lock': lock['lockfileVersion'] = 1
    raw = json.dumps(lock).encode()
    if change == 'duplicate-key': raw = b'{"lockfileVersion":3,"lockfileVersion":3,"packages":{}}'
    with pytest.raises(ValueError): verify_materials(raw, store)


def test_fifo_material_is_rejected_without_blocking(tmp_path):
    import os
    lock, store, digest = fixture(tmp_path)
    (store / digest).unlink()
    os.mkfifo(store / digest)
    import subprocess
    lockfile = tmp_path / 'input.lock'; lockfile.write_bytes(json.dumps(lock).encode())
    code = """import sys
from npm_materials import verify_materials
try:
    verify_materials(open(sys.argv[1], 'rb').read(), sys.argv[2])
except ValueError as e:
    assert 'material' in str(e)
else:
    raise AssertionError('FIFO was accepted')
"""
    env = dict(__import__('os').environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    subprocess.run([sys.executable, '-c', code, str(lockfile), str(store)],
                   env=env, check=True, timeout=5)


def test_noncanonical_package_path_is_rejected(tmp_path):
    lock, store, _ = fixture(tmp_path)
    package = lock['packages'].pop('node_modules/example')
    lock['packages']['node_modules/./example'] = package
    with pytest.raises(ValueError, match='entry'):
        verify_materials(json.dumps(lock).encode(), store)


def test_registry_signature_binds_verified_bytes_and_identity(tmp_path):
    import subprocess
    lock, store, digest = fixture(tmp_path)
    private = tmp_path / 'key.pem'; public = tmp_path / 'key.der'; sig = tmp_path / 'sig.der'
    subprocess.run(['openssl', 'ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out', str(private)], check=True, capture_output=True)
    subprocess.run(['openssl', 'pkey', '-in', str(private), '-pubout', '-outform', 'DER', '-out', str(public)], check=True, capture_output=True)
    der = public.read_bytes()
    def field(value): return len(value).to_bytes(4, 'big') + value
    ssh = field(b'ecdsa-sha2-nistp256') + field(b'nistp256') + field(der[-65:])
    keyid = 'SHA256:' + base64.b64encode(hashlib.sha256(ssh).digest()).decode().rstrip('=')
    entry = lock['packages']['node_modules/example']
    message = f"example@{entry['version']}:{entry['integrity']}".encode()
    subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(private), '-out', str(sig)], input=message, check=True, capture_output=True)
    evidence = {'node_modules/example': {'keyid': keyid, 'sig': base64.b64encode(sig.read_bytes()).decode()}}
    report = verify_materials(json.dumps(lock).encode(), store, registry_evidence=evidence, registry_keys={keyid: der})
    assert report['coverage'] == 'incomplete'
    assert report['packages'][0]['registry_signature'] == 'verified-with-pinned-key'
    entry['version'] = '1.0.1'
    with pytest.raises(ValueError, match='signature'):
        verify_materials(json.dumps(lock).encode(), store, registry_evidence=evidence, registry_keys={keyid: der})


def test_published_npm_signature_uses_registry_key_fingerprint():
    from npm_materials import _verify_registry_signature
    from npm_registry_policy import decode_policy
    keyid = 'SHA256:jl3bwswu80PjjokCgh0o2w5c2U4LhQAE57gj9cz1kzA'
    key = base64.b64decode('MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE1Olb3zMAFFxXKHiIkQO5cJ3Yhl5i6UPp+IhuteBJbuHcA5UogKo0EWtlWwW6KSaKoTNEYL7JlCQiVnkhBktUgg==')
    integrity = 'sha512-zRpUiDwd/xk6ADqPMATG8vc9VPrkck7T07OIx0gnjmJAnHnTVXNQG3vfvWNuiZIkwu9KrKdA1iJKfsfTVxE6NA=='
    signature = 'MEYCIQCxn2UBpu2wlAmln1ChWuYUBMnxSn2adhrYCIB90FGjzAIhAIRdBfoB5qhFGwn6GbSAKAqFI/JmBKVwDFMWXDRj8KeY'
    assert decode_policy({keyid: base64.b64encode(key).decode()}) == {keyid: key}
    with pytest.raises(ValueError, match='fingerprint'):
        decode_policy({'SHA256:wrong': base64.b64encode(key).decode()})
    _verify_registry_signature('node_modules/buffer-equal-constant-time', '1.0.1', integrity,
                               {'keyid': keyid, 'sig': signature}, {keyid: key})
