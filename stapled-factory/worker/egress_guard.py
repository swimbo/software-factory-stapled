"""Fail-closed inspection of an isolated Docker worker's network boundary.

This is a pre-dispatch check, not protection against a privileged Docker operator
changing the topology afterward. Inspect again after any lifecycle change.
"""
import argparse
import json
import os
import subprocess


class UnsafeTopology(ValueError):
    pass


def _non_root_numeric_user(container):
    # Numeric UID avoids image-dependent username resolution. A supplied GID
    # must also be numeric and nonzero. Image identity is checked elsewhere.
    value = container.get('Config', {}).get('User', '')
    if not isinstance(value, str):
        return False
    parts = value.split(':')
    return len(parts) in (1, 2) and all(p.isascii() and p.isdecimal() and int(p) > 0 for p in parts)


def _private_namespace(mode):
    # Docker can also join another container's namespace, exposing its
    # processes or IPC resources even when host namespaces are not selected.
    return isinstance(mode, str) and mode not in ('host', 'shareable') and not mode.startswith('container:')


def _confined_security_options(host):
    # A custom profile may be unconfined under an arbitrary name or JSON body.
    # Accept only the provisioner's explicit option; trust in the daemon's
    # default seccomp and MAC profile must be established separately.
    options = host.get('SecurityOpt') or []
    return (isinstance(options, list) and bool(options) and
            all(option in ('no-new-privileges', 'no-new-privileges=true') for option in options))


def validate(worker, proxy, isolated, uplink, workspace_source=None):
    def require(condition, message):
        if not condition:
            raise UnsafeTopology(message)

    wid, pid = worker['Id'], proxy['Id']
    iid, uid = isolated['Id'], uplink['Id']
    require(wid != pid and iid != uid, 'worker, proxy and networks must be distinct')
    require(isolated.get('Internal') is True, 'worker network must be internal')
    require(isolated.get('Driver') == 'bridge', 'worker network must use bridge driver')
    require(uplink.get('Internal') is False, 'proxy uplink must allow external access')
    for c in (worker, proxy):
        require(c.get('State', {}).get('Running') is True, 'container must be running')
        require(_non_root_numeric_user(c), 'explicit numeric non-root user required')
        host = c.get('HostConfig', {})
        require(not host.get('Privileged'), 'privileged container')
        require(not host.get('PublishAllPorts') and not host.get('PortBindings'), 'published port')
        mode = host.get('NetworkMode', '')
        require(mode != 'host' and not mode.startswith('container:'), 'shared host or container network')
    host = worker.get('HostConfig', {})
    require('ALL' in (host.get('CapDrop') or []), 'worker must drop all capabilities')
    require(not host.get('CapAdd'), 'worker must not add capabilities')
    require(not host.get('Devices') and not host.get('DeviceRequests'), 'worker devices forbidden')
    require(_private_namespace(host.get('PidMode') or '') and _private_namespace(host.get('IpcMode') or ''), 'shared namespaces forbidden')
    require(host.get('CgroupnsMode') != 'host', 'host cgroup namespace forbidden')
    require(not host.get('ExtraHosts') and not host.get('Dns'), 'custom hosts or DNS forbidden')
    require(any(x in ('no-new-privileges', 'no-new-privileges=true') for x in (host.get('SecurityOpt') or [])), 'worker must use no-new-privileges')
    require(_confined_security_options(host), 'unconfined worker security profile forbidden')
    mounts = worker.get('Mounts', [])
    if mounts:
        require(bool(workspace_source) and os.path.isabs(workspace_source), 'workspace mount requires explicit absolute source')
        source = os.path.realpath(workspace_source)
        require(source != '/' and source != os.path.realpath(os.path.expanduser('~')) and os.path.isdir(source), 'invalid workspace source')
        require(len(mounts) == 1, 'only one workspace mount allowed')
        mount = mounts[0]
        require(mount.get('Type') == 'bind' and os.path.realpath(mount.get('Source', '')) == source and mount.get('Destination') == '/workspace', 'unexpected worker mount')
    def network_ids(container):
        return {v.get('NetworkID') for v in container.get('NetworkSettings', {}).get('Networks', {}).values()}
    require(network_ids(worker) == {iid}, 'worker must have only the internal network')
    require(network_ids(proxy) == {iid, uid}, 'proxy must have only internal network and uplink')
    require(set((isolated.get('Containers') or {}).keys()) == {wid, pid}, 'internal network has unexpected members')
    # The uplink may contain other trusted services; it must not contain the worker.
    require(wid not in (uplink.get('Containers') or {}), 'worker attached to uplink')
    require(pid in (uplink.get('Containers') or {}), 'proxy missing from uplink')


def validate_networkless(worker, proxy, socket_volume, budget_volume, proxy_network, workspace_source=None, anchor_volume=None):
    """Inspect topology only; this does not establish image or proxy identity."""
    def require(condition, message):
        if not condition:
            raise UnsafeTopology(message)

    require(socket_volume and budget_volume and socket_volume != budget_volume, 'distinct named volumes required')
    require(worker.get('Id') != proxy.get('Id'), 'worker and proxy must be distinct')
    for container in (worker, proxy):
        host = container.get('HostConfig', {})
        require(container.get('State', {}).get('Running') is True, 'container must be running')
        require(_non_root_numeric_user(container), 'explicit numeric non-root user required')
        require(not host.get('Privileged') and not host.get('CapAdd') and 'ALL' in (host.get('CapDrop') or []), 'container capabilities too broad')
        require(any(x in ('no-new-privileges', 'no-new-privileges=true') for x in (host.get('SecurityOpt') or [])), 'no-new-privileges required')
        require(_confined_security_options(host), 'unconfined security profile forbidden')
        require(not host.get('PublishAllPorts') and not host.get('PortBindings'), 'published port forbidden')
        require(not host.get('Devices') and not host.get('DeviceRequests'), 'devices forbidden')
        require(_private_namespace(host.get('PidMode') or '') and _private_namespace(host.get('IpcMode') or ''), 'shared namespaces forbidden')
        require(host.get('CgroupnsMode') != 'host', 'host cgroup namespace forbidden')
        require(not host.get('ExtraHosts') and not host.get('Dns'), 'custom hosts or DNS forbidden')
    require(worker['HostConfig'].get('NetworkMode') == 'none', 'worker must use network none')
    worker_networks = worker.get('NetworkSettings', {}).get('Networks', {})
    require(set(worker_networks) <= {'none'}, 'worker must not join a network')
    mode = proxy['HostConfig'].get('NetworkMode', '')
    require(mode != 'host' and not mode.startswith('container:'), 'proxy host or container network forbidden')
    networks = proxy.get('NetworkSettings', {}).get('Networks', {})
    require({n.get('NetworkID') for n in networks.values()} == {proxy_network}, 'unexpected proxy network')
    def named(mount, name, destination, readonly):
        return (mount.get('Type') == 'volume' and mount.get('Name') == name and
                mount.get('Destination') == destination and mount.get('RW') is (not readonly))
    wm = worker.get('Mounts', [])
    require(sum(named(m, socket_volume, '/gateway', True) for m in wm) == 1, 'worker needs read-only socket volume at /gateway')
    allowed_worker = [m for m in wm if named(m, socket_volume, '/gateway', True)]
    if workspace_source:
        source = os.path.realpath(workspace_source)
        require(os.path.isabs(workspace_source) and source != '/' and source != os.path.realpath(os.path.expanduser('~')) and os.path.isdir(source), 'invalid workspace source')
        workspace_mounts = [m for m in wm if m.get('Type') == 'bind' and os.path.realpath(m.get('Source', '')) == source and m.get('Destination') == '/workspace' and m.get('RW') is True]
        require(len(workspace_mounts) == 1, 'worker needs the bound writable workspace')
        allowed_worker += workspace_mounts
    require(len(wm) == len(allowed_worker), 'unexpected worker mount')
    pm = proxy.get('Mounts', [])
    if anchor_volume:
        require(anchor_volume not in (socket_volume, budget_volume), 'anchor volume must be distinct')
        require(sum(named(m, anchor_volume, '/anchor', False) for m in pm) == 1, 'missing anchor mount')
        pm = [m for m in pm if not named(m, anchor_volume, '/anchor', False)]
    require(len(pm) == 2 and sum(named(m, socket_volume, '/gateway', False) for m in pm) == 1 and
            sum(named(m, budget_volume, '/budget', False) for m in pm) == 1, 'unexpected proxy mounts')


def inspect(kind, identifier):
    result = subprocess.run(['docker', kind, 'inspect', identifier], check=True, capture_output=True, text=True, timeout=15)
    items = json.loads(result.stdout)
    if len(items) != 1:
        raise UnsafeTopology('inspection returned unexpected count')
    return items[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', required=True)
    parser.add_argument('--proxy', required=True)
    parser.add_argument('--mode', choices=('networkless', 'internal'), default='networkless')
    parser.add_argument('--internal-network')
    parser.add_argument('--uplink-network', required=True)
    parser.add_argument('--socket-volume')
    parser.add_argument('--budget-volume')
    parser.add_argument('--anchor-volume')
    parser.add_argument('--workspace-source', help='explicit trusted workspace bind source; mounted at /workspace')
    args = parser.parse_args()
    if args.mode == 'networkless' and not (args.socket_volume and args.budget_volume and args.anchor_volume):
        parser.error('networkless mode requires --socket-volume, --budget-volume and --anchor-volume')
    if args.mode == 'internal' and not args.internal_network:
        parser.error('internal mode requires --internal-network')
    try:
        worker, proxy = inspect('container', args.worker), inspect('container', args.proxy)
        uplink = inspect('network', args.uplink_network)
        if args.mode == 'networkless':
            validate_networkless(worker, proxy, args.socket_volume, args.budget_volume,
                                 uplink['Id'], args.workspace_source, args.anchor_volume)
        else:
            validate(worker, proxy, inspect('network', args.internal_network), uplink, args.workspace_source)
    except (UnsafeTopology, subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
        parser.exit(1, 'egress topology rejected: ' + str(error) + '\n')
    print('egress topology accepted; privileged topology changes require reinspection')


if __name__ == '__main__':
    main()
