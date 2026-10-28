"""Disposable experimental successor and proxy-firewall integration probe using a dummy credential.

Requires a locally cached python:3.11-slim image and Docker. No inference
credential is used. Provisioning failures can retain budget evidence volumes.
"""
import json, os, subprocess, sys, tempfile, time, uuid
from pathlib import Path
ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT)
from guarded_store import GuardedAttemptStore
from provision_runtime import provision_once, inspect_bound_allocation, ProvisionError
from crash_fence import fence_bound_worker
from successor_runtime import provision_successor, inspect_successor
import hashlib

def docker(*args):
    return subprocess.run(['docker', *args], check=True, capture_output=True, text=True).stdout.strip()

base = docker('image', 'inspect', 'python:3.11-slim', '--format', '{{.Id}}')
base_env = ['PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin']
scratch = tempfile.TemporaryDirectory(prefix='factory-egress-probe-')
directory = Path(scratch.name)
for name in ('egress_proxy.py', 'guarded_store.py', 'pinned_https.py'):
    (directory/name).write_bytes((Path(ROOT)/name).read_bytes())
suffix = uuid.uuid4().hex
tag = 'factory-egress-probe-' + suffix
worker_tag = 'factory-worker-probe-' + suffix
firewall_tag = 'factory-firewall-probe-' + suffix
allocation = None
successor_id = None
try:
    (directory/'Dockerfile').write_text('FROM python:3.11-slim\nRUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iptables && rm -rf /var/lib/apt/lists/*\n')
    docker('build', '-t', firewall_tag, str(directory))
    firewall_image = docker('image', 'inspect', firewall_tag, '--format', '{{.Id}}')
    builder = docker('create', '--network', 'none', base, 'true')
    try:
        for name in ('egress_proxy.py', 'guarded_store.py', 'pinned_https.py'):
            docker('cp', str(directory/name), builder+':/tmp/'+name)
        docker('commit', builder, tag)
        exporter = subprocess.Popen(['docker','export',builder], stdout=subprocess.PIPE)
        importer = subprocess.run(['docker','import','--change','ENV '+base_env[0],'-',worker_tag], stdin=exporter.stdout, capture_output=True, check=True)
        exporter.stdout.close()
        if exporter.wait() != 0: raise RuntimeError('export failed')
    finally:
        docker('rm', '-f', builder)
    image = docker('image', 'inspect', tag, '--format', '{{.Id}}')
    worker_image = docker('image', 'inspect', worker_tag, '--format', '{{.Id}}')
    with tempfile.TemporaryDirectory(prefix='factory-workspace-') as workspace:
        workspace = os.path.realpath(workspace)
        os.chmod(workspace, 0o700)
        subprocess.run(['git', 'init', '-q', workspace], check=True)
        subprocess.run(['git', '-C', workspace, '-c', 'user.name=Probe', '-c', 'user.email=probe@example.invalid',
                        'commit', '-q', '--allow-empty', '-m', 'baseline'], check=True)
        baseline = subprocess.run(['git', '-C', workspace, 'rev-parse', 'HEAD'], check=True,
                                  capture_output=True, text=True).stdout.strip()
        (directory / 'attempt-anchor').mkdir()
        store = GuardedAttemptStore.initialize(directory / 'attempt.db', directory / 'attempt-anchor' / 'watermark')
        store.reserve('probe', 'b' * 64, 'probe-conversation')
        config = dict(worker_image=worker_image, proxy_image=image, helper_image=base,
            worker_command=['python3','-c','import time;time.sleep(300)'],
            proxy_command=['python3','/tmp/egress_proxy.py','--socket','/gateway/api.sock',
                '--model','deepseek-v4.1-flash:1017','--gateway-ip','100.92.236.42','--max-requests','3',
                '--budget-db','/budget/b.db','--budget-anchor','/anchor/b.json',
                '--initialize-budget-store','--attempt-id','probe','--attempt-fingerprint','a'*64,
                '--ceiling-microdollars','2000000','--worst-case-request-microdollars','500000',
                '--pricing-policy-id','synthetic-test','--pricing-policy-expires-at',str(int(time.time())+3600)],
            workspace=workspace, uid=10001, worker_environment=base_env,
            firewall={'helper_image':firewall_image, 'gateway_ip':'100.92.236.42'})
        allocation = provision_once(store, 'probe', 'b' * 64, 'probe-conversation', workspace_baseline=baseline,
                                    gateway_key=b'invalid-probe-key', **config)
        bound = inspect_bound_allocation(store, 'probe', 'b' * 64, 'probe-conversation',
                                         workspace_baseline=baseline, **config)
        if bound['allocation_id'] != allocation['allocation_id']:
            raise RuntimeError('reinspected allocation identity differs')
        try:
            provision_once(store, 'probe', 'b' * 64, 'probe-conversation', workspace_baseline=baseline,
                           gateway_key=b'invalid-probe-key', **config)
        except ProvisionError:
            print('bound allocation reinspection passed; repeat allocation rejected')
        else:
            raise RuntimeError('repeat allocation unexpectedly succeeded')
        worker=allocation['worker']
        time.sleep(3)
        script = '''import socket, json
for target in [('1.1.1.1',443),('8.8.8.8',53),('169.254.169.254',80)]:
 s=socket.socket();s.settimeout(2)
 try: s.connect(target);print('direct_allowed', target)
 except OSError: print('direct_blocked', target)
 s.close()
try: socket.getaddrinfo('hermes-control.tail4296e6.ts.net',443);print('dns_allowed')
except OSError: print('dns_blocked')
for request_type in ('agent','title'):
 s=socket.socket(socket.AF_UNIX);s.settimeout(30);s.connect('/gateway/api.sock')
 b=json.dumps({'model':'deepseek-v4.1-flash:1017','max_tokens':1,'messages':[{'role':'user','content':request_type}]}).encode()
 s.sendall(b'POST /v1/messages HTTP/1.0\\r\\nHost: gateway\\r\\nContent-Type: application/json\\r\\nContent-Length: '+str(len(b)).encode()+b'\\r\\n\\r\\n'+b)
 print('proxy_status',request_type,s.recv(1024).split(b'\\r\\n')[0].decode());s.close()
'''
        output = docker('exec',worker,'python3','-c',script)
        print(output)
        if (output.count('direct_blocked ') != 3 or 'direct_allowed ' in output or
                'dns_blocked' not in output or 'dns_allowed' in output or
                output.count('HTTP/1.0 401 Unauthorized') != 2):
            raise RuntimeError('gateway isolation negative control failed')
        ledger = docker('exec', allocation['proxy'], 'python3', '-c',
            "import sqlite3; d=sqlite3.connect('/budget/b.db'); "
            "print(*d.execute('select used, spent from budgets join monetary_budgets using (attempt)').fetchone())")
        if ledger != '2 1000000':
            raise RuntimeError('synthetic requests did not share one durable ledger')
        print('synthetic ledger: 2 requests, 1000000 microdollars reserved')
        spec = {**config, 'workspace_baseline': baseline}
        digest = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        fence_bound_worker(store, 'probe', 'b' * 64, 'probe-conversation', digest)
        store.reserve_fenced_restart('probe', 'b' * 64, 'probe-conversation', digest,
                                     'successor', 'b' * 64, 'successor-conversation')
        successor_workspace = os.path.realpath(directory / 'successor-workspace')
        subprocess.run(['git', 'clone', '--no-hardlinks', '-q', workspace, successor_workspace], check=True)
        os.chmod(successor_workspace, 0o700)
        successor = provision_successor(store, 'probe', 'b' * 64, 'probe-conversation',
                                         'successor', 'b' * 64, 'successor-conversation',
                                         configuration=config, workspace=successor_workspace, baseline=baseline)
        successor_id = successor['worker_id']
        assert inspect_successor(store, 'probe', 'b' * 64, 'probe-conversation', 'successor',
                                 configuration=config, baseline=baseline) == successor
        assert docker('inspect', '--format', '{{.State.Running}}', allocation['worker']) == 'false'
        assert successor['proxy_id'] == bound['proxy_id']
        output = docker('exec', successor_id, 'python3', '-c', """import socket, json
s=socket.socket(socket.AF_UNIX);s.settimeout(30);s.connect('/gateway/api.sock')
b=json.dumps({'model':'deepseek-v4.1-flash:1017','max_tokens':1,'messages':[{'role':'user','content':'successor'}]}).encode()
s.sendall(b'POST /v1/messages HTTP/1.0\\r\\nHost: gateway\\r\\nContent-Type: application/json\\r\\nContent-Length: '+str(len(b)).encode()+b'\\r\\n\\r\\n'+b)
print(s.recv(1024).split(b'\\r\\n')[0].decode());s.close()
""")
        if 'HTTP/1.0 401 Unauthorized' not in output: raise RuntimeError('successor did not reach original proxy')
        ledger = docker('exec', allocation['proxy'], 'python3', '-c',
            "import sqlite3; d=sqlite3.connect('/budget/b.db'); print(*d.execute('select used, spent from budgets join monetary_budgets using (attempt)').fetchone())")
        if ledger != '3 1500000': raise RuntimeError('successor did not share original ledger: '+ledger)
        try:
            provision_successor(store, 'probe', 'b' * 64, 'probe-conversation',
                                'successor', 'b' * 64, 'successor-conversation', configuration=config,
                                workspace=successor_workspace, baseline=baseline)
        except Exception:
            pass
        else:
            raise RuntimeError('duplicate successor unexpectedly created')
        print('successor used original proxy and ledger; old worker stopped; repeat rejected; synthetic reservation total 1500000 microdollars')

finally:
    if successor_id:
        subprocess.run(['docker','rm','-f',successor_id],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if allocation:
        # The deterministic name is unique to this probe's allocation and also
        # catches a failed create/start before a successor ID was returned.
        subprocess.run(['docker','rm','-f','factory-'+allocation['allocation_id']+'-successor'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        allocation['proxy_process'].terminate()
        for container in (allocation['worker'], allocation['proxy']):
            subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['docker','network','rm',allocation['network']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for key in ('socket_volume','budget_volume','anchor_volume'):
            subprocess.run(['docker','volume','rm',allocation[key]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['docker','image','rm',firewall_tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['docker','image','rm',worker_tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['docker','image','rm',tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    scratch.cleanup()
