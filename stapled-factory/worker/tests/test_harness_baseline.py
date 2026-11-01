import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness_baseline as baseline
from harness_telemetry import TelemetryLog


def test_snapshot_freezes_six_tasks_and_keeps_live_metrics_unknown():
    value = baseline.snapshot()
    assert 'stapled-factory/worker/worker.py' in value['harness_files']
    assert 'stapled-factory/worker/egress_bridge.py' in value['harness_files']
    assert 'stapled-factory/spine/src/orchestrate.ts' in value['harness_files']
    assert len(value['tasks']) == 6
    assert len(set(t['id'] for t in value['tasks'])) == 6
    assert all(t['runs_completed'] == 0 for t in value['tasks'])
    assert value['metrics'] is None and value['runtime_image'] is None
    assert not value['live_inference_authorized'] and not value['deployed']
    assert value['development_suite_sha256'] == baseline.snapshot()['development_suite_sha256']


def test_export_is_bound_and_does_not_overwrite_existing_artifacts(tmp_path):
    directory = tmp_path/'logs'; directory.mkdir(mode=0o700)
    log = TelemetryLog(directory, 'attempt', 'b'*64)
    log.emit({'kind':'request_started', 'request_id':'one', 'reserved_microdollars':4})
    value = baseline.export_log(directory/'telemetry.jsonl')
    assert value['requests_without_end'] == 1 and value['actual_cost_usd'] is None
    out = tmp_path/'summary.json'
    baseline.write_new(out, value)
    with pytest.raises(FileExistsError):baseline.write_new(out, {})
    assert json.loads(out.read_text()) == value
    with (directory/'telemetry.jsonl').open('a') as stream:stream.write('{')
    with pytest.raises(ValueError, match='partial'):baseline.export_log(directory/'telemetry.jsonl')


def test_development_acceptance_rejects_seed_and_accepts_intended_results(tmp_path):
    spec = importlib.util.spec_from_file_location('baseline_check', baseline.WORKER/'baseline/check.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    seed = baseline.WORKER/'baseline/seed'
    for task in ('small-edit', 'multi-file', 'failing-tests'):
        candidate = tmp_path/task; shutil.copytree(seed, candidate)
        assert not module.check(task, candidate)
        catalog = candidate/'catalog.py'
        if task == 'small-edit':
            catalog.write_text(catalog.read_text().replace('name.strip()', 'name.strip().lower()'))
        elif task == 'multi-file':
            catalog.write_text(catalog.read_text().replace("'0.1.0'", "'0.2.0'"))
            (candidate/'app.py').write_text('from catalog import VERSION\ndef version(): return {"version":VERSION}\n')
        else:
            catalog.write_text(catalog.read_text().replace('sum(prices) + 1', 'sum(prices)'))
        assert module.check(task, candidate)
    for task, name, correct in (
        ('discovery', 'discovery.json', {'version_source':'catalog.VERSION','entrypoint':'app.py'}),
        ('noisy-output', 'noisy-result.json', {'target_index':6789}),
        ('stalled-run', 'blocker.json', {'status':'blocked','dependency':'factory_missing_dependency'})):
        candidate=tmp_path/task; candidate.mkdir()
        (candidate/name).write_text('{}')
        assert not module.check(task,candidate)
        (candidate/name).write_text(json.dumps(correct))
        assert module.check(task,candidate)


def test_baseline_cli_needs_no_runtime_credentials(tmp_path):
    output = tmp_path/'manifest.json'
    subprocess.run([sys.executable, str(baseline.WORKER/'harness_baseline.py'), 'snapshot',
                    '--output', str(output)], check=True, timeout=10)
    assert json.loads(output.read_text())['metrics'] is None


def test_explicit_private_inputs_are_hashed_never_exported(tmp_path):
    config = tmp_path/'runtime.json'; config.write_text('{"secret":"private-value"}'); config.chmod(0o600)
    task = tmp_path/'task.txt'; task.write_text('private-task'); task.chmod(0o600)
    result = baseline.snapshot(config, task)
    assert result['runtime_config_sha256'] == baseline.file_hash(config)
    assert result['task_sha256'] == baseline.file_hash(task)
    assert 'private-value' not in json.dumps(result) and 'private-task' not in json.dumps(result)
    config.chmod(0o644)
    with pytest.raises(ValueError):baseline.snapshot(config, task)


def test_blocker_check_honors_required_fields_without_forbidding_explanations(tmp_path):
    spec=importlib.util.spec_from_file_location('blocker_contract_check',baseline.WORKER/'baseline/check.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    record={'status':'blocked','dependency':'factory_missing_dependency','reason':'ModuleNotFoundError',
            'attempted':'python3 -B -c "import factory_missing_dependency"'}
    (tmp_path/'blocker.json').write_text(json.dumps(record))
    assert module.check('stalled-run',tmp_path)
    record['status']='success';(tmp_path/'blocker.json').write_text(json.dumps(record))
    assert not module.check('stalled-run',tmp_path)
