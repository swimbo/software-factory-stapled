"""An accepted validation response can be lost without rerunning the candidate."""
import hashlib
from dataclasses import asdict
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
sys.path.insert(0, str(SPINE.parent / 'wall/evaluator'))
from gate import ScenarioResult, gate

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


def test_v2_proof_binds_manifest_and_preserves_legacy_contract():
    original = binding()
    assert 'build_manifest_digest' not in m.evidence(result(), original)
    current = dict(original, receipt_version=2)
    m.validate_binding(current)
    assert m.evidence(result(), current)['build_manifest_digest'] == current['build_manifest_digest']
    with pytest.raises(ValueError, match='receipt version'):
        m.validate_binding(dict(original, receipt_version=3))


@pytest.mark.parametrize('engines', [{}, [], {'sast': ''}, {'': 'semgrep'}])
def test_v2_rejects_missing_or_invalid_security_engine_evidence(engines):
    checks = result(); checks['security']['engines'] = engines
    with pytest.raises(ValueError, match='security engine evidence'):
        m.evidence(checks, dict(binding(), receipt_version=2))


def test_passing_receipt_requires_requests_for_each_scenario_run():
    checks = result()
    checks['wall']['receipt']['requests'] = 0
    with pytest.raises(ValueError, match='cover every scenario run'):
        m.evidence(checks, binding())


@pytest.mark.parametrize('checks_recorded', [False, True])
def test_explicit_continuation_uses_original_completed_checks_without_reevaluation(tmp_path, checks_recorded):
    folder = tmp_path / 'session'; folder.mkdir(mode=0o700)
    m.record(folder/'intent.json', binding())
    if checks_recorded:
        m.record(folder/'checks.json', result())
    else:
        (folder/'target').mkdir(mode=0o700)
        m.record(folder/'target'/'result.json', result())
    runtime = Runtime()
    accepted = runtime.run(folder, resume_only=True)
    assert accepted['accepted'] is True
    assert runtime.evaluations == 0 and runtime.submissions == 1
    assert runtime.run(folder, resume_only=True) == accepted
    assert runtime.evaluations == 0 and runtime.submissions == 1


def test_continuation_never_repeats_an_uncertain_submission(tmp_path):
    runtime = Runtime(); runtime.lose_response = True
    folder = tmp_path / 'session'
    # The first submission is recorded but never accepted by the remote.
    def lose_before_acceptance(envelope):
        runtime.submissions += 1
        raise TimeoutError('unknown outcome')
    runtime.submit = lose_before_acceptance
    with pytest.raises(ValueError, match='unresolved'):
        runtime.run(folder)
    with pytest.raises(ValueError, match='never resend'):
        runtime.run(folder, resume_only=True)
    assert runtime.evaluations == 1 and runtime.submissions == 1


def test_continuation_without_completed_checks_never_restarts_target(tmp_path):
    folder = tmp_path / 'session'; folder.mkdir(mode=0o700)
    m.record(folder/'intent.json', binding())
    runtime = Runtime()
    with pytest.raises(ValueError, match='never recreate'):
        runtime.run(folder, resume_only=True)
    assert runtime.evaluations == 0 and runtime.submissions == 0


def test_explicit_prejournal_continuation_claims_exactly_one_original_run(tmp_path):
    runtime = Runtime(); folder = tmp_path/'session'
    accepted = runtime.run(folder, resume_only=True, resume_unstarted=True)
    assert accepted['accepted'] is True
    assert runtime.evaluations == 1 and runtime.submissions == 1
    assert runtime.run(folder, resume_only=True, resume_unstarted=True) == accepted
    assert runtime.evaluations == 1 and runtime.submissions == 1


def test_prejournal_continuation_never_recreates_existing_target(tmp_path):
    runtime = Runtime(); folder = tmp_path/'session'; folder.mkdir(mode=0o700)
    (folder/'target').mkdir(mode=0o700)
    with pytest.raises(ValueError, match='original'):
        runtime.run(folder, resume_only=True, resume_unstarted=True)
    assert runtime.evaluations == 0 and runtime.submissions == 0


def test_resume_can_claim_journal_intent_when_no_target_was_created(tmp_path):
    runtime = Runtime(); folder = tmp_path/'session'; folder.mkdir(mode=0o700)
    m.record(folder/'intent.json', binding())
    assert runtime.run(folder, resume_only=True, resume_unstarted=True)['accepted'] is True
    assert runtime.evaluations == 1 and runtime.submissions == 1


def test_prejournal_continuation_serializes_against_original_start(tmp_path):
    import threading
    runtime = Runtime(); folder = tmp_path/'session'
    started = threading.Event(); release = threading.Event(); outcomes = []
    def evaluate():
        runtime.evaluations += 1
        started.set()
        assert release.wait(5)
        return result()
    runtime.evaluate = evaluate
    thread = threading.Thread(target=lambda: outcomes.append(runtime.run(folder)))
    thread.start()
    try:
        assert started.wait(5)
        with pytest.raises(BlockingIOError):
            runtime.run(folder, resume_only=True, resume_unstarted=True)
    finally:
        release.set(); thread.join(5)
    assert not thread.is_alive() and outcomes[0]['accepted'] is True
    assert runtime.evaluations == 1 and runtime.submissions == 1


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
        proof = json.loads(payload)
        self.state.update(stage='validated', validation=proof, receipt=proof['receipt'])
        if self.lose_response:
            raise TimeoutError('accepted response lost')
        return True

    def run(self, folder, **kwargs):
        return m.validate_session(folder, binding(), lambda: self.state,
                                  self.evaluate, self.submit, KEY, **kwargs)


def test_binding_response_loss_uses_original_readback_before_evaluation(tmp_path):
    runtime=Runtime();runtime.state['validation_contract']='manifest-v2';calls=[]
    def bind(envelope):
        calls.append(envelope)
        assert hmac.compare_digest(envelope['signature'],hmac.new(KEY,envelope['payload'].encode(),hashlib.sha256).hexdigest())
        runtime.state['validation_binding']=json.loads(envelope['payload'])
        raise TimeoutError('response lost after acceptance')
    v2=dict(binding(),receipt_version=2)
    outcome=m.validate_session(tmp_path/'session',v2,lambda:runtime.state,runtime.evaluate,runtime.submit,KEY,bind=bind)
    assert outcome['accepted'] is True and len(calls)==runtime.evaluations==runtime.submissions==1
    assert runtime.state['validation_binding']['build_manifest_digest']==v2['build_manifest_digest']


def test_uncertain_binding_never_repeats_or_runs_candidate(tmp_path):
    runtime=Runtime();runtime.state['validation_contract']='manifest-v2';calls=[]
    def bind(envelope):
        calls.append(envelope);raise TimeoutError('unknown outcome')
    v2=dict(binding(),receipt_version=2);folder=tmp_path/'session'
    with pytest.raises(ValueError,match='binding unresolved'):
        m.validate_session(folder,v2,lambda:runtime.state,runtime.evaluate,runtime.submit,KEY,bind=bind)
    with pytest.raises(ValueError,match='never resend'):
        m.validate_session(folder,v2,lambda:runtime.state,runtime.evaluate,runtime.submit,KEY,bind=bind,resume_only=True)
    assert len(calls)==1 and runtime.evaluations==runtime.submissions==0


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


@pytest.mark.parametrize('stored_receipt', [None, {'git_revision': '0'*40}])
def test_receipt_must_be_durably_stored_before_acceptance(tmp_path, stored_receipt):
    runtime = Runtime()
    def submit(envelope):
        runtime.submissions += 1
        runtime.state.update(stage='validated', validation=json.loads(envelope['payload']), receipt=stored_receipt)
        return True
    runtime.submit = submit
    with pytest.raises(ValueError, match='acceptance unresolved'):
        runtime.run(tmp_path/'session')
    with pytest.raises(ValueError, match='acceptance unresolved'):
        runtime.run(tmp_path/'session', observe_only=True)
    assert runtime.evaluations == runtime.submissions == 1
    assert not (tmp_path/'session/accepted.json').exists()


def test_failed_quorum_is_not_submitted_and_feedback_stays_private(tmp_path):
    scenarios = [ScenarioResult(f'private-pass-{i}', 3, 3) for i in range(9)]
    markers = ('private-holdout-marker', '/private/holdout-path', 'expected-secret-value')
    scenarios.append(ScenarioResult(markers[0], 0, 3,
                                    f'GET {markers[1]} expected {markers[2]}'))
    verdict = gate(scenarios)
    assert verdict.passed is False
    assert verdict.aggregate_pass_rate == .9
    original = result()
    original['passed'] = False
    original['wall']['verdict'] = asdict(verdict)
    original['wall']['receipt']['requests'] = 30
    runtime = Runtime()
    def evaluate():
        runtime.evaluations += 1
        return original
    runtime.evaluate = evaluate
    folder = tmp_path / 'session'
    rejected = runtime.run(folder)
    assert runtime.run(folder, observe_only=True) == rejected
    assert rejected['accepted'] is False
    assert runtime.evaluations == 1 and runtime.submissions == 0
    checks = json.loads((folder / 'checks.json').read_text())
    assert checks == original
    assert markers[0] in m.canonical(checks)
    for marker in markers:
        assert marker not in m.canonical(rejected)
    assert rejected['checks_digest'] == hashlib.sha256(m.canonical(original).encode()).hexdigest()
    assert not (folder/'envelope.json').exists()
    assert not (folder/'accepted.json').exists()


def test_receipt_rejects_inconsistent_passing_verdict_with_failed_quorum():
    checks = result()
    checks['wall']['verdict']['per_scenario']['failed-case'] = False
    checks['wall']['receipt']['requests'] = 6
    with pytest.raises(ValueError, match='every scenario quorum'):
        m.evidence(checks, dict(binding(), receipt_version=2))


def test_receipt_rejects_aggregate_not_derived_from_scenarios():
    checks = result()
    checks['wall']['verdict']['aggregate_pass_rate'] = 0.9
    with pytest.raises(ValueError, match='aggregate differs'):
        m.evidence(checks, dict(binding(), receipt_version=2))


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
