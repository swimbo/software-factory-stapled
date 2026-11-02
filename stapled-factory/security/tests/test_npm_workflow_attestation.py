import base64
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from npm_workflow_attestation import validate_statement


def test_signed_subject_must_match_package_identity_and_bytes():
    statement={'_type':'https://in-toto.io/Statement/v1','predicateType':'https://slsa.dev/provenance/v1',
               'subject':[{'name':'pkg:npm/%40scope/example@1.2.3','digest':{'sha512':'a'*128}}],
               'predicate':{'buildDefinition':{'buildType':'https://example.invalid/build','externalParameters':{}},
                            'runDetails':{'builder':{'id':'https://example.invalid/builder'}}}}
    bundle={'mediaType':'application/vnd.dev.sigstore.bundle.v0.3+json',
            'dsseEnvelope':{'payloadType':'application/vnd.in-toto+json',
                            'payload':base64.b64encode(json.dumps(statement).encode()).decode()}}
    raw=json.dumps(bundle).encode()
    validate_statement(raw,'@scope/example','1.2.3','a'*128)
    with pytest.raises(ValueError,match='subject'):
        validate_statement(raw,'@scope/other','1.2.3','a'*128)
    with pytest.raises(ValueError,match='subject'):
        validate_statement(raw,'@scope/example','1.2.3','b'*128)
    statement.pop('predicate')
    bundle['dsseEnvelope']['payload']=base64.b64encode(json.dumps(statement).encode()).decode()
    with pytest.raises(ValueError,match='predicate'):
        validate_statement(json.dumps(bundle).encode(),'@scope/example','1.2.3','a'*128)
