# Next Steps — handoff for a non-sandboxed harness

State as of **2026-10-17**. The end-to-end loop has been demonstrated live once (unit-005 → `merged`).
This doc is the continuation point. Companion docs: `progress.md` (full log), `architecture.md`,
`consensus-plan.md`, `phase-9-plan.md`, `tech-guide.md`.

## 1. What exists and where

```
/Users/bo/code/software-factory-stapled/stapled-factory/
├── wall/            # The wall — holdout evaluator (Python, verified)
│   ├── holdout/scenarios.json          # 5 BDD scenarios the worker must never see
│   ├── evaluator/{gate,receipt,evaluate,config,cli}.py
│   ├── target/{app,broken_app}.py      # correct + deliberately-broken auth fixtures
│   ├── config.json                     # per-tier pricing_mode + ceilings
│   ├── isolation/ tests/ README.md
├── security/        # deterministic boundary (Python, verified)
│   ├── firewall.py                     # effect-based destructive-op firewall
│   ├── broker.py                       # short-lived task-scoped credential tokens
│   ├── pipeline.py                     # FULL SAST: Semgrep/Gitleaks/OSV/Cosign + regex fallback
│   ├── pipeline_cli.py  tests/ README.md
├── worker/          # worker adapter (Python)
│   ├── worker.py                       # drives OpenHands Agent Server (SDK calls UNVERIFIED)
│   └── guard.py                        # wraps tool calls: firewall + broker
├── spine/           # Restate orchestration (TypeScript, verified)
│   ├── src/{state,approval_store,gate,orchestrate,wall,build,security_scan,types}.ts
│   ├── scripts/{invoke,approve}.ts     # plain-HTTP ingress clients
│   ├── package.json  tsconfig.json  dist/  README.md
└── fixtures/unit-002.task.md

docs/planning/stapled-factory/          # all planning artifacts (this dir)
/Users/bo/code/software-factory-stapled/dark-software-factory-report.md   # the source research
```

## 2. Verified run-book (these exact commands worked)

```bash
# One-time: install the server + CLI via npm (brew is blocked on macOS 26 CLT)
npm install --global @restatedev/restate-server@latest @restatedev/restate@latest

# 1. Restate server  (terminal A)
restate-server                                   # ingress :8080, admin :9070

# 2. Build + run the spine  (terminal B)
cd ~/code/software-factory-stapled/stapled-factory/spine
npm install && npm run build                     # tsc → dist/
npm run start                                    # node dist/main.js  → :9080

# 3. Auth target for the wall  (terminal C)
cd ~/code/software-factory-stapled/stapled-factory && python3 wall/target/app.py   # :8777

# 4. Register (use --force for a NEW revision)  (terminal D)
restate deployments register http://localhost:9080 --force

# 5. Invoke one unit
cd spine && npm run invoke -- unit-100 http://127.0.0.1:8777 \
  wall/holdout/scenarios.json ~/code/software-factory-stapled/stapled-factory/fixtures/unit-002.task.md \
  ~/code/software-factory-stapled/stapled-factory
#   ^ <unitId> <wallTarget> <scenarios> <taskFile> [targetRepo]

# 6. When the run parks at the human gate, approve it
npm run approve -- unit-100:deploy approved <your-name>
```

**Debug surface (all `restate` CLI):**
`invocations ls [--all]` · `invocations describe <id>` · `invocations journal <id>` ·
`state get WorkUnit <unitId>` (authoritative stage/claim/receipt) · `services ls` ·
`deployments register --force` · `invocations kill <id>`.

## 3. Verified vs. unverified (be honest with yourself here)

| Piece | Status |
|---|---|
| Spine TS type-checks vs `@restatedev/restate-sdk@1.17.3` | ✅ clean |
| Full loop: invoke → claim → build → wall → scan → **gate suspend** → approve → `merged` | ✅ ran live |
| Wall gate (quorum, ≥90%, one-line feedback) + isolation | ✅ 7/7 + isolation |
| Firewall + broker | ✅ 12 assertions |
| Security pipeline parsers (Semgrep/Gitleaks/OSV JSON) | ✅ 10/10; **Gitleaks verified live** |
| Semgrep `--config auto` | ✅ ran live (caught `eval` + hardcoded key) |
| OSV-Scanner | ✅ engaged (needs a real lockfile to return vulns) |
| **Cosign provenance** | ⬜ not wired (needs an image + expected identity) |
| **OpenHands Agent Server** | ⬜ `worker.py` SDK calls (`Conversation`/`Workspace`/`Agent`) unverified against the installed SDK; currently returns `skipped` + git rev |
| **firewall/broker hooked into OpenHands' internal tool execution** | ⬜ guard wraps our own calls only; OpenHands' own commands are unhooked |
| Inception engine / qualifier / dashboard / Postgres audit | ⬜ not built |

## 4. Known bugs & loose ends (fix before they bite)

1. **`tier: "unknown"` in receipts.** `invoke.ts` doesn't pass a tier/config, so ceilings + cost accounting
   never engage. Thread a tier through `OrchestrateRequest` → `runWall` (`--config wall/config.json --tier …`).
2. **Security-scan scope is a stub for real diffs.** `security_scan.ts` does `git show <rev>` in
   `targetRepo`, but Semgrep/Gitleaks scan a *directory* — so once OpenHands produces real diffs you must
   scope them to the changed paths (or `--target` the repo) rather than scanning a diff string. Today a
   no-diff unit correctly skips (with an `engines.note`).
3. **Lease expiry uses `Date.now()`** (`state.ts`) — non-deterministic. Use a durable `ctx.sleep` timer.
4. **Invoke POST blocks** until the workflow completes/gate; the client's undici fetch caps at 300 s. Prefer
   fire-and-forget submit + poll `invocations ls`, or raise the client timeout.
5. **Approval store is Restate-only** — no Postgres `ApprovalGate` audit table yet.
6. **`restate deployments register` needs `--force`** to pick up a rebuilt spine — a plain register is a
   silent no-op ("nothing was changed"), which cost us a debugging round.
7. **`restate invocations submit` does not exist** — the only invocation path is a plain HTTP POST to the
   ingress (`/:8080/<workflow>/<key>/<handler>`, `/:8080/<service>/<handler>`). SDK 1.x has **no client
   `connect()`**. (Both already fixed in `scripts/invoke.ts` / `scripts/approve.ts`.)
8. **Sandbox-specific runbook traps** (irrelevant off-sandbox; listed so they don't confuse anyone reading
   the old logs): `app.py` "Address already in use" was a stale/time-wait phantom — `lsof -nP -iTCP:8777`
   is the truth; the old spine must be killed before rebinding :9080.

## 5. Prioritized next work

**A. OpenHands Agent Server (turns `build` real).** Replace the `worker.py` stub: run `Conversation` +
`Workspace` against `openhands-agent-server` (session-key auth), collect the diff, return the revision.
Then (a) scope the security scan to the changed files, (b) register `worker/guard.py` on OpenHands'
command-execution/confirmation hook so the firewall + broker actually gate the agent's tool calls.
*This is the biggest gap — the whole loop is exercised, but the "build" step has never produced a diff.*

**B. Qualifier + Inception engine.** The two designed-but-unbuilt components. Qualifier = deterministic
issue scoring → dark-lane vs human-lane. Inception = 3yr goal → yearly/quarterly OKRs (human approves) →
monthly epics (hybrid proxy: deterministic gate + LLM judge) → work packages. See `architecture.md` §5, §11.2.

**C. Wire the other three human gates.** `budget_raise`, `destructive_op`, `credential_issue` are declared
in `GateType` and emitted by `guard.py`, but only `initial_prod_deploy` is exercised in the workflow.

**D. Dogfood (Phase 12).** Point the factory at a fresh fullstack app (from `~/.claude/templates/fullstack`)
and drive one real intent → merged PR.

**E. Hardening.** Postgres `ApprovalGate`/`BudgetLedger` audit + dashboard (fullstack template); durable
lease timer; tier/cost accounting end-to-end; Cosign provenance.

**Open planning item:** the Tech Guide (Phase 6) debate was deliberately deferred (the volatile forks
were already argued at the architecture level). If stack choices are revisited, run `agents-argue:debate`
on `tech-guide.md`.

## 6. Non-obvious constraints (why things are the way they are)

- **Sandbox limits that shaped the build** (this harness): no outbound network (pypi/npm/brew), no local
  port binding, no loopback TCP connect, cert/trust-store access blocked, `~/.semgrep` / `~/.cache` writes
  blocked. Everything server-ish therefore ran in the *user's* terminal; the sandbox could only type-check,
  parse tool JSON, and verify pure logic. A normal (non-sandboxed) harness loses all of these limits.
- **Restate is durable-replay**: killing/restarting the spine never loses an invocation — it resumed a
  suspended workflow across a restart during the demo. Lean on that in testing.
- **The wall is a quality gate, not a security boundary** (the debate's central finding). Security lives in
  `security/`. Don't conflate them. See `consensus-plan.md`.
