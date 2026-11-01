"""Summarize payload-free request shapes and reported cache coverage offline."""
import argparse
from collections import Counter
import json
from pathlib import Path


def inventory(rows):
    starts = [row for row in rows if row.get('kind') == 'request_started']
    ends = {row['request_id']: row for row in rows if row.get('kind') == 'request_ended'}
    tokens = []
    for row in starts:
        end = ends.get(row['request_id'], {})
        fields = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens')
        values = [end.get(k) for k in fields]
        if (all(end.get(k) is True for k in ('transport_complete', 'message_complete', 'output_tokens_final'))
                and end.get('parse_status') == 'observed'
                and all(type(x) is int and x >= 0 for x in values)):
            tokens.append(values)
    prompt = sum(sum(v[:3]) for v in tokens) if tokens else None
    cached = sum(v[1] for v in tokens) if tokens else None
    agent = [s for s in starts if type(s.get('tool_schema_count')) is int and s['tool_schema_count'] > 0]
    def sizes(field):
        values = [s[field] for s in agent if type(s.get(field)) is int]
        return {'min': min(values), 'max': max(values)} if values else None
    return {'schema': 'factory.context-inventory/v1', 'requests': len(starts),
        'requests_with_tools': len(agent), 'requests_without_tools_or_unknown': len(starts)-len(agent),
        'tool_schema_counts': dict(Counter(str(s['tool_schema_count']) for s in agent)),
        'tool_schema_bytes': sizes('tool_schema_bytes'), 'system_bytes': sizes('system_bytes'),
        'historical_tool_result_bytes': sizes('tool_result_bytes'),
        'distinct_tools_hashes': len({s['tools_sha256'] for s in agent if s.get('tools_sha256')}),
        'distinct_full_system_hashes': len({s['system_sha256'] for s in agent if s.get('system_sha256')}),
        'cache_usage': {'reported_complete_requests': len(tokens),
            'unknown_or_incomplete_requests': len(starts)-len(tokens),
            'prompt_tokens_reported': prompt, 'cache_read_tokens_reported': cached,
            'output_tokens_reported': sum(v[3] for v in tokens) if tokens else None,
            'cached_read_share_reported': cached / prompt if prompt else None,
            'provider_cache_eligibility': 'unknown; reported coverage only'},
        'actual_charges_usd': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('telemetry_directory', type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for path in sorted(args.telemetry_directory.glob('*.jsonl'))
            for line in path.read_text().splitlines()]
    print(json.dumps(inventory(rows), indent=2))


if __name__ == '__main__':
    main()
