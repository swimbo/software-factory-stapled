import base64
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import npm_registry_attestation as registry


def fixture():
    keyid='SHA256:DhQ8wR5APBvFHLF/+Tc+AYvPOdTpcIDqOhxsBHRwC7U'
    key=('MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEY6Ya7W++7aUPzvMTrezH6Ycx'
         '3c+HOKYCcNGybJZSCJq/fd7Qa8uuAKtdIkUQtQiEKERhAmE5lMMJhP8OkDOa2g==')
    policy={'keyid':keyid,'key':key,'valid_from':'2025-01-13T00:00:00Z','valid_until':None,
            'registry':'https://registry.npmjs.org'}
    statement={'_type':'https://in-toto.io/Statement/v0.1',
        'predicateType':'https://github.com/npm/attestation/tree/main/specs/publish/v0.1',
        'subject':[{'name':'pkg:npm/example@1.2.3','digest':{'sha512':'a'*128}}],
        'predicate':{'name':'example','version':'1.2.3','registry':'https://registry.npmjs.org'}}
    bundle={'mediaType':'application/vnd.dev.sigstore.bundle+json;version=0.2',
        'verificationMaterial':{'publicKey':{'hint':keyid},'tlogEntries':[{'integratedTime':'1737000000'}]},
        'dsseEnvelope':{'payloadType':'application/vnd.in-toto+json','payload':base64.b64encode(json.dumps(statement).encode()).decode(),
                        'signatures':[{'keyid':keyid,'sig':'fixture'}]}}
    return policy,json.dumps(bundle).encode()


def test_publish_statement_binds_registry_package_and_signing_key():
    policy,raw=fixture()
    registry.validate_publish_statement(raw,'example','1.2.3','a'*128,policy)
    with pytest.raises(ValueError,match='subject'):
        registry.validate_publish_statement(raw,'other','1.2.3','a'*128,policy)
    with pytest.raises(ValueError,match='validity'):
        registry.validate_publish_statement(raw,'example','1.2.3','a'*128,{**policy,'valid_from':'2026-01-01T00:00:00Z'})


def test_receipt_binds_the_policy_snapshot_checked_before_verification(tmp_path,monkeypatch):
    policy,raw=fixture();original=json.dumps(policy,sort_keys=True,separators=(',',':')).encode()
    bundle=tmp_path/'bundle';bundle.write_bytes(raw);root=tmp_path/'root';root.write_bytes(b'root')
    def verify(*args,**kwargs):policy['valid_from']='2026-01-01T00:00:00Z'
    monkeypatch.setattr(registry,'_verify_cosign',verify)
    result=registry.verify_registry_attestation(package=tmp_path/'package',bundle=bundle,name='example',version='1.2.3',
        expected_sha512='a'*128,policy=policy,trusted_root=root,trusted_root_sha256=hashlib.sha256(b'root').hexdigest(),
        cosign='/unused',cosign_sha256='b'*64)
    assert result['registry_policy_sha256']==hashlib.sha256(original).hexdigest()
    assert result['policy_authentication']=='not_evaluated'
