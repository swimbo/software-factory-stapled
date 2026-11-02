"""Prepare create-only systemd startup guards; does not arm or activate a fence.

The caller must reload systemd and independently verify that all three loaded
guards match these files before arming the hold. Partial preparation is retained
for observation; this module never replaces or removes existing unit files.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

UNITS = ('factory-restate', 'factory-endpoint', 'factory-ingress')
DROPIN = '90-factory-admission.conf'


def _directory(path, private=False):
    path = Path(path).absolute()
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(path)) or path.resolve() != path:
        raise ValueError('unsafe guard installation path')
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            info.st_mode & (0o077 if private else 0o022)):
        raise ValueError('unsafe guard installation directory')
    return path


def _regular(path, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_mode & (0o077 if private else 0o022) or info.st_size > 1024*1024):
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
