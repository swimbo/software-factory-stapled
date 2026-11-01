"""Conservative offline detector over bounded, payload-free completed results.

The caller durably saves returned state before attempting the one intervention.
A replay never reissues that intervention. Stopping and delivery are caller-owned.
"""
import copy
import math
import re

SCHEMA = 'factory.stall-detection/v1'
DEFAULT_POLICY = {'window': 4, 'min_span_seconds': 5,
                  'post_intervention_window': 3, 'history_limit': 12}
_SIGNATURE = re.compile(r'[0-9a-f]{64}\Z')
_SAMPLE_KEYS = {'seq', 'at_seconds', 'action_signature', 'error_signature',
                'evidence_signature', 'workspace_signature', 'complete'}
_STATE_KEYS = {'schema', 'policy', 'history', 'last_sample', 'intervention_seq', 'stopped'}


def _policy(value):
    if value is not None and (not isinstance(value, dict) or set(value) - DEFAULT_POLICY.keys()):
        raise ValueError('invalid stall policy')
    result = {**DEFAULT_POLICY, **(value or {})}
    for name, low, high in [('window', 3, 12), ('post_intervention_window', 3, 12),
                            ('history_limit', 6, 12)]:
        if type(result[name]) is not int or not low <= result[name] <= high:
            raise ValueError('invalid stall policy bounds')
    span = result['min_span_seconds']
    if type(span) not in (int, float) or not math.isfinite(span) or not 1 <= span <= 120:
        raise ValueError('invalid stall duration')
    if result['history_limit'] < max(result['window'], result['post_intervention_window'], 6):
        raise ValueError('history cannot hold detection window')
    return result


def _sample(value):
    if not isinstance(value, dict) or set(value) != _SAMPLE_KEYS:
        raise ValueError('invalid stall sample')
    if type(value['seq']) is not int or value['seq'] < 0 or type(value['complete']) is not bool:
        raise ValueError('invalid stall sequence or completion')
    at = value['at_seconds']
    if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
        raise ValueError('invalid stall time')
    for name in ('action_signature', 'error_signature', 'evidence_signature', 'workspace_signature'):
        signature = value[name]
        if name == 'error_signature' and signature is None:
            continue
        if not isinstance(signature, str) or not _SIGNATURE.fullmatch(signature):
            raise ValueError('stall signatures must be SHA256 digests')


def _restore(state, policy):
    if state is None:
        return {'schema': SCHEMA, 'policy': policy, 'history': [], 'last_sample': None,
                'intervention_seq': None, 'stopped': False}
    if (not isinstance(state, dict) or set(state) != _STATE_KEYS or state['schema'] != SCHEMA
            or state['policy'] != policy or type(state['stopped']) is not bool):
        raise ValueError('invalid restored stall state or changed policy')
    history = state['history']
    if not isinstance(history, list) or len(history) > policy['history_limit']:
        raise ValueError('invalid restored stall history')
    previous = None
    for item in history:
        _sample(item)
        if not item['complete'] or previous is not None and (
                item['seq'] <= previous['seq'] or item['at_seconds'] < previous['at_seconds']):
            raise ValueError('invalid restored stall history order')
        previous = item
    last = state['last_sample']
    if last is not None:
        _sample(last)
    if previous is not None and (last is None or previous['seq'] > last['seq']
                                or previous['at_seconds'] > last['at_seconds']):
        raise ValueError('restored stall cursor differs')
    intervention = state['intervention_seq']
    if intervention is not None and (type(intervention) is not int or intervention < 0
                                     or last is None or intervention > last['seq']):
        raise ValueError('invalid restored intervention claim')
    if state['stopped'] and intervention is None:
        raise ValueError('stopped state lacks intervention claim')
    return copy.deepcopy(state)


def _stalled(history, policy):
    window = history[-policy['window']:]
    if (len(window) == policy['window'] and window[0]['error_signature'] is not None
            and window[-1]['at_seconds'] - window[0]['at_seconds'] >= policy['min_span_seconds']
            and all(len({item[key] for item in window}) == 1 for key in
                    ('action_signature', 'error_signature', 'evidence_signature', 'workspace_signature'))):
        return True
    window = history[-6:]
    if len(window) != 6 or window[-1]['at_seconds'] - window[0]['at_seconds'] < policy['min_span_seconds']:
        return False
    workspace = [item['workspace_signature'] for item in window]
    return (workspace[0] != workspace[1] and workspace == workspace[:2] * 3
            and all(len({item[key] for item in window}) <= 2
                    for key in ('action_signature', 'evidence_signature')))


def observe(state, sample, policy=None):
    """Return a new bounded state and observe/intervene/stop decision.

    Duplicate observations are harmless. Missing/incomplete tool observations
    cannot trigger detection; elapsed time or unchanged workspace alone cannot.
    """
    effective = _policy(state.get('policy') if policy is None and isinstance(state, dict) else policy)
    _sample(sample)
    result = _restore(state, effective)
    last = result['last_sample']
    if last is not None:
        if sample['seq'] < last['seq'] or sample['at_seconds'] < last['at_seconds']:
            raise ValueError('stall observation regressed')
        if sample['seq'] == last['seq']:
            if sample != last:
                raise ValueError('stall replay differs')
            return result, 'stop' if result['stopped'] else 'observe'
    result['last_sample'] = copy.deepcopy(sample)
    if result['stopped']:
        return result, 'stop'
    if not sample['complete']:
        return result, 'observe'
    result['history'] = (result['history'] + [copy.deepcopy(sample)])[-effective['history_limit']:]
    if not _stalled(result['history'], effective):
        return result, 'observe'
    if result['intervention_seq'] is None:
        result['intervention_seq'] = sample['seq']
        return result, 'intervene'
    after = sum(item['seq'] > result['intervention_seq'] for item in result['history'])
    if after >= effective['post_intervention_window']:
        result['stopped'] = True
        return result, 'stop'
    return result, 'observe'
