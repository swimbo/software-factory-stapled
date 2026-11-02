"""Check a narrow npm lockfile's downloaded bytes; this is not provenance proof.

The caller owns the material directory. Files are named by their SHA-512 digest;
no network access, package extraction, installation, or signing occurs here.
"""
import base64
import binascii
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from material_store import MaterialStore

MAX_LOCK_BYTES = 16 * 1024 * 1024
MAX_PACKAGES = 10000


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate lockfile key')
        value[key] = item
    return value


def _verify_registry_signature(path, version, integrity, record, keys):
    """Verify an npm registry signature against an independently pinned P-256 key.

    This does not check key lifetime or revocation, nor authenticate publishers.
    """
    parts = path.split('/')
    name = '/'.join(parts[len(parts) - 1 - parts[::-1].index('node_modules') + 1:])
    if not re.fullmatch(r'(?:[A-Za-z0-9._~-]+|@[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]+)', name):
        raise ValueError('unsupported signature package identity')
    if not re.fullmatch(r'[A-Za-z0-9.+_-]+', version):
        raise ValueError('unsupported signature version')
    if not isinstance(record, dict) or set(record) != {'keyid', 'sig'}:
        raise ValueError('invalid registry signature record')
    keyid, encoded = record['keyid'], record['sig']
    if not isinstance(keyid, str) or not isinstance(encoded, str) or len(encoded) > 256:
        raise ValueError('invalid registry signature')
    try:
        signature = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError('invalid registry signature') from exc
    if not signature or base64.b64encode(signature).decode() != encoded:
        raise ValueError('invalid registry signature')
    key = keys.get(keyid)
    p256_prefix = bytes.fromhex('3059301306072a8648ce3d020106082a8648ce3d03010703420004')
    if not isinstance(key, bytes) or len(key) != 91 or not key.startswith(p256_prefix):
        raise ValueError('registry signature key not pinned')
    def field(value): return len(value).to_bytes(4, 'big') + value
    ssh_key = field(b'ecdsa-sha2-nistp256') + field(b'nistp256') + field(key[-65:])
    if keyid != 'SHA256:' + base64.b64encode(hashlib.sha256(ssh_key).digest()).decode().rstrip('='):
        raise ValueError('registry signature key not pinned')
    pem = b'-----BEGIN PUBLIC KEY-----\n' + base64.b64encode(key) + b'\n-----END PUBLIC KEY-----\n'
    with tempfile.TemporaryDirectory(prefix='factory-npm-signature-') as temp:
        public, sig = Path(temp) / 'key.pem', Path(temp) / 'sig.der'
        public.write_bytes(pem); sig.write_bytes(signature)
        try:
            proc = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(public), '-signature', str(sig)],
                input=(name + '@' + version + ':' + integrity).encode(), capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError('registry signature verifier unavailable') from exc
        if proc.returncode != 0:
            raise ValueError('registry signature verification failed')


def verify_materials(lock_bytes, directory, *, registry_evidence=None, registry_keys=None):
    """Verify supported locked tarballs at this instant, retaining an incomplete verdict."""
    if not isinstance(lock_bytes, bytes) or len(lock_bytes) > MAX_LOCK_BYTES:
        raise ValueError('invalid lockfile size')
    try:
        lock = json.loads(lock_bytes, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid lockfile') from exc
    if (not isinstance(lock, dict) or type(lock.get('lockfileVersion')) is not int or
            lock['lockfileVersion'] != 3 or not isinstance(lock.get('packages'), dict)):
        raise ValueError('npm lockfile v3 packages required')
    packages = lock['packages']
    if len(packages) > MAX_PACKAGES or '' not in packages or not isinstance(packages[''], dict):
        raise ValueError('invalid npm lockfile packages')

    if (registry_evidence is None) != (registry_keys is None):
        raise ValueError('registry evidence and pinned keys must be supplied together')
    if registry_evidence is not None and (not isinstance(registry_evidence, dict) or not isinstance(registry_keys, dict) or
            set(registry_evidence) != set(packages) - {''}):
        raise ValueError('complete registry signature evidence required')

    with MaterialStore(directory) as store:
        entries = []
        for path, item in sorted(packages.items()):
            if path == '':
                continue
            parts = path.split('/') if isinstance(path, str) else ()
            if (not parts or path.startswith('/') or '\\' in path or
                    any(part in ('', '.', '..') for part in parts) or 'node_modules' not in parts or
                    parts[-1] == 'node_modules' or
                    not isinstance(item, dict) or item.get('link')):
                raise ValueError('unsupported npm package entry')
            version = item.get('version')
            resolved = item.get('resolved')
            integrity = item.get('integrity')
            if not isinstance(version, str) or not version or not isinstance(resolved, str):
                raise ValueError('unresolved npm package')
            url = urlsplit(resolved)
            if (url.scheme != 'https' or url.netloc != 'registry.npmjs.org' or
                    url.query or url.fragment or not url.path.endswith('.tgz')):
                raise ValueError('unsupported npm material source')
            if not isinstance(integrity, str) or not integrity.startswith('sha512-'):
                raise ValueError('SHA-512 package integrity required')
            encoded = integrity[7:]
            try:
                digest = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError('invalid package integrity') from exc
            if len(digest) != 64 or base64.b64encode(digest).decode() != encoded:
                raise ValueError('invalid package integrity')
            name = digest.hex()
            store.verify('sha512', name, path)
            entry = {'path': path, 'version': version, 'sha512': name}
            if registry_evidence is not None:
                _verify_registry_signature(path, version, integrity, registry_evidence[path], registry_keys)
                entry['registry_signature'] = 'verified-with-pinned-key'
                entry['registry_key_status'] = 'lifetime-and-revocation-unknown'
            entries.append(entry)
        return {'status': 'verified-material-bytes-only', 'coverage': 'incomplete',
                'lock_sha256': hashlib.sha256(lock_bytes).hexdigest(), 'packages': entries}
