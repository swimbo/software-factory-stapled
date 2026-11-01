import pathlib,json,subprocess,urllib.request,sqlite3
root=pathlib.Path('/home/bo/.local/share/stapled-factory-control');rev='850259ae877e0cb171310e95d11719d9882e879e'
before=json.loads((root/'rollouts'/rev/'before.json').read_text())
def get(unit):
 request=urllib.request.Request('http://127.0.0.1:19081/WorkUnit/'+unit+'/getState',b'{}',headers={'Content-Type':'application/json','Authorization':'Bearer '+(root/'ingress.token').read_text().strip()})
 return json.load(urllib.request.urlopen(request,timeout=10))
after={u:get(u) for u in before};assert after==before
assert not (root/'operator-active.json').exists()
service=pathlib.Path.home()/'.config/systemd/user/factory-endpoint.service'
assert service.read_text()==(root/'dispatch/c9d1-canary-20261030/baseline.previous.service').read_text()
with sqlite3.connect('file:'+str(root/'attempt-store/attempts.db')+'?mode=ro',uri=True) as db:
 rows=db.execute("SELECT attempt_id,status FROM attempts WHERE attempt_id LIKE 'c9d1-%' ORDER BY attempt_id").fetchall();assert len(rows)==19 and all(x[1]=='completed' for x in rows)
 b=json.loads(db.execute('SELECT binding FROM allocation_bindings WHERE attempt_id=?',('c9d1-canary-20261030:0',)).fetchone()[0])
 for role in ('worker','proxy'):
  info=json.loads(subprocess.check_output(['docker','inspect',b[role+'_id']],text=True))[0];assert not info['State']['Running'] and info['State']['StartedAt']==b[role+'_lifecycle']['StartedAt']
result={'schema':'factory.context-final-preservation/v1','legacy_workunit_states_verified_unchanged':len(before),'legacy_states':after,'paired_and_paid_canary_attempts':rows,'all_19_real_attempts_completed':True,'original_task_service_binding_restored':True,'operator_active_marker_absent':True,'paid_canary_containers_still_stopped':True,'phase_9d_3_started':False}
p=root/'baseline-setup/context-v1-20261030/final-preservation.json';p.write_text(json.dumps(result,indent=2)+'\n');p.chmod(0o600)
print(json.dumps({'legacy_states_unchanged':len(before),'real_attempts_completed':len(rows),'original_binding_restored':True,'operator_active_marker_absent':True}))
