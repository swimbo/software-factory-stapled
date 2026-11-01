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
