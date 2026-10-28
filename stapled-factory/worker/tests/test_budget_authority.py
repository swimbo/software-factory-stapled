import os
import sqlite3
import sys
import threading
import time
import tempfile
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from budget_authority import AuthorityClient, AuthorityError, AuthorityServer, BudgetAuthority, WitnessedBudgetAuthority


@pytest.fixture
def setup(tmp_path):
    authority = BudgetAuthority.initialize(tmp_path / 'authority.db')
    capability = os.urandom(32)
    fingerprint = 'a' * 64
    authority.provision('attempt', capability, fingerprint, 'model', 2_000_000, 1_000_000, 10, int(time.time()) + 3600)
    sock_dir = Path(tempfile.mkdtemp(prefix='ba-', dir='/tmp'))
    server = AuthorityServer(sock_dir / 'a.sock', authority)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = AuthorityClient(sock_dir / 'a.sock', 'attempt', capability, fingerprint, 'model')
    yield authority, client, tmp_path
    server.shutdown()
    server.server_close()
    thread.join()
    shutil.rmtree(sock_dir)


def test_two_successors_share_atomic_authority(setup):
    authority, client, path = setup
    successor = AuthorityClient(client.socket_path, client.attempt, client.capability, client.fingerprint, client.model)
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda i: (client if i % 2 else successor).reserve(), range(20)))
    assert sum(results) == 2
    assert not client.reserve()


def test_mismatched_capability_or_identity_cannot_spend(setup):
    authority, client, path = setup
    for capability, fingerprint, model in [(os.urandom(32), client.fingerprint, client.model), (client.capability, 'b' * 64, client.model), (client.capability, client.fingerprint, 'other')]:
        assert not AuthorityClient(client.socket_path, 'attempt', capability, fingerprint, model).reserve()
    assert client.reserve()


def test_proxy_local_rollback_cannot_reset_independent_authority(setup):
    authority, client, path = setup
    local = path / 'proxy-state'
    local.write_text('old')
    assert client.reserve() and client.reserve()
    local.write_text('old')
    assert not client.reserve()


def test_policy_cannot_be_recreated_or_increased(setup):
    authority, client, _ = setup
    with pytest.raises(sqlite3.IntegrityError):
        authority.provision('attempt', client.capability, client.fingerprint, client.model, 2_000_000, 1, 100, int(time.time()) + 3600)
    with pytest.raises(ValueError):
        authority.provision('second', client.capability, client.fingerprint, client.model, 2_000_001, 1, 100, int(time.time()) + 3600)


def test_missing_authority_denies_without_recreating(tmp_path):
    with pytest.raises(AuthorityError):
        BudgetAuthority(tmp_path / 'missing')
    assert not AuthorityClient(tmp_path / 'missing.sock', 'a', os.urandom(32), 'a' * 64, 'm').reserve()
    assert not (tmp_path / 'missing').exists()


def test_lost_reply_consumes_reservation_without_automatic_retry(setup):
    authority, client, _ = setup
    # An upstream connection may fail after commit and before acknowledgment.
    # The caller must treat this as spent, not repeat it automatically.
    assert authority.reserve(client.attempt, client.capability, client.fingerprint, client.model)
    assert client.reserve()
    assert not client.reserve()


def test_restoring_authority_itself_is_not_protected(setup):
    authority, client, path = setup
    # This documents the separate operational requirement: restoring the
    # authority along with the proxy makes the older budget valid again.
    backup = path / 'snapshot.db'
    with sqlite3.connect(authority.path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    assert client.reserve() and client.reserve()
    with sqlite3.connect(backup) as source, sqlite3.connect(authority.path) as target:
        source.backup(target)
    assert client.reserve()


class MemoryWitness:
    # Test double only: production requires a separately protected durable witness.
    def __init__(self):
        self.values = {}
        self.lock = threading.Lock()
        self.fail_after_write = False

    def compare_and_swap(self, key, expected, value):
        with self.lock:
            if self.values.get(key) != expected:
                return False
            self.values[key] = value
            if self.fail_after_write:
                raise OSError('simulated lost acknowledgment')
            return True


def test_witness_rejects_restored_authority_and_reinitialization(tmp_path):
    witness = MemoryWitness()
    authority = WitnessedBudgetAuthority.initialize(tmp_path / 'guarded.db', witness, 'allocated-once')
    key = os.urandom(32)
    authority.provision('attempt', key, 'a' * 64, 'model', 2_000_000, 1_000_000, 2, int(time.time()) + 60)
    snapshot = tmp_path / 'snapshot.db'
    with sqlite3.connect(authority.path) as source, sqlite3.connect(snapshot) as target:
        source.backup(target)
    assert authority.reserve('attempt', key, 'a' * 64, 'model')
    with sqlite3.connect(snapshot) as source, sqlite3.connect(authority.path) as target:
        source.backup(target)
    with pytest.raises(AuthorityError):
        authority.reserve('attempt', key, 'a' * 64, 'model')
    with pytest.raises(AuthorityError):
        WitnessedBudgetAuthority.initialize(tmp_path / 'new.db', witness, 'allocated-once')


def test_witness_lost_acknowledgment_strands_authority(tmp_path):
    witness = MemoryWitness()
    authority = WitnessedBudgetAuthority.initialize(tmp_path / 'guarded.db', witness, 'one')
    key = os.urandom(32)
    authority.provision('attempt', key, 'a' * 64, 'model', 2_000_000, 1_000_000, 2, int(time.time()) + 60)
    witness.fail_after_write = True
    with pytest.raises(OSError):
        authority.reserve('attempt', key, 'a' * 64, 'model')
    witness.fail_after_write = False
    with pytest.raises(AuthorityError):
        authority.reserve('attempt', key, 'a' * 64, 'model')


def test_witness_rejects_trigger_that_suppresses_charges(tmp_path):
    witness = MemoryWitness()
    authority = WitnessedBudgetAuthority.initialize(tmp_path / 'guarded.db', witness, 'one')
    key = os.urandom(32)
    authority.provision('attempt', key, 'a' * 64, 'model', 2_000_000, 1_000_000, 2, int(time.time()) + 60)
    with sqlite3.connect(authority.path) as db:
        db.execute('CREATE TRIGGER suppress BEFORE UPDATE ON policies BEGIN SELECT RAISE(IGNORE); END')
    with pytest.raises(AuthorityError):
        authority.reserve('attempt', key, 'a' * 64, 'model')


def test_witness_prevents_double_spend_from_copied_databases(tmp_path):
    witness = MemoryWitness()
    authority = WitnessedBudgetAuthority.initialize(tmp_path / 'first.db', witness, 'one')
    key = os.urandom(32)
    authority.provision('attempt', key, 'a' * 64, 'model', 2_000_000, 1_000_000, 2, int(time.time()) + 60)
    other_path = tmp_path / 'second.db'
    with sqlite3.connect(authority.path) as source, sqlite3.connect(other_path) as target:
        source.backup(target)
    other = WitnessedBudgetAuthority(other_path, witness, 'one')
    def reserve(a):
        try:
            return a.reserve('attempt', key, 'a' * 64, 'model')
        except AuthorityError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(reserve, [authority, other])) == 1


def test_real_proxy_restart_and_local_rollback_cannot_reopen_authority(setup):
    import http.client
    import json
    import socket
    import subprocess
    import sys
    authority, client, path = setup
    # The authority's socket and the worker-facing socket use separate dirs.
    sock_dir = Path(tempfile.mkdtemp(prefix='px-', dir='/tmp'))
    proxy_path = sock_dir / 'proxy.sock'
    script = Path(__file__).resolve().parents[1] / 'egress_proxy.py'
    local_state = path / 'proxy-state'
    local_state.write_text('old')
    args = [sys.executable, str(script), '--socket', str(proxy_path), '--authority-socket', client.socket_path,
            '--model', client.model, '--max-requests', '10', '--attempt-id', client.attempt,
            '--attempt-fingerprint', client.fingerprint, '--worst-case-request-microdollars', '1000000',
            '--pricing-policy-id', 'synthetic', '--pricing-policy-expires-at', '4102444800']

    class UnixHTTP(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(10)
            self.sock.connect(str(proxy_path))

    def run_proxy():
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.stdin.write(b'dummy-key\n' + client.capability.hex().encode() + b'\n')
        process.stdin.flush()
        for _ in range(100):
            if proxy_path.exists() or process.poll() is not None:
                break
            time.sleep(.02)
        assert proxy_path.exists(), 'proxy failed to start'
        return process

    def send():
        c = UnixHTTP('localhost')
        raw = json.dumps({'model': client.model, 'max_tokens': 1, 'messages': []})
        c.request('POST', '/v1/messages', raw, {'Content-Type': 'application/json'})
        try:
            response = c.getresponse()
            status = response.status
            response.read()
            return status
        except (http.client.RemoteDisconnected, OSError):
            return 0
        finally:
            c.close()

    process = None
    try:
        process = run_proxy()
        assert send() != 429
        with sqlite3.connect(authority.path) as db:
            assert db.execute("SELECT used FROM policies WHERE attempt='attempt'").fetchone() == (1,)
        process.terminate(); process.wait(timeout=10); process = None
        proxy_path.unlink(missing_ok=True)
        local_state.write_text('old')
        process = run_proxy()
        assert send() != 429
        with sqlite3.connect(authority.path) as db:
            assert db.execute("SELECT used FROM policies WHERE attempt='attempt'").fetchone() == (2,)
        assert send() == 429
    finally:
        if process:
            process.terminate(); process.wait(timeout=10)
        shutil.rmtree(sock_dir)
