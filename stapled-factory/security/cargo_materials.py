"""Verify narrow crates.io lock materials without claiming build provenance."""
import hashlib
import re
import tomllib

from material_store import MaterialStore

MAX_LOCK_BYTES = 16 * 1024 * 1024
MAX_PACKAGES = 10000
CRATES_IO = 'registry+https://github.com/rust-lang/crates.io-index'


def verify_materials(lock_bytes, directory):
    if not isinstance(lock_bytes, bytes) or len(lock_bytes) > MAX_LOCK_BYTES:
        raise ValueError('invalid Cargo lockfile size')
    try:
        lock = tomllib.loads(lock_bytes.decode('utf-8'))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError('invalid Cargo lockfile') from exc
    if (type(lock.get('version')) is not int or lock['version'] not in (3, 4) or
            not isinstance(lock.get('package'), list) or len(lock['package']) > MAX_PACKAGES):
        raise ValueError('unsupported Cargo lockfile')
    packages, local, identities = [], [], set()
    with MaterialStore(directory) as store:
        for item in lock['package']:
            if not isinstance(item, dict):raise ValueError('invalid Cargo package')
            name, version, source = item.get('name'), item.get('version'), item.get('source')
            if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
                raise ValueError('unresolved Cargo package')
            identity = (name, version, source)
            if identity in identities:raise ValueError('duplicate Cargo package')
            identities.add(identity)
            if source is None:
                local.append({'name': name, 'version': version})
                continue
            if source != CRATES_IO:
                raise ValueError('unsupported Cargo package source')
            digest = item.get('checksum')
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError('Cargo package checksum required')
            store.verify('sha256', digest, name + '@' + version)
            packages.append({'name': name, 'version': version, 'sha256': digest})
    return {'status': 'verified-material-bytes-only', 'coverage': 'incomplete',
            'lock_sha256': hashlib.sha256(lock_bytes).hexdigest(),
            'packages': sorted(packages, key=lambda p: (p['name'], p['version'])),
            'local_packages': sorted(local, key=lambda p: (p['name'], p['version']))}
