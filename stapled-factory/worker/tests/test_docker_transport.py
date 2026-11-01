import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_transport import DockerTransport, DockerTransportError

ID = 'a' * 64
CONV = '12345678-1234-1234-1234-123456789abc'


def test_key_and_payload_only_sent_on_stdin():
    transport = DockerTransport(ID, 'secret-session')
    with patch('docker_transport.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '{"ok":true,"body":{"id":"ok"}}')) as run:
        assert transport.request('POST', '/api/conversations', {'private': 'sensitive'}) == {'id': 'ok'}
    args, kwargs = run.call_args
    assert 'secret-session' not in str(args) and 'sensitive' not in str(args)
    assert json.loads(kwargs['input'])['key'] == 'secret-session'
    assert kwargs['stderr'] == subprocess.DEVNULL


@pytest.mark.parametrize('output', ['', 'invalid', '[]', '{"ok":true,"body":[]}', '{"ok":false}', '{"ok":true}'])
def test_malformed_or_failed_output_is_uncertain(output):
    with patch('docker_transport.subprocess.run', return_value=subprocess.CompletedProcess([], 0, output)):
        with pytest.raises(DockerTransportError):
            DockerTransport(ID, 'secret').request('GET', '/api/conversations/' + CONV)


def test_invalid_ids_and_request_paths_are_rejected():
    for value in ('--privileged', 'a' * 12, 'A' * 64, 'a' * 64 + '\n'):
        with pytest.raises(ValueError): DockerTransport(value, 'key')
    transport = DockerTransport(ID, 'key')
    for method, path in [('GET', '/api/conversations/../../x'), ('POST', '/api/conversations/' + CONV), ('GET', '/api/conversations/' + CONV + '?x=1')]:
        with pytest.raises(ValueError): transport.request(method, path)


def test_exec_failure_does_not_expose_exception():
    with patch('docker_transport.subprocess.run', side_effect=subprocess.TimeoutExpired('secret', 1)):
        with pytest.raises(DockerTransportError, match='outcome is uncertain') as exc:
            DockerTransport(ID, 'secret').request('GET', '/api/conversations/' + CONV)
        assert 'secret' not in str(exc.value)


def test_stall_routes_are_narrow_and_cannot_restart_the_conversation():
    from stall_runtime import DIAGNOSTIC
    transport=DockerTransport(ID,'key')
    with patch('docker_transport.subprocess.run',return_value=subprocess.CompletedProcess([],0,'{"ok":true,"body":{"success":true}}')) as run:
        transport.request('POST','/api/conversations/'+CONV+'/events',{'role':'user','content':[{'type':'text','text':DIAGNOSTIC}],'run':False})
        assert json.loads(run.call_args.kwargs['input'])['payload']['run'] is False
        transport.request('GET','/api/conversations/'+CONV+'/events/search?limit=100&sort_order=TIMESTAMP')
    for payload in ({'role':'system','content':[],'run':False},{'role':'user','content':[{'type':'text','text':DIAGNOSTIC}],'run':True}):
        with pytest.raises(ValueError):transport.request('POST','/api/conversations/'+CONV+'/events',payload)
    for path in ('/api/conversations/'+CONV+'/run','/api/conversations/'+CONV+'/events/search?limit=1000','/api/conversations/'+CONV+'/events/search?body=secret'):
        with pytest.raises(ValueError):transport.request('GET',path)
