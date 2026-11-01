"""Privacy, stream transparency and incomplete-observation semantics."""
import json
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness_telemetry import ResponseObserver, request_shape, summarize, TelemetryLog


def test_shape_hashes_content_without_retaining_it():
    body = {'model': 'auto', 'system': 'private prompt', 'tools': [{'name': 'x', 'input_schema': {}}],
            'messages': [{'role': 'user', 'content': [{'type': 'tool_result', 'content': 'private output'}]}]}
    result = request_shape(body)
    assert 'private' not in json.dumps(result)
    assert result['tool_result_bytes'] > 0
    assert result['tool_schema_count'] == 1
    assert request_shape({**body, 'messages': []})['system_sha256'] == result['system_sha256']
    assert request_shape({**body, 'system': 'changed'})['system_sha256'] != result['system_sha256']


def test_sse_chunk_boundaries_usage_and_missing_fields():
    data = (b'event: message_start\r\ndata: {"type":"message_start","message":{"model":"model","usage":{"input_tokens":12,"cache_read_input_tokens":5}}}\r\n\r\n'
            b'data: {"type":"content_block_start","content_block":{"type":"tool_use","input":{"secret":"not persisted"}}}\n\n'
            b'data: {"type":"message_delta","usage":{"output_tokens":7},"delta":{"stop_reason":"tool_use"}}\n\n'
            b'data: {"type":"message_stop"}\n\n')
    observer = ResponseObserver('text/event-stream')
    for byte in data:
        observer.feed(bytes([byte]))
    result = observer.finish()
    assert result['input_tokens'] == 12 and result['output_tokens'] == 7
    assert result['cache_read_input_tokens'] == 5
    assert result['cache_creation_input_tokens'] is None
    assert result['tool_calls'] == 1 and result['message_complete'] is True
    assert 'secret' not in json.dumps(result)
    assert ResponseObserver('text/event-stream').finish()['tool_calls'] is None


def test_json_usage_zero_is_distinct_from_absent_and_malformed():
    observer = ResponseObserver('application/json')
    observer.feed(json.dumps({'model': 'x', 'content': [], 'stop_reason': 'end_turn',
                             'usage': {'input_tokens': 0, 'output_tokens': True,
                                       'cache_read_input_tokens': -1}}).encode())
    result = observer.finish()
    assert result['input_tokens'] == 0
    assert result['output_tokens'] is None and result['cache_read_input_tokens'] is None
    observer = ResponseObserver('text/event-stream', limit=128)
    observer.feed(b'data: ' + b'x'*1000 + b'\n\n')
    assert observer.finish()['parse_status'] == 'oversized'


def test_private_bounded_log_and_missing_end_survives_reopen(tmp_path):
    directory = tmp_path / 'telemetry'; directory.mkdir(mode=0o700)
    log = TelemetryLog(directory, 'attempt', 'a'*64)
    log.emit({'kind': 'request_started', 'request_id': 'one', 'reserved_microdollars': 10})
    reopened = TelemetryLog(directory, 'attempt', 'a'*64)
    records = [json.loads(x) for x in (directory/'telemetry.jsonl').read_text().splitlines()]
    summary = summarize(records)
    assert summary['requests_observed'] == 1 and summary['requests_without_end'] == 1
    assert summary['input_tokens'] is None and summary['actual_cost_usd'] is None
    assert (directory/'telemetry.jsonl').stat().st_mode & 0o077 == 0
    with pytest.raises(ValueError):
        TelemetryLog(directory, 'different', 'a'*64)
    with pytest.raises(ValueError):
        reopened.emit({'kind': 'request_ended', 'secret': 'do not write'})
    tiny = tmp_path / 'tiny'; tiny.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        TelemetryLog(tiny, 'attempt', 'a'*64, max_bytes=10)


def test_summary_never_turns_partial_coverage_into_totals():
    rows = [{'kind': 'request_started', 'request_id': '1', 'reserved_microdollars': 4},
            {'kind': 'request_ended', 'request_id': '1', 'message_complete': True, 'transport_complete': True, 'parse_status': 'observed', 'output_tokens_final': True, 'input_tokens': 10, 'output_tokens': 2},
            {'kind': 'request_started', 'request_id': '2', 'reserved_microdollars': 4},
            {'kind': 'request_ended', 'request_id': '2', 'message_complete': True, 'transport_complete': True, 'parse_status': 'observed', 'output_tokens_final': True, 'input_tokens': None, 'output_tokens': 3}]
    result = summarize(rows)
    assert result['input_tokens'] is None and result['observed_input_tokens'] == 10
    assert result['output_tokens'] == 5
    assert result['estimated_reserved_microdollars'] == 8
    assert result['cached_input_share'] is None


def test_log_rejects_symlink_and_partial_history(tmp_path):
    directory = tmp_path / 'telemetry'; directory.mkdir(mode=0o700)
    target = tmp_path / 'target'; target.write_text('untouched')
    (directory/'telemetry.jsonl').symlink_to(target)
    with pytest.raises(OSError):
        TelemetryLog(directory, 'a', 'a'*64)
    assert target.read_text() == 'untouched'
    (directory/'telemetry.jsonl').unlink()
    (directory/'telemetry.jsonl').write_text('{')
    (directory/'telemetry.jsonl').chmod(0o600)
    with pytest.raises(ValueError):
        TelemetryLog(directory, 'a', 'a'*64)


@pytest.mark.parametrize('tail', [b'', b'data: '+b'x'*1000+b'\n\n'])
def test_partial_response_usage_cannot_become_a_complete_total(tail):
    observer = ResponseObserver('text/event-stream', limit=256)
    observer.feed(b'data: {"type":"message_start","message":{"usage":{"input_tokens":12,"output_tokens":1}}}\n\n')
    observer.feed(tail)
    rows = [{'kind':'request_started', 'request_id':'1', 'reserved_microdollars':10},
            {'kind':'request_ended', 'request_id':'1', 'transport_complete':True, **observer.finish()}]
    result = summarize(rows)
    assert result['output_tokens'] is None and result['output_tokens_coverage'] == 0
    assert result['observed_output_tokens'] == 1
    assert result['estimated_reserved_microdollars'] == 10
