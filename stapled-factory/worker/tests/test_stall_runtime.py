import hashlib
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from stall_runtime import run_session, StallIntervention, DIAGNOSTIC, PROFILE, validate_profile


def digest(text):return hashlib.sha256(text.encode()).hexdigest()


def callbacks():
    time=[0];calls={'send':[],'stop':0,'read':0}
    def sample(_state=None):
        n=int(time[0]/2)+1
        return {'seq':n,'at_seconds':time[0], 'action_signature':digest('command'),
                'error_signature':digest('exit1'),'evidence_signature':digest('sameerror'),
                'workspace_signature':digest('tree'),'complete':True}
    def read():calls['read']+=1;return None
    def send(text):calls['send'].append(text)
    def stop():calls['stop']+=1
    def sleep(_):time[0]+=2
    return calls,dict(sample=sample,read=read,send=send,stop=stop,sleep=sleep,validate=lambda:None)


def test_persisted_one_shot_intervention_then_fence_same_attempt(tmp_path):
    path=tmp_path/'state.json';tmp_path.chmod(0o700);calls,ops=callbacks()
    with pytest.raises(StallIntervention,match='stopped'):
        run_session(path,{'attempt':'original:0'},**ops)
    state=json.loads(path.read_text());assert state['effect']=='stopped'
    assert calls['send']==[DIAGNOSTIC] and calls['stop']==1
    assert state['identity']=={'attempt':'original:0'}
    with pytest.raises(StallIntervention):run_session(path,{'attempt':'original:0'},**ops)
    assert calls['send']==[DIAGNOSTIC]
    with pytest.raises(ValueError):run_session(path,{'attempt':'different:0'},**ops)


def test_uncertain_message_is_never_retried_and_is_contained(tmp_path):
    tmp_path.chmod(0o700);path=tmp_path/'state.json';calls,ops=callbacks()
    def uncertain(text):calls['send'].append(text);raise TimeoutError('private payload')
    ops['send']=uncertain
    with pytest.raises(StallIntervention) as error:run_session(path,{'attempt':'original'},**ops)
    assert 'private' not in str(error.value) and calls['stop']==1
    with pytest.raises(StallIntervention):run_session(path,{'attempt':'original'},**ops)
    assert calls['send']==[DIAGNOSTIC]


def test_crash_after_claim_before_delivery_never_reissues_message(tmp_path):
    tmp_path.chmod(0o700);path=tmp_path/'state.json';calls,ops=callbacks()
    def crash(text):calls['send'].append(text);raise KeyboardInterrupt()
    ops['send']=crash
    with pytest.raises(KeyboardInterrupt):run_session(path,{'attempt':'original'},**ops)
    assert json.loads(path.read_text())['effect']=='intervention_claimed'
    with pytest.raises(StallIntervention):run_session(path,{'attempt':'original'},**ops)
    assert calls['send']==[DIAGNOSTIC] and calls['stop']==1


def test_completion_after_diagnostic_can_be_observed_without_restart(tmp_path):
    tmp_path.chmod(0o700);calls,ops=callbacks()
    def read():
        calls['read']+=1
        return {'status':'completed','git_revision':'a'*40} if calls['send'] else None
    ops['read']=read
    assert run_session(tmp_path/'state.json',{'attempt':'original'},**ops)['status']=='completed'
    assert calls['send']==[DIAGNOSTIC] and calls['stop']==0


def test_capability_profile_rejects_unsupported_provider_options():
    cfg={'provision':{'execution_profile':'bounded-native-v1'},'payload':{'agent_settings':{'llm':{'model':'anthropic/auto','api_key':'dummy','base_url':'http://127.0.0.1:8123','usage_id':'x','max_output_tokens':1024}}}}
    validate_profile(cfg,PROFILE)
    cfg['payload']['agent_settings']['llm']['reasoning_effort']='high'
    with pytest.raises(ValueError,match='capability'):validate_profile(cfg,PROFILE)
    cfg['payload']['agent_settings']['llm'].pop('reasoning_effort');cfg['provision']['execution_profile']=None
    with pytest.raises(ValueError):validate_profile(cfg,PROFILE)


def test_original_deadline_fences_after_one_message_without_resetting_ledger(tmp_path,monkeypatch):
    from test_deadline_watchdog import fixture
    import deadline_watchdog as deadline
    from stall_runtime import StallAdapter
    from types import SimpleNamespace
    args,containers,commands=fixture(tmp_path,monkeypatch)
    deadline.arm_deadline(*args,'sha256:'+'1'*64,'sha256:'+'2'*64,seconds=300)
    store=args[0];clock=[10000.];monkeypatch.setattr(deadline.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr('stall_runtime.time.sleep',lambda _:clock.__setitem__(0,clock[0]+2))
    monkeypatch.setattr('workspace_progress.snapshot',lambda _:{'complete':True,'sha256':'c'*64})
    counts={'create':0,'message':0,'reads':0}
    class Transport:
        def request(self,method,path,payload=None):
            if method=='POST':counts['message']+=1;assert payload['run'] is False;return {'success':True}
            if '/events/search' not in path:return {'id':args[3],'execution_status':'running'}
            events=[]
            for n in range(counts['reads']):
                events += [{'id':str(n),'kind':'ActionEvent','tool_name':'terminal','tool_call_id':str(n),'action':{'command':'false'}},
                           {'id':'obs'+str(n),'kind':'ObservationEvent','tool_name':'terminal','tool_call_id':str(n),'action_id':str(n),'observation':{'exit_code':1,'content':[{'type':'text','text':'same error'}]}}]
            return {'items':events,'next_page_id':None}
    class Base:
        transport=Transport()
        def create(self,*_):counts['create']+=1;return None
        def read(self,*_):counts['reads']+=1;return None
    runtime=tmp_path/'runtime.json';runtime.write_text('{}');runtime.chmod(0o600)
    with pytest.raises(StallIntervention,match='stopped'):
        StallAdapter(Base(),store,args[1],str(runtime),str(tmp_path)).create(args[3],{})
    assert counts['create']==1 and counts['message']==1
    assert all(not c['State']['Running'] for c in containers.values())
    with store._transaction() as (db,_):
        assert db.execute('SELECT status FROM attempts WHERE attempt_id=?',(args[1],)).fetchone()==('reserved',)
        assert db.execute('SELECT count(*) FROM allocations').fetchone()[0]==1
    assert store.lookup(*args[1:4])==('unknown',None)
    assert len([c for c in commands if c[0]=='kill'])==2


def test_repeated_sdk_observation_ids_do_not_count_as_new_failures(tmp_path):
    tmp_path.chmod(0o700);calls,ops=callbacks();original=ops['sample']
    def replay(state):return {**original(state),'_source_id':digest('same SDK observation')}
    ops['sample']=replay
    assert run_session(tmp_path/'state.json',{'attempt':'original'},**ops) is None
    state=json.loads((tmp_path/'state.json').read_text())
    assert len(state['detector']['history'])==1 and not calls['send'] and calls['stop']==0


def test_productive_diagnostics_between_polls_break_the_error_window(tmp_path):
    from stall_observation import latest_sample
    tmp_path.chmod(0o700);calls,ops=callbacks();events=[];tick=[0]
    def append(command,code,text):
        n=len(events);key='action-'+str(n)
        events.extend([{'id':key,'kind':'ActionEvent','tool_name':'terminal','tool_call_id':key,'action':{'command':command}},
                       {'id':'obs-'+key,'kind':'ObservationEvent','tool_name':'terminal','tool_call_id':key,'action_id':key,
                        'observation':{'exit_code':code,'content':[{'type':'text','text':text}]}}])
    def sample(state):
        tick[0]+=1
        if tick[0]>1:append('inspect new source '+str(tick[0]),0,'new evidence '+str(tick[0]))
        append('same failing command',1,'same error')
        return latest_sample(events,{'complete':True,'sha256':'a'*64},state,tick[0]*2)
    def read():return {'status':'completed','git_revision':'a'*40} if tick[0]>=5 else None
    ops.update(sample=sample,read=read)
    result=run_session(tmp_path/'state.json',{'attempt':'original'},**ops)
    assert result['status']=='completed' and not calls['send'] and calls['stop']==0


def test_failed_fence_retains_uncertainty_and_restart_never_resends_diagnostic(tmp_path):
    tmp_path.chmod(0o700);path=tmp_path/'state.json';calls,ops=callbacks()
    def stop():
        calls['stop']+=1
        if calls['stop']==1:raise OSError('fixture ledger unavailable')
    ops['stop']=stop
    with pytest.raises(StallIntervention,match='fencing_uncertain'):
        run_session(path,{'attempt':'original'},**ops)
    assert json.loads(path.read_text())['effect']=='fencing_uncertain'
    with pytest.raises(StallIntervention,match='stopped'):
        run_session(path,{'attempt':'original'},**ops)
    assert calls['send']==[DIAGNOSTIC] and calls['stop']==2


def test_terminal_sdk_state_is_not_messaged_or_automatically_resumed(tmp_path):
    tmp_path.chmod(0o700);calls,ops=callbacks();ops['ready']=lambda:False
    with pytest.raises(StallIntervention,match='stopped'):
        run_session(tmp_path/'state.json',{'attempt':'original'},**ops)
    assert not calls['send'] and calls['stop']==1
    assert json.loads((tmp_path/'state.json').read_text())['delivery_status']=='not_sent'


def test_inactive_loop_after_delivery_is_fenced_without_new_run_or_message(tmp_path):
    tmp_path.chmod(0o700);calls,ops=callbacks();ops['ready']=lambda:not calls['send']
    with pytest.raises(StallIntervention,match='stopped'):
        run_session(tmp_path/'state.json',{'attempt':'original'},**ops)
    assert calls['send']==[DIAGNOSTIC] and calls['stop']==1


def test_verified_completion_at_terminal_boundary_is_preserved_without_message(tmp_path):
    tmp_path.chmod(0o700);calls,ops=callbacks();ops['ready']=lambda:False
    def read():
        calls['read']+=1
        return {'status':'completed','git_revision':'a'*40} if calls['read']>=5 else None
    ops['read']=read
    result=run_session(tmp_path/'state.json',{'attempt':'original'},**ops)
    assert result['status']=='completed' and not calls['send'] and calls['stop']==0
