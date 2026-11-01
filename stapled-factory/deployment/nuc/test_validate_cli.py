"""The NUC signing authority must not expose the older unattested build path."""
from pathlib import Path
import hashlib
import hmac
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys
import threading

import pytest


def test_legacy_invocation_rejected_before_key_or_docker_access(tmp_path):
    script = Path(__file__).with_name('validate.py')
    args = [sys.executable, str(script)]
    for option in ('repo', 'revision', 'baseline', 'scenarios', 'entrypoint', 'runtime-image', 'unit', 'attempt'):
        args.extend(['--' + option, 'unused'])
    args.extend(['--key-file', str(tmp_path / 'missing-key')])
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 2
    assert '--prebuilt-image' in result.stderr
    assert '--build-signature' in result.stderr
    assert 'No such file' not in result.stderr


def load_cli():
    spec = importlib.util.spec_from_file_location('validate_cli_test', Path(__file__).with_name('validate.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inputs(tmp_path):
    values = {'repo': '/unused', 'revision': 'a' * 40, 'baseline': 'b' * 40,
              'unit': 'factory-unit', 'attempt': 'factory-unit:0',
              'prebuilt-image': 'sha256:' + 'c' * 64, 'diff-file': '/unused',
              'build-signature': '/unused', 'session-dir': str(tmp_path / 'session')}
    for name, content in [('key-file', b'test-only-authority-32bytes-minimum'),
                          ('source-archive', b'exact source archive'),
                          ('scenarios', b'private scenarios'), ('build-manifest', b'signed build')]:
        path = tmp_path / name
        path.write_bytes(content)
        path.chmod(0o600)
        values[name] = str(path)
    return values


def arguments(values):
    return [item for key, value in values.items() for item in ('--' + key, value)]


def test_actual_http_response_loss_reconciles_without_second_validation(tmp_path, monkeypatch):
    cli = load_cli()
    values = inputs(tmp_path)
    state = {'stage': 'needs_validation', 'build': {
        'status': 'completed', 'attempt_id': values['attempt'], 'git_revision': values['revision']}}
    calls = {'evaluate': 0, 'submit': 0}
    key = Path(values['key-file']).read_bytes()

    class RestateFixture(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            assert self.path.startswith('/WorkUnit/factory-unit/')
            if self.path.endswith('/getValidationCapabilities'):
                self.send_response(200);self.end_headers()
                self.wfile.write(json.dumps({'receipt_storage': 'atomic-v1'}).encode())
            elif self.path.endswith('/getState'):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(state).encode())
            else:
                assert self.path.endswith('/acceptValidation')
                envelope = json.loads(raw)
                assert hmac.compare_digest(envelope['signature'],
                    hmac.new(key, envelope['payload'].encode(), hashlib.sha256).hexdigest())
                calls['submit'] += 1
                proof = json.loads(envelope['payload'])
                state.update(stage='validated', validation=proof, receipt=proof['receipt'])
                self.close_connection = True  # Commit, then lose the HTTP response.
        def log_message(self, *args):
            pass

    def evaluate(*args, **kwargs):
        calls['evaluate'] += 1
        assert kwargs['evidence_dir'] == tmp_path / 'session/target'
        return dict(git_revision=values['revision'], artifact_digest=values['prebuilt-image'],
            source_archive_digest=hashlib.sha256(Path(values['source-archive']).read_bytes()).hexdigest(),
            scenario_digest=hashlib.sha256(Path(values['scenarios']).read_bytes()).hexdigest(),
            deployment_id='d' * 64, passed=True,
            security={'verdict': 'pass', 'engines': {'sast': 'fixture-only'}},
            wall={'verdict': {'passed': True, 'aggregate_pass_rate': 1,
                             'failure_message': '', 'per_scenario': {}},
                  'receipt': {'tokens_in': 0, 'tokens_out': 0, 'requests': 3, 'retries': 0,
                              'wall_clock_ms': 10, 'tier': 'unknown', 'pricing_mode': 'subscription',
                              'cost_estimate_usd': None, 'ceiling_violation': ''}})

    monkeypatch.setenv('FACTORY_BUILD_APPROVAL_PUBLIC_KEY', '/fixture-public-key')
    monkeypatch.setattr(cli, 'validate_prebuilt', evaluate)
    server = ThreadingHTTPServer(('127.0.0.1', 0), RestateFixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    values['ingress'] = 'http://127.0.0.1:' + str(server.server_port)
    try:
        receipt = cli.main(arguments(values))
        assert receipt['accepted'] is True
        assert cli.main(arguments(values) + ['--observe-only']) == receipt
        assert calls == {'evaluate': 1, 'submit': 1}
    finally:
        server.shutdown()
        server.server_close()


def test_cli_cannot_validate_caller_selected_stale_attempt(tmp_path, monkeypatch):
    cli = load_cli()
    values = inputs(tmp_path)
    monkeypatch.setattr(cli, 'post', lambda ingress, unit, method, value: (
        {'receipt_storage': 'atomic-v1'} if method == 'getValidationCapabilities' else
        {'stage': 'needs_validation', 'successor': {
        'attempt_id': values['attempt'] + ':successor', 'git_revision': values['revision'], 'status': 'completed'}}))
    monkeypatch.setattr(cli, 'validate_prebuilt', lambda *a, **k: pytest.fail('stale source must not execute'))
    with pytest.raises(ValueError, match='selected build'):
        cli.main(arguments(values))


def test_old_runtime_is_rejected_before_validation_effect(tmp_path, monkeypatch):
    cli = load_cli();values = inputs(tmp_path)
    monkeypatch.setattr(cli, 'post', lambda *args: {})
    monkeypatch.setattr(cli, 'validate_prebuilt', lambda *a, **kw: pytest.fail('legacy runtime must not evaluate'))
    with pytest.raises(ValueError, match='receipt capability'):
        cli.main(arguments(values))
    assert not (tmp_path/'session').exists()


def test_frozen_holdout_digest_is_enforced_before_candidate(tmp_path, monkeypatch):
    cli = load_cli();values = inputs(tmp_path)
    monkeypatch.setattr(cli, 'post', lambda *args: {'receipt_storage': 'atomic-v1'})
    monkeypatch.setattr(cli, 'validate_prebuilt', lambda *a, **kw: pytest.fail('changed holdout must not run'))
    with pytest.raises(ValueError, match='scenario digest'):
        cli.main(arguments(values) + ['--expected-scenario-digest', '0'*64])
    assert not (tmp_path/'session').exists()


def test_cli_refuses_external_signing_destination_before_key_access(tmp_path):
    cli = load_cli()
    values = inputs(tmp_path)
    Path(values['key-file']).unlink()
    values['ingress'] = 'http://external.invalid:18080'
    with pytest.raises(ValueError, match='loopback'):
        cli.main(arguments(values))
