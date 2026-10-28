#!/usr/bin/env python3
"""Disposable namespace firewall feasibility test; not a production provisioner.

Builds a temporary helper image from python:3.11-slim with iptables. Requires
Docker build network and permission for NET_ADMIN in a container namespace.
No credentials are used, and only this probe's resources are removed.
"""
import argparse
import ipaddress
import os
import subprocess
import tempfile
import uuid
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from netns_firewall import install_proxy_firewall


def docker(*args, check=True, timeout=60):
    return subprocess.run(['docker', *args], check=check, capture_output=True,
                          text=True, timeout=timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway-ip', required=True, type=ipaddress.IPv4Address)
    parser.add_argument('--gateway-host', required=True)
    args = parser.parse_args()
    gateway_ip, host = str(args.gateway_ip), args.gateway_host
    if not host or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for c in host):
        parser.error('invalid gateway host')
    suffix = uuid.uuid4().hex
    target, tag, network = 'factory-fw-probe-' + suffix, 'factory-fw-probe:' + suffix, 'factory-fw-net-' + suffix
    with tempfile.TemporaryDirectory(prefix='factory-fw-probe-') as folder:
        with open(os.path.join(folder, 'Dockerfile'), 'w', encoding='utf-8') as f:
            f.write('FROM python:3.11-slim\nRUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iptables && rm -rf /var/lib/apt/lists/*\n')
        try:
            docker('build', '-t', tag, folder, timeout=120)
            docker('network', 'create', network)
            docker('run', '-d', '--name', target, '--network', network, '--label',
                   'factory.allocation=' + suffix, '--label', 'factory.role=proxy',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                   '--user', '10001:10001', '--mount', 'type=bind,src=' + str(Path(__file__).resolve().parents[1] / 'pinned_https.py') + ',dst=/pinned_https.py,readonly', 'python:3.11-slim', 'sleep', '180')
            proxy_id = docker('inspect', '--format', '{{.Id}}', target).stdout.strip()
            network_id = docker('network', 'inspect', '--format', '{{.Id}}', network).stdout.strip()
            image_id = docker('image', 'inspect', '--format', '{{.Id}}', tag).stdout.strip()
            install_proxy_firewall(proxy_id, suffix, network_id, image_id, gateway_ip)
            probe = '''import socket, sys, threading
sys.path.insert(0, '/')
from pinned_https import PinnedHTTPSConnection
ip, host = sys.argv[1:]
def blocked(address, family=socket.AF_INET):
 s=socket.socket(family); s.settimeout(2)
 try: s.connect(address)
 except OSError: return True
 finally: s.close()
 return False
assert blocked(('1.1.1.1',443))
assert blocked(('169.254.169.254',80))
assert blocked((ip,80))
assert blocked(('2606:4700:4700::1111',443),socket.AF_INET6)
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
assert blocked(listener.getsockname()), 'TCP loopback reachable'
listener.close()
try: socket.getaddrinfo('example.com',443)
except OSError: pass
else: raise AssertionError('DNS reachable')
conn=PinnedHTTPSConnection(host,443,gateway_ip=ip,timeout=5)
conn.request('GET','/anthropic/v1/models')
assert conn.getresponse().status==401
conn.close()
assert next(x for x in open('/proc/self/status') if x.startswith('CapEff:')).split()[1]=='0000000000000000'
print('gateway TLS 401; public, metadata, wrong port, IPv6, loopback and DNS denied; zero capabilities')
'''
            result = docker('exec', target, 'python', '-c', probe, gateway_ip, host, check=False)
            if result.returncode: raise RuntimeError('namespace probe failed: ' + result.stderr)
            print(result.stdout.strip())
            r = docker('run', '--rm', '--network', 'container:' + target, '--cap-drop', 'ALL',
                       '--security-opt', 'no-new-privileges', '--user', '10001:10001', '--entrypoint',
                       'iptables', tag, '-P', 'OUTPUT', 'ACCEPT', check=False)
            assert r.returncode != 0, 'unprivileged firewall modification succeeded'
            print('unprivileged firewall modification denied')
        finally:
            docker('rm', '-f', target, check=False)
            docker('network', 'rm', network, check=False)
            docker('image', 'rm', tag, check=False)


if __name__ == '__main__':
    main()
