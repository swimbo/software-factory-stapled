# Phase 9d.3 — native batching and bounded execution

Implementation and unpaid qualification complete; production activation pending. Existing context-v1 deployment remains unchanged. No new model-facing execution primitive was added and no paid factory inference calls were made.

## Reuse decision

Tested the actual packaged OpenHands SDK 1.58.2 (upstream d0d9d81324210414462fb676b377e0563a08a0cb) TerminalTool against all six frozen tasks, using operator-authored known solutions. Twelve sequential/batched cases passed the separate independent checker; each task produced identical Git trees, clean worktrees and only allowed file changes. Native terminal calls fell from 49 to 12 (75.5%). This establishes capability and equivalence, not autonomous model quality, request/token savings or cost improvements. The prior 18-pair context-v1 results remain unchanged.

The final qualification excludes the independent checker from the worker mounts. `batching.json` includes image identity, source hashes, boundaries, per-case trees and acceptance. Raw archives and SDK logs are retained privately under the operator output directory.

## Implemented controls

Opt-in `bounded-native-v1` enforces a private retained filesystem of at most 1 GiB with no descendant mounts, read-only root, bounded temporary storage, existing CPU/memory/PID restrictions, immutable workspace/allocation identity, and an external 300-second lifetime. Unverified storage is refused before credential reads/provisioning. Timer validation happens before credential release and during reconciliation. SDK cancellation is wired into the existing capture tool. Uncertain batch successors are refused; GET-only reconciliation preserves partial effects and budget reservations.

Native terminal timeout is demonstrably soft: a delayed mutation occurred after its timeout observation. Real namespace deadline fencing stopped both original containers and an escaped session after exactly one partial mutation; the attempt remained reserved with durable fence intent/confirmation and no timer rearm. Containers were scoped by exact identity/labels, and ledgers/workspaces were retained. This is not hosted Restate or daemon-loss recovery.

`limits.json` records actual SDK output truncation, disk/PID exhaustion, cooperative interrupt, SDK cancellation and output-flood containment in a network-none, read-only-root test container with 64 MiB workspace tmpfs. `oom.json` records a separate 64 MiB/no-swap container killed by the kernel (exit 137, OOMKilled true). Production retained bind-mount activation was not performed. No gateway, Docker socket, budget file or checker was exposed to the worker.

The real partial-mutation/drop-response test uses a subprocess, HTTP disconnect and reopened durable store: repeated GET-only reconciliation leaves one POST, one mutation, reserved status and unchanged request-budget spend/anchors. This is a local recovery boundary, not proof of exactly-once upstream SDK actions.

## Review and verification

Initial independent safety review found descendant mount bypass and missing post-arm validation. The subsequent full review found that ledger failure or lock contention could prevent deadline cleanup. All three issues were corrected with tests observed failing before the fixes and passing afterward. Namespace containment now precedes ledger access; durable fencing remains separately uncertain if the ledger fails, and late publication cannot be excluded in that failure case. The final real systemd probe was rerun successfully after this fix. Existing proof tests were preserved; no safety checks were simplified away. Exact source revision `3c07b8145836f5a9f28620bea7c10c41e80c9efe` passed secret scanning, 507 Python tests plus 12 subtests, dependency installation/audit, TypeScript build and 25 Node tests; see `local-ci.json`. Final resource qualification uses the reviewed source hashes in `limits.json`; batching recipes were unchanged after qualification.

## Remaining rollout gate

Prepare a retained quota-backed workspace, build and approve a fresh immutable worker release, deploy the opt-in profile and run its model canary before declaring hosted activation complete. This turn does not authorize or perform deployment, paid trials, promotion, default switching, host filesystem formatting or Phase 9d.4. Existing deployed context-v1 has not acquired these bounds automatically.

See [operator guidance](../../../stapled-factory/worker/NATIVE_BATCHING.md).

Simplification reviewed reuse, quality, efficiency, test value and comments. No substantive changes were warranted; all proof tests were preserved. Three cosmetic probe comments were left unchanged. Reuse had an independent reviewer; other lenses ran inline after dispatch capacity was exhausted. No separate lint script is configured.

Full review receipt: `review-historical.json` covers a9c2148 across correctness, testing, maintainability, security, performance, reliability, adversarial and agent-native lenses. Its historical Not ready verdict is preserved. `review-fix-verification.json` independently verifies the sole P1 resolved at 3c07b81 with no new reliability findings. No peer content reached a provider: Claude transport failed before egress and Antigravity lacked an ACP route; a local adversarial reviewer supplied that lens. One nonblocking test opportunity remains: a provision-level arm/validation failure test asserting zero credential bytes, beyond existing ordering and fail-closed tests.

Changes are committed locally on `codex/phase9d-native-batching`; no remote push or PR was made because the branch includes earlier unpublished phase work. The original workspace's staged work and index were preserved.
