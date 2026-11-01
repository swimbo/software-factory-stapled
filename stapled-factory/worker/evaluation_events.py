"""Operator-only metadata audit of retained SDK events after namespace stop.

Event completion means the directory was read in full within these limits, not
that every tool completed. Callers must stop the original namespace first.
"""
import collections
import hashlib
import json
import os
from pathlib import Path
import stat
import shlex
from evaluation_diagnostics import diagnostic_imports, diagnostic_error
import uuid


DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
MAX_NODES = 400
MAX_FILE = 1048576
MAX_TOTAL = 8388608


def _owned(fd):
    info = os.fstat(fd)
    if info.st_uid != os.geteuid():
        raise ValueError('event ownership changed')
    return info


def _read_events(workspace, conversation_id):
    root = Path(workspace)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError('canonical workspace required')
    cid = uuid.UUID(conversation_id)
    fd = os.open(root, DIR)
    try:
        _owned(fd)
        for name in ('.git', 'factory-home', 'conversations'):
            child = os.open(name, DIR, dir_fd=fd)
            os.close(fd)
            fd = child
            _owned(fd)
        matches = [name for name in os.listdir(fd) if name in (str(cid), cid.hex)]
        if len(matches) != 1:
            raise ValueError('conversation directory missing or ambiguous')
        for name in (matches[0], 'events'):
            child = os.open(name, DIR, dir_fd=fd)
            os.close(fd)
            fd = child
            _owned(fd)
        names = os.listdir(fd)
        if len(names) > MAX_NODES:
            raise ValueError('event node limit')
        events = []
        total = 0
        for name in names:
            if not name.endswith('.json'):
                continue  # SDK lock/marker files are not event records.
            file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                before = _owned(file)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE:
                    raise ValueError('unsafe event file')
                with os.fdopen(os.dup(file), 'rb') as stream:
                    raw = stream.read(MAX_FILE + 1)
                total += len(raw)
                if len(raw) > MAX_FILE or total > MAX_TOTAL:
                    raise ValueError('event file byte limit')
                after = _owned(file)
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                    raise ValueError('event file changed during read')
                event = json.loads(raw)
                if not isinstance(event, dict):
                    raise ValueError('invalid event record')
                events.append(event)
            finally:
                os.close(file)
        return events, total
    finally:
        os.close(fd)


def audit_events(workspace, conversation_id, diagnostic_text, dependency=None):
    events, total = _read_events(workspace, conversation_id)
    events.sort(key=lambda event: (event.get('timestamp', ''), event.get('id', '')))
    actions = {event.get('id'): event for event in events if event.get('kind') == 'ActionEvent'}
    kinds = collections.Counter()
    tools = collections.Counter()
    codes = collections.Counter()
    commands = []
    diagnostics = completed = failed = incomplete = unpaired = errors = 0
    imports = 0 if dependency is not None else None
    dependency_errors = installs = 0
    for event in events:
        kind = event.get('kind')
        kinds[kind if kind in ('ActionEvent', 'ObservationEvent', 'MessageEvent', 'SystemPromptEvent') else 'other'] += 1
        if kind == 'ActionEvent':
            tool = event.get('tool_name')
            tools[tool if tool in ('terminal', 'file_editor', 'factory_capture', 'finish', 'task_tracker') else 'other'] += 1
            action = event.get('action') or {}
            command = action.get('command')
            if command is None and isinstance(action.get('argv'), list): command = shlex.join(action['argv'])
            if isinstance(command, str):
                commands.append(hashlib.sha256(command.encode()).hexdigest())
                installs += any(x in command for x in ('pip install', 'pip3 install', 'uv add', 'uv pip install'))
                if dependency is not None and imports is not None:
                    try: imports += diagnostic_imports(command, dependency)
                    except (ValueError, SyntaxError): imports = None
        elif kind == 'MessageEvent':
            message = event.get('llm_message') or {}
            text = ''.join(part.get('text', '') for part in message.get('content', []) if part.get('type') == 'text')
            diagnostics += event.get('source') == 'user' and message.get('role') == 'user' and text == diagnostic_text
        elif kind == 'ObservationEvent':
            obs = event.get('observation') or {}
            errors += obs.get('is_error') is True
            original = actions.get(event.get('action_id'))
            if (dependency and original and original.get('tool_call_id') == event.get('tool_call_id')
                    and diagnostic_error(obs, dependency)):
                dependency_errors += 1
            if event.get('tool_name') != 'terminal':
                continue
            action = actions.get(event.get('action_id'))
            if not action or action.get('tool_name') != 'terminal' or action.get('tool_call_id') != event.get('tool_call_id'):
                unpaired += 1
                continue
            code = obs.get('exit_code')
            if type(code) is not int or code == -1 or obs.get('timeout') is True:
                incomplete += 1
                continue
            completed += 1
            codes[str(code)] += 1
            failed += code != 0 or obs.get('is_error') is True
    return {
        'schema': 'factory.evaluation-events/v1', 'event_count': len(events),
        'event_files_bytes': total, 'event_kinds': dict(kinds), 'tool_actions': dict(tools),
        'terminal_completed': completed, 'terminal_failed': failed,
        'terminal_incomplete': incomplete, 'terminal_unpaired': unpaired,
        'terminal_exit_codes': dict(codes), 'error_observations': errors,
        'diagnostic_messages': diagnostics, 'command_sha256': commands,
        'dependency_diagnostic_commands': imports, 'dependency_error_observations': dependency_errors,
        'install_commands': installs,
        'truncations': None, 'truncation_coverage': 'not available from pinned SDK event contract',
        'full_completed': True, 'raw_thoughts_exported': False,
        'requires_stopped_namespace': True,
    }
