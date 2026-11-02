import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from controlled_cargo import freeze_cargo, verify_frozen_cargo


def fixture(tmp_path):
    materials = tmp_path / 'materials'; materials.mkdir(mode=0o700)
    vendor = tmp_path / 'vendor'; crate = vendor / 'crate-a'; (crate / 'src').mkdir(parents=True)
    data = b'pub fn value() -> u8 { 42 }\n'
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        item = tarfile.TarInfo('crate-a-1.0.0/src/lib.rs'); item.size = len(data); item.mode = 0o644
        tar.addfile(item, io.BytesIO(data))
    material = archive.getvalue(); digest = hashlib.sha256(material).hexdigest()
    (materials / digest).write_bytes(material)
    (crate / 'src/lib.rs').write_bytes(data)
    (crate / '.cargo-checksum.json').write_text(json.dumps(
        {'package': digest, 'files': {'src/lib.rs': hashlib.sha256(data).hexdigest()}}))
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
    return lock, materials, vendor, digest


def test_frozen_tar_binds_verified_vendor_and_original_archive(tmp_path):
    lock, materials, vendor, digest = fixture(tmp_path)
    out = tmp_path / 'out'
    report = freeze_cargo(lock, materials, vendor, out)
    path = out / 'cargo-inputs.tar'
    assert report['coverage'] == 'incomplete'
    assert report['archive_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report['lock_sha256'] == hashlib.sha256(lock).hexdigest()
    assert report['packages'][0]['sha256'] == digest
    assert report['local_packages'] == [{'name': 'app', 'version': '0.1.0'}]
    (vendor / 'crate-a/src/lib.rs').write_text('changed after freezing')
    with tarfile.open(path) as tar:
        assert tar.extractfile('vendor/crate-a/src/lib.rs').read() == b'pub fn value() -> u8 { 42 }\n'
        assert hashlib.sha256(tar.extractfile('materials/' + digest).read()).hexdigest() == digest
    with pytest.raises(FileExistsError):
        freeze_cargo(lock, materials, vendor, out)


def test_changed_vendor_with_forged_checksums_is_rejected(tmp_path):
    lock, materials, vendor, digest = fixture(tmp_path)
    source = vendor / 'crate-a/src/lib.rs'; source.write_bytes(b'changed')
    (vendor / 'crate-a/.cargo-checksum.json').write_text(json.dumps(
        {'package': digest, 'files': {'src/lib.rs': hashlib.sha256(b'changed').hexdigest()}}))
    with pytest.raises(ValueError, match='differ'):
        freeze_cargo(lock, materials, vendor, tmp_path / 'out')


def test_missing_and_symlink_inputs_are_rejected(tmp_path):
    lock, materials, vendor, digest = fixture(tmp_path)
    (vendor / 'crate-a/src/lib.rs').unlink()
    (vendor / 'crate-a/src/lib.rs').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='snapshot file'):
        freeze_cargo(lock, materials, vendor, tmp_path / 'out')
    (materials / digest).unlink()
    with pytest.raises(ValueError, match='missing'):
        freeze_cargo(lock, materials, vendor, tmp_path / 'out2')


def test_independent_verifier_rejects_forged_vendor_tar(tmp_path):
    lock, materials, vendor, digest = fixture(tmp_path)
    original = freeze_cargo(lock, materials, vendor, tmp_path / 'out')
    path = Path(original['archive'])
    assert verify_frozen_cargo(path, original['archive_sha256']) == original
    with pytest.raises(ValueError, match='digest differs'):
        verify_frozen_cargo(path, '0' * 64)
    altered = tmp_path / 'altered.tar'
    changed = b'altered source despite forged metadata'
    with tarfile.open(path, 'r:') as source, tarfile.open(altered, 'w') as out:
        for item in source:
            data = source.extractfile(item).read() if item.isfile() else None
            if item.name == 'vendor/crate-a/src/lib.rs':
                data = changed
            if item.name == 'vendor/crate-a/.cargo-checksum.json':
                data = json.dumps({'package': digest, 'files': {
                    'src/lib.rs': hashlib.sha256(changed).hexdigest()}}).encode()
            if data is not None:
                item.size = len(data)
            out.addfile(item, io.BytesIO(data) if data is not None else None)
    with pytest.raises(ValueError, match='differ'):
        verify_frozen_cargo(altered, hashlib.sha256(altered.read_bytes()).hexdigest())
