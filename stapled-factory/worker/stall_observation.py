"""Reduce bounded SDK action/observation pairs to hashes; never promote tool text."""
import hashlib
import json


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def latest_sample(events,workspace,state,elapsed):
    if workspace.get('complete') is not True or not workspace.get('sha256'):return None
    if not isinstance(events,list) or len(events)>400:raise ValueError('event window exceeds bound')
    detector=state.get('detector') if state and 'detector' in state else state
    seen=set(state.get('seen_observations',[])) if state and 'detector' in state else set()
    actions={};latest=None;responded=set();last_action=None
    unseen=[];samples=[];unknown=False
    for seq,event in enumerate(events,1):
        if event.get('kind')=='ActionEvent':
            actions[event.get('id')]=event;last_action=event
        elif event.get('kind')=='ObservationEvent':
            action=actions.get(event.get('action_id'));obs=event.get('observation') or {}
            source=_digest(event.get('id'))
            if source in seen:continue
            unseen.append(source)
            if (not action or action.get('tool_call_id')!=event.get('tool_call_id')
                    or action.get('tool_name')!=event.get('tool_name')):
                unknown=True;continue
            responded.add(action.get('id'))
            tool=action.get('tool_name')
            if tool not in ('terminal','file_editor','factory_capture'):
                unknown=True;continue
            content=obs.get('content',[]);code=obs.get('exit_code');reason=None
            if tool=='terminal':
                if type(code) is not int or code==-1 or obs.get('timeout') is True:
                    unknown=True;continue
            elif tool=='factory_capture':
                try:
                    data=json.loads(''.join(c['text'] for c in content if c.get('type')=='text'))
                except (ValueError,KeyError,TypeError):data=None
                if data is None:
                    if obs.get('is_error') is not True:
                        unknown=True;continue
                    reason='capture_unavailable'
                else:
                    if (action.get('action') or {}).get('operation')!='capture' or 'capture_complete' not in data:
                        unknown=True;continue
                    if data.get('reason') in ('timeout','cancelled') or not data['capture_complete']:
                        unknown=True;continue
                    code=data.get('exit_code');reason=data.get('reason')
                    content={k:data.get(k) for k in ('preview','digest','capture_complete','reason')}
            failed=obs.get('is_error') is True or type(code) is int and code!=0
            latest={'_source_id':_digest(event.get('id')),'seq':seq,'at_seconds':elapsed,'complete':True,
                    'action_signature':_digest([tool,action.get('action')]),
                    'error_signature':_digest([tool,code,reason,obs.get('is_error',False)]) if failed else None,
                    'evidence_signature':_digest(content),'workspace_signature':workspace['sha256']}
            samples.append(latest)
    if (latest and last_action and last_action.get('id') not in responded
            and _digest([last_action.get('tool_name'),last_action.get('action')])!=latest['action_signature']):
        return None  # A different investigation/test is already in flight.
    if latest is None or detector and detector.get('last_sample') and latest['seq']<=detector['last_sample']['seq']:
        return None
    latest['_source_ids']=list(dict.fromkeys(unseen))
    latest['_progress_between']=unknown or any(
        any(s[k]!=latest[k] for k in ('action_signature','error_signature','evidence_signature'))
        for s in samples[:-1])
    return latest
