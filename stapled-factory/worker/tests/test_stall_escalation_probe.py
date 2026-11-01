"""Failure-path proof for the unpaid operational probe; no Docker is contacted."""
import importlib.util
from pathlib import Path
import os
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('stall_probe', ROOT/'probes/stall_escalation.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize('timeout_phase', ['create', 'start'])
def test_accepted_launch_timeout_cannot_leave_running_unbound_container(tmp_path, monkeypatch, timeout_phase):
    image = 'sha256:'+'f'*64
    containers = {}
    def inspect(identity):
        return {'Id': image} if identity == image else containers[identity]
    def command(*args, **kwargs):
        if args[0] == 'systemctl': return ''
        if args[0] == 'git':
            if 'init' in args: Path(args[2], '.git').mkdir()
            return ''
        assert args[0] == 'docker'
        op = args[1]
        if op in ('run', 'create'):
            cid = 'a'*64
            labels = {args[n+1].split('=',1)[0]:args[n+1].split('=',1)[1]
                      for n,a in enumerate(args[:-1]) if a == '--label'}
            containers[cid] = {'Id':cid,'Image':image,'Config':{'Labels':labels},
                               'State':{'Running':op=='run'}}
            if op == 'run' or timeout_phase == 'create':
                raise subprocess.TimeoutExpired(args, 30)
            return cid
        if op == 'start':
            containers[args[2]]['State']['Running'] = True
            raise subprocess.TimeoutExpired(args, 30)
        if op == 'kill':
            containers[args[-1]]['State']['Running'] = False
            return ''
        raise AssertionError(args)
    monkeypatch.setattr(probe, 'inspect', inspect)
    monkeypatch.setattr(probe, 'command', command)
    monkeypatch.setattr(probe.sys, 'platform', 'linux')
    monkeypatch.setattr(probe.sys, 'argv', ['probe','--image',image,'--output',str((tmp_path/'attempt').resolve())])
    previous = os.umask(0o077)
    try:
        with pytest.raises(subprocess.TimeoutExpired): probe.main()
    finally: os.umask(previous)
    assert containers and not any(v['State']['Running'] for v in containers.values())
    assert (tmp_path/'attempt/ledger/attempts.db').exists()


@pytest.mark.parametrize('mutation', ['diagnostic', 'read'])
def test_reopen_rejects_any_transport_activity(mutation):
    from stall_runtime import StallIntervention
    calls = {'create':1, 'diagnostic':1, 'run':0, 'read':8}
    class Adapter:
        def read(self, cid, payload):
            calls[mutation] += 1
            raise StallIntervention('stopped')
    with pytest.raises(RuntimeError, match='reopen'):
        probe._verify_stopped_reopen(Adapter(), 'original', calls)


@pytest.mark.parametrize('status', ['stopped', 'fencing_uncertain', None])
def test_reopen_requires_confirmed_stopped_outcome(status):
    from stall_runtime import StallIntervention
    calls = {'create':1, 'diagnostic':1, 'run':0, 'read':8}
    class Adapter:
        def read(self, cid, payload):
            if status is not None: raise StallIntervention(status)
    if status == 'stopped':
        probe._verify_stopped_reopen(Adapter(), 'original', calls)
    else:
        with pytest.raises(RuntimeError, match='reopen'):
            probe._verify_stopped_reopen(Adapter(), 'original', calls)
