"""An accepted validation response can be lost without rerunning the candidate."""
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SPINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPINE))
spec = importlib.util.spec_from_file_location('validation_session_test', SPINE / 'validation_session.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

KEY = b'private-test-validation-key-only!' * 2


def binding():
    return dict(unit_id='unit', attempt_id='unit:0', git_revision='a' * 40,
                baseline='b' * 40, artifact_digest='sha256:' + 'c' * 64,
                source_archive_digest='d' * 64, scenario_digest='e' * 64,
                build_manifest_digest='f' * 64)


def result(passed=True):
    b = binding()
    return dict(git_revision=b['git_revision'], artifact_digest=b['artifact_digest'],
                source_archive_digest=b['source_archive_digest'],
                scenario_digest=b['scenario_digest'], deployment_id='1' * 64,
                passed=passed, security={'verdict': 'pass', 'engines': {'sast': 'fixture'}},
                wall={'verdict': {'passed': passed, 'aggregate_pass_rate': 1 if passed else 0,
                                 'failure_message': '', 'per_scenario': {'private-case': passed}},
                      'receipt': {'tokens_in': 0, 'tokens_out': 0, 'requests': 3, 'retries': 0,
                                  'wall_clock_ms': 10, 'tier': 'unknown', 'pricing_mode': 'subscription',
                                  'cost_estimate_usd': None, 'ceiling_violation': ''}})


class Runtime:
    def __init__(self):
        self.state = {'stage': 'needs_validation', 'build': {
            'status': 'completed', 'attempt_id': 'unit:0', 'git_revision': 'a' * 40}}
        self.evaluations = self.submissions = 0
        self.lose_response = False

    def evaluate(self):
        self.evaluations += 1
        return result()

    def submit(self, envelope):
        self.submissions += 1
        payload = envelope['payload']
        assert hmac.compare_digest(envelope['signature'], hmac.new(KEY, payload.encode(), hashlib.sha256).hexdigest())
        self.state.update(stage='validated', validation=json.loads(payload))
        if self.lose_response:
            raise TimeoutError('accepted response lost')
        return True

    def run(self, folder, **kwargs):
        return m.validate_session(folder, binding(), lambda: self.state,
                                  self.evaluate, self.submit, KEY, **kwargs)


def test_accepted_response_loss_and_reobservation_never_revalidate(tmp_path):
    runtime = Runtime()
    runtime.lose_response = True
    first = runtime.run(tmp_path / 'session')
    assert first['accepted'] is True
    assert runtime.run(tmp_path / 'session', observe_only=True) == first
    assert runtime.evaluations == runtime.submissions == 1
    checks = json.loads((tmp_path / 'session/checks.json').read_text())
    canonical = json.dumps(checks, sort_keys=True, separators=(',', ':')).encode()
    assert first['evidence']['checks_digest'] == hashlib.sha256(canonical).hexdigest()
    assert first['evidence']['receipt']['usage']['requests'] == 3
    assert first['evidence']['receipt']['verdict']['per_scenario'] == {}


@pytest.mark.parametrize('changed', [
    {'attempt_id': 'unit:0:successor'}, {'git_revision': '0' * 40}, {'status': 'unknown'},
])
def test_stale_selected_build_rejects_before_candidate_effect(tmp_path, changed):
    runtime = Runtime()
    runtime.state['build'].update(changed)
    with pytest.raises(ValueError, match='selected build'):
        runtime.run(tmp_path / 'session')
    assert runtime.evaluations == runtime.submissions == 0


def test_failed_wall_is_retained_but_cannot_mint_validation(tmp_path):
    runtime = Runtime()
    runtime.evaluate = lambda: result(False)
    first = runtime.run(tmp_path / 'session')
    assert first['accepted'] is False
    assert not (tmp_path / 'session/envelope.json').exists()
    runtime.evaluate = lambda: pytest.fail('rejected original must not be rerun')
    assert runtime.run(tmp_path / 'session') == first
    assert runtime.submissions == 0


def test_interrupted_evaluation_cannot_be_recreated(tmp_path):
    runtime = Runtime()
    def interrupt():
        raise RuntimeError('observer interrupted')
    runtime.evaluate = interrupt
    with pytest.raises(RuntimeError):
        runtime.run(tmp_path / 'session')
    runtime.evaluate = lambda: pytest.fail('must preserve original uncertain evaluation')
    with pytest.raises(ValueError, match='original evaluation'):
        runtime.run(tmp_path / 'session')


def test_changed_frozen_input_or_signed_envelope_cannot_reuse_acceptance(tmp_path):
    runtime = Runtime()
    runtime.run(tmp_path / 'session')
    changed = {**binding(), 'scenario_digest': '0' * 64}
    with pytest.raises(ValueError, match='binding'):
        m.validate_session(tmp_path / 'session', changed, lambda: runtime.state,
                           runtime.evaluate, runtime.submit, KEY)
    path = tmp_path / 'session/envelope.json'
    envelope = json.loads(path.read_text())
    envelope['payload'] = envelope['payload'].replace('fixture', 'forged')
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match='signature'):
        runtime.run(tmp_path / 'session', observe_only=True)
    assert runtime.submissions == 1


def test_unknown_submission_is_observed_without_resend(tmp_path):
    runtime = Runtime()
    def uncertain(envelope):
        runtime.submissions += 1
        raise TimeoutError('acceptance outcome unknown')
    runtime.submit = uncertain
    with pytest.raises(ValueError, match='acceptance unresolved'):
        runtime.run(tmp_path / 'session')
    with pytest.raises(ValueError, match='acceptance unresolved'):
        runtime.run(tmp_path / 'session', observe_only=True)
    assert runtime.evaluations == runtime.submissions == 1


def test_observe_only_cannot_start_fresh_validation(tmp_path):
    runtime = Runtime()
    with pytest.raises(ValueError, match='no original validation'):
        runtime.run(tmp_path / 'session', observe_only=True)
    assert runtime.evaluations == runtime.submissions == 0


def test_incomplete_usage_cannot_be_signed_as_a_passing_receipt(tmp_path):
    runtime = Runtime()
    broken = result()
    del broken['wall']['receipt']['tokens_out']
    runtime.evaluate = lambda: broken
    with pytest.raises(ValueError, match='wall usage receipt'):
        runtime.run(tmp_path / 'session')
    assert runtime.submissions == 0
    assert not (tmp_path / 'session/envelope.json').exists()
