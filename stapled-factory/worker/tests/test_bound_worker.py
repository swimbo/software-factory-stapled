import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guarded_store import GuardedAttemptStore
from worker import execute_bound_attempt
import provision_runtime

FIREWALL = {'helper_image': 'sha256:' + 'c' * 64, 'gateway_ip': '192.0.2.1'}


def setup_store(tmp_path):
    (tmp_path / 'ledger').mkdir()
    (tmp_path / 'anchor').mkdir()
    return GuardedAttemptStore.initialize(tmp_path / 'ledger/db', tmp_path / 'anchor/mark')


def test_dispatch_crash_reconciles_bound_worker_without_reprovision(monkeypatch, tmp_path):
    store = setup_store(tmp_path)
    config = {'workspace_baseline': 'a' * 40, 'gateway_key': b'dummy', 'workspace': '/workspace', 'firewall': FIREWALL}
    calls = []
    def provision(store, aid, digest, cid, **kw):
        calls.append(('provision', aid, cid))
    def inspect(store, aid, digest, cid, **kw):
        assert 'gateway_key' not in kw
        calls.append(('inspect', aid, cid))
        return {'worker_id': 'worker-original'}
    monkeypatch.setattr(provision_runtime, 'provision_once', provision)
    monkeypatch.setattr(provision_runtime, 'inspect_bound_allocation', inspect)
    class Adapter:
        def create(self, cid, payload):
            calls.append(('create', cid))
            raise TimeoutError('accepted then crashed')
        def read(self, cid, payload):
            calls.append(('read', cid))
            return {'status': 'completed', 'git_revision': 'b' * 40}
    def factory(worker_id):
        assert worker_id == 'worker-original'
        return Adapter()
    first = execute_bound_attempt(store, 'unit:0', {'task': 'one'}, config, factory)
    second = execute_bound_attempt(store, 'unit:0', {'task': 'one'}, config, factory, reconcile_only=True)
    assert first['status'] == 'unknown'
    assert second['status'] == 'completed'
    assert [call[0] for call in calls] == ['provision', 'inspect', 'create', 'inspect', 'read']
    assert first['conversation_id'] == second['conversation_id']


def test_unavailable_binding_never_reaches_adapter_or_reprovisions(monkeypatch, tmp_path):
    store = setup_store(tmp_path)
    calls = []
    def provision(*args, **kwargs):
        calls.append('provision')
        raise RuntimeError('crashed after allocation reservation')
    def inspect(*args, **kwargs):
        calls.append('inspect')
        raise RuntimeError('binding unavailable')
    monkeypatch.setattr(provision_runtime, 'provision_once', provision)
    monkeypatch.setattr(provision_runtime, 'inspect_bound_allocation', inspect)
    def factory(*args):
        raise AssertionError('unbound worker must never be contacted')
    config = {'workspace_baseline': 'a' * 40, 'gateway_key': b'dummy', 'firewall': FIREWALL}
    for _ in range(2):
        result = execute_bound_attempt(store, 'unit:0', {'task': 'one'}, config, factory)
        assert result['status'] == 'unknown'
    assert calls == ['provision', 'inspect']


def test_reconcile_missing_attempt_never_provisions_or_contacts_worker(monkeypatch, tmp_path):
    store = setup_store(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('no runtime call permitted')
    monkeypatch.setattr(provision_runtime, 'provision_once', forbidden)
    monkeypatch.setattr(provision_runtime, 'inspect_bound_allocation', forbidden)
    result = execute_bound_attempt(store, 'missing', {'task': 'one'},
        {'workspace_baseline': 'a' * 40, 'firewall': FIREWALL}, forbidden, reconcile_only=True)
    assert result['status'] == 'unknown'


def test_missing_firewall_blocks_before_reservation_or_runtime(monkeypatch, tmp_path):
    store = setup_store(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('unfirewalled runtime must not be contacted')
    monkeypatch.setattr(provision_runtime, 'provision_once', forbidden)
    monkeypatch.setattr(provision_runtime, 'inspect_bound_allocation', forbidden)
    for firewall in (None, {}, {'helper_image': 'image'}, {'helper_image': '', 'gateway_ip': '192.0.2.1'}):
        result = execute_bound_attempt(store, 'unit:0', {'task': 'one'},
            {'workspace_baseline': 'a' * 40, 'gateway_key': b'dummy', 'firewall': firewall}, forbidden)
        assert result['status'] == 'blocked'
    import hashlib, json, uuid
    digest = hashlib.sha256(json.dumps({'task': 'one'}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    cid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'stapled-factory:unit:0'))
    assert store.lookup('unit:0', digest, cid) == ('missing', None)


def test_real_guarded_binding_survives_provision_crash(monkeypatch, tmp_path):
    store = setup_store(tmp_path)
    monkeypatch.setattr(provision_runtime, '_check_baseline', lambda *args: None)
    allocation = {}
    def volume(name):
        return {'Name': name, 'CreatedAt': 'time', 'Driver': 'local', 'Scope': 'local',
                'Labels': {}, 'Options': {}, 'Mountpoint': '/isolated/' + name}
    def provision(*, allocation_id, before_credential, **kwargs):
        allocation['id'] = allocation_id
        prefix = 'factory-' + allocation_id
        volumes = {prefix + '-' + suffix: volume(prefix + '-' + suffix)
                   for suffix in ('socket', 'budget', 'anchor')}
        before_credential({'Id': '1' * 64, 'State': {'Pid': 1, 'StartedAt': 'time', 'Running': True}}, {'Id': '2' * 64, 'State': {'Pid': 2, 'StartedAt': 'time', 'Running': True}}, volumes, {'Id': '3' * 64})
        raise RuntimeError('controller lost after binding')
    def inspect(kind, name):
        if kind == 'volume': return volume(name)
        if kind == 'network': return {'Id': '3' * 64}
        is_worker = name.endswith('-worker')
        return {'Id': ('1' if is_worker else '2') * 64, 'State': {'Pid': 1 if is_worker else 2, 'StartedAt': 'time', 'Running': True}}
    monkeypatch.setattr(provision_runtime, 'provision', provision)
    monkeypatch.setattr(provision_runtime, 'inspect', inspect)
    monkeypatch.setattr(provision_runtime, 'validate_identity', lambda *args, **kw: None)
    monkeypatch.setattr(provision_runtime, 'verify_proxy_firewall', lambda *args, **kw: None)
    config = {'workspace_baseline': 'a' * 40, 'workspace': '/workspace', 'gateway_key': b'dummy',
              'worker_image': 'worker', 'proxy_image': 'proxy', 'helper_image': 'helper',
              'worker_command': ['worker'], 'proxy_command': ['proxy'], 'uid': 1000, 'worker_environment': [], 'firewall': FIREWALL}
    class Adapter:
        def create(self, *_): raise AssertionError('must not redispatch')
        def read(self, *_): return None
    def factory(worker_id):
        assert worker_id == '1' * 64
        return Adapter()
    first = execute_bound_attempt(store, 'unit:0', {'task': 'one'}, config, factory)
    second = execute_bound_attempt(store, 'unit:0', {'task': 'one'}, config, factory, reconcile_only=True)
    assert first['status'] == second['status'] == 'unknown'
    assert allocation['id']
