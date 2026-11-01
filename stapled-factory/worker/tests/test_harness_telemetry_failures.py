"""Diagnostic failures must not change forwarding, admission or usage certainty."""
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import egress_proxy
from harness_telemetry import ResponseObserver, TelemetryLog, summarize


def test_final_output_usage_must_be_reported_in_message_delta():
    observer = ResponseObserver('text/event-stream')
    observer.feed(b'data: {"type":"message_start","message":{"usage":{"output_tokens":1}}}\n\n')
    observer.feed(b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n')
    observer.feed(b'data: {"type":"message_stop"}\n\n')
    rows = [{'kind':'request_started', 'request_id':'one'},
            {'kind':'request_ended', 'request_id':'one', 'transport_complete':True, **observer.finish()}]
    result = summarize(rows)
    assert result['output_tokens'] is None
    assert result['observed_output_tokens'] == 1


@pytest.mark.parametrize('failure', ['start_sink', 'end_sink', 'upstream_interrupt'])
def test_proxy_diagnostic_failures_never_retry_or_refund(tmp_path, monkeypatch, failure):
    directory=tmp_path/'logs'; directory.mkdir(mode=0o700)
    log=TelemetryLog(directory, 'attempt', 'a'*64)
    finished=threading.Event()
    class Sink:
        def emit(self, record):
            try:
                if record['kind'] == ('request_started' if failure == 'start_sink' else 'request_ended') and failure != 'upstream_interrupt':
                    raise OSError('private exception must not leak')
                log.emit(record)
            finally:
                if record['kind'] == 'request_ended':finished.set()
    first=b'data: {"type":"message_start","message":{"usage":{"input_tokens":12,"output_tokens":1}}}\n\n'
    rest=b'data: {"type":"message_delta","usage":{"output_tokens":7}}\n\ndata: {"type":"message_stop"}\n\n'
    calls=[]
    class Response:
        status=200
        def __init__(self):self.step=0
        def getheader(self, name, default):return 'text/event-stream'
        def read1(self, size):
            self.step+=1
            if self.step == 1:return first
            if self.step == 2:
                if failure == 'upstream_interrupt':raise OSError('private upstream detail')
                return rest
            return b''
    class Upstream:
        def __init__(self, *args, **kwargs):pass
        def request(self, method, path, raw, headers):calls.append(raw)
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr(egress_proxy.http.client, 'HTTPSConnection', Upstream)
    (tmp_path/'ledger').mkdir(); (tmp_path/'anchors').mkdir()
    db=tmp_path/'ledger/budget.db'; anchor=tmp_path/'anchors/anchor'
    egress_proxy.GuardedAttemptStore.initialize(db,anchor)
    budget=egress_proxy.RequestBudget(str(db),'attempt','model',1,'a'*64,
        anchor_path=str(anchor),ceiling_microdollars=100,reserve_microdollars=10,
        policy_id='test',expires_at=4102444800)
    path='/tmp/sf-'+next(tempfile._get_candidate_names())+'.sock'
    server=egress_proxy.UnixServer(path,'private-key','example.com',443,'model',budget,telemetry=Sink())
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    connection=http.client.HTTPConnection('localhost')
    def connect():
        connection.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);connection.sock.connect(path)
    connection.connect=connect
    try:
        raw=b'{"model":"model","max_tokens":1}'
        connection.request('POST','/v1/messages',raw)
        response=connection.getresponse()
        assert response.read() == (first if failure == 'upstream_interrupt' else first+rest)
        assert finished.wait(3)
        assert calls == [raw] and not budget.reserve()
        rows=[json.loads(line) for line in (directory/'telemetry.jsonl').read_text().splitlines()]
        if failure == 'upstream_interrupt':
            end=rows[-1]
            assert end['transport_error'] and not end['transport_complete']
            summary=summarize(rows)
            assert summary['output_tokens'] is None
            assert summary['estimated_reserved_microdollars'] == 10
        assert 'private upstream detail' not in json.dumps(rows)
        assert 'private exception' not in json.dumps(rows)
        assert 'private-key' not in json.dumps(rows)
    finally:
        connection.close();server.shutdown();server.server_close();thread.join();os.unlink(path)


def test_proxy_rejects_abbreviated_telemetry_option_before_any_effect():
    import subprocess
    result=subprocess.run([sys.executable,str(Path(egress_proxy.__file__)),
        '--socket','/tmp/not-created-telemetry-probe.sock','--model','test','--max-requests','1',
        '--attempt-id','test','--attempt-fingerprint','a'*64,
        '--worst-case-request-microdollars','10','--pricing-policy-id','test',
        '--pricing-policy-expires-at','4102444800','--budget-db','/tmp/not-created-budget.db',
        '--budget-anchor','/tmp/not-created-anchor.json','--telemetry-d','/budget'],
        input='',text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
    assert result.returncode == 2
    assert 'unrecognized arguments: --telemetry-d /budget' in result.stderr
