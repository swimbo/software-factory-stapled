"""Verify selected attestations against the exact bytes in a frozen npm archive.

This is an evidence collector, not a release gate. Trust policy must come from
the operator, and absent packages and unevaluated build claims remain unknown.
"""
import hashlib
import os
from pathlib import Path
import re
import stat
import tarfile
import tempfile

from controlled_npm import MAX_TOTAL_BYTES, MAX_PACKAGES, _BoundedTarInput, _entries, verify_frozen_npm
from npm_registry_attestation import verify_registry_attestation
from npm_workflow_attestation import verify_workflow_attestation


def verify_frozen_attestations(archive, archive_sha256, evidence, *, registry_keys=None,
                               trusted_root, trusted_root_sha256, cosign, cosign_sha256):
    """Verify evidence by lockfile path, binding each result to frozen bytes.

    Each evidence value may have ``workflow`` and/or ``registry`` entries.
    Workflow entries contain bundle, identity and issuer; registry entries
    contain bundle and an independently supplied key policy. Missing entries do
    not pass implicitly. The returned coverage is never a release verdict.
    """
    if not isinstance(archive_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', archive_sha256):
        raise ValueError('pinned archive SHA-256 required')
    if not isinstance(evidence, dict) or not evidence or len(evidence) > MAX_PACKAGES:
        raise ValueError('invalid attestation inventory')
    with tempfile.TemporaryDirectory(prefix='factory-frozen-attestations-') as tmp:
        root = Path(tmp); frozen = root / 'materials.tar'
        fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source, frozen.open('xb') as dest:
            info = os.fstat(source.fileno())
            limit = MAX_TOTAL_BYTES + (MAX_PACKAGES + 4) * 4096 + 10240
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError('unsafe npm material archive')
            digest = hashlib.sha256(); count = 0
            while chunk := source.read(1024 * 1024):
                count += len(chunk)
                if count > limit: raise ValueError('npm material archive too large')
                digest.update(chunk); dest.write(chunk)
        if digest.hexdigest() != archive_sha256:
            raise ValueError('npm material archive digest differs')
        verified = verify_frozen_npm(frozen, archive_sha256, registry_keys=registry_keys)
        results = {}
        with frozen.open('rb') as stream, tarfile.open(fileobj=_BoundedTarInput(stream), mode='r:') as tar:
            entries = _entries(tar.extractfile('package-lock.json').read())
            by_path = {entry['path']: entry for entry in entries}
            if not set(evidence) <= set(by_path): raise ValueError('attestation path not in frozen lock')
            for index, path in enumerate(sorted(evidence)):
                spec = evidence[path]
                if not isinstance(spec, dict) or not spec or not set(spec) <= {'workflow', 'registry'}:
                    raise ValueError('invalid package attestation evidence')
                entry = by_path[path]
                name = path.rsplit('/node_modules/', 1)[-1]
                if name.startswith('node_modules/'): name = name[len('node_modules/'):]
                package = root / ('package-' + str(index))
                member = tar.getmember('materials/' + entry['sha512'])
                with tar.extractfile(member) as src, package.open('xb') as dst:
                    while chunk := src.read(1024 * 1024): dst.write(chunk)
                common = dict(package=package, name=name, version=entry['version'], expected_sha512=entry['sha512'],
                              trusted_root=trusted_root, trusted_root_sha256=trusted_root_sha256,
                              cosign=cosign, cosign_sha256=cosign_sha256)
                result = {}
                if 'workflow' in spec:
                    workflow = spec['workflow']
                    if not isinstance(workflow, dict) or set(workflow) != {'bundle', 'identity', 'issuer'}:
                        raise ValueError('invalid workflow evidence')
                    result['workflow'] = verify_workflow_attestation(**common, **workflow)
                if 'registry' in spec:
                    registry = spec['registry']
                    if not isinstance(registry, dict) or set(registry) != {'bundle', 'policy'}:
                        raise ValueError('invalid registry evidence')
                    result['registry'] = verify_registry_attestation(**common, **registry)
                results[path] = result
        return {'status': 'verified-selected-frozen-npm-attestations', 'coverage': 'incomplete',
                'archive_sha256': archive_sha256, 'lock_sha256': verified['lock_sha256'],
                'manifest_sha256': verified['manifest_sha256'], 'package_count': len(entries),
                'attested_package_count': len(results), 'packages': results,
                'workflow_count': sum('workflow' in result for result in results.values()),
                'registry_count': sum('registry' in result for result in results.values()),
                'build_claims': 'not_evaluated', 'policy_authentication': 'not_evaluated'}
