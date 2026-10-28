# Phase 9 NUC release approval

## Approved deployment scope

On October 28, 2026, the operator explicitly requested resolving release approval
requirements for a **single-operator deployment**, then enabling and verifying
NUC worker dispatch under the best-effort budget. This authorizes the dedicated
NUC deployment and bounded operator-selected sandbox tasks. It supplies human
deployment authorization; it does not supply an independent GitHub review.

The architecture requires human approval for production deployment. It does not
require a second human for this bounded single-operator scope. Accordingly, a
signed operator deployment attestation is the release gate for this scope.
Protected `main` promotion and enterprise separation of duties retain their
independent-review gate. A verified feature commit may be deployed directly to
the dedicated NUC without merging it into `main`.

Budget reservations remain best effort: the $2 target is not an actual-charge
ceiling. Shared NUC workloads, privileged tools, unrestricted production tasks,
and automatic promotion remain outside this authorization.

## Single-operator deployment attestation

Before enabling dispatch, record an evidence receipt identifying all of:

1. The exact deployed source commit, frozen release manifest and local-CI receipt.
   Local CI must pass for that commit; no GitHub runners are used.
2. Exact NUC image identities and signed operator image approval. Its
   `build_inputs` must bind the frozen source and reviewed deployment evidence.
   The trusted operator key stays outside the build context and worker mounts.
3. Accepted upstream publication evidence and completed image/dependency audit,
   including explicit treatment of material residual findings. Online provider
   publication evidence is not a portable publisher signature.
4. The operator authorization above, deployment scope, bounded task identity,
   private service binding, prior release and rollback procedure. A signature
   attests the operator's deployment decision, not an independent human review.
5. Actual post-binding checks: authenticated Restate dispatch, committed worker
   output, gateway-only egress/isolation, and unchanged accounting on duplicate
   dispatch. Earlier or synthetic checks cannot establish a changed deployment.

The signed image approval can bind this receipt by its absolute path and SHA256
in `build_inputs`; no additional signing service is required. Record the receipt
and signature reference in progress/evidence without copying secrets. Keep the
receipt's pre-dispatch acceptance separate from the later observed verification
result, so successful behavior is never asserted in advance. These are operator
release checks; the preparation script's `activation_requires` is a checklist,
not a claim that every operational check is machine enforced.

If an audit exposes an unresolved material isolation or credential-exposure gap,
leave dispatch disabled. On dispatch failure, remove the service's runtime binding,
stop only the dedicated worker resources, preserve attempts/budget state, and
restore the prior frozen endpoint release. Never reset ledgers to retry a task.

## Protected branch and enterprise scope

Live GitHub readback on October 28:

- PR [#1](https://github.com/swimbo/software-factory-stapled/pull/1) is ready for
  review at `75a5c1d3938a5a8d2b7e7ddf295ad565893c9ecd`, with no reviews and
  `REVIEW_REQUIRED`. A later push requires a fresh head/CI readback.
- `main` requires passing strict `local-ci/verified` and one approving review,
  including for administrators. Stale reviews are dismissed and approval of the
  latest push is required. Force pushes and branch deletion are disabled.
- `swimbo` is the sole collaborator and PR author, so cannot provide that review.

Preserve these protections. Do not synthesize approval, merge through an author
bypass, or count agent review as independent human review. A future protected
release requires the operator to name another reviewer, grant access, obtain
approval of the final head and confirm required checks before merging.

ENT-05 remains open for enterprise separation of duties. That limitation is
accepted only as outside the single-operator deployment claim; it is not marked
remediated by an operator signature. The enterprise assessment does not become
an enterprise release approval merely because bounded NUC dispatch succeeds.

## Bounded dispatch preparation

`stapled-factory/deployment/nuc/prepare_dispatch.py` prepares operator files only.
It never starts a container, contacts the gateway, changes a service, reads a
gateway key or executes the generated invocation. Its identity manifest requires
exactly `worker_image`, `proxy_image`, `helper_image`, `firewall_image` (immutable
SHA256 IDs), and `worker_environment` (credential-free image environment).
Identities come from trusted NUC image readback, never worker output.

Supply `--root`, `--unit`, `--manifest`, `--release` (canonical frozen source),
`--approval`, `--signature`, `--trust-root`, `--attempt-db`, `--attempt-anchor`,
and a private `--task-file`. Service store paths are
`<control-root>/attempt-store/attempts.db` and
`<control-root>/attempt-anchor/watermark.json`. The tool verifies the signed,
unexpired sandbox image approval before preparation. It initializes the store
only when both files are absent; partial state or a mismatched watermark fails
closed without resetting either file.

Preparation creates a new unit directory, empty committed workspace baseline,
matching successor clone, task, private runtime configuration and invocation.
Existing units are never overwritten. The real proxy pins gateway
`100.92.236.42`, Flash, six requests and 1024 output tokens under the existing
$2 best-effort reservation policy. Its one-hour pricing policy requires fresh
preparation if activation is delayed. Activation additionally requires the
operator attestation and service binding above. Generic production scope remains
denied by the worker.

## Verified single-operator release

The single-operator deployment gate passed for frozen source `6335e302fb873fe280529e6e512bf9bf7d5140b8`, locally verified with305 Python/23 Node tests, build, npm audit and Gitleaks. All804 release file hashes matched on NUC. Actual signed image approval binds the reviewed frozen code, CI, audit acceptance and prepared runtime/task/invocation. The private signing key remains on Mac; only its public trust root was provisioned to the NUC.

The bound persistent endpoint dispatched `nuc-dispatch-20261028-01:0` through real Restate/CLI/OpenHands/Bifrost, producing a clean proof commit and `needs_validation`. A duplicate returned409 without changing durable state, container lifecycles or accounting. Five requests reserved$1.579010 estimated; actual charges unknown. The actual allocation passed confinement, image, cgroup and firewall checks. [Evidence and signature](../../verification/nuc-dispatch-2026-10-28/) are retained separately from pre-dispatch acceptance.

The endpoint is enabled only for that signed unit. A new unit needs new signed preparation and service binding; no unattended scheduler or generic production tool scope is enabled. Protected-main approval remains outstanding and unchanged.
