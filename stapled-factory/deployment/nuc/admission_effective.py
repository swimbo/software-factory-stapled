"""Capture the non-secret digest of one loaded factory service configuration."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from admission_install import _directory, _regular, DROPIN

UNITS=frozenset(('factory-endpoint','factory-ingress'))


def _command(args):
    runtime='/run/user/'+str(os.getuid())
    env={'PATH':'/usr/bin:/bin','HOME':str(Path.home()),'XDG_RUNTIME_DIR':runtime,
         'DBUS_SESSION_BUS_ADDRESS':'unix:path='+runtime+'/bus'}
    try:
        result=subprocess.run(['/usr/bin/systemctl','--user',*args],check=True,stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL,timeout=15,env=env)
    except (OSError,subprocess.SubprocessError) as exc:raise ValueError('systemd readback unavailable') from exc
    if len(result.stdout)>1024*1024:raise ValueError('systemd readback too large')
    return result.stdout.decode('utf-8')


def _static_exec(value):
    matches=re.findall(r'\{ (path=[^;]* ; argv\[\]=[^;]* ; ignore_errors=(?:yes|no) ;) ',value)
    if len(matches)!=1 or value.count('{ path=')!=1:
        raise ValueError('unsupported loaded command format')
    return matches[0]


def _fields(raw):
    fields={}
    for line in raw.splitlines():
        if '=' not in line:raise ValueError('invalid systemd property')
        key,value=line.split('=',1)
        if key in fields:raise ValueError('duplicate systemd property')
        fields[key]=value
    return fields


def capture_effective(name, service_dir):
    """Read loaded unit, its only drop-in and manager environment without exposing values."""
    if name not in UNITS:raise ValueError('unsupported guarded service')
    service_dir=_directory(service_dir)
    unit=service_dir/(name+'.service');directory=_directory(service_dir/(name+'.service.d'))
    dropin=directory/DROPIN
    unit_raw=_regular(unit);dropin_raw=_regular(dropin)
    if any(token in unit_raw or token in dropin_raw for token in (b'$',b'%',b'EnvironmentFile=')):
        raise ValueError('unsupported dynamic service configuration')
    properties=('FragmentPath','DropInPaths','NeedDaemonReload','WorkingDirectory','User','Group',
                'Environment','EnvironmentFiles','ExecStart','ExecStartPre')
    manager_before=_command(['show-environment'])
    args=['show','--all',name]
    for prop in properties:args.extend(['-p',prop])
    fields=_fields(_command(args))
    if fields.get('NeedDaemonReload')!='no':raise ValueError('systemd reload required')
    if fields.get('FragmentPath')!=str(unit) or fields.get('DropInPaths','').split()!=[str(dropin)]:
        raise ValueError('effective service files differ')
    if fields.get('EnvironmentFiles',''):
        raise ValueError('environment files are unsupported')
    for key in ('WorkingDirectory','User','Group','Environment','ExecStart','ExecStartPre'):
        if key not in fields:raise ValueError('loaded service property unavailable')
    later=_fields(_command(args))
    stable=[key for key in properties if key not in ('ExecStart','ExecStartPre')]
    if (any(later.get(key)!=fields.get(key) for key in stable) or
            any(_static_exec(later.get(key,''))!=_static_exec(fields[key]) for key in ('ExecStart','ExecStartPre')) or
            _regular(unit)!=unit_raw or _regular(dropin)!=dropin_raw or _command(['show-environment'])!=manager_before):
        raise ValueError('service configuration changed during capture')
    value={'unit_sha256':hashlib.sha256(unit_raw).hexdigest(),
           'dropin_sha256':hashlib.sha256(dropin_raw).hexdigest(),
           'manager_environment_sha256':hashlib.sha256(manager_before.encode()).hexdigest(),
           'working_directory':fields['WorkingDirectory'],'user':fields['User'],'group':fields['Group'],
           'environment_sha256':hashlib.sha256(fields['Environment'].encode()).hexdigest(),
           'exec_start':_static_exec(fields['ExecStart']),'exec_start_pre':_static_exec(fields['ExecStartPre'])}
    encoded=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    return {'unit_sha256':value['unit_sha256'],'dropin_sha256':value['dropin_sha256'],
            'config_sha256':hashlib.sha256(encoded).hexdigest()}
