# Architecture — Stapled-Together Software Factory (Path A)

## 1. Overview

A **dark software factory**: AI agents take work from intent to production, gated only by a small set of
human approvals. Built by **stitching** open-source components (Path A) — reusing what's already on this
machine — and writing only the glue: the **wall**, the **qualifier**, and the **gates**.

**Scope of "dark" (tiered):** non-security-relevant diffs run fully dark (no human reads them).
Security-relevant diffs — auth, crypto, permissions, data-boundary — pass the deterministic security
pipeline **and** a human escalation before merge. The qualifier routes by security-relevance
deterministically.

## 2. Component map (the stitch)

| Layer | Component | Stitched from | Why |
|---|---|---|---|
| Model routing | **Bifrost** gateway | `/Users/bo/code/reference/nanoclaw/src` (MIT) | one Anthropic/OpenAI-compatible surface; tiered routing; already running |
| Worker agent | **OpenHands (CodeAct v3)** | self-hosted, MIT | auditability + model-agnostic; strongest OSS agent |
| Orchestration | **Restate** | single Rust binary | durable replay + Virtual Objects for claims/leases/receipts; kill/pause for live agents |
| Execution isolation | **NUC/M1 build-routing** | `plugins/m1-build-routing/` (m1_worker.py, deploy.py) | existing job-ID/snapshot/receipt/slot isolation boundary |
| Issue intake | **GitHub issues → webhook** | — | intent lands as issues; webhook feeds the qualifier |
| Eval (the wall) | **custom holdout evaluator** | build (Python or Rust) | the part no platform ships — build first |
| Cascade (Inception) | **drafting loop + hybrid proxy** | OpenHands/cheap model + LLM judge via Bifrost | 3yr-goal-and-down decomposition |
| Gates | **budget/destructive/prod-deploy/credential** | build | human-approval mechanisms |

## 3. Data flow

```
Human control plane (you):  3yr goals  (stays human — "intent thinking")
      │  (structured, machine-derivable intent)
      ▼
 Inception (decomposition)  ──── yearly OKR → quarterly OKRs  [YOU approve each]
      │                        → monthly epics  [hybrid proxy: deterministic gate + LLM judge]
      │                        → work packages → issues → specs  [dark]
      ▼
 Qualifier  ──── scores each issue (risk + security-relevance, deterministic): dark-lane vs human-lane
      │
      ▼  (dark lane)
 Restate  ──── durable state: claims / leases / stages / receipts (Virtual Objects + event log)
      │ dispatches
      ▼
 OpenHands worker (CodeAct)  ──── in NUC/M1 build-routing sandbox
      │  models via Bifrost (tiered: DeepSeek/GLM-5 cheap, Claude frontier hard)
      ▼  produces diff
 Wall (holdout evaluator)  ──── ISOLATED: generator never sees the scenarios
      │  per-scenario quorum + ≥90% aggregate, opaque one-line failure → loop back
      ▼
 Deterministic security pipeline  ──── SAST / secret scan / dependency+provenance (non-LLM)
      │  security-relevant diff? ── yes → human escalation before merge (tiered dark)
      │                          ── no  → dark merge
      ▼
 Gates: budget / destructive-op / initial-prod-deploy / credentials (human)
      ▼
 Merge & deploy (existing CI/CD)
```

## 4. The wall (load-bearing isolation)

The wall is the single most important element. Its invariant: **the generator and the validator never
share the same model, prompt, or scenario text.** It is a **quality / non-determinism gate, not a
security boundary** — it catches sycophancy and flaky output, not backdoors, auth flaws, or malicious
dependencies (those need the deterministic security pipeline in §11.3).

1. **Holdout scenarios** are plain-English BDD tests stored where the OpenHands worker's sandbox has **no
   mount/network path** (separate storage, separate credentials). This is a **provable property**: a test
   asserts the worker cannot reach the scenarios, rather than relying on a config we believe.
2. **Single-run** per scenario for the wall MVP and all non-prod tasks; **triple-run (2-of-3)** only at the
   final prod gate. (Triple-run is quota/time overhead that isn't justified outside the one gate that matters.)
3. Gate = **per-scenario 2-of-3 AND ≥90% aggregate** — never aggregate-only (an aggregate can pass while an
   individual scenario sits at 1-of-3).
4. On failure, the generator receives a **one-line** message (e.g. `"Login failure: endpoint returned 500"`),
   never the scenario text — so it cannot game the test.
5. Every wall result carries a **usage receipt** (tokens / requests / wall-clock / model tier per stage) —
   for runaway detection and quota forecasting, not dollar accounting (see §10).

This mirrors StrongDM/Pulumi's pattern from the report.

## 5. The cascade rule (intent decomposition)

The human authors only the **3yr goal**; everything below is drafted. Two approval kinds:

- **Human approval** — yearly OKRs and quarterly OKRs (the strategic "what to build" boundary).
- **Hybrid proxy approval** — monthly epics, via a two-layer gate that runs **deterministic-first**:
  1. **Deterministic policy gate** (hard floor, ungamable, the *sole enforcement point*): the epic is within
     an approved quarterly OKR's scope, non-destructive, within budget, and touches no privileged/regulated
     data boundary. On failure it **short-circuits** — the LLM judge is not called.
  2. **LLM judge** (via Bifrost): a semantic check that the epic genuinely serves the OKR's key results.
     **Advisory only** — it emits a recorded signal (annotation + recommendation); it never blocks or
     permits on its own. Only a deterministic rule acts on its output.

Below epics runs dark. Two isolation concerns: (a) the judge must **not** be the same model/config that
drafted the epic (anti-sycophancy, same logic as the wall); (b) every gate result and judge verdict is
logged (auditability by design).

## 6. Restate service shape (control-plane API surface)

The factory's control plane is a set of Restate services/handlers:

- **`cascade`** — Virtual Object keyed by node id (Goal/OKR/Epic/…); `draftNextLevel`, `approve` (human),
  `proxyApprove` (deterministic gate + LLM judge), each writing an append-only `ApprovalGate` record.
- **`orchestrate`** — the workflow: claim task → dispatch OpenHands → validate → gate → merge; with
  `Claim`/`Lease`/`Stage`/`Receipt` Virtual Objects.
- **`qualify`** — score an incoming GitHub issue; route dark-lane vs human-lane.
- **`evaluate`** — the wall; runs holdout scenarios (as Restate child invocations), returns opaque verdict.
- **`gate`** — budget / destructive / prod-deploy / credential approval; durable timers + signals for
  human-in-the-loop.

Non-deterministic work (the OpenHands run, the eval execution, network calls) is wrapped as `ctx.run`
effect calls / child invocations, per Restate's determinism model.

## 7. Isolation & security boundaries

- One **disposable worktree** per task; no host mounts; deny-by-default network with allowlist.
- **Credential broker** issues short-lived, task-scoped, single-repo, single-action tokens; the agent never
  sees the secret value. Dedicated agent secret set distinct from dev/CI secrets.
- **Agent branch + draft PR only**; no direct merge/deploy authority. `block-no-verify` refuses `--no-verify`.
- Restate **Operations** (pause/resume/cancel/kill) is the kill-switch for runaway invocations.

## 8. Human gates (the "mostly lights-out" posture)

1. **Initial production deployment** — first promotion requires human sign-off of the *promotion decision*
   (`promotion.yaml` gated on `verification.yaml` passing + human approval); later drift runs dark.
2. **Budget decisions** — agents spend within an envelope; only a human raises it (hard cap + spend ledger).
3. **Credentials** — privileged/destructive class needs human approval.
4. **Cascade approvals** — human approves yearly + quarterly; hybrid proxy approves epics.

## 9. Deployment (local)

- Restate: single binary on the Mac control plane.
- Postgres: from the fullstack template (control-plane relational state + dashboard).
- Workers: NUC/M1 build-routing (existing infra).
- Bifrost: local gateway for tiered model routing.
- Dashboard: React (fullstack template) for mission/approval visibility.

## 10. Cost model — dual pricing mode (per-token OR subscription)

The factory supports **two pricing modes**, configured per model/tier (a `pricing_mode` field on the
Bifrost/tier config), so the same system runs against flat subscriptions or metered APIs — or a mix —
without a code change.

| Mode | `pricing_mode` | Marginal cost | Ceilings enforced | Receipt carries |
|------|----------------|---------------|-------------------|-----------------|
| Subscription | `subscription` | $0 | quota/time: `max_tokens`, `max_retries`, `max_wall_clock`, optional `max_requests` | usage only (tokens / requests / wall-clock / tier) |
| Metered | `metered` | per-token | quota/time **plus** `max_cost` (dollars) | usage **plus** `cost_estimate` |

- **Always recorded (both modes):** a per-run usage record — `tokens_in`, `tokens_out`, `requests`,
  `wall_clock`, `model`, `tier`, `pricing_mode`. This is the substrate for runaway detection and quota
  forecasting regardless of how you're billed.
- **Metered mode adds:** a hard `max_cost` ceiling enforced by Restate before dispatch, and a
  `cost_estimate` on the receipt, so the budget gate reasons in dollars where dollars are real.
- **The budget gate** is expressed in the mode's unit: a dollar envelope for metered tiers, a quota/time
  envelope for subscription tiers. Either way the ceiling is enforced by the orchestrator, not a human knob.
- **Switching modes** is a config change (`z.ai` subscription → its API key as `metered`), not a code path
  fork — both modes share the same usage ledger.

## 11. Consensus deltas (from adversarial debate)

### 11.1 Systems semantics (sequencing)
- **Effect boundary first, receipt schema second.** The receipt/claim schema is a one-way door; commit it
  only after the Restate effect boundary (what's durable vs replayable) is confirmed. Derive the schema from it.
- **Replay-vs-checkpoint split.** Control-plane state (stage/claim/lease/verdict) is replayable; data-plane
  state (LLM call results, worktree snapshots) is checkpointed externally. Never replay an LLM call.
- **Schema-versioned seams.** The four component boundaries (Bifrost ↔ OpenHands ↔ Restate ↔ sandbox) carry
  explicit versioned contracts, not implicit coupling.
- **Monotonic dark-mode authority.** Lane changes (dark → human-gated) are explicit, logged transitions —
  never silent config.

### 11.2 Kill-switch recovery
- Kill-switch latency is a defined numeric budget (not "undefined").
- On kill, dangling leases and worktrees are **compensated** (released/cleaned), so a kill doesn't leak
  resources or leave a half-held credential.

### 11.3 Deterministic security pipeline (later phase — hard prerequisite to prod)
A deterministic (non-LLM) pipeline is required before security-relevant code reaches prod, as a separate
boundary from the wall: SAST, detection of leaked values, dependency/vulnerability scanning, build provenance.
**Prompt-injection mitigation** (instruction/data separation, intent-scoped tool authorization) is a
prerequisite to any agent holding credentials.

## 12. Decisions resolved (formerly open)

1. **Scope of "dark"** — **RESOLVED: tiered dark.** Non-security-relevant diffs run fully dark (wall +
   deterministic pipeline, no human). Security-relevant diffs (auth, crypto, permissions, data-boundary)
   get the deterministic pipeline **plus a human escalation** before merge. The qualifier routes by
   security-relevance deterministically (not an LLM). Commits to building the deterministic security
   pipeline (Phase 9c).
2. **Credential-broker trust anchor** — **RESOLVED:** `FACTORY_MASTER_KEY` env, outside the agent loop;
   swappable for a secret store / KMS via `broker.py`. (`security/broker.py`)
3. **Destructive-op gating** — **RESOLVED:** effect-based infra deny. `firewall.py` classifies operations
   by effect (allow/approve/block, deny-by-default), not intent; `approve` routes to the human
   `destructive_op` gate. (`security/firewall.py`)

## 13. Open architectural questions (non-blocking)

- Restate `ctx.run` effect boundaries for the agent run + eval — confirm before committing the receipt schema.
- DeepSeek vs GLM-5 as the default "cheap" model (Phase 6).
- Dashboard: React vs lighter CLI/TUI (Phase 7).

## Existing complexity router integration — October28

User explicitly selected the existing NUC router. The concise ModeB finance/security/implementation review and current evidence are recorded in decisions/2026-10-28-existing-complexity-router.md. Use gatewayaliasauto through the same confined proxy; do not duplicate or modify the shared classifier. Dynamic body-sized Pro-rate estimates replace Flash-only reservations for this route, with classifier/fallback charge uncertainty retained. Fixed-model policy compatibility, signed unit bindings and gateway-only egress remain.
