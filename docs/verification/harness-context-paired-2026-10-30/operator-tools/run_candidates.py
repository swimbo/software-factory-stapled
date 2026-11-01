import base64,json,pathlib,re,shlex,subprocess,sys
out=pathlib.Path(__file__).resolve().parent
config=json.loads((out/'config.json').read_text());setup=json.loads((out/'release.json').read_text())['setup']
runs=out/'trials';runs.mkdir(mode=0o700,exist_ok=True)
ssh=['ssh','-o','BatchMode=yes','-o','ForwardAgent=no','hermes-control']
def invoke(unit,action='run',envelope=None,canary=False):
 argv=['python3',setup+'/candidate_trials.py','--config',setup+('/canary-config.json' if canary else '/config.json'),action,'--unit',unit]
 if envelope:argv+=['--envelope',envelope]
 process=subprocess.Popen(ssh+[shlex.join(argv)],stdout=subprocess.PIPE,text=True)
 result=None
 with (runs/(unit+'.log')).open('a') as log:
  for line in process.stdout:
   log.write(line);log.flush();value=json.loads(line)
   if 'result' in value:result=value['result']
   else:print(json.dumps(value),flush=True)
 if process.wait() or result is None:raise RuntimeError('Observer interrupted; only reconcile this original unit: '+unit)
 return result
entries=json.loads((out/('canary-suite.json' if '--canary' in sys.argv else 'suite.json')).read_text())['runs']
for index,entry in enumerate(entries,1):
 unit=entry['unit'];assert re.fullmatch('[A-Za-z0-9_-]{1,80}',unit)
 path=runs/(unit+'.json')
 if path.exists():result=json.loads(path.read_text())
 else:
  print(json.dumps({'trial':index,'of':len(entries),'unit':unit,'action':'run_or_observe_original'}),flush=True)
  result=invoke(unit,canary='--canary' in sys.argv)
  if result.get('requires_abandonment'):
   reason='Bounded candidate trial did not naturally finish; original containers stopped and all evidence retained.'
   helper=config['release']+'/stapled-factory/worker/abandon_attempt.py'
   payload=subprocess.check_output(ssh+[shlex.join(['python3',helper,'--db',config['root']+'/attempt-store/attempts.db','--anchor',config['root']+'/attempt-anchor/watermark.json','--unit',unit,'--reason='+reason])],text=True).strip()
   data=runs/(unit+'.abandon-payload');data.write_text(payload);data.chmod(0o600);signature=runs/(unit+'.abandon.sig')
   subprocess.run(['openssl','dgst','-sha256','-sign','/Users/bo/.local/share/stapled-factory-release/keys/image-approval.pem','-out',str(signature),str(data)],check=True)
   envfile=runs/(unit+'.abandon.json');envfile.write_text(json.dumps({'payload':payload,'signature':base64.b64encode(signature.read_bytes()).decode()}));envfile.chmod(0o600)
   remote=setup+'/'+envfile.name;subprocess.run(['scp','-q',str(envfile),'hermes-control:'+remote],check=True)
   result=invoke(unit,'close-abandoned',remote,canary='--canary' in sys.argv)
  with path.open('x') as s:json.dump(result,s,indent=2);s.write('\n')
  path.chmod(0o600)
 subprocess.run(ssh+[shlex.join(['python3',setup+'/release_network.py',unit])],check=True,capture_output=True,text=True)
 print(json.dumps({'trial':index,'of':len(entries),'unit':unit,'accepted':result['accepted'],'stage':result['state']['stage'],
 'requests':sum(r[3] for r in result['budget']['budgets']),'reserved_usd':sum(r[-1] for r in result['budget']['monetary_budgets'])/1e6,
 'elapsed_s':result['task_wall_elapsed_s'],'trace_tools':result['trace']['tool_actions'],'profile':'context-v1','telemetry_reservation_parity':result['reservation_count_matches_telemetry']}),flush=True)
 if '--canary' in sys.argv:
  assert result['accepted'] and result['trace']['complete'] and result['trace']['tool_actions'].get('factory_capture',0)>=2 and result['trace']['output_artifacts_exported_before_stop']>=1,'Canary not accepted; do not start paired trials'
print(json.dumps({'completed':len(entries),'phase_9d_3_started':False}),flush=True)
