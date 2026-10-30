"""Operator-only abandonment of an already stopped original allocation.

No stop, restart, deletion, budget refund or successful-result fabrication occurs.
The guarded transaction fences late completion before recording abandonment.
"""
import argparse
import json
import re
import subprocess
import sys
from guarded_store import GuardedAttemptStore


def stopped(allocation, binding):
    for role in ('worker', 'proxy'):
        identity = binding[role + '_id']
        if not re.fullmatch(r'[0-9a-f]{64}', identity):
            raise ValueError('invalid container identity')
        info = json.loads(subprocess.check_output(['docker', 'inspect', identity], text=True, timeout=15))[0]
        labels = info.get('Config', {}).get('Labels') or {}
        state = info.get('State') or {}
        if (info.get('Id') != identity or labels.get('factory.allocation') != allocation or
                labels.get('factory.role') != role or
                (info.get('HostConfig', {}).get('RestartPolicy') or {}).get('Name') != 'no' or
                state.get('Running') is not False or state.get('Restarting') is not False or
                state.get('Paused') is not False or
                state.get('StartedAt') != binding[role + '_lifecycle']['StartedAt']):
            raise ValueError('allocation must be stopped with its original identity and no restart policy')
    if not isinstance(binding.get('volumes'), dict) or not binding['volumes']:
        raise ValueError('retained volume bindings required')
    for name, expected in binding['volumes'].items():
        info = json.loads(subprocess.check_output(['docker', 'volume', 'inspect', name], text=True, timeout=15))[0]
        if any(info.get(k) != expected.get(k) for k in ('Name', 'CreatedAt', 'Driver', 'Labels')):
            raise ValueError('retained volume identity changed')


def operation(store, unit, reason, payload=None):
    if (not isinstance(unit, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', unit) or
            not isinstance(reason, str) or not reason.strip() or len(reason) > 500):
        raise ValueError('bounded unit and abandonment reason required')
    attempt = unit + ':0'
    with store._transaction() as (db, state):
        row = db.execute('SELECT request_hash,conversation_id,status FROM attempts WHERE attempt_id=?', (attempt,)).fetchone()
        tables = {x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not row or row[2] not in ('reserved', 'abandoned') or not {'allocations', 'allocation_bindings'} <= tables:
            raise ValueError('only an unresolved allocated original attempt may be abandoned')
        if 'restart_reservations' in tables and db.execute('SELECT 1 FROM restart_reservations WHERE source_attempt=?', (attempt,)).fetchone():
            raise ValueError('successor already reserved; manual reconciliation required')
        alloc = db.execute('SELECT specification_hash,allocation_id FROM allocations WHERE attempt_id=?', (attempt,)).fetchone()
        bound = db.execute('SELECT binding FROM allocation_bindings WHERE attempt_id=?', (attempt,)).fetchone()
        if not alloc or not bound or not re.fullmatch(r'[0-9a-f]{32}', alloc[1]):
            raise ValueError('allocation binding unavailable')
        binding = json.loads(bound[0])
        stopped(alloc[1], binding)
        if row[2] == 'abandoned':
            saved = db.execute('SELECT payload FROM abandonments WHERE attempt_id=?', (attempt,)).fetchone()[0]
            if json.loads(saved)['reason'] != reason or (payload is not None and saved != payload):
                raise ValueError('conflicting abandonment')
            return saved
        evidence = dict(kind='operator-abandon-v1', unit_id=unit, attempt_id=attempt,
                        conversation_id=row[1], request_hash=row[0], specification_hash=alloc[0],
                        allocation_id=alloc[1], worker_id=binding['worker_id'], proxy_id=binding['proxy_id'],
                        store_id=state['identity'], generation=state['generation'], reason=reason)
        expected = json.dumps(evidence, sort_keys=True, separators=(',', ':'))
        if payload is None:
            return expected
        if payload != expected:
            raise ValueError('stale or mismatched abandonment evidence; prepare again')
        db.execute('CREATE TABLE IF NOT EXISTS fence_intents (attempt_id TEXT PRIMARY KEY)')
        db.execute('INSERT OR IGNORE INTO fence_intents VALUES (?)', (attempt,))
        db.execute('CREATE TABLE IF NOT EXISTS abandonments (attempt_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        db.execute('INSERT INTO abandonments VALUES (?,?)', (attempt, payload))
        db.execute("UPDATE attempts SET status='abandoned' WHERE attempt_id=?", (attempt,))
        state['changed'] = True
        return payload


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('db', 'anchor', 'unit', 'reason'):p.add_argument('--' + name, required=True)
    p.add_argument('--commit', action='store_true')
    a = p.parse_args()
    payload = sys.stdin.read(16385) if a.commit else None
    if payload is not None and len(payload) > 16384:raise ValueError('oversized abandonment')
    print(operation(GuardedAttemptStore(a.db, a.anchor), a.unit, a.reason, payload))


if __name__ == '__main__':main()
