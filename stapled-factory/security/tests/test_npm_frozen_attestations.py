import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import npm_frozen_attestations as frozen_attestations
from controlled_npm import freeze_npm
from test_controlled_npm import inputs


def test_attestations_use_frozen_material_and_exact_lock_identity(tmp_path, monkeypatch):
    lock, store, archive = inputs(tmp_path)
    frozen = freeze_npm(lock, store, archive)
    seen = []
    def verify(**values):
        seen.append((values['name'], values['version'], values['package'].read_bytes(), values['expected_sha512']))
        return {'status': 'fixture'}
    monkeypatch.setattr(frozen_attestations, 'verify_workflow_attestation', verify)
    evidence = {'node_modules/example': {'workflow': {'bundle': 'bundle', 'identity': 'identity', 'issuer': 'issuer'}}}
    options = dict(trusted_root='root', trusted_root_sha256='a' * 64, cosign='cosign', cosign_sha256='b' * 64)
    result = frozen_attestations.verify_frozen_attestations(archive, frozen['archive_sha256'], evidence, **options)
    assert seen == [('example', '1.0.0', b'fixture package archive', hashlib.sha512(b'fixture package archive').hexdigest())]
    assert result['archive_sha256'] == frozen['archive_sha256']
    assert (result['package_count'], result['workflow_count'], result['registry_count']) == (1, 1, 0)
    assert result['coverage'] == 'incomplete'
    with pytest.raises(ValueError, match='not in frozen lock'):
        frozen_attestations.verify_frozen_attestations(archive, frozen['archive_sha256'], {'node_modules/other': evidence['node_modules/example']}, **options)
    with pytest.raises(ValueError, match='digest differs'):
        frozen_attestations.verify_frozen_attestations(archive, '0' * 64, evidence, **options)
    with pytest.raises(ValueError, match='inventory'):
        frozen_attestations.verify_frozen_attestations(archive, frozen['archive_sha256'], {}, **options)


def test_registry_rejection_cannot_produce_a_partial_success(tmp_path, monkeypatch):
    lock, store, archive = inputs(tmp_path)
    frozen = freeze_npm(lock, store, archive)
    monkeypatch.setattr(frozen_attestations, 'verify_workflow_attestation', lambda **kwargs: {'status': 'fixture'})
    def reject(**kwargs):
        raise ValueError('registry signature rejected')
    monkeypatch.setattr(frozen_attestations, 'verify_registry_attestation', reject)
    evidence = {'node_modules/example': {
        'workflow': {'bundle': 'bundle', 'identity': 'identity', 'issuer': 'issuer'},
        'registry': {'bundle': 'bundle', 'policy': {}}}}
    with pytest.raises(ValueError, match='registry signature rejected'):
        frozen_attestations.verify_frozen_attestations(archive, frozen['archive_sha256'], evidence,
            trusted_root='root', trusted_root_sha256='a' * 64, cosign='cosign', cosign_sha256='b' * 64)
