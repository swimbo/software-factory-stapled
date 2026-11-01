"""Opt-in, payload-free observations. Never used for billing or authorization."""
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import stat

SCHEMA = 'factory.harness-telemetry/v1'
TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())


def request_shape(body):
    """Canonical component fingerprints, not provider cache keys or raw slices."""
    tools = body.get('tools')
    system = body.get('system')
    messages = body.get('messages')
    output = 0
    observed = False
    if isinstance(messages, list):
        for message in messages:
            content = message.get('content') if isinstance(message, dict) else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'tool_result':
                        output += _size(block.get('content'))
                        observed = True
    return {'system_sha256': digest(system) if system is not None else None,
            'system_bytes': _size(system) if system is not None else None,
            'tools_sha256': digest(tools) if tools is not None else None,
            'tool_schema_bytes': _size(tools) if tools is not None else None,
            'tool_schema_count': len(tools) if isinstance(tools, list) else None,
            'message_count': len(messages) if isinstance(messages, list) else None,
            # Historical tool results in this request, not unique output per run.
            'tool_result_bytes': output if observed else None}


def _count(value):
    return value if type(value) is int and 0 <= value <= 2**63-1 else None


class ResponseObserver:
    """Bounded incremental Anthropic JSON/SSE parser; never stores content."""
    def __init__(self, content_type, limit=256*1024):
        self.sse = content_type.split(';')[0].strip().lower() == 'text/event-stream'
        self.limit = limit
        self.buffer = bytearray()
        self.result = dict.fromkeys(TOKEN_FIELDS)
        self.result.update(tool_calls=None, stop_reason=None, message_complete=False,
                           response_model_sha256=None, parse_status='missing', output_tokens_final=False)
        self.tool_calls = 0
        self.started = False
        self.disabled = False

    def feed(self, data):
        if self.disabled:
            return
        self.buffer.extend(data)
        if self.sse:
            # SSE delimiters are ASCII; normalize only framing, never forwarded bytes.
            self.buffer[:] = self.buffer.replace(b'\r\n', b'\n')
            while b'\n\n' in self.buffer:
                event, _, rest = self.buffer.partition(b'\n\n')
                self.buffer[:] = rest
                if len(event) > self.limit:
                    self._disable('oversized'); return
                payload = b'\n'.join(line[5:].lstrip(b' ') for line in event.split(b'\n') if line.startswith(b'data:'))
                if payload:
                    self._parse(payload)
        if len(self.buffer) > self.limit:
            self._disable('oversized')

    def _disable(self, reason):
        self.disabled = True
        self.buffer.clear()
        self.result['parse_status'] = reason

    def _parse(self, raw):
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            self._event(value)
            if self.result['parse_status'] != 'malformed':
                self.result['parse_status'] = 'observed'
        except (ValueError, TypeError, RecursionError):
            self.result['parse_status'] = 'malformed'

    def _event(self, value):
        kind = value.get('type')
        if not self.sse or kind == 'message_start':
            message = value.get('message') if self.sse else value
            if not isinstance(message, dict):
                raise ValueError()
            self.started = True
            model = message.get('model')
            if isinstance(model, str):
                self.result['response_model_sha256'] = digest(model)
            self._usage(message.get('usage'), final=not self.sse)
            if not self.sse:
                content = message.get('content')
                if isinstance(content, list):
                    self.tool_calls = sum(isinstance(x, dict) and x.get('type') == 'tool_use' for x in content)
                    self.result['tool_calls'] = self.tool_calls
                self._stop(message)
                self.result['message_complete'] = isinstance(content, list) and self.result['stop_reason'] is not None
        elif kind == 'message_delta':
            self._usage(value.get('usage'), final=True)
            self._stop(value.get('delta'))
        elif kind == 'content_block_start':
            block = value.get('content_block')
            if isinstance(block, dict) and block.get('type') == 'tool_use':
                self.tool_calls += 1
        elif kind == 'message_stop' and self.started:
            self.result['message_complete'] = True
            self.result['tool_calls'] = self.tool_calls

    def _usage(self, usage, *, final=False):
        if isinstance(usage, dict):
            for name in TOKEN_FIELDS:
                if name in usage:
                    self.result[name] = _count(usage[name])
                    if name == 'output_tokens':
                        self.result['output_tokens_final'] = final and self.result[name] is not None

    def _stop(self, value):
        if isinstance(value, dict) and value.get('stop_reason') in (
                'end_turn', 'max_tokens', 'stop_sequence', 'tool_use', 'pause_turn', 'refusal'):
            self.result['stop_reason'] = value['stop_reason']

    def finish(self):
        if not self.sse and self.buffer and not self.disabled:
            self._parse(bytes(self.buffer))
        self.buffer.clear()
        return dict(self.result)


# A fixed field allowlist prevents callers from accidentally persisting payloads.
FIELDS = frozenset({'kind', 'request_id', 'schema', 'attempt_sha256', 'fingerprint',
    'model_sha256', 'session_sha256', 'request_sha256', 'request_bytes',
    'reserved_microdollars', 'http_status', 'elapsed_ms', 'response_bytes',
    'transport_complete', 'transport_error', 'observation',
    *request_shape({}).keys(), *TOKEN_FIELDS, 'tool_calls', 'stop_reason',
    'message_complete', 'response_model_sha256', 'parse_status', 'output_tokens_final'})


class TelemetryLog:
    """Private append-only, size-bounded diagnostic log with immutable binding.

    Caller provisions a trusted, private directory outside worker mounts. This
    is diagnostic evidence, not a signed ledger or a rollback-resistant store.
    """
    def __init__(self, directory, attempt, fingerprint, max_bytes=4*1024*1024):
        if not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
            raise ValueError('SHA-256 attempt fingerprint required')
        directory = Path(directory)
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError('private telemetry directory required')
        self.directory = directory
        self.max_bytes = max_bytes
        self.binding = {'schema': SCHEMA, 'attempt_sha256': digest(attempt), 'fingerprint': fingerprint}
        self._write({'kind': 'binding', **self.binding}, initialize=True)

    def emit(self, record):
        if record.keys() - FIELDS or record.get('kind') not in ('request_started', 'request_ended', 'conversation_observed'):
            raise ValueError('unsupported telemetry fields')
        self._write({**record, **self.binding})

    def _write(self, record, initialize=False):
        raw = (json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()
        fd = os.open(self.directory/'telemetry.jsonl', os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > self.max_bytes):
                raise ValueError('invalid telemetry file')
            if info.st_size:
                os.lseek(fd, 0, os.SEEK_SET)
                header = bytearray()
                while len(header) < min(info.st_size, 2048):
                    char = os.read(fd, 1)
                    header.extend(char)
                    if char == b'\n': break
                expected = {'kind': 'binding', **self.binding}
                if json.loads(header) != expected or os.pread(fd, 1, info.st_size-1) != b'\n':
                    raise ValueError('telemetry binding or history mismatch')
                if initialize: return
            elif not initialize:
                raise ValueError('telemetry binding missing')
            if info.st_size + len(raw) > self.max_bytes:
                raise ValueError('telemetry quota exhausted')
            if os.write(fd, raw) != len(raw):
                raise OSError('incomplete telemetry append')
            os.fsync(fd)
        finally:
            os.close(fd)


def summarize(records):
    """Keep totals unknown unless every observed reservation has that metric."""
    starts = {}
    ends = {}
    for record in records:
        if record.get('kind') == 'request_started':
            if record['request_id'] in starts:
                raise ValueError('duplicate request start')
            starts[record['request_id']] = record
        elif record.get('kind') == 'request_ended':
            if record['request_id'] in ends:
                raise ValueError('duplicate request end')
            ends[record['request_id']] = record
    if ends.keys() - starts.keys():
        raise ValueError('request end without start')
    result = {'schema': SCHEMA, 'requests_observed': len(starts),
              'requests_without_end': len(starts.keys()-ends.keys()),
              'actual_cost_usd': None, 'cached_input_share': None,
              'actual_routed_model': None, 'retries': None}
    completed = [r for r in ends.values() if r.get('message_complete') is True
                 and r.get('transport_complete') is True and r.get('parse_status') == 'observed']
    result['complete_responses'] = len(completed)
    for field in TOKEN_FIELDS:
        values = [r.get(field) for r in ends.values()]
        known = [x for x in values if _count(x) is not None]
        result['observed_'+field] = sum(known) if known else None
        final = [r.get(field) for r in completed if _count(r.get(field)) is not None
                 and (field != 'output_tokens' or r.get('output_tokens_final') is True)]
        result[field+'_coverage'] = len(final)
        result[field] = sum(final) if starts and len(final) == len(starts) else None
    amounts = [r.get('reserved_microdollars') for r in starts.values()]
    result['estimated_reserved_microdollars'] = (sum(amounts) if amounts and
        all(_count(x) is not None for x in amounts) else None)
    return result
