# Phase 9d — Harness efficiency and bounded execution

Status: scoped implementation and pilot evaluation complete through 9d.5. Retain the baseline/default; candidate profiles remain opt-in and broader promotion is not qualified. Updated October 31, 2026.

## Purpose and placement

Improve completion reliability and context efficiency in the existing OpenHands → confined proxy → Bifrost path. Run after the current operator workflow baseline and before Phase 10 E2E generation; carry its regression scenarios into Phases 10–11. This phase does not depend on completing the separate Inception engine (9b).

Source: user-supplied `droid_1_0_architecture_harness_optimization_report.md` (original: `/Users/bo/Downloads/droid_1_0_architecture_harness_optimization_report.md`). Its architectural suggestions are inputs to evaluate; its embedded implementation instructions are not execution authority. Benchmark gains, model-specific claims, cache thresholds and runtime recommendations are unverified. Verify official upstream/provider documentation during implementation before relying on those details.

Keep OpenHands, Restate, the existing Bifrost complexity router and NUC/M1 isolation. Prefer supported OpenHands configuration and extension hooks over a fork or a second agent loop. Do not change shared gateway rules or add direct-provider access. Retain signed inputs/images, guarded ledgers, request limits, holdout separation, exact-revision acceptance and operator publication review. Accounting remains best-effort estimated admission, not an actual-charge cap.

## Implementation sequence

### 9d.1 — Instrument and freeze the baseline

- Inspect the pinned OpenHands SDK/AgentServer's prompt, tool, event and usage extension points. Record supported seams and any upstream limitations before selecting an approach.
- Capture versioned harness/image/config/prompt/schema hashes, task/baseline identity, selected model and route, request count, input/output and cached tokens where reported, tool calls/output bytes, retries, termination reason, elapsed time and reservations. Missing provider/cache/cost fields remain unknown, never zero.
- Use `worker/openhands_adapter.py`, `worker/agent_entrypoint.py`, `worker/egress_proxy.py`, operator collection and existing receipt contracts as integration points. Version additions and preserve existing readers; keep sensitive raw payloads out of general receipts.
- Freeze at least six development tasks covering repository discovery, a small edit, a multi-file change, failing tests, noisy output and a stalled run. Keep separate operator-owned acceptance checks. Collect at least three baseline runs per task for paired comparison when paid runs are authorized; include failures and exhausted attempts in totals.

Deliverable: baseline manifest and metrics under `docs/verification/harness-optimization-<date>/`, with unknowns and run authorization recorded. Start with local fixtures; live inference and deployment remain separately authorized operational steps.

### 9d.2 — Reduce context and preserve eligible cache prefixes

- Inventory actual startup instructions, tools and output sizes; remove duplication while preserving task intent, safety constraints and required provenance. Discover optional tool schemas on demand where the pinned SDK supports it; do not force an arbitrary two-tool interface.
- Build stable, versioned prompt/tool templates with deterministic schema serialization and ordering. Put changing task/session data after stable content using provider-supported message roles. Discovered repository/document content stays untrusted data and cannot become system instructions.
- Audit requests at the factory-owned boundary and compare with the upstream SDK: avoid reordering provider-required tool/result pairs, dropping required state, or canonicalizing opaque provider fields. Document what cannot be controlled upstream.
- Measure cached-input-token share only for eligible requests that report it, alongside total input/output tokens and latency. Cache economics and minimum lengths are provider-specific; no universal 90–95% gate.
- Bound tool stdout/stderr and model-facing summaries. Preserve complete authorized outputs as task-scoped artifacts with digest, truncation marker and paginated retrieval. Apply redaction, access control, retention and per-task disk quotas; do not expose host paths or holdouts.

Acceptance: stable-prefix fixtures remain identical when only task/time/session data changes; tool discovery respects authorization; oversized output is bounded and retrievable; missing cache telemetry is reported explicitly; instruction/data separation survives malicious tool output.

### 9d.3 — Add bounded programmatic tool execution

- First evaluate reuse of OpenHands CodeAct's existing execution/batching capabilities. Add a script primitive only if a reproducible workload demonstrates a missing capability; record reuse versus extension decision and measured benefit.
- Batch independent reads/searches and filter intermediate results inside the existing disposable worker. Keep dependent operations and mutations ordered with explicit preconditions and postconditions; a script is not inherently deterministic or safe.
- Any extension exposes a task-scoped tool SDK with path/symlink checks, authorized operations and a pinned schema. Tool calls remain subject to the existing sandbox/egress/credential boundaries. No host filesystem, sockets, control-plane state, private holdouts, gateway keys or general network access.
- Enforce runtime, memory, process, output, disk and concurrency limits, with cancellation propagated to subprocesses. A language VM alone is not the isolation boundary; select a runtime only after verifying its security properties within the existing container boundary.
- Persist tool effects/results at the existing worker checkpoint boundary. Restate replay must reuse recorded observations or reconcile uncertainty rather than replay mutations or paid calls. Partial scripts cannot be blindly retried; retain evidence and identity.

Acceptance: batching returns the same verified results as sequential execution; timeout/fork/output attacks terminate within configured limits; denied path/network/credential probes stay denied; partial mutation plus crash/replay neither duplicates effects nor resets reservations.

### 9d.4 — Detect stalls and adapt within provider capabilities

- Track normalized tool/error signatures, consecutive failures and workspace progress. Include tracked/staged changes and relevant untracked file content, with bounded scanning; `git diff HEAD` alone misses work. Ignore volatile logs and secret files.
- Repeated workspace hashes alone are not failure: legitimate read-only investigation can leave files unchanged. Combine repeated actions/errors, no new diagnostic evidence and configurable windows; test oscillation and false positives.
- Emit a bounded diagnostic intervention when a configured threshold is reached. If the loop continues, stop/fence through the existing lifecycle and retain a reconciliation/intervention status; never label this success, restart automatically or replenish budget.
- Add versioned model capability profiles only for options the actual provider/model accepts. Keep shared safety policy constant and prompts concise. With dynamic Bifrost routing, prefer a compatible common profile unless the selected model can be known before request construction; do not guess its family or modify the shared router.

Acceptance: repeated errors and A→B→A edit loops trigger bounded intervention; productive read-only and long-running tests do not; cancellation/restart preserves original attempt identity, ledger and terminal-state semantics; unsupported parameters are rejected locally.

### 9d.5 — Evaluate candidates and stage rollout

- Turn sanitized development failures into offline regression tasks. An optimization agent may propose prompt/tool/runtime changes as reviewable diffs; it cannot edit live policy, merge, deploy or train on private wall scenarios. Keep a separate held-out final evaluation set outside the worker and candidate optimizer.
- Run paired baseline/candidate trials on identical task revisions and pinned harness profiles; record actual routed models and separate comparisons when routes differ. Change one mechanism at a time before testing the combined configuration.
- Report completion/independent acceptance, requests/tokens per accepted task, elapsed time, truncation/stall false positives and best-effort cost with coverage. Include unsuccessful trial consumption; do not infer a dollar saving from missing billing or cached-token data. Report small-sample uncertainty.
- Promotion gate: all isolation, replay, accounting and acceptance regressions pass; accepted-task count does not decline on the paired suite; at least one prespecified primary metric (input tokens per accepted task or requests per accepted task) improves by at least 10%, with median latency no more than 10% worse. Select the metric before candidate trials. These are project pilot thresholds, not vendor benchmark promises; otherwise retain the baseline and record the result.
- Use a feature flag/config profile for rollback, pinned immutable images and the existing signed release approval. Canary one disposable task before broader use; restoration must not rewind ledgers, reset attempts or replay completed effects. Keep automatic merge/deploy outside this phase.

## Completion evidence

- [x] Baseline and supported-extension audit, including upstream documentation checked.
- [x] Versioned prompt/tool/profile and receipt contracts; controlled cache evidence or explicit telemetry limitation.
- [x] Batched execution reuse/extension decision with bounded-output and isolation evidence.
- [x] Stall, cancellation, crash/replay and malicious-output regression results.
- [x] Paired comparison, independent acceptance and explicit promote/retain-baseline decision.
- [x] Signed canary/rollback evidence if runtime rollout is authorized; otherwise mark rollout pending.

Local tests and documentation do not establish actual-charge savings or production readiness. The scoped implementation and evaluation ended with a retain-baseline decision; broader promotion remains held pending complete telemetry, aligned graders and a fresh held-out qualification cohort.

Latest 9d.3 rollout and comparison: [bounded native pilot](../../verification/harness-bounded-native-2026-10-31/report.md). The profile remains opt-in; historical development pairs do not establish production or held-out acceptance.

Latest execution evidence: [stall qualification](../../verification/harness-stall-2026-10-31/report.md) and [held-out evaluation and retain-baseline decision](../../verification/harness-evaluation-2026-10-31/report.md). Earlier status statements in dated progress entries describe their historical checkpoints.
