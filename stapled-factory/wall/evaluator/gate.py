"""The wall's gate — per-scenario quorum + aggregate threshold, one-line feedback.

The gate is a quality / non-determinism check (catches sycophancy and flaky
output), NOT a security boundary. It deliberately returns only a single-line
failure string so a generator cannot read the scenario text back out of it.
"""
from dataclasses import dataclass, field


@dataclass
class ScenarioResult:
    scenario_id: str
    runs_passed: int
    runs_total: int
    failure: str = ""  # one-line message, or "" when the scenario passed
    requests: int = 0  # HTTP requests actually attempted across all runs


@dataclass
class Verdict:
    passed: bool
    aggregate_pass_rate: float
    per_scenario: dict = field(default_factory=dict)  # scenario_id -> bool
    failure_message: str = ""  # one line only, "" when passed


def required_majority(runs_total: int) -> int:
    """Require a strict majority, including 1-of-1 and 2-of-3."""
    if type(runs_total) is not int or runs_total < 1:
        raise ValueError('positive run count required')
    return runs_total // 2 + 1


def gate(
    scenario_results: list[ScenarioResult],
    aggregate_threshold: float = 0.90,
) -> Verdict:
    if not scenario_results:
        return Verdict(passed=False, aggregate_pass_rate=0.0, failure_message="no scenarios evaluated")

    per_scenario: dict[str, bool] = {}
    first_failure = ""

    for r in scenario_results:
        if (not isinstance(r.scenario_id, str) or not r.scenario_id or r.scenario_id in per_scenario or
                type(r.runs_total) is not int or r.runs_total < 1 or type(r.runs_passed) is not int or
                not 0 <= r.runs_passed <= r.runs_total):
            raise ValueError('invalid or duplicate scenario result')
        majority = required_majority(r.runs_total)
        ok = r.runs_passed >= majority
        per_scenario[r.scenario_id] = ok
        if not ok and not first_failure:
            # Scenario IDs, request paths and expected values are holdout data.
            first_failure = 'A holdout scenario did not meet its quorum'

    passed_count = sum(1 for ok in per_scenario.values() if ok)
    aggregate = passed_count / len(scenario_results)

    return Verdict(
        # Every scenario must meet its quorum as well as the aggregate threshold.
        passed=all(per_scenario.values()) and aggregate >= aggregate_threshold,
        aggregate_pass_rate=aggregate,
        per_scenario=per_scenario,
        failure_message=first_failure,
    )
