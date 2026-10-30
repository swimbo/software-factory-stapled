"""End-to-end: run the wall against the correct and broken target fixtures."""
import json
import sys
import threading
from contextlib import contextmanager
from http.server import HTTPServer
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "target"))
sys.path.insert(0, str(_ROOT / "evaluator"))

from app import Handler as CorrectHandler  # noqa: E402
from broken_app import Handler as BrokenHandler  # noqa: E402
from evaluate import evaluate  # noqa: E402

HOLDOUT = _ROOT / "holdout" / "scenarios.json"


@contextmanager
def _serve(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join()


def _url(srv):
    return f"http://127.0.0.1:{srv.server_port}"


def test_correct_target_passes():
    with _serve(CorrectHandler) as srv:
        verdict, receipt = evaluate(_url(srv), HOLDOUT)
    assert verdict.passed is True, verdict.failure_message
    assert verdict.aggregate_pass_rate == 1.0
    assert receipt.pricing_mode == "subscription"
    assert receipt.requests > 0


def test_broken_target_rejected_with_one_line_feedback():
    with _serve(BrokenHandler) as srv:
        verdict, _ = evaluate(_url(srv), HOLDOUT)
    assert verdict.passed is False
    # one line only, and it names the failing scenario without leaking scenario text
    assert verdict.failure_message == "auth-login-success: POST /login returned 500 (expected 200)"
    assert "\n" not in verdict.failure_message


def test_logout_negative_control():
    class NoLogout(CorrectHandler):
        def do_POST(self):
            if self.path == "/logout":
                self._body()
                return self._send(200, {"status": "logged out"})
            return super().do_POST()

    with _serve(NoLogout) as srv:
        verdict, _ = evaluate(_url(srv), HOLDOUT)
    assert verdict.per_scenario["auth-logout-invalidates-session"] is False
    assert verdict.passed is False


def test_expiry_negative_control(monkeypatch):
    import app
    monkeypatch.setattr(app, "SESSION_TTL", 60)
    with _serve(CorrectHandler) as srv:
        verdict, _ = evaluate(_url(srv), HOLDOUT)
    assert verdict.per_scenario["auth-session-expiry"] is False
    assert verdict.passed is False


def test_rate_limit_negative_control(monkeypatch):
    import app
    monkeypatch.setattr(app, "RATE_LIMIT", 1000)
    with _serve(CorrectHandler) as srv:
        verdict, _ = evaluate(_url(srv), HOLDOUT)
    assert verdict.per_scenario["auth-rate-limit"] is False
    assert verdict.passed is False


def _write_scenarios(tmp_path, scenarios):
    path = tmp_path / "scenarios.json"
    path.write_text(json.dumps({"unit": "test", "scenarios": scenarios}))
    return path


def _get(path, status=200):
    return {"request": {"method": "GET", "path": path}, "expect": {"status": status}}


def test_receipt_counts_only_requests_attempted_before_early_failure(tmp_path):
    scenarios = _write_scenarios(tmp_path, [{
        "id": "early-failure",
        "steps": [
            {"delay_seconds": 0},
            _get("/missing"),
            _get("/health"),
        ],
    }])
    with _serve(CorrectHandler) as srv:
        verdict, receipt = evaluate(_url(srv), scenarios, runs_per_scenario=3)
    assert verdict.passed is False
    assert verdict.failure_message == "early-failure: GET /missing returned 404 (expected 200)"
    assert receipt.requests == 3


def test_receipt_counts_all_steps_executed_before_later_failure(tmp_path):
    scenarios = _write_scenarios(tmp_path, [{
        "id": "later-failure",
        "steps": [
            {"delay_seconds": 0},
            _get("/health"),
            _get("/missing"),
            _get("/health"),
        ],
    }])
    with _serve(CorrectHandler) as srv:
        verdict, receipt = evaluate(_url(srv), scenarios, runs_per_scenario=3)
    assert verdict.passed is False
    assert verdict.failure_message == "later-failure: GET /missing returned 404 (expected 200)"
    assert receipt.requests == 6


def test_receipt_counts_mixed_repeat_outcomes_and_ignores_delay_steps(tmp_path):
    class FlakyHandler(CorrectHandler):
        hits = 0
        statuses = [200, 500, 200]

        def do_GET(self):
            type(self).hits += 1
            if self.path == "/flaky":
                status = type(self).statuses.pop(0)
                return self._send(status, {"status": "ok" if status == 200 else "boom"})
            return super().do_GET()

    scenarios = _write_scenarios(tmp_path, [{
        "id": "flaky",
        "steps": [
            {"delay_seconds": 0.01},
            _get("/flaky"),
            {"delay_seconds": 0.01},
            _get("/health"),
        ],
    }])
    with _serve(FlakyHandler) as srv:
        verdict, receipt = evaluate(_url(srv), scenarios, runs_per_scenario=3)
    assert verdict.passed is True
    assert verdict.per_scenario["flaky"] is True
    assert FlakyHandler.hits == 5
    assert receipt.requests == 5


def test_receipt_counts_all_successes_across_scenarios_and_repeats(tmp_path):
    scenarios = _write_scenarios(tmp_path, [
        {
            "id": "one-request",
            "steps": [{"delay_seconds": 0.01}, _get("/health")],
        },
        {
            "id": "two-requests",
            "steps": [{"delay_seconds": 0}, _get("/health"), _get("/health")],
        },
        {
            "id": "delay-only",
            "steps": [{"delay_seconds": 0.01}],
        },
    ])
    with _serve(CorrectHandler) as srv:
        verdict, receipt = evaluate(_url(srv), scenarios, runs_per_scenario=3)
    assert verdict.passed is True
    assert verdict.aggregate_pass_rate == 1.0
    assert receipt.requests == 9
