"""Single-purpose Unix-socket Anthropic proxy. Run outside the worker sandbox.

The credential is read from stdin, never supplied to the worker. Access to the
socket authorizes model requests, so use a dedicated per-attempt socket and a
separately enforced dollar budget. The optional authority backend serializes
reservations outside proxy-local state and never falls back on failure. Its
policy and storage require a trusted independent provisioner and failure domain.
The local SQLite backend is not sufficient against coordinated rollback.
"""
import argparse
import http.client
import json
import os
import socketserver
import sqlite3
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from guarded_store import GuardedAttemptStore, StoreQuarantined
from budget_authority import AuthorityClient
from pricing_policy import ROUTER_MODEL, router_reservation_microdollars

MAX_BODY = 1024 * 1024
MAX_ATTEMPT_MICRODOLLARS = 2_000_000
# Only request options exercised by the worker's ordinary message flow are
# accepted. Unknown options can select unpriced provider features or services.
SUPPORTED_FIELDS = frozenset({
    'model', 'max_tokens', 'messages', 'system', 'tools', 'tool_choice',
    'stream', 'temperature', 'top_p', 'top_k', 'stop_sequences', 'metadata',
    'thinking',
})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _reject_nonfinite(_value):
    raise ValueError('non-finite JSON number')


def _reject_unbudgeted_blocks(value):
    # The deployed relay can change model and invoke extra summarization
    # requests for image-bearing histories; neither charge is in this budget.
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if item.get('type') in ('image', 'input_image', 'image_url',
                                    'document', 'audio', 'input_audio', 'video'):
                raise ValueError('multimodal requests are not budgeted')
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def _reject_server_tools(body):
    # Provider-hosted tools may add charges beyond model token pricing.
    # OpenHands uses client-executed custom tools, with absent or custom type.
    tools = body.get('tools', [])
    if not isinstance(tools, list):
        raise ValueError('invalid tools')
    for tool in tools:
        if not isinstance(tool, dict) or tool.get('type', 'custom') != 'custom':
            raise ValueError('provider-hosted tools are not budgeted')


class RequestBudget:
    """Atomic request reservations for one immutable attempt configuration."""
    def __init__(self, path, attempt, model, limit, fingerprint, *, anchor_path, ceiling_microdollars, reserve_microdollars, policy_id, expires_at):
        if not attempt or not model or limit < 1 or len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint):
            raise ValueError('invalid budget configuration')
        if (type(ceiling_microdollars) is not int or type(reserve_microdollars) is not int
                or not 0 < reserve_microdollars <= ceiling_microdollars <= MAX_ATTEMPT_MICRODOLLARS
                or not policy_id or type(expires_at) is not int or expires_at <= int(time.time())):
            raise ValueError('missing, expired or invalid monetary policy')
        self.ceiling, self.cost, self.policy_id, self.expires_at = ceiling_microdollars, reserve_microdollars, policy_id, expires_at
        self.path, self.attempt, self.model, self.limit = path, attempt, model, limit
        self.fingerprint = fingerprint
        self.store = GuardedAttemptStore(Path(path), Path(anchor_path))
        with self.store._transaction() as (db, state):
            db.execute("CREATE TABLE IF NOT EXISTS budgets (attempt TEXT PRIMARY KEY, model TEXT NOT NULL, request_limit INTEGER NOT NULL, used INTEGER NOT NULL, fingerprint TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS monetary_budgets (attempt TEXT PRIMARY KEY, ceiling INTEGER NOT NULL, reserve INTEGER NOT NULL, policy_id TEXT NOT NULL, expires_at INTEGER NOT NULL, spent INTEGER NOT NULL)")
            monetary = db.execute('SELECT ceiling, reserve, policy_id, expires_at FROM monetary_budgets WHERE attempt=?', (attempt,)).fetchone()
            policy = (ceiling_microdollars, reserve_microdollars, policy_id, expires_at)
            # An existing request counter without monetary history cannot be upgraded safely.
            existing = db.execute('SELECT 1 FROM budgets WHERE attempt=?', (attempt,)).fetchone()
            if bool(existing) != bool(monetary):
                raise ValueError('incomplete attempt budget state')
            if monetary is None:
                db.execute('INSERT INTO monetary_budgets VALUES (?, ?, ?, ?, ?, 0)', (attempt, *policy))
                state['changed'] = True
            elif monetary != policy:
                raise ValueError('monetary policy mismatch')
            row = db.execute('SELECT model, request_limit, fingerprint FROM budgets WHERE attempt=?', (attempt,)).fetchone()
            if row is None:
                db.execute('INSERT INTO budgets VALUES (?, ?, ?, 0, ?)', (attempt, model, limit, fingerprint))
            elif row != (model, limit, fingerprint):
                raise ValueError('attempt budget configuration mismatch')

    def reserve(self, estimated_microdollars=None):
        if estimated_microdollars is not None:
            if (self.model != ROUTER_MODEL or type(estimated_microdollars) is not int
                    or not 1 <= estimated_microdollars <= self.cost):
                raise ValueError('invalid dynamic request reservation')
        cost = self.cost if estimated_microdollars is None else estimated_microdollars
        with self.store._transaction() as (db, state):
            if int(time.time()) >= self.expires_at:
                return False
            row = db.execute('SELECT used, request_limit, model, fingerprint FROM budgets WHERE attempt=?', (self.attempt,)).fetchone()
            money = db.execute('SELECT ceiling, reserve, policy_id, expires_at, spent FROM monetary_budgets WHERE attempt=?', (self.attempt,)).fetchone()
            if row is None or money is None or row[1:] != (self.limit, self.model, self.fingerprint) or money[:4] != (self.ceiling, self.cost, self.policy_id, self.expires_at):
                raise ValueError('budget state mismatch')
            if row[0] >= self.limit or money[4] > self.ceiling - cost:
                return False
            db.execute('UPDATE budgets SET used=used+1 WHERE attempt=?', (self.attempt,))
            db.execute('UPDATE monetary_budgets SET spent=spent+? WHERE attempt=?', (cost, self.attempt))
            state['changed'] = True
            return True


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path, key, host, port, model, budget, gateway_ip=None):
        self.key, self.host, self.port, self.model = key, host, port, model
        self.gateway_ip = gateway_ip
        self.budget = budget
        if model == ROUTER_MODEL and not isinstance(budget, RequestBudget):
            raise ValueError('router requires body-sized local request reservations')
        self.router_session_id = (str(uuid.uuid5(uuid.NAMESPACE_URL,
                                  'stapled-factory-attempt:' + budget.attempt))
                                  if model == ROUTER_MODEL else None)
        super().__init__(path, Handler)
        os.chmod(path, 0o600)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, *_):
        pass

    def do_POST(self):
        if self.path != '/v1/messages':
            return self.send_error(404)
        try:
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                raise ValueError()
            size = int(self.headers['Content-Length'])
            if not 0 < size <= MAX_BODY:
                raise ValueError()
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError()
            body = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite)
            if not isinstance(body, dict) or body.get('model') != self.server.model:
                raise ValueError()
            if body.keys() - SUPPORTED_FIELDS:
                raise ValueError('unsupported request option')
            _reject_unbudgeted_blocks(body)
            _reject_server_tools(body)
            tokens = body.get('max_tokens')
            if type(tokens) is not int or not 1 <= tokens <= 1024:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return self.send_error(400)
        try:
            reserved = (self.server.budget.reserve(router_reservation_microdollars(len(raw)))
                        if self.server.model == ROUTER_MODEL else self.server.budget.reserve())
            if not reserved:
                return self.send_error(429)
        except (sqlite3.Error, ValueError, StoreQuarantined):
            return self.send_error(503)
        if self.server.gateway_ip is None:
            conn = http.client.HTTPSConnection(self.server.host, self.server.port, timeout=90)
        else:
            from pinned_https import PinnedHTTPSConnection
            conn = PinnedHTTPSConnection(self.server.host, self.server.port, gateway_ip=self.server.gateway_ip, timeout=90)
        try:
            headers = {
                'content-type': 'application/json', 'anthropic-version': '2023-06-01',
                'x-bf-vk': self.server.key,
            }
            if self.server.router_session_id is not None:
                headers['x-bf-session-id'] = self.server.router_session_id
            conn.request('POST', '/anthropic/v1/messages', raw, headers)
            response = conn.getresponse()
            # Do not follow redirects or relay their location headers.
            if 300 <= response.status < 400:
                return self.send_error(502)
            self.send_response(response.status)
            self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
            self.send_header('Connection', 'close')
            self.end_headers()
            # read() may wait for the whole buffer or EOF, holding SSE events
            # while the model continues running. Forward available bytes promptly.
            while data := response.read1(8192):
                self.wfile.write(data)
                self.wfile.flush()
        except (OSError, http.client.HTTPException):
            self.close_connection = True
        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--gateway-ip', help='trusted pinned IPv4 address; requires matching namespace firewall')
    parser.add_argument('--max-requests', type=int, required=True)
    parser.add_argument('--authority-socket', help='trusted authority Unix socket, separate from worker socket')
    parser.add_argument('--budget-db', help='proxy-side durable SQLite file for local test backend')
    parser.add_argument('--budget-anchor', help='trusted watermark path for local test backend')
    parser.add_argument('--initialize-budget-store', action='store_true', help='initialize only new, empty store and anchor')
    parser.add_argument('--attempt-id', required=True)
    parser.add_argument('--attempt-fingerprint', required=True, help='SHA-256 of immutable attempt inputs')
    parser.add_argument('--ceiling-microdollars', type=int, default=MAX_ATTEMPT_MICRODOLLARS,
                        help='per-attempt reservation ceiling, at most $2 (default $2)')
    parser.add_argument('--worst-case-request-microdollars', type=int, required=True, help='pre-request reservation estimate; not a provider charge guarantee')
    parser.add_argument('--pricing-policy-id', required=True, help='trusted versioned pricing and model-limit policy')
    parser.add_argument('--pricing-policy-expires-at', type=int, required=True, help='UTC Unix timestamp; fail closed after expiry')
    args = parser.parse_args()
    if args.gateway_ip is not None:
        from pinned_https import PinnedHTTPSConnection
        try:
            PinnedHTTPSConnection('hermes-control.tail4296e6.ts.net', 443, gateway_ip=args.gateway_ip)
        except ValueError:
            parser.error('invalid gateway IP')
    if args.max_requests < 1:
        parser.error('max-requests must be positive')
    if not 0 < args.ceiling_microdollars <= MAX_ATTEMPT_MICRODOLLARS:
        parser.error('per-attempt ceiling must be between 1 and 2,000,000 microdollars')
    if args.authority_socket:
        if args.model == ROUTER_MODEL:
            parser.error('router model requires body-sized local request reservations')
        if (not os.path.isabs(args.authority_socket) or not os.path.isabs(args.socket)
                or os.path.realpath(os.path.dirname(args.authority_socket)) == os.path.realpath(os.path.dirname(args.socket))
                or args.initialize_budget_store):
            parser.error('authority socket must be absolute and separate from the worker socket; initialization is forbidden')
    elif not args.budget_db or not args.budget_anchor:
        parser.error('local budget requires database and anchor')
    if args.initialize_budget_store:
        GuardedAttemptStore.initialize(Path(args.budget_db), Path(args.budget_anchor))
    key = sys.stdin.readline().strip()
    if not key:
        parser.error('missing credential on stdin')
    # Deliberately fixed; never derived from worker requests or environment.
    if args.authority_socket:
        # The authority's preprovisioned policy is authoritative; CLI limits do
        # not override it. The second line must arrive on the trusted stdin pipe.
        try:
            capability = bytes.fromhex(sys.stdin.readline().strip())
        except ValueError:
            parser.error('invalid authority capability on stdin')
        if len(capability) < 32:
            parser.error('missing authority capability on stdin')
        budget = AuthorityClient(args.authority_socket, args.attempt_id, capability, args.attempt_fingerprint, args.model)
    else:
        budget = RequestBudget(args.budget_db, args.attempt_id, args.model, args.max_requests, args.attempt_fingerprint, anchor_path=args.budget_anchor, ceiling_microdollars=args.ceiling_microdollars, reserve_microdollars=args.worst_case_request_microdollars, policy_id=args.pricing_policy_id, expires_at=args.pricing_policy_expires_at)
    server = UnixServer(args.socket, key, 'hermes-control.tail4296e6.ts.net', 443, args.model, budget, gateway_ip=args.gateway_ip)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        os.unlink(args.socket)


if __name__ == '__main__':
    main()
