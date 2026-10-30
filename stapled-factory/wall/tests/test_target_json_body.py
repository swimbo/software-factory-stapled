"""Regression tests for JSON body parsing in the target auth fixture."""
import http.client
import json
import sys
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "target"))

import app  # noqa: E402


class TargetJsonBodyTests(unittest.TestCase):
    def setUp(self):
        app.ATTEMPTS.clear()
        app.SESSIONS.clear()
        app.REVOKED.clear()
        self.server = HTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def _request(self, method, path, body=None):
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5
        )
        try:
            conn.request(method, path, body=body)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def _assert_rejected(self, raw):
        app.ATTEMPTS.clear()
        status, payload = self._request("POST", "/login", raw)
        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "bad credentials"})

    def test_non_object_json_is_rejected_without_dropping_connection(self):
        for raw in (b"[]", b'[{"username": "alice"}]', b"null", b'"alice"',
                    b"42", b"1.5", b"true"):
            with self.subTest(body=raw):
                self._assert_rejected(raw)

    def test_malformed_json_and_invalid_utf8_are_rejected(self):
        for raw in (b"{not json", b'{"username": "\xff"}', b"\xff"):
            with self.subTest(body=raw):
                self._assert_rejected(raw)

    def test_valid_login_still_works(self):
        body = json.dumps({"username": "alice", "password": "correct"}).encode()
        status, payload = self._request("POST", "/login", body)
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"token": app.VALID_TOKEN})

    def test_health_still_works(self):
        status, payload = self._request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
