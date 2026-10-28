# Consensus Plan — Stapled-Together Dark Software Factory (Path A)

> Produced by adversarial debate on 2026-10-13.
> Source plan: `architecture.md`
> Personas: Security Architect, Systems Architect, FinOps Engineer
> Rounds: 1 · Resolution: 3 resolved, 3 NEEDS_HUMAN_DECISION

This file records **deltas** to the original plan, not a rewrite. Where a delta conflicts with `architecture.md`, the delta wins.

---

## Key Decisions

| Decision | Resolution | Rationale |
|----------|-----------|-----------|
| Triple-run evaluator | 3× only at final prod gate; single-run elsewhere | FinOps: permanent 3× on the most expensive step is unjustified outside the one gate that matters. |
| "2-of-3 AND ≥90%" gate | Per-scenario quorum, not aggregate-only | Systems: aggregate can pass while a scenario is 1-of-3. |
| LLM judge | Advisory-only; deterministic is enforcement | Security: no LLM may be the enforcement point. Preserves user's hybrid intent. |
| Hybrid proxy ordering | Deterministic short-circuits before LLM judge | FinOps: avoids double-spend; Security: deterministic must act first. |
| Wall MVP scope | Security pipeline deferred to later phases | Security's pipeline is prerequisite to prod, not to the wall MVP. |
| "No human reviews the diff" | NEEDS_HUMAN_DECISION | Scope of "dark" unresolved. |
| Credential broker trust anchor | NEEDS_HUMAN_DECISION | Root of trust unspecified. |
| Destructive-op intent vs effect | NEEDS_HUMAN_DECISION | Enforcement level unresolved. |

---

## Deltas to the Original Plan

### 1. The wall (holdout evaluator) — correction of scope and gate

**What changes:** The wall is renamed conceptually from "security boundary" to "quality/non-determinism evaluator." It detects sycophancy and non-determinism only. It is **not** a security check — it does not catch backdoors, auth flaws, SQLi/XSS, secret leakage, or malicious dependencies.

- **Gate semantics (bug fix):** `pass = per-scenario 2-of-3 AND aggregate ≥90%`. Never aggregate-only 2-of-3.
- **Run count:** single-run for the wall MVP and all non-prod tasks. 3× applies only at the final prod gate.
- **Spend receipt:** every wall result carries a spend receipt — tokens and $ per stage — as a first-class field on the evaluation record, feeding the cost feedback loop (see §4).
- **Isolation as a provable property:** holdout storage separation is implemented with a test that asserts inaccessibility from the agent sandbox — not a config we "believe."

### 2. The hybrid proxy (monthly-epic gate) — LLM judge demoted to advisory

**What changes:** The deterministic/LLM hybrid is preserved, but the LLM judge is **advisory-only**.

- **Ordering:** deterministic gate runs first and short-circuits. Fail → no LLM call. Pass → LLM judge runs.
- **Authority:** the LLM judge emits an advisory signal (annotations, confidence, recorded recommendation). It never blocks or permits on its own.
- **Acting on the signal:** only a deterministic rule may act on the LLM judge's output. For security or destructive-op decisions, only deterministic enforcement applies — never an LLM.

### 3. Security boundary — new, deterministic, deferred to later phases

**What changes (added, not replacing the wall):** A deterministic security pipeline is a hard prerequisite to any security-relevant code reaching prod. It is a **later phase**, not part of the wall MVP.

Pipeline components (each deterministic): SAST, detection of leaked values, dependency/vulnerability scanning, build provenance. None of these are LLMs.

- **Prompt-injection mitigation is a prerequisite** to any agent holding credentials (added hard gate before the credential broker is exercised in anger).

### 4. Cost control — hard ceilings, not knobs

**What changes (added):** The budget gate is demoted from "human knob" to "machine governor."

- **Hard per-run ceiling** enforced by the orchestrator (Restate): `max_tokens`, `max_retries`, `max_cost`. Non-negotiable.
- **Failure-path cost feedback loop:** a failed run records its spend and feeds it back, so retries are bounded by actual cost, not policy.
- **Cascade drafting cap:** the recursive cascade cannot draft unbounded levels before a human sees a number — a human-approved budget is required before any decomposition below the monthly-epic level drafts work packages/issues/specs.
- **Tiering:** the cheapest model tier is forced for non-prod/evaluator tasks; higher tiers are permitted only at the final prod gate.

### 5. Systems semantics — sequencing and state discipline

**What changes (added, sequencing):**

- **Effect boundary first, receipt schema second.** The receipt/claim schema is a one-way door; do not commit it until the effect boundary (what an effect is, when it is durable, when it is replayable) is confirmed. Derive the schema from the confirmed boundary.
- **Replay-vs-checkpoint split:** distinguish control-plane state (replayable — decisions, transitions, receipts) from data-plane state (checkpointed — LLM call results, worktree snapshots). Never attempt to replay an LLM call.
- **Schema-versioned component APIs:** the four component seams (Bifrost ↔ OpenHands ↔ Restate ↔ sandbox) get an explicit compatibility contract, so version coupling is not implicit.
- **Monotonic dark-mode authority:** lane changes (dark → human-gated) are explicit, logged transitions — never silent config.

### 6. Kill-switch — latency and recovery

**What changes (added):**

- **Latency bound:** kill-switch latency is defined (a numeric budget), not "undefined."
- **Recovery/compensation path:** on kill, dangling leases and worktrees are compensated (released/cleaned), so a kill does not leak resources or leave a half-held credential.

---

## Unresolved Items (NEEDS_HUMAN_DECISION)

These block finalization and require the user to decide; the debate could not resolve them because they are product-intent or secret-policy decisions, not architecture.

1. **Scope of "dark."** Does "no human reviews the diff" mean (a) no human reviews *anything* (Security: non-viable), or (b) no human reviews non-security-relevant diffs, while security-relevant paths get the deterministic pipeline plus an optional human escalation? **Gates whether the plan is coherent at all.**

2. **Credential-broker trust anchor.** The broker mints short-lived task-scoped tokens, but the root of trust (the long-lived key/identity that must never enter the agent loop) is unspecified. Who holds it, how it rotates, where it lives.

3. **Destructive-op gating: intent vs effect.** Is the factory allowed to gate on *intent* classification, or must it gate on *effect* (infra-level deny at the sandbox/network boundary)? A risk-appetite decision.

---

## Dissenting Opinions

- **Security (minority):** prefers 3× evaluation for the wall MVP as well, arguing single-run forfeits the one non-human quality check early in the build. Overridden on cost grounds; its core concern is covered by the per-scenario quorum at the final prod gate.
- **Security (not overridden):** holds that no deterministic pipeline fully replaces a human on security-critical paths — recorded as NEEDS_HUMAN_DECISION #1, not dismissed.
