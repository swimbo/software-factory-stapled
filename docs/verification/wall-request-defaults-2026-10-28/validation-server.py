"""Trusted protocol adapter: run the candidate wall CLI inside its isolated image."""
import json,subprocess,sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  status,body=(200,b'root') if self.path=='/' else ((200,b'OK') if self.path=='/ok' else (404,b'missing'))
  self.send_response(status);self.end_headers();self.wfile.write(body)
 def do_POST(self):
  if self.path!='/evaluate':self.send_response(404);self.end_headers();self.wfile.write(b'missing');return
  n=int(self.headers.get('Content-Length','0'));data=json.loads(self.rfile.read(n))
  r=subprocess.run([sys.executable,'stapled-factory/wall/evaluator/cli.py','--target','http://127.0.0.1:8000','--scenarios','/dev/stdin','--runs','1'],input=json.dumps({'scenarios':[data['scenario']]}),capture_output=True,text=True,timeout=10)
  self.send_response(200 if r.returncode==0 else 500);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(r.stdout.encode() if r.returncode==0 else b'{"error":"candidate CLI failed"}')
 def log_message(self,*args):pass
ThreadingHTTPServer(('127.0.0.1',8000),Handler).serve_forever()
