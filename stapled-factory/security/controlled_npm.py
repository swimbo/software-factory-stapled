"""Freeze a narrow npm material set for a future network-isolated builder.

The returned hashes bind bytes, not publisher identity, an npm installation, or
an image. The caller must keep the archive immutable from verification through
build and must separately enforce an offline install with lifecycle scripts off.
"""
import base64
import binascii
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile
from urllib.parse import urlsplit

from material_store import MaterialStore, MAX_MATERIAL_BYTES
from npm_materials import MAX_LOCK_BYTES, MAX_PACKAGES, _unique_object, verify_materials

MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
NAME = r'[A-Za-z0-9._~-]+'


def _entries(lock_bytes):
    if not isinstance(lock_bytes, bytes) or len(lock_bytes) > MAX_LOCK_BYTES:
        raise ValueError('invalid npm lockfile size')
    try:
        lock = json.loads(lock_bytes, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid npm lockfile') from exc
    if (not isinstance(lock, dict) or type(lock.get('lockfileVersion')) is not int or
            lock['lockfileVersion'] != 3 or not isinstance(lock.get('packages'), dict)):
        raise ValueError('npm lockfile v3 packages required')
    packages = lock['packages']
    if len(packages) > MAX_PACKAGES or not isinstance(packages.get(''), dict):
        raise ValueError('invalid npm lockfile packages')
    result, urls = [], {}
    for path, item in sorted(packages.items()):
        if path == '':
            continue
        if not isinstance(path, str) or not isinstance(item, dict) or item.get('link'):
            raise ValueError('unsupported npm package')
        parts = path.split('/')
        component = r'(?:' + NAME + r'|@' + NAME + r'/' + NAME + r')'
        if not re.fullmatch(r'node_modules/' + component + r'(?:/node_modules/' + component + r')*', path):
            raise ValueError('unsupported npm package path')
        if any(part in ('', '.', '..') for part in parts) or '\\' in path or 'node_modules' not in parts:
            raise ValueError('unsafe npm package path')
        last = len(parts) - 1 - parts[::-1].index('node_modules')
        name = '/'.join(parts[last + 1:])
        if not re.fullmatch(r'(?:' + NAME + r'|@' + NAME + r'/' + NAME + r')', name):
            raise ValueError('unsupported npm package path')
        version, url, integrity = item.get('version'), item.get('resolved'), item.get('integrity')
        if not isinstance(version, str) or not version or not isinstance(url, str) or not isinstance(integrity, str) or not integrity.startswith('sha512-'):
            raise ValueError('unresolved npm package')
        parsed = urlsplit(url)
        prefix = '/' + name + '/-/'
        if (parsed.scheme != 'https' or parsed.netloc != 'registry.npmjs.org' or parsed.query or parsed.fragment or
                not parsed.path.startswith(prefix) or '/' in parsed.path[len(prefix):] or not parsed.path.endswith('.tgz')):
            raise ValueError('unsupported npm source or alias')
        try:
            encoded = integrity[7:]
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError('invalid package integrity') from exc
        if len(raw) != 64 or base64.b64encode(raw).decode() != encoded:
            raise ValueError('invalid package integrity')
        digest = raw.hex()
        if url in urls and urls[url] != digest:
            raise ValueError('registry URL has conflicting materials')
        urls[url] = digest
        result.append({'path': path, 'version': version, 'resolved': url, 'sha512': digest})
    return result


def _manifest(lock_bytes, entries):
    return json.dumps({'schema': 'factory.npm-materials.v1',
        'lock_sha256': hashlib.sha256(lock_bytes).hexdigest(), 'packages': entries},
        sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def _add(tar, name, size, stream):
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime, info.uid, info.gid = size, 0o600, 0, 0, 0
    info.uname = info.gname = ''
    tar.addfile(info, stream)


class _BoundedTarInput:
    """Limit tarfile's reads of attacker-controlled extended header sizes."""
    def __init__(self, stream):
        self.stream = stream

    def read(self, size=-1):
        if size < 0 or size > MAX_MANIFEST_BYTES + 1024:
            raise ValueError('npm tar read exceeds limit')
        return self.stream.read(size)

    def seek(self, *args):
        return self.stream.seek(*args)

    def tell(self):
        return self.stream.tell()


class _DigestingReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha512()
        self.count = 0

    def read(self, size):
        data = self.stream.read(size)
        self.digest.update(data)
        self.count += len(data)
        return data


def freeze_npm(lock_bytes, material_dir, output_tar):
    """Create a new private archive; failures retain any incomplete output."""
    checked = verify_materials(lock_bytes, material_dir)
    entries = _entries(lock_bytes)
    if len(entries) != len(checked['packages']):
        raise ValueError('material inventory differs')
    manifest = _manifest(lock_bytes, entries)
    if len(manifest) > MAX_MANIFEST_BYTES:
        raise ValueError('npm material manifest too large')
    path = Path(output_tar)
    if path.name in ('', '.', '..'):
        raise ValueError('invalid output name')
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(parent)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('private output directory required')
        fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'w+b') as output:
            total = len(lock_bytes) + len(manifest)
            with MaterialStore(material_dir) as store, tarfile.open(fileobj=output, mode='w|') as tar:
                _add(tar, 'package-lock.json', len(lock_bytes), io.BytesIO(lock_bytes))
                _add(tar, 'manifest.json', len(manifest), io.BytesIO(manifest))
                for digest in sorted({entry['sha512'] for entry in entries}):
                    try:
                        child = os.open(digest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=store.fd)
                    except OSError as exc:
                        raise ValueError('unsafe or missing material') from exc
                    with os.fdopen(child, 'rb') as source:
                        info = os.fstat(source.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MATERIAL_BYTES:
                            raise ValueError('unsafe material')
                        total += info.st_size
                        if total > MAX_TOTAL_BYTES:
                            raise ValueError('npm material set too large')
                        reader = _DigestingReader(source)
                        _add(tar, 'materials/' + digest, info.st_size, reader)
                        if reader.count != info.st_size or reader.digest.hexdigest() != digest:
                            raise ValueError('npm material changed while freezing')
            output.flush(); os.fsync(output.fileno()); output.seek(0)
            archive_sha256 = hashlib.file_digest(output, 'sha256').hexdigest()
        os.fsync(parent)
    finally:
        os.close(parent)
    return {'status': 'frozen-npm-material-bytes', 'coverage': 'incomplete',
            'archive_sha256': archive_sha256, 'lock_sha256': checked['lock_sha256'],
            'manifest_sha256': hashlib.sha256(manifest).hexdigest()}


def verify_frozen_npm(archive, expected_tar_sha256):
    """Verify a pinned frozen archive without extracting it or executing npm."""
    if not isinstance(expected_tar_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_tar_sha256):
        raise ValueError('pinned archive SHA-256 required')
    fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_TOTAL_BYTES + (MAX_PACKAGES + 4) * 4096 + 10240:
            raise ValueError('unsafe npm material archive')
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_tar_sha256:
            raise ValueError('npm material archive digest differs')
        stream.seek(0)
        with tarfile.open(fileobj=_BoundedTarInput(stream), mode='r:') as tar:
            members = {}
            total = 0
            for member in tar:
                if (len(members) >= MAX_PACKAGES + 2 or member.name in members or not member.isfile() or
                        not (member.name in ('package-lock.json', 'manifest.json') or
                             re.fullmatch(r'materials/[a-f0-9]{128}', member.name))):
                    raise ValueError('unsafe or duplicate npm archive member')
                total += member.size
                if total > MAX_TOTAL_BYTES or member.size > MAX_MATERIAL_BYTES:
                    raise ValueError('npm material archive too large')
                members[member.name] = member
            def read_small(name, limit):
                member = members.get(name)
                if member is None or member.size > limit:
                    raise ValueError('missing or oversized npm archive metadata')
                return tar.extractfile(member).read()
            lock = read_small('package-lock.json', MAX_LOCK_BYTES)
            manifest = read_small('manifest.json', MAX_MANIFEST_BYTES)
            entries = _entries(lock)
            if manifest != _manifest(lock, entries):
                raise ValueError('npm material manifest differs from lock')
            expected = {'package-lock.json', 'manifest.json'} | {'materials/' + e['sha512'] for e in entries}
            if set(members) != expected:
                raise ValueError('npm material archive inventory differs')
            for name in sorted(expected - {'package-lock.json', 'manifest.json'}):
                data = tar.extractfile(members[name]); digest = hashlib.sha512()
                while chunk := data.read(1024 * 1024):
                    digest.update(chunk)
                if digest.hexdigest() != name.split('/')[1]:
                    raise ValueError('npm material digest differs')
        stream.seek(0)
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_tar_sha256:
            raise ValueError('npm material archive changed during verification')
    return {'status': 'verified-frozen-npm-material-bytes', 'coverage': 'incomplete',
            'archive_sha256': expected_tar_sha256, 'lock_sha256': hashlib.sha256(lock).hexdigest(),
            'manifest_sha256': hashlib.sha256(manifest).hexdigest()}
