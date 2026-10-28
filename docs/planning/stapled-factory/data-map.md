# Data Map

Entities, relationships, flows, access patterns, and storage for the stapled-together factory.

## Entity inventory

### Intent cascade (top of the tree — see "cascade rule" in architecture)

| Entity | Authored/drafted | Approved by | Notes |
|---|---|---|---|
| `Goal` | human | — (source of truth) | 3yr goal; the only thing the human authors |
| `YearlyOKR` | factory (drafted) | human | objective + measurable KRs |
| `QuarterlyOKR` | factory (drafted) | human | child of YearlyOKR |
| `Epic` | factory (drafted) | hybrid proxy | monthly; deterministic gate + LLM judge |
| `WorkPackage` | factory (drafted) | dark | child of Epic |
| `Issue` | factory (drafted) | dark | the GitHub issue unit |
| `Spec` | factory (drafted) | dark | machine-derivable acceptance criteria per unit |

### Execution / durable state

| Entity | Notes |
|---|---|
| `Claim` | who owns what unit of work |
| `Lease` | expiry on a claim (reclaim on timeout) |
| `Stage` | lifecycle position of a unit (draft → claimed → built → validated → gated → merged → deployed) |
| `Receipt` | result bound to exact git revision (idempotent reconcile after crash); carries a `UsageRecord` (below) |
| `Cooldown` | backoff between retries |

### The wall / validation

| Entity | Notes |
|---|---|
| `HoldoutScenario` | plain-English BDD scenario the generator **cannot reach** |
| `EvalRun` | one scenario execution against an ephemeral deploy (single-run; triple-run only at the final prod gate) |
| `EvalVerdict` | per-scenario 2-of-3 (prod gate) + ≥90% aggregate |

### Gates / control

| Entity | Notes |
|---|---|
| `Gate` | budget / destructive-op / initial-prod-deploy / credential |
| `UsageRecord` | per-run usage: `tokens_in`, `tokens_out`, `requests`, `wall_clock`, `model`, `tier`, `pricing_mode` (subscription or metered) |
| `CostCeiling` | per-run limit, in the tier's unit: `max_tokens` / `max_retries` / `max_wall_clock` always; `max_cost` (dollars) only when `pricing_mode = metered` |
| `BudgetLedger` | per-unit + per-day envelope, denominated in the tier's unit (dollars for metered, quota/time for subscription) |
| `CredentialLease` | short-lived, task-scoped, single-repo, single-action token |
| `ApprovalGate` | audit record of every cascade + gate approval/rejection (LLM-judge verdicts included) |

## Relationships

```
Goal 1──N YearlyOKR 1──N QuarterlyOKR 1──N Epic 1──N WorkPackage 1──N Issue 1──1 Spec
                                                                        │
Issue ──N Claim (workers race to claim; lease bounds it)
Claim ──1 Lease, ──1 Receipt (bound to git revision)
Spec  ──N HoldoutScenario
Issue ──N EvalRun (each scenario × 3) ──▶ EvalVerdict
BudgetLedger ── tracks spend across ──N Claim
CredentialLease ── scoped to ──1 Claim / Issue
ApprovalGate ── records ── every cascade level + Gate verdict
```

## Access patterns & isolation

- **HoldoutScenario** lives in storage the OpenHands worker's sandbox has **no mount/network path** to. The
  evaluator alone reads them, with its own credentials.
- **Receipt** is the write-once, append-only record that makes a lost executor reconcilable: a result is
  bound to claim + stage + git revision, so a timeout is resolved by checking "did a receipt already land?"
- **ApprovalGate** is append-only for auditability (the report's "auditability by design").
- The **intent cascade** (`Goal` → `Spec`) is committed as versioned docs (markdown), not opaque rows — the
  report requires "every intent translated into explicit, reviewable documents before work proceeds."

## Storage

- **Restate** owns the durable execution state (Virtual Objects: Claim/Lease/Stage/Receipt; journaled
  event log for replay).
- **Postgres** (from the fullstack template) holds the control-plane relational state (cascade docs index,
  BudgetLedger, ApprovalGate, CredentialLease audit) and the dashboard's data.
- **Git** holds the cascade documents and all produced code/diffs (worktree per task).
- **Bifrost** routes model calls; its own SQLite config.db/logs.db are runtime, not factory state.
