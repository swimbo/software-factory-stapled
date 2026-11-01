import pathlib,json,hashlib,subprocess,urllib.request,urllib.error,time,os,fcntl,sqlite3
b=pathlib.Path('/home/bo/.local/share/stapled-factory-control');rev='850259ae877e0cb171310e95d11719d9882e879e';r=b/'releases'/rev
manifest=json.loads((r/'release-manifest.json').read_text());assert manifest['revision']==rev
for name,digest in manifest['files'].items():assert hashlib.sha256((r/name).read_bytes()).hexdigest()==digest,name
assert json.loads((r/'local-ci.json').read_text())['passed']
def command_output(args):return subprocess.check_output(args,text=True)
def get(unit):
 req=urllib.request.Request('http://127.0.0.1:19081/WorkUnit/'+unit+'/getState',b'{}',headers={'Content-Type':'application/json','Authorization':'Bearer '+(b/'ingress.token').read_text().strip()})
 return json.load(urllib.request.urlopen(req,timeout=5))
def store_snapshot():
 with sqlite3.connect('file:'+str(b/'attempt-store/attempts.db')+'?mode=ro',uri=True) as db:
  digest=hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest()
 return {'logical_store_sha256':digest,'anchor_sha256':hashlib.sha256((b/'attempt-anchor/watermark.json').read_bytes()).hexdigest()}
with (b/'operator.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);assert not (b/'operator-active.json').exists()
 units=['wall-count-batched-replay-20261030-01','wall-request-count-20261030-01','batch-spec-types-20261029-01','batch-fixture-json-20261029-01','batch-scan-cleanup-20261029-01','operator-wall-ports-replay-20261029-02','nuc-merged-runtime-20261028-01','nuc-version-example-20261028-02','nuc-wall-defaults-20261028-01','operator-nuc-readme-20261028-01']
 units += ['h9d-20261030-discovery-r1', 'h9d-20261030-small-edit-r1', 'h9d-20261030-multi-file-r1', 'h9d-20261030-failing-tests-r1-p2', 'h9d-20261030-noisy-output-r1', 'h9d-20261030-stalled-run-r1', 'h9d-20261030-discovery-r2', 'h9d-20261030-small-edit-r2', 'h9d-20261030-multi-file-r2', 'h9d-20261030-failing-tests-r2', 'h9d-20261030-noisy-output-r2', 'h9d-20261030-stalled-run-r2', 'h9d-20261030-discovery-r3', 'h9d-20261030-small-edit-r3', 'h9d-20261030-multi-file-r3', 'h9d-20261030-failing-tests-r3', 'h9d-20261030-noisy-output-r3', 'h9d-20261030-stalled-run-r3', 'h9d-20261030-failing-tests-r1']
 before={u:get(u) for u in units};store_before=store_snapshot()
 assert all(v['stage'] in (('abandoned',) if u=='batch-scan-cleanup-20261029-01' else ('validated','needs_validation','needs_reconciliation')) for u,v in before.items())
 recovered=json.loads((b/'approvals/batch-scan-cleanup-20261029-01/recovery.json').read_text())
 for role in ('worker','proxy'):assert not json.loads(command_output(['docker','inspect',recovered[role]]))[0]['State']['Running']
 out=b/'rollouts'/rev;out.mkdir(mode=0o700,parents=True,exist_ok=False)
 (out/'before.json').write_text(json.dumps(before,indent=2)+'\n')
 services=['factory-endpoint','factory-ingress'];old={}
 for s in services:
  p=pathlib.Path.home()/'.config/systemd/user'/(s+'.service');old[s]=p.read_text()
  expected={'factory-endpoint':'974aece75d62644b35b5f4e51928bb61cce140e2','factory-ingress':'974aece75d62644b35b5f4e51928bb61cce140e2'}
  assert '/releases/'+expected[s]+'/' in old[s]
  (out/(s+'.previous.service')).write_text(old[s]);(out/(s+'.previous.service')).chmod(0o600)
 try:
  for s in services:
   p=pathlib.Path.home()/'.config/systemd/user'/(s+'.service')
   p.write_text(old[s].replace('/releases/'+expected[s]+'/','/releases/'+rev+'/'))
  subprocess.run(['systemctl','--user','daemon-reload'],check=True)
  subprocess.run(['systemctl','--user','restart',*services],check=True)
  for _ in range(30):
   try:
    if {u:get(u) for u in units}==before:break
   except (OSError,ValueError):pass
   time.sleep(1)
  else:raise RuntimeError('retained state unavailable or changed')
  for s in services:
   pid=subprocess.check_output(['systemctl','--user','show',s,'-p','MainPID','--value'],text=True).strip()
   assert pathlib.Path('/proc',pid,'cwd').resolve()==r/'stapled-factory/spine'
  assert store_snapshot()==store_before,'guarded store or anchor changed during deployment'
  (out/'store-preservation.json').write_text(json.dumps(store_before,indent=2)+'\n')
  result={'guarded_store_and_anchor_unchanged':True,'revision':rev,'frozen_files_verified':len(manifest['files']),'manifest_sha256':hashlib.sha256((r/'release-manifest.json').read_bytes()).hexdigest(),'services_updated':services,'retained_states_unchanged':True,'rollback_directory':str(out),'shared_services_restarted':False}
  (out/'deployment.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
 except Exception:
  for s in services:(pathlib.Path.home()/'.config/systemd/user'/(s+'.service')).write_text(old[s])
  subprocess.run(['systemctl','--user','daemon-reload'],check=True);subprocess.run(['systemctl','--user','restart',*services],check=True);raise
