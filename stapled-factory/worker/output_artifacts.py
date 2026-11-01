"""Private, bounded output capture for disposable workers.

Artifacts contain redacted UTF-8 (invalid bytes become U+FFFD), never original
bytes. stdout/stderr are merged. This is diagnostic storage within the existing
container boundary, not a sandbox against code running with the worker's UID.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time
import uuid

PAGE_BYTES = 4096
_METADATA_RESERVE = 2048
_ID = re.compile(r'^[0-9a-f]{32}$')
_CREDENTIALS = (
    re.compile(rb'(?im)(authorization[\"\']?\s*[=:]\s*)[^\r\n]+'),
    re.compile(rb'(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+=*'),
    re.compile(rb'(?im)((?:api[_-]?key|access[_-]?token|token|password|secret|authorization)[\"\']?\s*[=:]\s*[\"\']?)[^\s\"\'<>]+'),
    re.compile(rb'\b(?:sk-[A-Za-z0-9_-]{8,}|AKIA[A-Z0-9]{16})\b'),
)


def _private(info, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
            or (not directory and info.st_nlink != 1)):
        raise ValueError('private artifact ownership or mode invalid')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _prefix(data, limit):
    """Largest valid UTF-8 prefix within limit (data is valid UTF-8)."""
    return data[:limit].decode('utf-8', errors='ignore').encode()


class CaptureStore:
    def __init__(self, root, workspace, *, secrets=(), max_artifact_bytes=1024*1024,
                 max_total_bytes=8*1024*1024, retention_seconds=86400, redact=None):
        self.root, self.workspace = Path(root), Path(workspace)
        for path in (self.root, self.workspace):
            if not path.is_absolute() or path.resolve() != path:
                raise ValueError('canonical absolute paths required')
        if self.root == self.workspace or self.workspace in self.root.parents:
            raise ValueError('artifact store must be outside workspace')
        if not self.workspace.is_dir():
            raise ValueError('workspace directory required')
        if (type(max_artifact_bytes) is not int or not 4096 <= max_artifact_bytes <= 1024*1024
                or type(max_total_bytes) is not int or not max_artifact_bytes+_METADATA_RESERVE <= max_total_bytes <= 8*1024*1024
                or not 0 < retention_seconds <= 86400):
            raise ValueError('invalid output limits')
        self.limit, self.quota, self.retention = max_artifact_bytes, max_total_bytes, retention_seconds
        self.redactor = redact
        self.secrets = []
        for value in secrets:
            value = value.encode() if isinstance(value, str) else value
            if not isinstance(value, bytes) or len(value) < 4:
                raise ValueError('explicit secrets must contain at least four bytes')
            self.secrets.append(value)
        self.secrets.sort(key=len, reverse=True)
        self.root.mkdir(mode=0o700, exist_ok=True)
        _private(self.root.stat(), directory=True)
        self.identity = (self.root.stat().st_dev, self.root.stat().st_ino)
        info = self.workspace.stat()
        self.binding = hashlib.sha256(_json([str(self.workspace), info.st_dev, info.st_ino])).hexdigest()
        with self._open() as folder:
            lock = self._file(folder, '.lock', os.O_RDWR | os.O_CREAT)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    existing = self._load(folder, '.binding')
                except FileNotFoundError:
                    self._save(folder, '.binding', _json({'workspace': self.binding}))
                else:
                    if existing != {'workspace': self.binding}:
                        raise ValueError('artifact workspace binding mismatch')
            finally:
                os.close(lock)

    @contextmanager
    def _open(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            _private(info, directory=True)
            if (info.st_dev, info.st_ino) != self.identity or self.root.resolve() != self.root:
                raise ValueError('artifact directory identity changed')
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def _file(folder, name, flags):
        try:
            fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=folder)
        except OSError as exc:
            if isinstance(exc, FileNotFoundError):
                raise
            raise ValueError('unsafe artifact file') from None
        try:
            _private(os.fstat(fd))
        except Exception:
            os.close(fd)
            raise
        return fd

    def _load(self, folder, name):
        fd = self._file(folder, name, os.O_RDONLY)
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(self.limit + _METADATA_RESERVE + 1)
        if len(raw) > self.limit + _METADATA_RESERVE:
            raise ValueError('oversized artifact')
        return json.loads(raw)

    def _save(self, folder, name, value):
        fd = self._file(folder, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())

    def _clean_and_size(self, folder):
        now = time.time()
        size = 0
        for name in os.listdir(folder):
            # Unknown files remain untouched and consume quota.
            fd = self._file(folder, name, os.O_RDONLY)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
            base, dot, suffix = name.partition('.')
            if dot and suffix in ('data', 'json') and _ID.fullmatch(base) and now - info.st_mtime > self.retention:
                os.unlink(name, dir_fd=folder)
            else:
                size += info.st_size
        return size

    def _redact(self, data, incomplete):
        for secret in self.secrets:
            data = data.replace(secret, b'[REDACTED]')
            # A quota cut can bisect a known secret at the tail.
            if incomplete:
                for size in range(min(len(secret)-1, len(data)), 0, -1):
                    if data.endswith(secret[:size]):
                        data = data[:-size] + b'[REDACTED]'
                        break
        for expression in _CREDENTIALS:
            data = expression.sub(lambda m: (m.group(1) if m.lastindex else b'') + b'[REDACTED]', data)
        text = data.decode('utf-8', errors='replace')
        if self.redactor is not None:
            try:
                text = self.redactor(text, incomplete=incomplete)
                if not isinstance(text, str):
                    raise TypeError()
            except Exception:
                raise ValueError('output redaction failed; nothing persisted') from None
        return text.encode()

    @staticmethod
    def _stop(process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # macOS can report EPERM for an already-reaped process group.
            if process.poll() is None:
                raise
        process.wait(timeout=2)

    def capture(self, argv, timeout=60, *, cancelled=None):
        if (not isinstance(argv, list) or not argv or len(argv) > 256
                or any(not isinstance(x, str) or '\0' in x for x in argv)
                or sum(len(x.encode()) for x in argv) > 32768
                or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not 0 < timeout <= 60):
            raise ValueError('bounded argv and timeout required')
        if cancelled is not None and not callable(cancelled):
            raise ValueError('cancellation predicate must be callable')
        if self.workspace.resolve() != self.workspace or not self.workspace.is_dir():
            raise ValueError('workspace changed')
        info = self.workspace.stat()
        if hashlib.sha256(_json([str(self.workspace), info.st_dev, info.st_ino])).hexdigest() != self.binding:
            raise ValueError('workspace binding changed')
        with self._open() as folder:
            lock = self._file(folder, '.lock', os.O_RDWR)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if self._clean_and_size(folder) + self.limit + _METADATA_RESERVE > self.quota:
                    raise ValueError('artifact quota exhausted; command not executed')
                raw = bytearray()
                process = None
                reason = 'cancelled' if cancelled is not None and cancelled() else None
                if reason is None:
                    process = subprocess.Popen(argv, cwd=self.workspace, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
                    deadline = time.monotonic() + timeout
                    selector = selectors.DefaultSelector()
                    try:
                        os.set_blocking(process.stdout.fileno(), False)
                        selector.register(process.stdout, selectors.EVENT_READ)
                        while True:
                            if cancelled is not None and cancelled():
                                reason = 'cancelled'
                                break
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                reason = 'timeout'
                                break
                            if not selector.select(min(remaining, .1)):
                                continue
                            chunk = os.read(process.stdout.fileno(), min(65536, self.limit + 1 - len(raw)))
                            if not chunk:
                                # Preserve the real exit status before cleaning descendants.
                                try:
                                    process.wait(timeout=min(.1, max(.001, remaining)))
                                except subprocess.TimeoutExpired:
                                    reason = 'output_closed_while_running'
                                break
                            raw.extend(chunk)
                            if len(raw) > self.limit:
                                raw = raw[:self.limit]
                                reason = 'output_limit'
                                break
                    finally:
                        # Also kill descendants that closed inherited output but keep running.
                        self._stop(process)
                        selector.close()
                        process.stdout.close()
                data = self._redact(bytes(raw), reason is not None)
                if len(data) > self.limit:
                    data = _prefix(data, self.limit)
                    reason = reason or 'redacted_output_limit'
                artifact_id = uuid.uuid4().hex
                metadata = {'artifact_id': artifact_id, 'digest': hashlib.sha256(data).hexdigest(),
                    'total_bytes': len(data), 'capture_complete': reason is None,
                    'reason': reason, 'exit_code': process.returncode if process is not None else None,
                    'encoding': 'utf-8-replacement', 'streams': 'stdout+stderr',
                    'trust': 'untrusted_tool_output'}
                self._save(folder, artifact_id+'.data', data)
                self._save(folder, artifact_id+'.json', _json(metadata))
                return self._page(metadata, data, 0, PAGE_BYTES)
            finally:
                os.close(lock)

    @staticmethod
    def _page(metadata, data, offset, limit):
        try:
            data[:offset].decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError('offset must be a UTF-8 boundary') from None
        page = _prefix(data[offset:], limit)
        end = offset + len(page)
        return {**metadata, 'preview': page.decode(), 'offset': offset,
            'next_offset': end if end < len(data) else None, 'truncated': offset > 0 or end < len(data)}

    def read(self, artifact_id, offset=0, limit=PAGE_BYTES):
        if (not isinstance(artifact_id, str) or not _ID.fullmatch(artifact_id)
                or type(offset) is not int or offset < 0
                or type(limit) is not int or not 4 <= limit <= PAGE_BYTES):
            raise ValueError('invalid artifact page')
        with self._open() as folder:
            lock = self._file(folder, '.lock', os.O_RDWR)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self._clean_and_size(folder)
                try:
                    metadata = self._load(folder, artifact_id+'.json')
                    fd = self._file(folder, artifact_id+'.data', os.O_RDONLY)
                except FileNotFoundError:
                    raise ValueError('unknown or expired artifact') from None
                with os.fdopen(fd, 'rb') as stream:
                    data = stream.read(self.limit+1)
                if (len(data) > self.limit or offset > len(data)
                        or metadata['artifact_id'] != artifact_id or metadata['total_bytes'] != len(data)
                        or metadata['digest'] != hashlib.sha256(data).hexdigest()):
                    raise ValueError('artifact integrity mismatch')
                return self._page(metadata, data, offset, limit)
            finally:
                os.close(lock)
