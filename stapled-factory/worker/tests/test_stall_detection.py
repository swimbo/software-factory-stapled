"""Offline behavior cases for conservative, one-intervention stall detection."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stall_detection import observe


def signature(value):
    return hashlib.sha256(value.encode()).hexdigest()


def sample(seq, *, action='read', error='missing', evidence='same error', workspace='clean', complete=True):
    return {'seq': seq, 'at_seconds': seq * 2,
            'action_signature': signature(action),
            'error_signature': signature(error) if error is not None else None,
            'evidence_signature': signature(evidence),
            'workspace_signature': signature(workspace), 'complete': complete}


def feed(samples, state=None, policy=None):
    decisions=[]
    for item in samples:
        state, decision=observe(state,item,policy)
        decisions.append(decision)
    return state, decisions


def test_repeated_completed_failure_intervenes_once_then_stops():
    state, decisions=feed([sample(n) for n in range(1,8)])
    assert decisions==['observe']*3+['intervene']+['observe']*2+['stop']
    saved=copy.deepcopy(state)
    state, decision=observe(state,sample(8,workspace='new work'))
    assert decision=='stop' and saved['stopped'] and state['stopped']


def test_unchanged_workspace_with_productive_read_only_work_does_not_stall():
    _, decisions=feed([sample(n,action='read '+str(n),error=None,evidence='finding '+str(n)) for n in range(1,30)])
    assert set(decisions)=={'observe'}
    _, decisions=feed([sample(n,error=None) for n in range(1,30)])
    assert set(decisions)=={'observe'}


def test_new_diagnostic_evidence_prevents_repeated_error_intervention():
    _, decisions=feed([sample(n,evidence='new diagnostic '+str(n)) for n in range(1,15)])
    assert set(decisions)=={'observe'}


def test_pending_long_running_test_is_not_a_sequence_of_failures():
    _, decisions=feed([sample(n,complete=False) for n in range(1,100)])
    assert set(decisions)=={'observe'}
    _, decisions=feed([sample(n) for n in range(1,4)]+[sample(4,complete=False)])
    assert set(decisions)=={'observe'}


def test_edit_oscillation_requires_three_cycles_and_bounded_evidence():
    cases=[sample(n,action='edit '+str(n%2),error=None,evidence='edit '+str(n%2),workspace=str(n%2)) for n in range(1,7)]
    state, decisions=feed(cases)
    assert decisions==['observe']*5+['intervene']
    _, decisions=feed([sample(n,error=None,evidence='new '+str(n),workspace=str(n%2)) for n in range(1,12)])
    assert set(decisions)=={'observe'}
    _, decisions=feed([sample(n,error=None,workspace=str(n)) for n in range(1,12)])
    assert set(decisions)=={'observe'}


def test_progress_after_intervention_never_resets_one_shot_claim():
    state, _=feed([sample(n) for n in range(1,5)])
    state, decision=observe(state,sample(5,error=None,evidence='new result',workspace='changed'))
    assert decision=='observe'
    state=json.loads(json.dumps(state))
    _, decisions=feed([sample(n) for n in range(6,11)],state)
    assert 'intervene' not in decisions and decisions[-1]=='stop'


def test_restart_and_same_sequence_replay_do_not_reissue_intervention():
    state, _=feed([sample(n) for n in range(1,5)])
    restored=json.loads(json.dumps(state));saved=copy.deepcopy(restored)
    result, decision=observe(restored,sample(4))
    assert result==saved and restored==saved and decision=='observe'
    with pytest.raises(ValueError):observe(restored,sample(3))
    with pytest.raises(ValueError):observe(restored,sample(4,evidence='different'))
    assert restored==saved


def test_duration_window_and_configurable_policy_are_enforced():
    state, decisions=feed([sample(n) for n in range(1,5)],policy={'min_span_seconds':10})
    assert set(decisions)=={'observe'}
    _, decisions=feed([sample(n) for n in range(1,6)],policy={'window':5})
    assert decisions[-1]=='intervene'
    with pytest.raises(ValueError):observe(state,sample(5),{'min_span_seconds':5})


def test_bounded_history_and_input_state_are_not_mutated():
    state, _=feed([sample(n,error=None,evidence=str(n)) for n in range(1,50)])
    assert len(state['history'])<=12
    before=copy.deepcopy(state);observe(state,sample(50,error=None))
    assert state==before


@pytest.mark.parametrize('policy',[{'window':True},{'window':2},{'window':13},{'min_span_seconds':-1},
    {'min_span_seconds':float('nan')},{'post_intervention_window':1},{'history_limit':1000},{'surprise':1}])
def test_invalid_policy_is_rejected(policy):
    with pytest.raises(ValueError):observe(None,sample(1),policy)


@pytest.mark.parametrize('field,value',[('seq',True),('seq',-1),('at_seconds',float('inf')),
    ('action_signature','raw command'),('error_signature','secret'),('complete',1),('workspace_signature',None)])
def test_invalid_samples_fail_without_changing_state(field,value):
    state, _=observe(None,sample(1));saved=copy.deepcopy(state);item=sample(2);item[field]=value
    with pytest.raises(ValueError):observe(state,item)
    assert state==saved


def test_time_regression_and_corrupt_restored_state_fail():
    state,_=observe(None,sample(5));item=sample(6);item['at_seconds']=1
    with pytest.raises(ValueError):observe(state,item)
    state['schema']='unknown'
    with pytest.raises(ValueError):observe(state,sample(6))


def test_oscillation_continuing_after_intervention_stops_without_second_message():
    _, decisions=feed([sample(n,action=str(n%2),error=None,evidence=str(n%2),workspace=str(n%2)) for n in range(1,10)])
    assert decisions.count('intervene')==1 and decisions[-1]=='stop'


def test_unknown_payload_fields_and_new_error_kinds_are_not_conflated():
    item=sample(1);item['raw_output']='private tool data'
    with pytest.raises(ValueError):observe(None,item)
    _, decisions=feed([sample(n,error='different error '+str(n)) for n in range(1,12)])
    assert set(decisions)=={'observe'}
