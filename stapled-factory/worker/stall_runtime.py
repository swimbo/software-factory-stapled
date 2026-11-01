"""Opt-in host observer: one durable diagnostic, then original-namespace fencing."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import re
import tempfile
import time

from stall_detection import observe

PROFILE='stall-v1'
CAPABILITIES='auto-common-v1'
PROFILE_POLICY={'window':3,'min_span_seconds':4,'post_intervention_window':3,'history_limit':12}
LLM_FIELDS=frozenset(('model','api_key','base_url','usage_id','max_output_tokens'))
DIAGNOSTIC=('Repeated tool failures or an edit loop produced no new evidence. '
            'Stop repeating the same action. Inspect one relevant source or diagnostic, '
            'choose a different supported approach, or report the blocker accurately. '
            'Do not install dependencies, restart the attempt, or expand its scope.')


class StallIntervention(RuntimeError):
    def __init__(self,status):
        if status not in ('stopped','fencing_uncertain'):raise ValueError('unknown intervention status')
        self.intervention_status=status
        super().__init__('Stall intervention requires reconciliation: '+status)


def validate_profile(config,profile):
    if profile is None:return
    if profile!=PROFILE or config['provision'].get('execution_profile')!='bounded-native-v1':
        raise ValueError('stall profile requires bounded execution')
    llm=config['payload']['agent_settings']['llm']
    if not isinstance(llm,dict) or set(llm)-LLM_FIELDS or llm.get('model')!='anthropic/auto':
        raise ValueError('unsupported common model capability options')


def manifest():
    return {'schema':'factory.stall-profile/v1','profile':PROFILE,'model_capabilities':CAPABILITIES,
            'llm_fields':sorted(LLM_FIELDS),'diagnostic_sha256':hashlib.sha256(DIAGNOSTIC.encode()).hexdigest(),
            'poll_seconds':2,'max_polls':120,'diagnostic_run':False,
            'policy':PROFILE_POLICY,'requires_running_sdk':True}


def _private(info,directory=False):
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or info.st_uid!=os.geteuid() or info.st_mode&0o077
            or not directory and info.st_nlink!=1):
        raise ValueError('private stall state required')


def _save(path,value):
    fd,temp=tempfile.mkstemp(prefix='.stall-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,sort_keys=True,separators=(',',':'))
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
        folder=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(folder)
        finally:os.close(folder)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def _load(path,identity):
    if not path.exists() and not path.is_symlink():
        return {'schema':'factory.stall-session/v1','identity':identity,'detector':None,'effect':'observing','delivery_status':None,'seen_observations':[]}
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        _private(os.fstat(stream.fileno()));raw=stream.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('stall state exceeds bound')
    value=json.loads(raw)
    if (set(value)!={'schema','identity','detector','effect','delivery_status','seen_observations'} or value['schema']!='factory.stall-session/v1'
            or value['delivery_status'] not in (None,'claimed','sent','uncertain','not_sent')
            or value['identity']!=identity or value['effect'] not in
            ('observing','intervention_claimed','intervention_sent','stop_claimed','stopped','fencing_uncertain')):
        raise ValueError('stall state identity or phase differs')
    seen=value['seen_observations']
    if (not isinstance(seen,list) or len(seen)>400 or len(set(seen))!=len(seen) or
            any(not isinstance(s,str) or not re.fullmatch('[0-9a-f]{64}',s) for s in seen)):
        raise ValueError('invalid stall observation cursor')
    return value


def _halt(path,state,stop):
    state['effect']='stop_claimed';_save(path,state)
    try:stop()
    except Exception:
        state['effect']='fencing_uncertain';_save(path,state)
        raise StallIntervention('fencing_uncertain') from None
    state['effect']='stopped';_save(path,state)
    raise StallIntervention('stopped')


def run_session(path,identity,*,sample,read,send,stop,validate,sleep=None,ready=None,policy=None):
    """Serialize observations, save an intent before delivery, never retry delivery.

    A lost/uncertain delivery is contained using the original allocation deadline
    record. Unknown observations never become evidence of a stalled tool.
    """
    sleep=time.sleep if sleep is None else sleep
    path=Path(path)
    if not path.is_absolute() or path.resolve()!=path:raise ValueError('canonical stall path required')
    path.parent.mkdir(mode=0o700,exist_ok=True);_private(path.parent.stat(),directory=True)
    fd=os.open(str(path)+'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        _private(os.fstat(fd));fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=_load(path,identity)
        if state['effect']=='stopped':raise StallIntervention('stopped')
        if state['effect'] in ('intervention_claimed','stop_claimed','fencing_uncertain'):
            if state['delivery_status']=='claimed':state['delivery_status']='uncertain'
            _halt(path,state,stop)
        for _ in range(120):
            validate()
            result=read()
            if result is not None:return result
            if state['effect']=='intervention_sent' and ready is not None and not ready():
                result=read()
                if result is not None:return result
                _halt(path,state,stop)
            value=sample(state)
            if value is not None:
                value=dict(value);source=value.pop('_source_id',None)
                sources=value.pop('_source_ids',[source] if source is not None else [])
                progress=value.pop('_progress_between',False)
                if sources:
                    if all(s in state['seen_observations'] for s in sources):
                        sleep(2);continue
                    state['seen_observations']+=list(dict.fromkeys(s for s in sources if s not in state['seen_observations']))
                    if len(state['seen_observations'])>400:raise ValueError('stall observation limit')
                    previous=state['detector']['last_sample'] if state['detector'] else None
                    value['seq']=previous['seq']+1 if previous else 1
                if progress and state['detector'] is not None:
                    state['detector']['history']=[]
                state['detector'],decision=observe(state['detector'],value,policy)
                if decision=='intervene':
                    validate()
                    if ready is not None and not ready():
                        result=read()
                        if result is not None:return result
                        state['delivery_status']='not_sent';_halt(path,state,stop)
                    state['effect']='intervention_claimed';state['delivery_status']='claimed'
                _save(path,state)
                if decision=='stop':_halt(path,state,stop)
                if decision=='intervene':
                    validate()
                    try:send(DIAGNOSTIC)
                    except Exception:
                        state['delivery_status']='uncertain';_halt(path,state,stop)
                    state['effect']='intervention_sent';state['delivery_status']='sent';_save(path,state)
            sleep(2)
        return None
    finally:os.close(fd)


class StallAdapter:
    """Observe one existing SDK loop on the host; never issue /run or recreate it."""
    def __init__(self,base,store,attempt_id,runtime_path,workspace):
        import deadline_watchdog as deadline
        self.base,self.store=base,store
        self.runtime_path=runtime_path;self.workspace=workspace
        with store._transaction() as (db,_):
            row=db.execute('SELECT allocation_id FROM allocations WHERE attempt_id=?',(attempt_id,)).fetchone()
        if not row:raise ValueError('original allocation required')
        self.deadline_path=deadline._path(store,row[0]);self.deadline=deadline._read(self.deadline_path)
        self.path=store.anchor_path.parent/'stalls'/(row[0]+'.json')
        from operator_cli import _private_file
        self.runtime_hash=hashlib.sha256(_private_file(runtime_path)).hexdigest()
        self.identity={'attempt_id':attempt_id,'conversation_id':self.deadline['conversation_id'],
                       'allocation_id':row[0],'runtime_sha256':self.runtime_hash,
                       'binding_sha256':hashlib.sha256(json.dumps(self.deadline['binding'],sort_keys=True).encode()).hexdigest()}

    def _validate(self):
        import deadline_watchdog as deadline
        from operator_cli import _private_file
        if hashlib.sha256(_private_file(self.runtime_path)).hexdigest()!=self.runtime_hash:
            raise ValueError('runtime changed during observation')
        d=self.deadline
        deadline.validate_deadline(self.store,**{k:d[k] for k in
            ('attempt_id','request_hash','conversation_id','specification_hash','allocation_id','binding')})

    def _sample(self,cid,state):
        from urllib.parse import quote
        from stall_observation import latest_sample
        from workspace_progress import snapshot
        from docker_transport import DockerTransportError
        try:
            events=[];page=None
            for _ in range(4):
                query='?limit=100&sort_order=TIMESTAMP'+('&page_id='+quote(page,safe='') if page else '')
                result=self.base.transport.request('GET','/api/conversations/'+cid+'/events/search'+query)
                items=result.get('items');page=result.get('next_page_id')
                if not isinstance(items,list) or len(items)>100:return None
                events+=items
                if not page:break
                if not isinstance(page,str) or len(page)>1024:return None
            else:return None
            return latest_sample(events,snapshot(self.workspace),state,
                                 max(0,time.monotonic()-self.deadline['armed_monotonic']))
        except (OSError,ValueError,TypeError,KeyError,DockerTransportError):
            return None  # Incomplete observation is never evidence of no progress.

    def _watch(self,cid):
        import deadline_watchdog as deadline
        if cid!=self.identity['conversation_id']:raise ValueError('original conversation required')
        def send(text):
            value=self.base.transport.request('POST','/api/conversations/'+cid+'/events',
                {'role':'user','content':[{'type':'text','text':text}],'run':False})
            if value.get('success') is not True:raise ValueError('diagnostic delivery uncertain')
        def ready():
            value=self.base.transport.request('GET','/api/conversations/'+cid)
            if value.get('id')!=cid:raise ValueError('original conversation differs')
            return value.get('execution_status')=='running'
        return run_session(self.path,self.identity,sample=lambda state:self._sample(cid,state),
            read=lambda:self.base.read(cid,None),send=send,
            stop=lambda:deadline.expire(self.deadline_path,cancel=True),validate=self._validate,
            ready=ready,policy=PROFILE_POLICY)

    def create(self,cid,payload):
        result=self.base.create(cid,payload)
        return result if result is not None else self._watch(cid)

    def read(self,cid,payload):
        return self._watch(cid)
