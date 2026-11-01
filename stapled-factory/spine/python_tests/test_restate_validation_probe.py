"""Preflight must preserve cross-engine image identities and reject bad bindings."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SPINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPINE))
spec = importlib.util.spec_from_file_location('restate_validation_probe',
    SPINE / 'probes/restate_validation_session.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def inputs(tmp_path, monkeypatch, *, baseline='f' * 40, config_digest='sha256:' + 'a' * 64):
    checks = {'git_revision': 'a' * 40, 'artifact_digest': 'sha256:' + 'b' * 64,
        'source_archive_digest': 'c' * 64, 'scenario_digest': 'd' * 64, 'deployment_id': 'e' * 64,
        'passed': True, 'security': {'verdict': 'pass', 'engines': {}},
        'wall': {'verdict': {'passed': True, 'aggregate_pass_rate': 1., 'failure_message': ''},
            'receipt': {'tokens_in': 0, 'tokens_out': 0, 'requests': 1, 'retries': 0,
                'wall_clock_ms': 1, 'tier': 'fixture', 'pricing_mode': 'subscription',
                'ceiling_violation': '', 'cost_estimate_usd': None}}}
    manifest = {'revision': checks['git_revision'], 'baseline': baseline,
        'archive_sha256': checks['source_archive_digest'], 'config_digest': config_digest}
    checks_path = tmp_path / 'checks.json';checks_path.write_text(json.dumps(checks))
    manifest_path = tmp_path / 'build.json';manifest_path.write_text(json.dumps(manifest))
    fake_spine = tmp_path / 'spine';(fake_spine / 'dist').mkdir(parents=True)
    (fake_spine / 'dist/state.js').write_text('preflight stops before compilation is used')
    (fake_spine / 'node_modules/@restatedev/restate-sdk').mkdir(parents=True)
    monkeypatch.setattr(probe, 'SPINE', fake_spine)
    def forbidden(*args, **kwargs):
        pytest.fail('preflight must not launch a process')
    monkeypatch.setattr(probe.subprocess, 'Popen', forbidden)
    output = tmp_path / 'existing-output';output.mkdir()
    return argparse.Namespace(checks=checks_path, baseline=baseline,
        build_manifest=manifest_path, output=output), checks_path.read_bytes(), manifest_path.read_bytes()


def test_different_daemon_and_signed_config_ids_reach_exclusive_output_boundary(tmp_path, monkeypatch):
    args, checks, manifest = inputs(tmp_path, monkeypatch)
    with pytest.raises(FileExistsError):
        probe.run(args)
    assert args.checks.read_bytes() == checks and args.build_manifest.read_bytes() == manifest
    assert not list(args.output.iterdir())


def test_malformed_baseline_rejected_before_files_or_processes(tmp_path, monkeypatch):
    args, _, _ = inputs(tmp_path, monkeypatch, baseline='not-a-commit',
                         config_digest='sha256:' + 'b' * 64)
    with pytest.raises(ValueError, match='immutable validation binding'):
        probe.run(args)
    assert not list(args.output.iterdir())
