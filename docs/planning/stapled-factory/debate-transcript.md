# Debate Transcript — Stapled-Together Dark Software Factory (Path A)

- **Date:** 2026-10-13
- **Source plan:** `docs/planning/stapled-factory/architecture.md`
- **Personas:** Principal Application Security Architect, Principal Systems Architect, FinOps / Cloud Cost Optimization Engineer
- **Rounds:** 1 (position papers only — positions converged sufficiently that further rounds added no new information)
- **Resolution:** 3 contested decisions resolved, 3 items flagged NEEDS_HUMAN_DECISION

---

## Persona Position-Paper Summaries

### Principal Application Security Architect
**Executive:** Flawed at its security core — a sycophancy detector (the wall) and an LLM judge are being asked to stand in for a human security review, while prompt injection is entirely unmodeled.

**Strengths acknowledged:** credential broker (short-lived task-scoped tokens, agent never sees secret); Restate durable replay for audit/kill; correct isolation instincts (disposable worktree, deny-by-default net, draft-PR-only, block-no-verify); holdout storage separation.

**Top concerns:** (1) prompt injection unmodeled — agent parses untrusted GitHub/webhook content while holding credentials; (2) the wall is a sycophancy check, not a security check — misses backdoors, auth flaws, SQLi/XSS, secret leakage, malicious deps; (3) LLM judge as enforcement is bypassable; (4) destructive-op gating classifies intent, not effect; (5) credential broker trust anchor unspecified; (6) supply chain ungoverned; (7) kill-switch latency undefined.

**Hard stances:**
- No LLM (judge or otherwise) may be the enforcement point for security or destructive-op decisions.
- The wall must not be the security boundary; "no human reviews the diff" is unacceptable for security-relevant code — a deterministic security pipeline (SAST / secret / dependency / provenance) is required.
- Prompt-injection mitigation is a prerequisite to any agent holding credentials.

### Principal Systems Architect
**Executive:** Risky but directionally coherent — correct to make Restate the load-bearing boundary, but under-specifies effect/replay semantics, the receipt schema, and state-transition discipline.

**Strengths acknowledged:** correct placement of the durability boundary; Restate is the right substrate; cascade authority maps to blast radius correctly; isolation is concrete; scope discipline (only write the glue).

**Top concerns:** (1) receipt/claim schema coupled to an unresolved effect boundary — fix the effect boundary first, derive the schema; (2) replay-vs-checkpoint model unspecified (control-plane vs data-plane state) — never replay an LLM call; (3) component seams 4-way version-coupled with no compatibility contract (need schema-versioned APIs); (4) cascade decomposition is a DAG with unhandled partial-failure/retry semantics; (5) deny-by-default network vs LLM-judge-via-Bifrost tension unproven; (6) kill-switch has no recovery/compensation path (dangling lease/worktree); (7) "2-of-3 AND ≥90%" has an interaction bug — aggregate can pass while a scenario is 1-of-3.

**Hard stances:**
- Receipt schema must not be committed until the effect boundary is confirmed (one-way door).
- Holdout isolation must be a provable property with a test asserting inaccessibility — not a config we believe.
- Dark-mode authority must be monotonic and non-reversible — lane changes are explicit logged transitions, never silent config.

### FinOps / Cloud Cost Optimization Engineer (contrarian)
**Executive:** High-risk bet with inverted risk-reward — every layer multiplies spend; no hard cost control exists.

**Strengths acknowledged:** self-hosted worker + local gateway preserve cost optionality; isolation limits retries; Restate durable replay is a free spend ledger; human approval at the cascade top is the sane gate position.

**Top concerns:** (1) triple-run evaluator = permanent 3× on the most expensive step; (2) no hard per-run spend ceiling anywhere (budget gate is a human knob, not a machine constraint); (3) recursive cascade can draft unbounded levels before a human sees a number; (4) tiering unspecified — cheapest tier never forced; (5) hybrid proxy double-spends (deterministic should short-circuit before LLM judge); (6) failure path has no cost feedback loop; (7) budget gate is a knob, not a governor.

**Hard stances:**
- Hard per-run cost ceiling (max_tokens / max_retries / max_cost) enforced by the orchestrator is non-negotiable.
- Triple-run reduced to single-run for non-prod tasks; keep 3× only at the final prod gate.
- Every wall result carries a spend receipt (tokens/$ per stage).

---

## Consensus Points (agreed by ≥2 personas)

| # | Point | Who |
|---|-------|-----|
| 1 | Restate durable replay is the correct load-bearing durability boundary (audit/kill ledger, correct substrate, free spend ledger). | All 3 |
| 2 | Credential broker with short-lived, task-scoped tokens; agent never sees the secret. | All 3 (FinOps via isolation limits) |
| 3 | Isolation instincts are correct: disposable worktree, deny-by-default network, draft-PR-only, block-no-verify. | All 3 |
| 4 | Holdout storage must be separated from agent-writable state. | Security, Systems |
| 5 | The wall (holdout evaluator) is **not** a security boundary — it detects sycophancy/non-determinism only. | Security, Systems |
| 6 | Human approval at the cascade top is the correct gate position. | Security, FinOps |
| 7 | Kill-switch is required but currently underspecified — latency undefined (Security) and no recovery/compensation path (Systems). | Security, Systems |
| 8 | Prompt injection is entirely unmodeled and is a prerequisite concern. | All 3 (uncontested) |
| 9 | Budget gate as currently specified is a human knob, not a machine-enforced governor. | FinOps (uncontested by others) |

---

## Contested Decisions and Resolution

### 1. Triple-run evaluator: 3× always vs single-run for non-prod
- **Position A (Security):** Keep 3× (2-of-3, ≥90%) — it is the only non-human quality check before a no-review merge.
- **Position B (FinOps):** Single-run for non-prod; 3× only at the final prod gate — 3× on the most expensive step is a permanent 3× tax.
- **Position C (Systems, corrective):** The stated gate "2-of-3 AND ≥90%" is buggy at the aggregate level — it can pass while an individual scenario is 1-of-3, defeating the point of the quorum.

**RESOLVED — MAJORITY (FinOps + Systems; Security retains a minority dissent on the wall MVP only).**
- Keep 3× **only at the final prod gate** — the single place it earns its keep (catching non-determinism and near-misses immediately before a no-human merge).
- All non-prod tasks, including the wall MVP itself, run **single-run**.
- Fix the interaction bug: the gate is **per-scenario 2-of-3 AND aggregate ≥90%**, never aggregate-only.
- Security's dissent is noted and does not change the wall MVP: its "3× always" concern is about quality regression risk, which the per-scenario quorum at the prod gate still covers where it matters.

### 2. LLM judge: enforcement vs advisory-only
- **Position A (Security):** LLM judge (and any LLM) must be advisory-only; never the enforcement point for security/destructive-op decisions.
- **Position B (User intent):** explicitly wanted a "deterministic + LLM" hybrid proxy at the monthly-epic gate.
- **Position C (FinOps):** deterministic gate must short-circuit **before** the LLM judge to avoid double-spend.

**RESOLVED — All three, compatible with user intent.**
- The hybrid proxy is preserved as the user asked: it runs **both** a deterministic gate and an LLM judge.
- **Ordering:** deterministic gate runs first and short-circuits (fail → no LLM call, spend saved; pass → LLM judge runs).
- **Authority:** the LLM judge produces an **advisory signal** (annotations, confidence, a recorded recommendation). It never blocks or permits on its own. A deterministic rule (not an LLM) acts on the signal; for security/destructive-op decisions, only deterministic enforcement applies.
- This is the user's hybrid, made safe: deterministic is the enforcement layer, LLM is the evidence layer.

### 3. The wall MVP scope (which concerns bind the MVP vs later phases)
- **Uncontested on the split principle;** the three papers disagree only implicitly on sequencing.

**RESOLVED — the wall MVP binds only these deltas; the rest are later-phase.**
- **Wall MVP (built first — the holdout evaluator only):**
  - Single-run (not 3×) for the MVP; per-scenario quorum applied at the prod gate when it later exists.
  - Spend receipt on every result (tokens/$ per stage).
  - Holdout isolation implemented as a provable property with a test asserting inaccessibility.
  - Evaluator is deterministic (scenario scoring), no LLM judge in the wall itself.
- **Later phases (after the wall MVP is proven):**
  - Deterministic security pipeline (SAST / secret / dependency / provenance) — Security hard stance.
  - Prompt-injection mitigation (prerequisite before any agent holds credentials).
  - Credential-broker trust-anchor hardening.
  - Kill-switch recovery/compensation (dangling lease/worktree cleanup).
  - Supply-chain governance; monotonic non-reversible dark-mode authority; effect-boundary confirmation before committing the receipt schema.

---

## Unresolved Items (NEEDS_HUMAN_DECISION)

These are genuine forks the debate could not resolve, because they turn on product intent or secret-handling policy, not architecture.

1. **"No human reviews the diff" — scope of "dark."**
   Security holds this is unacceptable for security-relevant code, and that no deterministic pipeline fully replaces a human on security-critical paths. The premise "AI takes work from intent to production with no human reviewing the diff" is in direct tension with "a deterministic security pipeline is required." The user must decide whether "dark" means (a) no human reviews *anything* (Security says non-viable), or (b) no human reviews non-security-relevant diffs, while security-relevant paths get the deterministic pipeline plus an optional human escalation. **This decision gates whether the plan is even coherent.**

2. **Credential-broker trust anchor.**
   The broker mints short-lived task-scoped tokens, but the *root* trust (a long-lived key or identity that itself must never enter the agent loop) is unspecified. Who/what holds it, how it rotates, and where it lives is a secret-handling policy decision, not an architecture one.

3. **Destructive-op gating: intent vs effect.**
   Security notes the current gating classifies *intent*, not *effect*, and asks whether effect-level enforcement (infra-level deny at the sandbox/network boundary, rather than intent classification) is required before destructive-op gating can be considered safe. Whether the factory is allowed to gate on intent at all, or must gate on effect, is a risk-appetite decision.

---

## Key Decisions Summary

| Decision | Resolution | Outcome |
|----------|-----------|---------|
| Triple-run scope | MAJORITY | 3× only at final prod gate; single-run elsewhere; per-scenario quorum fix |
| LLM judge authority | RESOLVED | Advisory-only; deterministic enforcement; deterministic short-circuits first |
| Wall MVP scope | RESOLVED | MVP binds single-run + spend receipt + provable isolation; security pipeline deferred |
| "No human reviews the diff" | NEEDS_HUMAN_DECISION | Scope of "dark" unresolved |
| Credential trust anchor | NEEDS_HUMAN_DECISION | Root of trust unspecified |
| Destructive-op intent vs effect | NEEDS_HUMAN_DECISION | Enforcement level unresolved |
