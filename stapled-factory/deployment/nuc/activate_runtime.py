"""Fenced activation core for two factory user services; not a host adapter.

A trusted host adapter must implement capture(), restart_services(names), and
admission_fence(). The fence must independently prevent all new invocations,
including direct Restate ingress, and remain effective through idle capture,
switch, verification, and any rollback. The context must retain the hold on
exit unless its explicit complete() method is called after durable terminal
evidence. A context manager is a contract, not
proof: the adapter and fence must be independently reviewed before use.
This module does not provide a CLI or discover a host. capture() must obtain an
independently complete inventory and authenticated, read-only WorkUnit data;
its booleans are not a substitute for that evidence. Before real use, implement
and review that adapter. Same-owner concurrent tampering is outside the trust
boundary. This core conservatively rejects every nonterminal WorkUnit even if
it is historical and no invocation is running. Prior qualification instead
checked preservation of known WorkUnit states and finished namespaces; this
core does not assert those two policies are equivalent. Restart and rollback
intents are durable before dispatch. An uncertain restart requires observation
and independent proof of quiescence before recovery; an uncertain rollback is
never repeated automatically. Recovery requires both services active. The NUC
adapter does not implement an admission fence. It can check quiescence only
when the original synchronous restart returned and its durable receipt exists;
an interrupted or timed-out request remains unresolved.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import grp
import pwd
import json
import os
from pathlib import Path
import re
import stat
import tempfile

SERVICES = ('factory-endpoint', 'factory-ingress')
TERMINAL = {'unclaimed', 'needs_validation', 'validated', 'gated', 'merged', 'deployed', 'failed', 'abandoned'}


class ManualRecoveryRequired(RuntimeError):
    """Original effect is retained; inspect it, never start a second attempt."""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def _private_group(gid):
    """A user-named private group must contain no other non-root accounts."""
    try:
        current=pwd.getpwuid(os.getuid())
        group=grp.getgrgid(gid)
        members=set(group.gr_mem) | {p.pw_name for p in pwd.getpwall() if p.pw_gid == gid}
    except KeyError:
        return False
    return group.gr_name == current.pw_name and members <= {current.pw_name, 'root'}


def _unsafe_mode(info, private):
    if private:return bool(info.st_mode & 0o077)
    return bool(info.st_mode & 0o002 or info.st_mode & 0o020 and not _private_group(info.st_gid))


def _directory(path, private=False):
    path = Path(path)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            _unsafe_mode(info,private)):
        raise ValueError('unsafe operator directory')
    return path


def _regular(path, *, private=False, maximum=16*1024*1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_mode & (0o077 if private else 0o022) or info.st_size > maximum):
            raise ValueError('unsafe operator file')
        return stream.read(), stat.S_IMODE(info.st_mode)


def _write_new(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def _atomic_service(path, raw, mode):
    _regular(path)
    fd, tmp = tempfile.mkstemp(prefix='.factory-rollout-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw);out.flush();os.fchmod(out.fileno(),mode);os.fsync(out.fileno())
        os.replace(tmp, path);_sync(path.parent)
    finally:
        if os.path.lexists(tmp):os.unlink(tmp)


@contextmanager
def _lock(control):
    fd = os.open(control/'operator.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'r') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('unsafe existing operator lock')
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _verify_release(control, revision, manifest_sha256):
    if not re.fullmatch(r'[a-f0-9]{40}', revision) or not re.fullmatch(r'[a-f0-9]{64}', manifest_sha256):
        raise ValueError('exact release identity required')
    _directory(control/'releases')
    root = _directory(control/'releases'/revision)
    raw, _ = _regular(root/'release-manifest.json')
    if _sha(raw) != manifest_sha256:raise ValueError('release manifest differs')
    manifest = json.loads(raw)
    if manifest.get('revision') != revision or not isinstance(manifest.get('files'),dict):
        raise ValueError('release identity differs')
    required={'local-ci.json','stapled-factory/spine/dist/main.js',
              'stapled-factory/spine/dist/ingress_main.js',
              'stapled-factory/spine/node_modules/@restatedev/restate-sdk/package.json'}
    if not required <= set(manifest['files']):raise ValueError('release runtime files missing')
    for name,digest in manifest['files'].items():
        if (not isinstance(name,str) or not name or name.startswith('/') or
                any(part in ('','.','..') for part in name.split('/')) or
                not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest)):
            raise ValueError('unsafe release manifest path')
        path = root/name
        if not path.resolve().is_relative_to(root.resolve()):raise ValueError('release path escapes')
        # Release dependency executables may be internal symlinks; the resolved
        # target must be a regular operator-owned file within the frozen tree.
        target=path.resolve(strict=True)
        fd=os.open(target,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    _unsafe_mode(info,False) or info.st_size > 1024*1024*1024 or
                    hashlib.file_digest(stream,'sha256').hexdigest()!=digest):
                raise ValueError('release file differs')
    ci_raw,_ = _regular(root/'local-ci.json')
    ci = json.loads(ci_raw)
    if ci.get('passed') is not True or ci.get('revision') != revision:
        raise ValueError('exact release CI required')
    return root


def _capture(host, expected_release=None, expected_runtime_config_sha256=None):
    """Validate trusted adapter evidence; persist hashes, never raw state."""
    value = host.capture()
    if (not isinstance(value,dict) or value.get('authenticated') is not True or
            value.get('inventory_complete') is not True or value.get('operator_active') is not False or
            not isinstance(value.get('workunits'),dict) or not isinstance(value.get('namespaces'),list) or
            not isinstance(value.get('guarded_store'),dict) or not isinstance(value.get('services'),dict)):
        raise ValueError('complete authenticated host evidence required')
    if any(not isinstance(v,dict) or v.get('stage') not in TERMINAL for v in value['workunits'].values()):
        raise ValueError('unfinished WorkUnit remains')
    for namespace in value['namespaces']:
        if (not isinstance(namespace,dict) or namespace.get('role') not in ('worker','proxy') or
                namespace.get('allocation_status') != 'completed' or namespace.get('running') is not True or
                not isinstance(namespace.get('id'),str) or not re.fullmatch(r'[a-f0-9]{64}',namespace['id']) or
                not isinstance(namespace.get('image'),str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',namespace['image']) or
                not isinstance(namespace.get('allocation_id'),str) or not namespace['allocation_id'] or
                not isinstance(namespace.get('started_at'),str) or not namespace['started_at'] or
                namespace.get('role') == 'worker' and namespace.get('sdk_status') != 'finished'):
            raise ValueError('unfinished or unidentified running namespace')
    guarded = value['guarded_store']
    if (not isinstance(guarded.get('store_id'),str) or not guarded['store_id'] or
            type(guarded.get('generation')) is not int or guarded['generation'] < 0 or
            any(not isinstance(guarded.get(k),str) or not re.fullmatch(r'[a-f0-9]{64}',guarded[k])
                for k in ('anchor_sha256','tables_sha256'))):
        raise ValueError('guarded store evidence required')
    if set(value['services']) != set(SERVICES):raise ValueError('service evidence incomplete')
    for info in value['services'].values():
        if (not isinstance(info,dict) or info.get('active') is not True or type(info.get('pid')) is not int or
                info['pid'] <= 0 or not isinstance(info.get('release'),str) or
                expected_release is not None and info['release'] != expected_release):
            raise ValueError('service release or health differs')
    endpoint_config = value['services']['factory-endpoint'].get('runtime_config_sha256')
    if (not isinstance(endpoint_config,str) or not re.fullmatch(r'[a-f0-9]{64}',endpoint_config) or
            expected_runtime_config_sha256 is not None and endpoint_config != expected_runtime_config_sha256):
        raise ValueError('running endpoint runtime configuration differs')
    stable = {k:value[k] for k in ('workunits','namespaces','guarded_store')}
    return {'stable_sha256':_sha(_canonical(stable)), 'workunits':len(value['workunits']),
            'namespaces':len(value['namespaces'])}


def _paths(control, service_dir, journal):
    control = _directory(control, private=True);service_dir = _directory(service_dir)
    rollouts = _directory(control/'rollouts', private=True)
    journal = Path(journal).absolute()
    if journal.parent != rollouts or journal.name in ('','.','..') or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',journal.name):
        raise ValueError('one private rollout directory required')
    return control,service_dir,journal


def _service_bytes(service_dir, old_revision, new_revision):
    old = {};new = {};modes = {}
    old_prefix = ('/releases/'+old_revision+'/').encode();new_prefix = ('/releases/'+new_revision+'/').encode()
    for name in SERVICES:
        path = service_dir/(name+'.service');raw,mode = _regular(path)
        references=re.findall(rb'/releases/([a-f0-9]{40})/',raw)
        if not references or any(ref.decode()!=old_revision for ref in references) or new_prefix in raw:
            raise ValueError('service does not reference exact source release')
        old[name]=raw;new[name]=raw.replace(old_prefix,new_prefix);modes[name]=mode
        def config(value):return [x for x in value.splitlines() if x.startswith(b'Environment=FACTORY_RUNTIME_CONFIG=')]
        if config(old[name]) != config(new[name]):raise ValueError('runtime config changed')
    lines=config(old['factory-endpoint'])
    if len(lines)!=1:
        raise ValueError('exactly one endpoint runtime configuration required')
    binding=lines[0][len(b'Environment=FACTORY_RUNTIME_CONFIG='):]
    if not binding.startswith(b'/') or any(ch in binding for ch in (b' ',b'\t',b'"',b"'",b'\\')):
        raise ValueError('unsupported endpoint runtime configuration format')
    return old,new,modes,_sha(binding)


def _load_journal(journal):
    _directory(journal,private=True)
    raw,_ = _regular(journal/'intent.json',private=True)
    value=json.loads(raw)
    if not isinstance(value,dict) or value.get('schema')!='factory.runtime-activation.v1':
        raise ValueError('invalid original activation intent')
    return value


def observe(control, service_dir, journal):
    """Read retained intent and classify exact unit-file state; never mutate."""
    control,service_dir,journal = _paths(control,service_dir,journal)
    with _lock(control):
        intent=_load_journal(journal);files={}
        for name in SERVICES:
            raw,_=_regular(service_dir/(name+'.service'));digest=_sha(raw)
            files[name] = ('original' if digest==intent['services'][name]['original_sha256'] else
                           'candidate' if digest==intent['services'][name]['candidate_sha256'] else 'foreign')
        terminal = {}
        for name in ('activated', 'rolled-back'):
            path = journal / (name + '.json')
            if not os.path.lexists(path):continue
            try:
                raw, _ = _regular(path, private=True)
                terminal[name] = json.loads(raw)
            except (OSError, ValueError):
                terminal[name] = None
        activated = {'activated': True, 'revision': intent['candidate_revision'],
                     'stable_sha256': intent['stable_sha256']}
        rolled_back = {'restored': True, 'stable_sha256': intent['stable_sha256']}
        completion = journal/'fence-completed.json'
        if os.path.lexists(completion):
            try:
                raw,_ = _regular(completion,private=True);fence_completed=json.loads(raw)
            except (OSError,ValueError):fence_completed=None
        else:fence_completed='absent'
        def terminal_state(name, expected, label):
            if terminal[name] != expected or type(terminal[name].get('activated' if name=='activated' else 'restored')) is not bool:
                return 'manual-reconciliation'
            if fence_completed == 'absent':return label+'-fence-unresolved'
            if fence_completed != {'terminal':name,'terminal_sha256':_sha(_canonical(expected))}:
                return 'manual-reconciliation'
            return label
        if 'foreign' in files.values() or len(terminal) > 1:
            state = 'manual-reconciliation'
        elif 'activated' in terminal:
            state = (terminal_state('activated',activated,'activation-recorded')
                     if set(files.values()) == {'candidate'} and isinstance(terminal['activated'],dict) else 'manual-reconciliation')
        elif 'rolled-back' in terminal:
            state = (terminal_state('rolled-back',rolled_back,'rollback-recorded')
                     if set(files.values()) == {'original'} and isinstance(terminal['rolled-back'],dict) else 'manual-reconciliation')
        else:state='interrupted-or-unresolved' if fence_completed == 'absent' else 'manual-reconciliation'
        return {'state':state,'service_files':files,'source_revision':intent['source_revision'],
                'candidate_revision':intent['candidate_revision']}


def _complete_fence(fence, journal, name):
    terminal,_ = _regular(journal/(name+'.json'),private=True)
    value=json.loads(terminal)
    fence.complete()
    _write_new(journal/'fence-completed.json',_canonical({'terminal':name,'terminal_sha256':_sha(_canonical(value))}))
    _sync(journal)


def _restore(service_dir, journal, intent, host, *, restart=True):
    for name in SERVICES:
        old,mode=_regular(journal/(name+'.previous'),private=True)
        current,_=_regular(service_dir/(name+'.service'))
        hashes=intent['services'][name]
        if _sha(old)!=hashes['original_sha256'] or _sha(current) not in (hashes['original_sha256'],hashes['candidate_sha256']):
            raise ManualRecoveryRequired('service or original backup differs; preserve all state')
    for name in SERVICES:
        old,_=_regular(journal/(name+'.previous'),private=True)
        _atomic_service(service_dir/(name+'.service'),old,intent['services'][name]['mode'])
    if restart:
        _write_new(journal/'rollback-restart-requested.json',_canonical({'services':list(SERVICES)}));_sync(journal)
        host.restart_services(SERVICES)
    after=_capture(host, intent['source_revision'], intent['runtime_config_sha256'])
    if after['stable_sha256'] != intent['stable_sha256']:
        raise ManualRecoveryRequired('prior host evidence changed after restoration')
    if not (journal/'rolled-back.json').exists():
        _write_new(journal/'rolled-back.json',_canonical({'restored':True,'stable_sha256':after['stable_sha256']}));_sync(journal)


def activate(*,control,service_dir,journal,source_revision,source_manifest_sha256,
             candidate_revision,candidate_manifest_sha256,host):
    """One activation; adapter must independently implement the capture contract."""
    control,service_dir,journal=_paths(control,service_dir,journal)
    if source_revision==candidate_revision:raise ValueError('distinct releases required')
    with _lock(control), host.admission_fence() as fence:
        if not callable(getattr(fence,'complete',None)):raise ValueError('explicit admission fence completion required')
        if os.path.lexists(journal):
            raise ValueError('original activation exists; observe or recover it')
        if os.path.lexists(control/'operator-active.json'):
            raise ValueError('operator active; preserve original work')
        _verify_release(control,source_revision,source_manifest_sha256)
        _verify_release(control,candidate_revision,candidate_manifest_sha256)
        old,new,modes,runtime_config_sha256=_service_bytes(service_dir,source_revision,candidate_revision)
        before=_capture(host,source_revision,runtime_config_sha256)
        journal.mkdir(mode=0o700);_sync(journal.parent)
        entries={}
        for name in SERVICES:
            _write_new(journal/(name+'.previous'),old[name]);_write_new(journal/(name+'.candidate'),new[name])
            entries[name]={'original_sha256':_sha(old[name]),'candidate_sha256':_sha(new[name]),'mode':modes[name]}
        intent={'schema':'factory.runtime-activation.v1','source_revision':source_revision,
                'candidate_revision':candidate_revision,'source_manifest_sha256':source_manifest_sha256,
                'candidate_manifest_sha256':candidate_manifest_sha256,'stable_sha256':before['stable_sha256'],
                'runtime_config_sha256':runtime_config_sha256,
                'services':entries}
        _write_new(journal/'intent.json',_canonical(intent));_sync(journal)
        restart_requested=False
        try:
            for name in SERVICES:
                current,_=_regular(service_dir/(name+'.service'))
                if current != old[name]:raise ValueError('service changed before original activation')
                _atomic_service(service_dir/(name+'.service'),new[name],modes[name])
            # Persist the exact original restart intent before issuing it. A
            # timeout or lost response is not evidence that no job was accepted.
            restart_requested=True
            _write_new(journal/'restart-requested.json',_canonical({'services':list(SERVICES)}));_sync(journal)
            host.restart_services(SERVICES)
            _write_new(journal/'restart-completed.json',_canonical({'services':list(SERVICES),'returned':True}));_sync(journal)
            after=_capture(host,candidate_revision,runtime_config_sha256)
            if after['stable_sha256']!=before['stable_sha256']:
                raise ValueError('retained host evidence changed')
            result={'activated':True,'revision':candidate_revision,'stable_sha256':after['stable_sha256']}
            _write_new(journal/'activated.json',_canonical(result));_sync(journal)
            _complete_fence(fence,journal,'activated')
            return result
        except Exception as original:
            if restart_requested:
                raise ManualRecoveryRequired('restart or subsequent observation uncertain; inspect original journal') from original
            try:
                _restore(service_dir,journal,intent,host,restart=False)
                _complete_fence(fence,journal,'rolled-back')
            except Exception as rollback:
                raise ManualRecoveryRequired('activation or restoration uncertain; inspect original journal') from rollback
            raise original


def recover(*,control,service_dir,journal,host):
    """Explicitly restore only exact original units from an interrupted attempt."""
    control,service_dir,journal=_paths(control,service_dir,journal)
    with _lock(control), host.admission_fence() as fence:
        if not callable(getattr(fence,'complete',None)):raise ValueError('explicit admission fence completion required')
        intent=_load_journal(journal)
        if os.path.lexists(journal/'activated.json') or os.path.lexists(journal/'rolled-back.json'):
            raise ManualRecoveryRequired('terminal evidence exists; observe and reconcile the original journal')
        if os.path.lexists(journal/'rollback-restart-requested.json'):
            raise ManualRecoveryRequired('original rollback restart requires reconciliation; never repeat it')
        if os.path.lexists(control/'operator-active.json'):
            raise ValueError('operator active; preserve original work')
        if os.path.lexists(journal/'restart-requested.json'):
            # The adapter must independently establish that the original
            # request cannot still apply before any compensating restart.
            host.assert_restart_quiescent(SERVICES,journal)
        _verify_release(control,intent['source_revision'],intent['source_manifest_sha256'])
        _verify_release(control,intent['candidate_revision'],intent['candidate_manifest_sha256'])
        before=_capture(host,expected_runtime_config_sha256=intent['runtime_config_sha256'])
        if before['stable_sha256'] != intent['stable_sha256']:
            raise ManualRecoveryRequired('retained host evidence changed; manual reconciliation required')
        _restore(service_dir,journal,intent,host)
        _complete_fence(fence,journal,'rolled-back')
        return {'restored':True,'revision':intent['source_revision']}
