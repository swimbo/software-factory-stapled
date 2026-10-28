"""Minimal auth target fixture — the deliberately-BROKEN variant.

Login returns 500, so the auth-login-success scenario must fail against it.
Used to prove the gate rejects a broken diff (and to keep the wall honest).
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

VALID_TOKEN = "tok-1"
REVOKED = set()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        if self.path == "/me":
            auth = self.headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ").strip()
            if token == VALID_TOKEN and token not in REVOKED:
                return self._send(200, {"user": "alice"})
            return self._send(401, {"error": "unauthorized"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        body = self._body()
        if self.path == "/login":
            # DELIBERATELY BROKEN: always 500.
            return self._send(500, {"error": "internal server error"})
        if self.path == "/logout":
            REVOKED.add(body.get("token", ""))
            return self._send(200, {"status": "logged out"})
        return self._send(404, {"error": "not found"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8778), Handler).serve_forever()
