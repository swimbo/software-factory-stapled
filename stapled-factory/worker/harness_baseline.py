"""Local-only baseline manifest and diagnostic-log export. No inference/dispatch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from harness_telemetry import SCHEMA, digest, summarize

ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).resolve().parent



def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(runtime_config=None, task_file=None):
    suite = json.loads((WORKER/'baseline/tasks.json').read_text())
    assets = {str(p.relative_to(WORKER/'baseline')): file_hash(p)
              for p in sorted((WORKER/'baseline').rglob('*'))
              if p.is_file() and '__pycache__' not in p.parts}
    names = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-co',
        '--exclude-standard', '-z', '--', 'stapled-factory', 'scripts', '.github'])
    files = {}
    for name in sorted(set(names.decode().rstrip('\0').split('\0'))):
        path = ROOT/name
        if not name or not path.exists():
            continue
        if path.is_symlink():
            raise ValueError('source inventory must not follow symlinks')
        if path.is_file():
            files[name] = file_hash(path)
    publication = json.loads((ROOT/'docs/planning/stapled-factory/evidence/openhands-upstream-publication.json').read_text())
    runtime = None
    if runtime_config is not None:
        from operator_cli import _private_file
        runtime = _private_file(str(Path(runtime_config).absolute()))
    task_hash = None
    if task_file is not None:
        from operator_cli import _private_file
        task_hash = hashlib.sha256(_private_file(str(Path(task_file).absolute()))).hexdigest()
    return {'schema': 'factory.harness-baseline/v1',
            'source_revision': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
            'working_tree_dirty': bool(subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'])),
            'harness_files': files, 'harness_sha256': digest(files),
            'upstream_image': publication['image'], 'upstream_revision': publication['revision'],
            'development_assets': assets, 'development_suite_sha256': digest(assets),
            'primary_metric': suite['primary_metric'],
            'tasks': [{'id': task['id'], 'task_sha256': digest(task), 'runs_completed': 0}
                      for task in suite['tasks']],
            'repeats_per_task': suite['repeats_per_task'],
            'live_inference_authorized': False, 'deployed': False,
            'runtime_image': None, 'runtime_config_sha256': hashlib.sha256(runtime).hexdigest() if runtime is not None else None,
            'task_sha256': task_hash,
            'rendered_prompt_sha256': None, 'rendered_tools_sha256': None,
            'actual_routed_model': None, 'metrics': None,
            'limitations': ['Source snapshot only; no live model baseline or runtime attestation.',
                            'Configured upstream image is not a measured deployed image.',
                            'Runtime prompts, schemas and usage require opt-in observations.']}


def export_log(path):
    path = Path(path)
    if path.stat().st_size > 4*1024*1024:
        raise ValueError('telemetry log exceeds quota')
    raw = path.read_bytes()
    if not raw.endswith(b'\n'):
        raise ValueError('partial telemetry history; preserve and reconcile')
    rows = [json.loads(line) for line in raw.splitlines()]
    if not rows or rows[0].get('kind') != 'binding' or rows[0].get('schema') != SCHEMA:
        raise ValueError('missing telemetry binding')
    binding = {key: rows[0].get(key) for key in ('schema', 'attempt_sha256', 'fingerprint')}
    if any(any(row.get(key) != value for key, value in binding.items()) for row in rows):
        raise ValueError('mixed telemetry bindings')
    return {**summarize(rows), **binding, 'log_sha256': hashlib.sha256(raw).hexdigest(),
            'coverage': 'observed requests only; compare count with authoritative budget ledger',
            'accounting': 'diagnostic estimates, not provider charges or authorization'}


def write_new(path, value):
    # Never overwrite an earlier baseline or follow a destination symlink.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    snap = sub.add_parser('snapshot')
    snap.add_argument('--output', required=True)
    snap.add_argument('--runtime-config', help='explicit owner-only config to fingerprint; contents are never exported')
    snap.add_argument('--task-file', help='explicit owner-only task to fingerprint; contents are never exported')
    export = sub.add_parser('summarize')
    export.add_argument('--log', required=True)
    export.add_argument('--output', required=True)
    args = parser.parse_args()
    write_new(args.output, snapshot(args.runtime_config, args.task_file) if args.command == 'snapshot' else export_log(args.log))


if __name__ == '__main__':
    main()
