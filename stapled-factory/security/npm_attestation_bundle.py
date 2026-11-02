"""Freeze npm attestation bundles for independent, policy-bound verification.

The bundle contains evidence only. Trust identities and key policy are supplied
separately by the verifier and are never accepted from the evidence file.
"""
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from npm_frozen_attestations import verify_frozen_attestations
from npm_workflow_attestation import MAX_METADATA, _json, _small

MAX_BUNDLE = 64 * 1024 * 1024


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def freeze_attestation_bundle(evidence, output):
    """Write a create-only private evidence file; no trust claims are made."""
    if not isinstance(evidence, dict) or not evidence:
        raise ValueError('attestation evidence required')
    records = {}; total = 0
    for path, spec in evidence.items():
        if not isinstance(path, str) or not path or not isinstance(spec, dict) or not spec or not set(spec) <= {'workflow', 'registry'}:
            raise ValueError('invalid attestation evidence')
        records[path] = {}
        for kind, file in spec.items():
            encoded = base64.b64encode(_small(file, MAX_METADATA)).decode()
            total += len(encoded) + len(path) + len(kind)
            if total > MAX_BUNDLE: raise ValueError('attestation bundle too large')
            records[path][kind] = encoded
    raw = _canonical({'schema': 'factory.npm-attestation-bundle.v1', 'packages': records})
    if len(raw) > MAX_BUNDLE: raise ValueError('attestation bundle too large')
    target = Path(output)
    parent = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(parent)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('private output directory required')
        fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.fsync(parent)
    finally:
        os.close(parent)
    return hashlib.sha256(raw).hexdigest()


def verify_attestation_bundle(bundle, expected_sha256, policy, *, archive, archive_sha256,
                              trusted_root, trusted_root_sha256, cosign, cosign_sha256, registry_keys=None):
    """Reverify frozen evidence under independently supplied exact trust policy."""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise ValueError('pinned attestation bundle digest required')
    raw = _small(bundle, MAX_BUNDLE)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('attestation bundle digest differs')
    data = _json(raw)
    if (not isinstance(data, dict) or set(data) != {'schema', 'packages'} or
            data['schema'] != 'factory.npm-attestation-bundle.v1' or
            not isinstance(data['packages'], dict) or not data['packages'] or _canonical(data) != raw):
        raise ValueError('invalid attestation bundle')
    policy_raw = _canonical(policy)
    if len(policy_raw) > MAX_METADATA: raise ValueError('attestation policy too large')
    policy = _json(policy_raw)
    if not isinstance(policy, dict) or set(policy) != set(data['packages']):
        raise ValueError('attestation policy inventory differs')
    with tempfile.TemporaryDirectory(prefix='factory-attestation-bundle-') as tmp:
        specs = {}
        for index, (path, entries) in enumerate(sorted(data['packages'].items())):
            selected = policy[path]
            if (not isinstance(entries, dict) or not entries or not set(entries) <= {'workflow', 'registry'} or
                    not isinstance(selected, dict) or set(entries) != set(selected)):
                raise ValueError('attestation policy types differ')
            specs[path] = {}
            for kind, encoded in entries.items():
                try: contents = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError, binascii.Error) as exc: raise ValueError('invalid attestation encoding') from exc
                if len(contents) > MAX_METADATA or base64.b64encode(contents).decode() != encoded:
                    raise ValueError('invalid attestation encoding')
                file = Path(tmp) / (str(index) + '-' + kind); file.write_bytes(contents)
                choice = selected[kind]
                if kind == 'workflow':
                    if not isinstance(choice, dict) or set(choice) != {'identity', 'issuer'}:
                        raise ValueError('workflow trust identity required')
                    specs[path][kind] = {'bundle': file, **choice}
                else:
                    specs[path][kind] = {'bundle': file, 'policy': choice}
        result = verify_frozen_attestations(archive, archive_sha256, specs, registry_keys=registry_keys,
            trusted_root=trusted_root, trusted_root_sha256=trusted_root_sha256, cosign=cosign, cosign_sha256=cosign_sha256)
    result['attestation_bundle_sha256'] = expected_sha256
    result['attestation_policy_sha256'] = hashlib.sha256(policy_raw).hexdigest()
    return result
