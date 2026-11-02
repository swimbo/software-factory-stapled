import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import npm_attestation_bundle as bundles


def test_bundle_requires_independent_matching_policy_and_exact_digest(tmp_path, monkeypatch):
    source = tmp_path / 'original'; source.write_bytes(b'signed evidence')
    output = tmp_path / 'evidence.json'
    digest = bundles.freeze_attestation_bundle({'node_modules/pkg': {'workflow': source}}, output)
    source.write_bytes(b'replaced')
    policy = {'node_modules/pkg': {'workflow': {'identity': 'identity', 'issuer': 'issuer'}}}
    def verify(archive, archive_sha256, evidence, **kwargs):
        assert evidence['node_modules/pkg']['workflow']['bundle'].read_bytes() == b'signed evidence'
        assert evidence['node_modules/pkg']['workflow']['identity'] == 'identity'
        return {'coverage': 'incomplete'}
    monkeypatch.setattr(bundles, 'verify_frozen_attestations', verify)
    opts = dict(archive='archive', archive_sha256='a'*64, trusted_root='root', trusted_root_sha256='b'*64,
                cosign='cosign', cosign_sha256='c'*64)
    result = bundles.verify_attestation_bundle(output, digest, policy, **opts)
    assert result['attestation_bundle_sha256'] == digest
    assert result['attestation_policy_sha256'] == hashlib.sha256(bundles._canonical(policy)).hexdigest()
    with pytest.raises(ValueError, match='inventory differs'):
        bundles.verify_attestation_bundle(output, digest, {}, **opts)
    with pytest.raises(ValueError, match='types differ'):
        bundles.verify_attestation_bundle(output, digest, {'node_modules/pkg': {'registry': {}}}, **opts)
    with pytest.raises(ValueError, match='digest differs'):
        bundles.verify_attestation_bundle(output, '0'*64, policy, **opts)
    with pytest.raises(FileExistsError):
        bundles.freeze_attestation_bundle({'node_modules/pkg': {'workflow': source}}, output)
