"""Opt-in OS containment for native execution; never a shell authorization filter.

A dedicated size-bounded workspace filesystem must already be provisioned by the
operator. This module never mounts, formats or changes a host filesystem.
"""
import os
from pathlib import Path
import re
import stat

PROFILE = 'bounded-native-v1'
MAX_WORKSPACE_BYTES = 1024**3
TMP_BYTES = 512*1024**2
HOME = '/workspace/.git/factory-home'
ENVIRONMENT = {'HOME':HOME,'OH_CONVERSATIONS_PATH':HOME+'/conversations',
               'OH_BASH_EVENTS_DIR':HOME+'/bash_events','OH_PERSISTENCE_DIR':HOME+'/.openhands'}


def enabled(profile):
    if profile not in (None,PROFILE):
        raise ValueError('unknown bounded execution profile')
    return profile is not None


def mounts():
    def unescape(text):
        return re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),text)
    records={}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields=line.split();separator=fields.index('-')
        records[unescape(fields[4])]={'id':fields[0],'type':fields[separator+1]}
    return records


def validate_workspace(workspace,profile):
    if not enabled(profile):return None
    p=Path(workspace)
    if not p.is_absolute() or p.resolve()!=p or p in (Path('/'),Path.home()):
        raise ValueError('canonical private workspace required')
    info=p.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
        raise ValueError('private owned workspace required')
    try:
        records=mounts()
        record=records.get(str(p))
    except (OSError,ValueError):raise ValueError('Linux mount verification unavailable') from None
    if not record:
        raise ValueError('dedicated bounded workspace mount required')
    if any(Path(name)!=p and p in Path(name).parents for name in records):
        raise ValueError('descendant workspace mount refused')
    fs=os.statvfs(p);size=fs.f_blocks*fs.f_frsize
    if not 0<size<=MAX_WORKSPACE_BYTES:
        raise ValueError('workspace filesystem capacity exceeds bound')
    return {'device':info.st_dev,'inode':info.st_ino,'mount_id':record['id'],
            'filesystem':record['type'],'capacity_bytes':size}


def temporary_option(uid):
    return f'rw,exec,nosuid,nodev,size={TMP_BYTES},uid={uid},gid={uid},mode=0700'


def worker_arguments(profile,uid):
    if not enabled(profile):return []
    return ['--read-only','--tmpfs','/tmp:'+temporary_option(uid)]+[
        part for key,value in ENVIRONMENT.items() for part in ('--env',key+'='+value)]


def effective_environment(environment,profile):
    if not enabled(profile):return environment
    return [key+'='+value for key,value in ENVIRONMENT.items()]+[
        x for x in environment if x.partition('=')[0] not in ENVIRONMENT]


def environment_matches(actual,expected,profile):
    if not enabled(profile):return actual==expected
    # Docker may reorder CLI overrides. Require the same unique names and values.
    def valid(values):
        return (isinstance(values,list) and
                all(isinstance(v,str) and '=' in v for v in values) and
                len({v.partition('=')[0] for v in values})==len(values))
    return valid(actual) and valid(expected) and sorted(actual)==sorted(expected)


def validate_worker(worker,profile,uid):
    if not enabled(profile):return
    host=worker.get('HostConfig') or {}
    if (host.get('ReadonlyRootfs') is not True or
            host.get('Tmpfs')!={'/tmp':temporary_option(uid)} or
            type(host.get('ShmSize')) is not int or not 0<host['ShmSize']<=64*1024**2):
        raise ValueError('bounded worker storage configuration changed')
    extras=[m for m in worker.get('Mounts',[]) if m.get('Type')=='tmpfs']
    if len(extras)>1 or any(m.get('Destination')!='/tmp' for m in extras):
        raise ValueError('unexpected writable tmpfs')
