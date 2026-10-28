"""End-to-end: run the wall against the correct and broken target fixtures."""
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "target"))
sys.path.insert(0, str(_ROOT / "evaluator"))

from app import Handler as CorrectHandler  # noqa: E402
from broken_app import Handler as BrokenHandler  # noqa: E402
from evaluate import evaluate  # noqa: E402

HOLDOUT = _ROOT / "holdout" / "scenarios.json"


def _serve(handler_cls, port):
    srv = HTTPServer(("127.0.0.1", port), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_correct_target_passes():
    _serve(CorrectHandler, 8777)
    verdict, receipt = evaluate("http://127.0.0.1:8777", HOLDOUT)
    assert verdict.passed is True, verdict.failure_message
    assert verdict.aggregate_pass_rate == 1.0
    assert receipt.pricing_mode == "subscription"
    assert receipt.requests > 0


def test_broken_target_rejected_with_one_line_feedback():
    _serve(BrokenHandler, 8778)
    verdict, _ = evaluate("http://127.0.0.1:8778", HOLDOUT)
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

    srv = _serve(NoLogout, 0)
    try:
        verdict, _ = evaluate(f"http://127.0.0.1:{srv.server_port}", HOLDOUT)
        assert verdict.per_scenario["auth-logout-invalidates-session"] is False
        assert verdict.passed is False
    finally:
        srv.shutdown()
        srv.server_close()


def test_expiry_negative_control(monkeypatch):
    import app
    monkeypatch.setattr(app, "SESSION_TTL", 60)
    srv = _serve(CorrectHandler, 0)
    try:
        verdict, _ = evaluate(f"http://127.0.0.1:{srv.server_port}", HOLDOUT)
        assert verdict.per_scenario["auth-session-expiry"] is False
        assert verdict.passed is False
    finally:
        srv.shutdown()
        srv.server_close()


def test_rate_limit_negative_control(monkeypatch):
    import app
    monkeypatch.setattr(app, "RATE_LIMIT", 1000)
    srv = _serve(CorrectHandler, 0)
    try:
        verdict, _ = evaluate(f"http://127.0.0.1:{srv.server_port}", HOLDOUT)
        assert verdict.per_scenario["auth-rate-limit"] is False
        assert verdict.passed is False
    finally:
        srv.shutdown()
        srv.server_close()
