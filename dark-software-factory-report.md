# The Dark Software Factory

**A field guide to building autonomous, lights-out software production — and the harnesses, orchestration, and building blocks to do it from scratch.**

*Research report · October 13, 2026*

---

## 1. Executive summary

A **dark software factory** is a software-delivery system in which AI agents take work from intent to production with *no human authoring or reviewing the diff*. The name borrows from manufacturing (FANUC's Oshino plant has run "with the lights off" since 1981), and the software equivalent became a named discipline in January 2026 when Dan Shapiro of Glowforge published the "Five Levels" autonomy ladder, followed by BCG Platinion's *The Dark Software Factory* report in March 2026.

Three decisive findings from this research:

1. **The factory is not the harness.** A harness (Claude Agent SDK, OpenHands, LangGraph) is what the agent runs *inside*. The factory is the surrounding system that makes a harness's output *mergeable without a human reading the diff*. The single most important architectural rule — the one every credible practitioner repeats — is **strict isolation between code generation and validation**, using "holdout" acceptance scenarios the generator never sees. Without that wall you have theater, not a quality gate.

2. **The bottleneck moves from coding to judgment.** The two competencies that decide whether a factory works are *harness engineering* (designing, building, and refining the factory itself) and *intent thinking* (translating business needs into precise, testable descriptions of "correct"). The scaffolding/orchestration alone is worth a 16-point swing on SWE-bench Verified (the same model scores 43.2% on one scaffold and 59.8% on another).

3. **The interesting work is not shipped in a box.** Every durable-execution engine, sandbox, and agent SDK is a commodity. What you must build yourself is the **wall** (holdout scenarios + isolated evaluator), the **qualifier** (routing work between the dark lane and human lane), and the **gates** (budget, destructive ops, initial production deployment, credential issuance). That is where the week of hard work and the years of maintenance live.

The remainder of this report covers the definition and landscape (§2–3), the canonical architecture (§4), a vendor-neutral map of every building block with ranked recommendations (§5–6), the hard-won best practices (§7), and two concrete build plans — **Path A, stitched from open-source repositories** (§8) and **Path B, a clean-room implementation** (§9) — plus the specific human gates for your "mostly lights-out, a few gates" posture (§10) and failure modes (§11).

---

## 2. What a dark factory is — precisely

The cleanest framing is Shapiro's five-level ladder, borrowed from SAE's self-driving levels (via Pulumi's summary):

| Level | Name | Human role |
|------|------|-----------|
| 0 | Spicy autocomplete | You write everything |
| 1 | Coding intern (boilerplate) | Cruise control |
| 2 | Junior developer (interactive pair) | One hand on the wheel |
| 3 | AI writes the majority; you review every PR | Eyes on the road |
| 4 | Spec-driven; agent runs unattended for hours; you review later | Sleeping at the wheel |
| 5 | **Dark factory** — no human review of code before production | No steering wheel |

Most teams sit at level 2–3; a few aggressive ones at 4. Level 5 is still an experiment. The design question is what *must be true* for level 5 to be safe at all.

BCG's definition is the canonical one: *"Autonomous AI agents build, test, and ship software solutions around the clock. Humans define business intent and review outcomes rather than writing code."* Critically, they stress that **"a lights-out factory doesn't mean an uncontrolled one"** — the defining shift is the *relocation* of human effort, not its absence. Two competencies become decisive:

- **Harness engineering** — designing, building, and refining the factory, constantly feeding information to its assembly lines.
- **Intent thinking** — translating business needs into precise, testable descriptions of desired outcomes (explicitly *not* prompt engineering; it requires domain depth no AI substitutes for).

Two other useful framings, both more operational:

- **gethuman.sh ("Getting to the Dark Software Factory")**: darkness is *per-unit*, not per-organization — about which *stages* run unattended for each unit. It requires three things from infrastructure, not the agent: **machine-derivable intent**, **machine-observable outcomes**, and **machine-bounded blast radius**. A **qualifier** routes each unit into a dark lane or leaves it for humans, and that scoring function — not the agents — "is the real product."
- **darkcli.dev (Dark Factory)**: an 8-phase pipeline from spec to shipped code, where "each phase is a gate," agents coordinate through typed interface contracts and a SQLite-backed "agent mail" system, and parallel builders work in isolated git worktrees bound by typed contracts.

---

## 3. The current landscape — who's doing it, and the numbers

**Reported results (treat vendor numbers skeptically; these are widely-cited but not independently verified):**

- **OpenAI** built a million-line product in five months with three engineers and zero manually written code, estimating a ~10x speedup (OpenAI, "Harness engineering," Feb 2026).
- **Spotify** — the "Honk" platform, built on coding agents — merges 650+ AI-generated PRs/month; roughly half of all updates flow through it; engineering time is down up to 90% on large migrations. Spotify's best engineers haven't written a line of code since December 2025.
- **StrongDM** — three engineers shipped ~32,000 lines of production code without writing or reviewing any of it.
- **Stripe** — the "Minions" agent system merges over a thousand PRs per week.
- **BCG** claims organizations operating at this level report **3–5x productivity gains**, and ran their own legacy-migration task force (5 days, 2 business-critical apps, 20% gain after 2 days, projected >50% at scale).

**The important independent data point is on SWE-bench Verified** (500 human-filtered GitHub issues; a model produces a patch that makes the repo's own tests pass). The SWE-bench maintainers run a "bash-only" mini-SWE-agent scaffold so every model gets the *same* harness. As of October 2026, the frontier is:

| Model | SWE-bench Verified (independent, bash-only) |
|-------|---------------------------------------------|
| Claude Opus 4.5 (high) | ~76.8% |
| Gemini 3 Flash (high) | ~75.8% |
| MiniMax M2.5 (high) | ~75.8% |
| Claude Opus 4.6 | ~75.6% |
| Claude Sonnet 4 | ~74.8% |
| **GLM-5 (high)** | **~72.8%** |
| GPT-5.2 / GPT-5.2-Codex | ~72.8% |
| Kimi K2.5 | ~70.8% |
| GLM-4.6 | ~68.2% |
| Claude Haiku 4.5 | ~66.6% |

Two cautions, in line with how these numbers always behave:

- **Vendor vs. independent is a real gap.** Provider-reported scores routinely overshoot what independent runs (vals.ai, SWE-bench maintainers) reproduce — sometimes by double digits. The bash-only board above is the trustworthy anchor. Newer models (e.g. "Opus 5," "Fable 5") carry provider numbers near 95% that independent runs have *not* matched; do not build a factory's model-choice decision on those.
- **The scaffold matters as much as the model.** The spread between rows sharing the same base model is the story: SWE-agent v1 on Claude Sonnet 4.5 scores 43.2%, while Cline in autonomous mode on the *same* model scores 59.8% — a 16-point gap from orchestration alone.

**The agent-scaffold tier list** (SWE-bench Verified, any scaffold):

- **OpenHands + CodeAct v3** (Claude Opus 4.6): ~68.4% — open source, MIT, the most active OSS coding agent. Its "CodeAct" scaffold has the agent write actual Python to read files/run tests/apply changes rather than emit JSON-schema tool calls, which significantly beats tool-calling on multi-step tasks.
- **SWE-agent / mini-SWE-agent v2**: >74% — Princeton/Stanford, MIT. The `mini-SWE-agent` variant is ~100 lines of Python and is the benchmark everyone else measures against.
- **Augment Code**: ~68%+ (managed/enterprise).
- **Cline (autonomous mode)**: ~59.8%.
- **Factory.ai Droid**: ~58.1% — notable as the only top-10 entry built on GPT-5.3-Codex; designed for CI/CD automated-fix triggering, not interactive dev.
- **Devin 2.0 (Cognition)**: ~45.8% unassisted — turnkey but model-locked, cloud-only, and the weakest of the group on raw scores despite the best marketing.

---

## 4. The canonical architecture

Strip a dark factory to its layers (Pulumi's framing, generalizable beyond IaC):

```
Inputs (humans: spec + acceptance criteria)
        │
        ▼
Code Generation (autonomous)  ──────►  Validation (autonomous, ISOLATED)
        │                                  │
        │          holdout scenarios        │  (generator never sees these)
        │                                  │
        ▼                                  ▼
                     Merge & Deploy (autonomous + existing CI/CD)
                     (fail → loop back to generation)
```

The **wall** between generator and validator is the load-bearing element. The reason is *sycophancy*: LLMs agree with their own prior turns and declare victory on what they just produced. If the same model writes the change and then grades it, a test stored next to the implementation "will get lazily rewritten to match the code, not the other way around." StrongDM's concrete pattern:

1. Holdout scenarios are plain-English BDD acceptance tests stored where the generator *cannot reach them*.
2. Each scenario runs **three times** against an ephemeral deployment; **two of three must pass**; overall pass rate must clear **90%**.
3. On failure, the generator gets a one-line failure message ("SQL Injection Detection failed: endpoint returned 500") — *not* the scenario text — so it cannot game the test.

BCG's five pillars of transformation (of which I confirmed three verbatim; the framework is five parts covering operating model, knowledge, workforce, governance, and the enabling technology):

1. **Intent-driven operating model** — sprints become "bolts" (weeks → hours). The SDLC becomes a continuous three-phase cycle: *Inception* (translate intent → specs + work packages), *Construction* (agents plan/code/test, humans validate via oversight), *Operation* (agents deploy, monitor, and triage incidents). **Auditability by design** is a deliberate consequence: every intent is translated into explicit, reviewable documents before work proceeds.
2. **Codified knowledge and tech readiness** — ADRs, API docs, domain models, coding standards, and business rules as versioned markdown in the repo, plus clean repos, reliable CI/CD, comprehensive tests, and production telemetry. "Automating chaos" is the failure mode if this is skipped.
3. **Workforce upskilling and role evolution** — intent thinking as the new critical competency; 80% of engineers need upskilling through 2027.

---

## 5. The building blocks — a vendor-neutral map

### 5.1 Models

The factory should be model-agnostic at the harness layer and route to the best model per task. Two tiers:

- **Frontier (API-only)**: Claude (Opus/Sonnet/Haiku line), Gemini, GPT-5.x — highest SWE-bench scores, highest cost.
- **Open-weights (self-hostable)**: **GLM-5** (~73% Verified — the strongest open-weight Chinese model and very cost-efficient), DeepSeek-V4, MiniMax M2.5 (~76%), Qwen, Kimi K2.5. For a cost-sensitive factory, open-weights through a gateway is the economically rational default; a TrueFoundry benchmark found a custom scaffold on **GLM-5.2 achieved ~75% lower cost** ($2.90 vs $11.80 per run) at ~equal task accuracy versus a managed agent.

A **gateway** (Bifrost, LiteLLM, OpenRouter) gives you one Anthropic/OpenAI-compatible surface to swap models without touching harness code — and lets you route cheap models to cheap tasks and frontier models to hard ones.

### 5.2 Coding agents / scaffolds

The agent is the "worker" the factory coordinates. Ranked for a self-hosted, auditable factory:

- **OpenHands** (MIT) — most active OSS, self-hosted, model-agnostic, transparent logging (every tool call/file read/shell command), multi-agent delegation. *Best default.*
- **SWE-agent / mini-SWE-agent** (MIT) — highest raw scores, radically simple; ideal if you want to *understand* and reimplement the loop rather than adopt a big platform.
- **A CLI harness** (Claude Code, Aider, OpenCode, Codex CLI) — the pragmatic choice if you treat the agent as a headless subprocess with an exit code + diff contract, rather than a platform. This is what a clean-room builder leans on.
- **Cline / Augment / Devin / Factory.ai Droid** — Cline is a strong autonomous scaffold; Devin is turnkey but locked and cloud-only; Factory's Droid is CI-native.
- **Goose (Block, Apache-2.0)** — a general-purpose Rust harness rather than a coding-agent scaffold, so it's *absent from SWE-bench leaderboards* and I've deliberately placed it in the harness layer below rather than ranking it here on benchmark scores. It can serve as a headless worker via `goose run` (exit-code + diff contract), but has no native git worktree support — isolation has to be layered on.

### 5.3 Harnesses / agent frameworks (the control loop)

This is the layer the user's own stack sits in. The key decision is **"who writes the control flow?"**

- **Claude Agent SDK** — a batteries-included agent loop (the Claude Code loop as a library) with 10+ built-in tools, sessions, subagents, permissions, hooks, and **budget caps** (`max_budget_usd`). Fastest to a working agent; but Claude-models-only and the loop shape is fixed.
- **LangGraph** — a low-level, provider-agnostic *graph runtime* where you draw nodes/edges/control flow yourself, with checkpointer persistence and human-in-the-loop interrupts. Earns its complexity when the workflow has a *required shape*.
- **OpenAI Agents SDK / smolagents / CrewAI / AutoGen / DeepAgents** — the rest of the field; CrewAI/AutoGen are opinionated multi-agent; smolagents is minimal. Fewer built-in safety surfaces than the Claude SDK.
- **Goose (Block, Apache-2.0, Rust)** — a general-purpose harness with a distinctive advantage for a factory: the **recipe model** (declarative YAML describing agent behavior) is *reviewable, versionable, and diffable*, which aligns directly with BCG's "auditability by design." Headless `goose run` exposes an exit-code + diff contract; it's MCP-native (~70 connectors) and model-agnostic (runs on any OpenAI-compatible endpoint, including a Bifrost/GLM/DeepSeek route). Its cost for a factory: no benchmark evidence for its coding loop (general-purpose, not code→test→fix tuned), and no native git worktree support — you supply worktree/microVM isolation. It's the other half of the Hermes-vs-Goose harness fork, applied to the worker layer.
- **The pragmatic synthesis** (documented in multiple 2026 comparisons): LangGraph (or your own control plane) owns the *outer* orchestration and durable state, and a node inside it calls the Claude Agent SDK (or a CLI harness) to do a self-contained coding task. That's the two-layer shape most production factories converge on.

### 5.4 Orchestration / durable execution

This is the deepest architectural fork. There are two families, and picking the wrong one makes your agent "fight the platform every commit" (Tanay Shah, 2026):

- **Replay-based** (Temporal, Cadence, Restate, Hatchet): workflow code must be deterministic; the engine records an event history and replays it, skipping completed steps. Best-in-class audit trail, "time travel," years-running workflows. Temporal is the battle-tested reference (7 languages, but you run a worker fleet + server + DB).
- **Checkpoint-resume** (Trigger.dev v4, LangGraph): snapshot state at await points, resume forward; non-deterministic code is fine, but no full event-history audit in the same shape.

- **Inngest** sits orthogonally: event-driven *step functions*, each step a durable atomic unit — serverless-native, no separate infrastructure, strong for event-coordinated agents.
- **Hatchet** is the "one Postgres box is enough" option — a DAG-style task queue + workflow with the lowest operational burden. **DBOS** similarly uses Postgres as the database.
- **DAG orchestrators** (Airflow, Prefect, Dagster) are for batch ETL, not agent loops — generally the wrong tool here.

**Recommendation for a dark factory:** a **replay-based engine if you need a bulletproof audit trail and long-running units** (Temporal for proven, Restate for one-binary simplicity, Hatchet for minimal ops) — OR, if your units are short and your app is TypeScript-first and you prefer non-deterministic code, **Trigger.dev v4 or Inngest**. If you already have a custom JSON/Postgres-backed claims+receipts queue (which is what a "Dev Projects"-style factory effectively is), you have most of Hatchet's value proposition already — see §9.

### 5.5 Sandboxing / isolation infrastructure

Every agent action is untrusted code. The boundary defaults that recur across every 2026 security guide:

- One **disposable checkout per task**, no host mounts.
- **Deny-by-default network**, then allowlist named registries/APIs.
- **No inherited developer/production secrets** — use a credential *broker* that injects short-lived, task-scoped tokens so the agent never sees the secret value.
- **Agent branch + draft PR only**; no direct merge/deploy authority.

Concrete runtimes:

- **E2B** (open-source runtime, Firecracker microVMs) — hardware-isolated microVMs that resume from a snapshot, pause/resume, copy-on-write filesystem. The reference for "one disposable VM per agent session."
- **DigitalOcean Harness Runtime** — managed microVM sandboxes with secrets-vs-env separation, egress allowlists, permissions blocks, and lifecycle APIs (pause/resume/fork).
- **Daytona, Modal, plain Docker + gVisor** — lighter-weight options; Docker alone is *not* a sufficient security boundary, so pair it with gVisor/Firecracker if you're self-hosting isolation.

### 5.6 MCP and tooling

MCP is now the standard tool-integration seam: a stateless JSON-RPC protocol for **tools, resources, prompts**, plus a Tasks extension (SEP-2663) for long-running work and server-initiated events. Practical guidance (AWS's MCP strategy guide):

- Bundle operations into tools that map to *complete* interactions (≥3-call workflows → one tool); limit parameters to ≤8.
- Use an **MCP gateway** as a centralized proxy so each agent connects to one endpoint for authn/authz/routing, rather than registering every server directly.

One notable 2026 result: the **MCP Workflow Engine** pattern decouples *intelligence* (deciding what to do) from *execution* (carrying it out) — the agent emits a declarative JSON blueprint once, and a lone `run_workflow` call executes it with >99% token reduction on repeat runs. This "plan-once, execute-cheap" split is directly relevant to a factory that repeats similar work.

---

## 6. Best practices — the hard-won lessons

Consolidated from the Pulumi dark-factory pattern, BCG, and the 2026 security/checklist literature:

1. **Isolate generator from validator.** The wall (holdout scenarios + separate evaluator) is non-negotiable. Triple-run with a 2-of-3 threshold and a 90% aggregate gate; feed failures back as opaque one-liners.
2. **Separate planning from execution for anything high-risk.** The agent prepares the command/patch/runbook in a lower tier; a human-controlled production workflow applies it.
3. **Broker credentials; never put secrets in the agent context.** Short-lived, task-scoped, single-repo, single-action tokens injected by a trusted proxy. Keep a *dedicated agent secret set* distinct from developer and CI secrets. Rotate anything exposed.
4. **Bound blast radius mechanically.** Canary releases, feature flags, automated rollback. Scope agent identities at the credential layer; require human approval for *destructive* operations (drop DB, delete zone, rotate root key, cross regulated-data boundaries).
5. **Cap costs before you discover them.** Cap retries (StrongDM/Pulumi suggest ~3 per spec), set `max_budget_usd`, alert on per-run token spend. StrongDM reported ~$1,000/day per engineer-equivalent — cheaper than a salary only if the cap is in place first.
6. **Gate the merge and deploy.** Protected branches, mandatory checks, code-owner approval on sensitive paths, and — critically — **the agent cannot merge or deploy its own change, and cannot bypass those rules.** A `block-no-verify` hook that refuses `--no-verify`.
7. **Make every action observable.** Full traceability of reasoning steps, tool invocations, and code-generation decisions is what *enables* layered verification and red-teaming. "The lights may be off, but nothing goes unseen."
8. **Treat the agent loop as a control surface, not a chat box.** Review max tool-use turns, spend caps, hooks, permissions, sandboxing, settings scope, and the human-review handoff before expanding scope.
9. **Write `AGENTS.md` and codified knowledge first.** Codified ADRs/domain rules/standards are a *prerequisite*, not an optimization — "better AI only amplifies the gap" between organizations that did this work and those that didn't.
10. **Earn auto-merge with data, don't declare trust.** Pulumi's Phase 3 gate: only flip auto-apply after three measurable thresholds hold over ~20 PRs — >90% scenario pass rate, <5% false-positive rate, <10% human-override rate. Add a weekly drift sweep through the same gate.

---

## 7. Two build plans

The two paths are genuinely different engineering problems. Path A minimizes bespoke code but accepts dependency sprawl and integration seams. Path B maximizes control and auditability but commits you to maintaining infrastructure code.

### Path A — Stitched together from open-source repositories

**The idea:** buy every layer, write only the glue (the qualifier, the wall, and the gates).

**Component wiring, with recommended repos:**

| Layer | Recommended component(s) | Notes |
|-------|--------------------------|-------|
| Model routing | Bifrost / LiteLLM / OpenRouter | one OpenAI/Anthropic-compatible surface; route cheap↔frontier per task |
| Worker agent | OpenHands (CodeAct) *or* a CLI harness (Claude Code / OpenCode) run headless | OpenHands for auditability; CLI harness for the exit-code+diff contract |
| Harness/loop | Claude Agent SDK (inner) | batteries-included tools, permissions, hooks, budget caps |
| Orchestration | Temporal *or* Hatchet (outer) | Temporal for proven audit/durability; Hatchet for "one Postgres box" |
| Sandbox | E2B (Firecracker microVMs) | snapshot-resume disposable VMs |
| Tool seam | MCP + a gateway | tools/resources; Tasks extension for long runs |
| Eval/wall | custom holdout evaluator (SWE-agent's eval harness as a starting point) | the part you build yourself |
| Issue intake | GitHub / Linear webhooks → qualifier | the qualifier routes dark-lane vs human-lane |
| Credentials | a broker (e.g. your gateway's OIDC, or a Vault/policy proxy) | short-lived, task-scoped |

**What you still build:** the **qualifier** (scores each ticket, claims dark-lane work, leaves the rest to humans), the **wall** (holdout scenarios + isolated evaluator), the **gates** (budget caps, destructive-op approval, initial-prod-deploy approval, credential issuance), and the **glue** that binds claims → worktree → receipt → merge decision.

**Strengths:** fast to a working line; each layer is best-in-class and independently maintained; you can swap any component.
**Risks:** dependency sprawl; multiple mental models (Claude SDK + Temporal + E2B + MCP = four); integration seams where the replay-vs-checkpoint and determinism assumptions clash; upgrade churn.

**Bottom line for Path A:** this is the right choice if you want a *running factory this quarter* and are comfortable treating the seams as permanent. The two components I'd be most opinionated about: **OpenHands for the worker** (auditability is the scarce resource) and **Temporal or Hatchet for orchestration** (do not hand-roll durability if you're also gluing three other systems).

### Path B — Clean-room implementation

**The idea:** borrow the *design* of the best tools, write a minimal, coherent engine that does exactly what a dark factory needs and nothing else — with a first-class audit trail and no dependency sprawl.

This is where the user's existing "Dev Projects" factory (goal cascade + durable state with claims/leases/stages/receipts bound to git revisions, a control-plane/worker split) is already half-way. A clean-room dark factory generalizes it.

**Reference architecture — four planes:**

1. **Control plane (the orchestrator).** Owns the authoritative state, the goal cascade (intent → spec → modules → work packages), the qualifier, and the gates. Does *not* execute agent code — mirroring Temporal's "the service doesn't execute your code" and the user's own NUC-control / Envision-execute split.

2. **Durable state (append-only event log).** The single most important clean-room decision. Borrow from three sources:
   - Temporal/Restate's **replay over an event log** — every step recorded, recoverable, auditable.
   - The user's own **receipts** — a receipt binds result → claim → stage → exact git revision, so a timeout/lost-executor can be reconciled.
   - Hatchet/DBOS's **"one Postgres box"** — Postgres as the queue + state store, avoiding Kafka/Cassandra.
   The state model: claims (who owns what), leases (expiry), stages (lifecycle position), attempts (retry count), receipts (result bound to revision), cooldowns (backoff).

3. **Execution plane (workers).** Bounded, disposable workers — each in its own **git worktree** (or microVM), seeded with its typed interface contract, running a headless agent loop. The **typed interface contract** (from darkcli.dev) is the key clean-room idea: modules are decomposed with a dependency DAG and typed contracts, so parallel builders implement *to a contract* without sharing state or coordinating.

4. **Verification plane (the wall).** An isolated evaluator with its own credentials and *no access* to the generator's prompt or output. Runs holdout scenarios (triple-run, 2-of-3, 90% gate) and a deterministic policy/static-analysis layer (lint/typecheck/test/secret-scan). Feeds opaque one-line failures back to generation.

**The loop:**

```
intent → spec (inception) → decompose into modules + typed contracts + DAG
      → parallel builders (worktrees) → integration + checkpoint tests
      → isolated holdout evaluation → [gate: budget / destructive / prod-deploy / credentials]
      → merge + deploy (existing CI/CD) → observe → drift sweep → next intent
```

**What to borrow from each source you're studying:**

- **The wall + holdout triple-run** from StrongDM / Pulumi.
- **Typed contracts + SQLite agent-mail** from darkcli.dev.
- **The qualifier / lane routing** from gethuman.sh.
- **Receipts bound to git revision** from your own Dev Projects model (this is genuinely more rigorous than what Hatchet ships).
- **Replay + determinism (or checkpoint-resume)** from Temporal vs Trigger.dev — pick one model and enforce it uniformly, rather than mixing.
- **Schema-backed artifacts + explicit promotion gates** from the "Ultimate Harness" OSS project (mission.yaml → verification.yaml → promotion.yaml as a human-approval step).
- **Credential broker + ExecutionRequest/SandboxLease/ExecutionReceipt records** from the 2026 sandbox-security literature.

**Strengths:** a coherent, auditable engine with no dependency sprawl; you understand every line; the audit trail is a first-class citizen; it fits your existing goal-cascade machinery.
**Costs:** you are now maintaining infrastructure, not just gluing it; the "wall" (holdout scenarios + evaluator) is a week of hard work that no platform ships; determinism/durability edge cases (lost executors, partial merges, clock skew) are yours to solve.

**Bottom line for Path B:** this is the right choice if you value auditability and control above time-to-first-line, and if you've already internalized that the *interesting* work (the wall, the qualifier, the gates) can't be bought anyway. The honest trade-off: Path A gets you a line running in weeks; Path B gets you an *engine you understand* in months — and for a "mostly lights-out" system that will make irreversible production changes, that understanding is the thing that lets you sleep.

---

## 8. The specific human gates (your "mostly lights-out" posture)

You specified three gates where the human stays in the loop. Here's how each maps to a concrete mechanism:

1. **Initial production deployment.** First time a unit is promoted to production, require human approval — not just of the diff, but of the *promotion decision*. This is BCG's "human validates outcomes at stage gates" and Pulumi's "start every stack at Review mode." Mechanically: a `promotion.yaml` artifact that is blocked until `verification.yaml` passes *and* a human signs off. After the first deploy, subsequent drift/change goes dark — but the *first* promotion is a human act.

2. **Budget decisions.** Agents can spend within an envelope; only a human raises the envelope. Implement as a hard budget cap (`max_budget_usd` at the harness level *and* a factory-level spend ledger), with a human approval gate for anything that would exceed the per-unit or per-day budget. Cap retries (3/spec) and alert on per-run token spend before you hit the ceiling.

3. **Credentials the agent can't obtain via the agent-mail account.** The agent-mail (email-based credential provisioning) is a legitimate self-service channel for *low-stakes, self-issued* credentials. Anything it can't obtain there — production database creds, cloud root/IAM, signing keys, secrets that cross a regulated data boundary — stays behind a **credential broker** that issues only short-lived, task-scoped, single-repo, single-action tokens, with a human approval step for the destructive/privileged class. The rule from the security literature is unambiguous: **keep a dedicated agent secret set distinct from developer and CI secrets, and rotate anything exposed.**

The unifying principle: **darkness is per-stage, not per-unit-of-work.** The qualifier decides what runs dark; these three gates are the *defaults* that everything else must clear before it can run dark.

---

## 9. Failure modes and honest caveats

From Pulumi's "what could go wrong," which applies verbatim to any factory:

1. **The validator approves a bad change.** Mitigation is layered, not single-point: triple-run + 2-of-3 threshold + 90% aggregate gate + human audit of the first ~50 auto-applied changes + your existing policy/static checks still running *after* the validator says yes. Expect to eat one or two of these in year one.
2. **The agent gets a destructive permission it shouldn't.** Scope at the credential layer; require human approval for the destructive class; *approvals without policy* and *policy without approvals* both fail, and *both without a human kill switch* means a 3 a.m. incident has nobody to escalate to.
3. **Costs blow up.** Cap before you find out.

Additional caveats specific to this space:

- **Sycophancy and self-declared victory** are the core failure mode the wall exists to catch — but a wall is only as good as its holdout scenarios. If your scenarios are weak or leak to the generator, the whole thing reverts to theater.
- **Vendor benchmark inflation** is real and persistent; anchor model-choice decisions on independent runs only.
- **Contamination risk** on the original (non-Verified) SWE-bench is real; Verified is the number to trust.
- **Sandboxing is not review.** It limits blast radius; it does not replace secure code review, dependency verification, secret management, or release controls. Defense in depth, not a single boundary.

---

## 10. Bottom-line recommendations

1. **Decide the replay-vs-checkpoint durability model first** and enforce it uniformly — this is the fork every other decision hangs off. For a factory with irreversible production changes, lean **replay-based** (Temporal or Restate) for the audit trail, or keep your own receipt-bound claims model and make it formally replayable.
2. **Build the wall before you build anything else.** Five holdout scenarios for one small-blast-radius unit, a janky isolated evaluator, triple-run + 90% gate. Watch it for a few weeks before auto-applying anything. "The interesting work is not building the factory — it's the wall."
3. **Adopt OpenHands (CodeAct) as the worker** if you go Path A; if Path B, reimplement the loop on top of a headless CLI harness and borrow CodeAct's "agent writes Python" insight.
4. **Write `AGENTS.md` + codified knowledge (ADRs, domain models, standards) into the repo now.** It is the prerequisite that amplifies everything else.
5. **Start at Level 3–4, not Level 5.** The five-level ladder is a *path*, and the path itself forces useful work (clearer specs, named bottlenecks, deterministic gates) even if full lights-out stays out of reach — which, for most teams, it should.

---

## 11. Recommended Path A stack (ranked)

If I were stitching the factory together, the two decisions that actually discriminate are the **worker** (what does the coding) and the **orchestrator** (what guarantees the work survives and is auditable). Everything else — sandbox, gateway, eval — is commodity you swap freely. Three complete stacks, ranked.

**#1 — OpenHands + Temporal + E2B + open-weights gateway. (This is what I'd build.)**

The logic is evidence plus auditability — the two scarcest things in a dark factory.

- **Worker: OpenHands (CodeAct v3).** Strongest independent open-source coding-loop evidence — 68.4% SWE-bench Verified, versus Devin 2.0's 45.8% and Factory Droid's 58.1% — so I'm not guessing whether the loop works. MIT, self-hosted, model-agnostic: it routes cleanly through a gateway to GLM-5 (~72.8% Verified, ~75% cheaper than a managed agent). Auditability is native (every tool call, file read, and shell command logged), which is exactly what the wall and layered verification need to function.
- **Orchestrator: Temporal (replay-based).** For a system making irreversible production changes, the audit trail is the product, and replay-based durable execution yields a reconstructable event history that checkpoint-resume engines don't. The "Temporal requires deterministic code" objection is a non-issue: put the non-deterministic LLM calls inside Activities, and the workflow itself is a trivially deterministic DAG.
- **Sandbox: E2B** (Firecracker microVMs, snapshot/resume) — purpose-built for one disposable VM per agent session.

The honest cost: OpenHands is a heavier headless worker than a CLI subprocess, and Temporal is a worker fleet + server + DB to operate. I accept both because they buy what a lights-out factory can't be without — the ability to reconstruct *why* something shipped.

**#2 — Headless CLI harness (Claude Code / OpenCode / Goose) + Hatchet + Docker/gVisor.**

Wins on operational simplicity and seam cleanliness, not raw evidence.

- **Worker: a headless CLI harness** with an exit-code + diff contract — the cleanest seam between orchestrator and worker ("run this, get a diff and a pass/fail"), no platform to wrap. Goose's recipe model adds reviewable, diffable worker behavior. The catch: no benchmark evidence for the coding loop, so you carry that quality with your own prompts/recipes.
- **Orchestrator: Hatchet** — "one Postgres box is enough," the lowest operational burden of any durable engine.

Right call if you'd rather own less infrastructure and trust your recipes. For a factory that already has a receipt-bound claims/leases queue, Hatchet's marginal value is small — that custom queue already does most of it.

**#3 — OpenHands + Inngest/Trigger.dev + E2B (fastest to a running line).**

If the constraint is "live this quarter, not perfect," Inngest (or Trigger.dev v4) gives durable agent loops with no separate infrastructure — the engine memoizes completed steps and resumes after crash/deploy, serverless-native. Best DX-to-durability ratio. Ranked third because the event-driven step-function model fits "one agent runs two hours touching N tools" less naturally than Temporal's replay, and it trades away the full event-history audit trail that #1 provides.

**Bottom line:** take **#1** — OpenHands for proven, auditable coding; Temporal for a reconstructable history of irreversible changes; open-weights behind a gateway for cost without lock-in. The wall, the qualifier, and the three human gates stay custom-built in every option, because that's the part nobody ships.

---

## 12. Sources

Primary and practitioner sources (all fetched/verified October 2026):

- **BCG Platinion — "The Dark Software Factory: A New Era of Autonomous Software Delivery"** (March 2026). The canonical framing; five pillars; OpenAI/Spotify/BCG data points. [PDF]
- **Dan Shapiro (Glowforge) — "The Five Levels"** (January 2026). The autonomy ladder, as summarized in the sources below.
- **Pulumi — "The Dark Factory Pattern for Infrastructure: Running Pulumi Lights-Out"** (Engin Diri, May 2026). The four-layer architecture, the "wall," StrongDM's holdout triple-run, the four-phase rollout, and failure modes.
- **gethuman.sh — "Getting to the Dark Software Factory"** (Stephan Schmidt, May 2026). The qualifier/lane-routing framing; machine-derivable intent, observable outcomes, bounded blast radius.
- **env.dev — "AI Dark Factory Playbook" and "AI Dark Factory: Autonomous Coding Explained"** (March/May 2026). The five-level ladder and playbook.
- **darkcli.dev — "Dark Factory"** and **darkfactory.dev**. The 8-phase pipeline, typed interface contracts, agent mail.
- **0-sayed/dark-factory (GitHub)**. An OSS dark factory on Codex + Archon + Agent Orchestrator.

Benchmarks and comparisons:

- **SWE-bench / BenchLeader / LLMLearner** (October 2026) — SWE-bench Verified leaderboards (independent bash-only vs vendor-reported).
- **AkoyaSoft — "Devin vs. OpenHands vs. SWE-Agent, Tested"** (April 2026) and **ToolHalla** (March 2026) — head-to-head agent evaluation.
- **Awesome Agents — "SWE-Bench Coding Agent Leaderboard 2026"** (April 2026) — the "scaffold matters" 16-point-gap finding.
- **TrueFoundry — "Claude Agent SDK vs LangGraph"** (September 2026) and **agentnotebook.dev** (July 2026) — harness-layer comparisons; the GLM-5.2 cost finding.
- **Upstash — "Durable Workflow Engines in 2026"** (July 2026); **youngju.dev** (May 2026); **Tanay Shah — "Picking a Durable Workflow Engine for AI Agents in 2026"** (May 2026); **Cadenya** (October 2026) — the durable-execution taxonomy (replay vs checkpoint vs step-functions) and engine comparisons.
- **Inngest docs — Durable Agents** — the durable-agent-as-durable-workflow model.

Security and best practices:

- **makeyouragent.ai — "AI Agent Code Execution Sandbox"** (October 2026); **Somanath Khadanga — "Coding Agent Sandboxes: A SaaS Security Blueprint"** (September 2026); **Mikołaj Gramowski — "AI Coding Agent Security Checklist: 8 Controls for 2026"** (September 2026); **ToolHalla — "Claude Code agent-loop control surface checklist"** (June 2026); **DigitalOcean Harness Runtime docs**.
- **E2B runtime (GitHub)** — Firecracker microVM sandboxing architecture.
- **Model Context Protocol** — spec/architecture, the 2026 roadmap (Tasks extension, server-initiated events), and **AWS "MCP strategies"** guidance.
- **Agentic Engineering Agency — "Ultimate Harness" (GitHub)** and **Intense Visions — "harness-engineering" (GitHub)** — schema-backed artifact lifecycles and the "outcome-eval" ship gate.
- **pith.science — "Separating Intelligence from Execution: A Workflow Engine for the Model Context Protocol"** (2026) — the plan-once/execute-cheap result.

---

*Note on your existing stack:* this report was written vendor-neutral per your request, but the mapping is direct — your "Dev Projects" goal cascade + receipt-bound durable state is effectively the clean-room control plane already (Path B §8), and the three gates you specified map one-to-one onto §9. If you want, I can produce a follow-up that overlays this field guide specifically onto Hermes + Bifrost + DeepSeek/GLM.
