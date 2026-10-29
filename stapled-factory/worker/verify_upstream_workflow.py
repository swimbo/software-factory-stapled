"""Verify reviewed upstream publication policy against GitHub and registry HTTPS.

Provider-authenticated online evidence; does not verify portable signatures.
Only independently reviewed, exact policies below are accepted.
"""
import argparse
from datetime import datetime
import hashlib
import json
import re
import subprocess

POLICIES = {
    'ghcr.io/openhands/agent-server@sha256:90da82d4dd57f713e2b9f5bc9444256b06c8bc709ca5ac794048ea9e1952b466': {
        'repo': 'OpenHands/software-agent-sdk', 'workflow': '.github/workflows/server.yml',
        'revision': 'd0d9d81324210414462fb676b377e0563a08a0cb', 'run_id': 40352978807,
        'event': 'push', 'actor': 'henriquehirako', 'job_id': 121684879794,
        'job_name': 'Merge Multi-Arch Manifests (python)', 'step_number': 6,
        'step_name': 'Create and push multi-arch manifest for python',
        'reference': 'ghcr.io/openhands/agent-server:d0d9d81324210414462fb676b377e0563a08a0cb-python',
        'publication_style': 'pushing-digest-to-reference'},
    'ghcr.io/restatedev/restate@sha256:2614a4e3a2102ef0cd4733e324574ee29a77e19e7e29262fa5660c9c55df70e5': {
        'repo': 'restatedev/restate', 'workflow': '.github/workflows/docker-refresh.yml',
        'revision': '954797e4a36cf146ab4261cf0b6622375d1f5ae6', 'run_id': 39179905022,
        'event': 'schedule', 'actor': 'tillrohrmann', 'job_id': 117830245950,
        'job_name': 'refresh', 'step_number': 12,
        'step_name': 'Build and push refreshed Docker image',
        # GitHub masks this repository owner in official push logs. The exact
        # reviewed workflow uses github.repository_owner; registry check below
        # independently verifies restatedev's exact immutable index bytes.
        'reference': 'ghcr.io/***/restate:1.8.0-20261019',
        'publication_style': 'pushing-manifest-reference-digest'},
}


def verify(image, repo, workflow, revision, run_id, *, runner=subprocess.run):
    policy = POLICIES.get(image)
    if policy is None or any(str(policy[k]) != str(v) for k, v in
                             {'repo': repo, 'workflow': workflow, 'revision': revision, 'run_id': run_id}.items()):
        raise ValueError('unreviewed upstream publication policy')
    def run(*args):
        return runner(list(args), capture_output=True, text=True, timeout=180, check=True).stdout
    metadata = json.loads(run('gh', 'api', f'repos/{repo}/actions/runs/{run_id}'))
    if (metadata.get('head_repository', {}).get('full_name') != repo
            or metadata.get('repository', {}).get('full_name') != repo
            or metadata.get('head_sha') != revision or metadata.get('path') != workflow
            or metadata.get('status') != 'completed' or metadata.get('conclusion') != 'success'
            or metadata.get('head_branch') != 'main' or metadata.get('event') != policy['event']
            or metadata.get('actor', {}).get('login') != policy['actor']
            or metadata.get('triggering_actor', {}).get('login') != policy['actor']):
        raise ValueError('official workflow source, event, actor or result mismatch')
    job = json.loads(run('gh', 'api', f'repos/{repo}/actions/jobs/{policy["job_id"]}'))
    if (job.get('run_id') != policy['run_id'] or job.get('name') != policy['job_name']
            or job.get('head_sha') != revision or job.get('conclusion') != 'success'):
        raise ValueError('trusted publication job mismatch')
    steps = [s for s in job.get('steps', []) if s.get('number') == policy['step_number']
             and s.get('name') == policy['step_name'] and s.get('conclusion') == 'success']
    if len(steps) != 1:
        raise ValueError('trusted publication step missing')
    step = steps[0]
    start, end = (datetime.fromisoformat(step[k].replace('Z', '+00:00'))
                  for k in ('started_at', 'completed_at'))
    logs = run('gh', 'run', 'view', '--job', str(policy['job_id']), '--repo', repo, '--log')
    digest = image.split('@')[1]
    ref = re.escape(policy['reference'])
    if policy['publication_style'] == 'pushing-digest-to-reference':
        publication = rf'#\d+ \d+(?:\.\d+)? pushing {re.escape(digest)} to {ref}'
    else:
        publication = rf'#\d+ pushing manifest for {ref}@{re.escape(digest)}(?: \d+(?:\.\d+)?s done)?'
    matching = []
    for line in logs.splitlines():
        parts = line.split('\t', 2)
        if len(parts) != 3 or parts[0] != policy['job_name']:
            continue
        timestamp, _, payload = parts[2].partition(' ')
        if not re.fullmatch(publication, payload):
            continue
        when = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        # GitHub step completion timestamps have second precision.
        if start <= when and when.timestamp() < end.timestamp() + 1:
            matching.append(line)
    if not matching:
        raise ValueError('exact image publication absent from trusted step interval')
    manifest = run('docker', 'buildx', 'imagetools', 'inspect', image, '--raw')
    if 'sha256:' + hashlib.sha256(manifest.encode()).hexdigest() != digest:
        raise ValueError('registry index digest mismatch')
    index = json.loads(manifest)
    if not index.get('manifests'):
        raise ValueError('registry index missing platform manifests')
    return {'image': image, 'repository': repo, 'revision': revision, 'workflow': workflow,
            'run_id': int(run_id), 'run_url': metadata['html_url'], 'policy': policy,
            'publication_step': {k: step[k] for k in ('number', 'name', 'started_at', 'completed_at')},
            'log_sha256': hashlib.sha256(logs.encode()).hexdigest(),
            'publication_lines': matching, 'registry_index_sha256': digest,
            'platform_manifests': index['manifests'], 'publisher_signature_verified': False,
            'evidence_type': 'authenticated-github-workflow-and-registry-publication'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('image', 'repo', 'workflow', 'revision', 'run_id'):
        parser.add_argument(argument)
    print(json.dumps(verify(**vars(parser.parse_args())), indent=2))
