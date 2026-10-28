import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
import provision_runtime as runtime


def _inspect_volume(kind, name):
    if kind == 'volume':
        parts = name.split('-')
        return {'Name': name, 'Driver': 'local', 'Scope': 'local', 'Options': None,
                'Labels': {'factory.allocation': parts[1], 'factory.role': parts[-1]}}
    return {'Id': name, 'Config': {'Env': []}}


def test_invalid_image_fails_before_docker(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, 'inspect', lambda *_: pytest.fail('must not inspect'))
    with pytest.raises(runtime.ProvisionError):
        runtime.provision(worker_image='latest', proxy_image='bad', helper_image='bad', worker_command=['x'], proxy_command=['y'], workspace=str(tmp_path), uid=1000, worker_environment=[], gateway_key=b'key')


def test_failure_cleans_only_created_resources_and_retains_budget(monkeypatch, tmp_path):
    calls = []
    image = 'sha256:' + '1' * 64
    monkeypatch.setattr(runtime, 'inspect', _inspect_volume)
    def fake_run(args, **kwargs):
        calls.append(args)
        if args[0] == 'run':
            raise runtime.ProvisionError('initializer failed')
        return ''
    monkeypatch.setattr(runtime, '_run', fake_run)
    with pytest.raises(runtime.ProvisionError, match='budget and anchor volumes retained'):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image, worker_command=['x'], proxy_command=['y'], workspace=str(tmp_path), uid=1000, worker_environment=[], gateway_key=b'secret-value')
    assert not any('secret-value' in str(call) for call in calls)
    removed = [call for call in calls if len(call) >= 2 and call[1] == 'rm']
    assert any(c[0] == 'network' for c in removed)
    assert any(c[0] == 'volume' and c[-1].endswith('-socket') for c in removed)
    assert not any(c[0] == 'volume' and c[-1].endswith('-budget') for c in removed)


def test_credential_is_not_released_when_preflight_fails(monkeypatch, tmp_path):
    import io
    image = 'sha256:' + '1' * 64
    class FakeProcess:
        def __init__(self): self.stdin = io.BytesIO(); self.terminated = False
        def terminate(self): self.terminated = True
    process = FakeProcess()
    monkeypatch.setattr(runtime, 'inspect', _inspect_volume)
    monkeypatch.setattr(runtime, '_run', lambda *args, **kwargs: '')
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *args, **kwargs: process)
    def reject(*args, **kwargs): raise ValueError('unsafe topology')
    monkeypatch.setattr(runtime, 'validate_identity', reject)
    with pytest.raises(runtime.ProvisionError):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy'], workspace=str(tmp_path),
                          uid=1000, worker_environment=[], gateway_key=b'test-secret')
    assert process.stdin.getvalue() == b''
    assert process.terminated


def test_provision_crash_never_recreates_budget_for_same_attempt(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from guarded_store import GuardedAttemptStore
    ledger, anchor = tmp_path / 'ledger', tmp_path / 'anchor'
    ledger.mkdir(); anchor.mkdir()
    db, marker = ledger / 'attempts', anchor / 'watermark'
    store = GuardedAttemptStore.initialize(db, marker)
    store.reserve('unit:0', 'request-hash', 'conversation')
    calls = []
    def crash(**kwargs):
        calls.append(kwargs['allocation_id'])
        raise runtime.ProvisionError('crash after possible resource creation')
    monkeypatch.setattr(runtime, 'provision', crash)
    monkeypatch.setattr(runtime.subprocess, 'run', lambda args, **kwargs: SimpleNamespace(stdout=(str(tmp_path) + '\n') if '--show-toplevel' in args else ('a' * 40 + '\n') if '--verify' in args else ''))
    config = {'worker_image': 'sha256:' + '1' * 64, 'gateway_key': b'secret', 'workspace': str(tmp_path), 'workspace_baseline': 'a' * 40}
    with pytest.raises(runtime.ProvisionError):
        runtime.provision_once(store, 'unit:0', 'request-hash', 'conversation', **config)
    with pytest.raises(runtime.ProvisionError, match='already reserved'):
        runtime.provision_once(GuardedAttemptStore(db, marker), 'unit:0', 'request-hash', 'conversation', **config)
    assert len(calls) == 1
    with pytest.raises(runtime.ProvisionError, match='reservation failed'):
        runtime.provision_once(store, 'unit:0', 'request-hash', 'conversation', worker_image='changed', gateway_key=b'secret', workspace=str(tmp_path), workspace_baseline='a' * 40)
    assert len(calls) == 1


def test_unreserved_attempt_cannot_provision(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from guarded_store import GuardedAttemptStore
    ledger, anchor = tmp_path / 'ledger', tmp_path / 'anchor'
    ledger.mkdir(); anchor.mkdir()
    store = GuardedAttemptStore.initialize(ledger / 'attempts', anchor / 'watermark')
    monkeypatch.setattr(runtime, 'provision', lambda **_: pytest.fail('must not provision'))
    monkeypatch.setattr(runtime.subprocess, 'run', lambda args, **kwargs: SimpleNamespace(stdout=(str(tmp_path) + '\n') if '--show-toplevel' in args else ('a' * 40 + '\n') if '--verify' in args else ''))
    with pytest.raises(runtime.ProvisionError, match='reservation failed'):
        runtime.provision_once(store, 'missing', 'hash', 'conversation', gateway_key=b'secret', workspace=str(tmp_path), workspace_baseline='a' * 40)


def test_allocation_binding_is_immutable_and_requires_original_specification(tmp_path):
    from guarded_store import GuardedAttemptStore, StoreQuarantined
    ledger, anchor = tmp_path / 'ledger', tmp_path / 'anchor'
    ledger.mkdir(); anchor.mkdir()
    store = GuardedAttemptStore.initialize(ledger / 'db', anchor / 'watermark')
    store.reserve('unit:0', 'request', 'conversation')
    _, allocation = store.reserve_allocation('unit:0', 'request', 'conversation', 'spec')
    with pytest.raises(StoreQuarantined):
        store.lookup_allocation('unit:0', 'request', 'conversation', 'spec')
    binding = {'worker_id': 'a' * 64, 'workspace_baseline': 'b' * 40}
    store.bind_allocation('unit:0', 'request', 'conversation', 'spec', allocation, binding)
    assert store.lookup_allocation('unit:0', 'request', 'conversation', 'spec') == (allocation, binding)
    with pytest.raises(StoreQuarantined):
        store.bind_allocation('unit:0', 'request', 'conversation', 'spec', allocation, {'worker_id': 'c' * 64})
    with pytest.raises(StoreQuarantined):
        store.lookup_allocation('unit:0', 'request', 'conversation', 'changed')


def test_reconciliation_rejects_replaced_container(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from guarded_store import GuardedAttemptStore
    ledger, anchor = tmp_path / 'ledger', tmp_path / 'anchor'
    ledger.mkdir(); anchor.mkdir()
    store = GuardedAttemptStore.initialize(ledger / 'db', anchor / 'watermark')
    store.reserve('unit:0', 'request', 'conversation')
    baseline = 'a' * 40
    config = {'worker_image': 'sha256:' + '1' * 64, 'proxy_image': 'sha256:' + '2' * 64,
              'helper_image': 'sha256:' + '3' * 64, 'worker_command': ['worker'], 'proxy_command': ['proxy'],
              'workspace': str(tmp_path), 'uid': 1000, 'worker_environment': []}
    monkeypatch.setattr(runtime.subprocess, 'run', lambda args, **kwargs: SimpleNamespace(stdout=(str(tmp_path) + '\n') if '--show-toplevel' in args else (baseline + '\n') if '--verify' in args else ''))
    resources = {}
    def fake_provision(**kwargs):
        allocation = kwargs['allocation_id']; prefix = 'factory-' + allocation
        volumes = {prefix + '-' + role: {'Name': prefix + '-' + role, 'CreatedAt': 'original'} for role in ('socket', 'budget', 'anchor')}
        worker = {'Id': '1' * 64, 'State': {'Running': True, 'Pid': 123, 'StartedAt': 'first'}}; proxy = {'Id': '2' * 64}; network = {'Id': '3' * 64}
        resources.update({('container', prefix + '-worker'): worker, ('container', prefix + '-proxy'): proxy,
                          ('network', prefix + '-network'): network, **{('volume', name): value for name, value in volumes.items()}})
        kwargs['before_credential'](worker, proxy, volumes, network)
        return allocation
    monkeypatch.setattr(runtime, 'provision', fake_provision)
    monkeypatch.setattr(runtime, 'inspect', lambda kind, name: resources[(kind, name)])
    monkeypatch.setattr(runtime, 'validate_identity', lambda *args, **kwargs: None)
    allocation = runtime.provision_once(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, gateway_key=b'secret', **config)
    result = runtime.inspect_bound_allocation(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, **config)
    assert result['worker_id'] == '1' * 64
    resources[('container', 'factory-' + allocation + '-worker')]['State']['StartedAt'] = 'restarted'
    with pytest.raises(runtime.ProvisionError, match='unavailable or changed'):
        runtime.inspect_bound_allocation(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, **config)
    resources[('container', 'factory-' + allocation + '-worker')] = {'Id': '4' * 64}
    with pytest.raises(runtime.ProvisionError, match='unavailable or changed'):
        runtime.inspect_bound_allocation(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, **config)


def test_reinspection_uses_real_topology_validator(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from guarded_store import GuardedAttemptStore
    from test_provision_check import fixture
    ledger, anchor = tmp_path / 'ledger', tmp_path / 'anchor'
    ledger.mkdir(); anchor.mkdir()
    store = GuardedAttemptStore.initialize(ledger / 'db', anchor / 'watermark')
    store.reserve('unit:0', 'request', 'conversation')
    baseline = 'a' * 40
    w, p, k = fixture(tmp_path)
    config = {'worker_image': w['Image'], 'proxy_image': p['Image'], 'helper_image': 'sha256:' + '3' * 64,
              'worker_command': k['worker_command'], 'proxy_command': k['proxy_command'],
              'workspace': str(tmp_path), 'uid': 1000, 'worker_environment': []}
    monkeypatch.setattr(runtime.subprocess, 'run', lambda args, **kwargs: SimpleNamespace(stdout=(str(tmp_path) + '\n') if '--show-toplevel' in args else (baseline + '\n') if '--verify' in args else ''))
    resources = {}
    def fake_provision(**kwargs):
        allocation = kwargs['allocation_id']; prefix = 'factory-' + allocation
        names = {k['socket_volume']: prefix + '-socket', k['budget_volume']: prefix + '-budget', 'anchor': prefix + '-anchor'}
        for container in (w, p):
            container['Config']['Labels']['factory.allocation'] = allocation
            for mount in container['Mounts']:
                if mount.get('Name') in names: mount['Name'] = names[mount['Name']]
        volumes = {names[name]: {**record, 'Name': names[name], 'Labels': {**record['Labels'], 'factory.allocation': allocation}} for name, record in k['volume_records'].items()}
        w['Id'] = '1' * 64; p['Id'] = '2' * 64
        w['State'].update({'Running': True, 'Pid': 123, 'StartedAt': 'first'})
        network = {'Id': k['proxy_network']}
        # The fixture's network ID is not necessarily a full Docker ID.
        network['Id'] = '3' * 64
        p['NetworkSettings']['Networks'] = {'factory': {'NetworkID': network['Id']}}
        resources.update({('container', prefix + '-worker'): w, ('container', prefix + '-proxy'): p,
                          ('network', prefix + '-network'): network, **{('volume', name): value for name, value in volumes.items()}})
        kwargs['before_credential'](w, p, volumes, network)
        return allocation
    monkeypatch.setattr(runtime, 'provision', fake_provision)
    monkeypatch.setattr(runtime, 'inspect', lambda kind, name: resources[(kind, name)])
    allocation = runtime.provision_once(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, gateway_key=b'secret', **config)
    assert runtime.inspect_bound_allocation(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, **config)['allocation_id'] == allocation
    w['HostConfig']['NetworkMode'] = 'bridge'
    with pytest.raises(runtime.ProvisionError):
        runtime.inspect_bound_allocation(store, 'unit:0', 'request', 'conversation', workspace_baseline=baseline, **config)


@pytest.mark.parametrize('failure', ['install', 'verify', 'restart'])
def test_opt_in_firewall_fails_before_credential(monkeypatch, tmp_path, failure):
    import io
    image = 'sha256:' + '1' * 64
    class FakeProcess:
        def __init__(self): self.stdin = io.BytesIO(); self.terminated = False
        def terminate(self): self.terminated = True
        def poll(self): return None
    process = FakeProcess()
    events = []
    counter = {'proxy': 0}
    def inspect(kind, name):
        if kind == 'image': return {'Id': name, 'Config': {'Env': []}}
        if kind == 'network': return {'Id': 'a'*64}
        if kind == 'volume': return _inspect_volume(kind, name)
        if name.endswith('-proxy'):
            counter['proxy'] += 1
            started = 'new' if failure == 'restart' and counter['proxy'] > 1 else 'old'
            return {'Id': 'b'*64, 'State': {'Pid': 42, 'StartedAt': started, 'Running': True}}
        return {'Id': 'c'*64}
    def install(*args):
        events.append('install')
        if failure == 'install': raise ValueError('firewall failed')
    def verify(*args):
        events.append('verify')
        if failure == 'verify': raise ValueError('verification failed')
    monkeypatch.setattr(runtime, 'inspect', inspect)
    monkeypatch.setattr(runtime, '_run', lambda *args, **kwargs: '')
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(runtime, 'validate_identity', lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, 'install_proxy_firewall', install)
    monkeypatch.setattr(runtime, 'verify_proxy_firewall', verify)
    with pytest.raises(runtime.ProvisionError):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy','--gateway-ip','100.92.236.42'],
                          workspace=str(tmp_path), uid=1000, worker_environment=[], gateway_key=b'secret',
                          firewall={'helper_image':image, 'gateway_ip':'100.92.236.42'})
    assert events[0] == 'install'
    assert process.stdin.getvalue() == b''
    assert process.terminated


def test_opt_in_firewall_rejects_proxy_ip_mismatch(monkeypatch, tmp_path):
    image = 'sha256:' + '1'*64
    monkeypatch.setattr(runtime, 'inspect', _inspect_volume)
    monkeypatch.setattr(runtime, '_run', lambda *args, **kwargs: pytest.fail('must not create resources'))
    with pytest.raises(runtime.ProvisionError, match='gateway IP differ'):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy','--gateway-ip','100.92.236.1'],
                          workspace=str(tmp_path), uid=1000, worker_environment=[], gateway_key=b'secret',
                          firewall={'helper_image':image, 'gateway_ip':'100.92.236.42'})


def test_firewall_helper_must_be_an_inspected_immutable_image(monkeypatch, tmp_path):
    image = 'sha256:' + '1' * 64
    monkeypatch.setattr(runtime, 'inspect', _inspect_volume)
    monkeypatch.setattr(runtime, '_run', lambda *args, **kwargs: pytest.fail('must not create resources'))
    with pytest.raises(runtime.ProvisionError, match='immutable firewall helper'):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy', '--gateway-ip', '100.92.236.42'],
                          workspace=str(tmp_path), uid=1000, worker_environment=[], gateway_key=b'secret',
                          firewall={'helper_image': 'mutable-tag', 'gateway_ip': '100.92.236.42'})


def test_firewall_helper_inspection_must_match_its_image_id(monkeypatch, tmp_path):
    image = 'sha256:' + '1' * 64
    firewall_image = 'sha256:' + '2' * 64
    monkeypatch.setattr(runtime, 'inspect', lambda kind, name: {'Id': image, 'Config': {'Env': []}})
    monkeypatch.setattr(runtime, '_run', lambda *args, **kwargs: pytest.fail('must not create resources'))
    with pytest.raises(runtime.ProvisionError, match='firewall helper image ID mismatch'):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy', '--gateway-ip', '100.92.236.42'],
                          workspace=str(tmp_path), uid=1000, worker_environment=[], gateway_key=b'secret',
                          firewall={'helper_image': firewall_image, 'gateway_ip': '100.92.236.42'})


def test_rejects_bind_backed_volume_before_privileged_initializer(monkeypatch, tmp_path):
    image = 'sha256:' + '1' * 64
    calls = []
    def inspect(kind, name):
        record = _inspect_volume(kind, name)
        if kind == 'volume':
            record['Options'] = {'type': 'none', 'o': 'bind', 'device': '/host/other'}
        return record
    monkeypatch.setattr(runtime, 'inspect', inspect)
    monkeypatch.setattr(runtime, '_run', lambda args, **kwargs: calls.append(args) or '')
    with pytest.raises(runtime.ProvisionError):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy'], workspace=str(tmp_path),
                          uid=1000, worker_environment=[], gateway_key=b'secret')
    assert not any(call[0] == 'run' for call in calls)
    assert not any(call[:2] == ['volume', 'rm'] for call in calls)
