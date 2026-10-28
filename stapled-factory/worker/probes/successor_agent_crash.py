#!/usr/bin/env python3
"""Disposable server-crash successor test using the same synthetic budget ledger.

Exercises a shared reservation ledger, not provider billing or production image
provenance. The proxy's HTTPS transport alone is replaced by a synthetic model.
Requires cached Agent Server and python:3.11-slim images. No real credentials.
"""
import json, subprocess, time, uuid
from pathlib import Path

from crash_recovery import IMAGE, CLIENT, cmd

ROOT = Path(__file__).resolve().parents[1]
id = uuid.uuid4().hex[:12]
w, successor, p, v = ('factory-successor-agent-' + id + '-' + x for x in ('worker', 'successor', 'proxy', 'socket'))
created = []
PROXY = r'''import http.client, io, json, sys, time
sys.path.insert(0,'/tmp')
from guarded_store import GuardedAttemptStore
from pathlib import Path
from egress_proxy import UnixServer, RequestBudget
class Response:
 status=200
 def __init__(self, body):
  self.body=io.BytesIO(body)
 def getheader(self,n,default=None): return 'text/event-stream'
 def read1(self,n): return self.body.read(n)
class Conn:
 def __init__(self,*a,**kw): pass
 def request(self,method,path,raw,headers):
  x=json.loads(raw)
  with open('/tmp/requests','a') as f: f.write(json.dumps({'tools':[t.get('name') for t in x.get('tools',[])], 'stream':x.get('stream')})+'\n')
  tools=x.get('tools',[])
  if tools and not Path('/tmp/first-inflight').exists():
   Path('/tmp/first-inflight').touch(); time.sleep(20)
  finish=next((t['name'] for t in tools if 'finish' in t.get('name','').lower()), None)
  if finish:
   block={'type':'tool_use','id':'toolu_1','name':finish,'input':{}}
   delta={'type':'input_json_delta','partial_json':'{"message":"done"}'}
   reason='tool_use'
  else:
   block={'type':'text','text':''}; delta={'type':'text_delta','text':'done'}; reason='end_turn'
  events=[('message_start',{'type':'message_start','message':{'id':'msg_1','type':'message','role':'assistant','content':[],'model':x['model'],'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':1,'output_tokens':1}}}),('content_block_start',{'type':'content_block_start','index':0,'content_block':block}),('content_block_delta',{'type':'content_block_delta','index':0,'delta':delta}),('content_block_stop',{'type':'content_block_stop','index':0}),('message_delta',{'type':'message_delta','delta':{'stop_reason':reason,'stop_sequence':None},'usage':{'output_tokens':1}}),('message_stop',{'type':'message_stop'})]
  if x.get('stream'):
   data=''.join('event: '+n+'\ndata: '+json.dumps(y)+'\n\n' for n,y in events).encode(); self.response=Response(data)
  else:
   content=({'type':'tool_use','id':'toolu_1','name':finish,'input':{'message':'done'}} if finish else {'type':'text','text':'done'})
   self.response=Response(json.dumps({'id':'msg_1','type':'message','role':'assistant','content':[content],'model':x['model'],'stop_reason':reason,'stop_sequence':None,'usage':{'input_tokens':1,'output_tokens':1}}).encode()); self.response.getheader=lambda n,default=None:'application/json'
 def getresponse(self): return self.response
 def close(self): pass
http.client.HTTPSConnection=Conn
GuardedAttemptStore.initialize(Path('/tmp/budget.db'),Path('/tmp/anchor/watermark'))
b=RequestBudget('/tmp/budget.db','probe','probe-model',5,'a'*64,anchor_path='/tmp/anchor/watermark',ceiling_microdollars=2000000,reserve_microdollars=500000,policy_id='synthetic',expires_at=int(time.time())+3600)
s=UnixServer('/gateway/api.sock','dummy','synthetic',443,'probe-model',b)
s.serve_forever()
'''
try:
    cmd('volume','create',v); created.append(('volume',v))
    cmd('run','--rm','--network','none','--user','0:0','-v',v+':/gateway','python:3.11-slim','chown','10001:10001','/gateway')
    cmd('run','-d','--name',p,'--label','factory.probe=agent-gateway','--network','none','--user','10001:10001','--cap-drop','ALL','--security-opt','no-new-privileges','-v',v+':/gateway','python:3.11-slim','sleep','300'); created.append(('container',p))
    for file in ('egress_proxy.py','guarded_store.py'):
        cmd('cp',str(ROOT/file),p+':/tmp/'+file)
    cmd('exec',p,'mkdir','/tmp/anchor')
    cmd('exec','-i',p,'python','-c','import sys;open("/tmp/probe.py","w").write(sys.stdin.read())',input=PROXY)
    cmd('exec','-d',p,'python','/tmp/probe.py')
    for _ in range(30):
        if cmd('exec',p,'test','-S','/gateway/api.sock',check=False).returncode==0: break
        time.sleep(1)
    else: raise RuntimeError('proxy failed to start')
    socket_identity=cmd('exec',p,'stat','-c','%u:%g %a','/gateway/api.sock').stdout.strip()
    if socket_identity != '10001:10001 600': raise RuntimeError('socket ownership or mode is wrong')
    cmd('run','-d','--name',w,'--label','factory.probe=agent-gateway','--network','none','--user','10001:10001','--cap-drop','ALL','--security-opt','no-new-privileges','-v',v+':/gateway:ro','-e','OH_ENABLE_VSCODE=false',IMAGE,timeout=60); created.append(('container',w))
    def inside(*a,input=None,check=True): return cmd('exec','-i',w,*a,input=input,check=check)
    for _ in range(90):
        if inside('python','-c','import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/openapi.json",timeout=2)',check=False).returncode==0: break
        time.sleep(1)
    else: raise RuntimeError('server unavailable')
    inside('python','-c','import sys;open("/tmp/bridge.py","w").write(sys.stdin.read())',input=(ROOT/'egress_bridge.py').read_text())
    cmd('exec','-d',w,'python','/tmp/bridge.py','--socket','/gateway/api.sock')
    conversation=str(uuid.uuid4())
    payload={'conversation_id':conversation,'agent_settings':{'llm':{'usage_id':'probe','model':'anthropic/probe-model','base_url':'http://127.0.0.1:8123','api_key':'dummy','max_output_tokens':128}},'workspace':{'working_dir':'/tmp'},'initial_message':{'role':'user','content':[{'type':'text','text':'Say hello.'}],'run':True},'max_iterations':3}
    result=inside('python','-c',CLIENT,'POST','/api/conversations',input=json.dumps(payload),check=False)
    if result.returncode: raise RuntimeError('conversation creation failed: '+result.stderr[-500:])
    for _ in range(90):
        count=cmd('exec',p,'sh','-c','wc -l < /tmp/requests',check=False)
        if count.returncode==0 and int(count.stdout.strip()) >= 2: break
        time.sleep(1)
    # Kill the server while the first agent model request is still in flight.
    if cmd('exec',p,'test','-f','/tmp/first-inflight',check=False).returncode:
        raise RuntimeError('agent request did not reach the in-flight barrier')
    cmd('kill','--signal','KILL',w)
    if cmd('inspect','--format','{{.State.Running}}',w).stdout.strip() != 'false':
        raise RuntimeError('old worker was not fenced')
    print('old server killed during model request',flush=True)
    cmd('run','-d','--name',successor,'--label','factory.probe=successor-agent','--network','none','--user','10001:10001','--cap-drop','ALL','--security-opt','no-new-privileges','-v',v+':/gateway:ro','-e','OH_ENABLE_VSCODE=false',IMAGE,timeout=60); created.append(('container',successor))
    def next_inside(*a,input=None,check=True): return cmd('exec','-i',successor,*a,input=input,check=check)
    for _ in range(90):
        if next_inside('python','-c','import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/openapi.json",timeout=2)',check=False).returncode==0: break
        time.sleep(1)
    else: raise RuntimeError('successor server unavailable')
    next_inside('python','-c','import sys;open("/tmp/bridge.py","w").write(sys.stdin.read())',input=(ROOT/'egress_bridge.py').read_text())
    cmd('exec','-d',successor,'python','/tmp/bridge.py','--socket','/gateway/api.sock')
    payload['conversation_id']=str(uuid.uuid4())
    result=next_inside('python','-c',CLIENT,'POST','/api/conversations',input=json.dumps(payload),check=False)
    if result.returncode: raise RuntimeError('successor conversation failed: '+result.stderr[-500:])
    for _ in range(90):
        ledger=cmd('exec',p,'python','-c',"import sqlite3; d=sqlite3.connect('/tmp/budget.db'); print(*d.execute('select used, spent from budgets join monetary_budgets using(attempt)').fetchone())").stdout.strip()
        if ledger == '4 2000000': break
        time.sleep(1)
    else: raise RuntimeError('successor did not consume the same ledger: '+ledger)
    for _ in range(45):
        observed=next_inside('python','-c',CLIENT,'GET','/api/conversations/'+payload['conversation_id'],check=False)
        if observed.returncode == 0:
            info=json.loads(observed.stdout)
            if info.get('execution_status') == 'finished': break
            if info.get('execution_status') == 'error': raise RuntimeError('successor conversation failed')
        time.sleep(1)
    else: raise RuntimeError('successor conversation did not finish')
    print('successor conversation finished',flush=True)
    denial=next_inside('python','-c', '''import json,urllib.request,urllib.error
b=json.dumps({'model':'probe-model','max_tokens':1,'messages':[{'role':'user','content':'extra'}]}).encode()
try: urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8123/v1/messages',data=b,headers={'Content-Type':'application/json'}),timeout=5)
except urllib.error.HTTPError as e: print(e.code)
''').stdout.strip()
    if denial != '429': raise RuntimeError('fifth request was not rejected')
    entries=cmd('exec',p,'cat','/tmp/requests').stdout
    if len(entries.splitlines()) != 4: raise RuntimeError('unexpected model request count')
    print('successor made real agent requests through original proxy; four synthetic reservations total $2; fifth denied',flush=True)

finally:
    for kind,name in reversed(created): cmd(kind,'rm',*(['-f'] if kind=='container' else []),name,check=False,timeout=60)
