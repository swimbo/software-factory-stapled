"""Unit tests for the wall's gate logic."""
from gate import ScenarioResult, gate, required_majority
import pytest


def test_required_majority_single_run_is_1_of_1():
    assert required_majority(1) == 1


def test_required_majority_triple_run_is_2_of_3():
    assert required_majority(3) == 2


def test_all_scenarios_pass():
    results = [
        ScenarioResult("a", runs_passed=1, runs_total=1),
        ScenarioResult("b", runs_passed=1, runs_total=1),
        ScenarioResult("c", runs_passed=1, runs_total=1),
    ]
    v = gate(results)
    assert v.passed is True
    assert v.aggregate_pass_rate == 1.0
    assert v.failure_message == ""


def test_broken_diff_rejected_with_one_line_feedback():
    results = [
        ScenarioResult("auth-login-success", runs_passed=0, runs_total=1, failure="POST /login returned 500 (expected 200)"),
        ScenarioResult("auth-login-wrong-password", runs_passed=1, runs_total=1),
        ScenarioResult("auth-logout-invalidates-session", runs_passed=1, runs_total=1),
        ScenarioResult("auth-unauthenticated-denied", runs_passed=1, runs_total=1),
        ScenarioResult("auth-health-ok", runs_passed=1, runs_total=1),
    ]
    v = gate(results)  # 4/5 = 80% < 90% -> fail
    assert v.passed is False
    assert v.aggregate_pass_rate == 0.8
    # feedback is one line, and does not leak scenario text
    assert v.failure_message == "auth-login-success: POST /login returned 500 (expected 200)"
    assert "\n" not in v.failure_message


def test_aggregate_threshold_cannot_override_failed_scenario():
    # 90% aggregate cannot override a missing scenario quorum.
    results = [ScenarioResult(f"s{i}", runs_passed=1, runs_total=1) for i in range(9)]
    results.append(ScenarioResult("s9", runs_passed=0, runs_total=1, failure="boom"))
    v = gate(results, aggregate_threshold=0.90)
    assert v.passed is False
    assert v.aggregate_pass_rate == 0.9


def test_two_of_three_counts_as_scenario_pass():
    # A scenario that passes 2/3 runs still passes its quorum.
    results = [ScenarioResult("flaky", runs_passed=2, runs_total=3)]
    v = gate(results)
    assert v.passed is True
    assert v.per_scenario["flaky"] is True


def test_one_of_three_fails_scenario_quorum():
    results = [ScenarioResult("flaky", runs_passed=1, runs_total=3, failure="x")]
    v = gate(results)
    assert v.per_scenario["flaky"] is False


def test_failed_triple_run_cannot_hide_in_passing_aggregate():
    results = [ScenarioResult(f's{i}', runs_passed=3, runs_total=3) for i in range(9)]
    results.append(ScenarioResult('failed-quorum', runs_passed=1, runs_total=3))
    verdict = gate(results)
    assert verdict.aggregate_pass_rate == 0.9
    assert verdict.per_scenario['failed-quorum'] is False
    assert verdict.passed is False


def test_invalid_or_duplicate_scenario_results_are_rejected():
    with pytest.raises(ValueError, match='duplicate'):
        gate([ScenarioResult('same', 3, 3), ScenarioResult('same', 3, 3)])
    with pytest.raises(ValueError, match='invalid'):
        gate([ScenarioResult('bad', 4, 3)])
