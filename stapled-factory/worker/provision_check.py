"""Fail-closed identity checks for a provisioned networkless worker.

Run immediately before dispatch, in addition to topology validation. This cannot
protect against a privileged Docker operator or prove an image's provenance.
"""
import os
import re
import stat

from egress_guard import UnsafeTopology, validate_networkless

# Fixed per-allocation limits. MemorySwap equals Memory, preventing extra swap.
RESOURCE_LIMITS = {
    'worker': {'Memory': 3 * 1024**3, 'MemorySwap': 3 * 1024**3, 'NanoCpus': 2_000_000_000, 'PidsLimit': 512},
    'proxy': {'Memory': 512 * 1024**2, 'MemorySwap': 512 * 1024**2, 'NanoCpus': 1_000_000_000, 'PidsLimit': 128},
    'helper': {'Memory': 128 * 1024**2, 'MemorySwap': 128 * 1024**2, 'NanoCpus': 500_000_000, 'PidsLimit': 32},
}


def resource_arguments(role):
    limits = RESOURCE_LIMITS[role]
    return ['--memory', str(limits['Memory']), '--memory-swap', str(limits['MemorySwap']),
            '--cpus', str(limits['NanoCpus'] / 1_000_000_000), '--pids-limit', str(limits['PidsLimit'])]


def validate_resources(container, role):
    config = container.get('HostConfig') or {}
    if any(type(config.get(field)) is not int or config[field] != value
           for field, value in RESOURCE_LIMITS[role].items()):
        raise UnsafeTopology(role + ' resource limits missing or changed')


def validate_volume(record, name, role, allocation_id):
    """Reject external or bind-backed volumes before privileged initialization."""
    labels = record.get('Labels') or {}
    if (record.get('Name') != name or record.get('Driver') != 'local'
            or record.get('Scope') != 'local' or record.get('Options')
            or labels.get('factory.allocation') != allocation_id
            or labels.get('factory.role') != role):
        raise UnsafeTopology('volume identity mismatch or unsafe driver options')


def validate_identity(worker, proxy, *, worker_image_id, proxy_image_id,
                      worker_command, proxy_command, socket_volume, budget_volume,
                      proxy_network, workspace_source, volume_records, allocation_id, worker_environment, anchor_volume):
    def require(ok, message):
        if not ok:
            raise UnsafeTopology(message)

    for image in (worker_image_id, proxy_image_id):
        require(isinstance(image, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', image),
                'pinned image ID required')
    require(worker_image_id != proxy_image_id, 'worker and proxy images must differ')
    require(re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', allocation_id or ''), 'invalid allocation ID')
    require(os.path.isabs(workspace_source or ''), 'workspace must be absolute')
    require(os.path.realpath(workspace_source) == workspace_source, 'workspace symlink forbidden')
    st = os.stat(workspace_source, follow_symlinks=False)
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid(), 'workspace must be an owned directory')
    require(not (st.st_mode & 0o022), 'workspace must not be group or world writable')
    validate_networkless(worker, proxy, socket_volume, budget_volume, proxy_network, workspace_source, anchor_volume)
    for container, image, command, role in ((worker, worker_image_id, worker_command, 'worker'),
                                             (proxy, proxy_image_id, proxy_command, 'proxy')):
        validate_resources(container, role)
        require(container.get('Image') == image, role + ' image mismatch')
        cfg = container.get('Config', {})
        require(isinstance(command, list) and command and (cfg.get('Entrypoint') or []) + (cfg.get('Cmd') or []) == command,
                role + ' command mismatch')
        require((cfg.get('Labels') or {}).get('factory.allocation') == allocation_id, role + ' allocation mismatch')
        require((cfg.get('Labels') or {}).get('factory.role') == role, role + ' role mismatch')
    require(worker['Config']['User'] == proxy['Config']['User'], 'socket requires matching numeric users')
    require(isinstance(worker_environment, list), 'explicit worker environment required')
    require((worker['Config'].get('Env') or []) == worker_environment, 'worker environment mismatch')
    for env in worker_environment:
        name = env.partition('=')[0].upper()
        require(not any(s in name for s in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL')), 'credential-like worker environment forbidden')
    require(set(volume_records) == {socket_volume, budget_volume, anchor_volume}, 'volume records mismatch')
    for name, role in ((socket_volume, 'socket'), (budget_volume, 'budget'), (anchor_volume, 'anchor')):
        validate_volume(volume_records[name], name, role, allocation_id)
