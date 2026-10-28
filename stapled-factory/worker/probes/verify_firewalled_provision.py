"""Disposable proxy-firewall probe; --live makes one paid gateway request.

Requires a locally cached python:3.11-slim image and Docker. No inference
credential is used unless --live is explicitly requested. Provisioning failures
can retain budget evidence volumes. Live reservations are only best-effort.
"""
import argparse, json, os, stat, subprocess, sys, tempfile, time, uuid
from pathlib import Path
ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT)
from guarded_store import GuardedAttemptStore
from provision_runtime import provision_once, inspect_bound_allocation, ProvisionError

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--live', action='store_true', help='make one billable request using the local DSH credential store')
live = parser.parse_args().live

def credential():
    path = Path.home() / '.dsh' / '.credentials.yaml'
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('credential store must be an owner-only regular file')
    result = subprocess.run(['ruby', '-ryaml', '-e',
        "data=YAML.safe_load(File.read(ARGV.fetch(0))); print data.fetch('refs').fetch('BIFROST_API_KEY')", str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    if not result.stdout or b'\n' in result.stdout or b'\r' in result.stdout:
        raise RuntimeError('invalid gateway credential')
    return result.stdout

def docker(*args):
    return subprocess.run(['docker', *args], check=True, capture_output=True, text=True).stdout.strip()

base = docker('image', 'inspect', 'python:3.11-slim', '--format', '{{.Id}}')
base_env = ['PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin']
scratch = tempfile.TemporaryDirectory(prefix='factory-egress-probe-')
directory = Path(scratch.name)
for name in ('egress_proxy.py', 'guarded_store.py', 'pinned_https.py', 'budget_authority.py'):
    (directory/name).write_bytes((Path(ROOT)/name).read_bytes())
suffix = uuid.uuid4().hex
tag = 'factory-egress-probe-' + suffix
worker_tag = 'factory-worker-probe-' + suffix
firewall_tag = 'factory-firewall-probe-' + suffix
allocation = None
try:
    (directory/'Dockerfile').write_bytes((Path(ROOT)/'Dockerfile.firewall').read_bytes())
    docker('build', '-t', firewall_tag, str(directory))
    firewall_image = docker('image', 'inspect', firewall_tag, '--format', '{{.Id}}')
    builder = docker('create', '--network', 'none', base, 'true')
    try:
        for name in ('egress_proxy.py', 'guarded_store.py', 'pinned_https.py', 'budget_authority.py'):
            docker('cp', str(directory/name), builder+':/tmp/'+name)
        exporter = subprocess.Popen(['docker','export',builder], stdout=subprocess.PIPE)
        importer = subprocess.run(['docker','import','--change','ENV '+base_env[0],'-',worker_tag], stdin=exporter.stdout, capture_output=True, check=True)
        exporter.stdout.close()
        if exporter.wait() != 0: raise RuntimeError('export failed')
    finally:
        docker('rm', '-f', builder)
    (directory/'Dockerfile').write_bytes((Path(ROOT)/'Dockerfile.proxy').read_bytes())
    docker('build', '--network', 'none', '-t', tag, str(directory))
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
        model = 'ollama-cloud/deepseek-v4.1-flash:1017' if live else 'deepseek-v4.1-flash:1017'
        config = dict(worker_image=worker_image, proxy_image=image, helper_image=base,
            worker_command=['python3','-c','import time;time.sleep(300)'],
            proxy_command=['python3','/opt/factory/egress_proxy.py','--socket','/gateway/api.sock',
                '--model',model,'--gateway-ip','100.92.236.42','--max-requests',('1' if live else '3'),
                '--budget-db','/budget/b.db','--budget-anchor','/anchor/b.json',
                '--initialize-budget-store','--attempt-id','probe','--attempt-fingerprint','a'*64,
                '--ceiling-microdollars','2000000','--worst-case-request-microdollars','500000',
                '--pricing-policy-id','synthetic-test','--pricing-policy-expires-at',str(int(time.time())+3600)],
            workspace=workspace, uid=10001, worker_environment=base_env,
            firewall={'helper_image':firewall_image, 'gateway_ip':'100.92.236.42'})
        allocation = provision_once(store, 'probe', 'b' * 64, 'probe-conversation', workspace_baseline=baseline,
                                    gateway_key=credential() if live else b'invalid-probe-key', **config)
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
        # Real host fixtures exist outside the sole workspace bind. Verify both
        # their host paths and conventional sandbox paths cannot be read.
        holdout = directory / 'holdout' / 'hidden-test.txt'
        holdout.parent.mkdir()
        holdout.write_text('disposable holdout sentinel')
        secret = directory / 'host-secret.txt'
        secret.write_text('disposable host secret sentinel')
        secret.chmod(0o600)
        denied_paths = [str(holdout), str(secret), '/holdout/hidden-test.txt',
                        '/root/.dsh/.credentials.yaml', '/budget/b.db', '/anchor/b.json',
                        '/var/run/docker.sock']
        isolation_script = "import pathlib\npaths=" + repr(denied_paths) + "\n" + '''
assert pathlib.Path('/workspace/.git/HEAD').read_text()
for value in paths:
 try: pathlib.Path(value).read_bytes()
 except OSError: pass
 else: raise AssertionError('host-only path readable: ' + value)
print('real host holdout, host secret, budget, anchor and Docker socket denied; workspace readable')
'''
        print(docker('exec', worker, 'python3', '-c', isolation_script))
        time.sleep(3)
        script = '''import socket, json
for target in [('1.1.1.1',443),('8.8.8.8',53),('169.254.169.254',80)]:
 s=socket.socket();s.settimeout(2)
 try: s.connect(target);print('direct_allowed', target)
 except OSError: print('direct_blocked', target)
 s.close()
try: socket.getaddrinfo('hermes-control.tail4296e6.ts.net',443);print('dns_allowed')
except OSError: print('dns_blocked')
for request_type in REQUEST_TYPES:
 s=socket.socket(socket.AF_UNIX);s.settimeout(120);s.connect('/gateway/api.sock')
 b=json.dumps({'model':MODEL,'max_tokens':16 if LIVE else 1,'messages':[{'role':'user','content':request_type}]}).encode()
 s.sendall(b'POST /v1/messages HTTP/1.0\\r\\nHost: gateway\\r\\nContent-Type: application/json\\r\\nContent-Length: '+str(len(b)).encode()+b'\\r\\n\\r\\n'+b)
 response=b''
 while True:
  part=s.recv(65536)
  if not part: break
  response+=part
  if len(response)>1048576: raise RuntimeError('response too large')
 s.close()
 headers,body=response.split(b'\\r\\n\\r\\n',1)
 status=headers.split(b'\\r\\n')[0].decode()
 print('proxy_status',request_type,status)
 if LIVE and status=='HTTP/1.0 200 OK':
  result=json.loads(body)
  if result.get('role')!='assistant' or not result.get('content') or not isinstance(result.get('usage'),dict): raise RuntimeError('invalid model response')
  print('live model response and usage verified')
'''
        script = 'MODEL='+repr(model)+'\nLIVE='+repr(live)+'\nREQUEST_TYPES='+repr(('Say hello briefly.',) if live else ('agent','title'))+'\n'+script
        output = docker('exec',worker,'python3','-c',script)
        print(output)
        if (output.count('direct_blocked ') != 3 or 'direct_allowed ' in output or
                'dns_blocked' not in output or 'dns_allowed' in output or
                (output.count('HTTP/1.0 200 OK') != 1 or 'live model response and usage verified' not in output if live else output.count('HTTP/1.0 401 Unauthorized') != 2)):
            raise RuntimeError('gateway isolation negative control failed')
        ledger = docker('exec', allocation['proxy'], 'python3', '-c',
            "import sqlite3; d=sqlite3.connect('/budget/b.db'); "
            "print(*d.execute('select used, spent from budgets join monetary_budgets using (attempt)').fetchone())")
        if ledger != ('1 500000' if live else '2 1000000'):
            raise RuntimeError('synthetic requests did not share one durable ledger')
        print('best-effort reservation: '+ledger if live else 'synthetic ledger: 2 requests, 1000000 microdollars reserved')
        proxy_check = """import socket
for address, family in [(('1.1.1.1',443), socket.AF_INET), (('169.254.169.254',80), socket.AF_INET), (('100.92.236.42',80), socket.AF_INET), (('2606:4700:4700::1111',443), socket.AF_INET6)]:
 s=socket.socket(family); s.settimeout(2)
 try: s.connect(address)
 except OSError: pass
 else: raise AssertionError('proxy bypass reachable')
 finally: s.close()
try: socket.getaddrinfo('example.com',443)
except OSError: pass
else: raise AssertionError('proxy DNS reachable')
print('proxy public, metadata, wrong port, IPv6 and DNS denied')
"""
        print(docker('exec', allocation['proxy'], 'python3', '-c', proxy_check))
        docker('restart', allocation['proxy'])
        try:
            inspect_bound_allocation(store, 'probe', 'b' * 64, 'probe-conversation', workspace_baseline=baseline, **config)
        except ProvisionError:
            print('reinspection rejected restarted proxy')
        else:
            raise RuntimeError('proxy restart unexpectedly accepted')
finally:
    if allocation:
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
