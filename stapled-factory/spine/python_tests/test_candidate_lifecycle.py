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
            if '--property=ExecStart' in args:
                cid = 'f' * 64 if self.deadline_substitute else self.state['Id']
                return '{ path=/usr/bin/docker ; argv[]=/usr/bin/docker kill --signal KILL ' + cid + ' ; ignore_errors=no ; }'
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
        assert docker.calls[arm][-5:] == ('/usr/bin/docker', 'kill', '--signal', 'KILL', 'a' * 64)
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
