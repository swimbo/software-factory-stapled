"""Hash retained SDK checkpoints after namespace stop, never follow worker links."""
import hashlib,json,os,stat
from pathlib import Path

MAX_NODES=2048
MAX_FILE=4*1024**2
MAX_TOTAL=64*1024**2
DIR=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW

def checkpoint_evidence(workspace):
    root=Path(workspace)
    if not root.is_absolute() or root.resolve()!=root:raise ValueError('canonical workspace required')
    records=[];nodes=0;total=0
    def owned(info):
        if info.st_uid!=os.geteuid():raise ValueError('checkpoint ownership changed')
    def walk(fd,prefix,depth=0):
        nonlocal nodes,total
        if depth>16:raise ValueError('checkpoint depth limit')
        with os.scandir(fd) as entries:
            for entry in entries:
                nodes+=1
                if nodes>MAX_NODES:raise ValueError('checkpoint node limit')
                info=entry.stat(follow_symlinks=False);owned(info)
                name=entry.name;relative=prefix+name
                if stat.S_ISDIR(info.st_mode):
                    child=os.open(name,DIR,dir_fd=fd)
                    try:owned(os.fstat(child));walk(child,relative+'/',depth+1)
                    finally:os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    file=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                    try:
                        opened=os.fstat(file);owned(opened)
                        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink!=1 or opened.st_size>MAX_FILE
                            or (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino)):
                            raise ValueError('unsafe checkpoint file')
                        digest=hashlib.sha256();size=0
                        while True:
                            raw=os.read(file,min(65536,MAX_FILE-size+1))
                            if not raw:break
                            size+=len(raw);total+=len(raw)
                            if size>MAX_FILE or total>MAX_TOTAL:raise ValueError('checkpoint byte limit')
                            digest.update(raw)
                        records.append({'path':relative,'bytes':size,'sha256':digest.hexdigest()})
                    finally:os.close(file)
                else:raise ValueError('checkpoint symlink or special file refused')
    fd=os.open(root,DIR)
    try:
        owned(os.fstat(fd))
        for name in ('.git','factory-home','conversations'):
            child=os.open(name,DIR,dir_fd=fd);os.close(fd);fd=child;owned(os.fstat(fd))
        walk(fd,'')
    finally:os.close(fd)
    if not records or not any(r['path'].endswith('/base_state.json') for r in records):
        raise ValueError('SDK checkpoint missing')
    records.sort(key=lambda r:r['path'])
    return {'schema':'factory.retained-checkpoint-evidence/v1','files':len(records),'bytes':total,
            'sha256':hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest(),
            'base_state_files':sum(r['path'].endswith('/base_state.json') for r in records),
            'event_files':sum('/events/' in r['path'] for r in records),
            'no_follow':True}
