# Tech Guide — Stapled-Together Software Factory

## Stack summary (locked)

| Layer | Technology | Version note | Hosting |
|---|---|---|---|
| Orchestration | **Restate** | single Rust binary; TS SDK first-class | local binary on Mac control plane |
| Worker agent | **OpenHands Agent Server** (`openhands-agent-server`) | HTTP + WebSocket API, session keys | self-hosted, in NUC/M1 sandbox |
| Model routing | **Bifrost** (existing) | `/anthropic` + `/openai/v1` surfaces | local gateway |
| Relational state | **PostgreSQL 16** | fullstack template | local |
| Control plane / services | **TypeScript** (Restate SDK) | Node 22 | local |
| Wall evaluator | **Python** (BDD via pytest + behave) | 3.12+ | local, isolated |
| Dashboard | React 19 (fullstack template) | Vite 6, Tailwind 4 | local |

## Language choices (rationale)

- **Restate services → TypeScript.** Restate's TS SDK is first-class; the fullstack template already ships a
  TS "agents" service on the Claude Agent SDK, so the control plane stays in one language. Node 22 LTS.
- **Wall evaluator → Python.** The wall runs plain-English BDD scenarios (Gherkin via `pytest-bdd`/`behave`)
  and drives an ephemeral deploy of the target unit. Python matches OpenHands' own ecosystem and is the
  fastest path to a "janky but isolated" evaluator. Keep it dependency-light (stdlib + pytest).

## Key interfaces (the seams — version these)

- **Restate → OpenHands**: call the Agent Server over HTTP/WebSocket with a session API key; dispatch a
  `Conversation` for the task, stream events, collect the produced diff. This is an **effect** (`ctx.run`
  child), not replayable state.
- **OpenHands → workspace**: `DockerWorkspace` / `APIRemoteWorkspace`, or the NUC/M1 build-routing
  workspace. Deny-by-default network; the holdout store is **not** mounted or reachable.
- **Bifrost → models**: one OpenAI/Anthropic-compatible surface; per-model `pricing_mode` config
  (`subscription` | `metered`).
- **Every seam** carries a schema-version field (the four-way compatibility contract from the debate).

## Determinism & effect boundary (Restate)

- Control-plane state (stage/claim/lease/verdict) is **replayable** journaled state.
- The OpenHands run, eval execution, and network calls are **non-deterministic effects** → wrap in
  `ctx.run` / child invocations. A crash before the effect result is journaled can cause a retry; reconcile ambiguous remote outcomes before dispatching again.
- Commit the receipt schema only after this boundary is confirmed in a spike.

## Cost model (dual mode)

Per-model `pricing_mode` (`subscription` | `metered`). Both modes always record a `UsageRecord`
(tokens/requests/wall-clock/tier). Metered adds a hard `max_cost` and a `cost_estimate`. See
`architecture.md` §10.

## Dev environment setup (local)

1. `restate` — run the single binary; TS SDK via `@restatedev/restate-sdk`.
2. `openhands-agent-server` — run with a session API key, bound to `127.0.0.1` unless a second host needs it.
3. `bifrost` — already running (point `ANTHROPIC_BASE_URL`/`OPENAI_BASE_URL` at it).
4. Postgres 16 — from the fullstack template's Docker Compose.
5. Wall evaluator — Python venv + pytest, run against an ephemeral target deploy.

## Version pinning (verify in Phase 6 kickoff — not yet confirmed)

- Restate: confirm current TS SDK major + any breaking changes to `ctx.run`/Virtual Object API.
- OpenHands Agent Server: confirm current image + SDK version and the headless/`always-approve` behavior.
- Node 22 vs 24; Python 3.12 vs 3.13.

## Debate note

The volatile forks (Restate vs Hatchet, OpenHands vs Hermes/OpenCode, model tiering, dual pricing mode)
were already argued at the **architecture** debate and locked by the user. This tech guide records the
consequences; a separate full tech-guide debate would re-litigate settled decisions, so it is deferred —
remaining **version-pinning** unknowns are flagged above and resolved via kickoff research during Phase 9
implementation.

## Tech-guide debate outcome (2026-10-26)

The four-perspective review is recorded in `tech-guide-debate.md`, with evidence and limits in `tech-guide-evidence.md`. Retain the locked stack, subject to the open version and security gates there. In particular, `ctx.run` journals completed results but does not by itself prevent a duplicate remote effect after an ambiguous crash; reconcile or stop on unknown outcome. An enforced sandbox, not a tool hook alone, is the security boundary. Budget enforcement and actual usage must be demonstrated at the provider or gateway.
