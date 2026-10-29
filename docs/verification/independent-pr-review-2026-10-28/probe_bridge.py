import importlib.util,subprocess,threading,json,http.client,pathlib
from http.server import ThreadingHTTPServer
p=pathlib.Path(__file__).resolve().parents[3]/'stapled-factory/spine/prebuilt_validation.py'
spec=importlib.util.spec_from_file_location('bridge',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
app='''from http.server import BaseHTTPRequestHandler,HTTPServer
import json
class H(BaseHTTPRequestHandler):
 def respond(self):
  n=int(self.headers.get('Content-Length','0'));b=self.rfile.read(n).decode()
  d=json.dumps({'method':self.command,'path':self.path,'key':self.headers.get('X-Api-Key'),'cookie':self.headers.get('Cookie'),'host':self.headers.get('Host'),'body':b}).encode()
  self.send_response(200 if self.headers.get('X-Api-Key') else 401);self.send_header('Content-Length',str(len(d)));self.end_headers()
  if self.command!='HEAD':self.wfile.write(d)
 do_GET=do_POST=do_PUT=do_PATCH=do_DELETE=do_HEAD=do_OPTIONS=do_PURGE=respond
 def log_message(self,*a):pass
HTTPServer(('127.0.0.1',8000),H).serve_forever()
'''
c=None;transport=None;server=None
try:
 c=subprocess.check_output(['docker','run','-d','--platform','linux/amd64','--network','none','--read-only','--user','65534:65534','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','64','--memory','128m','--cpus','1','--entrypoint','python3','stapled-wall-defaults:20261028','-u','-c',app],text=True).strip()
 subprocess.run(['docker','exec',c,'python3','-c',"import socket,time\nfor _ in range(30):\n try:s=socket.create_connection(('127.0.0.1',8000));s.close();break\n except OSError:time.sleep(.1)\nelse:raise RuntimeError('not ready')"],check=True)
 transport=subprocess.Popen(['docker','exec','-i',c,'python3','-u','-c',m.CLIENT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 server=ThreadingHTTPServer(('127.0.0.1',0),m.forwarder_handler(transport,threading.Lock()));threading.Thread(target=server.serve_forever,daemon=True).start()
 def req(method,headers,body=None):
  conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
  try:conn.request(method,'/probe?value=1',body=body,headers=headers);r=conn.getresponse();return r.status,r.read()
  finally:conn.close()
 assert req('GET',{})[0]==401
 results=[]
 for method in ['GET','POST','PUT','PATCH','DELETE','OPTIONS','PURGE','HEAD']:
  status,body=req(method,{'X-Api-Key':'bad','Cookie':'session=bad','Host':'elsewhere.invalid:1234'},'probe')
  assert status==200
  if method!='HEAD':
   data=json.loads(body);assert data=={'method':method,'path':'/probe?value=1','key':'bad','cookie':'session=bad','host':'elsewhere.invalid:1234','body':'probe'}
  else:assert body==b''
  results.append({'method':method,'status':status,'candidate_received_exact_inputs':True})
 i=json.loads(subprocess.check_output(['docker','inspect',c],text=True))[0]
 assert i['HostConfig']['NetworkMode']=='none' and not i['Mounts'] and i['Config']['User']=='65534:65534' and i['HostConfig']['ReadonlyRootfs']
 result={'scope':'unpaid real docker-exec bridge integration on local Docker, not NUC deployment or model execution','source_sha256':__import__('hashlib').sha256(p.read_bytes()).hexdigest(),'image':i['Image'],'missing_key_status':401,'invalid_supplied_key_status':200,'methods':results,'network':'none','user':'65534:65534','readonly':True,'mounts':[],'host_header_cannot_change_destination':True,'paid_requests':0}
 pathlib.Path('/tmp/stapled-review-bridge-docker.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'verified':True,'methods':len(results),'missing_and_invalid_credentials_distinct':True,'network':'none','paid_requests':0}))
finally:
 if server:server.shutdown();server.server_close()
 if transport:transport.terminate();transport.wait(timeout=10)
 if c:subprocess.run(['docker','rm','-f',c],check=True,stdout=subprocess.DEVNULL)
