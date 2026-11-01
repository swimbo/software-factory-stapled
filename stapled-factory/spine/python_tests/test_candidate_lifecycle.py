"""A lost Docker response must never orphan or replace a validation target."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

spec = importlib.util.spec_from_file_location(
    'candidate_lifecycle_test', Path(__file__).resolve().parents[1] / 'prebuilt_validation.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(autouse=True)
def linux_clock(monkeypatch):
    monkeypatch.setattr(m, '_boot_id', lambda: 'test-boot', raising=False)
    monkeypatch.setattr(m.time, 'monotonic', lambda: 100.0)


class Docker:
    def __init__(self, *, lose_start=False, lose_create=False, substitute=False,
                 deadline_active=True, lose_deadline=False, deadline_substitute=False,
                 due_seconds=340.0, fire_on_start=False):
        self.calls = []
        self.state = None
        self.lose_start, self.lose_create, self.substitute = lose_start, lose_create, substitute
        self.deadline_active, self.lose_deadline = deadline_active, lose_deadline
        self.deadline_substitute = deadline_substitute
        self.deadline = None
        self.due_seconds, self.fire_on_start = due_seconds, fire_on_start

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == 'systemd-run':
            self.deadline = next(a.split('=', 1)[1] for a in args if a.startswith('--unit='))
            if self.lose_deadline:
                raise subprocess.TimeoutExpired(args, 30)
            return ''
        if args[0] == 'systemctl':
            if args[2] == 'stop':
                return ''
            if '--property=Restart' in args:
                return 'Restart=always\nRestartUSec=1s\nStartLimitIntervalUSec=0\nType=exec'
            if '--property=ExecStart' in args:
                cid = 'f' * 64 if self.deadline_substitute else self.state['Id']
                return '{ path=/bin/sh ; argv[]=/bin/sh -c ' + m._deadline_enforcer(cid) + ' ; ignore_errors=no ; }'
            return 'ActiveState=' + ('active' if self.deadline_active else 'inactive') + '\nTriggers=' + self.deadline + '.service'
        if args[0] == 'busctl':
            if args[-1] == 'NextElapseUSecMonotonic':
                return 't ' + str(int(self.due_seconds * 1000000))
            if args[-1] == 'AccuracyUSec':
                return 't 1000000'
            if args[-1] == 'WakeSystem':
                return 'b false'
            raise AssertionError(args)
        op = args[1]
        if op == 'create':
            name = args[args.index('--name') + 1]
            label = args[args.index('--label') + 1].split('=', 1)
            self.state = {'Id': 'a' * 64, 'Image': args[-1], 'Name': '/' + name,
                          'Config': {'Labels': {label[0]: label[1]}},
                          'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True},
                          'State': {'Running': False, 'Restarting': False}, 'Mounts': []}
            if self.lose_create:
                raise subprocess.TimeoutExpired(args, 30)
            return self.state['Id']
        if op == 'inspect':
            if self.substitute:
                return json.dumps([{**self.state, 'Image': 'sha256:' + 'f' * 64}])
            return json.dumps([self.state])
        if op == 'start':
            self.state['State']['Running'] = True
            if self.fire_on_start:
                self.deadline_active = False
                self.due_seconds = 0
            if self.lose_start:
                raise subprocess.TimeoutExpired(args, 30)
            return args[-1]
        if op == 'stop':
            self.state['State']['Running'] = False
            return args[-1]
        raise AssertionError(args)


@pytest.mark.parametrize('lost', ['start', 'create'])
def test_lost_response_retains_and_stops_exact_owned_target(tmp_path, monkeypatch, lost):
    docker = Docker(lose_start=lost == 'start', lose_create=lost == 'create')
    monkeypatch.setattr(m, 'command', docker)
    with pytest.raises(subprocess.TimeoutExpired):
        with m.candidate_lifecycle(tmp_path / 'evidence', 'sha256:' + 'b' * 64):
            pytest.fail('uncertain start/create must not evaluate')
    owned = json.loads((tmp_path / 'evidence/container.json').read_text())
    assert owned['container_id'] == 'a' * 64
    assert not docker.state['State']['Running']
    assert (tmp_path / 'evidence/stopped.json').is_file()
    assert sum(c[1] == 'create' for c in docker.calls) == 1
    assert not any(c[1] == 'rm' for c in docker.calls)


def test_deadline_is_verified_before_original_start(tmp_path, monkeypatch):
    docker = Docker(deadline_active=False)
    monkeypatch.setattr(m, 'command', docker)
    with pytest.raises(ValueError, match='deadline'):
        with m.candidate_lifecycle(tmp_path / 'evidence', 'sha256:' + 'b' * 64):
            pytest.fail('unarmed target must not execute')
    assert not any(c[:2] == ('docker', 'start') for c in docker.calls)
    assert (tmp_path / 'evidence/deadline.json').is_file()


def test_lost_deadline_arm_response_never_starts_or_rearms(tmp_path, monkeypatch):
    docker = Docker(lose_deadline=True)
    monkeypatch.setattr(m, 'command', docker)
    with pytest.raises(subprocess.TimeoutExpired):
        with m.candidate_lifecycle(tmp_path / 'evidence', 'sha256:' + 'b' * 64):
            pytest.fail('uncertain deadline must not start target')
    assert not any(c[:2] == ('docker', 'start') for c in docker.calls)
    result = m.observe_candidate(tmp_path / 'evidence', contain=True)
    assert result['stopped'] is True
    assert sum(c[0] == 'systemd-run' for c in docker.calls) == 1


def test_deadline_bound_to_original_container_precedes_start(tmp_path, monkeypatch):
    docker = Docker()
    monkeypatch.setattr(m, 'command', docker)
    evidence = tmp_path / 'evidence'
    with m.candidate_lifecycle(evidence, 'sha256:' + 'b' * 64):
        record = json.loads((evidence / 'deadline.json').read_text())
        assert record['container_id'] == 'a' * 64
        arm = next(i for i, c in enumerate(docker.calls) if c[0] == 'systemd-run')
        start = next(i for i, c in enumerate(docker.calls) if c[:2] == ('docker', 'start'))
        assert arm < start
        assert docker.calls[arm][-3:] == ('/bin/sh', '-c', m._deadline_enforcer('a' * 64))
        assert record['seconds'] <= 300


def test_crashed_observer_only_contains_original_without_create_or_start(tmp_path, monkeypatch):
    docker = Docker()
    monkeypatch.setattr(m, 'command', docker)
    evidence = tmp_path / 'evidence'
    observer = m.candidate_lifecycle(evidence, 'sha256:' + 'b' * 64)
    observer.__enter__()
    # No observer exit/cleanup ran: recover through only the durable records.
    try:
        assert docker.state['State']['Running']
        prior = len(docker.calls)
        result = m.observe_candidate(evidence, contain=True)
        assert result['container_id'] == 'a' * 64 and result['stopped']
        assert not any(c[:2] in (('docker', 'create'), ('docker', 'start')) or c[0] == 'systemd-run'
                       for c in docker.calls[prior:])
    finally:
        observer.__exit__(None, None, None)


def test_active_deadline_for_other_container_does_not_authorize_start(tmp_path, monkeypatch):
    docker = Docker(deadline_substitute=True)
    monkeypatch.setattr(m, 'command', docker)
    with pytest.raises(ValueError, match='deadline'):
        with m.candidate_lifecycle(tmp_path / 'evidence', 'sha256:' + 'b' * 64):
            pytest.fail('timer must target the original container')
    assert not any(c[:2] == ('docker', 'start') for c in docker.calls)


def test_expired_deadline_cannot_start_or_be_extended(tmp_path, monkeypatch):
    docker = Docker()
    monkeypatch.setattr(m, 'command', docker)
    ticks = iter([10.0, 251.0])
    monkeypatch.setattr(m.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(ValueError, match='expired'):
        with m.candidate_lifecycle(tmp_path / 'evidence', 'sha256:' + 'b' * 64):
            pytest.fail('expired original must not start')
    assert sum(c[0] == 'systemd-run' for c in docker.calls) == 1
    assert not any(c[:2] == ('docker', 'start') for c in docker.calls)


def test_active_but_misscheduled_timer_cannot_start_candidate(tmp_path, monkeypatch):
    docker = Docker(due_seconds=700.0)
    monkeypatch.setattr(m, 'command', docker)
    with pytest.raises(ValueError, match='deadline'):
        with m.candidate_lifecycle(tmp_path / 'evidence', 'sha256:' + 'b' * 64):
            pytest.fail('late timer is not a bounded deadline')
    assert not any(c[:2] == ('docker', 'start') for c in docker.calls)


def test_deadline_fired_during_start_contains_original_without_evaluation(tmp_path, monkeypatch):
    docker = Docker(fire_on_start=True)
    monkeypatch.setattr(m, 'command', docker)
    with pytest.raises(ValueError, match='deadline'):
        with m.candidate_lifecycle(tmp_path / 'evidence', 'sha256:' + 'b' * 64):
            pytest.fail('expired start must not evaluate')
    assert not docker.state['State']['Running']
    assert (tmp_path / 'evidence/stopped.json').is_file()
    assert sum(c[:2] == ('docker', 'create') for c in docker.calls) == 1
    assert sum(c[:2] == ('docker', 'start') for c in docker.calls) == 1
    assert sum(c[0] == 'systemd-run' for c in docker.calls) == 1


def test_reopening_original_intent_never_creates_or_starts_again(tmp_path, monkeypatch):
    docker = Docker()
    monkeypatch.setattr(m, 'command', docker)
    evidence = tmp_path / 'evidence'
    with m.candidate_lifecycle(evidence, 'sha256:' + 'b' * 64) as cid:
        assert cid == 'a' * 64
        assert json.loads((evidence / 'container.json').read_text())['container_id'] == cid
    prior = list(docker.calls)
    with pytest.raises(ValueError, match='original validation'):
        with m.candidate_lifecycle(evidence, 'sha256:' + 'b' * 64):
            pytest.fail('must not replay validation')
    assert docker.calls == prior


def test_substituted_identity_is_never_stopped_or_used(tmp_path, monkeypatch):
    docker = Docker(substitute=True)
    monkeypatch.setattr(m, 'command', docker)
    with pytest.raises(ValueError, match='identity'):
        with m.candidate_lifecycle(tmp_path / 'evidence', 'sha256:' + 'b' * 64):
            pytest.fail('substituted artifact must not execute')
    assert not any(c[1] in ('start', 'stop', 'rm') for c in docker.calls)


def test_target_is_retained_after_checks_fail(tmp_path, monkeypatch):
    docker = Docker()
    monkeypatch.setattr(m, 'command', docker)
    with pytest.raises(RuntimeError, match='wall failed'):
        with m.candidate_lifecycle(tmp_path / 'evidence', 'sha256:' + 'b' * 64):
            raise RuntimeError('wall failed')
    assert not docker.state['State']['Running']
    assert (tmp_path / 'evidence/container.json').is_file()
    assert not any(c[1] == 'rm' for c in docker.calls)


def test_closure_cannot_stop_a_service_substituted_in_private_deadline(tmp_path, monkeypatch):
    docker = Docker()
    monkeypatch.setattr(m, 'command', docker)
    evidence = tmp_path / 'evidence'
    with pytest.raises(ValueError, match='deadline identity'):
        with m.candidate_lifecycle(evidence, 'sha256:' + 'b' * 64):
            path = evidence / 'deadline.json'
            value = json.loads(path.read_text())
            value['unit'] = 'factory-endpoint'
            path.write_text(json.dumps(value))
    assert not docker.state['State']['Running']
    assert not any(c[0] == 'systemctl' and 'stop' in c and
                   any(str(arg).startswith('factory-endpoint') for arg in c) for c in docker.calls)


def test_uncertain_start_keeps_post_expiry_enforcer(tmp_path, monkeypatch):
    docker = Docker(lose_start=True)
    monkeypatch.setattr(m, 'command', docker)
    evidence = tmp_path / 'evidence'
    with pytest.raises(subprocess.TimeoutExpired):
        with m.candidate_lifecycle(evidence, 'sha256:' + 'b' * 64):
            pass
    arm = next(c for c in docker.calls if c[0] == 'systemd-run')
    assert '--property=Restart=always' in arm
    assert '--property=StartLimitIntervalSec=0' in arm
    assert 'while :' in arm[-1]
    assert not any(c[:3] == ('systemctl', '--user', 'stop') for c in docker.calls)
    assert not (evidence / 'start-settled.json').exists()


def test_acknowledged_start_and_stopped_original_close_enforcer(tmp_path, monkeypatch):
    docker = Docker()
    monkeypatch.setattr(m, 'command', docker)
    evidence = tmp_path / 'evidence'
    with m.candidate_lifecycle(evidence, 'sha256:' + 'b' * 64):
        assert (evidence / 'start-settled.json').is_file()
    assert (evidence / 'closed.json').is_file()
    stops = [c for c in docker.calls if c[:3] == ('systemctl', '--user', 'stop')]
    assert stops[0][-1].endswith('.timer')
    assert stops[1][-1].endswith('.service')


@pytest.mark.parametrize('load,active,accepted', [
    ('not-found', 'inactive', True),
    ('loaded', 'active', False),
    ('loaded', 'inactive', False),
    ('not-found', 'active', False),
])
def test_retirement_accepts_only_verified_unloaded_unit(tmp_path, monkeypatch, load, active, accepted):
    docker = Docker()
    def system(*args, **kwargs):
        if args[:3] == ('systemctl', '--user', 'stop') and args[-1].endswith('.service'):
            raise subprocess.CalledProcessError(5, args)
        if args[:3] == ('systemctl', '--user', 'show') and '--property=LoadState' in args:
            return 'LoadState=' + load + '\nActiveState=' + active
        return docker(*args, **kwargs)
    monkeypatch.setattr(m, 'command', system)
    evidence = tmp_path / 'evidence'
    def run():
        with m.candidate_lifecycle(evidence, 'sha256:' + 'b' * 64):
            pass
    if accepted:
        run()
        assert m.observe_candidate(evidence, contain=True)['stopped']
    else:
        with pytest.raises(subprocess.CalledProcessError):
            run()
    assert not docker.state['State']['Running']
    assert (evidence / 'closed.json').is_file()
    assert sum(c[:2] == ('docker', 'start') for c in docker.calls) == 1


def test_late_start_after_expiry_is_repeatedly_stopped_without_observer(tmp_path, monkeypatch):
    """Execute the production loop with a file-backed fake Docker executable.

    Timer scheduling and timeout's Linux executable remain live-proof work;
    this exercises the actual shell loop after its first stopped-target kill.
    """
    import time
    import shlex
    docker = Docker(lose_start=True)
    monkeypatch.setattr(m, 'command', docker)

    def unavailable(*args):
        raise RuntimeError('observer cleanup unavailable')

    monkeypatch.setattr(m, '_contain_candidate', unavailable)
    with pytest.raises(RuntimeError, match='cleanup unavailable'):
        with m.candidate_lifecycle(tmp_path / 'evidence', 'sha256:' + 'b' * 64):
            pass
    arm = next(c for c in docker.calls if c[0] == 'systemd-run')
    script = arm[-1]
    assert '/usr/bin/timeout --kill-after=1s 1s /usr/bin/docker kill --signal KILL ' + 'a' * 64 in script
    state, attempts = tmp_path / 'state', tmp_path / 'attempts'
    state.write_text('stopped')
    fake = tmp_path / 'docker'
    fake.write_text('#!/bin/sh\n'
                    + 'echo attempt >> ' + shlex.quote(str(attempts)) + '\n'
                    + 'echo stopped > ' + shlex.quote(str(state)) + '\n'
                    + 'exit 1\n')  # Failure must not spend the fence.
    fake.chmod(0o700)
    script = script.replace('/usr/bin/timeout --kill-after=1s 1s /usr/bin/docker', shlex.quote(str(fake)))
    script = script.replace('/usr/bin/sleep 1', '/bin/sleep 0.01')
    enforcer = subprocess.Popen(['/bin/sh', '-c', script])

    def wait_for(predicate):
        until = time.perf_counter() + 2
        while not predicate():
            assert time.perf_counter() < until, 'independent enforcement did not recur'
            time.sleep(.005)

    try:
        wait_for(lambda: attempts.exists() and len(attempts.read_text().splitlines()) >= 2)
        for _ in range(2):
            prior = len(attempts.read_text().splitlines())
            state.write_text('running')  # Daemon acceptance arrives after expiry.
            wait_for(lambda: len(attempts.read_text().splitlines()) > prior and
                     state.read_text().strip() == 'stopped')
            assert enforcer.poll() is None
    finally:
        enforcer.terminate()
        enforcer.wait(timeout=2)
    assert not (tmp_path / 'evidence/closed.json').exists()
