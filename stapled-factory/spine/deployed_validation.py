"""Trusted-provider validation boundary, called by an operator-owned adapter.

The provider must authenticate its readback and derive address/artifact from its
own deployment record. This module never accepts an orchestration caller URL.
It produces evidence only; it does not authorize Restate acceptance.
"""
from dataclasses import dataclass
import hashlib
import re
from pathlib import Path


@dataclass(frozen=True)
class Deployment:
    project: str
    environment: str
    service: str
    deployment: str
    revision: str
    artifact: str
    target: str
    status: str


def validate_deployed(provider, expected, scenarios, security, wall):
    """Bind trusted provider readback and both verdicts to one exact deployment.

    expected is an operator-controlled Deployment, not request data. Provider
    snapshot must read the active service deployment, not an old deployment ID.
    """
    if not isinstance(expected, Deployment):
        raise ValueError('operator deployment binding required')
    if not re.fullmatch(r'[0-9a-f]{40}', expected.revision):
        raise ValueError('exact source revision required')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', expected.artifact):
        raise ValueError('immutable artifact digest required')
    if not all((expected.project, expected.environment, expected.service, expected.deployment, expected.target)):
        raise ValueError('complete provider identity required')
    def snapshot():
        active = provider.snapshot(expected.project, expected.environment, expected.service)
        if active != expected or active.status != 'SUCCESS':
            raise ValueError('active deployment binding mismatch')
    scenario_digest = hashlib.sha256(Path(scenarios).read_bytes()).hexdigest()
    snapshot()
    scan = security(expected.revision)
    snapshot()
    result = wall(expected.target, scenarios, runs=3)
    snapshot()
    if hashlib.sha256(Path(scenarios).read_bytes()).hexdigest() != scenario_digest:
        raise ValueError('scenario set changed')
    return dict(scope='trusted-provider', git_revision=expected.revision,
                artifact_digest=expected.artifact, deployment_id=expected.deployment,
                project=expected.project, environment=expected.environment,
                service=expected.service, scenario_digest=scenario_digest,
                security=scan, wall=result,
                passed=scan['verdict'] == 'pass' and result['verdict']['passed'])


class RailwayProvider:
    """Read active provider state via authenticated CLI, never request metadata.

    bindings map immutable locally built image digests to their source revisions;
    they must come from an operator-owned verified build manifest. Targets are
    Railway service domains. Private transport needs its own trusted adapter.
    """
    def __init__(self, bindings, runner=None):
        import subprocess
        import os
        self.bindings = dict(bindings)
        cli_env = {k: v for k, v in os.environ.items() if k not in ("RAILWAY_TOKEN", "RAILWAY_API_TOKEN")}
        self.runner = runner or (lambda args: subprocess.check_output(args, text=True, env=cli_env))

    def snapshot(self, project, environment, service):
        import json
        state = json.loads(self.runner(['railway', 'status', '--project', project,
                                       '--environment', environment, '--json']))
        if state['id'] != project:
            raise ValueError('provider project mismatch')
        envs = [x['node'] for x in state['environments']['edges'] if x['node']['id'] == environment]
        if len(envs) != 1:
            raise ValueError('provider environment mismatch')
        services = [x['node'] for x in envs[0]['serviceInstances']['edges'] if x['node']['serviceId'] == service]
        if len(services) != 1:
            raise ValueError('provider service mismatch')
        instance = services[0]
        active = instance['activeDeployments']
        if len(active) != 1:
            raise ValueError('ambiguous active deployment')
        deployment = active[0]
        if deployment['deploymentStopped'] or deployment['id'] != instance['latestDeployment']['id']:
            raise ValueError('stopped or superseded deployment')
        if not deployment.get('instances') or any(x['status'] != 'RUNNING' for x in deployment['instances']):
            raise ValueError('deployment instances unavailable')
        digest = deployment['meta']['imageDigest']
        revision = self.bindings.get(digest)
        if revision is None:
            raise ValueError('artifact absent from trusted local build manifest')
        domains = instance['domains']['serviceDomains']
        if len(domains) != 1 or instance['domains']['customDomains']:
            raise ValueError('unambiguous provider service domain required')
        domain = domains[0]['domain']
        if not re.fullmatch(r'[a-zA-Z0-9-]+\.up\.railway\.app', domain):
            raise ValueError('invalid provider domain')
        return Deployment(project, environment, service, deployment['id'], revision,
                          digest, 'https://' + domain, deployment['status'])
