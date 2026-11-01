"""Trusted NUC candidate validation: prebuilt images, network-none, no remote build."""
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import re
import os
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CLIENT="""import sys,json,http.client,base64
for line in sys.stdin:
 r=json.loads(line)
 c=http.client.HTTPConnection("127.0.0.1",8000,timeout=10)
 c.request(r["method"],r["path"],body=base64.b64decode(r["body"]),headers=r["headers"])
 response=c.getresponse();body=response.read(1048577)
 if len(body)>1048576:raise ValueError("candidate response too large")
 print(json.dumps({"status":response.status,"body":base64.b64encode(body).decode()}),flush=True)
 c.close()
"""


def forwarder_handler(transport, transport_lock):
    """Keep scenario HTTP semantics while fixing transport to the candidate."""
    class Forwarder(BaseHTTPRequestHandler):
        def __getattr__(self, name):
            # BaseHTTPRequestHandler dispatches through do_<method>. Even a
            # candidate-specific verb must reach the candidate, not a proxy 501.
            if name.startswith('do_'):
                return self.forward
            raise AttributeError(name)

        def forward(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                self.send_error(400)
                return
            if not 0 <= length <= 1048576:
                self.send_error(413)
                return
            hop_by_hop = {'connection', 'keep-alive', 'proxy-authenticate',
                          'proxy-authorization', 'te', 'trailer',
                          'transfer-encoding', 'upgrade', 'content-length'}
            for value in self.headers.get_all('Connection', []):
                hop_by_hop.update(token.strip().lower() for token in value.split(','))
            headers = {k: v for k, v in self.headers.items() if k.lower() not in hop_by_hop}
            # CLIENT always connects to loopback:8000 in the network-none
            # candidate. Host and other scenario headers cannot change it.
            request = dict(method=self.command, path=self.path,
                           body=base64.b64encode(self.rfile.read(length)).decode(),
                           headers=headers)
            with transport_lock:
                if hasattr(transport, 'exchange'):
                    response = transport.exchange(request)
                else:
                    transport.stdin.write(json.dumps(request)+'\n')
                    transport.stdin.flush()
                    response=json.loads(transport.stdout.readline(1500000))
            body=base64.b64decode(response['body'])
            self.send_response(response['status'])
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self,*args):pass
    return Forwarder

def command(*args, timeout=30):
    return subprocess.check_output(args,text=True,timeout=timeout).strip()


def record(path, value):
    """Append an immutable, fsynced private recovery record before effects."""
    data = (json.dumps(value, sort_keys=True) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def candidate_lifecycle(evidence_dir, image):
    """One original target only; never recreate an uncertain validation run."""
    evidence = Path(evidence_dir)
    try:
        evidence.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError('original validation exists; inspect retained evidence, never recreate')
    name = 'factory-validation-' + uuid.uuid4().hex
    operation = uuid.uuid4().hex
    record(evidence / 'intent.json', dict(name=name, image=image, operation=operation))
    container = None

    def identity(selector):
        value = json.loads(command('docker', 'inspect', selector))[0]
        if (not re.fullmatch(r'[a-f0-9]{64}', value['Id']) or
                container is not None and value['Id'] != container or
                value['Name'] != '/' + name or value['Image'] != image or
                value['Config']['Labels'].get('factory.validation') != operation or
                value['HostConfig']['NetworkMode'] != 'none' or
                not value['HostConfig']['ReadonlyRootfs'] or value.get('Mounts')):
            raise ValueError('validation container identity/isolation differs; preserve uncertainty')
        return value

    try:
        try:
            container = command('docker', 'create', '--name', name,
                '--label', 'factory.validation=' + operation, '--network=none',
                '--read-only', '--user=65534:65534', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--pids-limit=64',
                '--memory=128m', '--cpus=1', image)
        except (OSError, subprocess.SubprocessError):
            # The daemon may have accepted create. Resolve only our durable name
            # and label; inspect failure leaves the original intent for recovery.
            container = identity(name)['Id']
            record(evidence / 'container.json', dict(container_id=container))
            raise
        identity(container)
        record(evidence / 'container.json', dict(container_id=container))
        command('docker', 'start', container)
        state = identity(container)
        if not state['State']['Running'] or state['State']['Restarting']:
            raise ValueError('validation container not running')
        yield container
    finally:
        if container is not None:
            state = identity(container)
            if state['State']['Running'] or state['State']['Restarting']:
                command('docker', 'stop', '--time', '5', container, timeout=15)
            state = identity(container)
            if state['State']['Running'] or state['State']['Restarting']:
                raise ValueError('original validation target not confirmed stopped')
            record(evidence / 'stopped.json', dict(container_id=container, stopped=True))


class CandidateTransport:
    def __init__(self, container):
        self.container = container

    def exchange(self, request):
        result = subprocess.run(
            ['docker', 'exec', '-i', self.container, 'python3', '-u', '-c', CLIENT],
            input=json.dumps(request) + '\n', capture_output=True, text=True,
            timeout=15, check=True)
        if len(result.stdout) > 1500000:
            raise ValueError('validation transport response too large')
        return json.loads(result.stdout)

def validate_prebuilt(archive,revision,image,scenarios,diff_file,repo,baseline,manifest,signature,public_key, *, evidence_dir=None):
    if not re.fullmatch(r'[a-f0-9]{40}',revision):raise ValueError('exact revision required')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',image):raise ValueError('immutable local image required')
    actual=command('git','-C',str(repo),'rev-parse',revision+'^{commit}')
    if actual!=revision:raise ValueError('revision mismatch')
    command('git','-C',str(repo),'merge-base','--is-ancestor',baseline,revision)
    derived=subprocess.check_output(['git','-C',str(repo),'archive',revision])
    raw=Path(archive).read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if raw!=derived:raise ValueError('archive differs from exact commit')
    diff=subprocess.check_output(['git','-C',str(repo),'diff',baseline,revision])
    if Path(diff_file).read_bytes().rstrip(b'\n')!=diff.rstrip(b'\n'):raise ValueError('diff differs from selected commits')
    subprocess.run(['openssl','pkeyutl','-verify','-pubin','-inkey',str(public_key),'-rawin','-in',str(manifest),'-sigfile',str(signature)],check=True,stdout=subprocess.DEVNULL)
    binding=json.loads(Path(manifest).read_text())
    if hashlib.sha256(Path(manifest).with_name('Dockerfile').read_bytes()).hexdigest()!=binding['dockerfile_sha256']:raise ValueError('signed Dockerfile mismatch')
    if binding['revision']!=revision or binding['baseline']!=baseline or binding['archive_sha256']!=digest or binding['diff_sha256']!=hashlib.sha256(diff).hexdigest():raise ValueError('signed build source mismatch')
    # Docker save gives the exact config bytes underlying the image selected by
    # the trusted daemon; config IDs and OCI manifest IDs differ across engines.
    with tempfile.TemporaryDirectory(prefix='image-binding-') as tmp:
        saved=Path(tmp)/'image.tar'
        subprocess.run(['docker','save','-o',str(saved),image],check=True,timeout=180)
        with tarfile.open(saved) as tar:
            entries=json.load(tar.extractfile('manifest.json'))
            if len(entries)!=1:raise ValueError('ambiguous saved image')
            config=tar.extractfile(entries[0]['Config']).read()
        if 'sha256:'+hashlib.sha256(config).hexdigest()!=binding['config_digest']:raise ValueError('signed build artifact mismatch')
    scenario_digest=hashlib.sha256(Path(scenarios).read_bytes()).hexdigest()
    metadata=json.loads(command('docker','image','inspect',image))[0]
    labels=metadata['Config']['Labels'] or {}
    if labels.get('factory.git-revision')!=revision or labels.get('factory.source-archive-sha256')!=digest:
        raise ValueError('prebuilt source binding mismatch')
    if evidence_dir is None:
        raise ValueError('durable validation evidence directory required')
    server=None
    with tempfile.TemporaryDirectory(prefix='prebuilt-validation-') as temp:
        source=Path(temp)/'source';source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:tar.extractall(source,filter='data')
        trusted_diff=Path(temp)/'trusted.diff';trusted_diff.write_bytes(diff)
        security=json.loads(command('python3',str(ROOT/'security/pipeline_cli.py'),'--diff-file',str(trusted_diff),'--target',str(source),timeout=180))
        with candidate_lifecycle(evidence_dir, image) as container:
            def identity():
                state=json.loads(command('docker','inspect',container))[0]
                if state['Id']!=container or state['Image']!=metadata['Id'] or not state['State']['Running'] or state['HostConfig']['NetworkMode']!='none' or state.get('Mounts'):
                    raise ValueError('candidate identity/isolation changed')
            transport=CandidateTransport(container)
            transport_lock=threading.Lock()
            identity();time.sleep(.3)
            server=ThreadingHTTPServer(('127.0.0.1',0),forwarder_handler(transport,transport_lock))
            threading.Thread(target=server.serve_forever,daemon=True).start()
            target='http://127.0.0.1:'+str(server.server_port)
            try:
                wall=json.loads(command('python3',str(ROOT/'wall/evaluator/cli.py'),'--target',target,'--scenarios',str(scenarios),'--runs','3',timeout=180))
                identity()
                if hashlib.sha256(Path(scenarios).read_bytes()).hexdigest()!=scenario_digest:raise ValueError('scenario set changed')
                result = dict(scope='nuc-disposable-candidate',git_revision=revision,artifact_digest=metadata['Id'],deployment_id=container,source_archive_digest=digest,scenario_digest=scenario_digest,security=security,wall=wall,passed=security['verdict']=='pass' and wall['verdict']['passed'])
                record(Path(evidence_dir) / 'checks.json', result)
            finally:
                server.shutdown();server.server_close()
        record(Path(evidence_dir) / 'result.json', result)
        return result
