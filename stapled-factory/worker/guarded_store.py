"""Attempt ledger with an independent monotonic watermark.

A mismatch is quarantined, including a crash between ledger commit and anchor
update. The anchor must be stored and backed up independently of the ledger;
restoring both to the same old state cannot be detected by this mechanism.
"""
import fcntl
import json
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path


class StoreQuarantined(RuntimeError):
    pass


class GuardedAttemptStore:
    def __init__(self, db_path: Path, anchor_path: Path):
        self.db_path, self.anchor_path = Path(db_path), Path(anchor_path)
        if not self.db_path.is_absolute() or not self.anchor_path.is_absolute():
            raise ValueError('absolute paths required')
        if self.db_path.parent.resolve() == self.anchor_path.parent.resolve():
            raise ValueError('anchor must be outside ledger directory')
        self.lock_path = self.anchor_path.with_name(self.anchor_path.name + '.lock')

    @classmethod
    def initialize(cls, db_path: Path, anchor_path: Path):
        store = cls(db_path, anchor_path)
        if store.db_path.exists() or store.anchor_path.exists():
            raise StoreQuarantined('initialization refuses existing state')
        if not store.db_path.parent.is_dir() or not store.anchor_path.parent.is_dir():
            raise ValueError('independent parent directories must exist')
        fd = os.open(store.db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        identity = str(uuid.uuid4())
        try:
            with sqlite3.connect(store.db_path) as db:
                db.execute('PRAGMA synchronous=FULL')
                db.execute('CREATE TABLE metadata (store_id TEXT NOT NULL, generation INTEGER NOT NULL)')
                db.execute('CREATE TABLE attempts (attempt_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, conversation_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, result TEXT)')
                db.execute('INSERT INTO metadata VALUES (?,0)', (identity,))
            store._write_anchor(identity, 0)
        except Exception:
            # Preserve partial state for inspection; never silently reinitialize.
            raise
        return store

    @contextmanager
    def _locked(self):
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write_anchor(self, identity, generation):
        fd, tmp = tempfile.mkstemp(prefix='.factory-anchor-', dir=self.anchor_path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'store_id': identity, 'generation': generation}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.anchor_path)
            dfd = os.open(self.anchor_path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @contextmanager
    def _transaction(self):
        with self._locked():
            if not self.db_path.is_file() or not self.anchor_path.is_file():
                raise StoreQuarantined('ledger or anchor missing')
            try:
                anchor = json.loads(self.anchor_path.read_text())
                db = sqlite3.connect(f'file:{self.db_path}?mode=rw', uri=True, timeout=10, isolation_level=None)
                try:
                    db.execute('PRAGMA synchronous=FULL')
                    db.execute('BEGIN IMMEDIATE')
                    rows = db.execute('SELECT store_id, generation FROM metadata').fetchall()
                    if len(rows) != 1 or anchor != {'store_id': rows[0][0], 'generation': rows[0][1]}:
                        raise StoreQuarantined('ledger identity or watermark mismatch')
                    state = {'identity': rows[0][0], 'generation': rows[0][1], 'changed': False}
                    yield db, state
                    if state['changed']:
                        state['generation'] += 1
                        db.execute('UPDATE metadata SET generation=?', (state['generation'],))
                    db.execute('COMMIT')
                    if state['changed']:
                        self._write_anchor(state['identity'], state['generation'])
                finally:
                    db.close()
            except (sqlite3.Error, OSError, ValueError, KeyError) as error:
                raise StoreQuarantined('ledger or watermark unavailable') from error

    def reserve(self, attempt_id, request_hash, conversation_id):
        with self._transaction() as (db, state):
            row = db.execute('SELECT request_hash, conversation_id, status, result FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row:
                if row[:2] != (request_hash, conversation_id):
                    raise ValueError('attempt identity mismatch')
                return ('completed', row[3]) if row[2] == 'completed' else ('unknown', None)
            db.execute("INSERT INTO attempts VALUES (?,?,?,'reserved',NULL)", (attempt_id, request_hash, conversation_id))
            state['changed'] = True
            return 'new', None

    def lookup(self, attempt_id, request_hash, conversation_id):
        """Observe an existing reservation without creating one."""
        with self._transaction() as (db, _state):
            row = db.execute('SELECT request_hash, conversation_id, status, result FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if not row:
                return 'missing', None
            if row[:2] != (request_hash, conversation_id):
                raise ValueError('attempt identity mismatch')
            return ('completed', row[3]) if row[2] == 'completed' else ('unknown', None)

    def complete(self, attempt_id, result):
        with self._transaction() as (db, state):
            has_fences = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fence_intents'").fetchone()
            if has_fences and db.execute('SELECT 1 FROM fence_intents WHERE attempt_id=?', (attempt_id,)).fetchone():
                raise StoreQuarantined('fenced attempt cannot complete')
            row = db.execute('SELECT status, result FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if not row or row[0] not in ('reserved', 'completed') or (row[0] == 'completed' and row[1] != result):
                raise ValueError('missing attempt or conflicting result')
            if row[0] != 'completed':
                db.execute("UPDATE attempts SET status='completed', result=? WHERE attempt_id=?", (result, attempt_id))
                state['changed'] = True

    def begin_fencing(self, attempt_id, request_hash, conversation_id, specification_hash, allocation_id, worker_id):
        """Irreversibly reject completion before stopping a bound uncertain worker."""
        with self._transaction() as (db, state):
            attempt = db.execute('SELECT request_hash, conversation_id, status FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if attempt != (request_hash, conversation_id, 'reserved') or not {'allocations', 'allocation_bindings'} <= tables:
                raise StoreQuarantined('attempt is not fenceable')
            row = db.execute('SELECT a.specification_hash, a.allocation_id, b.binding FROM allocations a JOIN allocation_bindings b ON a.attempt_id=b.attempt_id WHERE a.attempt_id=?', (attempt_id,)).fetchone()
            if not row or row[:2] != (specification_hash, allocation_id) or json.loads(row[2]).get('worker_id') != worker_id:
                raise StoreQuarantined('fence binding mismatch')
            db.execute('CREATE TABLE IF NOT EXISTS fence_intents (attempt_id TEXT PRIMARY KEY)')
            if not db.execute('SELECT 1 FROM fence_intents WHERE attempt_id=?', (attempt_id,)).fetchone():
                db.execute('INSERT INTO fence_intents VALUES (?)', (attempt_id,))
                state['changed'] = True

    def confirm_fencing(self, attempt_id, request_hash, conversation_id, specification_hash, allocation_id, worker_id):
        """Record verified stop, not permission to restart or discard evidence."""
        with self._transaction() as (db, state):
            attempt = db.execute('SELECT request_hash, conversation_id, status FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if attempt != (request_hash, conversation_id, 'reserved') or not {'allocations', 'allocation_bindings', 'fence_intents'} <= tables:
                raise StoreQuarantined('fence intent missing')
            row = db.execute('SELECT a.specification_hash, a.allocation_id, b.binding FROM allocations a JOIN allocation_bindings b ON a.attempt_id=b.attempt_id WHERE a.attempt_id=?', (attempt_id,)).fetchone()
            if (not row or row[:2] != (specification_hash, allocation_id)
                    or json.loads(row[2]).get('worker_id') != worker_id
                    or not db.execute('SELECT 1 FROM fence_intents WHERE attempt_id=?', (attempt_id,)).fetchone()):
                raise StoreQuarantined('fence identity mismatch')
            db.execute('CREATE TABLE IF NOT EXISTS fence_confirmations (attempt_id TEXT PRIMARY KEY)')
            if not db.execute('SELECT 1 FROM fence_confirmations WHERE attempt_id=?', (attempt_id,)).fetchone():
                db.execute('INSERT INTO fence_confirmations VALUES (?)', (attempt_id,))
                state['changed'] = True

    def reserve_fenced_restart(self, attempt_id, request_hash, conversation_id, specification_hash,
                               restart_id, restart_hash, restart_conversation_id):
        """Reserve one successor identity; this never authorizes provisioning or dispatch.

        Reuse of the original budget and stopped worker must be revalidated by
        the trusted runtime at dispatch time.
        """
        if (not all(isinstance(x, str) and x for x in (restart_id, restart_hash, restart_conversation_id))
                or restart_id == attempt_id or restart_conversation_id == conversation_id or restart_hash != request_hash):
            raise StoreQuarantined('invalid restart identity')
        with self._transaction() as (db, state):
            attempt = db.execute('SELECT request_hash, conversation_id, status FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if attempt != (request_hash, conversation_id, 'reserved') or not {'allocations', 'allocation_bindings', 'fence_intents', 'fence_confirmations'} <= tables:
                raise StoreQuarantined('confirmed fence required')
            row = db.execute('SELECT a.specification_hash, a.allocation_id, b.binding FROM allocations a JOIN allocation_bindings b ON a.attempt_id=b.attempt_id WHERE a.attempt_id=?', (attempt_id,)).fetchone()
            if (not row or row[0] != specification_hash
                    or not db.execute('SELECT 1 FROM fence_intents WHERE attempt_id=?', (attempt_id,)).fetchone()
                    or not db.execute('SELECT 1 FROM fence_confirmations WHERE attempt_id=?', (attempt_id,)).fetchone()):
                raise StoreQuarantined('fenced allocation unavailable')
            binding = json.loads(row[2])
            volumes = binding.get('volumes')
            prefix = 'factory-' + row[1]
            if (not re.fullmatch(r'[0-9a-f]{32}', row[1])
                    or not isinstance(binding.get('proxy_id'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', binding['proxy_id'])
                    or not isinstance(volumes, dict)):
                raise StoreQuarantined('original budget binding unavailable')
            for role in ('budget', 'anchor'):
                name = prefix + '-' + role
                volume = volumes.get(name) or {}
                labels = volume.get('Labels') or {}
                if (volume.get('Name') != name or volume.get('Driver') != 'local'
                        or labels.get('factory.allocation') != row[1] or labels.get('factory.role') != role):
                    raise StoreQuarantined('original budget binding unavailable')
            db.execute('CREATE TABLE IF NOT EXISTS restart_reservations (source_attempt TEXT PRIMARY KEY, restart_id TEXT NOT NULL UNIQUE, restart_hash TEXT NOT NULL, restart_conversation_id TEXT NOT NULL UNIQUE, allocation_id TEXT NOT NULL)')
            existing = db.execute('SELECT restart_id, restart_hash, restart_conversation_id, allocation_id FROM restart_reservations WHERE source_attempt=?', (attempt_id,)).fetchone()
            desired = (restart_id, restart_hash, restart_conversation_id, row[1])
            if existing:
                if existing != desired:
                    raise StoreQuarantined('restart already reserved with different identity')
                return 'existing'
            # A successor cannot enter the normal dispatch path until the runtime
            # inspects the bound successor and consumes its dispatch right.
            db.execute("INSERT INTO attempts VALUES (?,?,?,'restart_blocked',NULL)", (restart_id, restart_hash, restart_conversation_id))
            db.execute('INSERT INTO restart_reservations VALUES (?,?,?,?,?)', (attempt_id, *desired))
            state['changed'] = True
            return 'new'

    def claim_restart(self, source_attempt, restart_id, restart_hash, restart_conversation_id, workspace, baseline):
        """Consume the sole provisioning claim before Docker side effects."""
        if not isinstance(workspace, str) or not os.path.isabs(workspace) or not re.fullmatch(r'[0-9a-f]{40}([0-9a-f]{24})?', baseline or ''):
            raise StoreQuarantined('invalid successor workspace identity')
        with self._transaction() as (db, state):
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'restart_reservations' not in tables:
                raise StoreQuarantined('restart reservation unavailable')
            row = db.execute('SELECT restart_id, restart_hash, restart_conversation_id, allocation_id FROM restart_reservations WHERE source_attempt=?', (source_attempt,)).fetchone()
            if not row or row[:3] != (restart_id, restart_hash, restart_conversation_id):
                raise StoreQuarantined('restart reservation mismatch')
            db.execute('CREATE TABLE IF NOT EXISTS restart_claims (source_attempt TEXT PRIMARY KEY, workspace TEXT NOT NULL, baseline TEXT NOT NULL, worker_id TEXT, lifecycle TEXT)')
            if db.execute('SELECT 1 FROM restart_claims WHERE source_attempt=?', (source_attempt,)).fetchone():
                raise StoreQuarantined('restart provisioning already claimed')
            db.execute('INSERT INTO restart_claims VALUES (?,?,?,NULL,NULL)', (source_attempt, workspace, baseline))
            state['changed'] = True
            return row[3]

    def begin_restart_dispatch(self, source_attempt, restart_id, restart_hash, restart_conversation_id):
        """Consume the successor's sole dispatch right after its worker is bound."""
        with self._transaction() as (db, state):
            row = db.execute(
                'SELECT r.restart_hash, r.restart_conversation_id, c.worker_id, c.lifecycle, a.status '
                'FROM restart_reservations r JOIN restart_claims c ON r.source_attempt=c.source_attempt '
                'JOIN attempts a ON a.attempt_id=r.restart_id WHERE r.source_attempt=? AND r.restart_id=?',
                (source_attempt, restart_id)).fetchone()
            if not row or row[:2] != (restart_hash, restart_conversation_id) or not row[2] or not row[3]:
                raise StoreQuarantined('successor binding unavailable')
            if row[4] != 'restart_blocked':
                return False
            db.execute("UPDATE attempts SET status='reserved' WHERE attempt_id=?", (restart_id,))
            state['changed'] = True
            return True

    def bind_restart_worker(self, source_attempt, restart_id, workspace, baseline, worker_id, lifecycle):
        if not isinstance(worker_id, str) or not re.fullmatch(r'[0-9a-f]{64}', worker_id):
            raise StoreQuarantined('full worker identity required')
        with self._transaction() as (db, state):
            encoded = None if lifecycle is None else json.dumps(lifecycle, sort_keys=True, separators=(',', ':'), allow_nan=False)
            try:
                row = db.execute('SELECT r.restart_id, c.workspace, c.baseline, c.worker_id, c.lifecycle FROM restart_reservations r JOIN restart_claims c ON r.source_attempt=c.source_attempt WHERE r.source_attempt=?', (source_attempt,)).fetchone()
            except sqlite3.Error as exc:
                raise StoreQuarantined('restart claim unavailable') from exc
            if not row or row[:3] != (restart_id, workspace, baseline) or (row[3], row[4]) not in ((None, None), (worker_id, None), (worker_id, encoded)):
                raise StoreQuarantined('restart claim mismatch')
            if (row[3], row[4]) != (worker_id, encoded):
                if row[4] is not None:
                    raise StoreQuarantined('restart lifecycle mismatch')
                db.execute('UPDATE restart_claims SET worker_id=?, lifecycle=? WHERE source_attempt=?', (worker_id, encoded, source_attempt))
                state['changed'] = True

    def lookup_restart_worker(self, source_attempt, restart_id):
        with self._transaction() as (db, _state):
            try:
                row = db.execute('SELECT c.workspace, c.baseline, c.worker_id, c.lifecycle FROM restart_reservations r JOIN restart_claims c ON r.source_attempt=c.source_attempt WHERE r.source_attempt=? AND r.restart_id=?', (source_attempt, restart_id)).fetchone()
            except sqlite3.Error as exc:
                raise StoreQuarantined('restart binding unavailable') from exc
            if not row or not row[2] or not row[3]:
                raise StoreQuarantined('restart binding unavailable')
            return {'workspace': row[0], 'baseline': row[1], 'worker_id': row[2], 'lifecycle': json.loads(row[3])}

    def reserve_allocation(self, attempt_id, request_hash, conversation_id, specification_hash):
        """Reserve one immutable allocation before any Docker resource is created.

        An existing reservation is never permission to provision again: the first
        process could have died after creating resources or spending money.
        """
        with self._transaction() as (db, state):
            attempt = db.execute('SELECT request_hash, conversation_id, status FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if not attempt or attempt != (request_hash, conversation_id, 'reserved'):
                raise StoreQuarantined('attempt is missing, changed, or complete')
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='allocations'").fetchone()
            if not exists:
                db.execute('CREATE TABLE allocations (attempt_id TEXT PRIMARY KEY, specification_hash TEXT NOT NULL, allocation_id TEXT NOT NULL UNIQUE)')
                state['changed'] = True
            row = db.execute('SELECT specification_hash, allocation_id FROM allocations WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row:
                if row[0] != specification_hash:
                    raise StoreQuarantined('allocation specification mismatch')
                return 'existing', row[1]
            allocation_id = uuid.uuid4().hex
            db.execute('INSERT INTO allocations VALUES (?,?,?)', (attempt_id, specification_hash, allocation_id))
            state['changed'] = True
            return 'new', allocation_id

    def bind_allocation(self, attempt_id, request_hash, conversation_id, specification_hash, allocation_id, binding):
        """Persist immutable observed identities; a missing binding never authorizes adoption."""
        encoded = json.dumps(binding, sort_keys=True, separators=(',', ':'), allow_nan=False)
        with self._transaction() as (db, state):
            attempt = db.execute('SELECT request_hash, conversation_id, status FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            allocation = db.execute('SELECT specification_hash, allocation_id FROM allocations WHERE attempt_id=?', (attempt_id,)).fetchone()
            if attempt != (request_hash, conversation_id, 'reserved') or allocation != (specification_hash, allocation_id):
                raise StoreQuarantined('attempt or allocation identity mismatch')
            db.execute('CREATE TABLE IF NOT EXISTS allocation_bindings (attempt_id TEXT PRIMARY KEY, binding TEXT NOT NULL)')
            row = db.execute('SELECT binding FROM allocation_bindings WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row:
                if row[0] != encoded:
                    raise StoreQuarantined('allocation binding mismatch')
                return
            db.execute('INSERT INTO allocation_bindings VALUES (?,?)', (attempt_id, encoded))
            state['changed'] = True

    def lookup_allocation(self, attempt_id, request_hash, conversation_id, specification_hash):
        with self._transaction() as (db, _state):
            attempt = db.execute('SELECT request_hash, conversation_id, status FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if attempt != (request_hash, conversation_id, 'reserved') or not {'allocations', 'allocation_bindings'} <= tables:
                raise StoreQuarantined('allocation binding unavailable')
            row = db.execute('SELECT a.specification_hash, a.allocation_id, b.binding FROM allocations a JOIN allocation_bindings b ON a.attempt_id=b.attempt_id WHERE a.attempt_id=?', (attempt_id,)).fetchone()
            if not row or row[0] != specification_hash:
                raise StoreQuarantined('allocation specification mismatch')
            return row[1], json.loads(row[2])



def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('initialize', 'check'))
    parser.add_argument('--db', required=True)
    parser.add_argument('--anchor', required=True)
    args = parser.parse_args()
    try:
        store = GuardedAttemptStore(Path(args.db), Path(args.anchor))
        if args.action == 'initialize':
            GuardedAttemptStore.initialize(store.db_path, store.anchor_path)
        else:
            with store._transaction():
                pass
        print('store verified')
    except (StoreQuarantined, ValueError, OSError):
        parser.exit(1, 'store quarantined or invalid\n')


if __name__ == '__main__':
    main()
