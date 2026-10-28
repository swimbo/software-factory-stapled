"""Install per-proxy network-namespace firewall rules before credential release.

This primitive assumes the exact helper image and Docker daemon are trusted. It
must be called by a trusted provisioner before releasing a credential. A Docker
administrator can modify the namespace later; resource inspection alone does
not establish image provenance or protect against a privileged host adversary.
"""
import ipaddress
import json
import re
import subprocess
from provision_check import resource_arguments


class FirewallError(RuntimeError):
    pass


def _run(args):
    try:
        return subprocess.run(['docker', *args], check=True, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise FirewallError('Docker firewall operation failed') from exc


def _check_proxy_firewall(proxy_id, allocation_id, network_id, helper_image_id, gateway_ip, *, install):
    if not re.fullmatch(r'[0-9a-f]{64}', proxy_id or '') or not re.fullmatch(r'[0-9a-f]{64}', network_id or ''):
        raise FirewallError('full proxy and network IDs required')
    if not re.fullmatch(r'[0-9a-f]{32}', allocation_id or '') or not re.fullmatch(r'sha256:[0-9a-f]{64}', helper_image_id or ''):
        raise FirewallError('full allocation and image IDs required')
    try:
        ip = ipaddress.IPv4Address(gateway_ip)
    except (ValueError, TypeError) as exc:
        raise FirewallError('IPv4 address required') from exc
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local or int(ip) == 0xffffffff:
        raise FirewallError('invalid gateway IPv4 address')
    try:
        proxy = json.loads(_run(['inspect', '--type', 'container', proxy_id]))[0]
        image = json.loads(_run(['inspect', '--type', 'image', helper_image_id]))[0]
        config = proxy['HostConfig']
        labels = proxy['Config']['Labels']
        networks = proxy['NetworkSettings']['Networks']
        if (proxy['Id'] != proxy_id or image['Id'] != helper_image_id
                or not proxy['State']['Running'] or not proxy['State'].get('Pid')
                or not proxy['State'].get('StartedAt') or labels.get('factory.allocation') != allocation_id
                or labels.get('factory.role') != 'proxy'
                or config.get('Privileged') or config.get('NetworkMode') in ('host', 'none', 'default')
                or str(config.get('NetworkMode', '')).startswith('container:')
                or config.get('CapAdd') or 'ALL' not in (config.get('CapDrop') or [])
                or len(networks) != 1 or next(iter(networks.values()))['NetworkID'] != network_id):
            raise FirewallError('proxy identity or namespace is not trusted')
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise FirewallError('proxy identity unavailable') from exc
    # Set DROP before flushing, so failure while applying rules does not open
    # egress. The helper inherits neither credentials nor proxy mounts.
    command = ('set -eu; iptables -P OUTPUT DROP; ip6tables -P OUTPUT DROP; '
               'iptables -F OUTPUT; ip6tables -F OUTPUT; '
               f'iptables -A OUTPUT -d {ip}/32 -p tcp --dport 443 -j ACCEPT; '
               'iptables -S OUTPUT; echo IPv6; ip6tables -S OUTPUT')
    if not install:
        command = 'set -eu; iptables -S OUTPUT; echo IPv6; ip6tables -S OUTPUT'
    output = _run(['run', '--rm', *resource_arguments('helper'), '--network', 'container:' + proxy_id,
                   '--cap-drop', 'ALL', '--cap-add', 'NET_ADMIN', '--security-opt', 'no-new-privileges',
                   '--read-only', '--tmpfs', '/run:rw,nosuid,nodev,noexec', '--user', '0:0',
                   '--entrypoint', '/bin/sh', helper_image_id, '-c', command])
    expected = ['-P OUTPUT DROP',
                f'-A OUTPUT -d {ip}/32 -p tcp -m tcp --dport 443 -j ACCEPT',
                'IPv6', '-P OUTPUT DROP']
    if output.splitlines() != expected:
        raise FirewallError('firewall rule verification failed')
    try:
        after = json.loads(_run(['inspect', '--type', 'container', proxy_id]))[0]
        if (after['Id'] != proxy_id or not after['State']['Running']
                or after['State']['Pid'] != proxy['State']['Pid']
                or after['State']['StartedAt'] != proxy['State']['StartedAt']
                or after['NetworkSettings']['Networks'] != networks):
            raise FirewallError('proxy namespace changed during firewall setup')
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise FirewallError('proxy namespace unavailable after setup') from exc


def install_proxy_firewall(proxy_id, allocation_id, network_id, helper_image_id, gateway_ip):
    """Install and verify rules in an isolated namespace before releasing credentials."""
    return _check_proxy_firewall(proxy_id, allocation_id, network_id, helper_image_id, gateway_ip, install=True)


def verify_proxy_firewall(proxy_id, allocation_id, network_id, helper_image_id, gateway_ip):
    """Verify existing rules; never repair a changed or restarted namespace."""
    return _check_proxy_firewall(proxy_id, allocation_id, network_id, helper_image_id, gateway_ip, install=False)
