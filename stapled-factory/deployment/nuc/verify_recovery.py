"""Read back an authenticated cold-restored joined recovery fixture, without dispatch.

Run against a disposable restored copy, never the active deployment. Restate must
use its original node name; all listeners use random loopback ports.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import socket


def request(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={'content-type': 'application/json', 'accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=3) as response:
        return json.loads(response.read())


def verify(root, restate_data, node_name, worker_source):
    sys.path.insert(0, str(worker_source))
    from guarded_store import GuardedAttemptStore
    db = sqlite3.connect(root / 'db')
    completed = db.execute('select attempt_id,request_hash,conversation_id,result from attempts where status="completed"').fetchall()
    assert len(completed) == 1
    attempt, digest, conversation, raw = completed[0]
    store = GuardedAttemptStore(root / 'db', root / 'anchor/file')
    assert store.lookup(attempt, digest, conversation) == ('completed', raw)
    result = json.loads(raw)
    revision = subprocess.check_output(['git', '-C', str(root / 'successor-workspace'), 'rev-parse', 'HEAD'], text=True).strip()
    assert revision == result['git_revision']
    for workspace in ('workspace', 'successor-workspace'):
        subprocess.run(['git', '-C', str(root / workspace), 'fsck', '--full'], check=True, capture_output=True)
    ledger = root / 'budget.db'
    ledger_before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    budget = sqlite3.connect('file:' + str(ledger) + '?mode=ro', uri=True)
    assert budget.execute('pragma integrity_check').fetchone()[0] == 'ok'
    metadata = budget.execute('select store_id,generation from metadata').fetchall()
    assert len(metadata) == 1
    assert json.loads((root / 'budget-anchor.json').read_text()) == {'store_id': metadata[0][0], 'generation': metadata[0][1]}
    monetary = budget.execute('select attempt,spent,ceiling from monetary_budgets').fetchall()
    assert monetary == [('unit:0', 2000000, 2000000)]
    assert budget.execute('select attempt,used from budgets').fetchall() == [('unit:0', 5)]
    budget.close(); db.close()
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(['restate-server', '--base-dir', str(restate_data), '--node-name', node_name,
                                 '--use-random-ports', 'true', '--bind-ip', '127.0.0.1', '--listen-mode', 'tcp', '--no-logo'],
                                stdout=log, stderr=log, start_new_session=True)
        try:
            admin = None
            for _ in range(100):
                if proc.poll() is not None:
                    log.seek(0); raise RuntimeError(log.read().decode(errors='replace')[-3000:])
                children = subprocess.run(['pgrep', '-P', str(proc.pid)], capture_output=True, text=True).stdout.split()
                for pid in [str(proc.pid), *children]:
                    out = subprocess.run(['lsof','-nP','-a','-p',pid,'-iTCP','-sTCP:LISTEN','-Fn'],capture_output=True,text=True).stdout
                    for line in out.splitlines():
                        if line.startswith('n127.0.0.1:'):
                            try:
                                health = request('http://' + line[1:] + '/health')
                                if health.get('admin_status') == 'Ready':
                                    admin = 'http://' + next(a['address'] for a in health['bound_addresses'] if a['name'] == 'admin-api-server')
                            except Exception:
                                pass
                if admin:
                    break
                time.sleep(.2)
            if not admin:
                raise RuntimeError('restored Restate readiness timeout')
            # Admin health can precede partition availability. Retry only the
            # read-only transport/query availability failure, never bad state.
            for query_attempt in range(30):
                try:
                    response = request(admin + '/query', {'query': "SELECT value_utf8 FROM state WHERE service_name = 'WorkUnit' AND service_key = 'unit' AND key = 'state'"})
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code != 500 or query_attempt == 29:
                        raise
                except (urllib.error.URLError, TimeoutError, socket.timeout):
                    if query_attempt == 29:
                        raise
                time.sleep(.2)
            rows = response['rows']
            assert len(rows) == 1
            state = json.loads(rows[0]['value_utf8'])
            assert state['stage'] == 'needs_validation'
            assert state['successor']['git_revision'] == revision
            assert state['successor']['attempt_id'] == attempt
            assert hashlib.sha256(ledger.read_bytes()).hexdigest() == ledger_before
            return {'restored_state': state['stage'], 'revision': revision, 'ledger_unchanged': True, 'budget': monetary}
        finally:
            import os,signal
            try: os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGKILL); proc.wait()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture', required=True, type=Path)
    p.add_argument('--restate-data', required=True, type=Path)
    p.add_argument('--node-name', required=True)
    p.add_argument('--worker-source', required=True, type=Path)
    a = p.parse_args()
    print(json.dumps(verify(a.fixture.resolve(), a.restate_data.resolve(), a.node_name, a.worker_source.resolve())))
