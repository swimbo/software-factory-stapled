# Phased Plan — The Stapled-Together Software Factory (Path A)

## Environment snapshot (Oct 13, 2026)

- **Code project root**: `/Users/bo/code/software-factory-stapled/stapled-factory/` (empty; factory code lands here)
- **Planning docs**: `/Users/bo/code/software-factory-stapled/docs/planning/stapled-factory/` (this directory)
- **Parent**: `/Users/bo/code/software-factory-stapled/` is *not* a git repo; contains only the research report
  `dark-software-factory-report.md` (Oct 2026) that this factory is based on.
- **Reusable assets already on the machine** (see `architecture.md` for full mapping):
  - **Bifrost LLM gateway** — `/Users/bo/code/reference/nanoclaw/src` (MIT)
  - **NUC/M1 build-routing workers** — `/Users/bo/code/personal/hermes-agent-p-nuc-reconstruction/plugins/m1-build-routing/`
  - **Dev Projects mission/receipt pattern** — `/Users/bo/code/.dev-projects-worktrees/` + Hermes fork `/Users/bo/code/personal/hermes-agent-p`
  - **Fullstack template** — `/Users/bo/.claude/templates/fullstack/` (React 19 + Rust/Axum/SQLx + agents SDK + Postgres)
  - **Harness-comparison research** — `/Users/bo/code/personal/hermes-agent-p/research/harness-comparison/`
- **Platform**: local (NUC + Mac), self-hosted. No cloud dependencies.

## Scope fence

**IN scope** (see `architecture.md` for detail)
- Goal-cascade decomposition engine ("Inception") — 3yr-goal-and-down.
- The wall — holdout BDD scenarios + isolated evaluator (triple-run, 2-of-3, 90% gate, opaque feedback).
- The qualifier — score GitHub issues, route dark-lane vs human-lane.
- Orchestration spine — Restate (Virtual Objects + replay) holding claims/leases/stages/receipts.
- Worker integration — OpenHands (CodeAct) in the NUC/M1 sandbox, driven by Restate.
- Tiered model routing through Bifrost.
- Human gates (production) + intent-level cascade approvals.
- Minimal control-plane dashboard (fullstack template).
- End-to-end dogfood run on a new fullstack app.

**OUT of scope**
- Clean-room reimplementation (Path B). Full Level-5 lights-out. Cloud hosting / multi-region.
- New OAuth/provider identity beyond Bifrost. General-purpose agent benchmark.

## Locked decisions

| Decision | Choice |
|---|---|
| Worker agent | OpenHands (CodeAct v3) |
| Orchestration | Restate |
| Execution isolation | NUC/M1 build-routing |
| Platform | Local (NUC + Mac) |
| Model routing | Tiered mix via Bifrost |
| Issue intake | GitHub issues → webhook → qualifier |
| Goal cascade depth | 3yr-goal-and-down (human approves yearly+quarterly; hybrid proxy approves epics) |
| First target | New fullstack app |
| Effort scope | Plan + wall MVP |

## Phase overview

| # | Phase | Output | Status |
|---|-------|--------|--------|
| 1 | Requirements & discovery | `phased-plan.md`, `findings.md` (init) | complete |
| 2 | Pain point research | `findings.md` | **SKIP** (internal tooling; user supplied report) |
| 3 | Data map | `data-map.md` | complete |
| 4 | User stories | `user-stories.md` | complete |
| 5 | Architecture (+ debate) | `architecture.md` | complete |
| 6 | Tech guide (+ debate) | `tech-guide.md` | complete |
| 7–8 | UX/UI (minimal) | `ux-plan.md`, `ui-plan.md` | complete |
| 9 | Implementation — wall MVP | wall evaluator code | complete (local gates; expanded integration tracked in progress.md) |
| 9b | Inception engine | goal-cascade decomposition | pending |
| 10 | E2E test generation | `e2e-tests.md` | pending |
| 11 | Testing & verification | `progress.md` | pending |
| 12 | Delivery | dogfood run | pending |
| 13 | Plan gap audit | `plan-gaps.md` | pending |
| 14 | Fix all gaps | `plan-gaps.md` | pending |

## Delegated work

| Task | Agent | Status | Result |
|------|-------|--------|--------|
| — (none yet) | — | — | — |

## The wall-first rule

The holdout evaluator (Phase 9) is built **before** anything else that touches production, per the
report §10.2. Its acceptance criteria live in `phase-9-plan.md`.
