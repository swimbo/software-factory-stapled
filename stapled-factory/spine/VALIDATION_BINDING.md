# Trusted validation binding

Status: production acceptance is disabled. `needs_validation` is intentional;
caller-provided URLs, receipts and revision strings cannot promote a build.

To close ENT-01, implement and verify a trusted deployment adapter that:

1. Selects the completed original or successor revision from durable WorkUnit
   state and checks out that exact commit into a fresh trusted build context.
2. Builds an immutable artifact and records its digest, build identity and source
   revision using operator-controlled credentials outside the worker sandbox.
3. Deploys that artifact to an isolated target and obtains deployment identity and
   target address from the trusted provider, rather than the orchestration caller.
4. Runs the holdout wall and security pipeline against that deployment and exact
   source revision; checks the target still names the same artifact before and
   after evaluation. A mutable self-reported revision endpoint is insufficient.
5. Persists the source revision, artifact digest, deployment identity, scenario
   digest and verdict together using an internal serialized state transition.
   Only this transition may change `needs_validation` to `validated`; stale
   results and successor/original identity substitutions must be rejected.

Required negative controls: caller target substitution, artifact mismatch,
revision mismatch, target replacement during evaluation, stale validation after
successor selection, failed security scan and failed wall verdict. All must
preserve the non-accepted state. External merge and production promotion retain
their separate approval boundaries.

No production deployment provider/target is configured in the current local
integration. A unit test of receipt shape cannot substitute for this evidence.

## Executable local artifact verification

`local_validation.py` implements an operator-only local adapter: exact git commit
archive, digest-pinned Python runtime, immutable Docker image ID, constrained
nonroot/read-only target, Docker-derived target address, before/after image and
container checks, source-diff security scan and triple-run holdout wall. It hashes
the scenario set and rejects changes during evaluation. It has no caller target
or receipt parameter. Run `python3 spine/probes/local_validation.py` from the
project directory for real disposable good, broken-wall and secret-diff controls.
The probe uses the already installed `python:3.11-slim` RepoDigest and makes no
model requests.

This is local adapter evidence only. The target has Docker bridge egress, the
security pipeline may use regex fallbacks, and operator-selected entrypoints and
the Docker daemon are trusted. This adapter deliberately has no authority to
change Restate state to `validated`: production provider binding and a serialized
selected-attempt acceptance transition are still required. ENT-01 remains open.
