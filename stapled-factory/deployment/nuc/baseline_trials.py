"""Trusted operator-only baseline trials; no optimization, publishing or retries.

Run on the NUC from a signed frozen release. Each action is explicit. The
operator signs prepared inputs on the Mac before any run action is permitted.
"""
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

WORKER = Path(__file__).resolve().parents[2]/'worker'
sys.path.insert(0, str(WORKER))


def write_new(path, value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write(value if isinstance(value,str) else json.dumps(value,indent=2)+'\n')
        stream.flush();os.fsync(stream.fileno())
    fd=os.open(Path(path).parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def command(*args, timeout=60):
    return subprocess.check_output(list(map(str,args)),text=True,stderr=subprocess.DEVNULL,timeout=timeout).strip()


def schedule(tasks,prefix):
    return [{'unit':f'{prefix}-{task["id"]}-r{repeat}', 'task_id':task['id'],'repeat':repeat}
            for repeat in (1,2,3) for task in tasks]


def in_scope(changed,allowed):
    return bool(changed) and set(changed)<=set(allowed)


def dispatch_once(intent,unit,request,post):
    if intent.exists():
        if json.loads(intent.read_text())['request'] != request:
            raise ValueError('dispatch intent differs; reconcile original')
        return post('WorkUnit/'+unit+'/getState',{})
    if post('WorkUnit/'+unit+'/getState',{}).get('stage')!='unclaimed':
        raise ValueError('unit already used without matching intent')
    write_new(intent,{'request':request,'started_at_unix':time.time()})
    try:post('orchestrate/'+unit+'/run',request)
    except (OSError,ValueError):pass  # An accepted request must never be repeated.
    return post('WorkUnit/'+unit+'/getState',{})


def post(root,route,value,raw=False):
    port=18080 if raw else 19081
    headers={'Content-Type':'application/json'}
    if not raw:headers['Authorization']='Bearer '+(root/'ingress.token').read_text().strip()
    request=urllib.request.Request(f'http://127.0.0.1:{port}/'+route,json.dumps(value).encode(),headers=headers)
    with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)


def config_file(path):
    from operator_cli import _private_file
    return json.loads(_private_file(str(Path(path).absolute())))


def prepare(config):
    from prepare_dispatch import prepare as prepare_attempt
    root=Path(config['root']);out=Path(config['suite_dir']);release=Path(config['release'])
    out.mkdir(mode=0o700,parents=True,exist_ok=False)
    tasks=json.loads((WORKER/'baseline/tasks.json').read_text())
    seed=out/'seed';shutil.copytree(WORKER/'baseline/seed',seed);seed.chmod(0o700)
    command('git','init','-q',seed)
    command('git','-C',seed,'config','user.name','Factory Baseline')
    command('git','-C',seed,'config','user.email','factory@example.invalid')
    command('git','-C',seed,'add','--all')
    env={**os.environ,'GIT_AUTHOR_DATE':'2026-10-30T00:00:00Z','GIT_COMMITTER_DATE':'2026-10-30T00:00:00Z'}
    subprocess.run(['git','-C',str(seed),'commit','-q','-m','Frozen Phase 9d development baseline'],env=env,check=True)
    baseline=command('git','-C',seed,'rev-parse','HEAD')
    entries=schedule(tasks['tasks'],config['prefix'])
    task_map={t['id']:t for t in tasks['tasks']}
    for entry in entries:
        task=task_map[entry['task_id']];unit=entry['unit']
        text=task['prompt']+'\n\nChange only: '+', '.join(task['allowed_paths'])+'. Commit the scoped change and finish with a clean worktree. Do not publish, merge or deploy.\n'
        text+=('The worker has no network access. Do not install dependencies. Use python3 -B for Python checks so no bytecode files are generated. '
               'Do not run the unrelated intentionally failing checks unless the task requests them.\n'
               'Reduce model round trips by batching independent file reads and searches into one terminal call. '
               'After edits, combine available checks, git diff --check, the scoped commit, and final git status in one terminal call, '
               'stopping on any failure. Keep required inspection and verification; do not skip them to save calls.\n')
        prepare_attempt(root=root/'dispatch',unit=unit,manifest=config['images'],release=release,
            approval=config['approval'],signature=config['signature'],trust_root=config['trust_root'],
            attempt_db=root/'attempt-store/attempts.db',attempt_anchor=root/'attempt-anchor/watermark.json',task_text=text)
        trial=root/'dispatch'/unit
        for folder in ('workspace','successor'):
            command('git','-C',trial/folder,'fetch','-q',seed,'HEAD')
            command('git','-C',trial/folder,'checkout','-q','-B','factory-task',baseline)
        path=trial/'runtime.json';runtime=json.loads(path.read_text());runtime['baseline']=baseline
        proxy=runtime['provision']['proxy_command'];proxy+=['--telemetry-dir','/telemetry']
        proxy[proxy.index('--attempt-fingerprint')+1]=hashlib.sha256((unit+'\n'+baseline+'\n'+text).encode()).hexdigest()
        proxy[proxy.index('--pricing-policy-expires-at')+1]=str(config['expires_at'])
        path.write_text(json.dumps(runtime,indent=2)+'\n');path.chmod(0o600)
        entry.update(baseline=baseline,task_sha256=hashlib.sha256(text.encode()).hexdigest(),
                     allowed_paths=task['allowed_paths'],runtime_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    manifest={'schema':'factory.baseline-trials/v1','release':str(release),'baseline':baseline,
        'suite_sha256':hashlib.sha256((WORKER/'baseline/tasks.json').read_bytes()).hexdigest(),
        'primary_metric':tasks['primary_metric'],'authorization':config['authorization'],
        'runs':entries,'estimated_target_per_trial_usd':2,'actual_charge_cap':False}
    write_new(out/'suite.json',manifest)
    return {'prepared':len(entries),'baseline':baseline,'suite':str(out/'suite.json')}


def trial_binding(root,unit):
    with sqlite3.connect('file:'+str(root/'attempt-store/attempts.db')+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT binding FROM allocation_bindings WHERE attempt_id=?',(unit+':0',)).fetchone()
        allocation=db.execute('SELECT allocation_id FROM allocations WHERE attempt_id=?',(unit+':0',)).fetchone()
    if not row or not allocation:raise ValueError('bound allocation unavailable')
    binding=json.loads(row[0])
    for role in ('worker','proxy'):
        info=json.loads(command('docker','inspect',binding[role+'_id']))[0]
        if (info['Config']['Labels'].get('factory.allocation')!=allocation[0] or
                info['Config']['Labels'].get('factory.role')!=role or
                info['State'].get('StartedAt')!=binding[role+'_lifecycle']['StartedAt']):
            raise ValueError('bound allocation identity mismatch')
    return binding




def freeze_candidate(repo,baseline,revision,destination):
    from revision import verify_revision
    if verify_revision(repo,baseline)!=revision:
        raise ValueError('stopped workspace no longer matches exact clean result')
    destination=Path(destination)
    if destination.exists():
        raise ValueError('candidate export already exists; retain and inspect it')
    archive=subprocess.check_output(['git','-c','core.fsmonitor=false','-C',repo,'archive',revision],timeout=15)
    if len(archive)>10*1024*1024:raise ValueError('development candidate archive exceeds limit')
    destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:stream.extractall(destination,filter='data')
    return hashlib.sha256(archive).hexdigest()


def check_candidate(entry,runtime,candidate):
    name='factory-baseline-check-'+entry['unit']
    label='factory.baseline.unit='+entry['unit']
    image=runtime['provision']['helper_image']
    def cleanup():
        try:info=json.loads(command('docker','inspect',name))[0]
        except subprocess.CalledProcessError:return
        if info['Config'].get('Labels',{}).get('factory.baseline.unit')!=entry['unit'] or info['Image']!=image:
            raise ValueError('checker ownership mismatch; no removal attempted')
        command('docker','rm','-f',info['Id'])
    cleanup()
    try:
        check=subprocess.run(['docker','run','--name',name,'--label',label,'--network','none','--read-only','--cap-drop','ALL',
            '--security-opt','no-new-privileges','--user',str(os.geteuid())+':'+str(os.geteuid()),
            '--memory','256m','--cpus','1','--pids-limit','64','--tmpfs','/tmp:rw,nosuid,nodev',
            '--mount','type=bind,src='+str(candidate)+',dst=/candidate,readonly',
            '--mount','type=bind,src='+str(WORKER/'baseline')+',dst=/checks,readonly',
            '--entrypoint','python3',image,'-B','/checks/check.py',entry['task_id'],'/candidate'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
        return {'passed':check.returncode==0,'exit_code':check.returncode,'timed_out':False}
    except subprocess.TimeoutExpired:
        return {'passed':False,'exit_code':None,'timed_out':True}
    finally:cleanup()


def final_accounting(binding,runtime):
    volumes=binding['volumes']
    names={record['Labels']['factory.role']:name for name,record in volumes.items()}
    # Read retained volumes after both processes stop; no live proxy is needed.
    code=("import sys,json,pathlib,sqlite3,hashlib;sys.path.insert(0,'/code');from harness_telemetry import summarize;"
          "db=sqlite3.connect('file:/budget/b.db?mode=ro',uri=True);"
          "ledger={t:db.execute('SELECT * FROM '+t).fetchall() for t in ('budgets','monetary_budgets')};"
          "raw=pathlib.Path('/telemetry/telemetry.jsonl').read_bytes();"
          "assert len(raw)<=4194304 and raw.endswith(b'\\n');"
          "rows=[json.loads(x) for x in raw.splitlines()];"
          "print(json.dumps({'ledger':ledger,'telemetry':{**summarize(rows),'log_sha256':hashlib.sha256(raw).hexdigest()}}))")
    value=json.loads(command('docker','run','--rm','--network','none','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--user',str(os.geteuid())+':'+str(os.geteuid()),
        '--memory','128m','--cpus','0.5','--pids-limit','32',
        '--mount','type=volume,src='+names['budget']+',dst=/budget,readonly',
        '--mount','type=volume,src='+names['telemetry']+',dst=/telemetry,readonly',
        '--mount','type=bind,src='+str(WORKER)+',dst=/code,readonly',
        '--entrypoint','python3',runtime['provision']['helper_image'],'-B','-c',code,timeout=30))
    return value

TRACE_CLIENT = r'''
import collections,hashlib,json,sys,urllib.request,urllib.parse
cid=sys.argv[1]; page=None; seen=set(); counts=collections.Counter(); tools=collections.Counter()
observed_bytes=0; diagnostics=0; installs=0; hashes=[]; timestamps=[]
for _ in range(50):
 query='?limit=20'+('&page_id='+urllib.parse.quote(page,safe='') if page else '')
 req=urllib.request.Request('http://127.0.0.1:8000/api/conversations/'+cid+'/events/search'+query,headers={'X-Session-API-Key':'dummy'})
 with urllib.request.urlopen(req,timeout=10) as response:
  raw=response.read(4194305)
  if len(raw)>4194304:raise ValueError('page limit')
  data=json.loads(raw)
 for event in data['items']:
  identity=event.get('id')
  if identity in seen:continue
  seen.add(identity);kind=event.get('kind');counts[kind if kind in ('ActionEvent','ObservationEvent','MessageEvent','SystemPromptEvent') else 'other']+=1
  if isinstance(event.get('timestamp'),str):timestamps.append(event['timestamp'])
  if kind=='ActionEvent':
   name=event.get('tool_name');tools[name if name in ('terminal','file_editor','finish','task_tracker') else 'other']+=1
   action=event.get('action') or {};command=action.get('command')
   if isinstance(command,str):
    hashes.append(hashlib.sha256(command.encode()).hexdigest())
    if 'factory_missing_dependency' in command and ('import factory_missing_dependency' in command or 'import_module(' in command):diagnostics+=1
    if any(x in command for x in ('pip install','pip3 install','uv add','uv pip install')):installs+=1
  if kind=='ObservationEvent':observed_bytes+=len(json.dumps(event.get('observation'),ensure_ascii=False).encode())
 page=data.get('next_page_id')
 if not page:break
else:raise ValueError('trace page limit')
print(json.dumps({'event_count':len(seen),'event_kinds':dict(counts),'tool_actions':dict(tools),
 'observation_serialized_bytes':observed_bytes,'dependency_diagnostic_commands':diagnostics,
 'install_commands':installs,'command_sha256':hashes,'first_event_timestamp':min(timestamps) if timestamps else None,
 'last_event_timestamp':max(timestamps) if timestamps else None,'complete':True}))
'''


def trace_summary(worker,conversation):
    try:
        return json.loads(command('docker','exec',worker,'python3','-c',TRACE_CLIENT,conversation,timeout=90))
    except (OSError,ValueError,subprocess.SubprocessError):
        return {'complete':False,'event_count':None,'tool_actions':None,
                'dependency_diagnostic_commands':None,'install_commands':None}


def restore(root,trial,active,service):
    previous=(trial/'baseline.previous.service').read_text()
    expected=(trial/'baseline.desired.service').read_text()
    if service.read_text() not in (expected,previous):raise ValueError('service changed; retain active marker')
    if service.read_text()!=previous:
        service.write_text(previous)
        command('systemctl','--user','daemon-reload');command('systemctl','--user','restart','factory-endpoint')
    if active.exists():
        if json.loads(active.read_text())['unit']!=trial.name:raise ValueError('active owner changed')
        active.unlink()


def run(config,unit):
    from image_approval import verify_approval
    from operator_cli import _private_file
    root=Path(config['root']);out=Path(config['suite_dir']);trial=root/'dispatch'/unit
    suite=json.loads((out/'suite.json').read_text())
    entry=next(x for x in suite['runs'] if x['unit']==unit)
    runtime=config_file(trial/'runtime.json')
    if hashlib.sha256((trial/'runtime.json').read_bytes()).hexdigest()!=entry['runtime_sha256']:
        raise ValueError('prepared runtime changed')
    verify_approval(_private_file(config['approval']),_private_file(config['signature']),
                    _private_file(config['trust_root']),runtime['provision'])
    result_path=out/(unit+'.json')
    if result_path.exists():return json.loads(result_path.read_text())
    with (root/'operator.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        active=root/'operator-active.json';service=Path.home()/'.config/systemd/user/factory-endpoint.service'
        if active.exists() and json.loads(active.read_text())['unit']!=unit:
            raise ValueError('another operator task needs observation')
        collected=trial/'baseline.collected.json'
        if collected.exists():
            result=json.loads(collected.read_text())
            if result['requires_abandonment']:return result
            restore(root,trial,active,service);result['binding_restored']=True
            write_new(result_path,result);return result
        previous_path=trial/'baseline.previous.service';desired_path=trial/'baseline.desired.service'
        if not previous_path.exists():
            if active.exists():raise ValueError('active marker without prior binding')
            previous=service.read_text()
            if '/releases/'+Path(config['release']).name+'/' not in previous:
                raise ValueError('release must already be deployed')
            current_config=next(x.split('=',2)[2] for x in previous.splitlines() if x.startswith('Environment=FACTORY_RUNTIME_CONFIG='))
            prior=config_file(current_config)['attempt_id'].rsplit(':',1)[0]
            if post(root,'WorkUnit/'+prior+'/getState',{}).get('stage') not in ('validated','needs_validation','failed','abandoned','unclaimed'):
                raise ValueError('prior task is not idle')
            write_new(previous_path,previous)
            desired='\n'.join('Environment=FACTORY_RUNTIME_CONFIG='+str(trial/'runtime.json') if x.startswith('Environment=FACTORY_RUNTIME_CONFIG=') else x for x in previous.splitlines())+'\n'
            write_new(desired_path,desired);write_new(active,{'unit':unit,'owner':'baseline-trials'})
        desired=desired_path.read_text()
        if service.read_text() not in (previous_path.read_text(),desired):raise ValueError('service binding changed')
        service.write_text(desired)
        command('systemctl','--user','daemon-reload');command('systemctl','--user','restart','factory-endpoint')
        for _ in range(30):
            try:
                post(root,'WorkUnit/'+unit+'/getState',{});break
            except OSError:time.sleep(1)
        else:raise ValueError('endpoint unavailable; observe same unit')
        request={'unitId':unit,'target':'http://127.0.0.1:1','scenarios':'baseline-external-development-checks',
                 'targetRepo':runtime['repo'],'taskFile':runtime['task_file']}
        intent=trial/'baseline.dispatch-intent.json'
        observed_path=trial/'baseline.observed.json'
        if not observed_path.exists():
            state=dispatch_once(intent,unit,request,lambda route,value:post(root,route,value))
            started=json.loads(intent.read_text())['started_at_unix'];last=None
            while time.time()-started<600:
                state=post(root,'WorkUnit/'+unit+'/getState',{})
                if state.get('stage')!=last:
                    last=state.get('stage');print(json.dumps({'unit':unit,'stage':last}),flush=True)
                if last in ('needs_validation','validated','failed'):break
                time.sleep(5)
            binding=trial_binding(root,unit)
            proxy,worker=binding['proxy_id'],binding['worker_id']
            observed_finished=time.time()
            ledger_code="import sqlite3,json;d=sqlite3.connect('file:/budget/b.db?mode=ro',uri=True);print(json.dumps({t:d.execute('SELECT * FROM '+t).fetchall() for t in ('budgets','monetary_budgets')}))"
            ledger=json.loads(command('docker','exec',proxy,'python3','-c',ledger_code))
            telemetry_code="import sys,json,pathlib,hashlib;sys.path.insert(0,'/opt/factory');from harness_telemetry import summarize;p=pathlib.Path('/telemetry/telemetry.jsonl');raw=p.read_bytes();rows=[json.loads(x) for x in raw.splitlines()];print(json.dumps({**summarize(rows),'log_sha256':hashlib.sha256(raw).hexdigest()}))"
            telemetry=json.loads(command('docker','exec',proxy,'python3','-c',telemetry_code))
            # Inspect actual worker mount and network boundaries before stopping it.
            info=json.loads(command('docker','inspect',worker))[0]
            assert info['HostConfig']['NetworkMode']=='none'
            assert {m['Destination'] for m in info['Mounts']}=={'/gateway','/workspace'}
            denied="import pathlib;assert not pathlib.Path('/telemetry').exists();assert not pathlib.Path('/budget').exists();print('denied')"
            assert command('docker','exec',worker,'python3','-c',denied)=='denied'
            trace=trace_summary(worker,state.get('build',{}).get('conversation_id',''))
            write_new(observed_path,{'state':state,'binding':binding,'started':started,'observed_finished':observed_finished,'ledger':ledger,'telemetry':telemetry,'trace':trace})
        observed=json.loads(observed_path.read_text())
        state=observed['state'];binding=observed['binding'];started=observed['started']
        observed_finished=observed['observed_finished'];ledger=observed['ledger'];telemetry=observed['telemetry'];trace=observed['trace']
        if trial_binding(root,unit)!=binding:raise ValueError('allocation changed after observed checkpoint')
        proxy,worker=binding['proxy_id'],binding['worker_id']
        command('docker','stop',worker,proxy,timeout=45)
        final_path=trial/'baseline.final-accounting.json'
        if not final_path.exists():write_new(final_path,final_accounting(binding,runtime))
        final=json.loads(final_path.read_text());ledger=final['ledger'];telemetry=final['telemetry']
        changed=[];accepted=False;revision=state.get('build',{}).get('git_revision')
        candidate=trial/'baseline.candidate';check={'passed':False,'reason':'worker did not finish'};archive_sha=None
        if state.get('stage') in ('needs_validation','validated'):
            changed=command('git','-c','core.fsmonitor=false','-C',runtime['repo'],'diff','--name-only',entry['baseline'],revision).splitlines()
            if in_scope(changed,entry['allowed_paths']):
                frozen=trial/'baseline.candidate.json'
                if not frozen.exists():
                    archive_sha=freeze_candidate(runtime['repo'],entry['baseline'],revision,candidate)
                    write_new(frozen,{'revision':revision,'archive_sha256':archive_sha})
                else:archive_sha=json.loads(frozen.read_text())['archive_sha256']
                check=check_candidate(entry,runtime,candidate)
                accepted=check['passed']
                if entry['task_id']=='stalled-run':
                    accepted=accepted and trace.get('complete') is True and trace.get('dependency_diagnostic_commands')==1 and trace.get('install_commands')==0
        result={**entry,'state':state,'revision':revision,'changed':changed,'accepted':accepted,
            'task_wall_elapsed_s':round(observed_finished-started,3),'budget':ledger,'telemetry':telemetry,'trace':trace,
            'candidate_archive_sha256':archive_sha,'candidate_check':check,
            'reservation_count_matches_telemetry':sum(r[3] for r in ledger['budgets'])==telemetry['requests_observed'],
            'worker_telemetry_and_budget_paths_denied':True,'resources_stopped':True,
            'requires_abandonment':state.get('stage') not in ('needs_validation','validated','failed'),
            'worker':worker,'proxy':proxy,'actual_charges':None,'phase_9d_2_started':False}
        write_new(trial/'baseline.collected.json',result)
        if result['requires_abandonment']:
            return result  # Keep binding/marker until a separately signed abandonment commits.
        restore(root,trial,active,service);result['binding_restored']=True
        write_new(result_path,result)
        return result


def close_abandoned(config,unit,envelope):
    root=Path(config['root']);trial=root/'dispatch'/unit;out=Path(config['suite_dir'])
    with (root/'operator.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=json.loads((trial/'baseline.collected.json').read_text())
        if not result['requires_abandonment']:raise ValueError('trial does not need abandonment')
        if post(root,'WorkUnit/'+unit+'/abandon',config_file(envelope),raw=True) is not True:
            raise ValueError('signed abandonment refused')
        state=post(root,'WorkUnit/'+unit+'/getState',{})
        if state.get('stage')!='abandoned':raise ValueError('abandonment not committed')
        result.update(state=state,requires_abandonment=False,abandoned=True)
        restore(root,trial,root/'operator-active.json',Path.home()/'.config/systemd/user/factory-endpoint.service')
        result['binding_restored']=True;write_new(out/(unit+'.json'),result)
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--config',required=True)
    parser.add_argument('action',choices=('prepare','run','close-abandoned'))
    parser.add_argument('--unit');parser.add_argument('--envelope')
    args=parser.parse_args();config=config_file(args.config)
    if args.action=='prepare':result=prepare(config)
    elif args.action=='run':result=run(config,args.unit)
    else:result=close_abandoned(config,args.unit,args.envelope)
    print(json.dumps({'result':result}),flush=True)


if __name__=='__main__':main()
