"""Verify a narrowly supported npm registry publish attestation.

The operator must independently authenticate the registry key policy and
Sigstore root. Registry authorization is a registry assertion; this does not
identify a human publisher or establish complete build provenance.
"""
import hashlib
import json
from datetime import datetime, timezone
import re

from npm_registry_policy import decode_policy
from npm_workflow_attestation import MAX_METADATA, _bundle_statement, _json, _purl, _small, _subject, _verify_cosign

PREDICATE='https://github.com/npm/attestation/tree/main/specs/publish/v0.1'
REGISTRY='https://registry.npmjs.org'


def _time(value):
    if not isinstance(value,str) or len(value)>40 or not value.endswith('Z'):
        raise ValueError('registry key validity time required')
    try:result=datetime.fromisoformat(value[:-1]+'+00:00')
    except ValueError as exc:raise ValueError('invalid registry key validity time') from exc
    if result.tzinfo!=timezone.utc:raise ValueError('registry key validity must be UTC')
    return result.timestamp()


def _policy(value):
    if (not isinstance(value,dict) or set(value)!={'keyid','key','valid_from','valid_until','registry'} or
            value['registry']!=REGISTRY or not isinstance(value['keyid'],str)):
        raise ValueError('invalid registry attestation policy')
    keys=decode_policy({value['keyid']:value['key']})
    start=_time(value['valid_from']);end=None if value['valid_until'] is None else _time(value['valid_until'])
    if end is not None and end<=start:raise ValueError('invalid registry key validity interval')
    return keys[value['keyid']],start,end


def validate_publish_statement(bundle_bytes,name,version,sha512,policy):
    """Validate identity and policy; cryptographic verification is separate."""
    _,start,end=_policy(policy)
    bundle,statement=_bundle_statement(bundle_bytes,media_types=('application/vnd.dev.sigstore.bundle+json;version=0.2',
                                                                 'application/vnd.dev.sigstore.bundle.v0.3+json'))
    if (not isinstance(statement,dict) or statement.get('_type')!='https://in-toto.io/Statement/v0.1' or
            statement.get('predicateType')!=PREDICATE):raise ValueError('unsupported registry publish statement')
    _subject(statement,name,version,sha512)
    predicate=statement.get('predicate')
    if (not isinstance(predicate,dict) or predicate.get('name')!=name or predicate.get('version')!=version or
            predicate.get('registry')!=REGISTRY):raise ValueError('registry publish predicate differs')
    material=bundle.get('verificationMaterial');envelope=bundle.get('dsseEnvelope')
    public=material.get('publicKey') if isinstance(material,dict) else None
    logs=material.get('tlogEntries') if isinstance(material,dict) else None
    signatures=envelope.get('signatures') if isinstance(envelope,dict) else None
    if (not isinstance(public,dict) or public.get('hint')!=policy['keyid'] or
            not isinstance(signatures,list) or len(signatures)!=1 or not isinstance(signatures[0],dict) or
            signatures[0].get('keyid')!=policy['keyid'] or not isinstance(logs,list) or len(logs)!=1 or
            not isinstance(logs[0],dict)):
        raise ValueError('registry attestation signing key differs')
    value=logs[0].get('integratedTime')
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,12}',value):
        raise ValueError('registry attestation integration time unavailable')
    integrated=int(value)
    if integrated<start or end is not None and integrated>=end:
        raise ValueError('registry key validity differs')
    return integrated


def verify_registry_attestation(*,package,bundle,name,version,expected_sha512,policy,
                                trusted_root,trusted_root_sha256,cosign,cosign_sha256):
    """Verify a pinned npm registry key, transparency proof and exact package subject."""
    _purl(name,version)
    encoded=json.dumps(policy,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if len(encoded)>65536:raise ValueError('registry policy too large')
    policy=_json(encoded)
    if not isinstance(trusted_root_sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',trusted_root_sha256):
        raise ValueError('pinned trusted root required')
    _policy(policy)
    bundle_raw=_small(bundle,MAX_METADATA);root_raw=_small(trusted_root,MAX_METADATA,trusted=True)
    if hashlib.sha256(root_raw).hexdigest()!=trusted_root_sha256:raise ValueError('trusted root differs')
    integrated=validate_publish_statement(bundle_raw,name,version,expected_sha512,policy)
    key_pem=b'-----BEGIN PUBLIC KEY-----\n'+policy['key'].encode()+b'\n-----END PUBLIC KEY-----\n'
    _verify_cosign(package,bundle_raw,root_raw,expected_sha512,cosign,cosign_sha256,
                   ['--type',PREDICATE],key_pem=key_pem)
    return {'status':'verified-registry-publish-attestation','coverage':'incomplete',
            'registry_authorization':'registry_asserted','policy_authentication':'not_evaluated','build_claims':'not_evaluated',
            'package':_purl(name,version),'package_sha512':expected_sha512,'registry_keyid':policy['keyid'],
            'integrated_time':integrated,'registry_policy_sha256':hashlib.sha256(encoded).hexdigest(),
            'bundle_sha256':hashlib.sha256(bundle_raw).hexdigest(),'trusted_root_sha256':trusted_root_sha256,
            'cosign_sha256':cosign_sha256}
