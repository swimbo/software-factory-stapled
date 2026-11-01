"""Operator-owned validation journal; never dispatches or resumes a worker."""
import fcntl
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re

from prebuilt_validation import record


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def read(path):
    st = path.lstat()
    if path.is_symlink() or not path.is_file() or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('validation journal must be an owner-only regular file')
    return json.loads(path.read_text())


def validate_binding(binding):
    fields = {'unit_id', 'attempt_id', 'git_revision', 'baseline', 'artifact_digest',
              'source_archive_digest', 'scenario_digest', 'build_manifest_digest'}
    if not isinstance(binding, dict) or set(binding) != fields:
        raise ValueError('complete immutable validation binding required')
    if not isinstance(binding['unit_id'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', binding['unit_id']):
        raise ValueError('invalid validation unit')
    if binding['attempt_id'] not in (binding['unit_id'] + ':0', binding['unit_id'] + ':0:successor'):
        raise ValueError('invalid selected build attempt')
    for name, pattern in [('git_revision', r'[a-f0-9]{40}'), ('baseline', r'[a-f0-9]{40}'),
                          ('artifact_digest', r'sha256:[a-f0-9]{64}'),
                          *[(name, r'[a-f0-9]{64}') for name in
                            ('source_archive_digest', 'scenario_digest', 'build_manifest_digest')]]:
        if not isinstance(binding[name], str) or not re.fullmatch(pattern, binding[name]):
            raise ValueError('invalid immutable validation binding')


def selected(state, binding):
    build = state.get('successor') or state.get('build') or {}
    if (build.get('status') != 'completed' or build.get('attempt_id') != binding['attempt_id'] or
            build.get('git_revision') != binding['git_revision'] or
            state.get('stage') not in ('needs_validation', 'validated', 'gated', 'merged', 'deployed')):
        raise ValueError('selected build differs from validation binding')


def evidence(result, binding):
    for name in ('git_revision', 'artifact_digest', 'source_archive_digest', 'scenario_digest'):
        if result.get(name) != binding[name]:
            raise ValueError('candidate checks differ from immutable binding')
    if not isinstance(result.get('deployment_id'), str) or not re.fullmatch(r'[a-f0-9]{64}', result['deployment_id']):
        raise ValueError('original disposable target identity required')
    passed = result.get('security', {}).get('verdict') == 'pass' and result.get('wall', {}).get('verdict', {}).get('passed') is True
    if type(result.get('passed')) is not bool or result['passed'] != passed:
        raise ValueError('candidate verdict differs from security/wall checks')
    proof = dict(unit_id=binding['unit_id'], attempt_id=binding['attempt_id'],
                git_revision=binding['git_revision'], artifact_digest=binding['artifact_digest'],
                deployment_id=result['deployment_id'], scenario_digest=binding['scenario_digest'],
                source_archive_digest=binding['source_archive_digest'],
                checks_digest=hashlib.sha256(canonical(result).encode()).hexdigest(),
                scope='disposable-candidate', security_engines=result['security']['engines'], passed=passed)
    if passed:
        verdict = result['wall']['verdict']
        rate = verdict.get('aggregate_pass_rate')
        usage = result['wall'].get('receipt')
        if (type(rate) not in (int, float) or not math.isfinite(rate) or not .9 <= rate <= 1 or
                not isinstance(verdict.get('failure_message'), str) or not isinstance(usage, dict)):
            raise ValueError('complete passing wall receipt required')
        if any(type(usage.get(name)) is not int or not 0 <= usage[name] <= 2**53-1
               for name in ('tokens_in', 'tokens_out', 'requests', 'retries', 'wall_clock_ms')):
            raise ValueError('complete wall usage receipt required')
        scenarios = verdict.get('per_scenario')
        if (not isinstance(scenarios, dict) or not scenarios or
                any(not isinstance(name, str) or not name or type(ok) is not bool for name, ok in scenarios.items()) or
                usage['requests'] < 3 * len(scenarios)):
            raise ValueError('passing wall receipt must cover every scenario run')
        cost = usage.get('cost_estimate_usd')
        if (not isinstance(usage.get('tier'), str) or
                usage.get('pricing_mode') not in ('subscription', 'metered') or
                usage.get('ceiling_violation') != '' or 'cost_estimate_usd' not in usage or
                cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0)):
            raise ValueError('invalid wall usage receipt')
        # Full per-scenario results remain in private checks.json. The signed
        # aggregate receipt binds their digest without exporting holdout names
        # or feedback from scenarios below the aggregate passing threshold.
        proof['receipt'] = dict(git_revision=binding['git_revision'], usage=usage,
            verdict=dict(passed=True, aggregate_pass_rate=rate,
                         failure_message='', per_scenario={}))
    return proof


def validate_session(directory, binding, read_state, evaluate, submit, key, *, observe_only=False, resume_only=False, resume_unstarted=False):
    """Persist checks and envelope before one signed acceptance submission.

    Readback can prove a lost response succeeded. An unresolved submission or
    interrupted evaluation stays attached to its original intent; observation
    cannot create another target, submit another acceptance, or invoke a model.
    """
    if observe_only and resume_only or resume_unstarted and not resume_only:
        raise ValueError('observation and continuation are mutually exclusive')
    validate_binding(binding)
    if not isinstance(key, bytes) or not 32 <= len(key) <= 4096:
        raise ValueError('bounded validation authority key required')
    root = Path(directory)
    root.mkdir(mode=0o700, exist_ok=True)
    st = root.lstat()
    if root.is_symlink() or not root.is_dir() or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('owner-only validation journal directory required')
    fd = os.open(root / 'lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        intent = root / 'intent.json'
        original = intent.exists()
        if original and read(intent) != binding:
            raise ValueError('original validation binding changed')
        state = read_state()
        selected(state, binding)
        if not original:
            if observe_only or resume_only and not resume_unstarted:
                raise ValueError('no original validation to observe')
            if resume_unstarted and any(os.path.lexists(root / name) for name in
                                        ('target', 'checks.json', 'envelope.json', 'submission.json', 'accepted.json')):
                raise ValueError('original target exists without journal; do not recreate')
            if state['stage'] != 'needs_validation' or state.get('validation'):
                raise ValueError('selected build already has different validation authority')
            record(intent, binding)
        checks_path = root / 'checks.json'
        if not checks_path.exists():
            retained = root / 'target' / 'result.json'
            if resume_only and original and retained.exists():
                result = read(retained)
                evidence(result, binding)
                record(checks_path, result)
            elif resume_unstarted and not any(os.path.lexists(root / name) for name in
                                               ('target', 'envelope.json', 'submission.json', 'accepted.json')):
                result = evaluate()
                evidence(result, binding)
                record(checks_path, result)
            elif original or observe_only or resume_only and not resume_unstarted:
                raise ValueError('original evaluation incomplete; inspect retained target, never recreate')
            else:
                result = evaluate()
                evidence(result, binding)
                record(checks_path, result)
        result = read(checks_path)
        proof = evidence(result, binding)
        if not proof['passed']:
            return {'accepted': False, 'reason': 'candidate validation rejected',
                    'checks_digest': proof['checks_digest']}
        payload = canonical(proof)
        expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
        envelope_path = root / 'envelope.json'
        if envelope_path.exists():
            envelope = read(envelope_path)
            if (envelope.get('payload') != payload or not isinstance(envelope.get('signature'), str) or
                    not hmac.compare_digest(envelope['signature'], expected)):
                raise ValueError('stored validation signature or checks changed')
        else:
            if observe_only:
                raise ValueError('original validation has no signed envelope; explicit continuation required')
            envelope = dict(payload=payload, signature=expected)
            record(envelope_path, envelope)

        def accepted(current):
            selected(current, binding)
            return (current.get('stage') in ('validated', 'gated', 'merged', 'deployed') and
                    current.get('validation') == proof and current.get('receipt') == proof['receipt'])

        submission = root / 'submission.json'
        envelope_digest = hashlib.sha256(canonical(envelope).encode()).hexdigest()
        if submission.exists() and read(submission) != {'envelope_digest': envelope_digest}:
            raise ValueError('original signed submission binding changed')
        if not accepted(state):
            if submission.exists() or observe_only:
                raise ValueError('original acceptance unresolved; observe the original submission, never resend')
            if state['stage'] != 'needs_validation' or state.get('validation'):
                raise ValueError('selected build no longer accepts this validation')
            record(submission, {'envelope_digest': envelope_digest})
            try:
                submit(envelope)
            except (OSError, TimeoutError):
                pass  # The remote may have committed; only exact readback proves it.
            state = read_state()
            if not accepted(state):
                raise ValueError('original acceptance unresolved; retain submission and inspect durable state')
        receipt = {'accepted': True, 'evidence': proof, 'envelope_digest': envelope_digest}
        receipt_path = root / 'accepted.json'
        if receipt_path.exists():
            if read(receipt_path) != receipt:
                raise ValueError('accepted validation receipt changed')
        else:
            record(receipt_path, receipt)
        return receipt
