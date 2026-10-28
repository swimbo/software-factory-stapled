import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from egress_guard import UnsafeTopology, validate


def topology():
    host = {'Privileged': False, 'CapDrop': ['ALL'], 'CapAdd': [],
            'SecurityOpt': ['no-new-privileges'], 'NetworkMode': 'isolated'}
    def container(id, networks):
        return {'Id': id, 'Config': {'User': '1000:1000'}, 'State': {'Running': True}, 'HostConfig': copy.deepcopy(host),
                'NetworkSettings': {'Networks': {n: {'NetworkID': n} for n in networks}}, 'Mounts': []}
    return [container('worker', ['internal']), container('proxy', ['internal', 'uplink']),
            {'Id': 'internal', 'Internal': True, 'Driver': 'bridge', 'Containers': {'worker': {}, 'proxy': {}}},
            {'Id': 'uplink', 'Internal': False, 'Containers': {'proxy': {}}}]


def test_accepts_only_restricted_topology():
    validate(*topology())


@pytest.mark.parametrize('mutate', [
    lambda t: t[2].update(Internal=False),
    lambda t: t[2]['Containers'].update(intruder={}),
    lambda t: t[0]['NetworkSettings']['Networks'].update(uplink={'NetworkID': 'uplink'}),
    lambda t: t[0]['HostConfig'].update(Privileged=True),
    lambda t: t[0]['HostConfig'].update(CapDrop=[]),
    lambda t: t[0]['HostConfig'].update(CapAdd=['NET_ADMIN']),
    lambda t: t[0]['HostConfig'].update(SecurityOpt=[]),
    lambda t: t[0]['HostConfig'].update(PortBindings={'3000/tcp': []}),
    lambda t: t[0]['Mounts'].append({'Source': '/var/run/docker.sock', 'Destination': '/sock'}),
    lambda t: t[1]['NetworkSettings']['Networks'].update(other={'NetworkID': 'other'}),
    lambda t: t[1]['State'].update(Running=False),
])
def test_rejects_unsafe_topologies(mutate):
    t = topology()
    mutate(t)
    with pytest.raises(UnsafeTopology):
        validate(*t)


def test_only_explicit_workspace_mount_allowed(tmp_path):
    t = topology()
    t[0]['Mounts'] = [{'Type': 'bind', 'Source': str(tmp_path), 'Destination': '/workspace'}]
    with pytest.raises(UnsafeTopology):
        validate(*t)
    validate(*t, workspace_source=str(tmp_path))
    t[0]['Mounts'][0]['Destination'] = '/etc'
    with pytest.raises(UnsafeTopology):
        validate(*t, workspace_source=str(tmp_path))

def test_host_namespace_and_extra_hosts_rejected():
    for field, value in [('PidMode', 'host'), ('IpcMode', 'host'), ('PidMode', 'container:other'), ('IpcMode', 'container:other'), ('ExtraHosts', ['host:host-gateway'])]:
        t = topology()
        t[0]['HostConfig'][field] = value
        with pytest.raises(UnsafeTopology):
            validate(*t)


def networkless():
    host = {'Privileged': False, 'CapDrop': ['ALL'], 'CapAdd': [], 'SecurityOpt': ['no-new-privileges'], 'NetworkMode': 'none'}
    volume = lambda name, dest, rw: {'Type': 'volume', 'Name': name, 'Destination': dest, 'RW': rw}
    worker = {'Id': 'w', 'Config': {'User': '1000:1000'}, 'State': {'Running': True}, 'HostConfig': copy.deepcopy(host),
              'NetworkSettings': {'Networks': {'none': {'NetworkID': 'none'}}}, 'Mounts': [volume('socket', '/gateway', False)]}
    proxy = {'Id': 'p', 'Config': {'User': '1000:1000'}, 'State': {'Running': True}, 'HostConfig': copy.deepcopy(host),
             'NetworkSettings': {'Networks': {'uplink': {'NetworkID': 'uplink-id'}}},
             'Mounts': [volume('socket', '/gateway', True), volume('budget', '/budget', True)]}
    proxy['HostConfig']['NetworkMode'] = 'uplink'
    return [worker, proxy, 'socket', 'budget', 'uplink-id']


def test_networkless_topology():
    from egress_guard import validate_networkless
    validate_networkless(*networkless())


@pytest.mark.parametrize('mutate', [
    lambda t: t[0]['HostConfig'].update(NetworkMode='host'),
    lambda t: t[0]['NetworkSettings']['Networks'].update(extra={'NetworkID': 'other'}),
    lambda t: t[0]['Mounts'][0].update(RW=True),
    lambda t: t[0]['Mounts'].append({'Type': 'volume', 'Name': 'budget', 'Destination': '/budget', 'RW': False}),
    lambda t: t[0]['Mounts'].append({'Type': 'bind', 'Source': '/', 'Destination': '/host', 'RW': False}),
    lambda t: t[1]['Mounts'].append({'Type': 'bind', 'Source': '/', 'Destination': '/host', 'RW': False}),
    lambda t: t[1]['HostConfig'].update(Privileged=True),
    lambda t: t[1]['HostConfig'].update(CapDrop=[]),
    lambda t: t[0]['HostConfig'].update(SecurityOpt=[]),
    lambda t: t[1]['HostConfig'].update(PortBindings={'8080/tcp': []}),
    lambda t: t.__setitem__(3, 'socket'),
])
def test_networkless_rejects_unsafe_changes(mutate):
    from egress_guard import validate_networkless
    t = networkless()
    mutate(t)
    with pytest.raises(UnsafeTopology):
        validate_networkless(*t)


@pytest.mark.parametrize('user', ['', '0', '0:1000', '1000:0', 'root', 'nobody', '1000:root'])
def test_networkless_rejects_non_numeric_or_root_users(user):
    from egress_guard import validate_networkless
    for index in (0, 1):
        t = networkless()
        t[index]['Config']['User'] = user
        with pytest.raises(UnsafeTopology):
            validate_networkless(*t)

def test_networkless_rejects_home_as_workspace():
    from egress_guard import validate_networkless
    import os
    t = networkless()
    home = os.path.expanduser('~')
    t[0]['Mounts'].append({'Type': 'bind', 'Source': home, 'Destination': '/workspace', 'RW': True})
    with pytest.raises(UnsafeTopology):
        validate_networkless(*t, workspace_source=home)


@pytest.mark.parametrize('field', ['PidMode', 'IpcMode'])
@pytest.mark.parametrize('index', [0, 1])
def test_networkless_rejects_other_container_namespaces(field, index):
    from egress_guard import validate_networkless
    t = networkless()
    t[index]['HostConfig'][field] = 'container:other'
    with pytest.raises(UnsafeTopology):
        validate_networkless(*t)


@pytest.mark.parametrize('field,value', [('IpcMode', 'shareable'), ('CgroupnsMode', 'host')])
@pytest.mark.parametrize('index', [0, 1])
def test_networkless_rejects_other_shared_namespaces(field, value, index):
    from egress_guard import validate_networkless
    t = networkless()
    t[index]['HostConfig'][field] = value
    with pytest.raises(UnsafeTopology):
        validate_networkless(*t)


@pytest.mark.parametrize('option', ['seccomp=unconfined', 'apparmor=unconfined', 'label=disable',
                                        'seccomp=/tmp/custom.json', 'apparmor=arbitrary',
                                        'seccomp={\"defaultAction\":\"SCMP_ACT_ALLOW\"}', 'systempaths=unconfined'])
@pytest.mark.parametrize('index', [0, 1])
def test_networkless_rejects_disabled_security_profiles(option, index):
    from egress_guard import validate_networkless
    t = networkless()
    t[index]['HostConfig']['SecurityOpt'].append(option)
    with pytest.raises(UnsafeTopology):
        validate_networkless(*t)
