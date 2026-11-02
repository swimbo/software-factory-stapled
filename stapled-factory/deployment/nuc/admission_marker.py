"""Durable startup hold for an interrupted runtime transition.

This marker alone is not an admission fence. A verified pre-start installation,
network fence, drain and explicit recovery procedure are also required.
"""
import hashlib
import grp
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys
import types

RESTARTABLE = frozenset(('factory-endpoint', 'factory-ingress'))
MODULES = ('admission_identity','admission_nft','admission_install','admission_live','admission_effective')


def _unsafe_unit_mode(info):
    if info.st_mode & 0o002:return True
    if not info.st_mode & 0o020:return False
    try:
        user = pwd.getpwuid(os.getuid());group = grp.getgrgid(info.st_gid)
        members = set(group.gr_mem) | {entry.pw_name for entry in pwd.getpwall() if entry.pw_gid == info.st_gid}
    except KeyError:
        return True
    return group.gr_name != user.pw_name or not members <= {user.pw_name, 'root'}


def _open_control(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise ValueError('unsafe admission control directory')
    return fd


def arm(control, intent_sha256):
    if not isinstance(intent_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', intent_sha256):
        raise ValueError('exact transition intent digest required')
    directory = _open_control(control)
    try:
        fd = os.open('transition', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json.dumps({'schema': 'factory.admission-hold.v1',
                                     'intent_sha256': intent_sha256},
                                    sort_keys=True, separators=(',', ':')).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory)
    finally:
        os.close(directory)


def _read_private(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 65536:
            raise ValueError('unsafe admission control file')
        return stream.read()


def _boot_id():
    value = open('/proc/sys/kernel/random/boot_id', encoding='ascii').read(64).strip()
    if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', value):
        raise ValueError('boot identity unavailable')
    return value


def _hold(directory):
    value = json.loads(_read_private(directory, 'transition'))
    if (not isinstance(value, dict) or set(value) != {'schema', 'intent_sha256'} or
            value['schema'] != 'factory.admission-hold.v1' or
            not isinstance(value['intent_sha256'], str) or
            not re.fullmatch(r'[a-f0-9]{64}', value['intent_sha256'])):
        raise ValueError('invalid transition hold')
    return value


def authorize_restarts(control, intent_sha256, unit_hashes):
    """Prepare one boot-bound permit; caller must first verify the active network fence."""
    if (not isinstance(unit_hashes, dict) or not unit_hashes or not set(unit_hashes) <= RESTARTABLE or
            any(not isinstance(hashes, list) or not 1 <= len(hashes) <= 2 or
                any(not isinstance(h, str) or not re.fullmatch(r'[a-f0-9]{64}', h) for h in hashes)
                for hashes in unit_hashes.values())):
        raise ValueError('invalid permitted service hashes')
    directory = _open_control(control)
    try:
        if _hold(directory)['intent_sha256'] != intent_sha256:
            raise ValueError('transition identity differs')
        value = {'schema': 'factory.restart-permit.v1', 'intent_sha256': intent_sha256,
                 'boot_id': _boot_id(), 'units': unit_hashes}
        fd = os.open('restart-permit', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())
            stream.flush(); os.fsync(stream.fileno())
        os.fsync(directory)
    finally:
        os.close(directory)


def _verified_modules(control):
    if sys.flags.isolated!=1:raise ValueError('isolated Python required for verified guard modules')
    root=Path(control).absolute().parent
    if Path(control).absolute()!=root/'hold' or Path(__file__).resolve()!=root/'guard.py':
        raise ValueError('guard is not in its installed bundle')
    directory=_open_control(root)
    try:
        receipt=json.loads(_read_private(directory,'installation.json'))
        if (not isinstance(receipt,dict) or not isinstance(receipt.get('modules'),dict) or
                set(receipt['modules'])!={name+'.py' for name in MODULES} or
                hashlib.sha256(_read_private(directory,'guard.py')).hexdigest()!=receipt.get('guard_sha256')):
            raise ValueError('guard bundle receipt differs')
        sources={}
        for name in MODULES:
            digest=receipt['modules'][name+'.py']
            source=_read_private(directory,name+'.py')
            if (not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest) or
                    hashlib.sha256(source).hexdigest()!=digest):
                raise ValueError('guard module differs')
            sources[name]=source
    finally:
        os.close(directory)
    sys.path.insert(0,str(root))
    modules={}
    for name in MODULES:
        if name in sys.modules:raise ValueError('admission module already loaded')
        module=types.ModuleType(name);module.__file__=str(root/(name+'.py'))
        sys.modules[name]=module
        exec(compile(sources[name],module.__file__,'exec'),module.__dict__)
        modules[name]=module
    return modules['admission_live'],modules['admission_effective']


def _authorizations(value):
    if not isinstance(value,dict) or set(value)!=RESTARTABLE:
        raise ValueError('both restartable service authorizations required')
    for entries in value.values():
        if not isinstance(entries,list) or not 1<=len(entries)<=2:
            raise ValueError('invalid effective service authorizations')
        for entry in entries:
            if (not isinstance(entry,dict) or set(entry)!={'unit_sha256','config_sha256'} or
                    any(not isinstance(v,str) or not re.fullmatch(r'[a-f0-9]{64}',v) for v in entry.values())):
                raise ValueError('invalid effective service authorization')


def _network(value):
    keys={'identifier','expected_identities','helper_image','docker_sha256','nft_sha256'}
    if not isinstance(value,dict) or set(value)!=keys:raise ValueError('complete network fence identity required')
    return value


def _effective_allowed(effective, entries):
    return any(effective.get('unit_sha256')==entry['unit_sha256'] and
               effective.get('config_sha256')==entry['config_sha256'] for entry in entries)


def authorize_verified_restarts(control, intent_sha256, authorizations, network, service_dir):
    """Write one immutable boot-bound permit after full live and unit verification."""
    _authorizations(authorizations);_network(network)
    # Freeze caller-owned values before checking and recording them.
    raw=json.dumps({'units':authorizations,'network':network},sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if len(raw)>60000:raise ValueError('restart authorization too large')
    frozen=json.loads(raw);authorizations=frozen['units'];network=frozen['network']
    service_dir=str(Path(service_dir).absolute())
    directory=_open_control(control)
    try:
        if _hold(directory)['intent_sha256']!=intent_sha256:raise ValueError('transition identity differs')
        live,effective=_verified_modules(control)
        evidence=live.verify_live(**network)
        if (evidence.get('verified') is not True or evidence.get('ingress_observed') is not True or
                not re.fullmatch(r'[a-f0-9]{64}',evidence.get('identities_sha256','')) or
                not re.fullmatch(r'[a-f0-9]{64}',evidence.get('nft_snapshot_sha256',''))):
            raise ValueError('full admission verification required')
        for name in sorted(RESTARTABLE):
            if not _effective_allowed(effective.capture_effective(name,service_dir),authorizations[name]):
                raise ValueError('effective service configuration differs')
        if _hold(directory)['intent_sha256']!=intent_sha256:raise ValueError('transition changed during authorization')
        value={'schema':'factory.restart-permit.v2','intent_sha256':intent_sha256,'boot_id':_boot_id(),
               'units':authorizations,'network':network,'service_dir':service_dir,
               'initial_identity_sha256':evidence['identities_sha256']}
        encoded=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        name='restart-permit-'+hashlib.sha256(encoded).hexdigest()
        existing=[item for item in os.listdir(directory) if item.startswith('restart-permit-')]
        if len(existing)>=8:raise ValueError('restart permit limit reached')
        fd=os.open(name,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o600,dir_fd=directory)
        with os.fdopen(fd,'wb') as stream:
            stream.write(encoded)
            stream.flush();os.fsync(stream.fileno())
        os.fsync(directory)
    finally:
        os.close(directory)


def _verified_prestart(directory, hold, permit, unit, unit_file, permit_name, permit_raw, modules):
    keys={'schema','intent_sha256','boot_id','units','network','service_dir','initial_identity_sha256'}
    if (set(permit)!=keys or permit['intent_sha256']!=hold['intent_sha256'] or permit['boot_id']!=_boot_id() or
            not isinstance(permit['service_dir'],str) or str(Path(unit_file).absolute().parent)!=permit['service_dir'] or
            not isinstance(permit['initial_identity_sha256'],str) or
            not re.fullmatch(r'[a-f0-9]{64}',permit['initial_identity_sha256'])):
        raise ValueError('verified restart permit differs')
    _authorizations(permit['units']);_network(permit['network'])
    live,effective=modules
    first=effective.capture_effective(unit,permit['service_dir'])
    if not _effective_allowed(first,permit['units'][unit]):raise ValueError('effective service configuration differs')
    evidence=live.verify_restart_fence(**permit['network'])
    if (evidence.get('verified') is not True or evidence.get('ingress_observed') is not False or
            evidence.get('identities_sha256')!=permit['initial_identity_sha256']):
        raise ValueError('live restart fence differs')
    if (effective.capture_effective(unit,permit['service_dir'])!=first or
            _read_private(directory,permit_name)!=permit_raw or _hold(directory)!=hold or _boot_id()!=permit['boot_id']):
        raise ValueError('restart evidence changed during verification')


def prestart(control, unit=None, unit_file=None):
    directory = _open_control(control)
    try:
        try:
            os.stat('transition', dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        if unit not in RESTARTABLE or unit_file is None or os.path.basename(unit_file) != unit + '.service':
            raise RuntimeError('runtime transition requires recovery')
        hold = _hold(directory)
        names=sorted(name for name in os.listdir(directory) if name.startswith('restart-permit-'))
        if names:
            if len(names)>8 or any(not re.fullmatch(r'restart-permit-[a-f0-9]{64}',name) for name in names):
                raise ValueError('invalid restart permit inventory')
            modules=_verified_modules(control)
            for name in names:
                try:
                    raw=_read_private(directory,name)
                    if name!='restart-permit-'+hashlib.sha256(raw).hexdigest():raise ValueError('restart permit digest differs')
                    value=json.loads(raw)
                    if not isinstance(value,dict) or value.get('schema')!='factory.restart-permit.v2':
                        raise ValueError('invalid verified permit')
                    _verified_prestart(directory,hold,value,unit,unit_file,name,raw,modules)
                    return
                except (OSError,ValueError,RuntimeError):
                    continue
            raise ValueError('no verified permit matches the live restart')
        permit_raw = _read_private(directory, 'restart-permit')
        permit = json.loads(permit_raw)
        if (not isinstance(permit, dict) or set(permit) != {'schema', 'intent_sha256', 'boot_id', 'units'} or
                permit['schema'] != 'factory.restart-permit.v1' or
                permit['intent_sha256'] != hold['intent_sha256'] or permit['boot_id'] != _boot_id() or
                not isinstance(permit['units'], dict) or unit not in permit['units'] or
                not isinstance(permit['units'][unit], list)):
            raise ValueError('restart permit differs')
        fd = os.open(unit_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    _unsafe_unit_mode(info) or info.st_size > 65536):
                raise ValueError('unsafe service unit')
            digest = hashlib.sha256(stream.read()).hexdigest()
        if digest not in permit['units'][unit]:
            raise ValueError('service unit differs from restart permit')
        # The permit does not itself establish that host firewall rules are
        # still installed or that all effective systemd drop-ins are pinned.
        raise RuntimeError('restart requires independently verified active network fence and effective unit')
    finally:
        os.close(directory)


if __name__ == '__main__':
    try:
        if len(sys.argv) in (3,5) and sys.argv[1]=='prestart':
            prestart(*sys.argv[2:])
        elif len(sys.argv)==3 and sys.argv[1]=='authorize':
            raw=sys.stdin.buffer.read(65537)
            if len(raw)>65536:raise ValueError('restart request too large')
            request=json.loads(raw)
            if not isinstance(request,dict) or set(request)!={'intent_sha256','units','network','service_dir'}:
                raise ValueError('invalid restart authorization request')
            authorize_verified_restarts(sys.argv[2],request['intent_sha256'],request['units'],
                                        request['network'],request['service_dir'])
        else:sys.exit(2)
    except (OSError, ValueError, RuntimeError, ImportError):
        sys.exit(1)
