"""Contain an uncertain attempt without replaying it or discarding evidence.

This is not a recovery/resume mechanism. The caller must independently detect a
server crash and authorize fencing; a timeout alone is not such evidence. Docker
administrators and the host are trusted, and the caller must prevent later starts.
"""
import re

from egress_guard import inspect
from provision_runtime import _run


class FenceError(RuntimeError):
    pass


def fence_bound_worker(store, attempt_id, request_hash, conversation_id, specification_hash):
    """Kill only the worker bound to an unresolved attempt; preserve all volumes.

    A durable fence intent prohibits later completion; interrupted effects stay
    uncertain. A privileged Docker operator could restart the container after
    the check, so this does not authorize replacing or replaying the attempt.
    """
    try:
        alloc, binding = store.lookup_allocation(attempt_id, request_hash, conversation_id, specification_hash)
        worker_id = binding['worker_id']
        if not re.fullmatch(r'[0-9a-f]{32}', alloc) or not re.fullmatch(r'[0-9a-f]{64}', worker_id):
            raise FenceError('invalid bound identity')

        def check():
            worker = inspect('container', worker_id)
            labels = worker.get('Config', {}).get('Labels') or {}
            if (worker.get('Id') != worker_id or labels.get('factory.allocation') != alloc or
                labels.get('factory.role') != 'worker' or
                (worker.get('HostConfig', {}).get('RestartPolicy') or {}).get('Name') != 'no'):
                raise FenceError('worker identity or restart policy changed')
            return worker.get('State') or {}

        state = check()
        if state.get('Restarting') is not False or not isinstance(state.get('Running'), bool):
            raise FenceError('indeterminate container state')
        # Persist before the kill: a concurrent controller may otherwise publish
        # a result after we have decided to contain an uncertain attempt.
        try:
            store.begin_fencing(attempt_id, request_hash, conversation_id, specification_hash, alloc, worker_id)
        except Exception:
            # Contain the running worker even if durable fencing is unavailable;
            # report failure because a late completion cannot then be excluded.
            if state['Running']:
                _run(['kill', '--signal', 'KILL', worker_id])
            raise
        if state['Running']:
            _run(['kill', '--signal', 'KILL', worker_id])
        state = check()
        if state.get('Running') is not False or state.get('Restarting') is not False:
            raise FenceError('worker did not stop')
        store.confirm_fencing(attempt_id, request_hash, conversation_id, specification_hash, alloc, worker_id)
        return {'status': 'fenced', 'worker_id': worker_id}
    except Exception as exc:
        raise FenceError('bound worker could not be fenced') from exc
