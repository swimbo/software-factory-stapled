"""Create a verified, digest-bound Cargo input snapshot for an offline builder.

This is a preparation primitive, not a build, a publisher identity check, or
proof that a builder consumed the resulting tar. The caller must bind the tar
hash, source, recipe, base image and builder to a signed output separately.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import tempfile

from cargo_materials import verify_materials
from cargo_vendor import MAX_CHECKSUM_BYTES, _unique, verify_vendor

MAX_FILES = 200000
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024


def _copy_file(source, target, *, directory_fd=None):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(source, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise ValueError('unsafe snapshot file') from exc
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES or info.st_mode & 0o7000:
            raise ValueError('unsafe snapshot file')
        with target.open('xb') as output:
            copied = 0
            while chunk := stream.read(1024 * 1024):
                copied += len(chunk)
                if copied > MAX_FILE_BYTES:
                    raise ValueError('snapshot file too large')
                output.write(chunk)
        if copied != info.st_size:
            raise ValueError('snapshot file changed during copy')
        # Match tarfile's data-filter mode policy so post-extraction inventory
        # describes the exact modes that will be consumed.
        target.chmod((stat.S_IMODE(info.st_mode) & 0o755) | 0o600)
        return copied


def _snapshot_vendor(source, target):
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError('unsafe vendor directory') from exc
    count = total = 0
    try:
        def fail(error):
            raise ValueError('unreadable vendor directory') from error
        for root, dirs, files, dirfd in os.fwalk('.', dir_fd=source_fd, follow_symlinks=False, onerror=fail):
            relative = Path(root)
            destination = target / relative
            destination.mkdir(exist_ok=True)
            count += len(dirs)
            if count > MAX_FILES:
                raise ValueError('too many vendor entries')
            for name in dirs:
                mode = os.stat(name, dir_fd=dirfd, follow_symlinks=False).st_mode
                if not stat.S_ISDIR(mode) or mode & 0o7000:
                    raise ValueError('unsafe vendor directory entry')
            for name in files:
                count += 1
                if count > MAX_FILES:
                    raise ValueError('too many vendor files')
                total += _copy_file(name, destination / name, directory_fd=dirfd)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError('vendor tree too large')
    finally:
        os.close(source_fd)


def _verify_snapshot(lock_bytes, root):
    report = verify_materials(lock_bytes, root / 'materials')
    packages = report['packages']
    by_digest = {item['sha256']: item for item in packages}
    if len(by_digest) != len(packages):
        raise ValueError('ambiguous locked crate digest')
    actual_materials = {entry.name for entry in (root / 'materials').iterdir()}
    if actual_materials != set(by_digest):
        raise ValueError('unlocked or missing crate material')
    seen = set()
    vendor_report = []
    for entry in sorted((root / 'vendor').iterdir()):
        if (not re.fullmatch(r'[A-Za-z0-9._+-]+', entry.name) or entry.name in ('.', '..') or
                not entry.is_dir() or entry.is_symlink()):
            raise ValueError('unsupported vendor crate path')
        metadata_path = entry / '.cargo-checksum.json'
        try:
            fd = os.open(metadata_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CHECKSUM_BYTES:
                    raise ValueError('unsafe vendor checksum')
                metadata = json.loads(stream.read(MAX_CHECKSUM_BYTES + 1), object_pairs_hook=_unique)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError('invalid vendor checksum') from exc
        digest = metadata.get('package') if isinstance(metadata, dict) else None
        if not isinstance(digest, str) or digest not in by_digest or digest in seen:
            raise ValueError('unlocked or duplicate vendor crate')
        package = by_digest[digest]
        verified = verify_vendor(root / 'materials' / digest, entry, package['name'], package['version'], digest)
        seen.add(digest)
        vendor_report.append({'directory': entry.name, 'sha256': digest,
                              'inventory_sha256': verified['file_inventory_sha256']})
    if seen != set(by_digest):
        raise ValueError('locked crate missing from vendor tree')
    return report, vendor_report


def freeze_cargo(lock_bytes, materials_dir, vendor_dir, output_dir):
    """Verify copies, archive them, then independently reverify the archive.

    All paths must be operator controlled. The created directory and tar are
    mutable by their owner; the caller must hash-check and isolate the exact tar
    before use. Failed output is retained and never silently overwritten.
    """
    report = verify_materials(lock_bytes, materials_dir)
    output = Path(output_dir)
    resolved = output.resolve()
    if any(resolved.is_relative_to(Path(source).resolve()) for source in (materials_dir, vendor_dir)):
        raise ValueError('output cannot be inside source inputs')
    output.mkdir(mode=0o700)  # exclusive; preserve any original attempt
    with tempfile.TemporaryDirectory(prefix='.cargo-snapshot-', dir=output) as temp:
        work = Path(temp)
        snapshot = work / 'snapshot'; snapshot.mkdir(mode=0o700)
        material_copy = snapshot / 'materials'; material_copy.mkdir(mode=0o700)
        vendor_copy = snapshot / 'vendor'; vendor_copy.mkdir(mode=0o700)
        (snapshot / 'Cargo.lock').write_bytes(lock_bytes)
        try:
            source_fd = os.open(materials_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ValueError('unsafe material directory') from exc
        try:
            total = 0
            for digest in sorted({p['sha256'] for p in report['packages']}):
                total += _copy_file(digest, material_copy / digest, directory_fd=source_fd)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError('material snapshot too large')
        finally:
            os.close(source_fd)
        _snapshot_vendor(vendor_dir, vendor_copy)
        first_report, first_vendors = _verify_snapshot(lock_bytes, snapshot)
        archive = output / 'cargo-inputs.tar'
        with archive.open('xb') as out, tarfile.open(fileobj=out, mode='w') as tar:
            tar.add(snapshot / 'Cargo.lock', arcname='Cargo.lock', recursive=False)
            tar.add(material_copy, arcname='materials', recursive=True)
            tar.add(vendor_copy, arcname='vendor', recursive=True)
        with archive.open('rb') as stream:
            archive_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        verified = verify_frozen_cargo(archive, archive_digest)
        if (verified['lock_sha256'] != first_report['lock_sha256'] or
                verified['packages'] != first_report['packages'] or
                verified['local_packages'] != first_report['local_packages'] or
                verified['vendor'] != first_vendors):
            raise ValueError('frozen snapshot inventory differs')
        return verified


class _BoundedTarInput:
    """Prevent a hostile tar extended header from requesting an unbounded read."""
    def __init__(self, stream):
        self.stream = stream

    def read(self, size=-1):
        if size < 0 or size > 8 * 1024 * 1024:
            raise ValueError('oversized snapshot tar read')
        return self.stream.read(size)

    def seek(self, offset, whence=0):
        return self.stream.seek(offset, whence)

    def tell(self):
        return self.stream.tell()

    def seekable(self):
        return self.stream.seekable()


def verify_frozen_cargo(archive, expected_sha256):
    """Independently verify one pinned tar into a fresh private directory.

    This does not freeze the archive against an owner with write access or
    establish anything about its use by a builder. The caller must isolate the
    exact verified bytes before a build.
    """
    if not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise ValueError('expected Cargo snapshot SHA-256 required')
    try:
        fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ValueError('unsafe Cargo snapshot archive') from exc
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        max_archive = 2 * MAX_TOTAL_BYTES + 16 * 1024 * 1024 + (MAX_FILES + 20000) * 8192
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_archive:
            raise ValueError('unsafe Cargo snapshot archive')
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_sha256:
            raise ValueError('Cargo snapshot digest differs')
        stream.seek(0)
        with tempfile.TemporaryDirectory(prefix='verified-cargo-snapshot-') as temp:
            check = Path(temp)
            try:
                with tarfile.open(fileobj=_BoundedTarInput(stream), mode='r:') as tar:
                    members = []; names = set(); total = 0
                    for member in tar:
                        if len(members) >= MAX_FILES + 20000:
                            raise ValueError('too many snapshot entries')
                        name = member.name
                        parts = name.split('/')
                        if (name in names or name.startswith('/') or '\\' in name or
                                any(part in ('', '.', '..') for part in parts) or
                                parts[0] not in ('Cargo.lock', 'materials', 'vendor') or
                                (parts[0] == 'Cargo.lock' and (len(parts) != 1 or not member.isfile())) or
                                (parts[0] == 'materials' and len(parts) > 2) or
                                (parts[0] == 'materials' and len(parts) == 2 and
                                 (not member.isfile() or not re.fullmatch(r'[a-f0-9]{64}', parts[1]))) or
                                not (member.isfile() or member.isdir()) or member.mode & 0o7000 or
                                (member.isfile() and member.mode != ((member.mode & 0o755) | 0o600)) or
                                set(member.pax_headers) - {'path', 'mtime'} or
                                member.size < 0 or member.size > MAX_FILE_BYTES):
                            raise ValueError('unsafe frozen snapshot entry')
                        names.add(name); members.append(member); total += member.size
                        if total > 2 * MAX_TOTAL_BYTES + 16 * 1024 * 1024:
                            raise ValueError('frozen snapshot too large')
                    if not {'Cargo.lock', 'materials', 'vendor'} <= names:
                        raise ValueError('incomplete frozen snapshot')
                    tar.extractall(check, members=members, filter='data')
            except (tarfile.TarError, OSError) as exc:
                raise ValueError('invalid frozen snapshot') from exc
            # The data extraction filter intentionally ignores directory modes.
            (check / 'materials').chmod(0o700)
            lock_path = check / 'Cargo.lock'
            if lock_path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('Cargo lock too large')
            verified, vendors = _verify_snapshot(lock_path.read_bytes(), check)
        stream.seek(0)
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_sha256:
            raise ValueError('Cargo snapshot changed during verification')
    return {'status': 'verified-frozen-cargo-inputs', 'coverage': 'incomplete',
            'archive': str(archive), 'archive_sha256': expected_sha256,
            'lock_sha256': verified['lock_sha256'], 'packages': verified['packages'],
            'local_packages': verified['local_packages'], 'vendor': vendors}
