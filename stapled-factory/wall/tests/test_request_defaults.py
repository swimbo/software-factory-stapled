"""Regression tests for default request fields in failure feedback."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evaluator"))

from evaluate import _check_step  # noqa: E402


class RequestDefaultFeedbackTests(unittest.TestCase):
    def test_omitted_method_status_mismatch_uses_get(self):
        step = {"request": {"path": "/login"}, "expect": {"status": 200}}
        self.assertEqual(
            _check_step(step, 500, ""),
            "GET /login returned 500 (expected 200)",
        )

    def test_omitted_path_status_mismatch_uses_root(self):
        step = {"request": {"method": "POST"}, "expect": {"status": 201}}
        self.assertEqual(
            _check_step(step, 500, ""),
            "POST / returned 500 (expected 201)",
        )

    def test_both_omitted_status_mismatch_uses_both_defaults(self):
        step = {"request": {}, "expect": {"status": 200}}
        self.assertEqual(
            _check_step(step, 503, ""),
            "GET / returned 503 (expected 200)",
        )

    def test_both_omitted_body_mismatch_uses_both_defaults(self):
        step = {"request": {}, "expect": {"body_contains": "healthy"}}
        self.assertEqual(
            _check_step(step, 200, "degraded"),
            "GET / body missing 'healthy'",
        )

    def test_explicit_method_and_path_keep_existing_feedback(self):
        step = {
            "request": {"method": "POST", "path": "/logout"},
            "expect": {"status": 204, "body_contains": "logged out"},
        }
        self.assertEqual(
            _check_step(step, 500, ""),
            "POST /logout returned 500 (expected 204)",
        )
        self.assertEqual(
            _check_step(step, 204, ""),
            "POST /logout body missing 'logged out'",
        )
        self.assertEqual(_check_step(step, 204, "logged out"), "")


if __name__ == "__main__":
    unittest.main()
