"""Actual confined-worker negative controls, with a workspace positive control."""
import json
import subprocess


def verify(container, root):
    sentinel = root / 'host-secret-negative-control'
    sentinel.write_text('synthetic-host-only-fixture')
    sentinel.chmod(0o600)
    script = '''import json,os,pathlib,socket
assert os.getuid()!=0
for path in json.loads(os.environ['PROBE_DENIED_PATHS']):
 try:
  open(path,'rb').read(1)
 except OSError: pass
 else: raise AssertionError('host path readable: '+path)
assert not pathlib.Path('/var/run/docker.sock').exists()
s=socket.socket();s.settimeout(2)
try:s.connect(('1.1.1.1',443))
except OSError:pass
else:raise AssertionError('direct network reachable')
finally:s.close()
try:socket.getaddrinfo('example.com',443)
except OSError:pass
else:raise AssertionError('DNS reachable')
try:pathlib.Path('/factory-negative-control').write_text('x')
except OSError:pass
else:raise AssertionError('root filesystem writable')
p=pathlib.Path('/workspace/.isolation-positive');p.write_text('ok');p.unlink()
print('PASS isolation: nonroot, host fixtures/stores/socket denied, direct TCP/DNS denied, root-directory write denied, workspace writable')
'''
    paths = [str(sentinel), str(root / 'db'), str(root / 'budget.db'), '/budget/b.db', '/anchor/b.json']
    result = subprocess.check_output(['docker', 'exec', '-e', 'PROBE_DENIED_PATHS='+json.dumps(paths),
                                      container, 'python', '-c', script], text=True)
    print(result.strip(), flush=True)
