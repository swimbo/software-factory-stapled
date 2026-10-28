#!/usr/bin/env python3
"""Verify fencing an in-flight container tool; uses no credentials or network.

This isolates a synthetic shell process, not an OpenHands server. It proves the
bound container stops before its delayed write; it does not prove safe replay or
that earlier tool effects can be rolled back. Requires a locally available
python:3.11-slim image and a functioning Docker daemon.
"""
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crash_fence import fence_bound_worker
from guarded_store import GuardedAttemptStore, StoreQuarantined


def docker(*args):
    return subprocess.run(['docker', *args], check=True, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=30).stdout.strip()


def main():
    name = 'factory-fence-probe-' + uuid.uuid4().hex
    cid = None
    with tempfile.TemporaryDirectory(prefix='factory-fence-probe-') as directory:
        root = Path(directory)
        for subdir in ('ledger', 'anchor', 'work'):
            (root / subdir).mkdir()
        store = GuardedAttemptStore.initialize(root / 'ledger/db', root / 'anchor/mark')
        store.reserve('probe', 'hash', 'conversation')
        _, allocation = store.reserve_allocation('probe', 'hash', 'conversation', 'spec')
        try:
            # Resolve an existing image only; never pull or touch shared images.
            image = docker('image', 'inspect', 'python:3.11-slim', '--format', '{{.Id}}')
            cid = docker('run', '-d', '--name', name, '--label', 'factory.probe=fence',
                         '--label', 'factory.allocation=' + allocation, '--label', 'factory.role=worker',
                         '--network', 'none', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                         '--mount', 'type=bind,src=' + str(root / 'work') + ',dst=/work',
                         image, 'sh', '-c', 'echo started >> /work/marker; sleep 4; echo late >> /work/marker; sleep 20')
            store.bind_allocation('probe', 'hash', 'conversation', 'spec', allocation, {'worker_id': cid})
            marker = root / 'work/marker'
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            if not marker.exists() or marker.read_text() != 'started\n':
                raise RuntimeError('first tool effect did not occur')
            if fence_bound_worker(store, 'probe', 'hash', 'conversation', 'spec')['status'] != 'fenced':
                raise RuntimeError('fencing did not succeed')
            time.sleep(5)
            if marker.read_text() != 'started\n':
                raise RuntimeError('tool executed the delayed write')
            if store.lookup('probe', 'hash', 'conversation')[0] != 'unknown':
                raise RuntimeError('unresolved attempt state changed')
            reopened = GuardedAttemptStore(root / 'ledger/db', root / 'anchor/mark')
            try:
                reopened.complete('probe', 'late result')
                raise RuntimeError('fenced attempt accepted late completion')
            except StoreQuarantined:
                pass
            print('Bound container fenced; delayed write absent; late completion rejected; attempt remains unknown.')
        finally:
            # Only the full ID returned by our own docker run is eligible for removal.
            if cid:
                subprocess.run(['docker', 'rm', '-f', cid], check=False, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30)


if __name__ == '__main__':
    main()
