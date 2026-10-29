import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import netns_firewall as firewall


PROXY, NETWORK, ALLOC, IMAGE, IP = 'a'*64, 'b'*64, 'c'*32, 'sha256:'+'d'*64, '100.92.236.42'
RULES = '\n'.join(['-P OUTPUT DROP',
                   '-A OUTPUT -d 100.92.236.42/32 -p tcp -m tcp --dport 443 -j ACCEPT',
                   'IPv6', '-P OUTPUT DROP'])


def proxy():
    return {'Id': PROXY, 'State': {'Running': True, 'Pid': 123, 'StartedAt': 'time-1'},
            'Config': {'Labels': {'factory.allocation': ALLOC, 'factory.role': 'proxy'}},
            'HostConfig': {'NetworkMode': 'factory-network', 'Privileged': False, 'CapAdd': None, 'CapDrop': ['ALL']},
            'NetworkSettings': {'Networks': {'factory-network': {'NetworkID': NETWORK}}}}


def setup(monkeypatch, record, item=None, rules=RULES):
    def run(args):
        record.append(args)
        if args[:3] == ['inspect', '--type', 'container']:
            return json.dumps([item if item is not None else proxy()])
        if args[:3] == ['inspect', '--type', 'image']:
            return json.dumps([{'Id': IMAGE}])
        return rules
    monkeypatch.setattr(firewall, '_run', run)


def test_installs_and_verifies_before_caller_can_continue(monkeypatch):
    calls = []
    setup(monkeypatch, calls)
    firewall.install_proxy_firewall(PROXY, ALLOC, NETWORK, IMAGE, IP)
    run = next(call for call in calls if call[0] == 'run')
    assert run[0:2] == ['run', '--rm']
    assert run[run.index('--network')+1] == 'container:' + PROXY
    assert '--cap-add' in run and 'NET_ADMIN' in run
    assert run[-1].index('iptables -P OUTPUT DROP') < run[-1].index('iptables -F OUTPUT')


@pytest.mark.parametrize('change', ['host', 'privileged', 'caps', 'identity', 'network'])
def test_fails_before_mutation_when_target_untrusted(monkeypatch, change):
    item = proxy()
    if change == 'host': item['HostConfig']['NetworkMode'] = 'host'
    if change == 'privileged': item['HostConfig']['Privileged'] = True
    if change == 'caps': item['HostConfig']['CapAdd'] = ['NET_ADMIN']
    if change == 'identity': item['Config']['Labels']['factory.allocation'] = '0'*32
    if change == 'network': item['NetworkSettings']['Networks']['factory-network']['NetworkID'] = '0'*64
    calls = []
    setup(monkeypatch, calls, item)
    with pytest.raises(firewall.FirewallError): firewall.install_proxy_firewall(PROXY, ALLOC, NETWORK, IMAGE, IP)
    assert not any(c[0] == 'run' for c in calls)


def test_unexpected_rules_fail_closed(monkeypatch):
    calls = []
    setup(monkeypatch, calls, rules=RULES+'\n-A OUTPUT -j ACCEPT')
    with pytest.raises(firewall.FirewallError): firewall.install_proxy_firewall(PROXY, ALLOC, NETWORK, IMAGE, IP)


def test_gateway_cannot_inject_commands(monkeypatch):
    calls = []
    setup(monkeypatch, calls)
    with pytest.raises(firewall.FirewallError): firewall.install_proxy_firewall(PROXY, ALLOC, NETWORK, IMAGE, '1.1.1.1; id')
    assert not calls


def test_restart_during_install_is_rejected(monkeypatch):
    before = proxy()
    after = proxy()
    after['State']['StartedAt'] = 'time-2'
    calls = []
    def run(args):
        calls.append(args)
        if args[:3] == ['inspect', '--type', 'container']:
            return json.dumps([after if any(c[0] == 'run' for c in calls) else before])
        if args[:3] == ['inspect', '--type', 'image']:
            return json.dumps([{'Id': IMAGE}])
        return RULES
    monkeypatch.setattr(firewall, '_run', run)
    with pytest.raises(firewall.FirewallError):
        firewall.install_proxy_firewall(PROXY, ALLOC, NETWORK, IMAGE, IP)
