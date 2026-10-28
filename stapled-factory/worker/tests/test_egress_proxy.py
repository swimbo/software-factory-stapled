import http.client
import json
import socket
import sys
import threading
import tempfile
import os
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import egress_proxy


def budget(*args, **kwargs):
    db_path = Path(args[0])
    anchor_dir = db_path.parent / 'anchors'
    anchor_dir.mkdir(exist_ok=True)
    anchor = anchor_dir / (db_path.name + '.anchor')
    if not db_path.exists() and not anchor.exists():
        egress_proxy.GuardedAttemptStore.initialize(db_path, anchor)
    policy = dict(anchor_path=str(anchor), ceiling_microdollars=100, reserve_microdollars=10,
                  policy_id='test-policy', expires_at=4102444800)
    policy.update(kwargs)
    return egress_proxy.RequestBudget(*args, **policy)


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost')
        self.path = path
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.path)


def test_proxy_filters_paths_models_tokens_and_redirects(tmp_path, monkeypatch):
    calls = []
    class Response:
        status = 302
        def getheader(self, name, default): return default
        def read1(self, size): return b''
    class Upstream:
        def __init__(self, host, port, timeout): calls.append((host, port))
        def request(self, method, path, body, headers): calls.append((method, path, headers))
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(egress_proxy.http.client, 'HTTPSConnection', Upstream)
    path = '/tmp/sf-' + next(tempfile._get_candidate_names()) + '.sock'
    request_budget = budget(str(tmp_path / 'budget.db'), 'attempt-1', 'allowed', 1, 'a'*64)
    server = egress_proxy.UnixServer(path, 'test-only-key', 'fixed.example', 443, 'allowed', request_budget)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path_socket = path
    def request(path, payload):
        c = UnixHTTP(path_socket)
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        c.request('POST', path, raw, {'Content-Type': 'application/json'})
        response = c.getresponse()
        status = response.status
        response.read()
        c.close()
        return status
    try:
        assert request('/other', {'model': 'allowed', 'max_tokens': 1}) == 404
        assert request('/v1/messages', {'model': 'other', 'max_tokens': 1}) == 400
        assert request('/v1/messages', {'model': 'allowed', 'max_tokens': True}) == 400
        for kind in ('image', 'input_image', 'image_url', 'document', 'audio', 'input_audio', 'video'):
            assert request('/v1/messages', {'model': 'allowed', 'max_tokens': 1,
                'messages': [{'content': [{'type': 'tool_result', 'content': [{'type': kind}]}]}]}) == 400
        for option in ('service_tier', 'container', 'mcp_servers', 'unknown_future_option'):
            assert request('/v1/messages', {'model': 'allowed', 'max_tokens': 1, option: 'unbudgeted'}) == 400
        for tool in ({'type': 'web_search_20250305', 'name': 'web_search'}, {'type': 'computer_20250124', 'name': 'computer'}, 'invalid'):
            assert request('/v1/messages', {'model': 'allowed', 'max_tokens': 1, 'tools': [tool]}) == 400
        assert request('/v1/messages', '{"model":"other","model":"allowed","max_tokens":1}') == 400
        assert request('/v1/messages', '{"model":"allowed","max_tokens":1,"value":NaN}') == 400
        assert request('/v1/messages', {'model': 'allowed', 'max_tokens': 1}) == 502
        assert calls[0] == ('fixed.example', 443)
        assert calls[1][0:2] == ('POST', '/anthropic/v1/messages')
        assert request('/v1/messages', {'model': 'allowed', 'max_tokens': 1}) == 429
        assert len(calls) == 2
    finally:
        server.shutdown(); server.server_close(); thread.join(); os.unlink(path_socket)


def test_streamed_response_reaches_worker_before_upstream_finishes(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    first = b'data: first\n\n'
    release = threading.Event()

    class StreamingUpstream(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(first)
            self.wfile.flush()
            release.wait(5)

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), StreamingUpstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    monkeypatch.setattr(egress_proxy.http.client, 'HTTPSConnection', http.client.HTTPConnection)
    path = '/tmp/sf-' + next(tempfile._get_candidate_names()) + '.sock'
    request_budget = budget(str(tmp_path / 'budget.db'), 'a', 'model', 1, 'a'*64)
    server = egress_proxy.UnixServer(path, 'test-key', '127.0.0.1', upstream.server_port, 'model', request_budget)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = UnixHTTP(path)
    try:
        conn.request('POST', '/v1/messages', json.dumps({'model':'model', 'max_tokens':1, 'stream':True}))
        response = conn.getresponse()
        response.fp.raw._sock.settimeout(2)
        assert response.read(len(first)) == first
    finally:
        release.set()
        conn.close()
        server.shutdown(); server.server_close(); thread.join(); os.unlink(path)
        upstream.shutdown(); upstream.server_close(); upstream_thread.join()


def test_reservations_survive_reopen_and_reject_changed_configuration(tmp_path):
    path = str(tmp_path / 'budget.db')
    assert budget(path, 'a', 'model', 1, 'a'*64).reserve()
    assert not budget(path, 'a', 'model', 1, 'a'*64).reserve()
    with pytest.raises(egress_proxy.StoreQuarantined):
        budget(path, 'a', 'other', 1, 'a'*64)
    with pytest.raises(egress_proxy.StoreQuarantined):
        budget(path, 'a', 'model', 2, 'a'*64)
    with pytest.raises(egress_proxy.StoreQuarantined):
        budget(path, 'a', 'model', 1, 'b'*64)

def test_concurrent_processes_cannot_exceed_limit(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    path = str(tmp_path / 'budget.db')
    budget(path, 'a', 'model', 4, 'a'*64)
    with ProcessPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_reserve, [path] * 20))
    assert sum(results) == 4
    assert not budget(path, 'a', 'model', 4, 'a'*64).reserve()

def _reserve(path):
    return budget(path, 'a', 'model', 4, 'a'*64).reserve()

def test_killed_process_does_not_refund_reservation(tmp_path):
    import multiprocessing
    path = str(tmp_path / 'budget.db')
    ready = multiprocessing.Event()
    process = multiprocessing.Process(target=_reserve_then_wait, args=(path, ready))
    process.start()
    try:
        assert ready.wait(10)
        process.kill()
        process.join(10)
        assert not budget(path, 'a', 'model', 1, 'a'*64).reserve()
    finally:
        if process.is_alive():
            process.kill()
            process.join()

def _reserve_then_wait(path, ready):
    import time
    assert budget(path, 'a', 'model', 1, 'a'*64).reserve()
    ready.set()
    time.sleep(30)


def test_monetary_ceiling_and_policy_persist(tmp_path):
    path = str(tmp_path / 'budget.db')
    b = budget(path, 'a', 'model', 10, 'a'*64, ceiling_microdollars=25)
    assert b.reserve() and b.reserve() and not b.reserve()
    assert not budget(path, 'a', 'model', 10, 'a'*64, ceiling_microdollars=25).reserve()
    with pytest.raises(egress_proxy.StoreQuarantined):
        budget(path, 'a', 'model', 10, 'a'*64, ceiling_microdollars=100)
    with pytest.raises(ValueError):
        budget(str(tmp_path / 'expired'), 'b', 'model', 1, 'b'*64, expires_at=int(time.time())-1)


def test_approved_two_dollar_maximum(tmp_path):
    path = str(tmp_path / 'budget.db')
    b = budget(path, 'a', 'model', 10, 'a'*64,
               ceiling_microdollars=2_000_000, reserve_microdollars=1_000_000)
    assert b.reserve() and b.reserve() and not b.reserve()
    assert not budget(path, 'a', 'model', 10, 'a'*64,
                      ceiling_microdollars=2_000_000, reserve_microdollars=1_000_000).reserve()
    with pytest.raises(ValueError):
        budget(str(tmp_path / 'above.db'), 'b', 'model', 1, 'b'*64,
               ceiling_microdollars=2_000_001)


def test_legacy_counter_fails_closed(tmp_path):
    import sqlite3
    path = str(tmp_path / 'legacy.db')
    anchor_dir = tmp_path / 'anchors'
    anchor_dir.mkdir()
    egress_proxy.GuardedAttemptStore.initialize(Path(path), anchor_dir / 'legacy.db.anchor')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE budgets (attempt TEXT PRIMARY KEY, model TEXT, request_limit INTEGER, used INTEGER, fingerprint TEXT)')
        db.execute('INSERT INTO budgets VALUES (?, ?, ?, ?, ?)', ('a', 'model', 10, 1, 'a'*64))
    with pytest.raises(egress_proxy.StoreQuarantined):
        budget(path, 'a', 'model', 10, 'a'*64)


def _reserve_money(path):
    return budget(path, 'money', 'model', 20, 'c'*64,
                  ceiling_microdollars=25).reserve()


def test_concurrent_money_reservations_never_exceed_ceiling(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    path = str(tmp_path / 'budget.db')
    budget(path, 'money', 'model', 20, 'c'*64, ceiling_microdollars=25)
    with ProcessPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(_reserve_money, [path] * 20)) == 2


def test_restored_old_database_is_quarantined(tmp_path):
    import shutil
    path = str(tmp_path / 'budget.db')
    b = budget(path, 'a', 'model', 5, 'a'*64)
    old = tmp_path / 'snapshot'
    shutil.copyfile(path, old)
    assert b.reserve()
    shutil.copyfile(old, path)
    with pytest.raises(egress_proxy.StoreQuarantined):
        b.reserve()


def test_failed_anchor_update_quarantines_committed_reservation(tmp_path, monkeypatch):
    path = str(tmp_path / 'budget.db')
    b = budget(path, 'a', 'model', 5, 'a'*64)
    def fail(*args):
        raise OSError('simulated crash after commit')
    monkeypatch.setattr(b.store, '_write_anchor', fail)
    with pytest.raises(egress_proxy.StoreQuarantined):
        b.reserve()
    with pytest.raises(egress_proxy.StoreQuarantined):
        b.reserve()


def test_key_not_persisted(tmp_path):
    path = str(tmp_path / 'budget.db')
    b = budget(path, 'a', 'model', 1, 'a'*64)
    server = egress_proxy.UnixServer('/tmp/sf-' + next(tempfile._get_candidate_names()) + '.sock', 'secret-not-persisted', 'example.com', 443, 'model', b)
    try:
        assert b.reserve()
        for f in tmp_path.rglob('*'):
            if f.is_file():
                assert b'secret-not-persisted' not in f.read_bytes()
    finally:
        socket_path = server.server_address
        server.server_close()
        os.unlink(socket_path)


def test_missing_counter_is_not_reinitialized(tmp_path):
    path = str(tmp_path / 'budget.db')
    existing = budget(path, 'a', 'model', 2, 'a' * 64)
    assert existing.reserve()
    with existing.store._transaction() as (db, state):
        db.execute("DELETE FROM budgets WHERE attempt='a'")
        state['changed'] = True
    with pytest.raises(egress_proxy.StoreQuarantined):
        budget(path, 'a', 'model', 2, 'a' * 64)


def test_proxy_uses_pinned_transport_when_configured(tmp_path, monkeypatch):
    import pinned_https
    calls = []
    class Response:
        status = 401
        def getheader(self, name, default): return default
        def read1(self, size): return b''
    class Upstream:
        def __init__(self, host, port, *, gateway_ip, timeout): calls.append((host, port, gateway_ip))
        def request(self, *args): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(pinned_https, 'PinnedHTTPSConnection', Upstream)
    path = '/tmp/sf-' + next(tempfile._get_candidate_names()) + '.sock'
    request_budget = budget(str(tmp_path/'budget.db'), 'a', 'model', 1, 'a'*64)
    server = egress_proxy.UnixServer(path, 'test-key', 'gateway.example', 443, 'model', request_budget, gateway_ip='100.92.236.42')
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        conn = UnixHTTP(path)
        conn.request('POST', '/v1/messages', json.dumps({'model':'model', 'max_tokens':1}))
        assert conn.getresponse().status == 401
        assert calls == [('gateway.example', 443, '100.92.236.42')]
        conn.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(); os.unlink(path)
