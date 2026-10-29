import pytest
from test_egress_guard import networkless
from provision_check import validate_identity, UnsafeTopology


def fixture(tmp_path):
    worker, proxy, socket, budget, network = networkless()
    worker['Mounts'].append({'Type': 'bind', 'Source': str(tmp_path), 'Destination': '/workspace', 'RW': True})
    proxy['Mounts'].append({'Type': 'volume', 'Name': 'anchor', 'Destination': '/anchor', 'RW': True})
    allocation = 'a' * 20
    for container, role, image in ((worker, 'worker', '1'), (proxy, 'proxy', '2')):
        container['Image'] = 'sha256:' + image * 64
        container['Config'].update(Entrypoint=['/run'], Cmd=[role], Labels={'factory.allocation': allocation, 'factory.role': role})
        container['HostConfig'].update(Memory=(3 * 1024**3 if role == 'worker' else 512 * 1024**2),
            MemorySwap=(3 * 1024**3 if role == 'worker' else 512 * 1024**2),
            NanoCpus=(2_000_000_000 if role == 'worker' else 1_000_000_000), PidsLimit=(512 if role == 'worker' else 128))
    records = {name: {'Name': name, 'Driver': 'local', 'Scope': 'local', 'Options': None, 'Labels': {'factory.allocation': allocation, 'factory.role': role}} for name, role in ((socket, 'socket'), (budget, 'budget'), ('anchor', 'anchor'))}
    kw = dict(worker_image_id=worker['Image'], proxy_image_id=proxy['Image'], worker_command=['/run', 'worker'], proxy_command=['/run', 'proxy'], socket_volume=socket, budget_volume=budget, proxy_network=network, workspace_source=str(tmp_path), volume_records=records, allocation_id=allocation, worker_environment=[], anchor_volume='anchor')
    return worker, proxy, kw


def test_accepts_matching_identity(tmp_path):
    validate_identity(*fixture(tmp_path)[:2], **fixture(tmp_path)[2])


@pytest.mark.parametrize('mutation', [
    lambda w,p,k: w.update(Image='sha256:' + '3'*64),
    lambda w,p,k: p['Config'].update(Cmd=['other']),
    lambda w,p,k: w['Config'].update(Env=['BIFROST_API_KEY=secret']),
    lambda w,p,k: k['volume_records']['budget']['Labels'].update(**{'factory.allocation': 'other'}),
    lambda w,p,k: k['volume_records']['budget'].update(Options={'type': 'none', 'o': 'bind', 'device': '/host/other'}),
    lambda w,p,k: k['volume_records']['anchor'].update(Scope='global'),
    lambda w,p,k: w['HostConfig'].update(NetworkMode='bridge'),
    lambda w,p,k: p['Config'].update(User='1001:1001'),
    lambda w,p,k: w['Mounts'].pop(),
    lambda w,p,k: w['Mounts'][-1].update(RW=False),
    lambda w,p,k: w['Mounts'].append(dict(w['Mounts'][-1])),
])
def test_rejects_identity_or_topology_mismatch(tmp_path, mutation):
    w,p,k = fixture(tmp_path)
    mutation(w,p,k)
    with pytest.raises(UnsafeTopology):
        validate_identity(w,p,**k)


def test_anchor_must_be_distinct_and_proxy_only(tmp_path):
    w, p, k = fixture(tmp_path)
    k['anchor_volume'] = k['budget_volume']
    with pytest.raises(UnsafeTopology):
        validate_identity(w, p, **k)
    w, p, k = fixture(tmp_path)
    w['Mounts'].append({'Type': 'volume', 'Name': 'anchor', 'Destination': '/anchor', 'RW': False})
    with pytest.raises(UnsafeTopology):
        validate_identity(w, p, **k)


@pytest.mark.parametrize('role', ['worker', 'proxy'])
@pytest.mark.parametrize('field,bad', [('Memory', 0), ('Memory', 4 * 1024**3),
    ('MemorySwap', -1), ('NanoCpus', 0), ('NanoCpus', 3_000_000_000), ('PidsLimit', -1), ('PidsLimit', None)])
def test_unbounded_or_changed_resources_rejected(tmp_path, role, field, bad):
    w, p, k = fixture(tmp_path)
    (w if role == 'worker' else p)['HostConfig'][field] = bad
    with pytest.raises(UnsafeTopology, match='resource limits'):
        validate_identity(w, p, **k)
