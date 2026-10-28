#!/usr/bin/env python3
"""Disposable pinned Agent Server crash probe. No external services or real keys.

Requires Docker. The server and model stub share an isolated networkless
container. This observes recovery; it does not prove safe replay of tool calls.
"""
import argparse
import json
import subprocess
import time
import uuid

IMAGE = 'ghcr.io/openhands/agent-server@sha256:90da82d4dd57f713e2b9f5bc9444256b06c8bc709ca5ac794048ea9e1952b466'
MOCK = '''import http.server,time\nclass H(http.server.BaseHTTPRequestHandler):\n def log_message(self,*a): pass\n def do_POST(self):\n  self.rfile.read(int(self.headers.get("Content-Length",0)))\n  with open("/tmp/mock-arrived", "a") as f: f.write("request\\n"); f.flush()\n  time.sleep(300)\nhttp.server.HTTPServer(("127.0.0.1",18080),H).serve_forever()\n'''
CLIENT = '''import json,sys,urllib.request\nmethod=sys.argv[1]; path=sys.argv[2]; data=sys.stdin.buffer.read(); q=urllib.request.Request("http://127.0.0.1:8000"+path, method=method, data=data if data else None, headers={"Content-Type":"application/json"}); r=urllib.request.urlopen(q,timeout=25); print(json.dumps(json.load(r)))\n'''

def cmd(*args, input=None, check=True, timeout=30):
    return subprocess.run(['docker', *args], input=input, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check, timeout=timeout)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--hydrate', action='store_true', help='after lease expiry, pause the disposable conversation to probe rehydration')
    options = parser.parse_args()
    name='factory-crash-probe-'+uuid.uuid4().hex[:12]
    created=False
    def inside(*args, input=None, check=True, timeout=30):
        return cmd('exec','-i',name,*args,input=input,check=check,timeout=timeout)
    def api(method,path,payload=None):
        r=inside('python','-c',CLIENT,method,path,input=json.dumps(payload) if payload else '',check=False)
        if r.returncode: raise RuntimeError(r.stderr[-600:])
        return json.loads(r.stdout)
    def ready():
        for _ in range(90):
            try:
                inside('python','-c','import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/openapi.json",timeout=2)',timeout=4)
                return
            except (subprocess.CalledProcessError,subprocess.TimeoutExpired): time.sleep(1)
        raise RuntimeError('server did not start')
    try:
        cmd('run','-d','--name',name,'--label','factory.probe=crash-recovery','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','2g','--pids-limit','256','-e','OH_ENABLE_VSCODE=false','-e','OH_SECRET_KEY=disposable-factory-probe-secret-0000000000000000',IMAGE,timeout=60)
        created=True
        ready()
        inside('python','-c','import sys; open("/tmp/mock.py","w").write(sys.stdin.read())',input=MOCK)
        cmd('exec','-d',name,'python','/tmp/mock.py')
        cid=str(uuid.uuid4())
        payload={'conversation_id':cid,'agent_settings':{'llm':{'usage_id':'crash-probe','model':'openai/gpt-4o','base_url':'http://127.0.0.1:18080/v1','api_key':'dummy','max_output_tokens':128}},'workspace':{'working_dir':'/tmp'},'initial_message':{'role':'user','content':[{'type':'text','text':'Say hello.'}],'run':True},'max_iterations':3}
        info=api('POST','/api/conversations',payload)
        assert info['id']==cid
        for _ in range(60):
            if inside('test','-f','/tmp/mock-arrived',check=False).returncode==0: break
            time.sleep(1)
        else: raise RuntimeError('mock model request did not arrive')
        print('model request in flight; conversation:',cid,flush=True)
        cmd('kill','--signal','KILL',name)
        cmd('start',name)
        ready()
        initial_status=api('GET','/api/conversations/'+cid)['execution_status']
        print('after restart:',initial_status,flush=True)
        time.sleep(50)
        if options.hydrate:
            response = api('POST', '/api/conversations/'+cid+'/pause')
            if response.get('success') is not True:
                raise RuntimeError('Pause did not rehydrate the conversation')
            print('disposable pause response:', response, flush=True)
        final_status=api('GET','/api/conversations/'+cid)['execution_status']
        count=int(inside('sh','-c','wc -l < /tmp/mock-arrived').stdout.strip())
        print('after lease expiry:',final_status,flush=True)
        print('mock requests before crash:',count,flush=True)
        if initial_status not in ('running', 'error') or final_status not in ('running', 'error') or count != 1:
            raise RuntimeError('Unexpected outcome: inspect conversation before attempting recovery')
    finally:
        if created: cmd('rm','-f',name,check=False,timeout=60)

if __name__=='__main__': main()
