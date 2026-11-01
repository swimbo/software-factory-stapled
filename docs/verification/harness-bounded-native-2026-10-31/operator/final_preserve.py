import pathlib,json,subprocess,urllib.request,sqlite3
root=pathlib.Path('/home/bo/.local/share/stapled-factory-control');rev='645cb597b45a21a89580f0d9b2ebb6a45c4f533e'
before=json.loads((root/'rollouts'/rev/'before.json').read_text())
def get(unit):
 request=urllib.request.Request('http://127.0.0.1:19081/WorkUnit/'+unit+'/getState',b'{}',headers={'Content-Type':'application/json','Authorization':'Bearer '+(root/'ingress.token').read_text().strip()})
 return json.load(urllib.request.urlopen(request,timeout=10))
after={u:get(u) for u in before};assert after==before
upgrade_before=json.loads((root/'rollouts/db96ddd55e8e197df195ac2a232904cfab10e628/before.json').read_text());assert {u:get(u) for u in upgrade_before}==upgrade_before
diagnostic_before=json.loads((root/'rollouts/eea52b0c32827555495fceb11568dfaf1fb342b8/before.json').read_text());assert {u:get(u) for u in diagnostic_before}==diagnostic_before
env_before=json.loads((root/'rollouts/049e51ab08af024b04ff8bbc2c2d9c25c4e2930f/before.json').read_text());assert {u:get(u) for u in env_before}==env_before
assert not (root/'operator-active.json').exists()
service=pathlib.Path.home()/'.config/systemd/user/factory-endpoint.service'
assert service.read_text()==(root/'dispatch/b9d3-canary-20261031-p2/baseline.previous.service').read_text().replace('/releases/645cb597b45a21a89580f0d9b2ebb6a45c4f533e/','/releases/049e51ab08af024b04ff8bbc2c2d9c25c4e2930f/')
with sqlite3.connect('file:'+str(root/'attempt-store/attempts.db')+'?mode=ro',uri=True) as db:
 rows=db.execute("SELECT attempt_id,status FROM attempts WHERE attempt_id LIKE 'b9d3-%' ORDER BY attempt_id").fetchall();assert len(rows)==24 and sum(x[1]=='completed' for x in rows)==19 and {x[0] for x in rows if x[1]=='reserved'}=={'b9d3-canary-20261031:0','b9d3-20261031-noisy-output-r1:0','b9d3-20261031-noisy-output-r1-p2:0','b9d3-20261031-stalled-run-r1:0','b9d3-20261031-discovery-r2:0'}
 b=json.loads(db.execute('SELECT binding FROM allocation_bindings WHERE attempt_id=?',('b9d3-canary-20261031-p2:0',)).fetchone()[0])
 for role in ('worker','proxy'):
  info=json.loads(subprocess.check_output(['docker','inspect',b[role+'_id']],text=True))[0];assert not info['State']['Running'] and info['State']['StartedAt']==b[role+'_lifecycle']['StartedAt']
result={'schema':'factory.bounded-native-final-preservation/v1','legacy_workunit_states_verified_unchanged':len(before),'pre_upgrade_workunit_states_verified_unchanged':len(upgrade_before),'pre_diagnostic_workunit_states_verified_unchanged':len(diagnostic_before),'pre_environment_fix_workunit_states_verified_unchanged':len(env_before),'legacy_states':after,'paired_and_paid_canary_attempts':rows,'all_19_model_attempts_completed':True,'original_prebinding_failures_reserved':5,'original_task_service_binding_restored':True,'operator_active_marker_absent':True,'paid_canary_containers_still_stopped':True,'phase_9d_3_started':True,'phase_9d_4_started':False}
p=root/'baseline-setup/bounded-native-v1-20261031/final-preservation.json';p.write_text(json.dumps(result,indent=2)+'\n');p.chmod(0o600)
print(json.dumps({'legacy_states_unchanged':len(before),'model_attempts_completed':sum(x[1]=='completed' for x in rows),'reserved_prebinding_failures':sum(x[1]=='reserved' for x in rows),'original_binding_restored':True,'operator_active_marker_absent':True}))
