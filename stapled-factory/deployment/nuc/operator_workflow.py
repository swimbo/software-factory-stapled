#!/usr/bin/env python3
"""Offline contract checks joining frozen provenance and validation receipts.

The inputs are operator-collected JSON evidence.  This module deliberately has
no network, Docker, SSH, Restate, or systemd client: it cannot redispatch work,
restart a service, or turn repository-only evidence into application approval.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


CLAIM_KEY = ('software-factory-stapled:delivery-loop-qualification-and-recovery:'
             'verify-frozen-dependency-provenance-and-operator-join-contracts')
IMAGE_RE = re.compile(r'[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}')
HEX64_RE = re.compile(r'[0-9a-f]{64}')
REVISION_RE = re.compile(r'[0-9a-f]{40}')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def evidence_digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _fields(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(label + ' has unexpected fields')


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be non-empty text')


def _workflow_evidence(evidence, policy):
    required = {'image', 'repository', 'revision', 'workflow', 'run_id', 'run_url',
                'policy', 'publication_step', 'log_sha256', 'publication_lines',
                'registry_index_sha256', 'platform_manifests',
                'publisher_signature_verified', 'evidence_type'}
    for name in required:
        if name not in evidence:
            raise ValueError('workflow evidence missing ' + name)
    image = evidence['image']
    if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
        raise ValueError('workflow evidence requires an immutable image')
    digest = image.rsplit('@', 1)[1]
    if evidence['registry_index_sha256'] != digest:
        raise ValueError('workflow image and registry index digest differ')
    _text(evidence['repository'], 'workflow repository')
    if evidence['repository'] not in policy['workflow_repositories']:
        raise ValueError('workflow repository is outside the operator trust policy')
    if not isinstance(evidence['revision'], str) or not REVISION_RE.fullmatch(evidence['revision']):
        raise ValueError('workflow evidence requires an exact revision')
    if (not isinstance(evidence['workflow'], str) or
            not evidence['workflow'].startswith('.github/workflows/') or
            '..' in evidence['workflow'].split('/')):
        raise ValueError('workflow evidence requires a reviewed workflow path')
    if type(evidence['run_id']) is not int or evidence['run_id'] <= 0:
        raise ValueError('workflow run identity required')
    if not isinstance(evidence['platform_manifests'], list) or not evidence['platform_manifests']:
        raise ValueError('workflow registry index has no platform manifests')
    if evidence['publisher_signature_verified'] is not False:
        raise ValueError('GitHub workflow evidence must not claim registry signature trust')
    if evidence['evidence_type'] != 'authenticated-github-workflow-and-registry-publication':
        raise ValueError('unexpected workflow evidence type')
    nested = evidence['policy']
    if not isinstance(nested, dict) or nested.get('repo') != evidence['repository']:
        raise ValueError('reviewed workflow policy differs from selected repository')
    for forbidden in ('publisher_identity', 'issuer', 'trust_policy'):
        if forbidden in evidence or forbidden in nested:
            raise ValueError('signature trust authority must stay outside workflow evidence')


def _trust_policy(policy):
    _fields(policy, {'schema_version', 'publisher_identity', 'issuer', 'workflow_repositories'},
            'trust policy')
    if policy['schema_version'] != 1:
        raise ValueError('unsupported trust policy version')
    _text(policy['publisher_identity'], 'publisher identity')
    _text(policy['issuer'], 'certificate issuer')
    repositories = policy['workflow_repositories']
    if (not isinstance(repositories, list) or not repositories or
            any(not isinstance(item, str) or not re.fullmatch(
                r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', item) for item in repositories) or
            len(repositories) != len(set(repositories))):
        raise ValueError('trust policy requires distinct reviewed repositories')


def _registry_evidence(evidence, image, policy):
    _fields(evidence, {'image', 'publisher_identity', 'issuer', 'verified_signatures',
                       'build_provenance_verified'}, 'registry attestation evidence')
    if evidence['image'] != image:
        raise ValueError('registry signature is bound to another image')
    if evidence['publisher_identity'] != policy['publisher_identity']:
        raise ValueError('registry publisher identity differs from trust policy')
    if evidence['issuer'] != policy['issuer']:
        raise ValueError('registry certificate issuer differs from trust policy')
    if type(evidence['verified_signatures']) is not int or evidence['verified_signatures'] < 1:
        raise ValueError('no registry signatures verified')
    if evidence['build_provenance_verified'] is not False:
        raise ValueError('registry signature evidence must not claim build provenance')


def _attestation(attestation, image, evidence, expected_digest, label):
    _fields(attestation, {'subject_digest', 'evidence_sha256', 'predicate_type'}, label)
    digest = image.rsplit('@', 1)[1]
    if attestation['subject_digest'] != digest:
        raise ValueError(label + ' is bound to another image digest')
    if not isinstance(attestation['predicate_type'], str) or not re.fullmatch(
            r'[A-Za-z][A-Za-z0-9+.-]+:/[^\s]+', attestation['predicate_type']):
        raise ValueError(label + ' requires a predicate type URI')
    if attestation['evidence_sha256'] != evidence_digest(evidence):
        raise ValueError(label + ' is not bound to its evidence digest')
    if expected_digest != evidence_digest(attestation):
        raise ValueError(label + ' bundle digest differs from its content')


def verify_dependency_provenance(bundle, trust_policy):
    """Bind reviewed publication and independently signed registry evidence."""
    _fields(bundle, {'schema_version', 'image', 'workflow_evidence',
                     'registry_attestation_evidence', 'workflow_attestation',
                     'registry_attestation', 'workflow_attestation_sha256',
                     'registry_attestation_sha256'}, 'dependency provenance bundle')
    if bundle['schema_version'] != 1:
        raise ValueError('unsupported dependency provenance schema')
    _trust_policy(trust_policy)
    image = bundle['image']
    if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
        raise ValueError('dependency provenance requires an immutable image')
    _workflow_evidence(bundle['workflow_evidence'], trust_policy)
    if bundle['workflow_evidence']['image'] != image:
        raise ValueError('workflow evidence is bound to another image')
    _registry_evidence(bundle['registry_attestation_evidence'], image, trust_policy)
    _attestation(bundle['workflow_attestation'], image, bundle['workflow_evidence'],
                 bundle['workflow_attestation_sha256'], 'workflow attestation')
    _attestation(bundle['registry_attestation'], image,
                 bundle['registry_attestation_evidence'],
                 bundle['registry_attestation_sha256'], 'registry attestation')
    if bundle['workflow_attestation_sha256'] == bundle['registry_attestation_sha256']:
        raise ValueError('workflow and registry attestations must remain distinct')
    return {
        'accepted': True,
        'image': image,
        'artifact_digest': image.rsplit('@', 1)[1],
        'workflow_evidence_sha256': evidence_digest(bundle['workflow_evidence']),
        'registry_attestation_evidence_sha256': evidence_digest(
            bundle['registry_attestation_evidence']),
        'workflow_attestation_sha256': bundle['workflow_attestation_sha256'],
        'registry_attestation_sha256': bundle['registry_attestation_sha256'],
        'publisher_identity': trust_policy['publisher_identity'],
        'certificate_issuer': trust_policy['issuer'],
        'verified_signatures': bundle['registry_attestation_evidence']['verified_signatures'],
        'workflow_signature_trust_claimed': False,
        'build_provenance_verified': False,
        'trust_policy_separated': True,
    }


def _selected_output(output):
    _fields(output, {'unit', 'attempt_id', 'git_revision', 'bundle_sha256',
                     'artifact_digest', 'envelope_digest'}, 'operator output')
    if not isinstance(output['unit'], str) or not re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', output['unit']):
        raise ValueError('invalid operator unit')
    if output['attempt_id'] not in (output['unit'] + ':0', output['unit'] + ':0:successor'):
        raise ValueError('operator output is not the selected original or successor')
    if not isinstance(output['git_revision'], str) or not REVISION_RE.fullmatch(
            output['git_revision']):
        raise ValueError('operator output requires an exact revision')
    for name, value in (('bundle_sha256', output['bundle_sha256']),
                       ('envelope_digest', output['envelope_digest'])):
        if not isinstance(value, str) or not HEX64_RE.fullmatch(value):
            raise ValueError('operator ' + name + ' must be a SHA-256 digest')
    if not isinstance(output['artifact_digest'], str) or not re.fullmatch(
            r'sha256:[0-9a-f]{64}', output['artifact_digest']):
        raise ValueError('operator output requires an immutable artifact digest')


def verify_operator_receipt_join(output, receipt, provenance=None):
    """Join the exact collected output to the separately accepted validation receipt."""
    _selected_output(output)
    if not isinstance(receipt, dict) or receipt.get('accepted') is not True:
        raise ValueError('validation receipt was not accepted')
    envelope = receipt.get('envelope_digest')
    if not isinstance(envelope, str) or not HEX64_RE.fullmatch(envelope):
        raise ValueError('validation receipt envelope digest required')
    proof = receipt.get('evidence')
    if not isinstance(proof, dict):
        raise ValueError('validation receipt evidence required')
    bindings = {
        'unit_id': output['unit'],
        'attempt_id': output['attempt_id'],
        'git_revision': output['git_revision'],
        'source_archive_digest': output['bundle_sha256'],
        'artifact_digest': output['artifact_digest'],
    }
    for name, expected in bindings.items():
        if proof.get(name) != expected:
            raise ValueError('validation receipt differs from operator output at ' + name)
    if envelope != output['envelope_digest']:
        raise ValueError('validation receipt envelope differs from operator output')
    if proof.get('passed') is not True or proof.get('scope') != 'disposable-candidate':
        raise ValueError('validation receipt is not accepted disposable-candidate evidence')
    for name in ('checks_digest', 'scenario_digest'):
        if not isinstance(proof.get(name), str) or not HEX64_RE.fullmatch(proof[name]):
            raise ValueError('validation receipt missing ' + name)
    if provenance is not None:
        if not isinstance(provenance, dict) or provenance.get('accepted') is not True:
            raise ValueError('dependency provenance was not accepted')
        if provenance.get('artifact_digest') != output['artifact_digest']:
            raise ValueError('dependency provenance is bound to another artifact')
    return {
        'accepted': True,
        'unit_id': output['unit'],
        'attempt_id': output['attempt_id'],
        'git_revision': output['git_revision'],
        'source_bundle_sha256': output['bundle_sha256'],
        'artifact_digest': output['artifact_digest'],
        'checks_digest': proof['checks_digest'],
        'scenario_digest': proof['scenario_digest'],
        'envelope_digest': envelope,
        'dependency_provenance_bound': provenance is not None,
    }


def verify_fixture(fixture):
    _fields(fixture, {'schema_version', 'trust_policy', 'dependency_provenance_bundle',
                      'operator_output', 'validation_receipt'}, 'verification fixture')
    if fixture['schema_version'] != 1:
        raise ValueError('unsupported fixture schema')
    provenance = verify_dependency_provenance(
        fixture['dependency_provenance_bundle'], fixture['trust_policy'])
    join = verify_operator_receipt_join(
        fixture['operator_output'], fixture['validation_receipt'], provenance)
    return {
        'accepted': True,
        'schema_version': 1,
        'claim_key': CLAIM_KEY,
        'fixture_sha256': evidence_digest(fixture),
        'dependency_provenance': provenance,
        'operator_receipt_join': join,
        'boundary': {
            'execution': 'local-in-process-fixture',
            'network_calls': 0,
            'service_restarts': 0,
            'production_authorization': False,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    args = parser.parse_args(argv)
    report = verify_fixture(json.loads(args.fixture.read_text()))
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


if __name__ == '__main__':
    sys.exit(0 if main()['accepted'] else 1)
