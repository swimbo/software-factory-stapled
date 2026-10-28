# Phase 9 NUC release approval

The October 28 continuation authorizes resolving the remaining gaps and enabling
verified worker dispatch on the NUC under the best-effort budget. It does not
supply an independent GitHub review. A bounded sandbox attempt and a protected
`main` release are separate approval scopes.

## Current readback

- PR [#1](https://github.com/swimbo/software-factory-stapled/pull/1) is draft,
  authored by `swimbo`, with no reviews and `REVIEW_REQUIRED`.
- Readback head: `a2543212c08679d8d760496c249d9cfc5c07aaab`.
  `local-ci/verified` is successful for that head.
- `main` requires that local CI check and one approving review, including for
  administrators. Stale reviews are dismissed and approval of the latest push
  is required. Force pushes and branch deletion are disabled.
- The sole current collaborator is `swimbo`, also the PR author. The author
  cannot supply the independent approval required by this gate.

## Reviewable release scope

The requested scope is the dedicated NUC integration, locally built artifacts,
private gateway-only model access, guarded attempts, fenced successor recovery,
and bounded operator-selected tasks. Budget reservations are best effort; they
are not an actual-charge ceiling. Existing shared workloads remain outside scope.
Full enterprise approval and unrestricted production dispatch are not implied.

The reviewer should inspect the final PR diff, exact-revision local CI receipt,
upstream provenance decision/evidence, immutable deployment manifest, isolation
and recovery results, and rollback procedure. Earlier results cannot approve a
changed head or a substituted runtime artifact.

## Remaining protected-release action

1. Finish changes and local CI, then publish the final head and its receipt.
2. Give an independent human reviewer appropriate repository access and have
   them approve that final head in GitHub. No invitation is sent without the
   operator naming the intended reviewer.
3. Confirm GitHub reports approval and passing required checks before a merge.
   Preserve all current branch protections; do not synthesize an approval or
   treat an agent review as independent human approval.
4. Bind any promoted deployment to the approved commit and immutable artifacts,
   retain its previous frozen release, and verify health plus dispatch behavior.

ENT-05 remains open for independent enterprise release approval until this
sequence has actual evidence. The sole-collaborator condition is an external
human gate, not a failing test that another test can remediate.

## Bounded dispatch preparation

`stapled-factory/deployment/nuc/prepare_dispatch.py` prepares local operator files
only. It never starts a container, contacts the gateway, changes a service, reads
a gateway key, or executes the generated invocation. It requires a private JSON
identity manifest with exactly `worker_image`, `proxy_image`, `helper_image`,
`firewall_image` (immutable SHA256 IDs), and `worker_environment` (the exact
credential-free image environment). These identities must come from trusted
NUC image readback, not worker output.

Supply `--root`, `--unit`, `--manifest`, `--release` (canonical frozen source),
`--approval`, `--signature`, `--trust-root`, `--attempt-db`, `--attempt-anchor`,
and a private `--task-file`. For the dedicated service, the store paths are
`<control-root>/attempt-store/attempts.db` and
`<control-root>/attempt-anchor/watermark.json`. The tool verifies the signed,
unexpired sandbox image approval before preparation. It initializes the store
only when both files are absent; partial state or a mismatched watermark is
quarantined without reset.

Preparation creates a new unit directory, empty committed workspace baseline,
matching successor clone, task, private runtime configuration, and private
`invocation.json`. Existing units are never overwritten. The real proxy command
pins gateway `100.92.236.42`, Flash, six requests, and 1024 output tokens under the
existing $2 best-effort reservation policy. The one-hour pricing policy expiry
requires fresh preparation if approval is delayed. The generated invocation is
review material; activation still requires the protected release, provenance
approval and explicit operator service binding. Generic production scope remains
denied by the worker.
