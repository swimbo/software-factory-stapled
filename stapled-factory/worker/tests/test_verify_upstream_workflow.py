import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_upstream_workflow import verify, POLICIES

MANIFEST = json.dumps({'manifests': [{'digest': 'sha256:' + 'c'*64}]})
DIGEST = 'sha256:' + hashlib.sha256(MANIFEST.encode()).hexdigest()
IMAGE = 'ghcr.io/example/runtime@' + DIGEST
POLICY = {'repo': 'example/runtime', 'workflow': '.github/workflows/build.yml', 'revision': 'b'*40,
          'run_id': 1, 'event': 'push', 'actor': 'maintainer', 'job_id': 2,
          'job_name': 'Publish', 'step_number': 3, 'step_name': 'Push image',
          'reference': 'ghcr.io/example/runtime:release',
          'publication_style': 'pushing-digest-to-reference'}
META = {'head_repository': {'full_name': 'example/runtime'}, 'repository': {'full_name': 'example/runtime'},
        'head_sha': 'b'*40, 'path': '.github/workflows/build.yml', 'status': 'completed',
        'conclusion': 'success', 'html_url': 'https://github.com/example/runtime/actions/runs/1',
        'head_branch': 'main', 'event': 'push', 'actor': {'login': 'maintainer'},
        'triggering_actor': {'login': 'maintainer'}}
JOB = {'run_id': 1, 'name': 'Publish', 'head_sha': 'b'*40, 'conclusion': 'success',
       'steps': [{'number': 3, 'name': 'Push image', 'conclusion': 'success',
                  'started_at': '2026-10-27T00:00:00Z', 'completed_at': '2026-10-27T00:01:00Z'}]}
LOG = f'Publish\tUNKNOWN STEP\t2026-10-27T00:00:30.123Z #1 1.2 pushing {DIGEST} to ghcr.io/example/runtime:release'


@pytest.fixture(autouse=True)
def reviewed_policy(monkeypatch):
    monkeypatch.setitem(POLICIES, IMAGE, POLICY)


def check(meta=META, logs=LOG, job=JOB, manifest=MANIFEST):
    def runner(command, **kwargs):
        if command[0] == 'docker': output = manifest
        elif command[1] == 'api': output = json.dumps(job if '/jobs/' in command[-1] else meta)
        else: output = logs
        return subprocess.CompletedProcess(command, 0, output)
    return verify(IMAGE, POLICY['repo'], POLICY['workflow'], POLICY['revision'], 1, runner=runner)


def test_exact_reviewed_workflow_step_publication_and_registry_index():
    assert check()['publisher_signature_verified'] is False


@pytest.mark.parametrize('field,value', [('head_sha', 'c'*40), ('conclusion', 'failure'),
    ('path', 'other.yml'), ('status', 'in_progress'), ('event', 'pull_request'),
    ('head_branch', 'attacker-branch'), ('actor', {'login': 'attacker'}),
    ('triggering_actor', {'login': 'attacker'}), ('repository', {'full_name': 'attacker/runtime'}),
    ('head_repository', {'full_name': 'attacker/runtime'})])
def test_source_event_actor_or_run_substitutions_rejected(field, value):
    with pytest.raises(ValueError): check(meta={**META, field: value})


@pytest.mark.parametrize('logs', [LOG.replace('ghcr.io/example/runtime', 'ghcr.io/attacker/runtime'),
    LOG.replace('Publish\t', 'Untrusted job\t'), LOG.replace('00:00:30.123', '01:00:30.123'),
    LOG.replace('#1 1.2 pushing', 'echo pushing'), 'Digest: ' + DIGEST, 'echo pushing ' + DIGEST,
    LOG.replace(DIGEST, 'sha256:' + 'a'*64), ''])
def test_wrong_image_digest_echo_job_or_step_interval_rejected(logs):
    with pytest.raises(ValueError): check(logs=logs)


def test_registry_substitution_rejected():
    with pytest.raises(ValueError): check(manifest='{}')


def test_wrong_job_or_step_rejected():
    job = copy.deepcopy(JOB)
    job['steps'][0]['name'] = 'Echo untrusted text'
    with pytest.raises(ValueError): check(job=job)


def test_unreviewed_policy_rejected():
    with pytest.raises(ValueError):
        verify(IMAGE, 'attacker/runtime', POLICY['workflow'], POLICY['revision'], 1)
