"""The wall runner — executes holdout scenarios against a target, returns a verdict.

Isolated from the generator: this file and the holdout store share no code path
with the worker, and the verdict it returns is opaque (one line on failure).
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from config import TierConfig, check_ceiling
from gate import ScenarioResult, Verdict, gate
from receipt import UsageRecord


def load_scenarios(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text())
    return data["scenarios"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(target_base: str, step: dict) -> tuple[int, str]:
    method = step["request"].get("method", "GET").upper()
    path = step["request"].get("path", "/")
    url = target_base.rstrip("/") + "/" + path.lstrip("/")
    headers = step["request"].get("headers", {}) or {}
    body_bytes = None
    if step["request"].get("body") is not None:
        body_bytes = json.dumps(step["request"]["body"]).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body_bytes, method=method, headers=headers)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        return 0, str(e.reason)


def _check_step(step: dict, status: int, body: str) -> str:
    """Return a one-line failure string, or "" when the step passed."""
    expect = step.get("expect", {})
    method = step["request"].get("method", "GET")
    path = step["request"].get("path", "/")
    if "status" in expect and status != expect["status"]:
        return f"{method} {path} returned {status} (expected {expect['status']})"
    if "body_contains" in expect and expect["body_contains"] not in body:
        return f"{method} {path} body missing '{expect['body_contains']}'"
    return ""


def _run_once(target_base: str, scenario: dict) -> tuple[bool, str, int]:
    requests = 0
    for step in scenario["steps"]:
        if "delay_seconds" in step:
            time.sleep(step["delay_seconds"])
            continue
        requests += 1
        status, body = _request(target_base, step)
        failure = _check_step(step, status, body)
        if failure:
            return False, failure, requests
    return True, "", requests


def run_scenario(target_base: str, scenario: dict, runs: int) -> ScenarioResult:
    passed = 0
    requests = 0
    first_failure = ""
    for _ in range(runs):
        ok, failure, attempted = _run_once(target_base, scenario)
        requests += attempted
        if ok:
            passed += 1
        elif not first_failure:
            first_failure = failure
    return ScenarioResult(
        scenario_id=scenario["id"],
        runs_passed=passed,
        runs_total=runs,
        failure=first_failure,
        requests=requests,
    )


def evaluate(
    target_base: str,
    scenarios_path: str | Path,
    runs_per_scenario: int = 3,
    tier: str = "unknown",
    pricing_mode: str = "subscription",
    cost_estimate_usd: float | None = None,
    tier_config: TierConfig | None = None,
) -> tuple[Verdict, UsageRecord]:
    scenarios = load_scenarios(scenarios_path)
    start = time.perf_counter()

    results = [run_scenario(target_base, s, runs_per_scenario) for s in scenarios]
    verdict = gate(results)

    wall_clock_ms = int((time.perf_counter() - start) * 1000)
    receipt = UsageRecord(
        requests=sum(r.requests for r in results),
        wall_clock_ms=wall_clock_ms,
        tier=tier,
        pricing_mode=pricing_mode,
        cost_estimate_usd=cost_estimate_usd,
    )
    if tier_config is not None:
        receipt.ceiling_violation = check_ceiling(receipt, tier_config)
    return verdict, receipt
