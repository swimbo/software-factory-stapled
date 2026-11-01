# Software factory current status

Updated: November 1, 2026. **Resumed at the user's request.** The full delivery objective remains incomplete. Baseline remains default and profile promotion remains held.

## Repository and verification

- Checkout: `/Users/bo/code/software-factory-stapled`.
- Branch: `codex/phase9d-qualification`.
- Latest implementation revision at this checkpoint: `a6205e53a4214714e58ab480f0d690cc9874ddc7`, which matched the remote branch before this status update. Confirm the current branch and remote before resuming.
- Exact-revision local CI passed: 751 Python tests, 18 subtests, 35 Node tests, secrets scan, locked npm dependency installation/audit and spine build. Receipt: `.local-ci/8d7012f52b9e0b9d1ed7b8cbb8a81cfd37a74939/receipt.json`.
- Baseline harness remains default; optimization profile promotion remains held. Native terminal truncation telemetry remains unknown.

## Work completed locally

- Durable original-only disposable validation lifecycle, independent deadline enforcement, crash/uncertain-start observation and bounded candidate transport output.
- Exact-build validation session binding, signed aggregate receipts, acceptance readback reconciliation and receipt storage code.
- Approval identity/decision integrity and conflict rejection. Authenticated dashboard identity and PostgreSQL audit persistence remain outstanding.
- The operator can prepare an exact source bundle, verify and load a signed image once, transfer a private holdout with digest binding, and join a prepared build to the validation journal. These steps have fixture evidence; they do not establish a real end-to-end delivery.
- Four confirmed foundation review findings were fixed and independently checked. The latest deadline retirement fix received a separate review with no actionable findings.
- These local checks and code changes do not establish live end-to-end delivery or production readiness.

## Live NUC evidence and pause boundary

The first unpaid probe used exact revision `c0d8669f67bf217a48dfbf21aabd32b7f4584bc9`. Its network-none, read-only original candidate returned HTTP 200 through the production Docker transport and was stopped and retained. Cleanup then failed because stopping the transient timer caused systemd to unload its unused service before the service-stop command. The late-start fault injection did not begin.

Independent readback verified the original container remained stopped, the service was not-found/inactive, and all 189 tracked WorkUnits plus the recorded prior volume/container/service/guarded-file inventory were unchanged. Original evidence remains retained; nothing was deleted or replaced.

Commit `dcb56ec` fixes retirement by accepting a failed stop only after exact not-found/inactive readback. Its regression failed before the fix and passed afterward. Corrected source, archive/core hashes and the unchanged reviewed helper were verified and staged on the NUC at:

`/home/bo/.local/share/stapled-factory-control/probe-delivery-validation-20261101/stage-dcb56ec`

**The corrected probe passed after resumption.** Exact `dcb56ec` source completed normal production transport and actual 240-second deadline expiry, then contained a late original start after observer exit without observer cleanup. An exact-ID Docker SIGKILL event and non-OOM exit 137 were witnessed before cleanup. All 189 tracked WorkUnits, 732 prior volume identities, 360 prior role-container lifecycles and recorded service/guarded-file state were unchanged. Original candidates and evidence are retained. Aggregate proof and evidence hashes: [lifecycle receipt](../verification/delivery-lifecycle-2026-11-01/receipt.json).

No new model calls, live runtime activation or profile promotion occurred during this delivery-foundation work. The last verified live runtime remains `b5fe329`; the new receipt code is not deployed. The first full-wall positive control failed at 40% and stopped before substitution controls. Bounded persistent transport in `8d7012f` fixes per-request startup overhead without weakening the unchanged 100ms fixture windows. A fresh actual NUC positive control passed at 100% over three runs; all four archive/diff/signature/artifact substitution controls rejected for their expected reasons. Both failed and passed original targets/evidence are retained. [Binding controls](../verification/delivery-lifecycle-2026-11-01/binding-controls.json).

An isolated actual Restate probe using those passing checks and a fresh fixture key also passed: stale/tampered acceptance denied, client crash after committed acceptance, owned endpoint crash/restart, repeated original observation without reevaluation/resubmission, exact stored receipt and duplicate rejection. Recorded owned PIDs and listeners were confirmed terminal/closed. This uses a fixture-seeded WorkUnit and unsigned loopback test endpoint; it does not establish production identity/authorization or real model output. [Receipt boundary](../verification/delivery-lifecycle-2026-11-01/restate-receipt-boundary.json).

Security still used regex fallbacks on the NUC; Semgrep/Gitleaks/OSV/Cosign are absent there. Full security-engine/provenance verification remains required, as do live runtime integration and application publication gates.

Commit `198677b` makes result collection select the completed original/successor revision using the original allocation/proxy and matching restart claim; 65 operator workflow tests pass and independent delta review found no remaining actionable issue. That operator change is not deployed.

Private evidence/checkpoint directory on the Mac:

`/Users/bo/.local/share/stapled-factory-release/out/delivery-20261101/`

First live probe evidence on the NUC:

`/home/bo/.local/share/stapled-factory-control/probe-delivery-validation-20261101/attempt-1`

## Resume order

1. Preserve the passed original lifecycle proof and retained candidates. Do not repeat or replace those original operations. Recheck Git, runtime and any currently running verification before taking the next step.
2. Resolve the runtime candidate review's six confirmed findings: build-context symlink substitution; false-passing empty holdouts; recovery before an acceptance submission; validation-release pinning across upgrades; complete dependency coverage; and candidate-controlled scanner suppression. Local fixes for all but dependency coverage have targeted tests, including local scanner suppression controls; they still need independent review and release verification. Complete dependency coverage and NUC scanner provisioning remain open. The active endpoint lacks the receipt capability; the new runtime has not been activated. Preserve the passed wall/substitution and isolated receipt proofs. Prepare and review the exact-source runtime release before activation; preserve shared services and all prior resources.
3. Complete the reusable operator join from selected factory output to attested local image build, immutable transfer, isolated validation, accepted matching receipt and validation-gated application publication.
4. Complete Phases 10–11 acceptance, rejection, crash recovery and approval paths without duplicate dispatch.
5. Run one real small fullstack dogfood feature through model build, validation and a reviewed remote PR. Preserve failed consumption; no automatic paid reruns.
6. Implement the planned Inception cascade, deterministic issue qualification/intake, dashboard, authenticated approval flows and required PostgreSQL audit persistence.
7. Complete the full plan-gap audit and required fixes, independent review, secret-safe atomic publication and final verification.

Detailed scope and gates: [delivery-loop-plan.md](stapled-factory/delivery-loop-plan.md). Execution history: [progress.md](stapled-factory/progress.md). The full objective remains intact and must not be declared complete based on the foundation tests or probe alone.
