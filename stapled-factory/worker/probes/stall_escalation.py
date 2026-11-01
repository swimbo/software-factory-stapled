#!/usr/bin/env python3
"""Unpaid SDK/stall/deadline integration; retain every owned resource.

Run on Linux with Docker and user systemd, with an already approved immutable
worker image. A local scripted model runs inside the network-none worker. The
proxy is a network-none lifecycle sentinel, not a gateway implementation. This
proves containment integration, never paid-model efficacy or task acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# No prompt/output body is logged. Delay leaves a live SDK loop for observation.
MODEL = r'''import http.server,json,time
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_POST(self):
  request=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
  tools=[t["function"]["name"] for t in request.get("tools",[]) if t["function"]["name"]=="terminal"]
  if not tools: self.send_error(400);return
  with open("/workspace/.git/probe-model-count","a") as f: f.write("request\n")
  time.sleep(4)
  action={"command":"sleep 6; printf 'controlled failure\\n'; false","timeout":20}
  result={"id":"fixture","object":"chat.completion","created":0,"model":"gpt-4o",
   "choices":[{"index":0,"message":{"role":"assistant","content":None,"tool_calls":[
    {"id":"call_"+str(time.monotonic_ns()),"type":"function","function":{"name":tools[0],"arguments":json.dumps(action)}}]},"finish_reason":"tool_calls"}],
   "usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}
  raw=json.dumps(result).encode();self.send_response(200);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
server=http.server.HTTPServer(("127.0.0.1",18080),Handler)
open("/workspace/.git/probe-model-ready","w").write("ready")
server.serve_forever()
'''


def command(*args, timeout=30, input=None):
    result = subprocess.run(args, input=input, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=timeout)
    if result.returncode:
        raise RuntimeError('probe command failed: ' + args[0])
    return result.stdout.strip()


def save(path, value):
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2, sort_keys=True); stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())


def inspect(container):
    return json.loads(command('docker', 'inspect', container))[0]


def _owned_container(container, image, allocation, role):
    if not re.fullmatch(r'[0-9a-f]{64}', container):
        raise RuntimeError('full probe container identity required')
    info = inspect(container)
    labels = info['Config']['Labels']
    if (info['Id'] != container or info['Image'] != image
            or labels.get('factory.allocation') != allocation or labels.get('factory.role') != role):
        raise RuntimeError('fixture ownership changed; retained for operator')
    return info


def _verify_stopped_reopen(adapter, conversation_id, calls):
    from stall_runtime import StallIntervention
    before = dict(calls)
    try:
        adapter.read(conversation_id, None)
    except StallIntervention as stopped:
        if stopped.intervention_status != 'stopped':
            raise RuntimeError('journal reopen did not confirm stopped original') from None
    else:
        raise RuntimeError('journal reopen unexpectedly resumed original')
    if calls != before:
        raise RuntimeError('journal reopen performed transport activity')


def fail_completion(*_):
    raise RuntimeError('scripted failure loop unexpectedly finished; no completion assertion')


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--image', required=True, help='Approved locally present sha256 image ID')
    parser.add_argument('--output', type=Path, required=True, help='New private output directory')
    args = parser.parse_args()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', args.image):
        raise ValueError('immutable image ID required')
    if sys.platform != 'linux': raise RuntimeError('Linux user systemd required')
    out = args.output
    if not out.is_absolute() or out.resolve() != out: raise ValueError('canonical output required')
    if inspect(args.image)['Id'] != args.image: raise ValueError('image identity differs')
    command('systemctl', '--user', 'list-units', '--no-legend', '--no-pager')
    out.mkdir(mode=0o700)  # Refuse any existing campaign, including incomplete ones.
    os.umask(0o077)
    for name in ('ledger', 'anchor', 'workspace'): (out/name).mkdir(mode=0o700)
    workspace = out/'workspace'
    command('git', '-C', str(workspace), 'init', '-q')
    command('git', '-C', str(workspace), '-c', 'user.name=Factory Probe',
            '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'Unpaid stall fixture')
    (workspace/'.git/factory-home').mkdir(mode=0o700)
    from bounded_profile import ENVIRONMENT
    from guarded_store import GuardedAttemptStore
    import deadline_watchdog as deadline
    from docker_transport import DockerTransport
    from openhands_adapter import OpenHandsAdapter
    from stall_runtime import StallAdapter, StallIntervention, DIAGNOSTIC
    store = GuardedAttemptStore.initialize(out/'ledger/attempts.db', out/'anchor/watermark.json')
    cid = str(uuid.uuid4()); attempt = 'unpaid-stall-' + uuid.uuid4().hex + ':0'
    request_hash = hashlib.sha256(attempt.encode()).hexdigest()
    specification = hashlib.sha256((args.image+'\n'+MODEL).encode()).hexdigest()
    store.reserve(attempt, request_hash, cid)
    _, allocation = store.reserve_allocation(attempt, request_hash, cid, specification)
    roles = {}; armed = None; outcome = None
    def alarm(*_): raise TimeoutError('bounded unpaid probe observation elapsed')
    previous_alarm = signal.signal(signal.SIGALRM, alarm); signal.alarm(210)
    try:
        common = ['--network','none','--restart','no','--cap-drop','ALL',
                  '--security-opt','no-new-privileges','--user',str(os.geteuid())+':'+str(os.getegid()),
                  '--memory','2g','--cpus','1','--pids-limit','256',
                  '--label','factory.allocation='+allocation,'--label','factory.probe=unpaid-stall-escalation']
        for role in ('worker','proxy'):
            extra = ['--mount','type=bind,src='+str(workspace)+',dst=/workspace'] if role=='worker' else []
            if role=='worker':
                for key,value in ENVIRONMENT.items(): extra += ['--env',key+'='+value]
                extra += ['--env','OH_ENABLE_VSCODE=false','--env','OH_SESSION_API_KEYS_0=dummy',
                          '--entrypoint','/usr/local/bin/openhands-agent-server',args.image]
            else: extra += ['--entrypoint','python3',args.image,'-c','import time; time.sleep(300)']
            # Creation alone cannot run a process. Record its full owned ID
            # before start so an accepted start timeout remains containable.
            container = command('docker','create','--name','factory-unpaid-stall-'+allocation+'-'+role,
                                *common,'--label','factory.role='+role,*extra)
            if _owned_container(container, args.image, allocation, role)['State']['Running']:
                raise RuntimeError('newly created fixture is already running')
            roles[role] = container
            command('docker', 'start', container)
        binding = {'volumes':{}}
        for role,container in roles.items():
            info = inspect(container)
            if info['HostConfig']['NetworkMode']!='none': raise ValueError('fixture network changed')
            binding[role+'_id'] = container
            binding[role+'_lifecycle'] = {k:info['State'][k] for k in ('Pid','StartedAt')}
        store.bind_allocation(attempt,request_hash,cid,specification,allocation,binding)
        armed = deadline.arm_deadline(store,attempt,request_hash,cid,specification,allocation,binding,
                                     args.image,args.image,seconds=240)
        save(out/'identity.json',{'attempt_id':attempt,'conversation_id':cid,'allocation_id':allocation,
                                 'request_hash':request_hash,'specification_hash':specification,'binding':binding})
        runtime = out/'runtime.json';save(runtime,{'fixture':'unpaid-local-model','image':args.image})
        transport = DockerTransport(roles['worker'],'dummy')
        for _ in range(45):
            try:
                command('docker','exec',roles['worker'],'python3','-c',
                        'import urllib.request;urllib.request.urlopen("http://127.0.0.1:8000/openapi.json",timeout=2)',timeout=4)
                break
            except (RuntimeError,subprocess.TimeoutExpired): time.sleep(1)
        else: raise RuntimeError('pinned AgentServer did not become ready')
        command('docker','exec','-i',roles['worker'],'python3','-c',
                'import sys;open("/tmp/stall-model.py","w").write(sys.stdin.read())',input=MODEL)
        command('docker','exec','-d',roles['worker'],'python3','/tmp/stall-model.py')
        for _ in range(20):
            if (workspace/'.git/probe-model-ready').is_file(): break
            time.sleep(.1)
        else: raise RuntimeError('local unpaid model did not become ready before dispatch')
        calls = {'create':0,'diagnostic':0,'run':0,'read':0}
        class CountingTransport:
            def request(self,method,path,payload=None):
                if method=='GET': calls['read']+=1
                if method=='POST':
                    if path=='/api/conversations': calls['create']+=1
                    elif path.endswith('/events'):
                        assert payload=={'role':'user','content':[{'type':'text','text':DIAGNOSTIC}],'run':False}
                        calls['diagnostic']+=1
                    else: calls['run']+=1;raise AssertionError('unexpected mutation')
                return transport.request(method,path,payload)
        base = OpenHandsAdapter('http://127.0.0.1:8000','dummy',fail_completion,transport=CountingTransport())
        observer = StallAdapter(base,store,attempt,str(runtime),str(workspace))
        payload = {'agent_settings':{'llm':{'usage_id':'unpaid-stall-fixture','model':'openai/gpt-4o',
                   'base_url':'http://127.0.0.1:18080/v1','api_key':'dummy','max_output_tokens':256},
                   'tools':[{'name':'terminal'}]},'workspace':{'working_dir':'/workspace'},
                   'initial_message':{'role':'user','content':[{'type':'text','text':'Run the controlled fixture.'}],'run':True},
                   'max_iterations':20}
        try: observer.create(cid,payload)
        except StallIntervention as stopped:
            if stopped.intervention_status!='stopped': raise
        else: raise RuntimeError('automatic continued-loop escalation did not occur')
        journal = json.loads(observer.path.read_text()); detector = journal['detector']
        after = sum(s['seq']>detector['intervention_seq'] for s in detector['history'])
        if not (journal['effect']=='stopped' and journal['delivery_status']=='sent'
                and detector['stopped'] and after>=3
                and {k:calls[k] for k in ('create','diagnostic','run')}=={'create':1,'diagnostic':1,'run':0}):
            raise RuntimeError('fixture stopped without verified post-intervention escalation')
        # Reopen same journal: no SDK reads, second diagnostic, or replacement.
        _verify_stopped_reopen(StallAdapter(base,store,attempt,str(runtime),str(workspace)), cid, calls)
        with store._transaction() as (db,_):
            row = db.execute('SELECT request_hash,conversation_id,status FROM attempts WHERE attempt_id=?',(attempt,)).fetchone()
            counts = {table:db.execute('SELECT count(*) FROM '+table).fetchone()[0]
                      for table in ('attempts','allocations','fence_intents','fence_confirmations')}
        if row!=(request_hash,cid,'reserved') or any(n!=1 for n in counts.values()):
            raise RuntimeError('original attempt or reservation changed')
        if any(inspect(container)['State']['Running'] for container in roles.values()):
            raise RuntimeError('original lifecycle still running')
        checkpoints = []
        for path in sorted((workspace/'.git/factory-home/conversations').rglob('*.json')):
            if path.is_symlink() or not path.is_file(): raise ValueError('unsafe retained checkpoint')
            checkpoints.append({'path':str(path.relative_to(workspace)),
                                'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        save(out/'checkpoint-hashes.json',checkpoints)
        outcome = {'schema':'factory.unpaid-stall-escalation/v1','passed':True,'image':args.image,
                   'calls':calls,'qualifying_post_intervention_samples':after,'reservation_retained':True,
                   'originals_stopped':True,'fence_confirmed':True,'same_journal_reopen_no_resend':True,
                   'checkpoint_files':len(checkpoints),'journal_sha256':hashlib.sha256(observer.path.read_bytes()).hexdigest(),
                   'paid_requests':0,'proxy_boundary':'network-none lifecycle sentinel, not gateway',
                   'limits':['Scripted-model integration only; no routed-model efficacy or task acceptance.',
                             'No actual-charge accounting; no production isolation or replay proof.',
                             'Durable-claim crash and productive-control coverage remains separate.'],
                   'resources_retained':True}
        save(out/'result.json',outcome)
        print(json.dumps(outcome,sort_keys=True))
    finally:
        signal.alarm(0);signal.signal(signal.SIGALRM,previous_alarm)
        if armed is not None:
            deadline.expire(armed,cancel=True)  # Idempotent original containment; never remove resources.
        else:
            # Before binding only this invocation's observed full IDs are owned.
            for role,container in roles.items():
                info=_owned_container(container,args.image,allocation,role)
                if info['State']['Running']:command('docker','kill','--signal','KILL',container)


if __name__=='__main__': main()
