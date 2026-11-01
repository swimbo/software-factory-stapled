"""Provision isolated per-attempt Docker resources; does not enable dispatch.

The caller must supply trusted image IDs, commands, environment and workspace.
Budget volumes are deliberately retained, including on failure; they are evidence.
"""
import hashlib
import json
import os
import re
import stat
import subprocess
import time
import uuid
import bounded_profile as bounded

from egress_guard import inspect
from provision_check import validate_identity, validate_volume, resource_arguments, telemetry_volume_name
from netns_firewall import install_proxy_firewall, verify_proxy_firewall


class ProvisionError(RuntimeError):
    pass


_DIAGNOSTIC_OPERATIONS=('volume','network','run','create','start','kill','container')
SAFE_DIAGNOSTIC_CODES=frozenset(
    ['provision.preflight.failed','docker.network.address_pool_exhausted']+
    ['docker.'+op+'.'+kind for op in _DIAGNOSTIC_OPERATIONS for kind in ('failed','timeout','permission_denied','resource_unavailable')])


def _run(args, *, timeout=30):
    # stderr may include sensitive arguments from Docker, so never expose it.
    try:
        return subprocess.run(['docker', *args], check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        error=ProvisionError('Docker operation failed: ' + args[0])
        operation=args[0] if args[0] in _DIAGNOSTIC_OPERATIONS else None
        text=(getattr(exc,'stderr',None) or '')
        if not isinstance(text,str):text=''
        kind='timeout' if isinstance(exc,subprocess.TimeoutExpired) else 'failed'
        if 'permission denied' in text.lower() or 'operation not permitted' in text.lower():kind='permission_denied'
        if 'resource temporarily unavailable' in text.lower():kind='resource_unavailable'
        error.diagnostic_code='docker.'+operation+'.'+kind if operation else None
        if operation=='network' and 'all predefined address pools have been fully subnetted' in text:
            error.diagnostic_code='docker.network.address_pool_exhausted'
        raise error from exc


def provision(*, worker_image, proxy_image, helper_image, worker_command, proxy_command,
              workspace, uid, worker_environment, gateway_key, allocation_id=None, before_credential=None, firewall=None, execution_profile=None):
    try:
        bounded.validate_workspace(workspace,execution_profile)
        if bounded.enabled(execution_profile) and (before_credential is None or firewall is None):
            raise ValueError('bounded execution requires the durable deadline coordinator')
    except (ValueError,OSError):
        raise ProvisionError('bounded workspace or deadline preflight unavailable') from None
    # Validate telemetry opt-in before any Docker observation or mutation.
    telemetry_volume_name(proxy_command, '')
    image_details = {}
    for image in (worker_image, proxy_image, helper_image):
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
            raise ProvisionError('immutable image IDs required')
        image_details[image] = inspect('image', image)
        if image_details[image].get('Id') != image:
            raise ProvisionError('image ID mismatch')
    if not isinstance(uid, int) or uid < 1 or uid > 2147483647:
        raise ProvisionError('invalid UID')
    if not isinstance(gateway_key, bytes) or not gateway_key or b'\n' in gateway_key or b'\r' in gateway_key:
        raise ProvisionError('invalid gateway credential')
    if not all(isinstance(c, list) and c and all(isinstance(x, str) and x and '\x00' not in x for x in c) for c in (worker_command, proxy_command)):
        raise ProvisionError('explicit commands required')
    if not isinstance(worker_environment, list) or any(not isinstance(x, str) or '=' not in x for x in worker_environment):
        raise ProvisionError('explicit worker environment required')
    if (image_details[worker_image].get('Config', {}).get('Env') or []) != worker_environment:
        raise ProvisionError('worker image environment mismatch')
    if any(any(x in item.partition('=')[0].upper() for x in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL')) for item in worker_environment):
        raise ProvisionError('worker credential-like environment forbidden')
    if not os.path.isabs(workspace) or os.path.realpath(workspace) != workspace or not os.path.isdir(workspace):
        raise ProvisionError('absolute canonical workspace required')
    workspace_stat = os.stat(workspace, follow_symlinks=False)
    if workspace_stat.st_uid != os.geteuid() or workspace_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ProvisionError('workspace must be owned and not group/world writable')
    if firewall is not None:
        if not isinstance(firewall, dict) or set(firewall) != {'helper_image', 'gateway_ip'}:
            raise ProvisionError('invalid firewall configuration')
        # This helper runs with network administration privileges and may be
        # different from the volume initializer. Inspect its immutable ID too.
        firewall_image = firewall['helper_image']
        if not isinstance(firewall_image, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', firewall_image):
            raise ProvisionError('immutable firewall helper image ID required')
        if inspect('image', firewall_image).get('Id') != firewall_image:
            raise ProvisionError('firewall helper image ID mismatch')
        if proxy_command.count('--gateway-ip') != 1:
            raise ProvisionError('pinned proxy command required')
        index = proxy_command.index('--gateway-ip')
        if index + 1 >= len(proxy_command) or proxy_command[index + 1] != firewall['gateway_ip']:
            raise ProvisionError('firewall and proxy gateway IP differ')
    alloc = allocation_id if allocation_id is not None else uuid.uuid4().hex
    if not isinstance(alloc, str) or not re.fullmatch(r'[0-9a-f]{32}', alloc):
        raise ProvisionError('invalid reserved allocation ID')
    prefix = 'factory-' + alloc
    sock, budget, anchor, network, worker, proxy = (prefix + '-' + x for x in ('socket', 'budget', 'anchor', 'network', 'worker', 'proxy'))
    telemetry = telemetry_volume_name(proxy_command, alloc)
    volume_roles = [(sock, 'socket'), (budget, 'budget'), (anchor, 'anchor')]
    if telemetry:
        volume_roles.append((telemetry, 'telemetry'))
    telemetry_mount = ['--mount', 'type=volume,src=' + telemetry + ',dst=/telemetry'] if telemetry else []
    created = []
    process = None
    labels = ['--label', 'factory.allocation=' + alloc]
    try:
        for name, role in volume_roles:
            _run(['volume', 'create', *labels, '--label', 'factory.role=' + role, name])
            # Docker volume create is idempotent. Never mount or chown an
            # unexpected pre-existing or bind-backed volume.
            validate_volume(inspect('volume', name), name, role, alloc)
            created.append(('volume', name))
        _run(['network', 'create', '--driver', 'bridge', *labels, network])
        created.append(('network', network))
        # Initialization is isolated, transient, and limited to the new volumes.
        _run(['run', '--rm', *resource_arguments('helper'), '--network', 'none', '--cap-drop', 'ALL', '--cap-add', 'CHOWN',
              '--security-opt', 'no-new-privileges', '--user', '0:0',
              '--mount', 'type=volume,src=' + sock + ',dst=/gateway',
              '--mount', 'type=volume,src=' + budget + ',dst=/budget',
              '--mount', 'type=volume,src=' + anchor + ',dst=/anchor', *telemetry_mount,
              '--entrypoint', '/bin/chown', helper_image, str(uid) + ':' + str(uid), '/gateway', '/budget', '/anchor',
              *(['/telemetry'] if telemetry else [])])
        if telemetry:
            _run(['run', '--rm', *resource_arguments('helper'), '--network', 'none', '--cap-drop', 'ALL',
                  '--security-opt', 'no-new-privileges', '--user', f'{uid}:{uid}', *telemetry_mount,
                  '--entrypoint', '/bin/chmod', helper_image, '0700', '/telemetry'])
        common = ['--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', f'{uid}:{uid}']
        _run(['create', '-i', '--name', proxy, *labels, '--label', 'factory.role=proxy', *common, *resource_arguments('proxy'),
              '--network', network, '--mount', 'type=volume,src=' + sock + ',dst=/gateway',
              '--mount', 'type=volume,src=' + budget + ',dst=/budget',
              '--mount', 'type=volume,src=' + anchor + ',dst=/anchor', *telemetry_mount,
              '--entrypoint', proxy_command[0], proxy_image, *proxy_command[1:]])
        created.append(('container', proxy))
        # docker start -ai keeps stdin connected; key is never in argv or env.
        process = subprocess.Popen(['docker', 'start', '-ai', proxy], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        startup_deadline = time.monotonic() + 5
        while True:
            if process.poll() is not None:
                raise ProvisionError('proxy controller exited during startup')
            state = inspect('container', proxy).get('State') or {}
            if state.get('Running') is True:
                break
            if state.get('Restarting') is True or time.monotonic() >= startup_deadline:
                raise ProvisionError('proxy did not start within the bounded wait')
            time.sleep(0.1)
        _run(['create', '--name', worker, *labels, '--label', 'factory.role=worker', *common, *resource_arguments('worker'), *bounded.worker_arguments(execution_profile,uid),
              '--network', 'none', '--mount', 'type=volume,src=' + sock + ',dst=/gateway,readonly',
              '--mount', 'type=bind,src=' + workspace + ',dst=/workspace',
              '--entrypoint', worker_command[0], worker_image, *worker_command[1:]])
        created.append(('container', worker))
        _run(['start', worker])
        w, p = inspect('container', worker), inspect('container', proxy)
        records = {name: inspect('volume', name) for name, _ in volume_roles}
        validate_identity(w, p, worker_image_id=worker_image, proxy_image_id=proxy_image,
                          worker_command=worker_command, proxy_command=proxy_command,
                          socket_volume=sock, budget_volume=budget, proxy_network=inspect('network', network)['Id'],
                          workspace_source=workspace, volume_records=records, allocation_id=alloc,
                          worker_environment=worker_environment, anchor_volume=anchor, execution_profile=execution_profile)
        network_record = inspect('network', network)
        if firewall is not None:
            install_proxy_firewall(p['Id'], alloc, network_record['Id'], firewall['helper_image'], firewall['gateway_ip'])
        if before_credential is not None:
            before_credential(w, p, records, network_record)
        if firewall is not None:
            after = inspect('container', proxy)
            if (after['Id'] != p['Id'] or after['State']['Pid'] != p['State']['Pid']
                    or after['State']['StartedAt'] != p['State']['StartedAt'] or not after['State']['Running']):
                raise ProvisionError('proxy restarted before credential release')
            verify_proxy_firewall(p['Id'], alloc, network_record['Id'], firewall['helper_image'], firewall['gateway_ip'])
            if process.poll() is not None:
                raise ProvisionError('proxy controller exited before credential release')
        # Release the credential only after the resources pass the trusted preflight.
        process.stdin.write(gateway_key + b'\n')
        process.stdin.flush()
        return {'allocation_id': alloc, 'worker': worker, 'proxy': proxy, 'network': network,
                'socket_volume': sock, 'budget_volume': budget, 'anchor_volume': anchor, 'proxy_process': process}
    except Exception as exc:
        failure_type = type(exc).__name__
        if process is not None:
            process.terminate()
        for kind, name in reversed(created):
            if kind == 'volume' and name in (budget, anchor, telemetry):
                continue
            try:
                _run([kind, 'rm', *(['-f'] if kind == 'container' else []), name])
            except ProvisionError:
                pass
        error=ProvisionError('provisioning failed (' + failure_type + '); budget and anchor volumes retained if created; telemetry retained if created')
        error.diagnostic_code=getattr(exc,'diagnostic_code','provision.preflight.failed')
        raise error from None


def _check_baseline(workspace, baseline):
    if not isinstance(workspace, str) or not os.path.isabs(workspace) or os.path.realpath(workspace) != workspace:
        raise ProvisionError('canonical workspace required')
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
                'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'})
    def git(*args):
        return subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', workspace, *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10, env=env).stdout
    try:
        root = git('rev-parse', '--show-toplevel').strip()
        head = git('rev-parse', '--verify', 'HEAD').strip()
        # Ignored files can alter a restarted task just as untracked files can.
        # A fresh baseline must not silently inherit ignored workspace state.
        dirty = git('status', '--porcelain', '--untracked-files=all', '--ignored=matching')
        if root != workspace or head != baseline or dirty:
            raise ProvisionError('workspace baseline is changed, dirty, or not the repository root')
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProvisionError('workspace baseline unavailable') from exc


def _running_lifecycle(container):
    state = container.get('State') or {}
    pid, started = state.get('Pid'), state.get('StartedAt')
    if (state.get('Running') is not True or not isinstance(pid, int) or isinstance(pid, bool)
            or pid <= 0 or not isinstance(started, str) or not started):
        raise ProvisionError('running container lifecycle unavailable')
    return {'Pid': pid, 'StartedAt': started}


def provision_once(store, attempt_id, request_hash, conversation_id, *, workspace_baseline=None, **configuration):
    """Reserve the allocation durably, then make at most one provision attempt.

    This is a primitive for a trusted coordinator, not an authorization to run an
    unverified image or re-create a lost allocation. The credential is excluded
    from the ledger; identity of the credential source must be bound separately.
    """
    if 'allocation_id' in configuration or 'before_credential' in configuration:
        raise ProvisionError('allocation ID must come from the guarded store')
    if not isinstance(workspace_baseline, str) or not re.fullmatch(r'[0-9a-f]{40}([0-9a-f]{24})?', workspace_baseline):
        raise ProvisionError('verified workspace baseline required')
    bounded.validate_workspace(configuration.get('workspace'),configuration.get('execution_profile'))
    _check_baseline(configuration.get('workspace'), workspace_baseline)
    specification = {**{k: v for k, v in configuration.items() if k != 'gateway_key'}, 'workspace_baseline': workspace_baseline}
    try:
        encoded = json.dumps(specification, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        status, allocation_id = store.reserve_allocation(attempt_id, request_hash, conversation_id, digest)
    except Exception:
        raise ProvisionError('allocation reservation failed') from None
    if status != 'new':
        raise ProvisionError('allocation already reserved; reconcile without reprovisioning')
    def bind(worker, proxy, volumes, network):
        # This narrows but cannot eliminate the race with a running worker.
        _check_baseline(configuration['workspace'], workspace_baseline)
        ids = [worker.get('Id'), proxy.get('Id'), network.get('Id')]
        if any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value) for value in ids):
            raise ProvisionError('full Docker IDs required')
        binding = {'worker_id': ids[0], 'proxy_id': ids[1], 'network_id': ids[2],
                   'worker_lifecycle': _running_lifecycle(worker),
                   'workspace_baseline': workspace_baseline,
                   'volumes': {name: {key: record.get(key) for key in ('Name', 'CreatedAt', 'Driver', 'Scope', 'Labels', 'Options', 'Mountpoint')}
                               for name, record in volumes.items()}}
        if configuration.get('firewall') is not None:
            binding['proxy_lifecycle'] = _running_lifecycle(proxy)
        if bounded.enabled(configuration.get('execution_profile')):
            binding['bounded_workspace']=bounded.validate_workspace(configuration['workspace'],configuration['execution_profile'])
        store.bind_allocation(attempt_id, request_hash, conversation_id, digest, allocation_id, binding)
        if bounded.enabled(configuration.get('execution_profile')):
            from deadline_watchdog import arm_deadline, validate_deadline
            arm_deadline(store,attempt_id,request_hash,conversation_id,digest,allocation_id,binding,configuration['worker_image'],configuration['proxy_image'])
            validate_deadline(store,attempt_id,request_hash,conversation_id,digest,allocation_id,binding)
    return provision(**configuration, allocation_id=allocation_id, before_credential=bind)


def inspect_bound_allocation(store, attempt_id, request_hash, conversation_id, *, workspace_baseline, **configuration):
    """Revalidate the originally bound resources without creating or starting any.

    The caller must supply configuration from a trusted source; neither the stored
    baseline nor these checks authenticate an image or a changed workspace.
    """
    if 'gateway_key' in configuration or 'allocation_id' in configuration or 'before_credential' in configuration:
        raise ProvisionError('invalid reconciliation configuration')
    specification = {**configuration, 'workspace_baseline': workspace_baseline}
    try:
        digest = hashlib.sha256(json.dumps(specification, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        allocation_id, binding = store.lookup_allocation(attempt_id, request_hash, conversation_id, digest)
        prefix = 'factory-' + allocation_id
        sock, budget, anchor, network, worker, proxy = (prefix + '-' + x for x in ('socket', 'budget', 'anchor', 'network', 'worker', 'proxy'))
        w, p, n = inspect('container', worker), inspect('container', proxy), inspect('network', network)
        telemetry = telemetry_volume_name(configuration['proxy_command'], allocation_id)
        volume_names = (sock, budget, anchor) + ((telemetry,) if telemetry else ())
        volumes = {name: inspect('volume', name) for name in volume_names}
        actual = {'worker_id': w.get('Id'), 'proxy_id': p.get('Id'), 'network_id': n.get('Id'),
                  'worker_lifecycle': _running_lifecycle(w),
                  'workspace_baseline': workspace_baseline,
                  'volumes': {name: {key: record.get(key) for key in ('Name', 'CreatedAt', 'Driver', 'Scope', 'Labels', 'Options', 'Mountpoint')}
                              for name, record in volumes.items()}}
        if configuration.get('firewall') is not None:
            actual['proxy_lifecycle'] = _running_lifecycle(p)
        if bounded.enabled(configuration.get('execution_profile')):
            actual['bounded_workspace']=bounded.validate_workspace(configuration['workspace'],configuration['execution_profile'])
        if actual != binding:
            raise ProvisionError('bound Docker resources changed')
        validate_identity(w, p, worker_image_id=configuration['worker_image'], proxy_image_id=configuration['proxy_image'],
                          worker_command=configuration['worker_command'], proxy_command=configuration['proxy_command'],
                          socket_volume=sock, budget_volume=budget, anchor_volume=anchor, proxy_network=n['Id'],
                          workspace_source=configuration['workspace'], volume_records=volumes, allocation_id=allocation_id,
                          worker_environment=configuration['worker_environment'], execution_profile=configuration.get('execution_profile'))
        if configuration.get('firewall') is not None:
            firewall = configuration['firewall']
            verify_proxy_firewall(p['Id'], allocation_id, n['Id'], firewall['helper_image'], firewall['gateway_ip'])
        if bounded.enabled(configuration.get('execution_profile')):
            from deadline_watchdog import validate_deadline
            validate_deadline(store,attempt_id,request_hash,conversation_id,digest,allocation_id,binding)
        return {'allocation_id': allocation_id, 'worker_id': binding['worker_id'], 'proxy_id': binding['proxy_id']}
    except Exception as exc:
        raise ProvisionError('bound allocation unavailable or changed') from exc
