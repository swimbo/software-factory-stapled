"""Trusted allocation lifetime supervisor, outside the disposable worker.

This enforces an explicitly authorized deadline/cancellation policy. Stopping a
container is not proof of rollback, a safe retry, or exactly-once shell effects.
The operator, systemd user manager and Docker daemon remain trusted.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

from guarded_store import GuardedAttemptStore

MAX_SECONDS = 300
SCHEMA = 'factory.execution-deadline/v1'


class DeadlineError(RuntimeError):
    pass


def _system(args):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, timeout=15)


def _docker(args):
    try:
        result = _system(['docker', *args])
    except (OSError,subprocess.SubprocessError):
        raise DeadlineError('deadline Docker operation unavailable') from None
    if result.returncode:
        raise DeadlineError('deadline Docker operation unavailable')
    return result.stdout


def _private(info, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
            or (not directory and info.st_nlink != 1)):
        raise DeadlineError('deadline private ownership or mode invalid')


def _directory(path):
    if not path.is_absolute() or path.resolve() != path:
        raise DeadlineError('canonical deadline directory required')
    path.mkdir(mode=0o700, exist_ok=True)
    _private(path.stat(), directory=True)


def _path(store, allocation_id):
    if not isinstance(allocation_id,str) or not re.fullmatch('[0-9a-f]{32}',allocation_id):
        raise DeadlineError('invalid deadline allocation')
    return store.anchor_path.parent/'deadlines'/(allocation_id+'.json')


def _write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(value,stream,sort_keys=True,separators=(',',':'))
        stream.write('\n');stream.flush();os.fsync(stream.fileno())
    folder = os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:os.fsync(folder)
    finally:os.close(folder)


def _read(path):
    path=Path(path)
    if not path.is_absolute() or path.resolve()!=path:
        raise DeadlineError('canonical deadline record required')
    _private(path.parent.stat(),directory=True)
    fd=os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        _private(os.fstat(stream.fileno()))
        raw=stream.read(1024*1024+1)
    if len(raw)>1024*1024:raise DeadlineError('deadline record too large')
    data=json.loads(raw)
    if (data.get('schema')!=SCHEMA or type(data.get('seconds')) is not int
            or not 1<=data['seconds']<=MAX_SECONDS
            or any(type(data.get(k)) not in (int,float) or not math.isfinite(data[k])
                   for k in ('armed_at_unix','deadline_unix','armed_monotonic','deadline_monotonic'))
            or data.get('deadline_unix')!=data['armed_at_unix']+data['seconds']
            or data.get('deadline_monotonic')!=data['armed_monotonic']+data['seconds']
            or not isinstance(data.get('boot_id'),str)):
        raise DeadlineError('invalid deadline record')
    return data


def _boot_id():
    try:
        value=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',value):
            raise ValueError()
        return value
    except (OSError,ValueError):
        raise DeadlineError('Linux boot identity unavailable') from None


def _clock_expired(record):
    if _boot_id()!=record['boot_id']:
        raise DeadlineError('execution boot changed; reconcile without rearming')
    return time.monotonic()>=record['deadline_monotonic']


def _identity(store, attempt_id, request_hash, conversation_id, specification_hash,
              allocation_id, binding):
    return {'db':str(store.db_path),'anchor':str(store.anchor_path),'attempt_id':attempt_id,
        'request_hash':request_hash,'conversation_id':conversation_id,
        'specification_hash':specification_hash,'allocation_id':allocation_id,'binding':binding}


def _bound(store, record):
    # lookup_allocation intentionally requires a reserved attempt. This checked
    # read also permits cleanup of an already-completed/abandoned allocation.
    with store._transaction() as (db,_):
        row=db.execute('SELECT request_hash,conversation_id,status FROM attempts WHERE attempt_id=?',
                       (record['attempt_id'],)).fetchone()
        allocation=db.execute('SELECT specification_hash,allocation_id FROM allocations WHERE attempt_id=?',
                              (record['attempt_id'],)).fetchone()
        binding=db.execute('SELECT binding FROM allocation_bindings WHERE attempt_id=?',
                           (record['attempt_id'],)).fetchone()
        if (not row or row[:2]!=(record['request_hash'],record['conversation_id'])
                or row[2] not in ('reserved','completed','abandoned')
                or allocation!=(record['specification_hash'],record['allocation_id'])
                or not binding or json.loads(binding[0])!=record['binding']):
            raise DeadlineError('deadline allocation binding changed')
        return row[2]


def arm_deadline(store, attempt_id, request_hash, conversation_id, specification_hash,
                 allocation_id, binding, worker_image, proxy_image, seconds=MAX_SECONDS):
    """Arm once after binding, before credential release; a failed arm stays used."""
    if type(seconds) is not int or not 1<=seconds<=MAX_SECONDS:
        raise DeadlineError('invalid execution deadline')
    if any(not isinstance(v,str) or not re.fullmatch('sha256:[0-9a-f]{64}',v)
           for v in (worker_image,proxy_image)):
        raise DeadlineError('immutable deadline images required')
    path=_path(store,allocation_id)
    _directory(store.anchor_path.parent);_directory(path.parent)
    record={'schema':SCHEMA,**_identity(store,attempt_id,request_hash,conversation_id,
        specification_hash,allocation_id,binding),'worker_image':worker_image,'proxy_image':proxy_image,
        'armed_at_unix':time.time(),'armed_monotonic':time.monotonic(),
        'boot_id':_boot_id(),'seconds':seconds}
    record['deadline_unix']=record['armed_at_unix']+seconds
    record['deadline_monotonic']=record['armed_monotonic']+seconds
    if _bound(store,record)!='reserved':raise DeadlineError('only unresolved allocation may arm')
    _container(record,'worker');_container(record,'proxy')
    _write(path,record)
    command=['systemd-run','--user','--quiet','--collect',
        '--unit=factory-deadline-'+allocation_id,'--on-active='+str(seconds)+'s',
        '--timer-property=AccuracySec=1s','--timer-property=RemainAfterElapse=no',
        '--property=Type=oneshot',
        sys.executable,str(Path(__file__).resolve()),'--record',str(path)]
    try:
        result=_system(command)
    except (OSError,subprocess.SubprocessError):
        raise DeadlineError('execution deadline could not be armed') from None
    if result.returncode:
        raise DeadlineError('execution deadline could not be armed')
    return path


def validate_deadline(store, attempt_id, request_hash, conversation_id, specification_hash,
                      allocation_id, binding):
    """Read-only validation of original timer identity; never repair or extend it."""
    record=_read(_path(store,allocation_id))
    expected=_identity(store,attempt_id,request_hash,conversation_id,specification_hash,
                       allocation_id,binding)
    if any(record.get(key)!=value for key,value in expected.items()):
        raise DeadlineError('execution deadline identity differs')
    _bound(store,record)
    if _clock_expired(record):
        raise DeadlineError('execution deadline elapsed; reconcile original allocation')
    try:
        result=_system(['systemctl','--user','is-active','factory-deadline-'+allocation_id+'.timer',
                        'factory-deadline-'+allocation_id+'.service'])
    except (OSError,subprocess.SubprocessError):
        raise DeadlineError('execution deadline supervisor unavailable') from None
    if result.returncode or not {'active','activating'} & set(result.stdout.splitlines()):
        raise DeadlineError('execution deadline supervisor unavailable')
    return record


def _container(record,role):
    identity=record['binding'][role+'_id']
    if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{64}',identity):
        raise DeadlineError('invalid original container ID')
    values=json.loads(_docker(['inspect',identity]));info=values[0]
    state=info.get('State') or {};labels=(info.get('Config') or {}).get('Labels') or {}
    original=record['binding'][role+'_lifecycle']
    if (len(values)!=1 or info.get('Id')!=identity or info.get('Image')!=record[role+'_image']
            or labels.get('factory.allocation')!=record['allocation_id']
            or labels.get('factory.role')!=role
            or (info.get('HostConfig',{}).get('RestartPolicy') or {}).get('Name')!='no'
            or state.get('Restarting') is not False or type(state.get('Running')) is not bool
            or state.get('StartedAt')!=original['StartedAt']
            or state['Running'] and state.get('Pid')!=original['Pid']):
        raise DeadlineError('original '+role+' identity or lifecycle changed')
    return state


def _stop(record,role):
    if _container(record,role)['Running']:
        _docker(['kill','--signal','KILL',record['binding'][role+'_id']])
    if _container(record,role)['Running']:
        raise DeadlineError('original '+role+' did not stop')


def expire(record_path, *, cancel=False):
    """Fence a timed-out/explicitly cancelled allocation; preserve all evidence."""
    record=_read(record_path)
    if not cancel and not _clock_expired(record):
        raise DeadlineError('execution deadline has not elapsed')
    store=GuardedAttemptStore(Path(record['db']),Path(record['anchor']))
    if _path(store,record['allocation_id'])!=Path(record_path):
        raise DeadlineError('deadline record location differs')
    # Private timer identity authorizes containment even if the ledger is locked
    # or quarantined. Durable fencing is separate and may remain uncertain.
    _container(record,'worker');_container(record,'proxy')
    try:
        _stop(record,'worker')
    finally:
        _stop(record,'proxy')
    try:
        status=_bound(store,record)
        if status=='reserved':
            from crash_fence import fence_bound_worker
            fence_bound_worker(store,record['attempt_id'],record['request_hash'],
                record['conversation_id'],record['specification_hash'])
    except Exception as error:
        raise DeadlineError('deadline fencing incomplete; original effects remain uncertain') from error
    _container(record,'worker')
    return {'status':'fenced' if status=='reserved' else 'stopped-'+status,
        'allocation_id':record['allocation_id'],'worker_id':record['binding']['worker_id'],
        'proxy_id':record['binding']['proxy_id'],'evidence_retained':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--record',required=True,type=Path)
    parser.add_argument('--cancel',action='store_true',help='Explicit early cancellation; never rollback/retry')
    args=parser.parse_args()
    print(json.dumps(expire(args.record,cancel=args.cancel)))


if __name__=='__main__':main()
