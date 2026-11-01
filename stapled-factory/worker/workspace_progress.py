"""Bounded content progress observations; incomplete scans never mean no progress."""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time

MAX_NODES = 2048
MAX_FILES = 256
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 16
MAX_GIT_OUTPUT = 128 * 1024
MAX_INDEX_BYTES = 2 * 1024 * 1024
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_GIT_RUNNER = """
import os, resource, sys
resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
if sys.platform.startswith('linux'):
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024**2, 256 * 1024**2))
os.execvp(sys.argv[1], sys.argv[1:])
"""


def _parts(path):
    parts = path.split('/')
    if not path or any(p in ('', '.', '..') for p in parts):
        raise ValueError('unsafe relative path')
    return parts


def _open_root(path):
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('canonical workspace required')
    fd = os.open('/', DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        if os.fstat(fd).st_uid != os.geteuid():
            raise ValueError('workspace ownership changed')
        return fd
    except Exception:
        os.close(fd)
        raise


def _read(root_fd, relative, maximum):
    parts = _parts(relative)
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
            if os.fstat(fd).st_uid != os.geteuid():
                raise ValueError('directory ownership changed')
        file = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(file)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != os.geteuid() or before.st_size > maximum):
                raise ValueError('unsafe or oversized file')
            chunks = []
            size = 0
            while True:
                chunk = os.read(file, min(65536, maximum - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    raise ValueError('file byte limit')
                chunks.append(chunk)
            after = os.fstat(file)
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')
            if any(getattr(before, field) != getattr(after, field) for field in fields):
                raise ValueError('file changed during read')
            return b''.join(chunks), bool(before.st_mode & 0o111)
        finally:
            os.close(file)
    finally:
        os.close(fd)


def _head(fd):
    value = _read(fd, '.git/HEAD', 1024)[0].strip()
    if value.startswith(b'ref: '):
        reference = os.fsdecode(value[5:])
        if not reference.startswith('refs/'):
            raise ValueError('invalid HEAD reference')
        _parts(reference)
        try:
            value = _read(fd, '.git/' + reference, 1024)[0].strip()
        except FileNotFoundError:
            packed = _read(fd, '.git/packed-refs', MAX_GIT_OUTPUT)[0]
            matches = [line.split()[0] for line in packed.splitlines()
                       if len(line.split()) == 2 and line.split()[1] == os.fsencode(reference)]
            if len(matches) != 1:
                raise ValueError('HEAD reference unavailable')
            value = matches[0]
    if not re.fullmatch(b'[0-9a-f]{40}|[0-9a-f]{64}', value):
        raise ValueError('invalid HEAD object identity')
    return value.decode('ascii')


def _git(workspace, directory, *, untracked=False):
    # The worker's config, hooks, includes and fsmonitor never enter this Git process.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
    args = ['git', '--git-dir=' + str(directory), '--work-tree=' + str(workspace),
            '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
            '-c', 'core.excludesFile=' + os.devnull, '-c', 'core.hooksPath=' + os.devnull,
            'ls-files', *(['--others', '--exclude-standard'] if untracked else ['--stage']), '-z']
    process = subprocess.Popen([sys.executable, '-I', '-c', _GIT_RUNNER, *args],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 5
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('Git observation timeout')
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, min(65536, MAX_GIT_OUTPUT - len(output) + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > MAX_GIT_OUTPUT:
                            raise ValueError('Git output limit')
            if process.wait(timeout=1):
                raise ValueError('Git observation unavailable')
        return bytes(output)
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)


def _eligible(relative):
    parts = [part.lower() for part in _parts(relative)]
    for name in parts:
        if (name in ('.git', '.ssh', 'secrets', 'credentials', 'id_rsa', 'id_ed25519')
                or name == '.env' or name.startswith('.env.')
                or name.endswith(('.log', '.pem', '.key', '.p12', '.pfx', '.secret'))
                or re.fullmatch(r'(?:credentials?|secrets?|api[_-]?keys?|tokens?)(?:\..*)?', name)):
            return False
    return True


def _check_tree(fd, prefix='', depth=0, nodes=None):
    # Git deliberately omits FIFOs and some other special files. Observe inode
    # types independently; never open ignored file contents or follow links.
    if nodes is None:
        nodes = [0, 0]
    with os.scandir(fd) as entries:
        for entry in entries:
            relative = prefix + entry.name
            if not _eligible(relative):
                continue
            nodes[0] += 1
            if nodes[0] > MAX_NODES or depth + 1 > MAX_DEPTH:
                raise ValueError('workspace metadata scan limit')
            info = entry.stat(follow_symlinks=False)
            if info.st_uid != os.geteuid():
                raise ValueError('workspace entry ownership changed')
            if entry.name.lower() == '.gitignore':
                nodes[1] += info.st_size
                if info.st_size > MAX_FILE_BYTES or nodes[1] > MAX_TOTAL_BYTES:
                    raise ValueError('ignore input byte limit')
            if stat.S_ISDIR(info.st_mode):
                child = os.open(entry.name, DIRECTORY, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise ValueError('workspace directory changed')
                    _check_tree(child, relative + '/', depth + 1, nodes)
                finally:
                    os.close(child)
            elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('workspace symlink or special file')


def snapshot(workspace):
    result = {'schema': 'factory.workspace-progress/v1', 'complete': False,
              'sha256': None, 'files': 0, 'bytes': 0}
    fd = None
    try:
        root = Path(workspace)
        fd = _open_root(root)
        _check_tree(fd)
        head = _head(fd)
        index = _read(fd, '.git/index', MAX_INDEX_BYTES)[0]
        # A private index copy gives semantic staged entries without consulting
        # worker-controlled Git config or mutating its stat cache.
        with tempfile.TemporaryDirectory(prefix='factory-progress-') as temp:
            directory = Path(temp)
            (directory/'objects').mkdir()
            (directory/'refs').mkdir()
            (directory/'HEAD').write_text(head + '\n')
            (directory/'index').write_bytes(index)
            try:
                excluded = _read(fd, '.git/info/exclude', MAX_GIT_OUTPUT)[0]
            except FileNotFoundError:
                excluded = b''
            (directory/'info').mkdir()
            (directory/'info'/'exclude').write_bytes(excluded)
            listings = [(_git(root, directory), True), (_git(root, directory, untracked=True), False)]
        candidates = {}
        index_entries = []
        nodes = set()
        for listing, tracked in listings:
            entries = listing.split(b'\0')
            if entries[-1] != b'':
                raise ValueError('incomplete Git listing')
            for entry in entries[:-1]:
                match = re.fullmatch(rb'([0-7]{6}) ([0-9a-f]{40}|[0-9a-f]{64}) ([0-3])\t(.*)', entry) if tracked else None
                if tracked and match is None:
                    raise ValueError('invalid index entry')
                path = os.fsdecode(match[4] if match else entry)
                if not _eligible(path):
                    continue
                parts = _parts(path)
                if len(parts) > MAX_DEPTH:
                    raise ValueError('workspace depth limit')
                nodes.update('/'.join(parts[:i]) for i in range(1, len(parts) + 1))
                if len(nodes) > MAX_NODES:
                    raise ValueError('workspace node limit')
                candidates[path] = tracked or candidates.get(path, False)
                if match:
                    index_entries.append([path, *(os.fsdecode(value) for value in match.group(1, 2, 3))])
        if len(candidates) > MAX_FILES:
            raise ValueError('workspace file count limit')
        records = []
        for path, tracked in sorted(candidates.items()):
            try:
                content, executable = _read(fd, path, min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - result['bytes']))
            except FileNotFoundError:
                if not tracked:
                    raise
                records.append([path, 'deleted'])
                continue
            result['files'] += 1
            result['bytes'] += len(content)
            if result['bytes'] > MAX_TOTAL_BYTES:
                raise ValueError('workspace total byte limit')
            records.append([path, hashlib.sha256(content).hexdigest(), executable])
        if _head(fd) != head or _read(fd, '.git/index', MAX_INDEX_BYTES)[0] != index:
            raise ValueError('Git state changed during observation')
        content = json.dumps([head, sorted(index_entries), records], ensure_ascii=True,
                             separators=(',', ':')).encode()
        result.update(complete=True, sha256=hashlib.sha256(content).hexdigest())
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        pass  # Unknown progress cannot establish a stall.
    finally:
        if fd is not None:
            os.close(fd)
    return result
