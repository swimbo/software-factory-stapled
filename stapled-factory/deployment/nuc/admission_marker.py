"""Durable startup hold for an interrupted runtime transition.

This marker alone is not an admission fence. A verified pre-start installation,
network fence, drain and explicit recovery procedure are also required.
"""
import json
import os
import re
import stat
import sys


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


def prestart(control):
    directory = _open_control(control)
    try:
        try:
            os.stat('transition', dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise RuntimeError('runtime transition requires recovery')
    finally:
        os.close(directory)


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != 'prestart':
        sys.exit(2)
    try:
        prestart(sys.argv[2])
    except (OSError, ValueError, RuntimeError):
        sys.exit(1)
