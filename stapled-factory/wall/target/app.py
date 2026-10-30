"""Minimal auth target fixture — the CORRECT variant.

A tiny stdlib HTTP service the wall evaluates against. This is a fixture, not
production code; its only job is to let the gate be demonstrated end-to-end.
"""
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

VALID_TOKEN = "tok-1"
REVOKED = set()
SESSIONS = {}
ATTEMPTS = {}
SESSION_TTL = 0.1
RATE_WINDOW = 0.1
RATE_LIMIT = 3


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
            body = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return body if isinstance(body, dict) else {}

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        if self.path == "/me":
            auth = self.headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ").strip()
            if token not in REVOKED and SESSIONS.get(token, 0) > time.monotonic():
                return self._send(200, {"user": "alice"})
            return self._send(401, {"error": "unauthorized"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        body = self._body()
        if self.path == "/login":
            username = body.get("username", "")
            now = time.monotonic()
            recent = [t for t in ATTEMPTS.get(username, []) if now - t < RATE_WINDOW]
            ATTEMPTS[username] = recent + [now]
            if len(recent) >= RATE_LIMIT:
                return self._send(429, {"error": "rate limited"})
            if body.get("password") == "correct":
                REVOKED.discard(VALID_TOKEN)
                SESSIONS[VALID_TOKEN] = now + SESSION_TTL
                return self._send(200, {"token": VALID_TOKEN})
            return self._send(401, {"error": "bad credentials"})
        if self.path == "/logout":
            REVOKED.add(body.get("token", ""))
            return self._send(200, {"status": "logged out"})
        return self._send(404, {"error": "not found"})

    def log_message(self, *args):  # silence request logging
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8777), Handler).serve_forever()
