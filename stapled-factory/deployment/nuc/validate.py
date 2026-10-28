"""Operator-only exact-commit Docker validation and signed Restate submission.
Run beside trusted Docker; never mount this authority key into worker containers.
"""
import argparse
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import stat
import urllib.request

p=argparse.ArgumentParser()
for name in ('repo','revision','baseline','scenarios','entrypoint','runtime-image','unit','attempt','key-file'):
    p.add_argument('--'+name,required=True)
p.add_argument('--prebuilt-image',required=True)
p.add_argument('--source-archive',required=True)
p.add_argument('--diff-file',required=True)
p.add_argument('--build-manifest',required=True)
p.add_argument('--build-signature',required=True)

p.add_argument('--ingress',default='http://127.0.0.1:18080')
a=p.parse_args()
keyfile=Path(a.key_file)
st=keyfile.lstat()
if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_mode&0o077:
    raise SystemExit('owner-only validation key required')
key=keyfile.read_bytes()
if len(key)<32:raise SystemExit('validation key must contain at least32bytes')
module=Path(__file__).resolve().parents[2]/'spine/prebuilt_validation.py'
spec=importlib.util.spec_from_file_location('prebuilt_validation',module)
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
result=adapter.validate_prebuilt(a.source_archive,a.revision,a.prebuilt_image,a.scenarios,a.diff_file,a.repo,a.baseline,a.build_manifest,a.build_signature,os.environ['FACTORY_BUILD_APPROVAL_PUBLIC_KEY'])
if not result['passed']:raise SystemExit('validation rejected; no signature issued')
payload=json.dumps(dict(unit_id=a.unit,attempt_id=a.attempt,git_revision=result['git_revision'],
    deployment_id=result['deployment_id'],artifact_digest=result['artifact_digest'],
    scenario_digest=result['scenario_digest'],scope='disposable-candidate',security_engines=result['security']['engines'],passed=True),sort_keys=True,separators=(',',':'))
envelope=json.dumps(dict(payload=payload,signature=hmac.new(key,payload.encode(),hashlib.sha256).hexdigest())).encode()
# The public bearer ingress intentionally has no acceptValidation route.
import urllib.parse
url=a.ingress+'/WorkUnit/'+urllib.parse.quote(a.unit,safe='')+'/acceptValidation'
request=urllib.request.Request(url,envelope,headers={'Content-Type':'application/json'})
with urllib.request.urlopen(request,timeout=30) as response:
    accepted=json.load(response)
if accepted is not True:raise SystemExit('stale or mismatched validation rejected')
print(json.dumps(dict(accepted=True,git_revision=result['git_revision'],artifact_digest=result['artifact_digest'])))
