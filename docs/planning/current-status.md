# Software factory current status

Updated: November 1, 2026. **Paused at the user's request.** The full delivery objective remains incomplete; do not resume implementation, probes, deployment or paid dispatch until requested.

## Repository and verification

- Checkout: `/Users/bo/code/software-factory-stapled`.
- Branch: `codex/phase9d-qualification`.
- Verified implementation revision: `dcb56ec9eb27e294fd068efba4a101e5771ee1eb`; subsequent checkpoint commits may change documentation only.
- At pause, 12 implementation/planning commits were local and the worktree was clean before this checkpoint was created. The user subsequently authorized committing and pushing all current work, including this checkpoint. That publication does not resume implementation or authorize runtime activation; confirm the current branch/remote match with Git.
- Exact-revision local CI passed: 745 Python tests, 18 subtests, 35 Node tests, secrets scan, locked npm dependency installation/audit and spine build. Receipt: `.local-ci/dcb56ec9eb27e294fd068efba4a101e5771ee1eb/receipt.json`.
- Baseline harness remains default; optimization profile promotion remains held. Native terminal truncation telemetry remains unknown.

## Work completed locally

- Durable original-only disposable validation lifecycle, independent deadline enforcement, crash/uncertain-start observation and bounded candidate transport output.
- Exact-build validation session binding, signed aggregate receipts, acceptance readback reconciliation and receipt storage code.
- Approval identity/decision integrity and conflict rejection. Authenticated dashboard identity and PostgreSQL audit persistence remain outstanding.
- Four confirmed foundation review findings were fixed and independently checked. The latest deadline retirement fix received a separate review with no actionable findings.
- These local checks and code changes do not establish live end-to-end delivery or production readiness.

## Live NUC evidence and pause boundary

The first unpaid probe used exact revision `c0d8669f67bf217a48dfbf21aabd32b7f4584bc9`. Its network-none, read-only original candidate returned HTTP 200 through the production Docker transport and was stopped and retained. Cleanup then failed because stopping the transient timer caused systemd to unload its unused service before the service-stop command. The late-start fault injection did not begin.

Independent readback verified the original container remained stopped, the service was not-found/inactive, and all 189 tracked WorkUnits plus the recorded prior volume/container/service/guarded-file inventory were unchanged. Original evidence remains retained; nothing was deleted or replaced.

Commit `dcb56ec` fixes retirement by accepting a failed stop only after exact not-found/inactive readback. Its regression failed before the fix and passed afterward. Corrected source, archive/core hashes and the unchanged reviewed helper were verified and staged on the NUC at:

`/home/bo/.local/share/stapled-factory-control/probe-delivery-validation-20261101/stage-dcb56ec`

**The corrected probe has not been launched.** The `attempt-2` output directory was confirmed absent at pause. No active probe execution handle remains from this work. No new model calls, live runtime activation or profile promotion occurred during this delivery-foundation work. The last verified live runtime remains `b5fe329`; the new receipt code is not deployed.

Private evidence/checkpoint directory on the Mac:

`/Users/bo/.local/share/stapled-factory-release/out/delivery-20261101/`

First live probe evidence on the NUC:

`/home/bo/.local/share/stapled-factory-control/probe-delivery-validation-20261101/attempt-1`

## Resume order

1. Recheck Git, runtime and retained probe state. Run the staged corrected unpaid lifecycle probe once in a fresh evidence directory; retain and observe its original handle/identity through any observation timeout. Prove actual late-start containment after observer death.
2. Verify full prebuilt wall/security positive and substitution controls, then real signed receipt acceptance and recovery. Prepare and review any necessary exact-source, locally built runtime release before activation; preserve shared services and all prior resources.
3. Complete the reusable operator join from selected factory output to attested local image build, immutable transfer, isolated validation, accepted matching receipt and validation-gated application publication.
4. Complete Phases 10–11 acceptance, rejection, crash recovery and approval paths without duplicate dispatch.
5. Run one real small fullstack dogfood feature through model build, validation and a reviewed remote PR. Preserve failed consumption; no automatic paid reruns.
6. Implement the planned Inception cascade, deterministic issue qualification/intake, dashboard, authenticated approval flows and required PostgreSQL audit persistence.
7. Complete the full plan-gap audit and required fixes, independent review, secret-safe atomic publication and final verification.

Detailed scope and gates: [delivery-loop-plan.md](stapled-factory/delivery-loop-plan.md). Execution history: [progress.md](stapled-factory/progress.md). The full objective remains intact and must not be declared complete based on the foundation tests or probe alone.
