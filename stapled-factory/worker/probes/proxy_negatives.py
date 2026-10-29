#!/usr/bin/env python3
"""Unpaid real proxy namespace negatives, with authenticated TLS positive control.

Usage: proxy_negatives.py OPERATOR_RUNTIME_JSON PREBUILT_IMAGES_JSON
Uses a dummy key and never sends an HTTP/model request.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from provision_runtime import provision
from netns_firewall import verify_proxy_firewall
from container_negatives import verify
from provision_check import RESOURCE_LIMITS, validate_resources, resource_arguments


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def main():
    config = json.loads(Path(sys.argv[1]).read_text())['provision']
    images = json.loads(Path(sys.argv[2]).read_text())
    allocation = None
    allocation_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='factory-proxy-negatives-') as tmp:
        root = Path(tmp)
        workspace = root / 'workspace'; workspace.mkdir(mode=0o700)
        subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
        config.update(workspace=str(workspace), worker_image=images['worker_image'],
                      proxy_image=images['proxy_image'], helper_image=images['helper_image'],
                      worker_environment=images['worker_environment'])
        config['firewall']['helper_image'] = images['firewall_image']
        # A fresh allocation always owns fresh volumes, and no HTTP request occurs.
        command = config['proxy_command']
        if command[:2] != ['python3', '/opt/factory/egress_proxy.py']:
            raise ValueError('production proxy entrypoint required')
        command[command.index('--pricing-policy-expires-at')+1] = str(int(time.time())+3600)
        try:
            allocation = provision(**config, allocation_id=allocation_id, gateway_key=b'unpaid-negative-control')
            worker = json.loads(docker('inspect', allocation['worker']))[0]
            proxy = json.loads(docker('inspect', allocation['proxy']))[0]
            network = json.loads(docker('network', 'inspect', allocation['network']))[0]
            kernel_check = '''import json,pathlib,sys
limits=json.loads(sys.argv[1]);root=pathlib.Path('/sys/fs/cgroup')
actual={name:(root/name).read_text().strip() for name in ['memory.max','memory.swap.max','cpu.max','pids.max']}
assert actual['memory.max']==str(limits['Memory']),actual
assert actual['memory.swap.max']=='0',actual
assert actual['pids.max']==str(limits['PidsLimit']),actual
quota,period=actual['cpu.max'].split()
assert quota!='max' and int(quota)*1000000000==limits['NanoCpus']*int(period),actual
print('PASS kernel cgroup v2 resource limits',json.dumps(actual,sort_keys=True))
'''
            for role, container in [('worker',worker),('proxy',proxy)]:
                validate_resources(container, role)
                print(role, docker('exec',container['Id'],'python3','-c',kernel_check,json.dumps(RESOURCE_LIMITS[role])))
            print('helper',docker('run','--rm',*resource_arguments('helper'),'--network','none',
                '--cap-drop','ALL','--security-opt','no-new-privileges','--read-only',
                '--entrypoint','python3',images['helper_image'],'-c',kernel_check,json.dumps(RESOURCE_LIMITS['helper'])))
            assert worker['HostConfig']['NetworkMode'] == 'none'
            assert 'ALL' in worker['HostConfig']['CapDrop'] and 'ALL' in proxy['HostConfig']['CapDrop']
            verify_proxy_firewall(proxy['Id'], allocation_id, network['Id'], images['firewall_image'], config['firewall']['gateway_ip'])
            verify(allocation['worker'], root)
            script = '''import socket,ssl,sys
ip=sys.argv[1]
for family,host,port in [(socket.AF_INET,'1.1.1.1',443),(socket.AF_INET,'169.254.169.254',80),(socket.AF_INET,ip,80),(socket.AF_INET6,'2606:4700:4700::1111',443)]:
 s=socket.socket(family);s.settimeout(2)
 try:s.connect((host,port))
 except OSError:print('DENIED',host,port)
 else:raise AssertionError('proxy escaped to '+host+':'+str(port))
 finally:s.close()
# DNS nameserver requests use UDP53 and cannot bypass the OUTPUT policy.
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(2)
try:
 s.sendto(bytes.fromhex('123401000001000000000000')+b'\\x07example\\x03com\\x00\\x00\\x01\\x00\\x01',('1.1.1.1',53))
 s.recvfrom(1024)
except OSError:print('DENIED DNS UDP53')
else:raise AssertionError('proxy DNS escaped')
finally:s.close()
context=ssl.create_default_context()
with socket.create_connection((ip,443),timeout=5) as raw:
 with context.wrap_socket(raw,server_hostname='hermes-control.tail4296e6.ts.net') as tls:
  assert tls.getpeercert();print('PASS gateway TLS certificate hostname verified; no HTTP sent')
'''
            print(docker('exec', allocation['proxy'], 'python3', '-c', script, config['firewall']['gateway_ip']))
            verify_proxy_firewall(proxy['Id'], allocation_id, network['Id'], images['firewall_image'], config['firewall']['gateway_ip'])
            # A TLS handshake never reaches the HTTP proxy's reservation path.
            # Verify the real durable ledgers rather than inferring no spending.
            budget_check = """import sqlite3
db=sqlite3.connect('file:/budget/b.db?mode=ro',uri=True)
assert db.execute('SELECT COALESCE(SUM(used),0) FROM budgets').fetchone()[0]==0
assert db.execute('SELECT COALESCE(SUM(spent),0) FROM monetary_budgets').fetchone()[0]==0
print('PASS durable budget: zero request reservations and zero estimated spend')
"""
            print(docker('exec', allocation['proxy'], 'python3', '-c', budget_check))
            print('PASS actual proxy gateway-only egress and worker binding; no paid requests')
        finally:
            if allocation: allocation['proxy_process'].terminate()
            prefix = 'factory-'+allocation_id
            for role in ('worker','proxy'):
                subprocess.run(['docker','rm','-f',prefix+'-'+role], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['docker','network','rm',prefix+'-network'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for role in ('socket','budget','anchor'):
                subprocess.run(['docker','volume','rm',prefix+'-'+role], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__': main()
