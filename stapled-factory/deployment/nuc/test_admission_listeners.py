from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import admission_listeners as listeners


def row(port, inode, address='0100007F', state='0A'):
    return f'0: {address}:{port:04X} 00000000:0000 {state} 0:0 0:0 0 1000 0 {inode}\n'


def test_loopback_listener_ownership_and_changed_bindings(tmp_path, monkeypatch):
    net=tmp_path/'net';net.mkdir(); header='sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n'
    tcp=net/'tcp';tcp6=net/'tcp6';tcp6.write_text(header)
    entries={port:100+i for i,port in enumerate(listeners.PORT_OWNERS)}
    tcp.write_text(header+''.join(row(port,inode) for port,inode in entries.items()))
    pids={'restate':11,'endpoint':12,'ingress':13}
    for name,pid in pids.items():
        fds=tmp_path/str(pid)/'fd';fds.mkdir(parents=True)
        for port,owner in listeners.PORT_OWNERS.items():
            if owner==name:(fds/str(port)).symlink_to(f'socket:[{entries[port]}]')
    monkeypatch.setattr(listeners,'read_identity',lambda pid,uid,**kwargs:{'pid':pid,'start_ticks':1})
    assert listeners.verify_listeners(pids,proc=tmp_path,uid=1000)['verified'] is True
    with pytest.raises(ValueError,match='still has a listener'):
        listeners.verify_listeners(pids,proc=tmp_path,uid=1000,absent='endpoint')
    tcp.write_text(header+''.join(row(port,inode) for port,inode in entries.items() if port!=19080))
    result=listeners.verify_listeners(pids,proc=tmp_path,uid=1000,absent='endpoint')
    assert 19080 not in result['ports'] and 'endpoint' not in result['identities']
    tcp.write_text(header+''.join(row(port,inode,address='00000000' if port==19070 else '0100007F') for port,inode in entries.items()))
    with pytest.raises(ValueError,match='not IPv4 loopback'):
        listeners.verify_listeners(pids,proc=tmp_path,uid=1000)
    tcp.write_text(header+''.join(row(port,inode) for port,inode in entries.items()))
    (tmp_path/'11/fd/19070').unlink()
    with pytest.raises(ValueError,match='process differs'):
        listeners.verify_listeners(pids,proc=tmp_path,uid=1000)


def test_reject_ipv6_and_duplicate_listener():
    header='sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n'
    tcp=header+''.join(row(port,100+i) for i,port in enumerate(listeners.PORT_OWNERS))
    with pytest.raises(ValueError,match='not IPv4 loopback'):
        listeners._listeners(tcp,header+row(19070,200,address='00000000000000000000000001000000'))
    with pytest.raises(ValueError,match='duplicate'):
        listeners._listeners(tcp+row(19070,201),header)
