"""Frozen evaluation rejects incomplete receipts and counts failed consumption."""
import copy
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness_evaluation import evaluate


def fixture():
    profiles = ['baseline', 'context-v1', 'bounded-native-v1', 'stall-v1']
    prereg = {'primary_metric': 'input_tokens_per_accepted_task', 'tasks': ['a', 'b'],
              'profiles': profiles, 'pins': {'source': 'frozen', 'regression_gate_passed': True},
              'schedule': [{'unit': p+'-'+t, 'task_id': t, 'profile': p}
                           for p in profiles for t in ['a', 'b']]}
    rows = [{**s, 'accepted': True, 'requests': 3, 'input_tokens': 100 if s['profile']=='baseline' else 80,
             'output_tokens': 5, 'elapsed_s': 10, 'gateway_models': ['lane:model'],
             'accounting_complete': True, 'estimated_reserved_usd': .1,
             'stall_false_positives': 0, 'truncations': 0} for s in prereg['schedule']]
    return prereg, rows


def test_combined_gate_and_adjacent_contrasts():
    prereg, rows = fixture(); result = evaluate(prereg, rows)
    assert result['combined']['decision'] == 'eligible_for_scoped_promotion'
    assert len(result['adjacent']) == 3
    assert result['combined']['paired_primary_outcomes'] == {'improved': 2, 'tied': 0, 'worse': 0, 'unknown': 0}
    assert result['profiles']['stall-v1']['input_tokens_per_accepted_task'] == 80


def test_failed_consumption_and_task_regression_cannot_hide_in_total_acceptance():
    prereg, rows = fixture(); rows[0]['accepted'] = False; rows[-1]['accepted'] = False
    result = evaluate(prereg, rows)
    assert result['profiles']['baseline']['input_tokens_per_accepted_task'] == 200
    assert result['profiles']['stall-v1']['input_tokens_per_accepted_task'] == 160
    assert not result['combined']['gates']['task_acceptance_no_loss']
    assert result['combined']['decision'] == 'retain_baseline'


@pytest.mark.parametrize('field,value', [('input_tokens', None), ('output_tokens', None),
    ('stall_false_positives', None), ('truncations', None), ('accounting_complete', False)])
def test_missing_evidence_never_passes(field, value):
    prereg, rows = fixture(); rows[-1][field] = value
    assert evaluate(prereg, rows)['combined']['decision'] == 'retain_baseline'


def test_route_difference_is_reported_and_blocks_unqualified_promotion():
    prereg, rows = fixture(); rows[-1]['gateway_models'] = ['other:model']
    result = evaluate(prereg, rows)['combined']
    assert result['decision'] == 'retain_baseline'
    assert result['route_strata']['matched_pairs'] == 1
    assert result['route_strata']['mismatched_pairs'] == 1


@pytest.mark.parametrize('field,value', [('requests', True), ('input_tokens', -1),
    ('elapsed_s', float('nan')), ('estimated_reserved_usd', float('inf')),
    ('accepted', 1), ('truncations', .5), ('gateway_models', [])])
def test_invalid_receipts_rejected(field, value):
    prereg, rows = fixture(); rows[-1][field] = value
    with pytest.raises(ValueError): evaluate(prereg, rows)


@pytest.mark.parametrize('alter', ['missing', 'duplicate', 'identity'])
def test_schedule_must_match_exactly(alter):
    prereg, rows = fixture()
    if alter == 'missing': rows.pop()
    if alter == 'duplicate': rows.append(copy.deepcopy(rows[0]))
    if alter == 'identity': rows[0]['task_id'] = 'b'
    with pytest.raises(ValueError): evaluate(prereg, rows)


def test_regression_pins_and_latency_are_required():
    prereg, rows = fixture(); prereg['pins']['regression_gate_passed'] = False
    assert evaluate(prereg, rows)['combined']['decision'] == 'retain_baseline'
    prereg['pins']['regression_gate_passed'] = True
    rows[-1]['elapsed_s'] = 13
    assert evaluate(prereg, rows)['combined']['decision'] == 'retain_baseline'


def test_zero_acceptance_is_unknown_ratio_and_no_promotion():
    prereg, rows = fixture()
    for row in rows: row['accepted'] = False
    result = evaluate(prereg, rows)
    assert result['profiles']['baseline']['input_tokens_per_accepted_task'] is None
    assert result['combined']['decision'] == 'retain_baseline'


def test_zero_call_admission_stays_in_consumption_and_route_evidence():
    prereg, rows = fixture()
    rows[-1].update(accepted=False, requests=0, input_tokens=0, output_tokens=0, gateway_models=[])
    result = evaluate(prereg, rows)
    assert result['profiles']['stall-v1']['trials'] == 2
    assert result['combined']['decision'] == 'retain_baseline'
    assert result['combined']['route_strata']['mismatched_pairs'] == 1


def repeated_fixture():
    prereg, original = fixture()
    prereg['repeats_per_task'] = 3
    rows = [{**row, 'unit': row['unit']+'-'+str(repeat), 'repeat': repeat}
            for repeat in range(1, 4) for row in original]
    prereg['schedule'] = [{k: row[k] for k in ('unit', 'task_id', 'profile', 'repeat')}
                         for row in rows]
    return prereg, rows


def test_repeated_trials_keep_all_consumption_and_pair_each_repeat():
    prereg, rows = repeated_fixture()
    # The same task has a lost candidate acceptance and a lost baseline
    # acceptance on different repeats; total accepted counts are still equal.
    next(r for r in rows if r['task_id']=='a' and r['repeat']==1 and r['profile']=='stall-v1')['accepted'] = False
    next(r for r in rows if r['task_id']=='a' and r['repeat']==2 and r['profile']=='baseline')['accepted'] = False
    rows[-1]['gateway_models'] = ['other:model']
    result = evaluate(prereg, list(reversed(rows)))
    assert result['profiles']['baseline']['trials'] == 6
    assert result['profiles']['baseline']['input_tokens_per_accepted_task'] == 120
    assert result['profiles']['stall-v1']['input_tokens_per_accepted_task'] == 96
    assert result['profiles']['stall-v1']['requests'] == 18
    assert result['combined']['gates']['acceptance_no_decline']
    assert not result['combined']['gates']['task_acceptance_no_loss']
    assert result['combined']['decision'] == 'retain_baseline'
    assert result['combined']['paired_primary_outcomes'] == {'improved': 4, 'tied': 0, 'worse': 0, 'unknown': 2}
    strata = result['combined']['route_strata']
    assert (strata['matched_pairs'], strata['mismatched_pairs']) == (5, 1)
    assert strata['matched']['baseline']['trials'] == 5
    assert strata['mismatched']['candidate']['input_tokens'] == 80
    assert result['uncertainty']['independent_tasks'] == 2
    assert result['uncertainty']['repeats_per_task'] == 3
    assert 'not independent tasks' in result['uncertainty']['limitations'][0]


@pytest.mark.parametrize('value', [True, 0, -1, 1.5, '3', None])
def test_invalid_repeat_count_is_rejected(value):
    prereg, rows = fixture(); prereg['repeats_per_task'] = value
    with pytest.raises(ValueError): evaluate(prereg, rows)


@pytest.mark.parametrize('alter', ['missing', 'duplicate', 'mismatch', 'absent', 'bool', 'out_of_range'])
def test_repeated_schedule_and_receipts_are_immutable(alter):
    prereg, rows = repeated_fixture()
    if alter == 'missing':
        rows.pop()
    elif alter == 'duplicate':
        # Keep row count and unique unit IDs, but duplicate a task/repeat pair.
        prereg['schedule'][8]['repeat'] = 1
    elif alter == 'mismatch':
        rows[0]['repeat'] = 2
    elif alter == 'absent':
        del prereg['schedule'][0]['repeat']
    elif alter == 'bool':
        rows[0]['repeat'] = True
    elif alter == 'out_of_range':
        prereg['schedule'][0]['repeat'] = 4
    with pytest.raises(ValueError): evaluate(prereg, rows)


def test_explicit_one_repeat_keeps_legacy_result():
    prereg, rows = fixture(); original = evaluate(prereg, rows)
    prereg['repeats_per_task'] = 1
    for entry in prereg['schedule'] + rows:
        entry['repeat'] = 1
    assert evaluate(prereg, rows) == original
