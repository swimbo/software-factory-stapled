"""Operator-only actual Restate acceptance controls using prior candidate evidence.
Fixture seeding uses private raw ingress, never exposes state mutation publicly.
Run on trusted NUC only after validate_prebuilt produced results.json.
"""
import argparse
import hashlib
import hmac
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
root=a.root;results=json.loads((root/'validation-candidates/results.json').read_text());key=(root/'validation.key').read_bytes()
unit='validation-'+uuid.uuid4().hex

def call(method,body,unit_id=unit,base='http://127.0.0.1:18080',auth=False):
    headers={'Content-Type':'application/json'}
    if auth:headers['Authorization']='Bearer '+(root/'ingress.token').read_text()
    req=urllib.request.Request(base+'/WorkUnit/'+unit_id+'/'+method,json.dumps(body).encode(),headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=20) as r:
            raw=r.read();return r.status,json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:return e.code,e.read().decode()

def envelope(evidence):
    payload=json.dumps(evidence,sort_keys=True,separators=(',',':'))
    return dict(payload=payload,signature=hmac.new(key,payload.encode(),hashlib.sha256).hexdigest())

revision=results['good']['git_revision']
assert call('claim','validation-operator')[1] is True
call('transition','building')
call('recordRecoveryContext',dict(request=dict(unitId=unit),attemptDb=str(root/'attempt-store/attempts.db')))
call('recordBuild',dict(attempt_id=unit+':0',status='completed',git_revision=revision))
call('transition','needs_validation')
evidence=dict(unit_id=unit,attempt_id=unit+':0',git_revision=revision,passed=True,scope='disposable-candidate',
    artifact_digest=results['good']['artifact_digest'],deployment_id=results['good']['deployment_id'],
    scenario_digest=results['good']['scenario_digest'],security_engines=results['good']['security']['engines'])
assert call('acceptValidation',envelope(evidence),base='http://127.0.0.1:19081',auth=True)[0]==404
for changed in (dict(evidence,unit_id='other'),dict(evidence,attempt_id=unit+':0:successor'),dict(evidence,git_revision='0'*40)):
    assert call('acceptValidation',envelope(changed))[1] is False
for changed in (dict(evidence,passed=False),dict(evidence,artifact_digest='invalid')):
    assert call('acceptValidation',envelope(changed))[0]>=400
bad=envelope(evidence);bad['payload']=json.dumps(dict(evidence,deployment_id='tampered'))
started=time.monotonic();assert call('acceptValidation',bad)[0]>=400
assert time.monotonic()-started<10,'invalid signature retried rather than terminated'
assert call('getState',None)[1]['stage']=='needs_validation'
assert call('acceptValidation',envelope(evidence))[1] is True
assert call('acceptValidation',envelope(evidence))[1] is False
state=call('getState',None)[1];assert state['stage']=='validated';assert state['validation']==evidence
print(json.dumps(dict(unit=unit,accepted=True,replay_rejected=True,tamper_rejected=True,stale_rejected=True,public_route_denied=True,validation=state['validation'])))
