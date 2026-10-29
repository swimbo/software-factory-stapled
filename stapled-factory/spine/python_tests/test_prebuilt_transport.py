"""Candidate HTTP inputs must not be changed into misleading wall results."""
import http.client
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import urllib.parse

import pytest

spec = importlib.util.spec_from_file_location(
    'prebuilt_transport', Path(__file__).resolve().parents[1] / 'prebuilt_validation.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def bridge():
    calls = []

    class Candidate(BaseHTTPRequestHandler):
        def respond(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            calls.append((self.command, self.path, dict(self.headers), body))
            # Deliberately broken auth: missing credentials are rejected, but
            # an invalid supplied credential is incorrectly accepted.
            status = 200 if self.headers.get('X-Api-Key') else 401
            self.send_response(status)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(b'candidate response')
        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_PURGE = respond
        def log_message(self, *args):pass

    candidate = ThreadingHTTPServer(('127.0.0.1', 0), Candidate)
    threading.Thread(target=candidate.serve_forever, daemon=True).start()
    # Production CLIENT runs via docker exec in network-none and fixes port8000.
    # Substitute only the ephemeral fixture port to exercise the exact client.
    client = m.CLIENT.replace('8000', str(candidate.server_port))
    transport = subprocess.Popen([sys.executable, '-u', '-c', client],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), m.forwarder_handler(transport, threading.Lock()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port), calls
    finally:
        server.shutdown();server.server_close()
        transport.terminate();transport.wait(timeout=5)
        transport.stdin.close();transport.stdout.close()
        candidate.shutdown();candidate.server_close()


def request(url, method='GET', headers=None, body=None):
    target = urllib.parse.urlsplit(url)
    connection = http.client.HTTPConnection(target.hostname, target.port, timeout=5)
    try:
        connection.request(method, (target.path or '/') + ('?' + target.query if target.query else ''),
                           body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_missing_and_invalid_custom_credentials_are_distinct(bridge):
    url, calls = bridge
    assert request(url)[0] == 401
    # A wall expecting invalid-key401 must fail against this broken candidate.
    assert request(url, headers={'X-Api-Key': 'bad', 'Cookie': 'session=bad'}) == (200, b'candidate response')
    headers = {k.lower(): v for k, v in calls[-1][2].items()}
    assert headers['x-api-key'] == 'bad'
    assert headers['cookie'] == 'session=bad'


@pytest.mark.parametrize('method', ['PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'PURGE'])
def test_methods_reach_candidate_instead_of_proxy_error(bridge, method):
    url, calls = bridge
    status, body = request(url + '/resource?q=1', method,
                           {'X-Api-Key': 'bad', 'Content-Type': 'application/json'}, b'{"value":1}')
    assert status == 200
    assert body == (b'' if method == 'HEAD' else b'candidate response')
    assert calls[-1][0:2] == (method, '/resource?q=1')
    assert calls[-1][3] == b'{"value":1}'


def test_framing_and_connection_headers_are_not_forwarded(bridge):
    url, calls = bridge
    assert request(url, 'POST', {'X-Api-Key': 'bad', 'Connection': 'X-Hop',
                               'X-Hop': 'remove', 'Proxy-Authorization': 'remove'}, b'payload')[0] == 200
    headers = {k.lower(): v for k, v in calls[-1][2].items()}
    assert 'x-hop' not in headers
    assert 'proxy-authorization' not in headers
    assert headers['content-length'] == '7'
    assert calls[-1][3] == b'payload'


def test_host_header_cannot_change_transport_destination(bridge):
    url, calls = bridge
    assert request(url, headers={'Host': 'elsewhere.invalid:1234', 'X-Api-Key': 'bad'})[0] == 200
    assert len(calls) == 1
    assert calls[0][2]['Host'] == 'elsewhere.invalid:1234'
