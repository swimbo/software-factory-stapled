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


class Docker:
    def __init__(self, *, lose_start=False, lose_create=False, substitute=False):
        self.calls = []
        self.state = None
        self.lose_start, self.lose_create, self.substitute = lose_start, lose_create, substitute

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
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
