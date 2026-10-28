# Findings

Research discoveries, external content, and technical decisions captured during planning.

## Phase 2 — Pain Point Research (SKIPPED)

**Skip reason:** this is internal tooling with no external users. The user supplied a comprehensive
research report (`dark-software-factory-report.md`, Oct 2026) that already grounds the problem space and
the component landscape. Per the planning workflow, Phase 2 may be skipped when "the user explicitly
provides their research." No web pain-point synthesis is required.

## Volatile decisions made (to be re-verified in Phase 5/6 kickoff)

- **Orchestration = Restate** (chosen over Hatchet). Rationale: single-binary local ops, Virtual Objects
  map 1:1 onto the claims/leases/receipts model, first-class human-approval + kill/pause for live agents,
  and Replit's coding-agent platform runs on it. See the Hatchet-vs-Restate comparison in the session; the
  full `agents-argue:debate` on this lands in Phase 5.
- **Worker = OpenHands (CodeAct v3)** over the existing Hermes fork / OpenCode / Goose — chosen for
  auditability + model-agnosticism per the report; validated against the user's own harness-comparison data.
- **Proxy (epic approver) = hybrid**: deterministic policy gate (hard floor) + LLM judge (semantic), with the
  judge deliberately distinct from the drafter to avoid self-approval sycophancy.
- **Scope of "dark" = tiered**: non-security-relevant diffs run fully dark; security-relevant diffs (auth,
  crypto, permissions, data-boundary) get the deterministic pipeline + a human escalation. (Resolved after
  the debate — the Security Architect's minimum-viable posture.)
- **Trust anchor = `FACTORY_MASTER_KEY`** env, outside the agent loop; swappable for secret store/KMS.
- **Destructive-op gating = effect-based** infra deny (not intent), via `security/firewall.py`.

## Kickoff research (per phase — populated as each phase runs)

### Phase 5/6 — OpenHands worker interface (Oct 2026)

How an orchestrator drives OpenHands headlessly:
- **Headless CLI**: `openhands --headless -t "task"` with `--json` for streaming JSONL events. **Always runs
  in `always-approve` mode** (no confirmation; `--llm-approve` unavailable) — a security consideration the
  debate flagged (no per-action approval in headless ⇒ need the per-tool-call firewall).
- **Software Agent SDK + Agent Server** (preferred for our worker): `openhands-agent-server` runs the agent
  behind an HTTP + WebSocket API, with session API keys (`X-Session-API-Key`), workspace management
  (local / Docker / remote VM), and an OpenAI-compatible endpoint. Clean seam for Restate to dispatch and
  stream from, vs. shelling out to the raw CLI.
- The SDK/Agent Server is the engine behind OpenHands Cloud; `OpenHands/automation` owns scheduling +
  webhooks + dispatching — OpenHands already splits "automation" (orchestration) from "execution" (runtime),
  matching our Restate = orchestration / Agent Server = execution split.

### Phase 5/6 — Restate (Oct 2026)

- Single self-contained Rust binary; journaled durable execution with deterministic replay; **no external
  DB/broker required** (own replicated command log).
- Primitives: durable handlers/workflows, **Virtual Objects** (keyed durable state, single-writer,
  concurrency control), queues + durable RPC, timers + signals (human approvals), flow control, and
  **Operations** (inspect/pause/resume/cancel/kill a live invocation).
- Explicitly markets "coding agents / AI agent platforms," "human approvals," "code-sandbox orchestration."
  Replit's coding-agent platform runs on it.
- TypeScript SDK is first-class. Determinism: side effects go through `ctx.run` / child calls; code between
  steps must be deterministic.

### Phase 5 — Architecture debate findings (3 personas)

See `debate-transcript.md` and `consensus-plan.md` (written by the moderator). Core convergent findings:
wall is a quality gate, not a security boundary; deterministic controls must enforce, LLM must advise;
effect boundary precedes the receipt schema; per-run cost ceilings required; holdout isolation must be
provable by test.

## Kickoff research (pending)

- Phase 10 (e2e): TBD.

## NEEDS_HUMAN_DECISION (unresolved)

- None — all three debate-flagged decisions (scope-of-dark, trust anchor, destructive-op gating) are
  resolved (see "Volatile decisions" above and `architecture.md` §12).
