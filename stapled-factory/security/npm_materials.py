"""Check a narrow npm lockfile's downloaded bytes; this is not provenance proof.

The caller owns the material directory. Files are named by their SHA-512 digest;
no network access, package extraction, installation, or signing occurs here.
"""
import base64
import binascii
import hashlib
import json
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


def verify_materials(lock_bytes, directory):
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
            entries.append({'path': path, 'version': version, 'sha512': name})
        return {'status': 'verified-material-bytes-only', 'coverage': 'incomplete',
                'lock_sha256': hashlib.sha256(lock_bytes).hexdigest(), 'packages': entries}
