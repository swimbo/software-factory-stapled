"""Read stable process and cgroup-v2 identities for admission evidence."""
import os
from pathlib import Path
import re
import stat

LIMIT=1024*1024


def _read(directory,name):
    fd=os.open(name,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=directory)
    with os.fdopen(fd,'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('unsafe process evidence')
        data=stream.read(LIMIT+1)
    if len(data)>LIMIT:raise ValueError('oversized process evidence')
    return data.decode('ascii')


def _process_stat(raw,pid):
    end=raw.rfind(')')
    if end<0 or not raw.startswith(str(pid)+' ('):
        raise ValueError('invalid process stat')
    fields=raw[end+1:].split()
    if len(fields)<20 or fields[0] in ('Z','X','x') or not re.fullmatch(r'[0-9]+',fields[19]):
        raise ValueError('process is not live')
    value=int(fields[19])
    if value<=0:raise ValueError('process start time unavailable')
    return value


def _cgroup(raw):
    rows=raw.splitlines()
    if len(rows)!=1 or not rows[0].startswith('0::'):
        raise ValueError('cgroup v2 identity unavailable')
    path=rows[0][3:]
    if not re.fullmatch(r'/[A-Za-z0-9_.@:/-]+',path) or any(part in ('','.','..') for part in path[1:].split('/')):
        raise ValueError('unsafe cgroup path')
    return path


def _uid(raw, expected_uid):
    lines=[line for line in raw.splitlines() if line.startswith('Uid:')]
    if len(lines)!=1 or lines[0].split()[1:] != [str(expected_uid)]*4:
        raise ValueError('process uid differs')


def _inode(path,root):
    current=os.open(root,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        root_info=os.fstat(current)
        fd=os.open('cgroup.controllers',os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=current)
        with os.fdopen(fd,'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('cgroup v2 mount unavailable')
        for part in path[1:].split('/'):
            child=os.open(part,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,dir_fd=current)
            os.close(current);current=child
            if os.fstat(current).st_dev!=root_info.st_dev:raise ValueError('cgroup mount changed')
        inode=os.fstat(current).st_ino
        if inode<=0:raise ValueError('cgroup inode unavailable')
        return inode
    finally:
        os.close(current)


def read_identity(pid, expected_uid, proc_root=Path('/proc'), cgroup_root=Path('/sys/fs/cgroup')):
    if type(pid) is not int or pid<=0 or type(expected_uid) is not int or expected_uid<0:
        raise ValueError('valid process identity required')
    proc=os.open(Path(proc_root)/str(pid),os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        start=_process_stat(_read(proc,'stat'),pid)
        _uid(_read(proc,'status'),expected_uid)
        path=_cgroup(_read(proc,'cgroup'));inode=_inode(path,cgroup_root)
        _uid(_read(proc,'status'),expected_uid)
        if _process_stat(_read(proc,'stat'),pid)!=start or _cgroup(_read(proc,'cgroup'))!=path:
            raise ValueError('process changed during identity read')
        return {'pid':pid,'start_ticks':start,'cgroup':path,'inode':inode}
    finally:
        os.close(proc)
