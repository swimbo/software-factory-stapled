"""Generate scoped IPv4 firewall transactions; not an admission fence by itself.

The caller must verify the cgroup identities, exclusive chain ownership,
iptables backend and lock, startup hold, listener addresses, drained work and
installed rules. These pure functions do not apply or remove firewall rules.
"""
import re

PORTS = '5122,18080,19070,19080,19081'


def _chain(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{16}', identifier):
        raise ValueError('invalid fence identity')
    return 'FCT_H_' + identifier


def _paths(*values):
    paths = []
    for path in values:
        if (not isinstance(path, str) or not path.startswith('/') or
                not re.fullmatch(r'/[A-Za-z0-9_.@:/-]+', path)):
            raise ValueError('invalid cgroup path')
        parts = path[1:].split('/')
        if any(part in ('', '.', '..') for part in parts):
            raise ValueError('invalid cgroup path')
        paths.append('/'.join(parts))
    for index, left in enumerate(paths):
        for right in paths[index + 1:]:
            if left == right or left.startswith(right + '/') or right.startswith(left + '/'):
                raise ValueError('overlapping cgroups')
    return paths


def _render(identifier, probe, ingress, restate, install):
    chain = _chain(identifier)
    probe, ingress, restate = _paths(probe, ingress, restate)
    rules = ['*filter', ':' + chain + ' - [0:0]',
             f'-A {chain} -m cgroup --path {probe} -j RETURN',
             f'-A {chain} -p tcp --dport 18080 -m cgroup --path {ingress} -j RETURN',
             f'-A {chain} -p tcp -m multiport --dports 5122,19080 -m cgroup --path {restate} -j RETURN',
             f'-A {chain} -p tcp -j REJECT --reject-with tcp-reset']
    if install:
        rules.append(f'-I OUTPUT 1 -j {chain} -d 127.0.0.1/32 -p tcp -m multiport --dports {PORTS}')
    return '\n'.join(rules + ['COMMIT', ''])


def render_install(identifier, probe, ingress, restate):
    return _render(identifier, probe, ingress, restate, True)


def render_rebind(identifier, probe, ingress, restate):
    return _render(identifier, probe, ingress, restate, False)
