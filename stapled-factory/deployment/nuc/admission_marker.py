"""Durable startup hold for an interrupted runtime transition.

This marker alone is not an admission fence. A verified pre-start installation,
network fence, drain and explicit recovery procedure are also required.
"""
import hashlib
import grp
import json
import os
import pwd
import re
import stat
import sys

RESTARTABLE = frozenset(('factory-endpoint', 'factory-ingress'))


def _unsafe_unit_mode(info):
    if info.st_mode & 0o002:return True
    if not info.st_mode & 0o020:return False
    try:
        user = pwd.getpwuid(os.getuid());group = grp.getgrgid(info.st_gid)
        members = set(group.gr_mem) | {entry.pw_name for entry in pwd.getpwall() if entry.pw_gid == info.st_gid}
    except KeyError:
        return True
    return group.gr_name != user.pw_name or not members <= {user.pw_name, 'root'}


def _open_control(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise ValueError('unsafe admission control directory')
    return fd


def arm(control, intent_sha256):
    if not isinstance(intent_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', intent_sha256):
        raise ValueError('exact transition intent digest required')
    directory = _open_control(control)
    try:
        fd = os.open('transition', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json.dumps({'schema': 'factory.admission-hold.v1',
                                     'intent_sha256': intent_sha256},
                                    sort_keys=True, separators=(',', ':')).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory)
    finally:
        os.close(directory)


def _read_private(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 65536:
            raise ValueError('unsafe admission control file')
        return stream.read()


def _boot_id():
    value = open('/proc/sys/kernel/random/boot_id', encoding='ascii').read(64).strip()
    if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', value):
        raise ValueError('boot identity unavailable')
    return value


def _hold(directory):
    value = json.loads(_read_private(directory, 'transition'))
    if (not isinstance(value, dict) or set(value) != {'schema', 'intent_sha256'} or
            value['schema'] != 'factory.admission-hold.v1' or
            not isinstance(value['intent_sha256'], str) or
            not re.fullmatch(r'[a-f0-9]{64}', value['intent_sha256'])):
        raise ValueError('invalid transition hold')
    return value


def authorize_restarts(control, intent_sha256, unit_hashes):
    """Prepare one boot-bound permit; caller must first verify the active network fence."""
    if (not isinstance(unit_hashes, dict) or not unit_hashes or not set(unit_hashes) <= RESTARTABLE or
            any(not isinstance(hashes, list) or not 1 <= len(hashes) <= 2 or
                any(not isinstance(h, str) or not re.fullmatch(r'[a-f0-9]{64}', h) for h in hashes)
                for hashes in unit_hashes.values())):
        raise ValueError('invalid permitted service hashes')
    directory = _open_control(control)
    try:
        if _hold(directory)['intent_sha256'] != intent_sha256:
            raise ValueError('transition identity differs')
        value = {'schema': 'factory.restart-permit.v1', 'intent_sha256': intent_sha256,
                 'boot_id': _boot_id(), 'units': unit_hashes}
        fd = os.open('restart-permit', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())
            stream.flush(); os.fsync(stream.fileno())
        os.fsync(directory)
    finally:
        os.close(directory)


def prestart(control, unit=None, unit_file=None):
    directory = _open_control(control)
    try:
        try:
            os.stat('transition', dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        if unit not in RESTARTABLE or unit_file is None or os.path.basename(unit_file) != unit + '.service':
            raise RuntimeError('runtime transition requires recovery')
        hold = _hold(directory)
        permit = json.loads(_read_private(directory, 'restart-permit'))
        if (not isinstance(permit, dict) or set(permit) != {'schema', 'intent_sha256', 'boot_id', 'units'} or
                permit['schema'] != 'factory.restart-permit.v1' or
                permit['intent_sha256'] != hold['intent_sha256'] or permit['boot_id'] != _boot_id() or
                not isinstance(permit['units'], dict) or unit not in permit['units'] or
                not isinstance(permit['units'][unit], list)):
            raise ValueError('restart permit differs')
        fd = os.open(unit_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    _unsafe_unit_mode(info) or info.st_size > 65536):
                raise ValueError('unsafe service unit')
            digest = hashlib.sha256(stream.read()).hexdigest()
        if digest not in permit['units'][unit]:
            raise ValueError('service unit differs from restart permit')
    finally:
        os.close(directory)


if __name__ == '__main__':
    if len(sys.argv) not in (3, 5) or sys.argv[1] != 'prestart':
        sys.exit(2)
    try:
        prestart(*sys.argv[2:])
    except (OSError, ValueError, RuntimeError):
        sys.exit(1)
