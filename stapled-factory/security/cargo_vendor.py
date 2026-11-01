"""Compare a Cargo vendor tree with a locked crate archive, without extracting it.

This point-in-time check does not freeze files, establish publisher identity,
or attest a build. The caller must preserve and bind verified bytes separately.
"""
import gzip
import hashlib
import json
import os
import re
import stat
import tarfile

MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 96 * 1024 * 1024
MAX_TAR_BYTES = 128 * 1024 * 1024
MAX_FILES = 100000
MAX_CHECKSUM_BYTES = 8 * 1024 * 1024


def _hash_stream(stream, limit):
    digest = hashlib.sha256()
    total = 0
    while chunk := stream.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            raise ValueError('vendor input exceeds size limit')
        digest.update(chunk)
    return digest.hexdigest(), total



class _BoundedTarStream:
    def __init__(self, stream):
        self.stream = stream
        self.total = 0

    def read(self, size=-1):
        remaining = MAX_TAR_BYTES - self.total
        limit = remaining + 1
        if size >= 0:limit = min(size, limit)
        data = self.stream.read(limit)
        self.total += len(data)
        if self.total > MAX_TAR_BYTES:
            raise ValueError('decompressed tar exceeds size limit')
        return data


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:raise ValueError('duplicate vendor checksum key')
        result[key] = value
    return result


def verify_vendor(archive, vendor, name, version, expected_sha256):
    if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name) or
            not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9.+_-]+', version) or
            not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256)):
        raise ValueError('invalid crate identity')
    try:
        fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ValueError('unsafe crate archive') from exc
    archive_files = {}
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE_BYTES:
            raise ValueError('unsafe crate archive')
        actual, _ = _hash_stream(stream, MAX_ARCHIVE_BYTES)
        if actual != expected_sha256:raise ValueError('crate archive digest differs')
        stream.seek(0)
        prefix = name + '-' + version + '/'
        seen = set();total = 0;count = 0
        try:
            with gzip.GzipFile(fileobj=stream) as uncompressed:
                bounded = _BoundedTarStream(uncompressed)
                with tarfile.open(fileobj=bounded, mode='r|') as tar:
                    for member in tar:
                        count += 1
                        if count > MAX_FILES:raise ValueError('too many crate entries')
                        path = member.name
                        if not path.startswith(prefix):raise ValueError('unexpected crate archive path')
                        relative = path[len(prefix):]
                        parts = relative.rstrip('/').split('/')
                        if (not relative or any(part in ('', '.', '..') for part in parts) or
                                '\\' in relative or relative in seen):
                            raise ValueError('unsafe crate archive path')
                        seen.add(relative)
                        if member.mode & 0o7000:raise ValueError('unsafe crate mode')
                        if member.isdir():continue
                        if not member.isfile() or relative == '.cargo-checksum.json':
                            raise ValueError('unsafe crate archive member')
                        total += member.size
                        if total > MAX_EXPANDED_BYTES:raise ValueError('crate archive too large')
                        source = tar.extractfile(member)
                        if source is None:raise ValueError('invalid crate archive member')
                        digest, length = _hash_stream(source, member.size)
                        if length != member.size:raise ValueError('incomplete crate archive member')
                        archive_files[relative] = (digest, member.mode & 0o111)
                while bounded.read(1024 * 1024):
                    pass
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise ValueError('invalid crate archive') from exc

    try:
        root_stat = os.lstat(vendor)
    except OSError as exc:
        raise ValueError('unsafe vendor directory') from exc
    if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_mode & 0o7000:
        raise ValueError('unsafe vendor directory mode')
    vendor_files = {};checksum = None;total = 0
    def traversal_error(error):
        raise ValueError('unreadable vendor directory') from error

    for root, dirs, files, dirfd in os.fwalk(vendor, follow_symlinks=False, onerror=traversal_error):
        for entry in dirs:
            directory_mode = os.stat(entry, dir_fd=dirfd, follow_symlinks=False).st_mode
            if not stat.S_ISDIR(directory_mode) or directory_mode & 0o7000:
                raise ValueError('unsafe vendor directory entry')
        for entry in files:
            relative = os.path.relpath(os.path.join(root, entry), vendor).replace(os.sep, '/')
            try:
                child = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dirfd)
            except OSError as exc:
                raise ValueError('unsafe vendor file') from exc
            with os.fdopen(child, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):raise ValueError('unsafe vendor file')
                if info.st_mode & 0o7000:raise ValueError('unsafe vendor file mode')
                if relative == '.cargo-checksum.json':
                    data = stream.read(MAX_CHECKSUM_BYTES + 1)
                    if len(data) > MAX_CHECKSUM_BYTES:raise ValueError('vendor checksum too large')
                    try:checksum = json.loads(data, object_pairs_hook=_unique)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ValueError('invalid vendor checksum') from exc
                else:
                    digest, length = _hash_stream(stream, MAX_EXPANDED_BYTES - total)
                    total += length
                    vendor_files[relative] = (digest, info.st_mode & 0o111)
                if len(vendor_files) > MAX_FILES:raise ValueError('too many vendor files')
    omitted = sorted(path for path in archive_files.keys() - vendor_files.keys()
                     if path.rsplit('/', 1)[-1] in ('.gitignore', '.gitattributes'))
    if archive_files.keys() - set(omitted) != vendor_files.keys():
        raise ValueError('vendor file set differs from archive')
    if any(archive_files[path] != digest for path, digest in vendor_files.items()):
        raise ValueError('vendor file contents differ from archive')
    if not isinstance(checksum, dict) or checksum.get('package') != expected_sha256 or checksum.get('files') != {path: data[0] for path, data in vendor_files.items()}:
        raise ValueError('vendor checksum metadata differs')
    return {'status': 'vendor-files-match-archive', 'coverage': 'incomplete',
            'crate_sha256': expected_sha256, 'files': len(vendor_files), 'omitted_archive_files': omitted}
