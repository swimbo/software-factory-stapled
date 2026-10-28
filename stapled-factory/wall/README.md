# The Wall — holdout evaluator (MVP)

The first thing built in the dark factory, per the report §10.2: an isolated evaluator that runs
**holdout acceptance scenarios the generator cannot see**, and gates the result.

This MVP is deliberately standalone — no Restate/OpenHands/Bifrost yet. It proves the gate mechanics and
the isolation property; the deterministic security pipeline and worker integration come in later phases.

## Layout

- `holdout/scenarios.json` — 5 plain-English BDD scenarios for a small auth unit. **Not reachable by the worker.**
- `evaluator/` — `gate.py` (quorum + aggregate + one-line feedback), `receipt.py` (usage record), `evaluate.py` (runner), `config.py` (tier pricing + ceilings).
- `config.json` — per-tier pricing mode (`subscription` | `metered`) and ceilings.
- `target/` — a correct (`app.py`) and a deliberately-broken (`broken_app.py`) auth fixture, to prove the gate.
- `isolation/` — a test asserting the holdout is unreachable from a worker namespace.
- `tests/` — unit + end-to-end tests.

## Run the tests

```bash
cd wall
python -m pytest tests isolation -q
```

## What the gate enforces

- **Per-scenario quorum** — 1-of-1 (single-run, MVP); 2-of-3 at the prod gate (`required_majority`).
- **Aggregate threshold** — ≥90% of scenarios must pass.
- **One-line feedback** — on failure, a single line naming the failing step (e.g.
  `auth-login-success: POST /login returned 500 (expected 200)`), never the scenario text.
- **Usage receipt** — every verdict carries tokens/requests/retries/wall-clock/tier/`pricing_mode`
  (`subscription` or `metered`), for runaway detection, not dollar accounting.

## Dual pricing mode (per-token OR subscription)

`config.json` declares each tier's `pricing_mode` and ceilings. The same factory runs in either mode — a
config change, not a code path:

- **`subscription`** (z.ai/ChatGPT): enforces quota/time ceilings (`max_tokens`, `max_retries`,
  `max_wall_clock_ms`). Dollar ceilings are ignored — marginal cost is $0.
- **`metered`** (per-token API): enforces the above **plus** `max_cost_usd` on the receipt's
  `cost_estimate_usd`.

`evaluate(..., tier_config=...)` sets `receipt.ceiling_violation` to a one-line message when a ceiling is
exceeded — the orchestrator treats a non-empty `ceiling_violation` as a hard stop.

## Manually exercise the wall

```bash
# in one shell: run the correct target
python target/app.py
# in another: evaluate it (should PASS)
python -c "import sys; sys.path.insert(0,'evaluator'); from evaluate import evaluate; v,r=evaluate('http://127.0.0.1:8777','holdout/scenarios.json'); print(v.passed, v.failure_message, r.to_dict())"
```
