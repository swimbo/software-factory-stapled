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


def require_protected_cgroup(path, expected_pid, root=Path('/sys/fs/cgroup')):
    """Require a root-controlled cgroup subtree occupied only by the named process."""
    path=_cgroup('0::'+path)
    if type(expected_pid) is not int or expected_pid<=0:raise ValueError('valid cgroup process required')
    if os.geteuid()==0:raise ValueError('non-root admission verifier required')
    current=os.open(root,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        root_dev=os.fstat(current).st_dev
        def protected_directory(fd):
            info=os.fstat(fd)
            if info.st_dev!=root_dev or info.st_uid!=0 or info.st_mode & 0o022:
                raise ValueError('cgroup directory is not protected')
            try:writable=os.access('.',os.W_OK,dir_fd=fd,effective_ids=True)
            except (NotImplementedError,TypeError) as exc:raise ValueError('cgroup protection unavailable') from exc
            if writable:raise ValueError('cgroup directory is not protected')
        protected_directory(current)
        for part in path[1:].split('/'):
            child=os.open(part,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,dir_fd=current)
            os.close(current);current=child;protected_directory(current)
        seen=[];count=[0]
        def visit(directory,depth):
            count[0]+=1
            if depth>32 or count[0]>1024:raise ValueError('cgroup subtree too large')
            protected_directory(directory)
            fd=os.open('cgroup.procs',os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=directory)
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
                    raise ValueError('cgroup membership is not protected')
                raw=stream.read(LIMIT+1)
            if len(raw)>LIMIT or any(not x.isdigit() for x in raw.split()):
                raise ValueError('invalid cgroup membership')
            seen.extend(int(x) for x in raw.split())
            try:writable=os.access('cgroup.procs',os.W_OK,dir_fd=directory,effective_ids=True,follow_symlinks=False)
            except (NotImplementedError,TypeError) as exc:raise ValueError('cgroup protection unavailable') from exc
            if writable:raise ValueError('cgroup membership is not protected')
            with os.scandir(directory) as entries:
                children=[entry.name for entry in entries if entry.is_dir(follow_symlinks=False)]
            for name in children:
                child=os.open(name,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,dir_fd=directory)
                try:visit(child,depth+1)
                finally:os.close(child)
        visit(current,0)
        if seen != [expected_pid]:raise ValueError('cgroup membership is not exclusive')
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
