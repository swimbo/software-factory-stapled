import json,pathlib,subprocess,sys,time,uuid,hashlib,os
root=pathlib.Path('/home/bo/.local/share/stapled-factory-control')
release=root/'releases/850259ae877e0cb171310e95d11719d9882e879e'
sys.path.insert(0,str(release/'stapled-factory/worker'))
sys.path.insert(0,str(release/'stapled-factory/deployment/nuc'))
from prepare_dispatch import prepare
from provision_runtime import provision_once,inspect_bound_allocation
from guarded_store import GuardedAttemptStore
from probes.container_negatives import verify
from baseline_trials import final_accounting
setup=root/'baseline-setup/context-v1-20261030'
unit='c9d1-unpaid-canary-20261030'
prepare(root=root/'dispatch',unit=unit,manifest=setup/'images.json',release=release,
    approval=setup/'image-approval.json',signature=setup/'image-approval.sig',trust_root=root/'image-approval-public.pem',
    attempt_db=setup/'canary-ledger/db',attempt_anchor=setup/'canary-anchor/marker',
    task_text='Unpaid isolation canary; no model run authorized.',context_profile='context-v1')
cfg=json.loads((root/'dispatch'/unit/'runtime.json').read_text())
cfg['provision']['proxy_command']+=['--telemetry-dir','/telemetry']
store=GuardedAttemptStore(setup/'canary-ledger/db',setup/'canary-anchor/marker')
request='a'*64;cid=str(uuid.uuid5(uuid.NAMESPACE_URL,unit));store.reserve(unit+':0',request,cid)
allocation=None
try:
 allocation=provision_once(store,unit+':0',request,cid,workspace_baseline=cfg['baseline'],gateway_key=b'unpaid-canary-dummy',**cfg['provision'])
 bound=inspect_bound_allocation(store,unit+':0',request,cid,workspace_baseline=cfg['baseline'],**cfg['provision'])
 worker,proxy=bound['worker_id'],bound['proxy_id']
 for _ in range(30):
  probe=subprocess.run(['docker','exec',proxy,'python3','-c',"import pathlib;assert pathlib.Path('/telemetry/telemetry.jsonl').exists()"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  if probe.returncode==0:break
  time.sleep(1)
 else:raise AssertionError('telemetry not initialized')
 permissions=json.loads(subprocess.check_output(['docker','exec',proxy,'python3','-c',"import os,pathlib,json;d=pathlib.Path('/telemetry');p=d/'telemetry.jsonl';assert d.stat().st_uid==os.getuid() and d.stat().st_mode&511==448;assert p.stat().st_uid==os.getuid() and p.stat().st_mode&511==384;print(json.dumps({'directory_mode':'0700','file_mode':'0600','owner_uid':os.getuid()}))"],text=True))
 verify(worker,setup)
 # Exercise normal binary startup and custom-module loading through HTTP. run=false
 # sends a message without starting the agent loop. The proxy has only a dummy key.
 import urllib.request
 payload=dict(cfg['payload']);payload['conversation_id']=cid
 payload['initial_message']={'role':'user','content':[{'type':'text','text':'Unpaid startup check; do not execute.'}],'run':False}
 client="import json,urllib.request,sys;data=json.loads(sys.argv[1]);r=urllib.request.Request('http://127.0.0.1:8000/api/conversations',json.dumps(data).encode(),headers={'X-Session-API-Key':'dummy','Content-Type':'application/json'});print(json.dumps(json.load(urllib.request.urlopen(r,timeout=30))))"
 for _ in range(30):
  check=subprocess.run(['docker','exec',worker,'python3','-c',client,json.dumps(payload)],capture_output=True,text=True)
  if check.returncode==0:break
  if 'ConnectionRefusedError' not in check.stderr:raise AssertionError(check.stderr[-1000:])
  time.sleep(1)
 else:raise AssertionError('normal AgentServer startup unavailable')
 info=json.loads(check.stdout);assert str(info['id'])==cid and info['execution_status']=='idle'

 subprocess.run(['docker','exec',worker,'python3','-c',"import pathlib;assert not pathlib.Path('/telemetry').exists();assert not pathlib.Path('/budget').exists();assert not pathlib.Path('/anchor').exists()"],check=True)
 with store._transaction() as (db,state):binding=json.loads(db.execute('SELECT binding FROM allocation_bindings WHERE attempt_id=?',(unit+':0',)).fetchone()[0])
 subprocess.run(['docker','stop',worker,proxy],check=True,stdout=subprocess.DEVNULL)
 final=final_accounting(binding,cfg)
 assert final['telemetry']['requests_observed']==0
 assert sum(row[3] for row in final['ledger']['budgets'])==0
 assert sum(row[-1] for row in final['ledger']['monetary_budgets'])==0
 network='factory-'+binding['allocation_id']+'-network' if 'allocation_id' in binding else None
 proof={'verified':True,'unit':unit,'release':release.name,'bound_original_allocation':True,
     'private_permissions':permissions,'networkless_worker_negatives_passed':True,
     'worker_telemetry_budget_anchor_paths_absent':True,'proxy_firewall_revalidated':True,
     'stopped_volume_accounting_verified':True,'model_requests':0,'resources_stopped':True,'normal_agent_server_http_startup':True,'custom_module_loaded_via_create_run_false':True,
     'images':{k:cfg['provision'][k] for k in ('worker_image','proxy_image','helper_image')},
     'telemetry_volume':[n for n,r in binding['volumes'].items() if r['Labels']['factory.role']=='telemetry'][0]}
 with store._transaction() as (db,state):alloc=db.execute('SELECT allocation_id FROM allocations WHERE attempt_id=?',(unit+':0',)).fetchone()[0]
 net=json.loads(subprocess.check_output(['docker','network','inspect','factory-'+alloc+'-network'],text=True))[0]
 assert net['Labels']['factory.allocation']==alloc and not net['Containers']
 subprocess.run(['docker','network','rm',net['Id']],check=True,stdout=subprocess.DEVNULL)
 proof['own_empty_network_released']=True
 p=setup/'unpaid-canary.json';p.write_text(json.dumps(proof,indent=2)+'\n');p.chmod(0o600);print(json.dumps(proof))
finally:
 if allocation:
  allocation['proxy_process'].terminate()
  for role in ('worker','proxy'):subprocess.run(['docker','stop',allocation[role]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
