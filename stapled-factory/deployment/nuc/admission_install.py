"""Prepare create-only systemd startup guards; does not arm or activate a fence.

The caller must reload systemd and independently verify that all three loaded
guards match these files before arming the hold. Partial preparation is retained
for observation; this module never replaces or removes existing unit files.
"""
import hashlib
import grp
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess

UNITS = ('factory-restate', 'factory-endpoint', 'factory-ingress')
DROPIN = '90-factory-admission.conf'
MODULES = frozenset(('admission_live.py','admission_identity.py','admission_listeners.py','admission_nft.py',
                     'admission_effective.py','admission_install.py'))


def _unsafe_mode(info, private):
    if private:return bool(info.st_mode & 0o077)
    if info.st_mode & 0o002:return True
    if not info.st_mode & 0o020:return False
    try:
        user = pwd.getpwuid(os.getuid())
        group = grp.getgrgid(info.st_gid)
        members = set(group.gr_mem) | {entry.pw_name for entry in pwd.getpwall() if entry.pw_gid == info.st_gid}
    except KeyError:
        return True
    return group.gr_name != user.pw_name or not members <= {user.pw_name, 'root'}


def _directory(path, private=False):
    path = Path(path).absolute()
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(path)) or path.resolve() != path:
        raise ValueError('unsafe guard installation path')
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            _unsafe_mode(info,private)):
        raise ValueError('unsafe guard installation directory')
    return path


def _regular(path, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                _unsafe_mode(info,private) or info.st_size > 1024*1024):
            raise ValueError('unsafe guard input file')
        return stream.read()


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def _write(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    _sync(path.parent)


def prepare_install(control, service_dir, guard_source, expected_sha256, *, module_sources=None):
    control = _directory(control, private=True);service_dir = _directory(service_dir)
    if not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise ValueError('exact guard digest required')
    guard = _regular(guard_source)
    if hashlib.sha256(guard).hexdigest() != expected_sha256:
        raise ValueError('guard source differs')
    modules={}
    if module_sources is not None:
        if not isinstance(module_sources,dict) or set(module_sources)!=MODULES:
            raise ValueError('complete pinned admission module bundle required')
        for name,source in module_sources.items():
            if (not isinstance(source,dict) or set(source)!={'path','sha256'} or
                    not isinstance(source['sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',source['sha256'])):
                raise ValueError('invalid admission module pin')
            data=_regular(source['path'])
            if hashlib.sha256(data).hexdigest()!=source['sha256']:
                raise ValueError('admission module differs')
            modules[name]=data
    root = control/'admission-fence'
    if os.path.lexists(root):raise FileExistsError('original admission installation exists')
    original = {}
    for name in UNITS:
        original[name] = _regular(service_dir/(name+'.service'))
        directory = service_dir/(name+'.service.d')
        if os.path.lexists(directory):
            _directory(directory)
            if os.path.lexists(directory/DROPIN):raise FileExistsError('existing admission drop-in')
    root.mkdir(mode=0o700);_sync(control)
    hold = root/'hold';hold.mkdir(mode=0o700);_sync(root)
    script = root/'guard.py';_write(script, guard)
    for name,data in sorted(modules.items()):_write(root/name,data)
    files = {}
    for name in UNITS:
        if _regular(service_dir/(name+'.service')) != original[name]:
            raise ValueError('service unit changed during guard installation')
        directory = service_dir/(name+'.service.d')
        if not os.path.lexists(directory):directory.mkdir(mode=0o700);_sync(service_dir)
        _directory(directory)
        data = ('[Service]\nExecStartPre=/usr/bin/python3 -I -B '+str(script)+' prestart '+str(hold)+' '+name+' '+
                str(service_dir/(name+'.service'))+'\n').encode()
        _write(directory/DROPIN, data)
        files[name] = hashlib.sha256(data).hexdigest()
    receipt = {'guard_sha256':expected_sha256,'dropins':files,'armed':False,'loaded':False}
    if module_sources is not None:receipt['modules']={name:hashlib.sha256(data).hexdigest() for name,data in modules.items()}
    _write(root/'installation.json',json.dumps(receipt,sort_keys=True,separators=(',', ':')).encode())
    return receipt


def verify_loaded(control, service_dir, expected_sha256, *, module_sha256=None):
    """Read back the prepared files and each active systemd unit before arming."""
    control = _directory(control, private=True);service_dir = _directory(service_dir)
    root = _directory(control/'admission-fence', private=True)
    hold = _directory(root/'hold', private=True);script = root/'guard.py'
    if os.path.lexists(hold/'transition'):raise ValueError('existing transition requires observation')
    if not isinstance(expected_sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',expected_sha256):
        raise ValueError('exact guard digest required')
    if hashlib.sha256(_regular(script, private=True)).hexdigest() != expected_sha256:
        raise ValueError('installed guard differs')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate installation receipt key')
            result[key]=value
        return result
    receipt=json.loads(_regular(root/'installation.json',private=True),object_pairs_hook=unique)
    keys={'guard_sha256','dropins','armed','loaded'} | ({'modules'} if module_sha256 is not None else set())
    if (not isinstance(receipt,dict) or set(receipt)!=keys or
            receipt['guard_sha256'] != expected_sha256 or receipt['armed'] is not False or receipt['loaded'] is not False or
            not isinstance(receipt['dropins'],dict) or set(receipt['dropins']) != set(UNITS)):
        raise ValueError('installation receipt differs')
    if module_sha256 is not None:
        if not isinstance(module_sha256,dict) or set(module_sha256)!=MODULES or receipt['modules']!=module_sha256:
            raise ValueError('admission module bundle differs')
        for name,digest in module_sha256.items():
            if (not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest) or
                    hashlib.sha256(_regular(root/name,private=True)).hexdigest()!=digest):
                raise ValueError('installed admission module differs')
    runtime='/run/user/'+str(os.getuid())
    env={'PATH':'/usr/bin:/bin','HOME':str(Path.home()),'XDG_RUNTIME_DIR':runtime,
         'DBUS_SESSION_BUS_ADDRESS':'unix:path='+runtime+'/bus'}
    result={}
    for name in UNITS:
        unit=service_dir/(name+'.service');dropin=service_dir/(name+'.service.d')/DROPIN
        _directory(dropin.parent)
        unit_hash=hashlib.sha256(_regular(unit)).hexdigest()
        if hashlib.sha256(_regular(dropin)).hexdigest() != receipt['dropins'][name]:
            raise ValueError('admission drop-in differs')
        command=['/usr/bin/systemctl','--user','show',name,'-p','ExecStartPre','-p','FragmentPath',
                 '-p','DropInPaths','-p','NeedDaemonReload','-p','ActiveState','-p','MainPID']
        out=subprocess.run(command,check=True,capture_output=True,text=True,timeout=15,env=env).stdout
        fields={}
        for line in out.splitlines():
            if '=' not in line:raise ValueError('invalid systemd readback')
            key,value=line.split('=',1)
            if key in fields:raise ValueError('duplicate systemd property')
            fields[key]=value
        argv='/usr/bin/python3 -I -B '+str(script)+' prestart '+str(hold)+' '+name+' '+str(unit)
        expected='{ path=/usr/bin/python3 ; argv[]='+argv+' ; ignore_errors=no ;'
        pid=fields.get('MainPID','')
        if (fields.get('FragmentPath')!=str(unit) or str(dropin) not in fields.get('DropInPaths','').split() or
                fields.get('NeedDaemonReload')!='no' or fields.get('ActiveState')!='active' or
                not re.fullmatch(r'[1-9][0-9]*',pid) or expected not in fields.get('ExecStartPre','')):
            raise ValueError('systemd has not loaded the active guard')
        if hashlib.sha256(_regular(unit)).hexdigest()!=unit_hash or hashlib.sha256(_regular(dropin)).hexdigest()!=receipt['dropins'][name]:
            raise ValueError('service changed during guard readback')
        result[name]={'pid':int(pid),'unit_sha256':unit_hash,'dropin_sha256':receipt['dropins'][name]}
    return {'loaded':True,'guard_sha256':expected_sha256,'units':result}
