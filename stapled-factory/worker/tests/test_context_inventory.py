import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from context_inventory import inventory


def test_cache_share_uses_only_complete_reported_usage_and_keeps_missing_unknown():
    rows = [{'kind':'request_started', 'request_id':'a', 'tool_schema_count':3,
             'tool_schema_bytes':100, 'system_bytes':50, 'tools_sha256':'stable', 'system_sha256':'dynamic'},
            {'kind':'request_ended', 'request_id':'a', 'transport_complete':True,
             'message_complete':True, 'parse_status':'observed', 'output_tokens_final':True,
             'input_tokens':10, 'output_tokens':2, 'cache_read_input_tokens':90,
             'cache_creation_input_tokens':0},
            {'kind':'request_started', 'request_id':'b'},
            {'kind':'request_ended', 'request_id':'b', 'input_tokens':1000,
             'output_tokens':None, 'cache_read_input_tokens':None}]
    result = inventory(rows)
    assert result['cache_usage']['reported_complete_requests'] == 1
    assert result['cache_usage']['unknown_or_incomplete_requests'] == 1
    assert result['cache_usage']['cached_read_share_reported'] == .9
    assert result['cache_usage']['prompt_tokens_reported'] == 100
    missing = inventory([{'kind':'request_started','request_id':'missing'}])
    assert missing['cache_usage']['cached_read_share_reported'] is None
    assert missing['cache_usage']['prompt_tokens_reported'] is None
    assert result['actual_charges_usd'] is None
