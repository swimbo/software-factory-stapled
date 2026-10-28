import json, pathlib, sqlite3, subprocess, sys, time, urllib.request, urllib.error
root=pathlib.Path('/home/bo/.local/share/stapled-factory-control')
unit=sys.argv[1]
factory_revision=sys.argv[2]
pid=subprocess.check_output(['systemctl','--user','show','factory-endpoint','-p','MainPID','--value'],text=True).strip()
assert pathlib.Path('/proc/'+pid+'/cwd').resolve()==root/'releases'/factory_revision/'stapled-factory/spine'
config=json.loads((root/'dispatch'/unit/'runtime.json').read_text())
token=(root/'ingress.token').read_text().strip()
def post(route,data,timeout=15):
 req=urllib.request.Request('http://127.0.0.1:19081/'+route,data=json.dumps(data).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=timeout) as response:return json.load(response)
request={'unitId':unit,'target':'http://127.0.0.1:1','scenarios':'operator-canary-no-wall-acceptance','targetRepo':config['repo'],'taskFile':config['task_file']}
initial=post('WorkUnit/'+unit+'/getState',{})
if initial.get('stage')=='unclaimed':
 try:post('orchestrate/'+unit+'/run',request)
 except (TimeoutError,urllib.error.URLError):print('Dispatch response uncertain; observing same attempt without redispatch',flush=True)
else:print('Observing existing task without dispatch',flush=True)
last=None
for i in range(48):
 state=post('WorkUnit/'+unit+'/getState',{})
 if state.get('stage')!=last:print('Stage: '+str(state.get('stage')),flush=True);last=state.get('stage')
 if state.get('stage')=='needs_validation':break
 if state.get('stage')=='failed':raise RuntimeError('canary failed; no automatic retry')
 time.sleep(5)
else:raise RuntimeError('canary outcome unresolved; retain state without redispatch')
revision=state['build']['git_revision'];repo=config['repo']
assert subprocess.check_output(['git','-C',repo,'rev-parse','HEAD'],text=True).strip()==revision
assert not subprocess.check_output(['git','-C',repo,'status','--porcelain'],text=True).strip()
assert pathlib.Path(repo,'dispatch-proof.txt').read_text()=='NUC Restate OpenHands dispatch verified\n'
def snapshot():
 with sqlite3.connect('file:'+str(root/'attempt-store/attempts.db')+'?mode=ro',uri=True) as db:
  row=db.execute('SELECT status,result FROM attempts WHERE attempt_id=?',(unit+':0',)).fetchone()
  binding=json.loads(db.execute('SELECT binding FROM allocation_bindings WHERE attempt_id=?',(unit+':0',)).fetchone()[0])
  allocation=db.execute('SELECT allocation_id FROM allocations WHERE attempt_id=?',(unit+':0',)).fetchone()[0]
  metadata=db.execute('SELECT store_id,generation FROM metadata').fetchall()
 assert row[0]=='completed' and json.loads(row[1])['git_revision']==revision
 containers={}
 for role in ('worker','proxy'):
  info=json.loads(subprocess.check_output(['docker','inspect',binding[role+'_id']],text=True))[0]
  containers[role]={'id':info['Id'],'image':info['Image'],'running':info['State']['Running'],'pid':info['State']['Pid'],'started':info['State']['StartedAt']}
 script="import sqlite3,json;d=sqlite3.connect('file:/budget/b.db?mode=ro',uri=True);print(json.dumps({t:d.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in ('budgets','monetary_budgets')}))"
 ledger=json.loads(subprocess.check_output(['docker','exec',binding['proxy_id'],'python3','-c',script],text=True))
 return {'state':post('WorkUnit/'+unit+'/getState',{}),'attempt':row,'metadata':metadata,'anchor':json.loads((root/'attempt-anchor/watermark.json').read_text()),'containers':containers,'allocation_id':allocation,'budget':ledger}
before=snapshot();duplicate_http=200
try:post('orchestrate/'+unit+'/run',request)
except urllib.error.HTTPError as exc:
 if exc.code!=409:raise
 duplicate_http=409
time.sleep(2);after=snapshot();assert before==after,'duplicate dispatch changed durable state or resources'
result={'unit':unit,'worker_revision':revision,'factory_revision':factory_revision,'state':state,'duplicate_dispatch_unchanged':True,'duplicate_http_status':duplicate_http,'runtime_snapshot':after,'best_effort_budget':True,'actual_charges':'unknown','scope':'operator-selected sandbox task; needs_validation; no candidate acceptance or enterprise promotion'}
output=root/'dispatch'/unit/'verified-dispatch.json';output.write_text(json.dumps(result,indent=2)+'\n');output.chmod(0o600)
print(json.dumps({'verified':True,'unit':unit,'revision':revision,'duplicate_dispatch_unchanged':True,'budget':after['budget']}),flush=True)
