"""Independent authority for pre-authorized per-attempt request reservations.

The database must be hosted in a failure domain protected from rollback separately
from proxy and worker storage. This process cannot detect restoration of its own
authoritative database. A privileged provisioner creates immutable policies;
clients may only reserve, never create or increase a policy.
"""
import hashlib
import hmac
import json
import os
import re
import socket
import socketserver
import sqlite3
import stat
import time
from pathlib import Path

MAX_CEILING = 2_000_000
IDENTIFIER = re.compile(r'^[A-Za-z0-9_.:-]{1,160}$')


class AuthorityError(RuntimeError):
    pass


class BudgetAuthority:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute() or not self.path.is_file():
            raise AuthorityError('authority database missing')
        self._connect().close()

    @classmethod
    def initialize(cls, path):
        path = Path(path)
        if not path.is_absolute():
            raise AuthorityError('absolute database path required')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(path) as db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE policies (attempt TEXT PRIMARY KEY, capability_hash BLOB NOT NULL, fingerprint TEXT NOT NULL, model TEXT NOT NULL, ceiling INTEGER NOT NULL, cost INTEGER NOT NULL, request_limit INTEGER NOT NULL, expires_at INTEGER NOT NULL, spent INTEGER NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0)')
        return cls(path)

    def _connect(self):
        db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, isolation_level=None, timeout=15)
        db.execute('PRAGMA synchronous=FULL')
        return db

    def provision(self, attempt, capability, fingerprint, model, ceiling, cost, request_limit, expires_at):
        self._validate_policy(attempt, capability, fingerprint, model, ceiling, cost, request_limit, expires_at)
        digest = hashlib.sha256(capability).digest()
        with self._connect() as db:
            db.execute('INSERT INTO policies (attempt, capability_hash, fingerprint, model, ceiling, cost, request_limit, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (attempt, digest, fingerprint, model, ceiling, cost, request_limit, expires_at))

    @staticmethod
    def _validate_policy(attempt, capability, fingerprint, model, ceiling, cost, request_limit, expires_at):
        if (not isinstance(attempt, str) or not IDENTIFIER.fullmatch(attempt)
                or not isinstance(capability, bytes) or len(capability) < 32
                or not isinstance(fingerprint, str) or not re.fullmatch(r'[a-f0-9]{64}', fingerprint)
                or not isinstance(model, str) or not IDENTIFIER.fullmatch(model)
                or type(ceiling) is not int or type(cost) is not int or not 0 < cost <= ceiling <= MAX_CEILING
                or type(request_limit) is not int or request_limit < 1
                or type(expires_at) is not int or expires_at <= int(time.time())):
            raise ValueError('invalid authority policy')

    def reserve(self, attempt, capability, fingerprint, model):
        if not isinstance(capability, bytes) or len(capability) < 32:
            return False
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT capability_hash, fingerprint, model, ceiling, cost, request_limit, expires_at, spent, used FROM policies WHERE attempt=?', (attempt,)).fetchone()
            if (row is None or not hmac.compare_digest(row[0], hashlib.sha256(capability).digest())
                    or row[1] != fingerprint or row[2] != model or int(time.time()) >= row[6]
                    or row[8] >= row[5] or row[7] > row[3] - row[4]):
                db.execute('ROLLBACK')
                return False
            db.execute('UPDATE policies SET spent=spent+cost, used=used+1 WHERE attempt=?', (attempt,))
            db.execute('COMMIT')
            return True
        finally:
            db.close()


class WitnessedBudgetAuthority(BudgetAuthority):
    """Fail closed if this database is restored behind an independent witness.

    The witness must implement atomic, durable ``compare_and_swap(key, old,
    new)``. It must authenticate this provisioner and must never accept a
    stale state after a restore. Its durability and isolation are external
    requirements, not properties supplied by this class. Never use the base
    BudgetAuthority to access the same database.

    Witness advancement precedes the SQLite commit. A crash or lost response
    in between can strand the database; it is deliberately not repaired
    automatically because the reservation may already have been acknowledged.
    """

    def __init__(self, path, witness, witness_key):
        if not witness_key or not isinstance(witness_key, str):
            raise AuthorityError('stable witness identity required')
        self.witness = witness
        self.witness_key = witness_key
        super().__init__(path)

    @classmethod
    def initialize(cls, path, witness, witness_key):
        # Creating a new database never resets an existing witness key.
        BudgetAuthority.initialize(path)
        authority = cls(path, witness, witness_key)
        db = authority._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            state = authority._state(db)
            if not witness.compare_and_swap(witness_key, None, state):
                raise AuthorityError('witness identity already used or unavailable')
            db.execute('COMMIT')
        finally:
            db.close()
        return authority

    @staticmethod
    def _state(db):
        # Schema objects are part of the witnessed state: a restored or
        # modified trigger must not be able to suppress a reservation update.
        schema = db.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        rows = db.execute('SELECT attempt, hex(capability_hash), fingerprint, model, ceiling, cost, request_limit, expires_at, spent, used FROM policies ORDER BY attempt').fetchall()
        return hashlib.sha256(json.dumps([schema, rows], separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()

    def _mutate(self, operation):
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            before = self._state(db)
            result = operation(db)
            after = self._state(db)
            # Even a denial must check that a stale database cannot be used.
            if not self.witness.compare_and_swap(self.witness_key, before, after):
                raise AuthorityError('witness mismatch or unavailable')
            db.execute('COMMIT')
            return result
        finally:
            db.close()

    def provision(self, attempt, capability, fingerprint, model, ceiling, cost, request_limit, expires_at):
        self._validate_policy(attempt, capability, fingerprint, model, ceiling, cost, request_limit, expires_at)
        digest = hashlib.sha256(capability).digest()
        def insert(db):
            db.execute('INSERT INTO policies (attempt, capability_hash, fingerprint, model, ceiling, cost, request_limit, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (attempt, digest, fingerprint, model, ceiling, cost, request_limit, expires_at))
        return self._mutate(insert)

    def reserve(self, attempt, capability, fingerprint, model):
        if not isinstance(capability, bytes) or len(capability) < 32:
            return False
        def reserve_one(db):
            row = db.execute('SELECT capability_hash, fingerprint, model, ceiling, cost, request_limit, expires_at, spent, used FROM policies WHERE attempt=?', (attempt,)).fetchone()
            if (row is None or not hmac.compare_digest(row[0], hashlib.sha256(capability).digest())
                    or row[1] != fingerprint or row[2] != model or int(time.time()) >= row[6]
                    or row[8] >= row[5] or row[7] > row[3] - row[4]):
                return False
            db.execute('UPDATE policies SET spent=spent+cost, used=used+1 WHERE attempt=?', (attempt,))
            return True
        return self._mutate(reserve_one)


class AuthorityClient:
    """One synchronous reservation per request; ambiguous failures never retry."""
    def __init__(self, socket_path, attempt, capability, fingerprint, model, timeout=5):
        self.socket_path, self.attempt, self.capability = str(socket_path), attempt, capability
        self.fingerprint, self.model, self.timeout = fingerprint, model, timeout

    def reserve(self):
        import json
        request = json.dumps({'attempt': self.attempt, 'capability': self.capability.hex(), 'fingerprint': self.fingerprint, 'model': self.model}, separators=(',', ':')).encode() + b'\n'
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout)
                connection.connect(self.socket_path)
                connection.sendall(request)
                reply = b''
                while not reply.endswith(b'\n') and len(reply) < 32:
                    chunk = connection.recv(32 - len(reply))
                    if not chunk:
                        break
                    reply += chunk
                return reply == b'allow\n'
        except (OSError, ValueError):
            return False


class _Handler(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def handle(self):
        import json
        try:
            raw = self.rfile.readline(4097)
            if len(raw) > 4096 or not raw.endswith(b'\n'):
                return
            request = json.loads(raw)
            if set(request) != {'attempt', 'capability', 'fingerprint', 'model'}:
                return
            capability = bytes.fromhex(request['capability'])
            allowed = self.server.authority.reserve(request['attempt'], capability, request['fingerprint'], request['model'])
            self.wfile.write(b'allow\n' if allowed else b'deny\n')
        except (ValueError, TypeError, KeyError, sqlite3.Error, OSError, AuthorityError):
            return


class AuthorityServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, path, authority):
        self.authority = authority
        # Never unlink an existing socket: another authority may own it.
        super().__init__(str(path), _Handler)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
