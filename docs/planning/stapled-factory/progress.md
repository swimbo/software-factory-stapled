# Progress — Session Log

## Oct 13, 2026

- **Decisions locked** (via Q&A): OpenHands worker · Restate orchestration · NUC/M1 sandbox · local
  platform · tiered models via Bifrost · GitHub issue intake · 3yr-goal-and-down cascade (hybrid proxy
  approves epics) · new fullstack app target · "plan + wall MVP" scope.
- **Hatchet → Restate**: user re-opened orchestration after the initial plan; comparison grounded in
  current docs (both now have durable replay); chose Restate for Virtual Objects + kill/pause + human-
  approval primitives.
- **Dual pricing mode**: cost model reframed for subscriptions (z.ai/ChatGPT) — `pricing_mode`
  (`subscription` | `metered`); ceilings/receipts in quota/time vs dollars. Folded into architecture §10.
- **Planning artifacts written**: `phased-plan.md`, `findings.md`, `data-map.md`, `user-stories.md`,
  `architecture.md` (consensus folded), `consensus-plan.md`, `debate-transcript.md`, `tech-guide.md`,
  `phase-9-plan.md`.
- **Architecture debate**: 3 personas (Security, Systems, FinOps) + moderator; 3 contested decisions
  resolved, 3 flagged NEEDS_HUMAN_DECISION (recorded in architecture §12, still open).
- **Wall MVP built** (`stapled-factory/wall/`): holdout scenarios, evaluator (gate/receipt/evaluate),
  correct+broken target fixtures, isolation test, pytest suite.
- **Dual pricing mode** wired: `config.json` + `config.py` (Ceiling/TierConfig/check_ceiling); verified
  subscription ignores $ / metered enforces `max_cost`; ceiling enforcement in `evaluate.py`.
- **Restate spine written** (`stapled-factory/spine/`): `WorkUnit` VO (claim/lease/stage/receipt),
  `orchestrate` workflow, `wall` effect (invokes `wall/evaluator/cli.py`), `build` stub, endpoint. Python
  `cli.py` seam added and verified (emits the JSON the spine parses).
- **Human gates + approval store** (`spine/src/gate.ts`, `approval_store.ts`, `scripts/approve.ts`):
  `gate.request` (durable `ctx.awakeable`), `resolve`, `get`; `initial_prod_deploy` wired into the
  orchestrate workflow (blocks merge until a human resolves); `GateType` covers all four gates.
- **Credential broker + destructive-op firewall** (`stapled-factory/security/`): `firewall.py`
  (deterministic effect-based op classification: allow/approve/block, deny-by-default) and `broker.py`
  (short-lived HMAC-signed task-scoped tokens; `verify_and_bind` anti-injection guard). **Resolves parked
  decisions #2** (trust anchor = `FACTORY_MASTER_KEY` env, swappable for secret store/KMS) **and #3**
  (effect-based deny, not intent). Verified via stdlib (12 assertions pass).
- **Scope of "dark" resolved (tiered)**: non-security diffs run dark; security-relevant diffs (auth/crypto/
  permissions/data-boundary) get the deterministic pipeline + human escalation. All three debate-flagged
  decisions now closed; commits to building the deterministic security pipeline (Phase 9c).
- **OpenHands Agent Server wired into `build.ts`**: `worker/worker.py` (Python adapter using the OpenHands
  SDK to drive the Agent Server in an isolated workspace, reporting the resulting git revision); `build.ts`
  now shells out to it instead of returning a stub revision. `worker.py --help` + fallback path verified
  (returns JSON with a clear "SDK not installed" error); full run needs the SDK + Agent Server (blocked in
  sandbox).
- **Firewall/broker wrapped around the worker (`worker/guard.py`)**: `guarded_exec` (allow/approve/block)
  + `request_credential` (broker token) are the deterministic boundary; wired into `worker.py`'s own
  subprocess calls and verified (6/6). Remaining third-party step: register `guarded_exec` on OpenHands'
  internal command-execution hook (unverifiable post-cutoff).
- **Full deterministic security pipeline (`security/pipeline.py` + `pipeline_cli.py`)**: orchestrates real
  engines — **Semgrep** (SAST), **Gitleaks** (secrets), **OSV-Scanner** (deps), **Cosign** (provenance) —
  with regex fallback per-category when a tool is absent or crashes, and the report records which engine
  actually ran. **All engines verified in the user's terminal**: semgrep `--config auto` caught `eval`
  injection + a hardcoded AWS key; gitleaks caught the AWS key; osv-scanner parsed a lockfile (0 vulns on a
  minimal fake lockfile); verdict `fail`. Fixed during verification: gitleaks `detect`→`dir` + `--report-path`;
  semgrep default `p/ci`→`auto` (p/ci missed audit rules); semgrep severity from `impact`; osv-scanner v2
  `scan` syntax. Wired into the spine (`security_scan.ts` → `orchestrate.ts`): `fail` blocks, `needs_review`
  → human `security_review` gate, `pass` runs dark. Completes the tiered-dark boundary (Phase 9c).

## Errors / blockers

- **Sandbox blocks pypi** (`pypi.org:443` denied) → cannot `pip`/`uv` install pytest; verified via stdlib.
- **Sandbox blocks local port binding** (`Operation not permitted` on `127.0.0.1`) → cannot run the HTTP
  e2e test in-sandbox; the gate/isolation/receipt logic is verified, the e2e needs to run outside the sandbox.
- **Sandbox blocks npm registry** (`registry.npmjs.org:443` denied) → cannot `npm install` the Restate SDK,
  so the TS spine is written but NOT type-checked or run. The Python `cli.py` seam (what it calls) IS verified.

## Test results

- Gate unit checks: **7/7 pass** (stdlib inline) — quorum, aggregate ≥90%, one-line feedback, 2-of-3.
- Scenario loading + receipt (subscription + metered): **pass**.
- Isolation property (holdout disjoint + unreachable from worker namespace): **pass**.
- HTTP e2e (correct target → pass, broken target → reject): **not run in-sandbox** (port binding blocked);
  run `cd stapled-factory/wall && python -m pytest tests isolation -q` outside the sandbox.

## Oct 16, 2026 — END-TO-END: first full loop completed live (task 6 DONE)

**unit-005 demonstrated the complete cycle**, through four spine process deaths (crash + restarts), with
Restate replaying from the journal every time — no state lost:

1. HTTP invoke → `orchestrate/unit-005/run` on ingress `:8080` (workflow keyed by unitId).
2. `WorkUnit` VO **claim** (single-writer) → stage `building`.
3. **Build effect**: `worker.py` → no OpenHands SDK → `skipped` + real git revision `b505af5a…`.
4. **Wall ran live** against the auth fixture (`:8777`): **5/5 scenarios, verdict passed**, usage receipt
   (6 requests, wall-clock, tier, `pricing_mode`), no ceiling violation.
5. **Deterministic security scan** — correctly scoped to the unit's diff (empty → skipped with a note);
   revision 1 of this scan wrongly gated on the factory's own fixtures and was fixed.
6. `gate.request` → **whole workflow SUSPENDED** (`orchestrate/unit-005/run` + `gate/request` both
   `suspended`; stage `gated`) — durable, zero compute, survives restarts (verified: it sat through one).
7. Human approval (`POST /gate/resolve` → awakeable) → workflow resumed → **stage: `merged`**.
8. Invocation list emptied; the suspended POST client received the full merged receipt.

**Verdicts along the way (all real outcomes, not artifacts):**
- unit-002: wall correctly failed 3× (nothing was listening on :8777 at the time) → exhaustion → failed.
- unit-004: wall passed but the **security pipeline blocked it** (scan was wrongly scoped to the factory
  tree; fixed to scan the unit's diff; a no-diff unit skips with a note).
- unit-003: my own typo'd targetRepo in the invoke → journaled → retry-forever → killed via
  `restate invocations kill` (the CLI kill worked).

**Run-book learnings (verify these persist):** `restate deployments register … --force` for new revisions;
workflow run POSTs block until completion/gate (invoke client: undici 300s headers cap — the workflow
itself is unaffected); `restate state get WorkUnit <id>` + `restate invocations ls --all` are the
authoritative debug surface; `restate invocations submit` does NOT exist (plain HTTP POST only).

## Oct 16, 2026 — first live orchestrate.run

- **Register + invoke WORKED**: ingress accepted `POST /orchestrate/unit-001/run`; Restate journaled it,
  the workflow claimed the unit, dispatched the build step — confirming the
  `/<workflow>/<key>/<handler>` URL pattern and the whole Restate wiring end-to-end.
- **First runtime bug (found by the run, fixed)**: `invoke.ts` stringified `taskFile: undefined` + dropped
  key → `buildStep` defaulted to `""` → `worker.py --task-file ''` crashed (a retried 500). Fixed:
  `orchestrate.run` now **fails fast** with `restate.TerminalError` listing missing required fields
  (unitId/target/scenarios/taskFile), and `invoke.ts` requires `<unitId> <target> <scenarios> <taskFile>`.
- Added `fixtures/unit-002.task.md` as the spec file for the first clean invocation.
- Note: stuck invocation `inv_1a7cx…` will replay on the new deployment and terminal-fail (expected).

## Oct 14-16, 2026 — spine brought to live

- **Type-check VERIFIED against the real SDK**: `npm install` (user terminal) → `@restatedev/restate-sdk`
  **1.17.3**; `tsc` compiles all of `spine/src` with **zero errors** — the blind-written server API
  (`restate.object/workflow/service`, `ctx.*`, `.endpoint().bind().listen()`) is confirmed correct.
- **Client-API bug found + fixed**: SDK 1.x has **no client-side `connect()`**, and the `restate` CLI has
  **no invoke/submit command** (its `invocations` group is management-only: list/journal/kill/pause/resume).
  External invocation is a **plain HTTP POST to the ingress :8080** (`/<workflow>/<key>/<handler>` /
  `/<service>/<handler>`). `invoke.ts` + `approve.ts` rewritten onto `fetch` to `:8080`, run-scripts use
  `node --experimental-strip-types` (no tsx/esbuild dependency); verified to compile AND execute up to the
  TCP connect (EPERM here = the known sandbox wall, not the script).
- **Restate installed via npm global** (brew is blocked: user's Command Line Tools too old for macOS 26 —
  same error re-ran twice; don't re-run): `restate-cli 1.8.0` + `restate-server 1.8.0` verified via `--version`.
- **Live (user terminal)**: `restate-server` running (partition leaders elected — normal startup); spine
  `node dist/main.js` **listening on :9080** (dev warning: no request-signature validation — tighten later).
- **Sandbox boundary confirmed**: local TCP *connect* is also blocked (`Operation not permitted` probing
  :9070), so every admin/ingress check from here is impossible; those are the user's terminal.
- **Remaining for end-to-end** (user): run `wall/target/app.py`; `restate deployments register
  http://localhost:9080`; `npm run invoke -- unit-001 http://127.0.0.1:8777 wall/holdout/scenarios.json`;
  then `npm run approve -- unit-001:deploy approved <name>` to break the human gate.

## Oct 26, 2026 — continuation preflight

- User confirmed Phase 9 verification and worker integration as the restart point; no Phase 1 restart.
- Enterprise implementation preflight is blocked: phase overview does not mark phases 1–8 complete; UI/UX and E2E planning artifacts are absent; the separate tech-guide debate is explicitly deferred. Architecture debate and phase-9 plan exist; findings report no unresolved human decisions.
- Current verification was interrupted after 16 passes and one failure: `security/tests/test_pipeline.py::test_secret_in_diff_fails` did not find a critical secret finding. Full suite not completed.
- Wall isolation test models an empty temporary directory, not a real worker security boundary. OpenHands SDK calls and command interception remain unverified.

## Oct 26, 2026 — preflight and Phase 9 continuation

- User authorized the tech-guide debate and Phase 9 continuation. Four perspectives reached conditional consensus; see `tech-guide-debate.md` and `tech-guide-evidence.md`. Actual worker isolation, dispatch reconciliation and budget enforcement remain unproved.
- Added minimal UX, UI and E2E planning artifacts and reconciled the phase overview against existing planning artifacts. These are planning completion, not implementation or acceptance of the dashboard.
- Wall tests: `python3 -m pytest wall/tests wall/isolation -q`: 14 passed. The isolation test is a simulation, not proof of real worker isolation. The broken-target test expects endpoint and status details; its compliance with the opaque-feedback requirement needs review.
- The spine now fails a workflow when the worker reports anything other than `completed`, preventing a skipped build from reaching wall and approval. `npm run build` passed. No live workflow test was performed.
- OpenHands SDK is absent in the current Python environment. The existing adapter uses unverified imports, does not pass its session key, and does not enforce the actual worker sandbox or reconcile ambiguous dispatches. OpenHands integration is not complete; do not run it against production or claim its boundary is enforced.
- Prior combined suite was interrupted after 16 passes and a failure in `test_secret_in_diff_fails`; investigate the engine-dependent classification before claiming the security suite passes.

## Oct 26, 2026 — security test and worker safety

- Fixed diff scanning to run deterministic patterns on the supplied diff even when external scanners are present; real engines now require an explicit scan target. Added a regression for clean external engine results with a secret in the diff. `python3 -m pytest security/tests wall/tests wall/isolation -q`: 32 passed.
- Removed the speculative OpenHands dispatch path and made the worker return `blocked` until the real integration, sandbox, and recovery gates are verified; the spine already fails closed on this status. No live worker task was run.
- Docker daemon is reachable, but no OpenHands SDK, Agent Server image, or configured OpenHands/Bifrost credentials were identified in this process environment. A real isolated agent, model-backed task, and crash recovery test remain outstanding. Existing wall isolation checks are simulations.

## Oct 26, 2026 — Agent Server disposable probe

- Started OrbStack (without stopping existing containers); installed Python 3.12.14 and `openhands-sdk` 1.58.2 in `stapled-factory/worker/.venv`. Pulled `ghcr.io/openhands/agent-server:latest-python`, digest `sha256:90da82d4dd57f713e2b9f5bc9444256b06c8bc709ca5ac794048ea9e1952b466`; server OpenAPI reported 1.58.2.
- Started a disposable non-root container with network `none`, no mounts, all capabilities dropped, no-new-privileges, 2 GiB memory and 256 PID limits. Server started. Via its real conversation-scoped bash endpoint, the Docker socket and specified host holdout path were absent and a connection to 1.1.1.1:443 returned network-unreachable (101). This does not prove isolation of a production deployment, credential secrecy, or all egress paths.
- Created a conversation with a caller-chosen UUID and dummy model settings; it remained readable after restarting the container. Repeating creation with the same UUID returned 200. No model call or agent task was run, so this is not proof of idempotent dispatch or recovery of an in-flight build. The container was removed after the probe; image and SDK remain locally installed.
- No Bifrost listener was observed on checked local ports, and no model credentials were available in the process environment. The real worker adapter, authenticated client/server setup, model routing, budget enforcement, host-to-worker isolation, crash-after-acceptance recovery, changed-revision handling, and full workflow remain unverified. Worker dispatch stays blocked.

## Oct 26, 2026 — gateway reachability and internal-network probe

- The existing private Bifrost OpenAI-compatible models endpoint was reachable from this host and returned HTTP 401 without credentials. No gateway credential variable was present in the current process environment. The local Ollama catalog contained only an embedding model; no usable local coding model was identified.
- A disposable Docker internal network with two containers using the pinned OpenHands image allowed the worker to reach an HTTP stand-in by container name (200); direct TCP to 1.1.1.1:443 returned network-unreachable (101), and host.docker.internal did not resolve. The worker had no Docker socket or holdout path. Both containers and the network were removed.
- This probe did not connect to Bifrost, enforce a production gateway-only proxy, run an agent/model task, or test in-flight recovery. The worker remains blocked pending an authorized credential source and a verified gateway path, sandbox, and recovery protocol.

## Oct 26, 2026 — authenticated Bifrost preflight

- User identified and authorized local use of the DSH credential source. Its mode was 0600; a host-side request to the private Bifrost models endpoint with the referenced credential returned HTTP 200. No credential value was printed or saved in this project.
- A gateway-only proxy and worker run were not established: Docker container creation stalled twice (the daemon responded to inspection and other workloads were running). Cancelled both create attempts and removed the task-created container and internal network without restarting the shared engine.
- No model inference, in-flight crash test, or production egress verification occurred. The worker remains blocked.

## Oct 27, 2026 — gateway and worker recovery probes

- Docker created and ran a network-disabled Python container in 0.3s; no engine restart was needed. Existing `aihardtryton-fit-app` and `aihardtryton-fit-db` were not interrupted.
- Authorized DSH credential authenticated Bifrost. A bounded direct request and an internal-network request through `worker/probes/gateway_proxy.py` returned HTTP 200 from `deepseek-v4.1-flash:1017`; the worker-side probe's direct TCP connection to 1.1.1.1:443 returned network-unreachable (101). Proxy credential entered via stdin and was kept on the separate proxy side. The probe proxy's request cap is process-local and is not a production budget or egress policy.
- A disposable OpenHands 1.58.2 server, internal network and proxy ran a model-backed task: `hello.txt` was created in a temporary Git repository. The server reported 34,239 prompt and 519 completion tokens for this conversation, and reported $0 cost; $0 is not verified billing or a hard spending limit. File was untracked, not a verified committed revision or a spine integration.
- For a second conversation, creation with an initial message started it automatically: an explicit run returned 409. It was observed `running`, then the server container was SIGKILLed and restarted. The same conversation ID remained readable, but its status stayed `running` throughout 30 seconds and the expected `recovery.txt` was absent. Recovery of in-flight work and duplicate-dispatch avoidance are therefore not verified; status could be stale. No redispatch was attempted.
- Probe containers and network were removed. The production worker remains blocked; actual isolated gateway policy, credential isolation against an adversarial worker, durable reconciliation, commit/diff handling, cost ceilings, and Restate integration remain open.

## Oct 27, 2026 — durable attempt groundwork

- Added `worker/attempt_store.py`: an atomic SQLite reservation grants dispatch only on first reservation; a repeated matching reservation is `unknown` unless a result was recorded, while changed identity or a conflicting result is rejected. This deliberately fails closed for crashes even before remote acceptance. It is not wired into the worker or Restate yet and is not a verified OpenHands reconciliation implementation.
- Tests kill a process after durable reservation and exercise concurrent callers, identity conflicts, and immutable completed results. `python3 -m pytest worker/tests security/tests wall/tests wall/isolation -q`: 36 passed; `npm --prefix spine run build`: passed.
- OpenHands upstream issue https://github.com/OpenHands/software-agent-sdk/issues/4487 reports interrupted tool calls can corrupt a conversation branch. Applicability to our pinned version has not been established. Prior real kill test remains unresolved; do not equate the local attempt-store kill test with real worker recovery.
- Production gateway-only egress, authenticated runtime deployment, end-to-end Restate dispatch and actual crash reconciliation remain open. Worker stays blocked.

## Oct 27, 2026 — attempt, egress and recovery integration work

- Restate build invocation now passes a stable workflow-key/attempt ID and a pre-existing absolute attempt-store path. The path is journaled before dispatch; missing/nonabsolute/missing-file configuration blocks. Mismatched workflow key and unit ID are rejected. Unknown results persist the attempt and conversation ID in the WorkUnit and transition to `needs_reconciliation` instead of initiating another build.
- An isolated Restate server and stub worker verified the unknown result and persisted identity in actual WorkUnit state. Repeating the same workflow invocation returned 409 and the stub was called once. This is not an end-to-end OpenHands test; no shared Restate deployment was modified.
- The worker's durable-attempt helper is connected to an authenticated OpenHands adapter with fixed conversation IDs, no redirect or ambient proxy, strict transport checks, GET-only reconciliation and verified-revision callback. On a disposable pinned real server, initial create and authenticated GET worked; wrong key returned 401, and reconciliation made no second POST. Production `run_task` remains blocked; no actual runtime or revision-verification binding is enabled.
- A disposable non-billable server crash probe observed stale `running` after SIGKILL. The pinned server source shows its lease may remain held for 45 seconds when hostname/PID are reused; after expiry a pause caused hydration and status `error`, but a run then failed because dummy model credentials were not persisted. Whether an interrupted tool already caused effects cannot be established from this status; safe automatic resume is not proven.
- Added experimental networkless Unix-socket proxy/bridge and topology checks. A non-root worker with `network=none` and a read-only shared socket volume reached real Bifrost through a separate proxy; public TCP and DNS were denied, no holdout/Docker socket/budget volume was visible, and unlink of the socket was denied. Wrong path/model/token requests were rejected. A proxy-side SQLite request limit survived a proxy container restart: the next request returned 429. The probe's containers and volumes were removed. The limit is not a dollar cap; deployment, trusted proxy identity, secure provisioning, control against privileged topology changes, and integration with the actual OpenHands runtime remain unverified.
- Verification: `python3 -m pytest worker/tests security/tests wall/tests wall/isolation -q` 83 passed; `npm run build` and two Node build-seam tests passed. These do not establish automatic recovery, production readiness, or end-to-end dispatch. The worker remains blocked.

### Combined networkless OpenHands probe (Oct 27)

- Tested a pinned OpenHands server with `network=none`, a read-only shared Unix-socket volume, a separate gateway proxy and durable proxy-only budget. The Agent Server runs as UID 10001; initial test proxy UID 1000 could not connect, so the disposable socket/proxy ownership was corrected to UID 10001. This UID dependency must be enforced by provisioning.
- Using the actual OpenHands adapter and attempt store with a disposable repo, a model-backed task wrote and committed `hello.txt`. Initial polling expired with `unknown`; later GET-only reconciliation returned `completed` and verified clean changed revision `e5ceb9a901d50dce1fb18507ffad72e41c7c6c5e`; another call returned the identical stored result. The store and workspace were inside the disposable worker for this probe, so this is not a safe production deployment or Restate end-to-end integration.
- The bounded proxy exhausted its six-request allowance (including retries and a connectivity check); a second disposable attempt received 429 and was paused. All probe containers and volumes were removed. No automatic duplicate submission was used. This was successful late-result reconciliation, not successful in-flight process-crash recovery.

### Final checks and remaining gates (Oct 27)

- Repeated the real OpenHands crash test with the corrected adapter and actual attempt ledger: server killed while a local mock model response was pending; same attempt reconciled as `unknown` after restart and again after lease expiry/hydration changed the server status to `error`. Server access records showed one create and the mock saw one request. No interrupted tool side effect or successful resume was demonstrated.
- `egress_guard.py` now supports the networkless volume topology through its CLI and checks explicit numeric non-root users. A disposable Docker topology passed; a root-user negative control failed. This does not attest proxy image/code identity or workspace content.
- Added a Git verifier that accepts only a clean, committed descendant of an explicit baseline, with tests. It is not connected to the production entry point and cannot alone establish sandbox/workspace provenance or prevent post-check mutation.
- Final rerun: 94 Python tests passed; TypeScript build and two Node tests passed. Test containers and volumes were removed. `run_task` still blocks. The Restate workflow records unknowns but currently ends rather than providing a reconciliation/resume handler; the store pathname is journaled, but store identity/replacement, restoring stale snapshots, and multi-host ownership still require a fail-closed design. Real end-to-end Restate-to-OpenHands dispatch, automatic recovery of interrupted tools, trusted runtime/proxy provisioning and separately enforced monetary ceilings remain open.

### Reconciliation and provisioning groundwork (Oct 27)

- Added a keyed Restate reconciliation workflow that preserves the original attempt, store paths and conversation identity, polls without redispatch, and holds a recovered revision at `needs_validation`. An isolated Restate server with a stub worker verified this state transition; the wall target is not yet bound to the recovered revision. The production worker's reconciliation entry point still blocks, so this is not a working end-to-end resume.
- Added a guarded attempt store with a store UUID and independently recorded generation watermark. Tests reject a replaced database, a stale database restored against a current watermark, and a failed watermark update. Both files restored together, or compromised together on one host, cannot be detected without an independent trusted authority. The guarded store is not yet wired into the production worker.
- Added durable proxy-side monetary reservations before upstream requests, including a ceiling, per-request worst-case amount and expiring policy identity. Concurrent and restart tests enforce the configured reservation ceiling. A trusted pricing policy, comprehensive cost bound and user-specified dollar ceiling are still required; these tests do not establish an actual provider billing cap.
- Added a Docker provisioner and preflight for a networkless worker and separate proxy, socket, budget and watermark volumes. A disposable Docker test used synthetic images and a dummy credential. Image provenance, independent restore protection and actual production wiring remain unverified. A separate Docker exec transport passed a disposable networkless loopback test with a dummy session key; it is not yet bound to the OpenHands adapter or an authenticated attempt/container identity.
- Verification: 128 Python tests passed; TypeScript build and five Node tests passed. An isolated Restate test used a stub, not OpenHands. No allocation-labeled test containers or volumes remained. `run_task` remains blocked; full integration, real in-flight crash recovery, production gateway-only egress and a verified monetary ceiling are not complete.

### $2 ceiling and integration follow-up (Oct 27)

- User selected $2 per work-unit attempt. The proxy defaults its durable reservation limit to 2,000,000 microdollars and rejects a greater configured amount. A caller still supplies the worst-case request reservation; without an authoritative pricing and routing policy that bounds every charge, this limits reservations rather than establishing a provider billing cap.
- Read-only inspection of the live Bifrost configuration found automatic routes with provider fallbacks. Their rule expressions do not match the worker's explicit Flash model; the relay can reroute image-bearing requests. Actual account billing, fees, retries and every route are not verified. Ollama's published prices alone are insufficient to prove this account's billed costs.
- Added a read-only guarded-store lookup and reconciliation-only path: a missing or mismatched attempt does not reserve or contact the server. Connected the OpenHands adapter to the Docker exec transport. Simulated lost-response tests show one POST followed by a GET and no second POST. The production entry point remains blocked until runtime identity, credentials, request and baseline are durably bound and the isolation and billing boundaries are proven.
- Both newly completed and reconciled revisions now stop at `needs_validation`: a caller-provided target is not evidence that the wall would evaluate that revision. This closes the earlier path that could promote an unbound target; a trusted deployment-to-revision binding and subsequent validation resume remain to be implemented.
- A new disposable `network=none` container denied direct public TCP and private-gateway name resolution. The attempted combined current-proxy/provisioner test did not run because Docker image building stalled; it was interrupted without an engine restart. The previously pinned OpenHands SDK 1.58.2 image was fetched and checked, but the earlier crash probe's complete setup was not preserved and could not safely be replayed. Earlier real gateway and crash observations remain as recorded above; no new successful in-flight crash recovery was demonstrated.
- Verification: 132 Python tests passed; TypeScript build and six Node tests passed. No production worker, deployment or shared workload was activated or restarted. Phase 9 remains open.

### Reproducible crash and egress probes (Oct 27)

- Added `worker/probes/crash_recovery.py`. A disposable pinned OpenHands 1.58.2 server with `network=none` and a local blocking model stub was killed after the model request arrived. In the first run the server recovered the same conversation, took over the dead owner's lease, and reported `error`, which persisted after 50 seconds. In an assertion-bearing second run, the process PID was reused and status remained `running` after 50 seconds; GET did not trigger takeover. Each run recorded one model request. No diagnostic error event or interrupted tool effect was observed, and successful safe resume was not established. The adapter continues to classify both as unknown; the production worker is blocked.
- Added `worker/probes/verify_gateway_isolation.py`. A disposable provisioner run with the current proxy, synthetic budget policy and dummy key denied direct TCP and passed the socket request to Bifrost, which returned 401. This verifies the tested topology and fixed proxy path, not authenticated inference, production image provenance, post-inspection tampering resistance or an actual billing cap. Probe-owned resources were removed.
- Proxy rejects image blocks, duplicate JSON keys and non-finite values before forwarding; the live relay can otherwise trigger image routing and extra model calls. Read-only gateway inspection found a $100/month global Ollama budget, not a per-attempt $2 cap. Per-request cost bound, account billing, provider retries and complete routing remain unverified; the $2 reservation is not an established charge ceiling.
- The Restate build seam now turns worker-process failure or malformed/mismatched output into an unknown result with the deterministic original conversation ID; it rejects a reconciler identity belonging to a different attempt. This protects against an immediate child-process failure, not a Restate process crash after remote acceptance. Durable provisioner/container binding, enabled production worker and revision-to-target validation remain open.
- Verification: 132 Python tests passed; TypeScript build and eight Node tests passed. The probes are local evidence only and do not complete Phase 9.

### Controller recovery and one-shot allocation (Oct 27)

- A repeatable `worker/probes/controller_recovery.py` killed the controller while the pinned real Agent Server was awaiting a local mock-model response. Reconciliation through DockerTransport and GuardedAttemptStore completed the same deterministic conversation, with one tool-bearing model request and two model calls total. This used a synthetic model and revision verifier; it did not test Restate end to end, a real git result, gateway billing, or recovery from server death.
- A separate server-crash probe confirmed that after lease expiry a pause rehydrates a stale conversation to `error`, with one observed mock request. PID reuse can keep the prior lease looking alive. Neither `running` nor `error` proves whether a tool had effects. Pausing is not automated because a healthy live owner could be interrupted; safe server-crash resume remains unresolved.
- Added guarded one-shot allocation reservations before provisioning: matching retries cannot create a second allocation and fresh budget volumes, including under concurrent reservation. The primitive is not wired to the blocked production entry point; raw provisioning still exists for probes, allocation reconstruction and image provenance are unverified, and coordinated rollback of store and watermark remains undetectable.
- Restate schedules read-only reconciliation for uncertain worker outcomes, stops when blocked and caps polling at 20 observations, retaining `needs_reconciliation`. An isolated Restate instance confirmed the earlier scheduling path; final blocked and bounded behavior is unit-tested. Restarting the completed keyed reconciliation workflow is not yet implemented.
- Hardened sandbox inspection against shared PID/IPC/cgroup namespaces and disabled security profiles. The proxy now streams response chunks promptly and rejects provider-hosted tools that could incur separate charges. A disposable networkless worker with the actual proxy denied direct TCP and reached Bifrost with a dummy key, returning 401. These do not establish production gateway-only egress or a verified billing ceiling.
- The controller probe's mock used an OpenAI-compatible endpoint. It did not establish that both the agent and its additional tool-free model request pass through the Anthropic proxy and the same budget ledger. Provider costs, routing, extra relay calls and account billing still need a verified hard bound; the $2 reservation cap is not proof of a $2 actual-charge cap.
- Verification: 150 Python tests passed; TypeScript build and ten Node tests passed. Production `run_task` remains blocked. Phase 9 remains open.

### Repeatable observation and bound allocation (Oct 27)

- A real pinned OpenHands server was killed during a local mock model request in `worker/probes/server_crash_guarded.py`. The actual adapter and reopened guarded store observed the same attempt three times as unknown; the mock recorded one request. This verifies no redispatch in that path, not successful server recovery or safety after an interrupted tool effect.
- Added repeatable keyed Restate observation and atomic attempt/identity-checked completion. A dedicated Restate server and SDK endpoint accepted two observation requests against the blocked worker; both returned blocked and the unit remained `needs_reconciliation`. This is not an end-to-end OpenHands dispatch, and Restate endpoint authentication and authorization remain open.
- A one-shot allocation can now bind full container and network IDs, volume metadata, a configuration digest and a clean Git baseline before releasing the gateway credential. A disposable Docker probe created and reinspected those actual resources, and rejected a repeat allocation. Image provenance, independent rollback authority, workspace race fencing and production worker integration remain unverified.
- The same networkless probe denied direct public TCP, metadata IP and DNS; two handcrafted agent/title requests traversed the proxy, received 401 using a dummy key, and produced two reservations in one ledger. This is not a real Agent Server model run or proof of production topology or provider charges.
- Read-only live inspection found the authorized DSH credential maps to the `deepseek-harness` Bifrost virtual key, which has no virtual-key budget. The existing $100/month model budget is not a per-attempt cap. Gateway/provider retry behavior and actual billing remain unbounded by the present proxy's caller-supplied reservation, so a $2 charge ceiling is not verified.
- Verification: 153 Python tests passed; TypeScript build and 12 Node tests passed. Probe containers and allocation-labeled volumes were removed. Production `run_task` remains blocked; Phase 9 remains open.

### Interrupted tool and Agent Server ledger probes (Oct 27)

- `worker/probes/server_tool_crash.py` used the real pinned OpenHands server and a synthetic model to start a shell tool that appended a workspace marker and then blocked. After the server container was killed and restarted, three guarded reconciliations stayed unknown; the marker remained exactly once. This verifies no replay for that observed tool-effect case, not safe resumption or proof that all tool effects are fenced.
- Added `execute_bound_attempt`, connecting the guarded attempt reservation to one-shot allocation, bound-resource reinspection and read-only reconciliation. Its coordinator tests include simulated loss after binding; image trust, independent store rollback protection, provider billing, server-crash recovery, and actual production wiring remain unverified. The production CLI still returns blocked.
- `worker/probes/agent_gateway_ledger.py` ran a real networkless Agent Server with the bridge and production proxy handler, a synthetic HTTPS upstream and dummy keys. Worker and proxy ran as UID/GID 10001 with a 0600 socket and a read-only worker mount. One tool-bearing agent request and one tool-free title request consumed two $1 synthetic reservations in one ledger; a third request got 429 without reaching the mock upstream. This does not prove real Bifrost rates, billing, trusted images, or independently durable budget anchors.
- The Restate SDK endpoint now binds `127.0.0.1:9080`; a disposable local start confirmed the listening address. This does not protect against other local processes and does not secure Restate ingress or admin interfaces. Updated `spine/README.md` to reflect the blocked worker and validation state.
- Verification: 157 Python tests passed; TypeScript build and 12 Node tests passed. Phase 9 remains open: real server-crash completion, authenticated end-to-end Restate-to-OpenHands dispatch, production egress and a verified $2 actual-charge ceiling are not established.

### Request identity and crash fencing checks (Oct 27)

- The Restate SDK endpoint now requires configured Restate request-identity public keys and binds loopback. An unsigned discovery request returned 401, and missing keys prevent startup. A dedicated Restate server with a disposable signing key registered the endpoint (201), invoked the blocked workflow (200) and made two signed observations (200 `blocked`). Temporary keys, data and processes were removed. Restate ingress/admin caller authorization and actual OpenHands dispatch remain separate gates.
- Runtime validation now requires exactly one writable workspace bind mount; missing, read-only, or duplicate mounts fail inspection. A disposable Docker probe reinspected the original bound allocation, rejected duplicate allocation, blocked direct public and metadata TCP and DNS, and recorded two dummy-key requests in one synthetic ledger. This is not proof of trusted image provenance, exclusive workspace ownership, actual billing or production topology.
- The real pinned-server interrupted-tool probe now kills the server process after a shell tool writes a first marker and waits for a later write. After container restart and a delay longer than the tool's sleep, the second marker was absent, the first remained once, and three guarded observations stayed unknown. This establishes the specific stopped-process/no-replay behavior, not safe continuation of arbitrary interrupted tools or independent effect fencing.
- Proxy requests now reject unsupported top-level options and unbudgeted document/audio/video blocks; the synthetic Agent Server ledger probe remained compatible. The per-request reservation is still caller supplied and does not bound provider retries, routing or charges. The $2 actual-charge ceiling remains unverified.
- Verification: 160 Python tests and 13 Node tests passed. Production `run_task` remains blocked. Phase 9 remains open.

### Recovery containment and ingress controls (Oct 27)

- Added `worker/crash_fence.py`, an explicit, idempotent operation that inspects the guarded unresolved allocation, kills only its bound full worker-container ID and verifies it is stopped without deleting the workspace or ledger. `worker/probes/fence_container.py` verified in disposable Docker that a pre-fence marker remained and a delayed second write did not occur; the attempt remained unknown. This contains later effects only, does not prove prior tool outcomes, and cannot prevent a privileged Docker administrator from restarting the container. It is not connected to production recovery.
- Hardened WorkUnit stage and evidence handling and added a local authenticated ingress gateway with a narrow operation allowlist. An isolated signed Restate server plus SDK endpoint and gateway returned 401 for unauthenticated input, 404 for a blocked mutation route, and 200 for an authorized workflow and blocked reconciliation; state remained `needs_reconciliation`. Direct Restate ingress/admin access still bypasses the gateway unless independently isolated. Filesystem paths and callers need production authorization.
- Read-only live Bifrost inspection found `ollama-cloud` configured with zero retries and one enabled provider key, while the shared virtual key has no per-attempt budget. Aggregate account logs showed Ollama cost zero or null and separately billed classifier activity; these are not worker-specific actual charges. Account plan and all upstream billing/route/fallback behavior remain unverified. The proxy has an unrestricted outbound Docker bridge: its fixed host is code policy, not network-level enforcement.
- Verification: 168 Python tests and 16 Node tests passed. Disposable probe resources and isolated Restate processes were removed. Production worker remains blocked; safe server-crash continuation, complete trusted runtime wiring, production gateway-only egress, and the $2 actual-charge ceiling remain open.
- User confirmed the Ollama account uses the current token-credit plan. Read-only account settings showed Pro usage credits with auto-reload off and per-request charges. Neither observation attributes gateway traffic to an attempt or establishes the effective route, retry/fallback behavior, additional calls, or a durable per-attempt provider charge cap.

### Proxy network firewall feasibility (Oct 27)

- Added standalone `worker/netns_firewall.py` and `worker/pinned_https.py`; neither is wired into the production proxy or provisioner. The installer assumes a trusted Docker daemon and helper image, sets and verifies a gateway-IP TCP/443-only IPv4 OUTPUT rule and IPv6 default deny, and checks the proxy's container ID, PID, start time and network identity across setup.
- `worker/probes/proxy_netns_firewall.py` exercised the installer and pinned TLS against a disposable proxy namespace on a dedicated Docker bridge. After the short-lived NET_ADMIN helper exited, certificate-validated TLS to the pinned gateway returned 401 with no credential; public, metadata, wrong-port, IPv6, TCP loopback and Docker embedded DNS attempts were denied, and an unprivileged rule change failed. An earlier default-bridge probe allowed loopback and missed Docker's embedded DNS; the final rule has no network loopback allowance. Probe-owned resources were removed.
- This is a feasibility result, not production gateway-only verification: helper/image provenance, binding to credential release, network-namespace replacement after restart, ongoing rule verification and the live proxy integration remain open. The $2 actual-charge ceiling and safe server-crash continuation also remain unverified.
- Final verification: 186 Python tests and 16 Node tests passed; the TypeScript build passed.

### Opt-in firewalled provisioning (Oct 27)

- Connected pinned-IP HTTPS and the namespace firewall to an opt-in provisioner path. The firewall is installed and checked before the gateway credential is released; the configured proxy IP must match the firewall. Bound-allocation reinspection checks proxy PID/start identity and existing rules without silently repairing them.
- `worker/probes/verify_firewalled_provision.py` exercised the actual proxy and provisioner on a disposable dedicated bridge with a dummy credential: the pinned gateway returned 401, direct proxy public/metadata/IPv6/DNS and wrong-port access were denied, and reinspection rejected a real proxy restart. Two requests consumed only synthetic reservations. Probe resources were removed.
- This is not production activation: trusted Docker and helper/image provenance, lifecycle enforcement after the last inspection, a proven provider charge bound, and safe server-crash continuation remain unresolved. The production worker still returns blocked.
- Final verification: 191 Python tests and 16 Node tests passed; the TypeScript build passed.

### Fenced restart groundwork (Oct 27)

- User selected recovery by abandoning an uncertain attempt, fencing the old worker, and restarting in a fresh isolated workspace from the original baseline under the same $2 budget. No successor has been dispatched: the current provisioner creates fresh budget volumes, so shared live budget and isolated workspace have not been established.
- The guarded ledger now durably records fence intent before stopping the bound worker, rejects late completion after fencing, and records confirmed stop separately. A cross-process race test verifies completion and fence serialize; the disposable Docker probe verified a delayed write stopped and a late completion was rejected. This does not establish prior tool effects or safe automatic resumption.
- A fenced source can reserve one successor identity bound to the original allocation, proxy and budget/anchor volume metadata. The successor remains `restart_blocked`, with normal provisioning and completion refused until shared live budget, workspace fencing and other runtime controls are proven; no unblock or dispatch path exists.
- Bound coordinator execution now requires a proxy-firewall configuration before reservation; allocation reinspection also rejects a restarted worker container by its saved PID/start identity. A disposable Docker test rejected worker-container restart and denied direct network/DNS. This does not detect an Agent Server child restarting inside an unchanged container or establish trusted image provenance.
- Ollama token-credit billing and public prices do not establish a $2 actual-charge cap through the current mutable gateway. Real provider routing, retry/fallback charges, independently durable budget reuse, ingress authorization and end-to-end Restate/OpenHands integration remain open. Production `run_task` remains blocked.
- Final verification: 199 Python tests and 16 Node tests passed; the TypeScript build passed. Disposable allocation-labeled resources were removed.

### Shared-budget successor prototype (Oct 27)

- Added experimental `worker/successor_runtime.py`: after a confirmed fence and one-shot durable claim, it provisions a networkless successor on the original proxy's read-only socket, rechecks the original stopped worker, proxy lifecycle, firewall and budget/anchor volume identities, and binds the new container before starting it. It requires the original task hash and a separate clean baseline workspace with standalone Git metadata and no hardlinked regular files. The successor remains `restart_blocked`; there is no model dispatch path.
- `worker/probes/successor_same_budget.py` exercised the path with disposable Docker resources: the old worker was fenced, the successor used a separate baseline clone and the same live proxy and ledger, a third dummy-key request returned 401 and advanced the ledger from two to three synthetic reservations, and duplicate successor provisioning was rejected. No real key, successful inference or actual-charge cap was tested. Probe resources were removed.
- Trusted image/startup-command provenance, exclusive workspace ownership, independently durable rollback authority, continuous lifecycle enforcement, real provider charges and Restate-to-OpenHands restart integration remain unverified. Production dispatch remains blocked.
- Verification: 204 Python tests and 16 Node tests passed; the TypeScript build passed.

### Provisioned real-server successor experiment (Oct 27)

- `worker/probes/successor_provisioned_agent.py` used the actual guarded provisioner, original proxy/ledger, fence and successor provisioner with two disposable pinned OpenHands servers. It fenced the first during an in-flight synthetic model request, started the second from a separate baseline clone, used OpenHandsAdapter and DockerTransport with conversation IDs matching the ledger, and observed the successor conversation finish. The four model requests consumed four synthetic $0.50 reservations in the same ledger. A duplicate successor using a second clean workspace failed at the durable one-shot claim; probe resources were removed.
- This probe deliberately sends through the adapter outside the production `restart_blocked` gate. The upstream and pricing were synthetic; no Restate dispatch, actual billing, successful tool work, committed revision, production image trust or independent rollback authority was verified. Production successor dispatch remains disabled.
- A separate local synthetic counterexample restored both the budget SQLite file and its same-host watermark after consuming the reservation limit; the store then accepted the same reservations again. This confirms simultaneous restore reopens capacity, and a separately trusted rollback-resistant authority is required for a production ceiling.
- Current local verification: 204 Python tests and 16 Node tests passed; TypeScript build passed. Phase 9 remains open.

### Authority and restart safeguards (Oct 27)

- Added an experimental reservation authority and opt-in proxy client. A separate authority serializes immutable attempt policies and reservations; source and successor can share one policy. In a process-level synthetic test, restarting the proxy and restoring its local state did not reopen the authority's reservation limit. Authority failure denies requests without falling back to the local ledger. Its own SQLite database can still be rolled back, and no independently protected authority, trusted remote transport or production provisioning is configured.
- Added an internal Restate restart handler with deterministic identities and atomic successor reservation. Known-unavailable preflight returns blocked without consuming that reservation; ambiguous outcomes after claiming it remain fenced. The preflight is deliberately disabled, the worker successor CLI remains blocked, and the route is not exposed through authenticated ingress. No Restate-driven successor has run.
- Restricted accepted Docker security options and validate volume identity and driver options before a privileged initialization helper mounts or changes them. Current-code disposable probes again verified gateway-only network controls with a dummy-key 401 response and a fenced real OpenHands-server successor with synthetic shared reservations. These do not prove trusted images, continuous protection against host changes, real billing, or production dispatch.
- The $2 actual-charge ceiling remains unproved: reservations depend on a verified worst-case provider charge and route, and neither that bound nor independently rollback-protected authority storage is established. Production dispatch remains blocked; Phase 9 remains open.
- Verification: 223 Python tests and 18 Node tests passed; the TypeScript build passed. Probe-owned Docker resources were removed.

### Witness and successor observation groundwork (Oct 27)

- Added a witnessed budget-authority prototype. Its compare-and-swap witness covers policy, reservation and schema state and rejects a restored authority database, competing copies and schema tampering in tests. Witness advancement before database commit fails closed on ambiguous outcomes. The witness in those tests is in memory: an independently durable, authenticated, rollback-protected witness, trusted provisioning and clock have not been established. See `stapled-factory/worker/BUDGET_WITNESS.md`.
- Added successor-specific reconciliation for unknown outcomes, with deterministic identity and revision checks; it never redispatches on observation. `worker/probes/restate_synthetic_successor.py` exercised an isolated real Restate server using a temporary preflight override and synthetic worker; this is not an OpenHands, billing, ingress-security or production dispatch test. The actual preflight and successor worker remain blocked.
- Fresh workspace baseline checks now reject ignored as well as untracked files. The real-server successor Docker probe still finished on the original proxy and synthetic $2 ledger, with the original worker stopped and duplicate successor rejected; no provider charges were incurred. Probe-owned resources were removed.
- Read-only live inspection confirmed the shared gateway key has no per-attempt cap and that relay routing/configuration can change. Published Ollama prices and context windows only support conditional estimates; the synthetic $0.50 per-request reservation is not a safe general bound for DeepSeek Pro. Real charges, hidden calls, retries and immutable pricing/routing remain unproved, so production dispatch is not enabled.
- Verification: 228 Python tests and 20 Node tests passed; the TypeScript build passed. Phase 9 remains open.

### Joined Restate/OpenHands synthetic recovery probe (Oct 27)

- `worker/probes/restate_openhands_successor.py` drove an isolated Restate server through a test-only worker shim to a real provisioned OpenHands source. After a synthetic gateway request was observed in flight, the Restate restart triggered fencing the original container and provisioning a fresh successor against the same proxy and synthetic ledger. The successor used an actual terminal tool to write and commit a file; `worker/revision.py` verified the clean changed descendant and its contents. Duplicate restart was rejected. Five synthetic $0.40 reservations exhausted the shared $2 ledger; no real provider calls or charges occurred.
- This joins the control and container flow in a disposable test, not production integration: the probe modifies preflight only in a temporary build, bridges Restate and a separate probe store via a shim, and uses permissive test workspace permissions and a test-specific server working directory. Production dispatch, runtime trust, and actual-charge enforcement remain blocked and unverified.
- Verification: 228 Python tests, 20 Node tests and the TypeScript build passed.

### Coordinator and private workspace integration (Oct 27)

- Restate now invokes the worker's side-effect-free successor preflight instead of a hard-coded TypeScript result; the real worker reports blocked. A preflight is not an authorization at dispatch time because Restate may replay its journal.
- Added `execute_bound_successor`, an internal coordinator for fencing, reserving and provisioning one successor against the original proxy, claiming dispatch once, and reconciling its result without redispatch. The production CLI does not call it and remains blocked.
- The disposable real-container probe now calls that coordinator. A locally derived image adds an unprivileged account matching the host workspace owner and uses `/tmp` for runtime state. Its real Agent Server tool committed a file in a mode-0700 workspace; coordinator reconciliation returned the verified revision twice without redispatch. This shows local compatibility, not image provenance or trusted production provisioning.
- The combined isolated Restate probe also passed with the coordinator and original synthetic shared ledger. Its CLI readiness and invocation still use a temporary test shim and its gateway is synthetic; this does not establish production Restate-to-worker dispatch or actual charges.
- Fresh billing review found the shared Bifrost key has no per-attempt cap. Published Ollama prices do not establish a hard actual-charge bound, and a $0.50 reservation does not cover a general DeepSeek Pro request. The $2 ceiling and production trusted provisioning remain unverified; no paid inference was run.
- Verification: 230 Python tests and 21 Node tests passed; the TypeScript build passed. Phase 9 remains open.

### Worker bridge startup and authenticated restart route (Oct 27)

- Added `worker/Dockerfile.agent` based on the pinned Agent Server image and `worker/agent_entrypoint.py` to start the gateway bridge with the server and shut both down if either exits. The disposable worker probe no longer copies or starts the bridge through Docker exec. Both standalone successor modes and the combined isolated Restate probe passed with synthetic billing and the new image. Probe-owned resources were removed.
- The authenticated local ingress now permits `Reconciliation/{unit}/restart`; unauthenticated calls remain rejected. The handler's real worker preflight still blocks and the combined probe still substitutes a temporary CLI shim. The real CLI has no trusted recovery-context/configuration loader or verified charge authority and cannot dispatch the coordinator.
- Read-only live gateway inspection found the shared Bifrost deployment uses a mutable image and configuration, and its virtual key has no per-attempt cap. Bifrost's documented budget check permits an individual request to take usage past its threshold, and unknown or zero model-cost records do not prove Ollama charges. A $2 hard actual-charge limit is not verified; no paid inference or gateway changes were made.
- Verification: 231 Python tests and 21 Node tests passed; TypeScript build passed. Trusted image provenance, production provisioning, real CLI integration and the actual-charge ceiling remain open. Phase 9 remains open.

### Operator CLI and best-effort budget (Oct 27)

- User explicitly dropped the $2 hard actual-charge cap. The worker retains a $2 best-effort reservation target; estimates are not a billing guarantee. The dated Flash policy reserves $0.315802 per request using the published peak rate and a conservative context assumption.
- `worker.py` now invokes the real OpenHands coordinators using a private, operator-owned per-attempt configuration. `worker/OPERATOR_CLI.md` documents the trust boundary and configuration. The operator must trust the host, Docker daemon, image IDs and commands; the CLI does not attest provenance, and coordinated rollback of the local store and anchor remains possible.
- `worker/probes/restate_openhands_successor.py` passed using the real worker CLI, an isolated Restate server, real OpenHands containers, an owner-only workspace and a synthetic gateway. It fenced the in-flight original, reconciled a successor's actual committed revision, rejected duplicate restart and charged five synthetic reservations to one ledger. No Python CLI shim or paid request was used in that recovery probe.
- A separate `verify_firewalled_provision.py --live` probe used the authorized DSH credential in memory for one short real Flash request. The gateway returned a valid assistant response with usage through the firewalled proxy; public, metadata and DNS access were denied, and proxy restart was rejected on reinspection. Its $0.50 reservation is not a measured charge. The tests do not establish end-to-end live model-backed recovery or production image/daemon provenance.
- A disposable paid smoke invoked the real worker CLI and OpenHands against the actual Bifrost Flash route. Its initial result was unknown; read-only reconciliation returned a verified commit containing the requested file. Four model requests reserved $1.263208 under the best-effort ledger; actual provider charges were not measured. This live smoke did not involve Restate or a crash. Resources and temporary configuration were removed. `worker/probes/live_cli_smoke.py` reconstructs the smoke and requires explicit `--run-paid-smoke`; that saved script was not itself rerun with paid requests.
- Verification: 239 Python tests and 21 Node tests passed; TypeScript build passed. Phase 9 remains open pending the remaining integration and deployment gates.

### Live Restate recovery (Oct 27)

- `worker/probes/restate_live_recovery.py` exercised real Restate, worker CLI, OpenHands and Bifrost Flash with the authorized DSH credential. After observing a real upstream POST in flight, the test killed the source worker; Restate fenced it and started a fresh successor on the same proxy and budget ledger. The successor produced a verified commit and duplicate restart was rejected. Six requests reserved $1.894812; actual charges were not measured.
- Two earlier paid attempts each reserved $1.579010: the first staged a file but did not commit, and the second committed but did not finish before the fixture's five-request limit. The final run used the permitted six-request best-effort limit. Disposable resources were cleaned.
- The separate local provisioning probe denied direct worker public/metadata/DNS access and proxy public/metadata/wrong-port/IPv6/DNS access; it rejected proxy restart and duplicate allocation. This depends on trusted operator-selected images and Docker daemon; provenance and hostile-host resistance are not established.
- Verification: 239 Python tests and 21 Node tests passed; TypeScript build passed. Phase 9 remains open pending remaining plan gates.

### Wall acceptance reconciliation (Oct 27)

- Reconciled the original Phase 9 wall checklist against current code. Restored rate-limit and session-expiry scenarios, and verified a live session before testing logout invalidation. The evaluator now defaults to triple-run majority. Three negative controls disable logout, expiry or rate limiting individually and demonstrate rejection. All 17 wall tests passed.
- The current disposable provisioning probe independently denied actual host holdout/secret fixture access and budget/anchor/socket reads inside the worker container, with a successful workspace-read positive control. Existing simulated isolation tests are no longer the sole evidence for the wall isolation gate. This assumes an operator-trusted host, Docker daemon and selected images; it does not attest provenance or resist a compromised administrator.
- Original wall MVP hard gates now have local evidence in phase-9-plan.md. Broader Phase 9 completion must still be reconciled with the expanded integration scope and enterprise phase gates; this audit does not authorize production cutover or mark later phases complete.
- Strengthened the live probe's duplicate restart check to require unchanged successor evidence, exact container identity and shared reservation ledger. These added live assertions were compiled and reviewed but not rerun against the paid gateway. Six focused successor tests passed.
- Final verification: 242 Python tests and 21 Node tests passed; TypeScript build passed. The original wall MVP phase row is now complete for local gates; expanded production/enterprise gates remain separate.

### Live duplicate rerun and enterprise assessment (Oct 27)

- Strengthened paid live recovery rerun did not pass: source fenced, successor started and staged expected file, but no verified commit within six reservations ($1.894812 estimate). The run was stopped and its resources cleaned; duplicate invariants were not reached. Actual charges unknown. No budget increase or further paid rerun.
- Formal enterprise assessment is D (40.9/100), below the required A/B gate. Remaining High gaps: revision-to-validation-target binding, actual OpenHands tool mediation, reviewed image provenance, coherent control-plane backup/restore and reviewed release/CI controls. See enterprise-assessment.md; production gate remains blocked.
- Fixed spine evaluator integration overriding triple-run quorum with one run; added subprocess-argument regression test. Corrected security documentation claiming standalone broker/firewall primitives mediated OpenHands terminal tools.
- Verification: 242 Python tests and 22 Node tests passed; TypeScript build passed. No production or enterprise completion claimed.

### Recovery remediation and High finding containment (Oct 27)

- Fixed synthetic gateway race: proxy-global tool marker could be set by late source response, making successor skip tool work. Fixture now uses tool-result conversation history. Raised fixture output allowance from 512 to supported 1024 and requests atomic create/stage/commit with explicit Git identity. Synthetic real-CLI recovery passed.
- One paid live recovery rerun passed: in-flight original killed/fenced, successor committed verified result, strengthened duplicate checks preserved successor record, exact container ID and shared ledger. Five reservations totaled $1.579010 estimated; actual charges unmeasured. Probe exit 0 and disposable resources cleaned.
- Operator CLI explicitly rejects production/unknown deployment scopes before loading credentials or dispatch; default sandbox only. This contains use but does not close tool mediation or provenance findings. Added validation binding and coherent restore acceptance runbooks (VALIDATION_BINDING.md, RECOVERY_OPERATIONS.md); actual production target and whole-control-plane restore drill remain absent.
- Added GitHub verification workflow for Python tests and locked npm install/build/Node tests. Local equivalent passed (246 Python, 22 Node); hosted CI, reviewed release and branch protection remain unverified. ENT-01 through ENT-05 remain open pending their production evidence; enterprise grade is not raised by documentation or containment.

### Executable High remediation and Railway target (Oct 27)

- Local immutable validation adapter built exact Git archives and checked actual container/image identities before and after evaluation. Good target passed, broken wall target failed, and secret-bearing source failed security despite passing wall. Production provider binding and serialized Restate acceptance remain open.
- Cold restore drill now preserves actual non-empty Restate WorkUnit completed/unresolved state, rejects new claims, and rejects three altered external fixture components. Actual external stores and independently protected recovery generation remain unverified.
- Signed operator image approval is enforced when an independent trust root is configured, with real OpenSSL and substitution/expiry negative controls. This is operator approval, not upstream publisher provenance; sandbox production-denial remains and real tool mediation is not implemented.
- Final full local suite: 247 Python tests, 22 Node tests and TypeScript build passed; npm audit zero known vulnerabilities. Staged source snapshot passed Gitleaks. Local baseline `9cfad9e` and corrective fixture commit `56f3495` exist. Fresh clone exposed globally ignored wall/target fixtures; ignore rule fixed, then all 247 Python tests passed in fresh clone. Locked npm registry install, build and 22 Node tests also passed in fresh clone (offline cache lacked one package). No GitHub publication or hosted CI run.
- Formal enterprise reassessment D 44.0/100, BLOCKED; High findings remain partial/open. User configured GitHub origin and chose a new Railway project for runtime. Existing CLI account login was masked by stale token overrides; omitting them restored access. Registered SSH key verified authenticated remote command execution without exposing/copying it.
- Created dedicated Railway project and private persistent Restate 1.8.0 control service. Latest deployment `952d7e5c-add6-4049-b619-7aeec0d0d943` SUCCESS; actual remote version, empty deployments API, ext4 /data mount and service health/restart settings verified. No public domain, model credential, worker sandbox or factory workflow configured. Full IDs and architecture gaps are in railway-target.md; this is not enterprise readiness or full factory deployment.

### Local CI publication and corrected NUC target (Oct 27)

- User requires local builds and upload, with no GitHub runners. Removed the GitHub workflow and added isolated-clone local CI with hashed logs/receipt. Baseline `252f0bd` passed 248 Python and 22 Node tests, TypeScript build, npm audit and Gitleaks, then was pushed. Follow-up `19b6a9a` passed and was pushed with a local CI success status.
- Restore drill now uses real guarded attempt and monetary reservation stores with non-empty Restate state. Preserved completed/unknown attempt behavior and budget usage; actual store rejects rollback behind its current anchor. Independent protected backup remains open.
- Actual authenticated provider readback rejects active/latest replacement, stopped instances, unknown artifacts and ambiguous domains. It does not enable acceptance without a trusted service boundary. Upstream signature discovery found no supply-chain artifacts for pinned bases; no provenance claim made.
- User corrected runtime deployment target from Railway to the local NUC. Stop Railway deployment; clean disposable sandboxes while preserving persistent service/data. NUC Docker works, uid1000/amd64, 34GiB free at preflight. Shared Bifrost and other workloads remain untouched. Locally built amd64 images are being transferred for isolated verification.
- NUC dedicated control plane is now installed/enabled: loopback Restate admin19070/raw18080, signed endpoint19080, bearer ingress19081. Signed registration/state read passed; missing bearer and unsigned endpoint returned401; forbidden ingress claim404. Actual persisted WorkUnit state survived a dedicated Restate restart. This proves control-plane deployment; per-attempt worker runtime activation remains separate.
- NUC live recovery passed through actual Restate/CLI/OpenHands/Bifrost. Source fenced while a real request was in flight; successor committed; duplicate requests left exact state/container identities/ledger unchanged. Five requests reserved $1.579010 estimated, with actual charges unknown. Actual worker host paths/stores/socket and direct TCP/DNS were denied; workspace writes permitted. Root-directory write denial is permissions-based: the container filesystem is not globally read-only.
- Unpaid proxy checks on the NUC denied public443, metadata80, gateway80, IPv6 and DNSUDP53; allowed only the pinned gateway TLS443 with certificate/hostname validation. Firewall rules were checked before and after. No additional model request was used for these checks.
- Exact joined cold restore passed off-host: 121 authenticated files, real Restate WorkUnit needs_validation, completed guarded-store result and GitHEAD all match; budget/anchor identity and unchanged shared ledger checked. Older signed backups are not an anti-rollback authority; manual quarantine/fencing remains mandatory.
- GitHub main now requires local-ci/verified including administrators; force pushes and deletion disabled. No GitHub runner or provider-side source build is used. Independent human review is not claimed.
- Trusted candidate gate now verified on the NUC: exact archive/diff derived from Git and bound to an operator-signed local build; actual immutable image checked. Network-none candidates passed good15/15, failed broken0/15, and rejected secret source despite wall15/15. Archive/diff/signature/image substitutions rejected before execution. Configured security engines are explicitly regex fallbacks, not full external SAST.
- Actual validation CLI evaluated, signed and accepted through private Restate; stale identity, tamper, failed verdict, replay and public-route attempts rejected. Release9f40ca5→f5f18a4→9f40ca5 rollback preserved validated state without restoring Restate data. Runtime endpoint uses frozen9f40ca5 with764 matching file hashes; per-attempt worker configuration remains unset.
- Independent technical review found an image-label/archive/diff substitution gap; signed local build provenance and commit-derived inputs fixed it and actual negative controls passed. Formal reassessment D50.7/100 BLOCKED: ENT01/02/04 resolved only within tested single-operator scope; upstream provenance ENT03 and enterprise release approval/governance ENT05 remain open. Main now also requires an independent approval of the latest push and dismisses stale reviews; human approval is not inferred from agent review.
- Final review removed the NUC signer CLI's unattested local-build fallback. Required prebuilt/source/diff/build signature arguments now reject legacy invocation before key/Docker access. Hardened release3e15fb4 is frozen/active (768 matching files), and the actual hardened CLI still evaluated/signed/accepted successfully. Exact deployed3e15fb4 passed local CI:260 Python tests,23 Node tests, TypeScript build, locked npm audit and Gitleaks.
- Stopped the unused Railway compute deployment after target correction. Provider readback confirmed zero active deployments; persistent volume READY/no pending deletion. Dedicated NUC services remain active; unrelated workloads untouched.
