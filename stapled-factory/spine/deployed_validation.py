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
