"""Cargo registry materials are checked against the lock, never trusted by name."""
import hashlib
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cargo_materials import verify_materials


def fixture(tmp_path):
    contents = b'crate archive fixture'
    digest = hashlib.sha256(contents).hexdigest()
    lock = f'''version = 4
[[package]]
name = "app"
version = "0.1.0"
[[package]]
name = "crate-a"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "{digest}"
'''.encode()
    store = tmp_path / 'materials';store.mkdir(mode=0o700)
    (store / digest).write_bytes(contents)
    return lock, store, digest


def test_registry_material_and_local_source_are_distinguished(tmp_path):
    lock, store, digest = fixture(tmp_path)
    report = verify_materials(lock, store)
    assert report['coverage'] == 'incomplete'
    assert report['packages'] == [{'name': 'crate-a', 'version': '1.0.0', 'sha256': digest}]
    assert report['local_packages'] == [{'name': 'app', 'version': '0.1.0'}]


def test_changed_missing_and_symlink_materials_reject(tmp_path):
    lock, store, digest = fixture(tmp_path)
    target = store / digest
    target.write_bytes(b'changed')
    with pytest.raises(ValueError, match='digest'):verify_materials(lock, store)
    target.unlink()
    with pytest.raises(ValueError, match='missing'):verify_materials(lock, store)
    target.symlink_to(tmp_path / 'missing')
    with pytest.raises(ValueError, match='material'):verify_materials(lock, store)


@pytest.mark.parametrize('replacement', [
    b'registry+https://unknown.invalid/index', b'git+https://example.invalid/repo',
    b'registry+https://github.com/rust-lang/crates.io-index?token=secret'])
def test_unsupported_source_rejected(tmp_path, replacement):
    lock, store, _ = fixture(tmp_path)
    lock = lock.replace(b'registry+https://github.com/rust-lang/crates.io-index', replacement)
    with pytest.raises(ValueError, match='source'):verify_materials(lock, store)


def test_fifo_material_does_not_block(tmp_path):
    lock, store, digest = fixture(tmp_path)
    (store / digest).unlink();os.mkfifo(store / digest)
    import subprocess
    lockfile = tmp_path / 'input.lock'; lockfile.write_bytes(lock)
    code = """import sys
from cargo_materials import verify_materials
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
