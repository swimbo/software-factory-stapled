"""Every Docker launch site must own the teardown of what it created.

These tests are the regression net for the abandoned-container incident: probe
and validation sites used `docker run`/`docker create` and then removed only
what they had *successfully read back*, so a create whose response was lost to a
client timeout leaked a container no compose project and no label sweep owned.

Covered here, for each fixed site:
  * success path teardown,
  * exception path teardown,
  * a failing teardown does not mask the original exception,
  * a second teardown is a no-op.

No test touches a real Docker daemon: every `docker` invocation is replaced by a
recording stub, so no host container can be created or removed by this file.
"""
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

FACTORY = Path(__file__).resolve().parents[2]        # .../stapled-factory
WORKER = FACTORY / 'worker'
PROBES = WORKER / 'probes'
SPINE = FACTORY / 'spine'
sys.path[:0] = [str(FACTORY), str(WORKER), str(PROBES), str(SPINE)]

import docker_scope
from docker_scope import DockerScope, LAUNCH_ID_LABEL, LAUNCH_LABEL


class Recorder:
    """Stand-in for a docker CLI: records argv, optionally fails."""

    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def __call__(self, argv, timeout=None):
        argv = [str(x) for x in argv]
        self.calls.append(argv)
        # argv is always ['docker', <verb>, ...] for the default runner.
        verb = argv[1] if len(argv) > 1 else ''
        if verb in self.fail:
            raise OSError('docker unavailable')
        return None

    def removed(self):
        """(kind, name) for every removal, handling both argv shapes.

        container: ['docker', 'rm', '-f', name]
        volume:    ['docker', 'volume', 'rm', name]
        network:   ['docker', 'network', 'rm', name]
        """
        out = []
        for argv in self.calls:
            if len(argv) > 1 and argv[1] == 'rm':
                out.append(('container', argv[-1]))
            elif len(argv) > 2 and argv[2] == 'rm' and argv[1] in ('volume', 'network'):
                out.append((argv[1], argv[-1]))
        return out

    def removed_names(self):
        return [name for _, name in self.removed()]


def scope_with(recorder):
    return DockerScope('test-site', runner=lambda argv: recorder(argv))


# ---------------------------------------------------------------- the helper
def test_teardown_is_idempotent_and_never_raises():
    recorder = Recorder()
    scope = scope_with(recorder)
    scope.container('factory-x')
    scope.volume('factory-x-vol')
    first = scope.teardown()
    assert [entry[2] for entry in first] == [True, True]
    assert scope.closed
    assert scope.teardown() == []          # second call: no-op, no new docker call
    assert recorder.removed_names() == ['factory-x-vol', 'factory-x']


def test_teardown_failure_returns_false_instead_of_raising():
    recorder = Recorder(fail=('rm',))
    scope = scope_with(recorder)
    scope.container('factory-x')
    results = scope.teardown()             # must not raise
    assert results == [('container', 'factory-x', False)]


def test_unconfirmed_volume_is_never_deleted():
    """`docker volume create` is idempotent, so a name match may be pre-existing."""
    recorder = Recorder()
    scope = scope_with(recorder)
    scope.volume('factory-shared', confirmed=False)
    scope.teardown()
    assert recorder.removed() == []
    assert scope.teardown() == []


def test_confirmed_volume_is_deleted():
    recorder = Recorder()
    scope = scope_with(recorder)
    scope.volume('factory-mine', confirmed=False)
    scope.confirm('factory-mine')
    scope.teardown()
    assert recorder.removed() == [('volume', 'factory-mine')]


def test_name_and_labels_are_recorded_before_create():
    scope = DockerScope('site-a')
    name = scope.name('factory-probe')
    assert name.startswith('factory-probe-')
    labels = scope.labels()
    assert labels[0] == '--label' and labels[1] == LAUNCH_LABEL + '=site-a'
    assert labels[2] == '--label' and labels[3].startswith(LAUNCH_ID_LABEL + '=')
    assert scope.label_filter() == LAUNCH_ID_LABEL + '=' + scope.launch_id


def test_release_transfers_ownership_instead_of_removing():
    recorder = Recorder()
    scope = scope_with(recorder)
    scope.container('factory-deliverable')
    released = scope.release('container')
    assert released == [('container', 'factory-deliverable')]
    scope.teardown()
    assert recorder.removed() == []


# ------------------------------------------------------- provision_runtime
def test_provision_removes_containers_and_network_on_failure(monkeypatch, tmp_path):
    import provision_runtime as runtime
    image = 'sha256:' + '1' * 64
    calls = []

    def inspect(kind, name):
        if kind == 'volume':
            parts = name.split('-')
            return {'Name': name, 'Driver': 'local', 'Scope': 'local', 'Options': None,
                    'Labels': {'factory.allocation': parts[1], 'factory.role': parts[-1]}}
        return {'Id': name, 'State': {'Running': True}}

    def run(args, **kwargs):
        calls.append(list(args))
        if args[0] == 'run':
            raise runtime.ProvisionError('initializer failed')
        return ''

    monkeypatch.setattr(runtime, 'inspect', inspect)
    monkeypatch.setattr(runtime, '_run', run)
    with pytest.raises(runtime.ProvisionError, match='provisioning failed'):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy'],
                          workspace=str(tmp_path), uid=1000, worker_environment=[],
                          gateway_key=b'secret-value')
    removed = [call for call in calls if 'rm' in call]
    names = [call[-1] for call in removed]
    # The three disposable volumes and the bridge network are removed; the ledger
    # volumes (budget/anchor/telemetry) are retained evidence.
    assert any(call[0] == 'network' for call in removed)
    assert any(call[0] == 'volume' and call[-1].endswith('-socket') for call in removed)
    assert not any(call[0] == 'volume' and call[-1].endswith('-budget') for call in removed)
    assert not any(call[0] == 'volume' and call[-1].endswith('-anchor') for call in removed)
    assert names, 'no resource was removed'


def test_provision_records_containers_before_create(monkeypatch, tmp_path):
    """A `docker create` whose response is lost still leaves a named container."""
    import provision_runtime as runtime
    image = 'sha256:' + '1' * 64
    calls = []

    def inspect(kind, name):
        if kind == 'volume':
            parts = name.split('-')
            return {'Name': name, 'Driver': 'local', 'Scope': 'local', 'Options': None,
                    'Labels': {'factory.allocation': parts[1], 'factory.role': parts[-1]}}
        return {'Id': name, 'State': {'Running': True}}

    class Timeout:
        def __init__(self): self.stdin = None

    def run(args, **kwargs):
        calls.append(list(args))
        if args[0] == 'create':
            raise runtime.ProvisionError('Docker operation failed: create')  # response lost
        return ''

    monkeypatch.setattr(runtime, 'inspect', inspect)
    monkeypatch.setattr(runtime, '_run', run)
    with pytest.raises(runtime.ProvisionError):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy'],
                          workspace=str(tmp_path), uid=1000, worker_environment=[],
                          gateway_key=b'secret-value')
    removed = [call[-1] for call in calls if 'rm' in call]
    assert any(name.endswith('-proxy') for name in removed), removed


def test_provision_teardown_failure_does_not_mask_original_error(monkeypatch, tmp_path):
    import provision_runtime as runtime
    image = 'sha256:' + '1' * 64

    def inspect(kind, name):
        if kind == 'volume':
            parts = name.split('-')
            return {'Name': name, 'Driver': 'local', 'Scope': 'local', 'Options': None,
                    'Labels': {'factory.allocation': parts[1], 'factory.role': parts[-1]}}
        return {'Id': name, 'State': {'Running': True}}

    def run(args, **kwargs):
        if 'rm' in args:                       # only teardown fails
            raise runtime.ProvisionError('teardown exploded')
        if args[0] == 'run':
            raise RuntimeError('original cause')
        return ''

    monkeypatch.setattr(runtime, 'inspect', inspect)
    monkeypatch.setattr(runtime, '_run', run)
    with pytest.raises(runtime.ProvisionError, match=r'provisioning failed \(RuntimeError\)'):
        runtime.provision(worker_image=image, proxy_image=image, helper_image=image,
                          worker_command=['worker'], proxy_command=['proxy'],
                          workspace=str(tmp_path), uid=1000, worker_environment=[],
                          gateway_key=b'secret-value')


# ------------------------------------------------------ successor_runtime
def test_successor_removes_container_when_start_fails(monkeypatch):
    import successor_runtime as runtime
    allocation, worker_id = 'b' * 32, 'e' * 64
    calls = []

    class Store:
        def claim_restart(self, *args): return allocation
        def bind_restart_worker(self, *args): raise AssertionError('must not bind')

    source = (allocation, {'proxy_id': 'p'}, {}, {'Id': 'n'}, {}, 'socket', 'budget', 'anchor')
    monkeypatch.setattr(runtime, '_source', lambda *_: source)
    monkeypatch.setattr(runtime, '_workspace', lambda *_: None)
    monkeypatch.setattr(runtime, 'inspect', lambda *_: {'Id': worker_id, 'State': {'Running': False},
                                                        'HostConfig': {'RestartPolicy': {'Name': 'no'}}})

    def run(args, **kwargs):
        calls.append(list(args))
        if args[0] == 'create':
            return worker_id
        if args[0] == 'start':
            raise runtime.ProvisionError('start timed out')
        return ''

    monkeypatch.setattr(runtime, '_run', run)
    config = {'workspace': '/old', 'worker_command': ['server'], 'proxy_command': ['proxy'],
              'worker_image': 'worker', 'proxy_image': 'proxy', 'worker_environment': [], 'uid': 1000}
    with pytest.raises(runtime.ProvisionError, match='successor provisioning failed'):
        runtime.provision_successor(Store(), 'old', 'hash', 'cid', 'new', 'new-hash', 'new-cid',
                                    configuration=config, workspace='/new', baseline='a' * 40)
    removed = [call for call in calls if call[0] == 'rm']
    assert removed and removed[-1][-1].endswith('-successor')


def test_successor_keeps_bound_container(monkeypatch):
    """The bound, running successor IS the result: release, never remove."""
    import successor_runtime as runtime
    allocation, worker_id = 'c' * 32, 'f' * 64
    calls = []

    class Store:
        def claim_restart(self, *args): return allocation
        def bind_restart_worker(self, *args): return None

    source = (allocation, {'proxy_id': 'p'}, {}, {'Id': 'n'}, {}, 'socket', 'budget', 'anchor')
    monkeypatch.setattr(runtime, '_source', lambda *_: source)
    monkeypatch.setattr(runtime, '_workspace', lambda *_: None)
    monkeypatch.setattr(runtime, 'validate_identity', lambda *a, **k: None)
    monkeypatch.setattr(runtime, '_running_lifecycle', lambda _: {'Pid': 1, 'StartedAt': 'then'})
    monkeypatch.setattr(runtime, 'inspect', lambda *_: {'Id': worker_id, 'State': {'Running': False},
                                                        'HostConfig': {'RestartPolicy': {'Name': 'no'}}})

    def run(args, **kwargs):
        calls.append(list(args))
        return worker_id if args[0] == 'create' else ''

    monkeypatch.setattr(runtime, '_run', run)
    config = {'workspace': '/old', 'worker_command': ['server'], 'proxy_command': ['proxy'],
              'worker_image': 'worker', 'proxy_image': 'proxy', 'worker_environment': [], 'uid': 1000}
    runtime.provision_successor(Store(), 'old', 'hash', 'cid', 'new', 'new-hash', 'new-cid',
                                configuration=config, workspace='/new', baseline='a' * 40)
    assert not [call for call in calls if call[0] == 'rm']


def test_successor_teardown_failure_does_not_mask_original_error(monkeypatch):
    import successor_runtime as runtime
    allocation, worker_id = 'd' * 32, 'a' * 64

    class Store:
        def claim_restart(self, *args): return allocation

    source = (allocation, {'proxy_id': 'p'}, {}, {'Id': 'n'}, {}, 'socket', 'budget', 'anchor')
    monkeypatch.setattr(runtime, '_source', lambda *_: source)
    monkeypatch.setattr(runtime, '_workspace', lambda *_: None)
    monkeypatch.setattr(runtime, 'inspect', lambda *_: {'Id': worker_id, 'State': {'Running': False},
                                                        'HostConfig': {'RestartPolicy': {'Name': 'no'}}})

    def run(args, **kwargs):
        if args[0] == 'rm':
            raise runtime.ProvisionError('teardown exploded')
        if args[0] == 'create':
            return worker_id
        if args[0] == 'start':
            raise ValueError('original start failure')
        return ''

    monkeypatch.setattr(runtime, '_run', run)
    config = {'workspace': '/old', 'worker_command': ['server'], 'proxy_command': ['proxy'],
              'worker_image': 'worker', 'proxy_image': 'proxy', 'worker_environment': [], 'uid': 1000}
    with pytest.raises(runtime.ProvisionError, match=r'successor provisioning failed') as caught:
        runtime.provision_successor(Store(), 'old', 'hash', 'cid', 'new', 'new-hash', 'new-cid',
                                    configuration=config, workspace='/new', baseline='a' * 40)
    assert isinstance(caught.value.__cause__, ValueError)
    assert 'teardown exploded' not in str(caught.value)


# --------------------------------------------------------- launch-site sites
def stub_teardown(monkeypatch, seen):
    """Capture the scope's own removal calls instead of running real docker.

    The probe sites give DockerScope its default runner (subprocess). These tests
    must never touch the host daemon, so the runner is replaced here.
    """
    def runner(argv, timeout=None):
        seen.append([str(x) for x in argv])
        return None
    monkeypatch.setattr(docker_scope, '_default_runner', runner)


def removals(seen):
    out = []
    for argv in seen:
        if len(argv) > 1 and argv[1] == 'rm':
            out.append(argv[-1])
        elif len(argv) > 2 and argv[2] == 'rm' and argv[1] in ('volume', 'network'):
            out.append(argv[-1])
    return out


def test_server_crash_guarded_teardown_covers_lost_run(monkeypatch):
    """`created=True` was set after `docker run`, so a timeout leaked the container."""
    import server_crash_guarded as probe
    seen = []
    stub_teardown(monkeypatch, seen)

    def cmd(*args, **kwargs):
        raise TimeoutError('client timed out after the daemon created the container')

    monkeypatch.setattr(probe, 'cmd', cmd)
    with pytest.raises(TimeoutError):
        probe.main()
    assert any(name.startswith('factory-server-guard-probe-') for name in removals(seen)), seen


def test_crash_recovery_teardown_covers_lost_run(monkeypatch):
    import crash_recovery as probe
    seen = []
    stub_teardown(monkeypatch, seen)

    def cmd(*args, **kwargs):
        raise TimeoutError('client timed out')

    monkeypatch.setattr(probe, 'cmd', cmd)
    monkeypatch.setattr(sys, 'argv', ['crash_recovery.py'])
    with pytest.raises(TimeoutError):
        probe.main()
    assert any(name.startswith('factory-crash-probe-') for name in removals(seen)), seen


def test_server_tool_crash_teardown_covers_lost_run(monkeypatch):
    import server_tool_crash as probe
    seen = []
    stub_teardown(monkeypatch, seen)

    def cmd(*args, **kwargs):
        raise TimeoutError('client timed out')

    monkeypatch.setattr(probe, 'cmd', cmd)
    with pytest.raises(TimeoutError):
        probe.main()
    assert any(name.startswith('factory-tool-crash-') for name in removals(seen)), seen


def test_fence_container_teardown_covers_lost_run(monkeypatch, tmp_path):
    import fence_container as probe
    seen = []
    stub_teardown(monkeypatch, seen)

    def docker(*args):
        if args[0] == 'image':
            return 'sha256:' + 'a' * 64
        raise TimeoutError('client timed out')       # `run` response lost

    monkeypatch.setattr(probe, 'docker', docker)
    with pytest.raises(TimeoutError):
        probe.main()
    assert any(name.startswith('factory-fence-probe-') for name in removals(seen)), seen


def test_operator_abandon_names_and_removes_its_containers(monkeypatch, tmp_path):
    """The probe used to run workers with no --name and remove an empty string."""
    import operator_abandon as probe
    seen = []
    stub_teardown(monkeypatch, seen)

    def cmd(*args, **kwargs):
        seen.append([str(x) for x in args])
        if 'openssl' in args[0]:
            # The probe chmods and reads what openssl wrote; emulate the output file.
            if '-out' in args:
                Path(args[args.index('-out') + 1]).write_bytes(b'fixture')
            return ''
        if args[:2] == ('docker', 'run'):
            raise RuntimeError('probe stopped after create')
        return ''

    monkeypatch.setattr(probe, 'cmd', cmd)
    with pytest.raises(Exception):
        probe.main(tmp_path / 'out')
    runs = [call for call in seen if call[:2] == ['docker', 'run']]
    assert runs, seen
    for call in runs:
        assert '--name' in call, call
    removed = removals(seen)
    assert removed, seen
    assert all(removed), removed                      # never an empty name


# ------------------------------------------- spine/local_validation (site 4)
def _tarball(entrypoint='app.py'):
    """A minimal `git archive` payload containing the operator entrypoint."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        data = b'print("ok")\n'
        info = tarfile.TarInfo(entrypoint)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _fake_command(args):
    if args[0] == 'docker' and args[1] == 'build':
        return 'sha256:' + 'a' * 64
    if args[0] == 'docker' and args[1] == 'run':
        return 'c' * 64
    if args[0] == 'docker' and args[1] == 'inspect':
        return json.dumps([{'Id': 'c' * 64, 'Image': 'sha256:' + 'a' * 64,
                            'State': {'Running': True},
                            'NetworkSettings': {'Ports': {'8000/tcp': [{'HostPort': '1234'}]}}}])
    if args[0] == 'git':
        return 'a' * 40
    if args[0] == 'python3' and 'pipeline_cli' in str(args[1]):
        return '{"verdict": "pass"}'
    if args[0] == 'python3' and 'cli.py' in str(args[1]):
        return '{"verdict": {"passed": true}}'
    return ''


def _run_local_validation(monkeypatch, calls):
    import local_validation as site
    monkeypatch.setattr(site, 'command',
                        lambda *a, **k: calls.append([str(x) for x in a]) or _fake_command(a))
    monkeypatch.setattr(site.subprocess, 'check_output', lambda *a, **k: _tarball())
    return site


def test_local_validation_names_and_labels_its_container(monkeypatch, tmp_path):
    """This site previously ran an unnamed, unlabelled container: invisible to sweeps."""
    calls = []
    site = _run_local_validation(monkeypatch, calls)

    repo, scenarios = tmp_path / 'repo', tmp_path / 'scenarios.json'
    repo.mkdir()
    scenarios.write_text('[]')
    site.validate(repo, 'a' * 40, 'b' * 40, scenarios, 'app.py', 'python@sha256:' + 'c' * 64)

    run = next(call for call in calls if call[:2] == ['docker', 'run'])
    assert '--name' in run, run
    name = run[run.index('--name') + 1]
    assert name.startswith('factory-local-validation-')
    assert LAUNCH_LABEL + '=local-validation' in run, run
    # Read-only / cap-drop / no-new-privileges must survive the change.
    assert '--read-only' in run and '--cap-drop=ALL' in run
    assert '--security-opt=no-new-privileges' in run
    # And the owned container is removed on the success path.
    assert [call[-1] for call in calls if call[:2] == ['docker', 'rm']] == [name], calls


def test_local_validation_removes_container_on_exception(monkeypatch, tmp_path):
    import json as real_json
    calls = []
    site = _run_local_validation(monkeypatch, calls)

    def boom(_):
        raise ValueError('pipeline failed')
    monkeypatch.setattr(site, 'json', type('J', (), {'loads': staticmethod(boom),
                                                    'dumps': staticmethod(real_json.dumps)}))

    repo, scenarios = tmp_path / 'repo', tmp_path / 'scenarios.json'
    repo.mkdir()
    scenarios.write_text('[]')
    with pytest.raises(ValueError):
        site.validate(repo, 'a' * 40, 'b' * 40, scenarios, 'app.py', 'python@sha256:' + 'c' * 64)
    removed = [call[-1] for call in calls if call[:2] == ['docker', 'rm']]
    assert any(name.startswith('factory-local-validation-') for name in removed), calls


# ------------------------------------------------- netns_firewall helper
def test_netns_firewall_helper_is_named_and_removed_after_timeout(monkeypatch):
    import netns_firewall as firewall
    calls = []

    def run(args):
        calls.append(list(args))
        if args[0] == 'inspect' and args[2] == 'container':
            return json.dumps([{'Id': 'a' * 64, 'State': {'Running': True, 'Pid': 321, 'StartedAt': 't'},
                                'Config': {'Labels': {'factory.allocation': 'b' * 32,
                                                      'factory.role': 'proxy'}},
                                'HostConfig': {'Privileged': False,
                                               'NetworkMode': 'factory-network',
                                               'CapAdd': None, 'CapDrop': ['ALL']},
                                'NetworkSettings': {'Networks': {
                                    'factory-network': {'NetworkID': 'c' * 64}}}}])
        if args[0] == 'inspect':
            return json.dumps([{'Id': 'sha256:' + 'd' * 64}])
        if args[0] == 'run':
            raise firewall.FirewallError('helper timed out')   # container may exist
        return ''

    monkeypatch.setattr(firewall, '_run', run)
    with pytest.raises(firewall.FirewallError, match='helper timed out'):
        firewall._check_proxy_firewall('a' * 64, 'b' * 32, 'c' * 64, 'sha256:' + 'd' * 64,
                                       '100.92.236.42', install=False)
    helper = next(call for call in calls if call[0] == 'run')
    assert '--name' in helper and helper[helper.index('--name') + 1].startswith('factory-firewall-helper-')
    assert '--rm' in helper
    assert any(call[:2] == ['rm', '-f'] and call[-1].startswith('factory-firewall-helper-')
               for call in calls), calls
