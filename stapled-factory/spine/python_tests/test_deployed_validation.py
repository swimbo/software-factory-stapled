import importlib.util
from pathlib import Path
from dataclasses import replace
import sys
import pytest

spec = importlib.util.spec_from_file_location('deployed_validation', Path(__file__).resolve().parents[1] / 'deployed_validation.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def test_provider_binding_and_negative_controls(tmp_path):
    scenarios = tmp_path / 'scenarios'
    scenarios.write_text('{}')
    bound = m.Deployment('project', 'environment', 'service', 'deployment', 'a'*40, 'sha256:'+'b'*64, 'https://trusted.invalid', 'SUCCESS')
    class Provider:
        def snapshot(self, *args):
            return bound
    security = lambda revision: {'verdict': 'pass'}
    wall = lambda target, scenarios, runs: {'verdict': {'passed': True}}
    assert m.validate_deployed(Provider(), bound, scenarios, security, wall)['passed']
    for field, value in [('target', 'https://attacker.invalid'), ('artifact', 'sha256:'+'c'*64), ('revision', 'd'*40), ('deployment', 'replacement')]:
        class Changed:
            def snapshot(self, *args):
                return replace(bound, **{field: value})
        with pytest.raises(ValueError, match='binding mismatch'):
            m.validate_deployed(Changed(), bound, scenarios, security, wall)
    class Replaced:
        calls = 0
        def snapshot(self, *args):
            self.calls += 1
            return bound if self.calls < 3 else replace(bound, deployment='replacement')
    with pytest.raises(ValueError, match='binding mismatch'):
        m.validate_deployed(Replaced(), bound, scenarios, security, wall)
    assert not m.validate_deployed(Provider(), bound, scenarios, lambda revision: {'verdict': 'fail'}, wall)['passed']
    assert not m.validate_deployed(Provider(), bound, scenarios, security, lambda *a, **k: {'verdict': {'passed': False}})['passed']


def railway_state():
    deployment = {'id': 'deploy', 'status': 'SUCCESS', 'deploymentStopped': False,
                  'instances': [{'id': 'instance', 'status': 'RUNNING'}],
                  'meta': {'imageDigest': 'sha256:'+'b'*64}}
    return {'id': 'project', 'environments': {'edges': [{'node': {'id': 'environment',
        'serviceInstances': {'edges': [{'node': {'serviceId': 'service',
          'activeDeployments': [deployment], 'latestDeployment': {'id': 'deploy'},
          'domains': {'serviceDomains': [{'domain': 'candidate.up.railway.app'}], 'customDomains': []}}}]}}}]}}


def instance(state):
    return state['environments']['edges'][0]['node']['serviceInstances']['edges'][0]['node']


def test_railway_authenticated_snapshot_and_replacements():
    import json
    state = railway_state()
    calls = []
    provider = m.RailwayProvider({'sha256:'+'b'*64: 'a'*40}, lambda args: (calls.append(args), json.dumps(state))[1])
    result = provider.snapshot('project', 'environment', 'service')
    assert result.target == 'https://candidate.up.railway.app'
    assert result.revision == 'a'*40
    assert calls == [['railway', 'status', '--project', 'project', '--environment', 'environment', '--json']]
    mutations = [
        lambda i: i['latestDeployment'].update(id='replacement'),
        lambda i: i['activeDeployments'][0].update(deploymentStopped=True),
        lambda i: i['activeDeployments'].append(dict(i['activeDeployments'][0])),
        lambda i: i['activeDeployments'][0]['instances'][0].update(status='STOPPED'),
        lambda i: i['activeDeployments'][0].update(instances=[]),
        lambda i: i['activeDeployments'][0]['meta'].update(imageDigest='sha256:'+'c'*64),
        lambda i: i['domains'].update(serviceDomains=[]),
        lambda i: i['domains']['serviceDomains'].append({'domain': 'other.up.railway.app'}),
        lambda i: i['domains'].update(customDomains=[{'domain': 'caller.invalid'}]),
        lambda i: i['domains']['serviceDomains'][0].update(domain='attacker.invalid'),
    ]
    for mutate in mutations:
        state = railway_state()
        mutate(instance(state))
        with pytest.raises(ValueError):
            provider.snapshot('project', 'environment', 'service')


def test_default_railway_runner_uses_account_auth_without_token_override(monkeypatch):
    import subprocess
    import json
    monkeypatch.setenv('RAILWAY_TOKEN', 'stale-token')
    monkeypatch.setenv('RAILWAY_API_TOKEN', 'stale-api-token')
    monkeypatch.setenv('FACTORY_KEEP_TEST', 'kept')
    def run(args, **kwargs):
        assert kwargs['text'] is True
        assert 'RAILWAY_TOKEN' not in kwargs['env']
        assert 'RAILWAY_API_TOKEN' not in kwargs['env']
        assert kwargs['env']['FACTORY_KEEP_TEST'] == 'kept'
        return json.dumps(railway_state())
    monkeypatch.setattr(subprocess, 'check_output', run)
    assert m.RailwayProvider({'sha256:'+'b'*64: 'a'*40}).snapshot('project', 'environment', 'service').deployment == 'deploy'
