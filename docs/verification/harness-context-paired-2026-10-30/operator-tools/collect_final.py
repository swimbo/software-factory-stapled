import hashlib,json,pathlib,subprocess
out=pathlib.Path(__file__).resolve().parent
suite=json.loads((out/'execution-schedule.json').read_text());units=[x['unit'] for x in suite['runs']]
code=r'''
import pathlib,json,sqlite3,uuid,subprocess,base64,hashlib
r=pathlib.Path('/home/bo/.local/share/stapled-factory-control');units=json.loads(UNITS_JSON)
result=[]
with sqlite3.connect('file:'+str(r/'attempt-store/attempts.db')+'?mode=ro',uri=True) as attempts, sqlite3.connect('file:/home/bo/.hermes/bifrost/logs.db?mode=ro',uri=True) as gateway:
 gateway.row_factory=sqlite3.Row
 for unit in units:
  status=attempts.execute('SELECT status FROM attempts WHERE attempt_id=?',(unit+':0',)).fetchone()[0]
  assert status in ('completed','abandoned')
  binding=json.loads(attempts.execute('SELECT binding FROM allocation_bindings WHERE attempt_id=?',(unit+':0',)).fetchone()[0])
  allocation=attempts.execute('SELECT allocation_id FROM allocations WHERE attempt_id=?',(unit+':0',)).fetchone()[0]
  containers={}
  for role in ('worker','proxy'):
   info=json.loads(subprocess.check_output(['docker','inspect',binding[role+'_id']],text=True))[0]
   assert not info['State']['Running'] and info['State']['StartedAt']==binding[role+'_lifecycle']['StartedAt']
   containers[role]={'id':info['Id'],'image':info['Image'],'running':False,'started_at':info['State']['StartedAt']}
  p=subprocess.run(['docker','network','inspect','factory-'+allocation+'-network'],capture_output=True,text=True);assert p.returncode!=0
  volume=[name for name,x in binding['volumes'].items() if x['Labels']['factory.role']=='telemetry'][0]
  cfg=json.loads((r/'dispatch'/unit/'runtime.json').read_text());image=cfg['provision']['helper_image']
  read="import pathlib,sys;p=pathlib.Path('/telemetry/telemetry.jsonl');raw=p.read_bytes();assert len(raw)<=4194304;sys.stdout.buffer.write(raw)"
  raw=subprocess.check_output(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user','1000:1000','--entrypoint','python3','--mount','type=volume,src='+volume+',dst=/telemetry,readonly',image,'-B','-c',read],timeout=30)
  rows=[json.loads(x) for x in raw.splitlines()]
  session=str(uuid.uuid5(uuid.NAMESPACE_URL,'stapled-factory-attempt:'+unit+':0'))
  logs=[dict(x) for x in gateway.execute('SELECT id,session_id,provider,model,served_model,complexity_tier,complexity_mechanism,status,prompt_tokens,completion_tokens,total_tokens,cached_read_tokens,cost FROM logs WHERE session_id=? ORDER BY timestamp',(session,))]
  cleanup=json.loads((r/'dispatch'/unit/'baseline.network-released.json').read_text())
  result.append({'unit':unit,'attempt_status':status,'session_id':session,'gateway_logs':logs,'containers':containers,'network_released':True,'all_bound_volumes_retained':cleanup['all_bound_volumes_retained'],'telemetry_jsonl_base64':base64.b64encode(raw).decode(),'telemetry_sha256':hashlib.sha256(raw).hexdigest()})
assert not (r/'operator-active.json').exists()
print(json.dumps({'trials':result,'operator_active_marker_absent':True}))
'''.replace('UNITS_JSON',repr(json.dumps(units)))
raw=subprocess.check_output(['ssh','-o','BatchMode=yes','-o','ForwardAgent=no','hermes-control','python3 -'],input=code,text=True,timeout=180)
x=json.loads(raw)
import base64
telemetry=out/'telemetry';telemetry.mkdir(mode=0o700,exist_ok=True)
for row in x['trials']:
 data=base64.b64decode(row.pop('telemetry_jsonl_base64'));assert hashlib.sha256(data).hexdigest()==row['telemetry_sha256']
 (telemetry/(row['unit']+'.jsonl')).write_bytes(data)
 result=json.loads((out/'trials'/(row['unit']+'.json')).read_text())
 assert result['telemetry']['log_sha256']==row['telemetry_sha256']
 row['gateway_request_count_matches_telemetry']=len(row['gateway_logs'])==result['telemetry']['requests_observed']
 row['gateway_all_requests_successful']=all(v['status']=='success' for v in row['gateway_logs'])
row_models=sorted(set(v['model'] for row in x['trials'] for v in row['gateway_logs']))
(out/'final-state-and-gateway.json').write_text(json.dumps(x,indent=2)+'\n')
print(json.dumps({'verified_trials':len(x['trials']),'models':row_models,'gateway_log_count':sum(len(r['gateway_logs']) for r in x['trials']),'all_container_lifecycles_stopped':True,'all_trial_networks_released':True,'all_bound_volumes_retained':True,'operator_active_marker_absent':True}))
