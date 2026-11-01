"""Explicit operator wrapper for context-v1; no model retries or phase 9d.3."""
import hashlib,json,os,pathlib,shlex,subprocess,sys,uuid
RELEASE=pathlib.Path('/home/bo/.local/share/stapled-factory-control/releases/850259ae877e0cb171310e95d11719d9882e879e')
sys.path[:0]=[str(RELEASE/'stapled-factory/deployment/nuc'),str(RELEASE/'stapled-factory/worker'),str(pathlib.Path(__file__).parent)]
import baseline_trials as runner
from context_profile import apply_profile,profile_manifest
from audit_diagnostics import diagnostic_imports
SETUP=pathlib.Path(__file__).parent
CURRENT=None
# Retain only executed action inputs and bounded diagnostic flags, never thoughts.
runner.TRACE_CLIENT=runner.TRACE_CLIENT.replace('observed_bytes=0;', 'actions=[];missing_observed=False;observed_bytes=0;')
runner.TRACE_CLIENT=runner.TRACE_CLIENT.replace("'task_tracker') else", "'task_tracker','factory_capture') else")
runner.TRACE_CLIENT=runner.TRACE_CLIENT.replace("action=event.get('action') or {};command=action.get('command')", "action=event.get('action') or {};command=action.get('command');actions.append({k:action[k] for k in ('command','argv','operation','artifact_id','offset') if k in action})")
runner.TRACE_CLIENT=runner.TRACE_CLIENT.replace("if kind=='ObservationEvent':observed_bytes+=len(json.dumps(event.get('observation'),ensure_ascii=False).encode())", "if kind=='ObservationEvent':\n   observed_bytes+=len(json.dumps(event.get('observation'),ensure_ascii=False).encode());missing_observed=missing_observed or 'ModuleNotFoundError' in json.dumps(event.get('observation'))")
runner.TRACE_CLIENT=runner.TRACE_CLIENT.replace("'observation_serialized_bytes':observed_bytes", "'executed_actions':actions,'module_not_found_observed':missing_observed,'observation_serialized_bytes':observed_bytes")
original_trace=runner.trace_summary

def trace(worker,cid):
    result=original_trace(worker,cid)
    if not result.get('complete'):return result
    actions=result.pop('executed_actions')
    raw=json.dumps({'unit':CURRENT,'actions':actions,'module_not_found_observed':result['module_not_found_observed']},indent=2)+'\n'
    if len(raw.encode())>1024*1024:raise ValueError('action export limit')
    target=SETUP/'trace-exports';target.mkdir(mode=0o700,exist_ok=True)
    receipt=target/(CURRENT+'.json')
    if receipt.exists():
        if receipt.read_text()!=raw:raise ValueError('observed action trace changed; retain original')
    else:runner.write_new(receipt,raw)
    commands=[x['command'] if 'command' in x else shlex.join(x['argv']) for x in actions if 'command' in x or isinstance(x.get('argv'),list)]
    try:count=sum(diagnostic_imports(command) for command in commands)
    except (ValueError,SyntaxError):count=None
    result.update(dependency_diagnostic_commands=count,diagnostic_audit_version='baseline-contract-v2',actions_sha256=hashlib.sha256(raw.encode()).hexdigest())
    # Export authorized redacted artifacts before tmpfs disappears at stop.
    code=r'''import pathlib,os,stat,json,base64,hashlib,sys,uuid
p=pathlib.Path('/tmp')/('factory-output-'+str(uuid.UUID(sys.argv[1])))
files=[];total=0
if p.exists():
 assert not p.is_symlink() and p.stat().st_uid==os.getuid() and p.stat().st_mode&511==448
 for f in p.iterdir():
  if f.suffix not in ('.json','.data'):continue
  fd=os.open(f,os.O_RDONLY|os.O_NOFOLLOW)
  with os.fdopen(fd,'rb') as s:
   info=os.fstat(s.fileno());assert stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1 and not info.st_mode&63
   raw=s.read(1048577);assert len(raw)<=1048576
  total+=len(raw);assert total<=8388608
  files.append({'name':f.name,'sha256':hashlib.sha256(raw).hexdigest(),'data':base64.b64encode(raw).decode()})
print(json.dumps(files))'''
    import base64,re
    files=json.loads(subprocess.check_output(['docker','exec',worker,'python3','-c',code,cid],text=True,timeout=20))
    artifact_root=SETUP/'output-exports'/CURRENT;artifact_root.mkdir(mode=0o700,parents=True,exist_ok=True)
    for f in files:
        assert re.fullmatch('[0-9a-f]{32}\\.(?:json|data)',f['name'])
        data=base64.b64decode(f['data']);assert hashlib.sha256(data).hexdigest()==f['sha256']
        destination=artifact_root/f['name']
        if destination.exists():
            if destination.is_symlink() or destination.read_bytes()!=data:raise ValueError('output export changed')
            continue
        fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as s:s.write(data);s.flush();os.fsync(s.fileno())
    result['output_artifacts_exported_before_stop']=len([f for f in files if f['name'].endswith('.data')])
    result['output_artifacts_sha256']={f['name']:f['sha256'] for f in files}
    return result
runner.trace_summary=trace

def prepare(config):
    result=runner.prepare(config)
    suite_path=pathlib.Path(config['suite_dir'])/'suite.json';suite=json.loads(suite_path.read_text())
    for entry in suite['runs']:
        root=pathlib.Path(config['root'])/'dispatch'/entry['unit'];p=root/'runtime.json'
        runtime=json.loads(p.read_text());runtime['payload']=apply_profile(runtime['payload'],'context-v1')
        p.write_text(json.dumps(runtime,indent=2)+'\n');p.chmod(0o600)
        (root/'context-profile.json').write_text(json.dumps(profile_manifest('context-v1'),indent=2)+'\n');(root/'context-profile.json').chmod(0o600)
        entry['runtime_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    suite.update(context_profile='context-v1',phase_9d_3_started=False,primary_metric_interpretation='total_prompt_tokens_including_cached_per_contract_v2_accepted_task')
    suite_path.write_text(json.dumps(suite,indent=2)+'\n');suite_path.chmod(0o600)
    return result

def main():
    import argparse
    p=argparse.ArgumentParser(allow_abbrev=False);p.add_argument('--config',required=True);p.add_argument('action',choices=['prepare','run','close-abandoned']);p.add_argument('--unit');p.add_argument('--envelope');args=p.parse_args()
    config=runner.config_file(args.config)
    global CURRENT
    CURRENT=args.unit
    if args.action=='prepare':result=prepare(config)
    else:
        result=runner.run(config,args.unit) if args.action=='run' else runner.close_abandoned(config,args.unit,args.envelope)
        result=dict(result,context_profile='context-v1',phase_9d_2_started=True,phase_9d_3_started=False,acceptance_version='contract-corrected-v2')
        if result['task_id']=='stalled-run':result['accepted']=result['accepted'] and result['trace'].get('module_not_found_observed') is True
    print(json.dumps({'result':result}),flush=True)
if __name__=='__main__':main()
