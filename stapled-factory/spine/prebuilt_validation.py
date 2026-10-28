"""Trusted NUC candidate validation: prebuilt images, network-none, no remote build."""
import base64
import hashlib
import io
import json
import re
import subprocess
import tarfile
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CLIENT='''import sys,json,http.client,base64
r=json.load(sys.stdin)
c=http.client.HTTPConnection("127.0.0.1",8000,timeout=10)
c.request(r["method"],r["path"],body=base64.b64decode(r["body"]),headers=r["headers"])
s=c.getresponse();body=s.read(1048577)
if len(body)>1048576:raise ValueError("candidate response too large")
print(json.dumps({"status":s.status,"body":base64.b64encode(body).decode()}))
'''

def command(*args):
    return subprocess.check_output(args,text=True).strip()

def validate_prebuilt(archive,revision,image,scenarios,diff_file):
    if not re.fullmatch(r'[a-f0-9]{40}',revision):raise ValueError('exact revision required')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',image):raise ValueError('immutable local image required')
    raw=Path(archive).read_bytes();digest=hashlib.sha256(raw).hexdigest()
    scenario_digest=hashlib.sha256(Path(scenarios).read_bytes()).hexdigest()
    metadata=json.loads(command('docker','image','inspect',image))[0]
    labels=metadata['Config']['Labels'] or {}
    if labels.get('factory.git-revision')!=revision or labels.get('factory.source-archive-sha256')!=digest:
        raise ValueError('prebuilt source binding mismatch')
    container=None;server=None
    with tempfile.TemporaryDirectory(prefix='prebuilt-validation-') as temp:
        source=Path(temp)/'source';source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:tar.extractall(source,filter='data')
        security=json.loads(command('python3',str(ROOT/'security/pipeline_cli.py'),'--diff-file',str(diff_file),'--target',str(source)))
        try:
            container=command('docker','run','-d','--network=none','--read-only','--user=65534:65534','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=64','--memory=128m','--cpus=1',image)
            def identity():
                state=json.loads(command('docker','inspect',container))[0]
                if state['Id']!=container or state['Image']!=metadata['Id'] or not state['State']['Running'] or state['HostConfig']['NetworkMode']!='none' or state.get('Mounts'):
                    raise ValueError('candidate identity/isolation changed')
            class Forwarder(BaseHTTPRequestHandler):
                def do_GET(self):self.forward()
                def do_POST(self):self.forward()
                def forward(self):
                    identity()
                    length=int(self.headers.get('Content-Length','0'))
                    if length>1048576: self.send_error(413);return
                    # Every destination is fixed inside the network-none candidate.
                    request=dict(method=self.command,path=self.path,body=base64.b64encode(self.rfile.read(length)).decode(),headers={k:v for k,v in self.headers.items() if k.lower() in ('content-type','authorization')})
                    response=json.loads(subprocess.check_output(['docker','exec','-i',container,'python3','-c',CLIENT],input=json.dumps(request),text=True,timeout=15))
                    body=base64.b64decode(response['body']);self.send_response(response['status']);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
                def log_message(self,*args):pass
            identity();time.sleep(.3)
            server=ThreadingHTTPServer(('127.0.0.1',0),Forwarder)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            target='http://127.0.0.1:'+str(server.server_port)
            wall=json.loads(command('python3',str(ROOT/'wall/evaluator/cli.py'),'--target',target,'--scenarios',str(scenarios),'--runs','3'))
            identity()
            if hashlib.sha256(Path(scenarios).read_bytes()).hexdigest()!=scenario_digest:raise ValueError('scenario set changed')
            return dict(scope='nuc-disposable-candidate',git_revision=revision,artifact_digest=metadata['Id'],deployment_id=container,source_archive_digest=digest,scenario_digest=scenario_digest,security=security,wall=wall,passed=security['verdict']=='pass' and wall['verdict']['passed'])
        finally:
            if server:server.shutdown();server.server_close()
            if container:subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,check=True)
