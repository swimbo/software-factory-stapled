import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from attempt_store import AttemptStore
from openhands_adapter import OpenHandsAdapter
from worker import execute_openhands_attempt


@pytest.fixture
def server():
    state = {'posts': 0, 'gets': 0, 'status': 'running', 'lose': False, 'mismatch': False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def respond(self, data):
            raw = json.dumps(data).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_POST(self):
            assert self.headers['X-Session-API-Key'] == 'test-key'
            assert self.path == '/api/conversations'
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['id'] = payload['conversation_id']
            state['posts'] += 1
            if state['lose']:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.respond({'id': state['id'], 'execution_status': state['status']})
        def do_GET(self):
            assert self.headers['X-Session-API-Key'] == 'test-key'
            assert self.path == '/api/conversations/' + state['id']
            state['gets'] += 1
            self.respond({'id': 'wrong' if state['mismatch'] else state['id'], 'execution_status': state['status']})
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    yield state, 'http://127.0.0.1:%d' % http.server_port
    http.shutdown()
    thread.join()
    http.server_close()


@pytest.mark.parametrize('lost', [False, True])
def test_create_then_reconcile_without_second_post(server, tmp_path, lost):
    state, url = server
    state['lose'] = lost
    store = AttemptStore(tmp_path / 'attempts.db')
    adapter = OpenHandsAdapter(url, 'test-key', lambda cid, info: 'a' * 40)
    request = {'agent': {'kind': 'test'}, 'initial_message': {'role': 'user', 'content': [{'type': 'text', 'text': 'task'}], 'run': True}}
    first = execute_openhands_attempt(store, 'attempt', request, adapter)
    assert first['status'] == 'unknown'
    assert execute_openhands_attempt(store, 'attempt', request, adapter)['status'] == 'unknown'
    assert state['posts'] == 1 and state['gets'] == 1
    state['status'] = 'finished'
    state['mismatch'] = True
    assert execute_openhands_attempt(store, 'attempt', request, adapter)['status'] == 'unknown'
    state['mismatch'] = False
    result = execute_openhands_attempt(store, 'attempt', request, adapter)
    assert result['status'] == 'completed' and result['git_revision'] == 'a' * 40
    assert execute_openhands_attempt(store, 'attempt', request, adapter) == result
    assert state['posts'] == 1 and state['gets'] == 3


def test_finished_without_verified_revision_stays_unknown(server, tmp_path):
    state, url = server
    state['status'] = 'finished'
    adapter = OpenHandsAdapter(url, 'test-key', lambda *_: None)
    result = execute_openhands_attempt(AttemptStore(tmp_path / 'a.db'), 'a', {'initial_message': {'run': True, 'content': [{'type': 'text', 'text': 'task'}]}}, adapter)
    assert result['status'] == 'unknown'


@pytest.mark.parametrize('url', ['http://example.com', 'http://user:pass@localhost:1234',
                                 'https://example.com/path?query=x', 'https://example.com/#frag',
                                 'ftp://localhost', 'http://127.0.0.1@evil.example'])
def test_invalid_server_urls_are_rejected(url):
    with pytest.raises(ValueError):
        OpenHandsAdapter(url, 'secret', lambda *_: None)


@pytest.mark.parametrize('status', ['error', 'stuck'])
def test_terminal_error_remains_reconcilable_without_redispatch(server, tmp_path, status):
    state, url = server
    state['status'] = status
    adapter = OpenHandsAdapter(url, 'test-key', lambda *_: 'a' * 40)
    store = AttemptStore(tmp_path / 'a.db')
    request = {'initial_message': {'run': True, 'content': [{'type': 'text', 'text': 'task'}]}}
    result = execute_openhands_attempt(store, 'a', request, adapter)
    assert result['status'] == 'unknown'
    assert execute_openhands_attempt(store, 'a', request, adapter)['status'] == 'unknown'
    assert state['posts'] == 1 and state['gets'] == 1
    state['status'] = 'finished'
    assert execute_openhands_attempt(store, 'a', request, adapter)['status'] == 'completed'
    assert state['posts'] == 1 and state['gets'] == 2


def test_malformed_revision_is_not_accepted(server, tmp_path):
    state, url = server
    state['status'] = 'finished'
    adapter = OpenHandsAdapter(url, 'test-key', lambda *_: 'not-a-revision')
    assert execute_openhands_attempt(AttemptStore(tmp_path / 'a.db'), 'a', {'initial_message': {'run': True, 'content': [{'type': 'text', 'text': 'task'}]}}, adapter)['status'] == 'unknown'


def test_redirect_does_not_send_session_key(tmp_path):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            seen.append(self.path)
            self.send_response(307)
            self.send_header('Location', '/redirect-target')
            self.end_headers()
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        adapter = OpenHandsAdapter('http://127.0.0.1:%d' % http.server_port, 'test-key', lambda *_: None)
        result = execute_openhands_attempt(AttemptStore(tmp_path / 'a.db'), 'a', {'initial_message': {'run': True, 'content': [{'type': 'text', 'text': 'task'}]}}, adapter)
        assert result['status'] == 'unknown'
        assert seen == ['/api/conversations']
    finally:
        http.shutdown()
        thread.join()
        http.server_close()


def test_ambient_proxy_disabled(monkeypatch):
    monkeypatch.setenv('https_proxy', 'http://proxy.invalid:1234')
    adapter = OpenHandsAdapter('https://server.example', 'test-key', lambda *_: None)
    assert not any(getattr(handler, 'proxies', None) for handler in adapter.opener.handlers)


def test_initial_message_must_request_execution(server, tmp_path):
    state, url = server
    adapter = OpenHandsAdapter(url, 'test-key', lambda *_: None)
    result = execute_openhands_attempt(AttemptStore(tmp_path / 'a.db'), 'a',
        {'initial_message': {'content': [{'type': 'text', 'text': 'task'}], 'run': False}}, adapter)
    assert result['status'] == 'unknown'
    assert state['posts'] == 0


def test_guarded_recovery_with_injected_container_transport(tmp_path):
    from guarded_store import GuardedAttemptStore
    from docker_transport import DockerTransportError
    (tmp_path / 'ledger').mkdir(); (tmp_path / 'anchor').mkdir()
    db, anchor = tmp_path / 'ledger' / 'attempts', tmp_path / 'anchor' / 'watermark'
    store = GuardedAttemptStore.initialize(db, anchor)
    class Transport:
        def __init__(self): self.calls = []; self.cid = None
        def request(self, method, path, payload):
            self.calls.append(method)
            if method == 'POST':
                self.cid = payload['conversation_id']
                raise DockerTransportError('accepted but response lost')
            assert path.endswith(self.cid)
            return {'id': self.cid, 'execution_status': 'finished'}
    transport = Transport()
    adapter = OpenHandsAdapter('http://127.0.0.1:8000', 'secret', lambda *_: 'a' * 40, transport=transport)
    request = {'initial_message': {'run': True, 'content': [{'type': 'text', 'text': 'task'}]}}
    assert execute_openhands_attempt(store, 'attempt', request, adapter)['status'] == 'unknown'
    result = execute_openhands_attempt(GuardedAttemptStore(db, anchor), 'attempt', request, adapter, reconcile_only=True)
    assert result['status'] == 'completed' and result['git_revision'] == 'a' * 40
    assert execute_openhands_attempt(GuardedAttemptStore(db, anchor), 'attempt', request, adapter, reconcile_only=True) == result
    assert transport.calls == ['POST', 'GET']
