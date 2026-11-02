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


def prepare_install(control, service_dir, guard_source, expected_sha256):
    control = _directory(control, private=True);service_dir = _directory(service_dir)
    if not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise ValueError('exact guard digest required')
    guard = _regular(guard_source)
    if hashlib.sha256(guard).hexdigest() != expected_sha256:
        raise ValueError('guard source differs')
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
    files = {}
    for name in UNITS:
        if _regular(service_dir/(name+'.service')) != original[name]:
            raise ValueError('service unit changed during guard installation')
        directory = service_dir/(name+'.service.d')
        if not os.path.lexists(directory):directory.mkdir(mode=0o700);_sync(service_dir)
        _directory(directory)
        data = ('[Service]\nExecStartPre=/usr/bin/python3 '+str(script)+' prestart '+str(hold)+' '+name+' '+
                str(service_dir/(name+'.service'))+'\n').encode()
        _write(directory/DROPIN, data)
        files[name] = hashlib.sha256(data).hexdigest()
    receipt = {'guard_sha256':expected_sha256,'dropins':files,'armed':False,'loaded':False}
    _write(root/'installation.json',json.dumps(receipt,sort_keys=True,separators=(',', ':')).encode())
    return receipt


def verify_loaded(control, service_dir, expected_sha256):
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
    if (not isinstance(receipt,dict) or set(receipt)!={'guard_sha256','dropins','armed','loaded'} or
            receipt['guard_sha256'] != expected_sha256 or receipt['armed'] is not False or receipt['loaded'] is not False or
            not isinstance(receipt['dropins'],dict) or set(receipt['dropins']) != set(UNITS)):
        raise ValueError('installation receipt differs')
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
        argv='/usr/bin/python3 '+str(script)+' prestart '+str(hold)+' '+name+' '+str(unit)
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
