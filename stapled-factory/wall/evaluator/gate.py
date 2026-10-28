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


@dataclass
class Verdict:
    passed: bool
    aggregate_pass_rate: float
    per_scenario: dict = field(default_factory=dict)  # scenario_id -> bool
    failure_message: str = ""  # one line only, "" when passed


def required_majority(runs_total: int) -> int:
    """2-of-3 for triple-run (prod gate); 1-of-1 for a single run (MVP)."""
    if runs_total >= 3:
        return (runs_total // 2) + 1
    return 1


def gate(
    scenario_results: list[ScenarioResult],
    aggregate_threshold: float = 0.90,
) -> Verdict:
    if not scenario_results:
        return Verdict(passed=False, aggregate_pass_rate=0.0, failure_message="no scenarios evaluated")

    per_scenario: dict[str, bool] = {}
    first_failure = ""

    for r in scenario_results:
        majority = required_majority(r.runs_total)
        ok = r.runs_passed >= majority
        per_scenario[r.scenario_id] = ok
        if not ok and not first_failure:
            # One line only — never the scenario text.
            first_failure = f"{r.scenario_id}: {r.failure or 'failed'}"

    passed_count = sum(1 for ok in per_scenario.values() if ok)
    aggregate = passed_count / len(scenario_results)

    return Verdict(
        # A scenario "passes" its quorum (2-of-3 or 1-of-1); the wall passes when
        # >= threshold of scenarios pass. failure_message is feedback, not the pass test.
        passed=aggregate >= aggregate_threshold,
        aggregate_pass_rate=aggregate,
        per_scenario=per_scenario,
        failure_message=first_failure,
    )
