"""Check the loopback listener assumption used by the candidate fence.

This is a point-in-time readback, not proof of continuous containment. It must
be combined with startup guards, firewall checks and a controlled transition.
"""
import os
from pathlib import Path
import re

from admission_identity import read_identity

PORT_OWNERS = {5122: 'restate', 18080: 'restate', 19070: 'restate',
               19080: 'endpoint', 19081: 'ingress'}


def _listeners(tcp, tcp6):
    result = {}
    for family, content in (('ipv4', tcp), ('ipv6', tcp6)):
        lines = content.splitlines()
        if not lines or 'local_address' not in lines[0]: raise ValueError('listener table unavailable')
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 10: raise ValueError('invalid listener table')
            try:
                address, port = fields[1].split(':'); port = int(port, 16)
                state = int(fields[3], 16); inode = int(fields[9])
            except (ValueError, IndexError) as exc: raise ValueError('invalid listener entry') from exc
            if port not in PORT_OWNERS or state != 10: continue
            if family != 'ipv4' or address != '0100007F' or inode <= 0:
                raise ValueError('factory listener is not IPv4 loopback')
            if port in result: raise ValueError('duplicate factory listener')
            result[port] = inode
    if set(result) != set(PORT_OWNERS): raise ValueError('factory listeners incomplete')
    return result


def verify_listeners(processes, *, proc=Path('/proc'), uid=None):
    """Bind each protected listener to its expected stable service process."""
    if (not isinstance(processes, dict) or set(processes) != {'restate', 'endpoint', 'ingress'} or
            any(type(pid) is not int or pid <= 0 for pid in processes.values()) or len(set(processes.values())) != 3):
        raise ValueError('expected listener processes required')
    if uid is None: uid = os.getuid()
    proc = Path(proc)
    def identities(): return {name: read_identity(pid, uid, proc_root=proc) for name, pid in processes.items()}
    def tables(): return _listeners((proc/'net/tcp').read_text(), (proc/'net/tcp6').read_text())
    before = identities(); listeners = tables(); observed = {}
    for name, pid in processes.items():
        try:
            sockets = set()
            for fd in (proc/str(pid)/'fd').iterdir():
                try: target = os.readlink(fd)
                except FileNotFoundError: continue
                match = re.fullmatch(r'socket:\[([0-9]+)\]', target)
                if match: sockets.add(int(match.group(1)))
            observed[name] = sockets
        except OSError as exc: raise ValueError('listener process descriptors unavailable') from exc
    if any(inode not in observed[PORT_OWNERS[port]] for port, inode in listeners.items()):
        raise ValueError('factory listener process differs')
    if tables() != listeners or identities() != before: raise ValueError('listener evidence changed')
    return {'verified': True, 'ports': sorted(listeners), 'scope': 'point-in-time-ipv4-loopback'}
