"""Vendored files must match the original locked crate, even if metadata is forged."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cargo_vendor import verify_vendor


def fixture(tmp_path):
    archive = tmp_path/'crate.tar.gz';contents = b'pub fn value() -> u8 { 42 }\n'
    with tarfile.open(archive, 'w:gz') as tar:
        for name, data in [('crate-a-1.0.0/src/lib.rs', contents), ('crate-a-1.0.0/.gitignore', b'target/')]:
            item = tarfile.TarInfo(name);item.size = len(data);tar.addfile(item, io.BytesIO(data))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    vendor = tmp_path/'vendor';(vendor/'src').mkdir(parents=True)
    (vendor/'src/lib.rs').write_bytes(contents)
    metadata = {'package': digest, 'files': {'src/lib.rs': hashlib.sha256(contents).hexdigest()}}
    (vendor/'.cargo-checksum.json').write_text(json.dumps(metadata))
    return archive, vendor, digest


def test_vendor_matches_locked_archive_and_reports_omitted_metadata(tmp_path):
    archive, vendor, digest = fixture(tmp_path)
    report = verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)
    assert report['status'] == 'vendor-files-match-archive'
    assert report['omitted_archive_files'] == ['.gitignore']
    assert report['coverage'] == 'incomplete'


def test_forged_checksum_metadata_cannot_hide_altered_source(tmp_path):
    archive, vendor, digest = fixture(tmp_path)
    changed = b'pub fn value() -> u8 { 0 }\n';(vendor/'src/lib.rs').write_bytes(changed)
    metadata = {'package': digest, 'files': {'src/lib.rs': hashlib.sha256(changed).hexdigest()}}
    (vendor/'.cargo-checksum.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='differ'):verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)


def test_extra_missing_and_symlink_files_reject(tmp_path):
    archive, vendor, digest = fixture(tmp_path)
    extra = vendor/'extra';extra.write_text('new')
    with pytest.raises(ValueError, match='file set'):verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)
    extra.unlink();(vendor/'src/lib.rs').unlink()
    with pytest.raises(ValueError, match='file set'):verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)
    (vendor/'src/lib.rs').symlink_to(tmp_path/'absent')
    with pytest.raises(ValueError, match='vendor'):verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)


def test_archive_identity_mismatch_rejects(tmp_path):
    archive, vendor, digest = fixture(tmp_path)
    with pytest.raises(ValueError, match='digest'):verify_vendor(archive, vendor, 'crate-a', '1.0.0', '0'*64)


@pytest.mark.parametrize('kind', ['symlink', 'traversal', 'duplicate'])
def test_unsafe_archive_members_rejected_before_vendor_comparison(tmp_path, kind):
    archive, vendor, _ = fixture(tmp_path)
    with tarfile.open(archive, 'w:gz') as tar:
        path = 'crate-a-1.0.0/src/lib.rs'
        item = tarfile.TarInfo(path)
        if kind == 'symlink':
            item.type = tarfile.SYMTYPE;item.linkname = '/etc/passwd';tar.addfile(item)
        else:
            if kind == 'traversal':item.name = 'crate-a-1.0.0/src/../../escape'
            item.size = 1;tar.addfile(item, io.BytesIO(b'x'))
            if kind == 'duplicate':tar.addfile(item, io.BytesIO(b'x'))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='archive'):
        verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)


def test_forged_vendor_checksum_metadata_rejected(tmp_path):
    archive, vendor, digest = fixture(tmp_path)
    (vendor/'.cargo-checksum.json').write_text(json.dumps({'package': '0'*64, 'files': {}}))
    with pytest.raises(ValueError, match='checksum metadata'):
        verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)


def test_changed_executable_mode_rejected(tmp_path):
    archive, vendor, digest = fixture(tmp_path)
    (vendor/'src/lib.rs').chmod(0o755)
    with pytest.raises(ValueError, match='differ'):
        verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)


def test_unreadable_directory_fails_closed(tmp_path):
    import os
    archive, vendor, digest = fixture(tmp_path)
    hidden = vendor/'hidden';hidden.mkdir();(hidden/'extra').write_text('unseen')
    hidden.chmod(0)
    try:
        if os.access(hidden, os.R_OK | os.X_OK):pytest.skip('cannot simulate unreadable directory as privileged user')
        with pytest.raises(ValueError, match='vendor'):
            verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)
    finally:
        hidden.chmod(0o700)


def test_compressed_pax_metadata_has_a_decompression_bound(tmp_path, monkeypatch):
    import cargo_vendor
    archive, vendor, _ = fixture(tmp_path)
    with tarfile.open(archive, 'w:gz', format=tarfile.PAX_FORMAT) as tar:
        item = tarfile.TarInfo('crate-a-1.0.0/src/lib.rs')
        data = (vendor/'src/lib.rs').read_bytes();item.size = len(data)
        item.pax_headers = {'comment': 'x' * 100000};tar.addfile(item, io.BytesIO(data))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    metadata = json.loads((vendor/'.cargo-checksum.json').read_text());metadata['package'] = digest
    (vendor/'.cargo-checksum.json').write_text(json.dumps(metadata))
    monkeypatch.setattr(cargo_vendor, 'MAX_TAR_BYTES', 32768, raising=False)
    with pytest.raises(ValueError, match='size limit'):
        verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)


def test_entry_and_expanded_size_limits(tmp_path, monkeypatch):
    import cargo_vendor
    archive, vendor, digest = fixture(tmp_path)
    monkeypatch.setattr(cargo_vendor, 'MAX_FILES', 1)
    with pytest.raises(ValueError, match='too many crate'):
        verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)
    monkeypatch.setattr(cargo_vendor, 'MAX_FILES', 100)
    monkeypatch.setattr(cargo_vendor, 'MAX_EXPANDED_BYTES', 1)
    with pytest.raises(ValueError, match='archive too large'):
        verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)


@pytest.mark.parametrize('target', ['archive', 'vendor'])
def test_fifo_inputs_fail_within_bounded_time(tmp_path, target):
    import os
    import subprocess
    archive, vendor, digest = fixture(tmp_path)
    victim = archive if target == 'archive' else vendor/'src/lib.rs'
    victim.unlink();os.mkfifo(victim)
    code = """import sys
from cargo_vendor import verify_vendor
try:
    verify_vendor(sys.argv[1], sys.argv[2], 'crate-a', '1.0.0', sys.argv[3])
except ValueError:
    pass
else:
    raise AssertionError('FIFO was accepted')
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    subprocess.run([sys.executable, '-c', code, str(archive), str(vendor), digest],
                   env=env, check=True, timeout=5)


def test_truncated_gzip_trailer_rejected(tmp_path):
    archive, vendor, _ = fixture(tmp_path)
    archive.write_bytes(archive.read_bytes()[:-8])
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    metadata = json.loads((vendor/'.cargo-checksum.json').read_text());metadata['package'] = digest
    (vendor/'.cargo-checksum.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='archive'):
        verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)


def test_special_mode_bits_rejected(tmp_path):
    archive, vendor, digest = fixture(tmp_path)
    (vendor/'src/lib.rs').chmod(0o4644)
    with pytest.raises(ValueError, match='mode'):
        verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)


def test_file_inventory_digest_binds_metadata_and_permission_bits(tmp_path):
    archive, vendor, digest = fixture(tmp_path)
    (vendor/'src/lib.rs').chmod(0o644)
    first = verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)
    assert len(first['file_inventory_sha256']) == 64
    (vendor/'src/lib.rs').chmod(0o600)
    second = verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)
    assert second['file_inventory_sha256'] != first['file_inventory_sha256']
    metadata = vendor/'.cargo-checksum.json'
    metadata.write_text(json.dumps(json.loads(metadata.read_text()), indent=2))
    third = verify_vendor(archive, vendor, 'crate-a', '1.0.0', digest)
    assert third['file_inventory_sha256'] != second['file_inventory_sha256']
