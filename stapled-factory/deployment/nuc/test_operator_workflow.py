"""Operator workflow scope, uncertain-send behavior and publication boundaries."""
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

BASE = Path(__file__).resolve().parents[2] / 'operator'
def load(name):
    spec = importlib.util.spec_from_file_location(name, BASE / (name + '.py'))
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

cli = load('cli')
nuc = load('nuc')


def spec():
    return {'unit': 'sample-1', 'repo': '/repo', 'baseline': 'main', 'task': 'Fix docs',
            'acceptance': ['Current policy is accurate'], 'allowed_paths': ['README.md'],
            'check_script': '/private/check.py'}


@pytest.mark.parametrize('path', ['/etc/passwd', '../README', 'docs/../../x', '.git/config', 'docs//x'])
def test_task_cannot_select_unsafe_paths(path):
    data = spec();data['allowed_paths'] = [path]
    with pytest.raises(ValueError):cli.validate_spec(data)


def test_task_requires_acceptance_and_rejects_unknown_authority():
    data = spec();data['acceptance'] = []
    with pytest.raises(ValueError):cli.validate_spec(data)
    data = spec();data['merge'] = True
    with pytest.raises(ValueError):cli.validate_spec(data)
    assert cli.validate_spec(spec())['unit'] == 'sample-1'


def test_completed_run_is_observed_without_rebinding_or_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    approval = tmp_path / 'approvals/unit-1';approval.mkdir(parents=True)
    result = {'binding_restored': True, 'revision': 'a' * 40, 'state': {'stage': 'needs_validation'}}
    (approval / 'result.json').write_text(json.dumps(result))
    monkeypatch.setattr(nuc, 'post', lambda *a: pytest.fail('unexpected paid dispatch'))
    monkeypatch.setattr(nuc, 'command', lambda *a: pytest.fail('unexpected service/container mutation'))
    assert nuc.main('run', {'unit': 'unit-1'}) == result


def test_run_does_not_collect_uncertain_result(tmp_path, monkeypatch):
    (tmp_path / 'operator.json').write_text(json.dumps({'unit': 'unit-1'}))
    monkeypatch.setattr(cli, 'remote', lambda *a: (_ for _ in ()).throw(ValueError('uncertain')))
    monkeypatch.setattr(cli, 'copy', lambda *a, **k: pytest.fail('must not collect or publish unknown result'))
    with pytest.raises(ValueError, match='uncertain'):cli.run_task(tmp_path)
    assert not (tmp_path / 'candidate').exists()


def test_publish_rejects_changed_candidate_before_check_or_push(tmp_path, monkeypatch):
    (tmp_path / 'operator.json').write_text('{}')
    (tmp_path / 'result.json').write_text(json.dumps({'revision': 'a' * 40}))
    monkeypatch.setattr(cli, 'command', lambda *a, **k: 'b' * 40)
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **k: pytest.fail('unreviewed code must not execute'))
    with pytest.raises(ValueError, match='changed'):cli.publish(tmp_path)


def test_other_uncertain_unit_blocks_endpoint_rebinding(tmp_path, monkeypatch):
    monkeypatch.setattr(nuc, 'ROOT', tmp_path)
    (tmp_path / 'operator-active.json').write_text(json.dumps({'unit': 'other'}))
    monkeypatch.setattr(nuc, 'command', lambda *a: pytest.fail('must not mutate endpoint'))
    with pytest.raises(ValueError, match='another task'):nuc.main('run', {'unit': 'unit-1'})
