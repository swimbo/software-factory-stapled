# Phase 9 — Wall MVP (holdout evaluator)

Build the wall first, per report §10.2 and the architecture debate consensus. This phase produces a
**runnable, tested** holdout evaluator — not a stub — but deliberately leaves Restate/OpenHands/Bifrost
integration and the deterministic security pipeline for later phases.

## Scope of this phase

- 5 plain-English BDD scenarios stored where a worker cannot reach.
- An isolated evaluator that runs each scenario against a target, applies the gate, and returns an opaque
  one-line verdict + a usage receipt.
- A minimal auth **target fixture** (correct + deliberately-broken variants) to prove the gate works.
- An **isolation test** that asserts the worker namespace cannot reach the holdout store.

## Out of scope (later phases)

- Restate dispatch / OpenHands worker integration (Phase 9b+).
- Deterministic security pipeline (SAST/secret/dep/provenance) — Phase 9c (hard prerequisite to prod).
- Credential broker, destructive-op gating, qualifier.

## File layout

```
stapled-factory/
  wall/
    holdout/                     # NOT reachable by the worker (isolation invariant)
      auth_login.feature         # 5 scenarios total across one small-blast-radius unit
    evaluator/
      gate.py                    # pure gate logic (per-scenario + aggregate + one-line messages)
      receipt.py                 # UsageRecord (tokens/requests/wall_clock/tier/pricing_mode)
      evaluate.py                # runner: target + scenarios → verdict + receipt
    target/
      app.py                     # minimal auth fixture (login/logout), correct version
      broken_app.py              # deliberately-broken variant (500 on login)
    isolation/
      test_no_holdout_reach.py   # asserts a "worker" namespace cannot read holdout/
    tests/
      test_gate.py               # broken diff fails, correct passes, feedback is one-line
    README.md
```

## Task checklist

1. Write the 5 holdout scenarios (auth login success, wrong password, logout, rate-limit, session expiry).
2. Implement `gate.py` — per-scenario pass/fail; aggregate ≥90%; one-line failure message only.
3. Implement `receipt.py` — `UsageRecord` with `tokens`/`requests`/`wall_clock`/`tier`/`pricing_mode`.
4. Implement `evaluate.py` — run scenarios against a target HTTP endpoint, tally, emit verdict + receipt.
5. Implement the target fixture (correct `app.py` + `broken_app.py`).
6. Implement `test_gate.py` + `test_no_holdout_reach.py`.
7. Run the tests and confirm: broken target rejected, correct target accepted, feedback one-line, isolation holds.

## Verification (hard gates)

- [x] `test_gate.py` passes: broken diff → rejected; correct diff → accepted; failure string is one line.
- [x] Holdout isolation: the simulated test passes, and `verify_firewalled_provision.py` independently denies actual host holdout access from the real worker container (Oct 27).
- [x] The gate enforces ≥90% aggregate (a scenario at 1-of-N cannot pass on aggregate alone).
- [x] A `UsageRecord` is emitted on every verdict.

## Acceptance criteria (from the plan)

- 5 scenarios stored out of reach; gate demonstrably rejects a broken diff and accepts a correct one;
  feedback is one-line only; isolation is a *proven* property, not a config we believe.

## Dependencies

- Python 3.12+ (stdlib + pytest). No Restate/OpenHands/Bifrost yet.

## Acceptance audit — Oct 27, 2026

The updated wall suite passed 17 tests during the current continuation, including negative controls for disabled logout, expiry and rate limiting. Checked items
above reflect that local evidence only; they do not establish deployment readiness.

- The simulated isolation test alone checks only paths in a temporary directory.
  The current disposable `worker/probes/verify_firewalled_provision.py` additionally
  passed with a real worker container: actual host holdout and private-data fixtures,
  budget/anchor data and proxy socket were unreadable, while a workspace positive
  control was readable. Gateway-only network negative controls also passed. This
  establishes the tested operator-provisioned container boundary; trusted Docker
  daemon, host and image selection remain assumptions, not attested provenance.
- Restored all five planned behaviors: login, wrong password, logout, rate limiting
  and session expiry. Logout and expiry first establish a valid session and confirm
  access. Negative controls demonstrate rejection when these protections are disabled.
- The evaluator now defaults to triple-run majority. Fixture-only short TTL/window
  values and delay steps exercise actual elapsed time without production auth scope.
- This historical wall MVP plan excludes the Restate/OpenHands scope subsequently
  authorized by the user; live recovery evidence is recorded in progress.md. Completing
  that integration alone does not close the wall acceptance gaps above.
