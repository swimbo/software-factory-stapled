"""Real mutation and lost HTTP observation, without model/provider calls."""
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from egress_proxy import RequestBudget
from guarded_store import GuardedAttemptStore
from worker import execute_attempt


class LostObservationServer(HTTPServer):
    def handle_error(self, *_):
        pass


def test_partial_batch_mutation_lost_observation_and_coordinator_death_never_redispatch(tmp_path):
    worker_path = Path(__file__).resolve().parents[1]
    for name in ('attempts', 'attempt-anchor', 'budget', 'budget-anchor'):
        (tmp_path/name).mkdir(mode=0o700)
    db = tmp_path/'attempts/db'
    anchor = tmp_path/'attempt-anchor/anchor'
    budget_db = tmp_path/'budget/db'
    budget_anchor = tmp_path/'budget-anchor/anchor'
    GuardedAttemptStore.initialize(db, anchor)
    GuardedAttemptStore.initialize(budget_db, budget_anchor)
    counter = tmp_path/'counter'
    marker = tmp_path/'mutation-finished'
    counts = {'post': 0, 'get': 0}
    mutate = ("import os,sys; from pathlib import Path; p=Path(sys.argv[1]); "
              "n=int(p.read_text()) if p.exists() else 0; "
              "f=p.open('w'); f.write(str(n+1)); f.flush(); os.fsync(f.fileno()); f.close(); "
              "p=Path(sys.argv[2]); f=p.open('w'); f.write('mutation durable; observation absent'); "
              "f.flush(); os.fsync(f.fileno()); f.close()")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            counts['post'] += 1
            subprocess.run([sys.executable, '-c', mutate, str(counter), str(marker)], check=True)
            # Effects are durable, but the coordinator never receives an observation.
            self.close_connection = True
            self.connection.close()

        def do_GET(self):
            counts['get'] += 1
            body = json.dumps({'counter': int(counter.read_text()), 'observation_missing': True}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = LostObservationServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
    thread.start()
    url = 'http://127.0.0.1:' + str(server.server_address[1])
    request = {'task': 'mutate once, crash before observation'}
    policy = dict(anchor_path=str(budget_anchor), ceiling_microdollars=100,
                  reserve_microdollars=10, policy_id='synthetic-admission', expires_at=4102444800)
    fingerprint = hashlib.sha256(b'fixed-batch-profile').hexdigest()
    args = [str(db), str(anchor), str(budget_db), str(budget_anchor), url, fingerprint]
    program = '''import os,sys,json,urllib.request
sys.path.insert(0,sys.argv[1])
from guarded_store import GuardedAttemptStore
from egress_proxy import RequestBudget
from worker import execute_attempt
db,anchor,budget_db,budget_anchor,url,fingerprint=sys.argv[2:]
store=GuardedAttemptStore(db,anchor)
budget=RequestBudget(budget_db,'batch:0','fixture-model',3,fingerprint,anchor_path=budget_anchor,ceiling_microdollars=100,reserve_microdollars=10,policy_id='synthetic-admission',expires_at=4102444800)
def dispatch(cid,request):
 assert budget.reserve()
 req=urllib.request.Request(url,json.dumps(request).encode(),method='POST')
 with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
r=execute_attempt(store,'batch:0',{'task':'mutate once, crash before observation'},dispatch,lambda *_:None)
assert r['status']=='unknown'
os._exit(23)
'''
    try:
        completed = subprocess.run([sys.executable, '-c', program, str(worker_path), *args],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        assert completed.returncode == 23, completed.stderr.decode()
        assert marker.exists() and counter.read_text() == '1'
        assert counts['post'] == 1
        anchor_before = anchor.read_bytes()
        budget_anchor_before = budget_anchor.read_bytes()

        def dispatch(_cid, data):
            budget = RequestBudget(str(budget_db), 'batch:0', 'fixture-model', 3, fingerprint, **policy)
            assert budget.reserve()
            req = urllib.request.Request(url, json.dumps(data).encode(), method='POST')
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)

        def reconcile(_cid, _data):
            with urllib.request.urlopen(url, timeout=5) as response:
                observed = json.load(response)
            assert observed == {'counter': 1, 'observation_missing': True}
            return None  # A mutation counter is not a verified completed Git revision.

        conversation = None
        for only_observe in (False, False, True, True):
            result = execute_attempt(GuardedAttemptStore(db, anchor), 'batch:0', request,
                                     dispatch, reconcile, reconcile_only=only_observe)
            assert result['status'] == 'unknown'
            conversation = conversation or result['conversation_id']
            assert result['conversation_id'] == conversation
        assert counts == {'post': 1, 'get': 4}
        assert counter.read_text() == '1'
        with sqlite3.connect('file:'+str(db)+'?mode=ro', uri=True) as connection:
            assert connection.execute('SELECT status,result FROM attempts').fetchall() == [('reserved', None)]
        with sqlite3.connect('file:'+str(budget_db)+'?mode=ro', uri=True) as connection:
            assert connection.execute('SELECT used FROM budgets').fetchone()[0] == 1
            assert connection.execute('SELECT spent FROM monetary_budgets').fetchone()[0] == 10
        assert anchor.read_bytes() == anchor_before
        assert budget_anchor.read_bytes() == budget_anchor_before
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
