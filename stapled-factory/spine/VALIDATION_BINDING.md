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

Railway project `a6f58ea9-2483-4606-bb6a-d51a9c9d2082` is selected, with a private
Restate control service. A deployed candidate application target and its provider
artifact binding have not yet been verified. A unit test cannot substitute for
that evidence.

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

## Provider adapter boundary

`deployed_validation.py` binds a complete operator-selected deployment identity
to authenticated provider snapshots of the **active** service before scanning,
between scanning and evaluation, and after triple evaluation. It rejects changed
source, image, URL, deployment, status or scenario bytes and carries failed wall
and security verdicts through to rejection. Its tests exercise substitutions and
a replacement during evaluation. A real provider implementation, deployed
candidate and internal serialized Restate acceptance remain required; this
boundary alone does not close ENT-01 or accept caller receipts.

## Operator signed acceptance

`WorkUnit.acceptValidation` now accepts an HMAC-signed evaluation only when its
unit, selected completed attempt and exact source revision match serialized state
at `needs_validation`. It records deployment/artifact/scenario identities with
`validated` atomically, rejecting replay and stale original/successor evidence.
The owner-only `FACTORY_VALIDATION_KEY_FILE` is mandatory; without it acceptance
is disabled. The external bearer ingress deliberately exposes no acceptance
route. Trusted operator processes on the NUC remain within the authority boundary.

`deployment/nuc/validate.py` runs the real exact-commit disposable Docker adapter,
issues a signature only after wall/security pass, and submits to private Restate.
It never signs a supplied receipt or caller target. The key must remain outside
worker mounts and backup exports accessible to workers. This grants validation
only, not merge/deploy or production tool permissions. The disposable target is
removed after evaluation; this evidence is not ongoing production-service health.
Actual NUC evaluation and signed acceptance remain required before closing ENT01.
