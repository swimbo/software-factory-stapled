"""Pure operator-side evaluation of a preregistered, one-repeat paired pilot.

Never executes candidate code, dispatches work or changes a live profile. Input
counts include reported cached input; estimates are not actual provider charges.
"""
import math
import statistics

PROFILES = ['baseline', 'context-v1', 'bounded-native-v1', 'stall-v1']


def _number(value, integer=False, optional=False):
    if optional and value is None:
        return
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid nonnegative finite numeric receipt')


def _identities(values):
    if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values):
        raise ValueError('nonempty string identities required')
    if len(set(values)) != len(values):
        raise ValueError('duplicate identities')


def _validate(prereg, results):
    if prereg.get('primary_metric') != 'input_tokens_per_accepted_task' or prereg.get('profiles') != PROFILES:
        raise ValueError('unsupported preregistered primary metric or profiles')
    tasks = prereg.get('tasks'); _identities(tasks)
    if not isinstance(prereg.get('pins'), dict) or not prereg['pins']:
        raise ValueError('immutable pins required')
    schedule = prereg.get('schedule')
    if not isinstance(schedule, list) or len(schedule) != len(tasks)*len(PROFILES):
        raise ValueError('one complete profile block per held-out task required')
    expected = {}
    pairs = set()
    for item in schedule:
        if not isinstance(item, dict): raise ValueError('invalid schedule row')
        unit, task, profile = (item.get(k) for k in ('unit', 'task_id', 'profile'))
        if not isinstance(unit, str) or not unit or unit in expected or task not in tasks or profile not in PROFILES:
            raise ValueError('invalid schedule identity')
        if (task, profile) in pairs: raise ValueError('duplicate task/profile pair')
        expected[unit] = (task, profile); pairs.add((task, profile))
    if not isinstance(results, list) or len(results) != len(expected):
        raise ValueError('incomplete evaluation; retain originals')
    seen = set()
    for row in results:
        if not isinstance(row, dict): raise ValueError('invalid result row')
        unit = row.get('unit')
        if not isinstance(unit, str) or unit in seen or unit not in expected or expected[unit] != (row.get('task_id'), row.get('profile')):
            raise ValueError('receipt does not match frozen schedule')
        seen.add(unit)
        for key in ('accepted', 'accounting_complete'):
            if type(row.get(key)) is not bool: raise ValueError('boolean receipt required')
        _number(row.get('requests'), integer=True)
        for key in ('input_tokens', 'output_tokens', 'stall_false_positives', 'truncations'):
            if key not in row: raise ValueError('missing coverage field')
            _number(row[key], integer=True, optional=True)
        for key in ('elapsed_s', 'estimated_reserved_usd'): _number(row.get(key))
        models = row.get('gateway_models')
        if models != [] or row['requests'] != 0:
            _identities(models)


def _total(rows, key):
    return None if any(row[key] is None for row in rows) else sum(row[key] for row in rows)


def _summary(rows):
    accepted = sum(row['accepted'] for row in rows)
    tokens = _total(rows, 'input_tokens')
    requests = sum(row['requests'] for row in rows)
    return {'trials': len(rows), 'accepted': accepted, 'requests': requests,
            'input_tokens': tokens, 'output_tokens': _total(rows, 'output_tokens'),
            'input_tokens_per_accepted_task': tokens/accepted if accepted and tokens is not None else None,
            'requests_per_accepted_task': requests/accepted if accepted else None,
            'median_elapsed_s': statistics.median(row['elapsed_s'] for row in rows),
            'estimated_reserved_usd': sum(row['estimated_reserved_usd'] for row in rows),
            'actual_charges_usd': None,
            'stall_false_positives': _total(rows, 'stall_false_positives'),
            'truncations': _total(rows, 'truncations')}


def _compare(prereg, left, right, summaries):
    by_task = {profile: {row['task_id']: row for row in rows} for profile, rows in (left, right)}
    baseline_name, baseline_rows = left; candidate_name, candidate_rows = right
    baseline, candidate = summaries[baseline_name], summaries[candidate_name]
    matched, mismatched = [], []
    outcomes = dict(improved=0, tied=0, worse=0, unknown=0)
    no_loss = True
    for task in prereg['tasks']:
        a, b = by_task[baseline_name][task], by_task[candidate_name][task]
        (matched if set(a['gateway_models']) == set(b['gateway_models']) else mismatched).append(task)
        no_loss = no_loss and (not a['accepted'] or b['accepted'])
        if not a['accepted'] or not b['accepted'] or a['input_tokens'] is None or b['input_tokens'] is None:
            outcomes['unknown'] += 1
        else:
            outcomes['improved' if b['input_tokens'] < a['input_tokens'] else 'worse' if b['input_tokens'] > a['input_tokens'] else 'tied'] += 1
    a, b = baseline['input_tokens_per_accepted_task'], candidate['input_tokens_per_accepted_task']
    improvement = (a-b)/a if a is not None and a > 0 and b is not None else None
    old_latency = baseline['median_elapsed_s']; new_latency = candidate['median_elapsed_s']
    latency_change = (new_latency-old_latency)/old_latency if old_latency > 0 else (0 if new_latency == 0 else None)
    rows = baseline_rows + candidate_rows
    gates = {
        'acceptance_no_decline': candidate['accepted'] >= baseline['accepted'],
        'task_acceptance_no_loss': no_loss,
        'primary_improves_at_least_ten_percent': improvement is not None and improvement >= .1,
        'median_latency_within_ten_percent': latency_change is not None and latency_change <= .1,
        'full_accounting': all(row['accounting_complete'] and row['input_tokens'] is not None and row['output_tokens'] is not None for row in rows),
        'zero_stall_false_positives': all(row['stall_false_positives'] == 0 for row in rows),
        'known_truncations': all(row['truncations'] is not None for row in rows),
        'matched_routes': not mismatched,
        'regressions_passed': prereg['pins'].get('regression_gate_passed') is True,
    }
    def stratum(tasks):
        if not tasks: return None
        return {'baseline': _summary([by_task[baseline_name][t] for t in tasks]),
                'candidate': _summary([by_task[candidate_name][t] for t in tasks])}
    return {'baseline': baseline_name, 'candidate': candidate_name, 'gates': gates,
            'primary_improvement_fraction': improvement, 'median_latency_change_fraction': latency_change,
            'paired_primary_outcomes': outcomes,
            'route_strata': {'matched_pairs': len(matched), 'mismatched_pairs': len(mismatched),
                             'matched': stratum(matched), 'mismatched': stratum(mismatched)},
            'decision': 'eligible_for_scoped_promotion' if all(gates.values()) else 'retain_baseline'}


def evaluate(prereg, results):
    """Evaluate immutable receipts; reject missing trials rather than drop them.

    Promotion eligibility is a numerical scoped gate, never deployment authority.
    Caller must verify pin digests and signed provenance outside this pure function.
    """
    _validate(prereg, results)
    rows = {profile: [r for r in results if r['profile'] == profile] for profile in PROFILES}
    summaries = {profile: _summary(values) for profile, values in rows.items()}
    def comparison(a, b): return _compare(prereg, (a, rows[a]), (b, rows[b]), summaries)
    return {'schema': 'factory.held-out-evaluation/v1', 'primary_metric': prereg['primary_metric'],
            'profiles': summaries, 'adjacent': [comparison(a, b) for a, b in zip(PROFILES, PROFILES[1:])],
            'combined': comparison(PROFILES[0], PROFILES[-1]),
            'uncertainty': {'independent_tasks': len(prereg['tasks']), 'repeats_per_task': 1,
                'method': 'Exact descriptive paired sign counts; no significance or causal claim.',
                'limitations': ['Small held-out task sample; no within-task replication.',
                               'Route-set equality does not attest identical weights or inference settings.',
                               'Matched-route strata are descriptive; no mismatched pair is discarded.']}}
