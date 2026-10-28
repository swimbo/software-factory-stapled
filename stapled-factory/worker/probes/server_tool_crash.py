#!/usr/bin/env python3
"""Crash a disposable Agent Server during an actual workspace-mutating tool.

A local synthetic model triggers a shell tool that appends to a marker then
blocks. Restart and read-only reconciliation must not append again. This is
not evidence that an interrupted task can be resumed or safely replayed.
"""
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'probes')]
from crash_recovery import cmd, IMAGE
from docker_transport import DockerTransport
from guarded_store import GuardedAttemptStore
from openhands_adapter import OpenHandsAdapter
from worker import execute_openhands_attempt

MOCK = r'''import http.server,json
class H(http.server.BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def do_POST(self):
  x=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))))
  tools=[i['function'] for i in x.get('tools',[])]
  open('/tmp/tool-schema','w').write(json.dumps(tools))
  choices=[t for t in tools if 'terminal' in t['name'].lower() or 'bash' in t['name'].lower()]
  if not choices:
   self.send_error(500); return
  t=choices[0]; props=t.get('parameters',{}).get('properties',{})
  key=next((k for k in ('command','cmd') if k in props),None)
  if key is None:
   self.send_error(500); return
  args={key:'printf "effect\\n" >> /tmp/tool-effect; sleep 10; printf "late-effect\\n" >> /tmp/tool-effect'}
  y={'id':'probe','object':'chat.completion','created':0,'model':'gpt-4o','choices':[{'index':0,'message':{'role':'assistant','content':None,'tool_calls':[{'id':'call_1','type':'function','function':{'name':t['name'],'arguments':json.dumps(args)}}]},'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
  b=json.dumps(y).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
http.server.HTTPServer(('127.0.0.1',18080),H).serve_forever()
'''

def main():
    name = 'factory-tool-crash-' + uuid.uuid4().hex[:12]
    created = False
    try:
        cid = cmd('run', '-d', '--name', name, '--label', 'factory.probe=tool-crash', '--network', 'none',
                  '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '-e', 'OH_ENABLE_VSCODE=false',
                  IMAGE, timeout=60).stdout.strip()
        created = True
        def inside(*args, input=None, check=True):
            return cmd('exec', '-i', cid, *args, input=input, check=check)
        def ready():
            for _ in range(90):
                if inside('python', '-c', 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/openapi.json",timeout=2)', check=False).returncode == 0:
                    return
                time.sleep(1)
            raise RuntimeError('server did not start')
        ready()
        inside('python', '-c', 'import sys;open("/tmp/mock.py","w").write(sys.stdin.read())', input=MOCK)
        cmd('exec', '-d', cid, 'python', '/tmp/mock.py')
        request = {'agent_settings': {'llm': {'usage_id':'tool-crash', 'model':'openai/gpt-4o',
                   'base_url':'http://127.0.0.1:18080/v1', 'api_key':'dummy', 'max_output_tokens':128}},
                   'workspace': {'working_dir':'/tmp'},
                   'initial_message': {'role':'user', 'content':[{'type':'text','text':'Run a shell command.'}], 'run':True},
                   'max_iterations':3}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'anchor').mkdir()
            db, anchor = root/'attempt.db', root/'anchor'/'watermark'
            store = GuardedAttemptStore.initialize(db, anchor)
            adapter = OpenHandsAdapter('http://127.0.0.1:8000', 'dummy', lambda *_: 'a'*40,
                                       transport=DockerTransport(cid, 'dummy'))
            initial = execute_openhands_attempt(store, 'tool-crash', request, adapter)
            if initial['status'] != 'unknown': raise RuntimeError('expected asynchronous attempt')
            for _ in range(60):
                if inside('test','-f','/tmp/tool-effect',check=False).returncode == 0: break
                time.sleep(1)
            else:
                schema = inside('cat','/tmp/tool-schema',check=False).stdout
                raise RuntimeError('tool did not produce effect; offered tools: '+schema[:4000])
            # Crash the server process itself, not just the Docker container.
            # The image uses tini as PID 1; it exits when its child dies and
            # Docker must stop the remaining tool processes before restart.
            children = inside('cat', '/proc/1/task/1/children').stdout.split()
            candidates = []
            for pid in children:
                if pid.isdigit():
                    command = inside('cat', '/proc/' + pid + '/cmdline', check=False).stdout.split('\0')[0]
                    if command == '/usr/local/bin/openhands-agent-server':
                        candidates.append(pid)
            if len(candidates) != 1:
                raise RuntimeError('unexpected init child process topology')
            inside('kill', '-KILL', candidates[0])
            cmd('wait', cid, timeout=30)
            cmd('start',cid); ready()
            # Give the interrupted command time to write its second marker if
            # a surviving child process were still executing after the crash.
            time.sleep(12)
            for _ in range(3):
                result = execute_openhands_attempt(GuardedAttemptStore(db, anchor), 'tool-crash', request,
                                                   adapter, reconcile_only=True)
                if result['status'] != 'unknown' or result['conversation_id'] != initial['conversation_id']:
                    raise RuntimeError('unexpected recovery result')
            contents=inside('cat','/tmp/tool-effect').stdout
            if contents != 'effect\n': raise RuntimeError('tool effect was repeated or lost: '+repr(contents))
            print('Server process killed after actual shell tool effect; container stopped and restarted: marker preserved once; three observations unknown, no replay.')
    finally:
        if created: cmd('rm','-f',name,check=False,timeout=60)

if __name__=='__main__': main()
