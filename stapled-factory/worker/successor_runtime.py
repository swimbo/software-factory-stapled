"""Experimental fenced successor provisioning; never dispatches a task.

Only the already-running, bound proxy may be reused. This module does not
establish image provenance, a real charge ceiling, independent restore authority,
or exclusive host ownership of a workspace. The production entry point remains
blocked. A claimed restart is never automatically re-created after a crash.
"""
import copy
import hashlib
import json
import os
import stat

from egress_guard import inspect
from netns_firewall import verify_proxy_firewall
from provision_check import validate_identity
from provision_runtime import ProvisionError, _check_baseline, _run, _running_lifecycle


def _workspace(workspace, old_workspace, baseline, *, require_clean=True):
    if not isinstance(workspace, str) or os.path.realpath(workspace) != workspace or not os.path.isdir(workspace):
        raise ProvisionError('canonical successor workspace required')
    if os.path.commonpath((workspace, old_workspace)) in (workspace, old_workspace):
        raise ProvisionError('successor workspace overlaps old workspace')
    st = os.stat(workspace, follow_symlinks=False)
    if st.st_uid != os.geteuid() or st.st_mode & 0o022:
        raise ProvisionError('successor workspace permissions invalid')
    git = os.path.join(workspace, '.git')
    if not os.path.isdir(git) or os.path.islink(git) or os.path.exists(os.path.join(git, 'objects', 'info', 'alternates')):
        raise ProvisionError('standalone git metadata required')
    def failed(_):
        raise ProvisionError('workspace scan failed')
    for root, dirs, files in os.walk(workspace, followlinks=False, onerror=failed):
        for name in dirs + files:
            item = os.lstat(os.path.join(root, name))
            if stat.S_ISREG(item.st_mode) and item.st_nlink != 1:
                raise ProvisionError('hardlinked workspace file forbidden')
    if require_clean:
        _check_baseline(workspace, baseline)


def _arg(command, flag, expected):
    if command.count(flag) != 1:
        raise ProvisionError('proxy budget option missing or ambiguous')
    index = command.index(flag)
    if index + 1 == len(command) or command[index + 1] != expected:
        raise ProvisionError('proxy budget identity mismatch')


def _source(store, attempt_id, request_hash, conversation_id, configuration, baseline):
    firewall = configuration.get('firewall')
    if not isinstance(firewall, dict) or set(firewall) != {'gateway_ip', 'helper_image'}:
        raise ProvisionError('gateway firewall required')
    if 'gateway_key' in configuration:
        raise ProvisionError('credential is not accepted by successor provisioning')
    command = configuration['proxy_command']
    _arg(command, '--attempt-id', attempt_id)
    _arg(command, '--gateway-ip', firewall['gateway_ip'])
    for flag, directory in (('--budget-db', '/budget/'), ('--budget-anchor', '/anchor/')):
        if command.count(flag) != 1 or command.index(flag) + 1 >= len(command):
            raise ProvisionError('proxy ledger option missing or ambiguous')
        path = command[command.index(flag) + 1]
        if not path.startswith(directory) or os.path.normpath(path) != path or path == directory:
            raise ProvisionError('proxy ledger path mismatch')
    spec = {**configuration, 'workspace_baseline': baseline}
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    allocation, binding = store.lookup_allocation(attempt_id, request_hash, conversation_id, digest)
    if binding.get('workspace_baseline') != baseline:
        raise ProvisionError('baseline differs from original allocation')
    prefix = 'factory-' + allocation
    sock, budget, anchor, network = (prefix + '-' + x for x in ('socket', 'budget', 'anchor', 'network'))
    old, proxy, net = inspect('container', binding['worker_id']), inspect('container', binding['proxy_id']), inspect('network', network)
    volumes = {name: inspect('volume', name) for name in (sock, budget, anchor)}
    stored_volumes = {name: {key: record.get(key) for key in ('Name', 'CreatedAt', 'Driver', 'Scope', 'Labels', 'Options', 'Mountpoint')} for name, record in volumes.items()}
    state = old.get('State') or {}
    if (old.get('Id') != binding['worker_id'] or state.get('Running') is not False or state.get('Restarting') is not False
            or state.get('StartedAt') != binding['worker_lifecycle']['StartedAt']
            or (old.get('HostConfig', {}).get('RestartPolicy') or {}).get('Name') != 'no'
            or proxy.get('Id') != binding['proxy_id'] or net.get('Id') != binding['network_id']
            or _running_lifecycle(proxy) != binding.get('proxy_lifecycle') or stored_volumes != binding['volumes']):
        raise ProvisionError('original allocation changed or worker not stopped')
    # validate_identity checks structural isolation only when Running is true.
    # The actual stopped state was checked above; alter a copy solely to reuse
    # its structural checks, never as evidence that the old worker is running.
    topology_old = copy.deepcopy(old)
    topology_old['State']['Running'] = True
    validate_identity(topology_old, proxy, worker_image_id=configuration['worker_image'], proxy_image_id=configuration['proxy_image'],
                      worker_command=configuration['worker_command'], proxy_command=command,
                      socket_volume=sock, budget_volume=budget, anchor_volume=anchor, proxy_network=net['Id'],
                      workspace_source=configuration['workspace'], volume_records=volumes, allocation_id=allocation,
                      worker_environment=configuration['worker_environment'])
    verify_proxy_firewall(proxy['Id'], allocation, net['Id'], firewall['helper_image'], firewall['gateway_ip'])
    return allocation, binding, proxy, net, volumes, sock, budget, anchor


def provision_successor(store, attempt_id, request_hash, conversation_id, restart_id, restart_hash,
                        restart_conversation_id, *, configuration, workspace, baseline):
    """Provision once after a durable claim. Never performs a model request."""
    _workspace(workspace, configuration['workspace'], baseline)
    allocation, binding, proxy, net, volumes, sock, budget, anchor = _source(store, attempt_id, request_hash, conversation_id, configuration, baseline)
    claimed = store.claim_restart(attempt_id, restart_id, restart_hash, restart_conversation_id, workspace, baseline)
    if claimed != allocation:
        raise ProvisionError('claimed allocation mismatch')
    worker_id = None
    try:
        # A second inspection closes the normal delay between claim and create.
        _source(store, attempt_id, request_hash, conversation_id, configuration, baseline)
        uid = configuration['uid']
        if type(uid) is not int or uid <= 0:
            raise ProvisionError('invalid uid')
        command = configuration['worker_command']
        name = 'factory-' + allocation + '-successor'
        worker_id = _run(['create', '--name', name, '--label', 'factory.allocation=' + allocation,
                          '--label', 'factory.role=worker', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                          '--user', f'{uid}:{uid}', '--network', 'none',
                          '--mount', 'type=volume,src=' + sock + ',dst=/gateway,readonly',
                          '--mount', 'type=bind,src=' + workspace + ',dst=/workspace',
                          '--entrypoint', command[0], configuration['worker_image'], *command[1:]])
        if len(worker_id) != 64 or any(c not in '0123456789abcdef' for c in worker_id):
            raise ProvisionError('unexpected worker identity')
        worker = inspect('container', worker_id)
        if (worker.get('Id') != worker_id or (worker.get('State') or {}).get('Running') is not False
                or (worker.get('HostConfig', {}).get('RestartPolicy') or {}).get('Name') != 'no'):
            raise ProvisionError('new worker identity or initial state invalid')
        structural = copy.deepcopy(worker)
        structural['State']['Running'] = True
        validate_identity(structural, proxy, worker_image_id=configuration['worker_image'], proxy_image_id=configuration['proxy_image'],
                          worker_command=command, proxy_command=configuration['proxy_command'], socket_volume=sock,
                          budget_volume=budget, anchor_volume=anchor, proxy_network=net['Id'], workspace_source=workspace,
                          volume_records=volumes, allocation_id=allocation, worker_environment=configuration['worker_environment'])
        # Record the concrete container before start. The image and command must
        # be independently trusted to stay idle; this primitive cannot prove that.
        store.bind_restart_worker(attempt_id, restart_id, workspace, baseline, worker_id, None)
        _source(store, attempt_id, request_hash, conversation_id, configuration, baseline)
        _run(['start', worker_id])
        worker = inspect('container', worker_id)
        # Inspect proxy and fence again after start; only the same live proxy may serve it.
        allocation, binding, proxy, net, volumes, sock, budget, anchor = _source(store, attempt_id, request_hash, conversation_id, configuration, baseline)
        validate_identity(worker, proxy, worker_image_id=configuration['worker_image'], proxy_image_id=configuration['proxy_image'],
                          worker_command=command, proxy_command=configuration['proxy_command'], socket_volume=sock,
                          budget_volume=budget, anchor_volume=anchor, proxy_network=net['Id'], workspace_source=workspace,
                          volume_records=volumes, allocation_id=allocation, worker_environment=configuration['worker_environment'])
        if worker['Id'] != worker_id:
            raise ProvisionError('successor identity changed')
        lifecycle = _running_lifecycle(worker)
        store.bind_restart_worker(attempt_id, restart_id, workspace, baseline, worker_id, lifecycle)
        return {'worker_id': worker_id, 'proxy_id': binding['proxy_id'], 'allocation_id': allocation}
    except Exception as exc:
        if worker_id:
            try:
                _run(['kill', '--signal', 'KILL', worker_id])
            except Exception:
                pass
        raise ProvisionError('successor provisioning failed; claim remains consumed') from exc


def inspect_successor(store, attempt_id, request_hash, conversation_id, restart_id, *, configuration, baseline):
    """Reinspect the exact bound successor and original proxy before any use."""
    saved = store.lookup_restart_worker(attempt_id, restart_id)
    if saved['baseline'] != baseline:
        raise ProvisionError('successor baseline changed')
    _workspace(saved['workspace'], configuration['workspace'], baseline, require_clean=False)
    allocation, binding, proxy, net, volumes, sock, budget, anchor = _source(store, attempt_id, request_hash, conversation_id, configuration, baseline)
    worker = inspect('container', saved['worker_id'])
    if (worker.get('Id') != saved['worker_id'] or _running_lifecycle(worker) != saved['lifecycle']
            or (worker.get('HostConfig', {}).get('RestartPolicy') or {}).get('Name') != 'no'):
        raise ProvisionError('successor restarted or changed')
    validate_identity(worker, proxy, worker_image_id=configuration['worker_image'], proxy_image_id=configuration['proxy_image'],
                      worker_command=configuration['worker_command'], proxy_command=configuration['proxy_command'],
                      socket_volume=sock, budget_volume=budget, anchor_volume=anchor, proxy_network=net['Id'],
                      workspace_source=saved['workspace'], volume_records=volumes, allocation_id=allocation,
                      worker_environment=configuration['worker_environment'])
    return {'worker_id': saved['worker_id'], 'proxy_id': binding['proxy_id'], 'allocation_id': allocation}
