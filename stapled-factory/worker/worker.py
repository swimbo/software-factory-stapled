"""OpenHands worker: durable at-most-once dispatch with fail-closed recovery.

Dispatch requires a private operator configuration. The per-attempt monetary
reservation is best effort, not a provider charge ceiling. A reserved attempt
is never dispatched a second time.
"""
import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attempt_store import AttemptStore  # noqa: E402


def execute_attempt(store, attempt_id, request, dispatch, reconcile, *, reconcile_only=False):
    """Dispatch once, or reconcile a previously reserved attempt without replay.

    The dispatch and reconcile callables must return a terminal result dictionary
    or None when the outcome cannot be proven. Exceptions leave the reservation
    unresolved, including a crash after a remote acceptance.
    """
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(canonical.encode()).hexdigest()
    conversation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "stapled-factory:" + attempt_id))
    try:
        state, stored = (store.lookup(attempt_id, request_hash, conversation_id) if reconcile_only
                         else store.reserve(attempt_id, request_hash, conversation_id))
        if state == 'missing':
            return {"status": "unknown", "git_revision": "", "conversation_id": conversation_id,
                    "error": "Attempt reservation missing; no dispatch was made"}
        if state == "completed":
            return json.loads(stored)
        result = dispatch(conversation_id, request) if state == "new" else reconcile(conversation_id, request)
        if result is None:
            return {"status": "unknown", "git_revision": "", "conversation_id": conversation_id,
                    "error": "Attempt outcome cannot be established; no repeat dispatch was made"}
        if not (result.get("status") == "failed" or
                (result.get("status") == "completed" and result.get("git_revision"))):
            return {"status": "unknown", "git_revision": "", "conversation_id": conversation_id,
                    "error": "A completed attempt with a verified revision was not established"}
        result = {**result, "conversation_id": conversation_id}
        store.complete(attempt_id, json.dumps(result, sort_keys=True))
        return result
    except Exception:
        # Do not leak server errors, credentials or model content into logs.
        return {"status": "unknown", "git_revision": "", "conversation_id": conversation_id,
                "error": "Attempt could not be safely dispatched or reconciled"}


def run_task(args):
    # Only an operator-owned private configuration enables this path; its
    # reservation is best effort and is not an actual provider charge ceiling.
    try:
        from operator_cli import run
        return run(args)
    except Exception:
        return {"git_revision": "", "status": "blocked",
                "error": "Trusted operator configuration or runtime prerequisites unavailable"}


def main():
    ap = argparse.ArgumentParser(description="Run a task on the OpenHands Agent Server.")
    ap.add_argument("--task-file", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--attempt-id", required=True)
    ap.add_argument("--attempt-db", required=True)
    ap.add_argument("--attempt-anchor", required=True)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--reconcile-only", action="store_true", help="observe existing attempts only; never dispatch")
    mode.add_argument("--successor-only", action="store_true", help="run the single fenced successor")
    mode.add_argument("--successor-reconcile-only", action="store_true", help="observe the successor without dispatch")
    mode.add_argument("--successor-preflight-only", action="store_true", help="check successor prerequisites without side effects")
    ap.add_argument("--agent-server", default=os.environ.get("OPENHANDS_AGENT_SERVER_URL", "http://127.0.0.1:3001"))
    ap.add_argument("--session-key", default=os.environ.get("OPENHANDS_SESSION_API_KEY"))
    ap.add_argument("--model", default=os.environ.get("OPENHANDS_MODEL"))
    ap.add_argument("--base-url", default=os.environ.get("OPENHANDS_BASE_URL"))
    args = ap.parse_args()
    print(json.dumps(run_task(args)))


def execute_openhands_attempt(store, attempt_id, request, adapter, *, reconcile_only=False):
    """Use the server adapter under the durable no-redispatch policy.

    The operator entry point supplies the isolated runtime and revision verifier.
    """
    return execute_attempt(store, attempt_id, request, adapter.create, adapter.read,
                           reconcile_only=reconcile_only)


def execute_bound_attempt(store, attempt_id, request, configuration, adapter_factory, *,
                          reconcile_only=False):
    """Coordinate one allocation and observe only the originally bound worker.

    This primitive requires a trusted coordinator to supply configuration and an
    authenticated adapter factory. It is not an authorization to enable the CLI:
    image provenance, workspace fencing, billing and restore authority remain
    independent prerequisites. A crash at any provisioning or dispatch point
    leaves an unresolved reservation, never a replacement allocation.
    """
    from provision_runtime import provision_once, inspect_bound_allocation

    # Refuse before reserving or contacting a runtime. A proxy without the
    # namespace firewall may expose its credential or spend to arbitrary hosts.
    # Presence here is only a prerequisite; the provisioner must install and
    # reverify the policy, and a trusted coordinator must authorize the inputs.
    firewall = configuration.get('firewall') if isinstance(configuration, dict) else None
    if (not isinstance(firewall, dict) or set(firewall) != {'helper_image', 'gateway_ip'} or
            any(not isinstance(value, str) or not value for value in firewall.values())):
        return {"status": "blocked", "git_revision": "",
                "error": "Bound execution requires a proxy firewall"}

    canonical = json.dumps(request, sort_keys=True, separators=(',', ':'))
    request_hash = hashlib.sha256(canonical.encode()).hexdigest()
    config = dict(configuration)
    baseline = config.pop('workspace_baseline')
    gateway_key = config.pop('gateway_key', None)

    def adapter_for(cid):
        bound = inspect_bound_allocation(store, attempt_id, request_hash, cid,
                                         workspace_baseline=baseline, **config)
        return adapter_factory(bound['worker_id'])

    def dispatch(cid, payload):
        # Reservation precedes all allocation or remote work. Provisioning binds
        # identity before delivering the credential. A missing binding fails shut.
        provision_once(store, attempt_id, request_hash, cid, workspace_baseline=baseline,
                       gateway_key=gateway_key, **config)
        return adapter_for(cid).create(cid, payload)

    def observe(cid, payload):
        return adapter_for(cid).read(cid, payload)

    return execute_attempt(store, attempt_id, request, dispatch, observe,
                           reconcile_only=reconcile_only)


def execute_bound_successor(store, attempt_id, request, configuration, adapter_factory, *,
                            workspace, baseline, reconcile_only=False):
    """Fence a bound source and dispatch its single successor through its original proxy.

    The caller is a trusted coordinator: it must establish the runtime and billing
    prerequisites and supply an isolated baseline workspace. A crash after
    dispatch is observed only; it must never cause another POST or allocation.
    """
    from crash_fence import fence_bound_worker
    from successor_runtime import provision_successor, inspect_successor

    successor_id = attempt_id + ':successor'
    source_cid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'stapled-factory:' + attempt_id))
    successor_cid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'stapled-factory:' + successor_id))
    request_hash = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    specification = {**configuration, 'workspace_baseline': baseline}
    specification_hash = hashlib.sha256(json.dumps(specification, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    unknown = {'status': 'unknown', 'git_revision': '', 'conversation_id': successor_cid,
               'error': 'Successor could not be safely dispatched or reconciled'}
    try:
        if not reconcile_only:
            fence_bound_worker(store, attempt_id, request_hash, source_cid, specification_hash)
            store.reserve_fenced_restart(attempt_id, request_hash, source_cid, specification_hash,
                                         successor_id, request_hash, successor_cid)
            provision_successor(store, attempt_id, request_hash, source_cid, successor_id,
                                request_hash, successor_cid, configuration=configuration,
                                workspace=workspace, baseline=baseline)
        state, result = store.lookup(successor_id, request_hash, successor_cid)
        if state == 'completed':
            return json.loads(result)
        if state == 'missing':
            return unknown
        bound = inspect_successor(store, attempt_id, request_hash, source_cid, successor_id,
                                  configuration=configuration, baseline=baseline)
        adapter = adapter_factory(bound['worker_id'])
        if not reconcile_only:
            if not store.begin_restart_dispatch(attempt_id, successor_id, request_hash, successor_cid):
                return unknown
            result = adapter.create(successor_cid, request)
        else:
            result = adapter.read(successor_cid, request)
        if result is None or not (result.get('status') == 'failed' or
                                  (result.get('status') == 'completed' and result.get('git_revision'))):
            return unknown
        result = {**result, 'conversation_id': successor_cid}
        store.complete(successor_id, json.dumps(result, sort_keys=True))
        return result
    except Exception:
        return unknown


if __name__ == "__main__":
    main()
