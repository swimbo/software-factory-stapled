"""Exercise production exchange with real processes and bounded hostile streams."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import select
import sys

import pytest

SPINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPINE))
spec = importlib.util.spec_from_file_location('production_exchange_test', SPINE / 'prebuilt_validation.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_close_reaps_already_exited_child_without_signalling_group(monkeypatch):
    from validation_transport import Session
    session = Session([sys.executable, '-u', '-c', 'pass'])
    ready, _, _ = select.select([session.child.stdout], [], [], 5)
    assert ready and os.read(session.child.stdout.fileno(), 1) == b''
    assert session.child.returncode is None
    monkeypatch.setattr(os, 'killpg', lambda *args: pytest.fail('exited child must not be signalled'))
    session.close()
    assert session.child.returncode == 0


def test_close_does_not_hide_permission_error_for_running_child(monkeypatch):
    from validation_transport import Session
    session = Session([sys.executable, '-u', '-c', 'import time; time.sleep(30)'])
    monkeypatch.setattr(os, 'killpg', lambda *args: (_ for _ in ()).throw(PermissionError('fixture')))
    try:
        with pytest.raises(PermissionError):
            session.close()
    finally:
        session.child.kill(); session.child.wait(timeout=5)
        for stream in (session.child.stdin, session.child.stdout, session.child.stderr):stream.close()


def redirect_docker(monkeypatch, program):
    original = subprocess.Popen
    children = []
    def launch(args, **kwargs):
        assert args[:4] == ['docker', 'exec', '-i', 'a' * 64]
        assert args[4:] == ['python3', '-u', '-c', m.CLIENT]
        child = original([sys.executable, '-u', '-c', program], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, 'Popen', launch)
    return children


def request():
    return {'method': 'POST', 'path': '/data', 'headers': {'X-Test': 'fixture'},
            'body': base64.b64encode(b'payload').decode()}


def test_production_exchange_reuses_one_started_client(monkeypatch):
    program = ("import json,sys,time,base64;time.sleep(.15)\n"
               "for count,line in enumerate(sys.stdin,1):\n"
               " json.loads(line);print(json.dumps({'status':200,'body':base64.b64encode(str(count).encode()).decode()}),flush=True)\n")
    children = redirect_docker(monkeypatch, program)
    transport = m.CandidateTransport('a' * 64)
    try:
        assert base64.b64decode(transport.exchange(request())['body']) == b'1'
        assert base64.b64decode(transport.exchange(request())['body']) == b'2'
        assert len(children) == 1
    finally:
        getattr(transport, 'close', lambda: None)()


def test_production_exchange_preserves_request_and_response(monkeypatch):
    program = "import json,sys;v=json.loads(sys.stdin.readline());assert v['path']=='/data';print(json.dumps({'status':201,'body':v['body']}))"
    children = redirect_docker(monkeypatch, program)
    transport = m.CandidateTransport('a' * 64)
    try:
        assert transport.exchange(request()) == {
            'status': 201, 'body': base64.b64encode(b'payload').decode()}
    finally:
        transport.close()
    assert children[0].returncode is not None


@pytest.mark.parametrize('stream', [1, 2])
def test_host_caps_each_stream_before_full_capture(tmp_path, monkeypatch, stream):
    completed = tmp_path / 'all-output-written'
    program = ("import os,sys,pathlib;sys.stdin.readline();"
               + "\nfor _ in range(128): os.write(" + str(stream) + ",b'x'*65536)\n"
               + "pathlib.Path(" + repr(str(completed)) + ").write_text('finished')\n"
               + "print('{\"status\":200,\"body\":\"\"}')")
    children = redirect_docker(monkeypatch, program)
    with pytest.raises(ValueError, match='too large'):
        m.CandidateTransport('a' * 64).exchange(request())
    assert not completed.exists(), 'host must stop reading before the full hostile stream is buffered'
    assert children[0].returncode is not None, 'the original host client must be reaped'


@pytest.mark.parametrize('program,error', [
    ("import sys;sys.stdin.readline();sys.exit(7)", subprocess.CalledProcessError),
    ("import sys;sys.stdin.readline();print('not-json')", ValueError),
])
def test_production_exchange_rejects_failed_or_malformed_response(monkeypatch, program, error):
    children = redirect_docker(monkeypatch, program)
    with pytest.raises(error):
        m.CandidateTransport('a' * 64).exchange(request())
    assert children[0].returncode is not None


def test_persistent_timeout_permanently_closes_original_client(monkeypatch):
    from validation_transport import Session
    original = subprocess.Popen
    children = []
    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, 'Popen', launch)
    session = Session([sys.executable, '-u', '-c', 'import time;time.sleep(20)'])
    with pytest.raises(subprocess.TimeoutExpired):
        session.exchange(b'request\n', timeout=.1)
    with pytest.raises(ValueError, match='must not be resent'):
        session.exchange(b'request\n', timeout=.1)
    assert len(children) == 1 and children[0].returncode is not None


def test_stderr_limit_is_cumulative_across_persistent_exchanges(monkeypatch):
    program = ("import json,sys,os\nfor line in sys.stdin:\n"
               " json.loads(line);os.write(2,b'e'*40000);print('{\"status\":200,\"body\":\"\"}',flush=True)\n")
    children = redirect_docker(monkeypatch, program)
    transport = m.CandidateTransport('a'*64)
    try:
        assert transport.exchange(request())['status'] == 200
        with pytest.raises(ValueError, match='stderr too large'):
            transport.exchange(request())
        with pytest.raises(ValueError, match='must not be resent'):
            transport.exchange(request())
    finally:
        transport.close()
    assert len(children) == 1 and children[0].returncode is not None


def test_persistent_exchange_drains_output_while_delivering_large_input():
    from validation_transport import Session
    payload = b'i' * 400000 + b'\n'
    program = "import os,sys;os.write(1,b'x'*100000);data=sys.stdin.buffer.readline();os.write(1,str(len(data)).encode()+b'\\n')"
    session = Session([sys.executable, '-u', '-c', program])
    try:
        assert session.exchange(payload, timeout=3) == b'x' * 100000 + b'400001\n'
    finally:
        session.close()
