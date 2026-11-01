"""Exercise production exchange with real processes and bounded hostile streams."""
import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SPINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPINE))
spec = importlib.util.spec_from_file_location('production_exchange_test', SPINE / 'prebuilt_validation.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


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


def test_production_exchange_preserves_request_and_response(monkeypatch):
    program = "import json,sys;v=json.load(sys.stdin);assert v['path']=='/data';print(json.dumps({'status':201,'body':v['body']}))"
    children = redirect_docker(monkeypatch, program)
    assert m.CandidateTransport('a' * 64).exchange(request()) == {
        'status': 201, 'body': base64.b64encode(b'payload').decode()}
    assert children[0].returncode == 0


@pytest.mark.parametrize('stream', [1, 2])
def test_host_caps_each_stream_before_full_capture(tmp_path, monkeypatch, stream):
    completed = tmp_path / 'all-output-written'
    program = ("import os,sys,pathlib;sys.stdin.read();"
               + "\nfor _ in range(128): os.write(" + str(stream) + ",b'x'*65536)\n"
               + "pathlib.Path(" + repr(str(completed)) + ").write_text('finished')\n"
               + "print('{\"status\":200,\"body\":\"\"}')")
    children = redirect_docker(monkeypatch, program)
    with pytest.raises(ValueError, match='too large'):
        m.CandidateTransport('a' * 64).exchange(request())
    assert not completed.exists(), 'host must stop reading before the full hostile stream is buffered'
    assert children[0].returncode is not None, 'the original host client must be reaped'


@pytest.mark.parametrize('program,error', [
    ("import sys;sys.stdin.read();sys.exit(7)", subprocess.CalledProcessError),
    ("import sys;sys.stdin.read();print('not-json')", ValueError),
])
def test_production_exchange_rejects_failed_or_malformed_response(monkeypatch, program, error):
    children = redirect_docker(monkeypatch, program)
    with pytest.raises(error):
        m.CandidateTransport('a' * 64).exchange(request())
    assert children[0].returncode is not None


def test_transport_timeout_reaps_client_and_does_not_resend(monkeypatch):
    from validation_transport import capture
    original = subprocess.Popen
    children = []
    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, 'Popen', launch)
    with pytest.raises(subprocess.TimeoutExpired):
        capture([sys.executable, '-c', 'import time;time.sleep(20)'], b'', timeout=.1)
    assert len(children) == 1
    assert children[0].returncode is not None


def test_bounded_capture_drains_output_while_delivering_large_input():
    from validation_transport import capture
    payload = b'i' * 400000
    program = "import os,sys;os.write(1,b'x'*100000);data=sys.stdin.buffer.read();os.write(1,str(len(data)).encode())"
    output = capture([sys.executable, '-c', program], payload, timeout=3)
    assert output == b'x' * 100000 + str(len(payload)).encode()
