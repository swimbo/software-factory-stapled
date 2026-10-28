# Security boundary — credential broker + destructive-op firewall

The two controls the adversarial debate said were missing, and that close the parked decisions
(#2 trust anchor, #3 effect-vs-intent). Both classifiers are **deterministic — no LLM in the loop.** They are standalone primitives; the current OpenHands Agent Server terminal path does not enforce them for every tool call. Do not grant that path privileged external credentials or production effects on the strength of these classifiers.

## `firewall.py` — destructive-op firewall (effect-based)

Classifies a worker's tool call by **what it does**, not what any model says it intends. Calls explicitly routed through `worker/guard.py` are checked *before* execution:

- `allow` — safe (e.g. `SELECT`, `DELETE ... WHERE`).
- `approve` — dangerous but legitimate (e.g. `DROP TABLE`, `DELETE` without `WHERE`, `rm -rf`, `chmod`),
  → routed to the spine's human `destructive_op` gate.
- `block` — deny-by-default (unknown op types).

## `broker.py` — credential broker (short-lived, task-scoped)

The agent never sees the long-lived **master key** (the root of trust). It holds only short-lived tokens:

- `issue(claim_id, repo, action, ttl)` → single-action, single-repo, task-scoped token, HMAC-signed.
- `verify(token, action)` → checks signature, expiry, and single-action scope.
- `verify_and_bind(token, action, claim_id, repo)` → re-binds the token to the originating claim — the
  anti-injection guard: a prompt-injected instruction can't repurpose a token mid-trajectory.

**Trust anchor (decision #2, default):** `FACTORY_MASTER_KEY` in the environment, outside the agent loop.
Swap for a secret store / KMS without changing the broker's API.

## How it wires into the spine

The standalone `worker/guard.py` wrapper calls `firewall.check(...)` and uses `broker.issue(...)`.
The current real OpenHands terminal path is not wired through this wrapper. A wrapper `firewall` verdict of `approve`
and a `broker` credential for a privileged action both route through the spine's human gates
(`gate.request(..., "destructive_op" | "credential_issue")`). Full tool mediation remains a separate production gate; connecting `build.ts` to the Agent Server does not establish that enforcement.

## `pipeline.py` — full deterministic security pipeline

The committed prerequisite for security-relevant code to run dark (tiered dark). Non-LLM; orchestrates
real engines and records which one ran (so a missing engine is never silently presented as full SAST):

| Check | Engine | Fallback (if absent) |
|---|---|---|
| SAST | **Semgrep** (`--config auto --json`) | regex SAST-lite |
| Secrets | **Gitleaks** (`dir --report-format json`) | regex secret patterns |
| Dependencies | **OSV-Scanner** (`scan <lockfile> --format json`) | manifest-change flag |
| Provenance | **Cosign** (`verify --certificate-identity ...`) | (unchecked) |

Install the engines to get full SAST (all run locally):

```bash
brew install semgrep gitleaks osv-scanner cosign    # or the equivalent for your distro
```

Verdict: `pass` (run dark) · `needs_review` (security-relevant / medium → human `security_review` gate) ·
`fail` (secrets / critical → block merge). CLI: `pipeline_cli.py` (reads a diff, prints JSON incl. `engines`).

## Verify (stdlib, no deps — runnable in-sandbox)

```bash
cd security && python3 -m pytest tests -q     # or run the assertions inline
```
