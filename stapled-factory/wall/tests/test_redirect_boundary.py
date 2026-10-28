"""An evaluated app must not redirect the trusted evaluator to another service."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evaluator'))
from evaluate import _request


def test_redirect_cannot_reach_host_service_or_environment_proxy(monkeypatch):
    visited = []
    class HostService(BaseHTTPRequestHandler):
        def do_GET(self):
            visited.append(self.path)
            self.send_response(200); self.end_headers()
        def log_message(self, *args): pass
    other = HTTPServer(('127.0.0.1', 0), HostService)
    class Candidate(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location', f'http://127.0.0.1:{other.server_port}/private')
            self.send_header('Content-Length', '0')
            self.end_headers()
        def log_message(self, *args): pass
    candidate = HTTPServer(('127.0.0.1', 0), Candidate)
    for server in (other, candidate):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    try:
        assert _request(f'http://127.0.0.1:{candidate.server_port}', {'request': {'method': 'GET', 'path': '/'}})[0] == 302
        assert visited == []
    finally:
        for server in (candidate, other):
            server.shutdown(); server.server_close()
