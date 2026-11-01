"""Candidate HTTP inputs must not be changed into misleading wall results."""
import http.client
import importlib.util
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import urllib.parse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location(
    'prebuilt_transport', Path(__file__).resolve().parents[1] / 'prebuilt_validation.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_archive_lockfiles_are_discovered_and_symlink_lockfiles_rejected(tmp_path):
    import io
    import tarfile
    def archive(link=False):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as tar:
            for name in ('web/package-lock.json', 'backend/Cargo.lock', 'README.md'):
                entry = tarfile.TarInfo(name)
                if link and name.endswith('Cargo.lock'):
                    entry.type = tarfile.SYMTYPE;entry.linkname = '../README.md';tar.addfile(entry)
                else:
                    entry.size = 2;tar.addfile(entry, io.BytesIO(b'{}'))
        return data.getvalue()
    assert m.archive_lockfiles(archive(), tmp_path) == [str(tmp_path / 'backend/Cargo.lock'),
                                                         str(tmp_path / 'web/package-lock.json')]
    with pytest.raises(ValueError, match='lockfile'):
        m.archive_lockfiles(archive(True), tmp_path)


def test_source_inventory_reports_manifests_without_matching_locks():
    import io
    import tarfile
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as tar:
        for name in ('frontend/package.json', 'agents/package.json', 'frontend/package-lock.json',
                     'backend/Cargo.toml', 'backend/Cargo.lock'):
            entry = tarfile.TarInfo(name);entry.size = 2;tar.addfile(entry, io.BytesIO(b'{}'))
    inventory = m.archive_dependency_inventory(data.getvalue())
    assert inventory['missing_locks'] == ['agents/package.json']
    assert inventory['manifests'] == ['agents/package.json', 'backend/Cargo.toml', 'frontend/package.json']
    assert inventory['coverage'] == 'incomplete'


def test_source_inventory_rejects_symlinked_dependency_manifest():
    import io
    import tarfile
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as tar:
        entry = tarfile.TarInfo('package.json');entry.type = tarfile.SYMTYPE
        entry.linkname = 'other';tar.addfile(entry)
    with pytest.raises(ValueError, match='manifest'):
        m.archive_dependency_inventory(data.getvalue())


@pytest.fixture(params=['stream', 'production'])
def bridge(request, monkeypatch):
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
    if request.param == 'production':
        launch = subprocess.Popen
        def docker_exec(args, **kwargs):
            assert args == ['docker', 'exec', '-i', 'a' * 64, 'python3', '-u', '-c', client]
            return launch([sys.executable, '-u', '-c', client], **kwargs)
        monkeypatch.setattr(m, 'CLIENT', client)
        monkeypatch.setattr(subprocess, 'Popen', docker_exec)
        transport = m.CandidateTransport('a' * 64)
    else:
        transport = subprocess.Popen([sys.executable, '-u', '-c', client],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), m.forwarder_handler(transport, threading.Lock()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port), calls
    finally:
        server.shutdown();server.server_close()
        if request.param == 'production':
            transport.close()
        else:
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


def test_full_wall_preserves_short_session_and_rate_window(monkeypatch):
    """Process startup must not consume the fixture's 100ms request windows."""
    import json
    root = Path(__file__).resolve().parents[2]
    target_spec = importlib.util.spec_from_file_location('bounded_wall_target', root / 'wall/target/app.py')
    app = importlib.util.module_from_spec(target_spec)
    target_spec.loader.exec_module(app)
    candidate = HTTPServer(('127.0.0.1', 0), app.Handler)
    thread = threading.Thread(target=candidate.serve_forever, daemon=True)
    thread.start()
    client = m.CLIENT.replace('8000', str(candidate.server_port))
    monkeypatch.setattr(m, 'CLIENT', client)
    launch = subprocess.Popen
    children = []
    def docker_exec(args, **kwargs):
        if args[0] != 'docker':
            return launch(args, **kwargs)
        assert args == ['docker', 'exec', '-i', 'a'*64, 'python3', '-u', '-c', client]
        child = launch([sys.executable, '-u', '-c', 'import time;time.sleep(.15)\n' + client], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, 'Popen', docker_exec)
    transport = m.CandidateTransport('a'*64)
    try:
        with m.candidate_http_server(transport, threading.Lock()) as server:
            result = subprocess.run([sys.executable, str(root / 'wall/evaluator/cli.py'),
                '--target', 'http://127.0.0.1:' + str(server.server_port),
                '--scenarios', str(root / 'wall/holdout/scenarios.json'), '--runs', '3'],
                check=True, capture_output=True, text=True, timeout=20)
            wall = json.loads(result.stdout)
            assert wall['verdict']['passed'] is True, wall['verdict']['failure_message']
            assert wall['verdict']['aggregate_pass_rate'] == 1
            assert len(children) == 1
    finally:
        transport.close()
        candidate.shutdown();candidate.server_close();thread.join(timeout=5)
    assert children[0].returncode is not None
