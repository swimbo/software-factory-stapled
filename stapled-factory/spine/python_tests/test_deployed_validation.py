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
