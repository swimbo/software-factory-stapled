"""Explicit operator-only held-out trials. Frozen candidates; no retries or tuning.

Fixtures/oracles stay in a private setup directory outside all worker mounts.
Signed input verification happens before binding services or dispatching work.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid

from baseline_trials import (command, config_file, dispatch_once, final_accounting,
                             freeze_candidate, post, restore, trial_binding, write_new)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_candidate(setup, entry, runtime, candidate):
    name = 'factory-eval-check-' + entry['unit']
    args = ['docker', 'run', '--rm', '--name', name, '--label',
            'factory.evaluation.unit=' + entry['unit'], '--network', 'none',
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--user', str(os.geteuid()) + ':' + str(os.geteuid()), '--memory', '128m',
            '--cpus', '1', '--pids-limit', '32', '--tmpfs', '/tmp:rw,nosuid,nodev',
            '--mount', 'type=bind,src=' + str(candidate) + ',dst=/candidate,readonly',
            '--mount', 'type=bind,src=' + str(setup/'oracle') + ',dst=/oracle,readonly',
            '--entrypoint', 'python3', runtime['provision']['helper_image'],
            '-B', '/oracle/check.py', entry['task_id'], '/candidate']
    try:
        result = subprocess.run(args, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'passed': result.returncode == 0, 'exit_code': result.returncode}
    except subprocess.TimeoutExpired:
        # Only remove this exact owned check process, never worker resources.
        info = json.loads(command('docker', 'inspect', name))[0]
        if (info['Config']['Labels'].get('factory.evaluation.unit') != entry['unit']
                or info['Image'] != runtime['provision']['helper_image']):
            raise ValueError('checker ownership changed')
        command('docker', 'rm', '-f', info['Id'])
        return {'passed': False, 'timed_out': True}


def _gateway(unit):
    session = str(uuid.uuid5(uuid.NAMESPACE_URL, 'stapled-factory-attempt:' + unit + ':0'))
    with sqlite3.connect('file:/home/bo/.hermes/bifrost/logs.db?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(
            'SELECT id,provider,model,served_model,status,prompt_tokens,completion_tokens,cached_read_tokens '
            'FROM logs WHERE session_id=? ORDER BY timestamp', (session,))]


def _stop_original(root, unit, binding):
    from guarded_store import GuardedAttemptStore
    from deadline_watchdog import _path, expire
    from crash_fence import fence_bound_worker
    store = GuardedAttemptStore(root/'attempt-store/attempts.db', root/'attempt-anchor/watermark.json')
    with store._transaction() as (db, _):
        row = db.execute('SELECT a.request_hash,a.conversation_id,a.status,l.specification_hash,l.allocation_id '
                         'FROM attempts a JOIN allocations l ON a.attempt_id=l.attempt_id WHERE a.attempt_id=?',
                         (unit + ':0',)).fetchone()
    if not row: raise ValueError('original allocation missing')
    request_hash, cid, status, spec, allocation = row
    deadline = _path(store, allocation)
    if deadline.exists():
        cleanup = expire(deadline, cancel=True)
    else:
        # Baseline has no bounded-profile timer; identical operator observation limit.
        trial_binding(root, unit)
        command('docker', 'stop', '--time', '5', binding['worker_id'], binding['proxy_id'], timeout=30)
        with store._transaction() as (db, _):
            status = db.execute('SELECT status FROM attempts WHERE attempt_id=?', (unit + ':0',)).fetchone()[0]
        if status == 'reserved': fence_bound_worker(store, unit + ':0', request_hash, cid, spec)
        cleanup = {'status': 'fenced' if status == 'reserved' else 'stopped-' + status}
    for role in ('worker', 'proxy'):
        if json.loads(command('docker', 'inspect', binding[role + '_id']))[0]['State']['Running']:
            raise ValueError('original namespace still running')
    return cleanup, cid, allocation


def _live_events(worker, cid, dependency=None):
    # Baseline SDK state lives on tmpfs: reduce metadata before stopping it.
    from baseline_trials import TRACE_CLIENT
    from stall_runtime import DIAGNOSTIC
    from evaluation_diagnostics import diagnostic_imports, diagnostic_error
    import inspect
    code = TRACE_CLIENT.replace("action=event.get('action') or {};command=action.get('command')",
                                "action=event.get('action') or {};command=action.get('command');command=shlex.join(action['argv']) if command is None and isinstance(action.get('argv'),list) else command")
    code = 'import shlex\n' + code
    code = code.replace('observed_bytes=0;', 'fixed_diagnostics=0;observed_bytes=0;')
    code = code.replace("if kind=='ActionEvent':", "if kind=='MessageEvent':\n   m=event.get('llm_message') or {};t=''.join(p.get('text','') for p in m.get('content',[]) if p.get('type')=='text');fixed_diagnostics+=event.get('source')=='user' and m.get('role')=='user' and t==" + repr(DIAGNOSTIC) + "\n  if kind=='ActionEvent':")
    code = code.replace("'event_count':len(seen)", "'diagnostic_messages':fixed_diagnostics,'event_count':len(seen)")
    if dependency is not None:
        code = code.replace("if 'factory_missing_dependency' in command and ('import factory_missing_dependency' in command or 'import_module(' in command):diagnostics+=1",
                            'diagnostics+=diagnostic_imports(command,' + repr(dependency) + ')')
        code = inspect.getsource(diagnostic_imports) + '\n' + inspect.getsource(diagnostic_error) + '\n' + code
        code = 'paired_actions={};dependency_errors=[]\n' + code
        code = code.replace("action=event.get('action') or {};command=", "paired_actions[event.get('id')]=event.get('tool_call_id');action=event.get('action') or {};command=")
        code = code.replace("if kind=='ObservationEvent':observed_bytes+=len(json.dumps(event.get('observation'),ensure_ascii=False).encode())",
            "if kind=='ObservationEvent':\n   observed_bytes+=len(json.dumps(event.get('observation'),ensure_ascii=False).encode())\n   if diagnostic_error(event.get('observation') or {}," + repr(dependency) + "):\n    dependency_errors.append((event.get('action_id'),event.get('tool_call_id')))")
        code = code.replace("'event_count':len(seen)", "'dependency_error_observations':sum(a in paired_actions and paired_actions[a]==t for a,t in dependency_errors),'event_count':len(seen)")
    try:
        value = json.loads(command('docker', 'exec', worker, 'python3', '-c', code, cid, timeout=45))
        value.update(full_completed=value.pop('complete'), truncations=None,
                     truncation_coverage='not available from pinned SDK event contract',
                     observation_boundary='live SDK metadata before original namespace stop',
                     raw_thoughts_exported=False)
        return value
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'full_completed': False, 'diagnostic_messages': None, 'truncations': None,
                'observation_boundary': 'live metadata unavailable; never infer zero'}


def _collect_unbound(config, entry, started, finished):
    from guarded_store import GuardedAttemptStore
    root = Path(config['root']); unit = entry['unit']
    intent = root/'dispatch'/unit/'baseline.dispatch-intent.json'
    saved = json.loads(intent.read_text())
    if saved['request']['unitId'] != unit: raise ValueError('original dispatch identity differs')
    state = post(root, 'WorkUnit/' + unit + '/getState', {})
    if state['stage'] not in ('failed', 'needs_reconciliation'):
        raise ValueError('unbound original still uncertain; retain operator ownership')
    store = GuardedAttemptStore(root/'attempt-store/attempts.db', root/'attempt-anchor/watermark.json')
    with store._transaction() as (db, _):
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'allocation_bindings' in tables and db.execute('SELECT 1 FROM allocation_bindings WHERE attempt_id=?', (unit + ':0',)).fetchone():
            raise ValueError('binding exists; collect the original allocation')
        row = db.execute('SELECT allocation_id FROM allocations WHERE attempt_id=?', (unit + ':0',)).fetchone() if 'allocations' in tables else None
        allocation = row[0] if row else None
        if allocation and command('docker', 'ps', '-aq', '--filter', 'label=factory.allocation=' + allocation):
            raise ValueError('partial original namespace exists; zero activity unproven')
        gateway = _gateway(unit)
        if gateway: raise ValueError('gateway activity exists without binding; retain unknown')
    # The provision callback binds before credentials; absence plus a returned
    # coordinator failure and no namespace/gateway activity establishes zero calls.
    return {**entry, 'accepted': False, 'execution_contract_passed': False,
            'requests': 0, 'input_tokens': 0, 'output_tokens': 0,
            'elapsed_s': round(finished-started, 3), 'gateway_models': [],
            'accounting_complete': True, 'estimated_reserved_usd': 0,
            'stall_false_positives': 0, 'truncations': 0, 'state': state,
            'prebinding_zero_proven': True, 'allocation_id': allocation,
            'gateway': [], 'resources_stopped': True, 'isolation_verified': None,
            'root_reservation_retained': True, 'actual_charges_usd': None,
            'cleanup': {'status': 'no-bound-namespace; original state retained'}}


def collect(config, entry, runtime, started, finished):
    from evaluation_events import audit_events
    from stall_runtime import DIAGNOSTIC
    from revision import verify_revision
    root, setup = Path(config['root']), Path(config['setup'])
    unit = entry['unit']; trial = root/'dispatch'/unit
    try:
        binding = trial_binding(root, unit)
    except ValueError as error:
        if str(error) != 'bound allocation unavailable': raise
        return _collect_unbound(config, entry, started, finished)
    info = json.loads(command('docker', 'inspect', binding['worker_id']))[0]
    isolation = (info['HostConfig']['NetworkMode'] == 'none'
                 and {m['Destination'] for m in info['Mounts']} == {'/gateway', '/workspace'})
    if not isolation: raise ValueError('worker isolation changed')
    observation = trial/'evaluation.observed.json'
    if observation.exists():
        observed = json.loads(observation.read_text())
        live_events = observed['events']; finished = observed['finished']
    else:
        live_events = None
        if entry['profile'] in ('baseline', 'context-v1'):
            from guarded_store import GuardedAttemptStore
            store = GuardedAttemptStore(root/'attempt-store/attempts.db', root/'attempt-anchor/watermark.json')
            with store._transaction() as (db, _):
                cid = db.execute('SELECT conversation_id FROM attempts WHERE attempt_id=?', (unit + ':0',)).fetchone()[0]
            live_events = _live_events(binding['worker_id'], cid, entry.get('diagnostic_dependency'))
        write_new(observation, {'finished': finished, 'events': live_events})
    cleanup, cid, allocation = _stop_original(root, unit, binding)
    state = post(root, 'WorkUnit/' + unit + '/getState', {})
    final = final_accounting(binding, runtime)
    events = live_events if live_events is not None else audit_events(runtime['repo'], cid, DIAGNOSTIC, entry.get('diagnostic_dependency'))
    revision = state.get('build', {}).get('git_revision'); changed = []; archive = None
    check = {'passed': False, 'reason': 'worker did not finish'}
    if state['stage'] in ('needs_validation', 'validated') and revision:
        if verify_revision(runtime['repo'], runtime['baseline']) != revision: raise ValueError('result identity changed')
        changed = command('git', '-c', 'core.fsmonitor=false', '-C', runtime['repo'], 'diff', '--name-only',
                          runtime['baseline'], revision).splitlines()
        if changed and set(changed) <= set(entry['allowed_paths']):
            candidate = trial/'evaluation.candidate'
            frozen = trial/'evaluation.candidate.json'
            if frozen.exists():
                record = json.loads(frozen.read_text())
                if record['revision'] != revision or candidate.is_symlink() or not candidate.is_dir():
                    raise ValueError('frozen original candidate changed')
                archive = record['archive_sha256']
            else:
                archive = freeze_candidate(runtime['repo'], runtime['baseline'], revision, candidate)
                write_new(frozen, {'revision': revision, 'archive_sha256': archive})
            check = check_candidate(setup, entry, runtime, candidate)
    requests = sum(row[3] for row in final['ledger']['budgets'])
    telemetry = final['telemetry']; gateway = _gateway(unit)
    # Read-only bounded observation of asynchronous Bifrost persistence; no retry of work.
    until = time.monotonic() + 20
    while len(gateway) < requests and time.monotonic() < until:
        time.sleep(1); gateway = _gateway(unit)
    token_keys = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
    prompt_tokens = sum(telemetry[k] for k in token_keys) if all(telemetry.get(k) is not None for k in token_keys) else None
    estimated_microdollars = sum(row[-1] for row in final['ledger']['monetary_budgets'])
    token_parity = (prompt_tokens is not None and telemetry.get('output_tokens') is not None
                    and all(type(r[k]) is int for r in gateway for k in ('prompt_tokens', 'completion_tokens', 'cached_read_tokens'))
                    and sum(r['prompt_tokens'] for r in gateway) == prompt_tokens
                    and sum(r['completion_tokens'] for r in gateway) == telemetry['output_tokens']
                    and sum(r['cached_read_tokens'] for r in gateway) == telemetry['cache_read_input_tokens'])
    parity = (token_parity and telemetry.get('estimated_reserved_microdollars') == estimated_microdollars and requests == len(gateway) == telemetry['requests_observed'] == telemetry['complete_responses']
              and len({r['id'] for r in gateway}) == requests and all(r['status'] == 'success' for r in gateway))
    models = sorted({r['provider'] + '/' + (r['served_model'] or r['model']) for r in gateway})
    stallpath = root/'attempt-anchor/stalls'/(allocation + '.json')
    stall = json.loads(stallpath.read_text()) if stallpath.exists() else None
    estimated = estimated_microdollars/1e6
    execution_passed = (not entry.get('diagnostic_dependency') or events['full_completed']
                        and events.get('dependency_diagnostic_commands') == 1
                        and events.get('dependency_error_observations') == 1
                        and events.get('install_commands') == 0)
    return {**entry, 'accepted': check['passed'] and execution_passed, 'execution_contract_passed': execution_passed, 'requests': requests,
            'input_tokens': prompt_tokens, 'output_tokens': telemetry.get('output_tokens'),
            'elapsed_s': round(finished-started, 3), 'gateway_models': models,
            'accounting_complete': parity, 'estimated_reserved_usd': estimated,
            'stall_false_positives': 0 if (not stall or stall.get('delivery_status') in (None, 'not_sent')) and events['full_completed'] and events['diagnostic_messages'] == 0 else None,
            'truncations': events['truncations'], 'events': events, 'stall_record': stall,
            'state': state, 'revision': revision, 'changed': changed, 'candidate_check': check,
            'candidate_archive_sha256': archive, 'accounting': final, 'gateway': gateway,
            'cleanup': cleanup, 'isolation_verified': isolation, 'resources_stopped': True,
            'allocation_id': allocation, 'conversation_id': cid, 'actual_charges_usd': None}


def run(config, unit, *, observe_only=False):
    from image_approval import verify_approval
    from operator_cli import _private_file
    root, setup = Path(config['root']), Path(config['setup'])
    prereg = json.loads((setup/'preregister.json').read_text())
    entry = next(e for e in prereg['schedule'] if e['unit'] == unit)
    trial = root/'dispatch'/unit; runtime = config_file(trial/'runtime.json')
    verify_approval(_private_file(config['approval']), _private_file(config['signature']),
                    _private_file(config['trust_root']), runtime['provision'])
    if digest(trial/'runtime.json') != entry['runtime_sha256']: raise ValueError('frozen runtime changed')
    output = setup/'results'/(unit + '.json')
    if output.exists(): return json.loads(output.read_text())
    with (root/'operator.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        active = root/'operator-active.json'; service = Path.home()/'.config/systemd/user/factory-endpoint.service'
        previous, desired = trial/'baseline.previous.service', trial/'baseline.desired.service'
        intent = trial/'baseline.dispatch-intent.json'
        if observe_only and not intent.exists(): raise ValueError('observation requires original dispatch intent')
        if intent.exists() and not previous.exists(): raise ValueError('original dispatch lacks owned service checkpoint')
        if not previous.exists():
            if active.exists() or post(root, 'WorkUnit/' + unit + '/getState', {})['stage'] != 'unclaimed':
                raise ValueError('operator or original unit not idle')
            old = service.read_text()
            if '/releases/' + Path(config['release']).name + '/' not in old: raise ValueError('wrong deployed source')
            write_new(previous, old)
            write_new(desired, '\n'.join('Environment=FACTORY_RUNTIME_CONFIG=' + str(trial/'runtime.json')
                if line.startswith('Environment=FACTORY_RUNTIME_CONFIG=') else line for line in old.splitlines()) + '\n')
            write_new(active, {'unit': unit, 'owner': 'held-out-evaluation'})
        collected = trial/'evaluation.collected.json'
        if collected.exists():
            if active.exists():
                if json.loads(active.read_text())['unit'] != unit: raise ValueError('foreign operator owner')
                restore(root, trial, active, service)
            elif service.read_text() != previous.read_text():
                raise ValueError('unowned service binding differs from restored state')
            value = json.loads(collected.read_text())
            value['binding_restored'] = True; write_new(output, value); return value
        if (not active.exists() or json.loads(active.read_text())['unit'] != unit
                or service.read_text() not in (previous.read_text(), desired.read_text())):
            raise ValueError('binding ownership changed')
        if intent.exists():
            if not observe_only: raise ValueError('original dispatch already exists; use --observe-only')
            saved = json.loads(intent.read_text())
            value = collect(config, entry, runtime, saved['started_at_unix'],
                            json.loads((trial/'evaluation.observed.json').read_text())['finished']
                            if (trial/'evaluation.observed.json').exists() else time.time())
            write_new(collected, value); restore(root, trial, active, service)
            value['binding_restored'] = True; write_new(output, value); return value
        if observe_only: raise ValueError('observation requires original dispatch intent')
        service.write_text(desired.read_text()); command('systemctl', '--user', 'daemon-reload')
        command('systemctl', '--user', 'restart', 'factory-endpoint')
        for _ in range(30):
            try: post(root, 'WorkUnit/' + unit + '/getState', {}); break
            except OSError: time.sleep(1)
        else: raise ValueError('endpoint unavailable; retain binding')
        request = {'unitId': unit, 'target': 'http://127.0.0.1:1', 'scenarios': 'separate-held-out-evaluation',
                   'targetRepo': runtime['repo'], 'taskFile': runtime['task_file']}
        try:
            dispatch_once(intent, unit, request, lambda route, value: post(root, route, value))
            started = json.loads(intent.read_text())['started_at_unix']
            while time.time()-started < 240:
                state = post(root, 'WorkUnit/' + unit + '/getState', {})
                if state['stage'] in ('needs_validation', 'validated', 'failed'): break
                time.sleep(2)
        except OSError:
            write_new(trial/'evaluation.poll-failure.json', {'original_dispatch_retained': True,
                      'observed_at': time.time(), 'redispatch_allowed': False})
            _stop_original(root, unit, trial_binding(root, unit))
            raise
        finished = time.time()
        value = collect(config, entry, runtime, started, finished)
        write_new(collected, value); restore(root, trial, active, service)
        value['binding_restored'] = True; write_new(output, value)
        return value


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--config', required=True); parser.add_argument('--unit', required=True)
    parser.add_argument('--observe-only', action='store_true')
    args = parser.parse_args()
    result = run(config_file(args.config), args.unit, observe_only=args.observe_only)
    print(json.dumps({k: result[k] for k in ('unit', 'accepted', 'requests', 'accounting_complete', 'binding_restored')}), flush=True)


if __name__ == '__main__': main()
