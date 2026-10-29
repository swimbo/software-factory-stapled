import hashlib,json,os,pathlib,sqlite3,subprocess,sys,time,types,uuid
root=pathlib.Path('/home/bo/.local/share/stapled-factory-control');rev='36cec5b2d3d52ebeb8273d255e3c0bb2469f51d0';sys.path.insert(0,str(root/'releases'/rev/'stapled-factory/worker'))
from operator_cli import load_config,_credential
from provision_runtime import provision
os.environ['FACTORY_IMAGE_APPROVAL_KEY']=str(root/'image-approval-public.pem');os.environ['FACTORY_GATEWAY_KEY_FILE']='/home/bo/.hermes/bifrost/virtual-key-claude-desktop-1.txt'
cases=[('simple','Reply with exactly OK.'),('complex','Design a distributed financial ledger protocol that preserves linearizability and exactly-once monetary effects despite duplicated messages, network partitions, clock skew, concurrent debits, coordinator crashes between committing a database transaction and publishing an outbox record, and restoring stale snapshots. Explain the fencing and reconciliation invariants and a counterexample to naive retries. Give a compact high-level protocol.')]
results=[]
for name,prompt in cases:
 unit='nuc-router-'+name+'-20261028-01';path=root/'dispatch'/unit;cfg=json.loads((path/'runtime.json').read_text());os.environ['FACTORY_RUNTIME_CONFIG']=str(path/'runtime.json')
 load_config(types.SimpleNamespace(attempt_id=cfg['attempt_id'],task_file=cfg['task_file'],repo=cfg['repo']))
 a=None
 try:
  a=provision(**cfg['provision'],gateway_key=_credential())
  for _ in range(30):
   ready=subprocess.run(['docker','exec',a['worker'],'python3','-c',"import socket;s=socket.create_connection(('127.0.0.1',8123),timeout=1);s.close()"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   if ready.returncode==0:break
   time.sleep(1)
  else:raise RuntimeError('bridge unavailable before route check')
  body=json.dumps({'model':'auto','max_tokens':128,'messages':[{'role':'user','content':prompt}]}).encode()
  code="import json,sys,urllib.request,urllib.error;r=urllib.request.Request('http://127.0.0.1:8123/v1/messages',data=sys.stdin.buffer.read(),headers={'Content-Type':'application/json'});\ntry:\n s=urllib.request.urlopen(r,timeout=100);d=json.load(s);print(json.dumps({'http_status':s.status,'model':d.get('model'),'usage':d.get('usage')}))\nexcept urllib.error.HTTPError as e:print(json.dumps({'http_status':e.code}))"
  response=json.loads(subprocess.check_output(['docker','exec','-i',a['worker'],'python3','-c',code],input=body,timeout=110))
  session=str(uuid.uuid5(uuid.NAMESPACE_URL,'stapled-factory-attempt:'+cfg['attempt_id']))
  for _ in range(10):
   with sqlite3.connect('file:/home/bo/.hermes/bifrost/logs.db?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row;logs=[dict(r) for r in db.execute('SELECT id,session_id,provider,model,routing_rule_name,complexity_tier,complexity_mechanism,status,cost FROM logs WHERE session_id=? ORDER BY timestamp',(session,))]
   if any('deepseek' in r.get('model','') for r in logs):break
   time.sleep(1)
  budgetcode="import json,sqlite3;d=sqlite3.connect('file:/budget/b.db?mode=ro',uri=True);print(json.dumps({'requests':d.execute('SELECT SUM(used) FROM budgets').fetchone()[0],'estimated_microdollars':d.execute('SELECT SUM(spent) FROM monetary_budgets').fetchone()[0]}))"
  ledger=json.loads(subprocess.check_output(['docker','exec',a['proxy'],'python3','-c',budgetcode],text=True))
  result={'case':name,'unit':unit,'session_id':session,'response':response,'gateway_logs':logs,'request_body_bytes':len(body),'ledger':ledger,'allocation_id':a['allocation_id'],'retained_budget_volume':a['budget_volume'],'retained_anchor_volume':a['anchor_volume'],'factory_revision':rev,'scope':'one bounded live route check through signed production proxy, not an OpenHands conversation','actual_charges':'unknown; null gateway costs are not zero'}
  (path/'route-check.json').write_text(json.dumps(result,indent=2)+'\n');results.append(result)
  assert response['http_status']==200,result['response']
  selected=[r for r in logs if 'deepseek' in r.get('model','')]
  assert any(('v4-pro' if name=='complex' else 'flash') in r['model'] for r in selected),'expected route not observed'
  assert ledger['requests']==1
  print(json.dumps({'case':name,'session_id':session,'models':[r['model'] for r in selected],'tiers':[r['complexity_tier'] for r in selected],'ledger':ledger}),flush=True)
 finally:
  if a:
   a['proxy_process'].terminate()
   for role in ('worker','proxy'):subprocess.run(['docker','rm','-f',a[role]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   subprocess.run(['docker','network','rm',a['network']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   subprocess.run(['docker','volume','rm',a['socket_volume']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
print('PASS both live complexity routes; paid budget/anchor volumes retained',flush=True)
