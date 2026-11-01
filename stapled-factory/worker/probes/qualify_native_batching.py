"""Run unpaid, operator-owned SDK qualification on an existing SSH Docker host.

No image build, gateway credentials, shared service changes or model requests.
Results are deterministic capability tests, not autonomous-agent benchmarks.
"""
import argparse,base64,hashlib,io,json,os
from pathlib import Path
import re,shlex,subprocess,tarfile,uuid

WORKER=Path(__file__).resolve().parents[1]
DEFAULT_WORKER='sha256:d1d11b40384c2eb25d5d9d38a069abed9801a0a290c64101a24cc200a8a26e30'
DEFAULT_HELPER='sha256:73570499e4f9272b8e96a1115a13f9c4d186a32cf3968621f5ae602e55083d19'


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--host',default='hermes-control');parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mode',choices=('batching','limits'),default='batching')
    parser.add_argument('--worker-image',default=DEFAULT_WORKER);parser.add_argument('--helper-image',default=DEFAULT_HELPER)
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.@-]*',args.host) or any(not re.fullmatch('sha256:[0-9a-f]{64}',v) for v in (args.worker_image,args.helper_image)):
        parser.error('explicit host and immutable image IDs required')
    os.umask(0o077);args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    ssh=['ssh','-o','BatchMode=yes','-o','ForwardAgent=no',args.host]
    def remote(argv,**kw):return subprocess.run(ssh+[shlex.join(list(map(str,argv)))],**kw)
    uid=int(remote(['id','-u'],capture_output=True,text=True,check=True).stdout);assert uid>0
    token=uuid.uuid4().hex;root='/tmp/factory-native-'+token;name='factory-native-'+token
    remote(['mkdir','-m','700',root],check=True)
    files={'modules/native_batching_sdk.py':WORKER/'probes/native_batching_sdk.py',
        'modules/native_limits_sdk.py':WORKER/'probes/native_limits_sdk.py',
        'modules/batching.py':WORKER/'baseline/batching.py',
        'inputs/tasks.json':WORKER/'baseline/tasks.json','grader/check.py':WORKER/'baseline/check.py'}
    for p in (WORKER/'baseline/seed').iterdir():
        if p.is_file() and p.name.endswith('.py'):files['seed/'+p.name]=p
    for name_part in ('bounded_profile.py','factory_capture_tool.py','output_artifacts.py'):files['modules/'+name_part]=WORKER/name_part
    payload=io.BytesIO()
    with tarfile.open(fileobj=payload,mode='w') as stream:
        for arc,path in files.items():stream.add(path,arcname=arc)
    remote(['tar','-xf','-','-C',root],input=payload.getvalue(),check=True)
    remote(['touch',root+'/inputs/boundary.json'],check=True)
    def cleanup(container,image):
        r=remote(['docker','inspect',container],capture_output=True,text=True)
        if r.returncode:return
        x=json.loads(r.stdout)[0]
        if x['Config']['Labels'].get('factory.probe.token')!=token or x['Image']!=image:
            raise RuntimeError('probe ownership changed; refusing cleanup')
        remote(['docker','rm','-f',x['Id']],check=True,stdout=subprocess.DEVNULL)
    pids=64 if args.mode=='limits' else 256
    argv=['docker','create','--name',name,'--label','factory.probe.token='+token,'--label','factory.probe=native-batching',
        '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user',f'{uid}:{uid}',
        '--memory','2g','--memory-swap','2g','--cpus','2','--pids-limit',str(pids),
        '--tmpfs',f'/tmp:rw,exec,nosuid,nodev,size=536870912,uid={uid},gid={uid},mode=0700',
        '--tmpfs',f'/workspace:rw,nosuid,nodev,size=67108864,uid={uid},gid={uid},mode=0700',
        '--mount','type=bind,src='+root+'/modules,dst=/probe,readonly',
        '--mount','type=bind,src='+root+'/seed,dst=/seed,readonly',
        '--mount','type=bind,src='+root+'/inputs,dst=/inputs,readonly',
        '-e','HOME=/tmp','--entrypoint','/bin/sleep',args.worker_image,'infinity']
    try:
        remote(argv,check=True,stdout=subprocess.DEVNULL);remote(['docker','start',name],check=True,stdout=subprocess.DEVNULL)
        boundary=json.loads(remote(['docker','inspect',name],capture_output=True,text=True,check=True).stdout)[0]
        # Only source modules/seed/task metadata enter the tested worker. The
        # independent checker stays in a separate helper container.
        remote(['tee',root+'/inputs/boundary.json'],input=json.dumps(boundary).encode(),check=True,stdout=subprocess.DEVNULL)
        module='native_limits_sdk' if args.mode=='limits' else 'native_batching_sdk'
        command=['docker','exec',name,'/usr/local/bin/openhands-agent-server','--extra-python-path','/probe:/inputs','--import-modules',module]
        run=remote(command,capture_output=True,text=True,timeout=120)
        (args.output/'sdk.log').write_text(run.stdout+'\n'+run.stderr)
        if run.returncode:raise RuntimeError('SDK qualification failed; see private sdk.log')
        prefix='FACTORY_LIMIT_PROBE=' if args.mode=='limits' else 'FACTORY_BATCH_PROBE='
        result=json.loads(next(line for line in run.stdout.splitlines() if line.startswith(prefix)).split('=',1)[1])
        result['boundary']={k:boundary['HostConfig'][k] for k in ('Memory','MemorySwap','NanoCpus','PidsLimit','ReadonlyRootfs','Tmpfs','NetworkMode')}
        result['worker_image']=args.worker_image;result['source_sha256']=[{'path':str(p.relative_to(WORKER)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files.values()]
        if args.mode=='batching':
            for row in result['cases']:
                raw=base64.b64decode(row.pop('archive_base64'));assert hashlib.sha256(raw).hexdigest()==row['archive_sha256']
                filename=row['task_id']+'-'+row['mode']+'.tar';(args.output/filename).write_bytes(raw)
                remote(['tee',root+'/'+filename],input=raw,check=True,stdout=subprocess.DEVNULL)
            checker=r'''import pathlib,tarfile,subprocess,json
root=pathlib.Path(ROOT);results=[]
for p in sorted(root.glob('*.tar')):
 mode='batched' if p.stem.endswith('-batched') else 'sequential';task=p.stem.removesuffix('-'+mode);dest=root/p.stem;dest.mkdir(mode=0o700)
 with tarfile.open(p) as t:
  assert all(m.isfile() and '/' not in m.name and m.size<1048576 for m in t.getmembers());t.extractall(dest,filter='data')
 name=NAME+'-check'
 r=subprocess.run(['docker','run','--name',name,'--rm','--label','factory.probe.token='+TOKEN,'--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user',UID,'--memory','256m','--pids-limit','64','--cpus','1','--tmpfs','/tmp:rw,nosuid,nodev,size=16m','--mount','type=bind,src='+str(root/'grader/check.py')+',dst=/checks/check.py,readonly','--mount','type=bind,src='+str(dest)+',dst=/candidate,readonly','--entrypoint','python3',IMAGE,'-B','/checks/check.py',task,'/candidate'],capture_output=True,timeout=15)
 results.append({'task_id':task,'mode':mode,'passed':r.returncode==0})
print(json.dumps(results))'''
            for key,value in {'ROOT':root,'NAME':name,'TOKEN':token,'UID':f'{uid}:{uid}','IMAGE':args.helper_image}.items():checker=checker.replace(key,repr(value))
            checked=remote(['python3','-'],input=checker,capture_output=True,text=True,check=True,timeout=60)
            accepted=json.loads(checked.stdout);assert len(accepted)==12 and all(r['passed'] for r in accepted)
            result['independent_acceptance']=accepted
        (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'mode':args.mode,'passed':True,'model_requests':0,'result':str(args.output/'result.json')}))
    finally:
        cleanup(name,args.worker_image);cleanup(name+'-check',args.helper_image)
        remote(['rm','-r','--',root],check=True)


if __name__=='__main__':main()
