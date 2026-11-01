# Context-v1 deployment and 18 paired trials — October 30, 2026

The deployed `context-v1` candidate completed its paid canary and **18/18 scheduled
candidate trials**. Independent contract-v2 acceptance: **18/18**,
versus the baseline's corrected **18/18**. Phase 9d.3 has not started.

The prespecified primary metric—total prompt tokens including cached input per
accepted task—improved by **44.33%**. Median
workflow latency changed by **-0.04%**.
Numeric pilot thresholds passed: **True**.
Scoped evidence/accounting/model-matching gate passed: **True**.

## Paired results

| Metric | Frozen baseline | Context-v1 |
| --- | ---: | ---: |
| Contract-corrected acceptance | 18/18 | 18/18 |
| Total prompt tokens, including cache | 1,088,401 | 605,900 |
| Uncached input tokens | 79,889 | 70,066 |
| Cached-read tokens | 1,008,512 | 535,834 |
| Output tokens | 19,714 | 20,392 |
| Worker requests | 107 | 103 |
| Requests per accepted task | 5.944 | 5.722 |
| Median workflow elapsed | 44.451 s | 44.433 s |
| Median agent event span | 12.184 s | 8.198 s |
| Reported cached-read share | 92.66% | 88.44% |
| Estimated reservations | $7.610171 | $4.843708 |

All trial consumption is included, including any failed/exhausted attempts; no
model trial was rerun or budget reset. Estimated reservations are admission
estimates, **not actual charges or verified dollar savings**. Actual billing is
unknown. Workflow latency includes provisioning/reconciliation polling; agent
event span is reported separately.

| Task | Baseline / candidate accepted | Baseline requests (r1,r2,r3) | Candidate requests (r1,r2,r3) | Baseline / candidate prompt tokens |
| --- | --- | --- | --- | --- |
| discovery | 3/3 / 3/3 | 6, 5, 7 | 7, 5, 6 | 185,045 / 108,904 |
| small-edit | 3/3 / 3/3 | 6, 6, 7 | 6, 6, 6 | 196,518 / 106,037 |
| multi-file | 3/3 / 3/3 | 5, 5, 7 | 5, 5, 5 | 172,816 / 85,578 |
| failing-tests | 3/3 / 3/3 | 6, 6, 6 | 6, 6, 6 | 183,186 / 109,548 |
| noisy-output | 3/3 / 3/3 | 6, 6, 6 | 7, 6, 5 | 181,680 / 106,482 |
| stalled-run | 3/3 / 3/3 | 6, 6, 5 | 6, 5, 5 | 169,156 / 89,351 |

Every pair uses the same seed revision
`e25eb19801c9a82b5255ecaccc35ce6e0677e91f`, byte-identical task text, allowed paths,
round-robin order, 12-request admission limit, 1024 output-token limit, $2 estimated
per-attempt target and unchanged shared `auto` routing policy. `preregistration.json`
was written before paid runs. The primary metric was not selected after results.

Baseline grading uses the preserved contract-corrected v2 **18/18** score; the
original frozen **17/18** score remains intact. Candidate checker executable AST
matches that same v2 checker; the sole source difference is its module docstring.
The same executed-snippet audit verifies stalled-run diagnostics rather than
counting quoted heredoc data as execution. See `checker-equivalence.json` and
`diagnostic-audit.json`. No generated candidate or historical baseline was edited.

## Routing, cache coverage and uncertainty

Actual gateway model identifiers match across every pair:
**True**. Exact provider-lane sets match across
all pairs: **False**. Provider/model/served
model metadata and separate per-lane request/token totals are retained in
`comparison.json` and `final-state-and-gateway.json`. Shared model identifiers do
not independently attest to identical weights or inference settings across lanes.
Changes in lane distribution are a limitation on attributing improvements solely
to prompt/schema reductions. Eleven of 18 pairs had the same provider-lane sets;
their descriptive totals are also retained, without excluding any pair from the
primary comparison.

Complete candidate wire usage coverage: **103/103**.
Gateway request counts and total prompt tokens reconcile with retained proxy
telemetry and budgets: **True**.
Unknown/missing usage would stay unknown and fail the evidence gate. Cache share
uses reported input + cache reads + cache creation; it is not an SDK-derived rate,
universal eligibility threshold or billing statement. Cached-read share fell from
92.66% to 88.44%; no cache-rate improvement is claimed. The primary result is
context reduction, measured including cached tokens. Uncached input also fell
12.30%, from 79,889 to 70,066 tokens.

This is a historical paired development comparison: six tasks with three repeats,
not a randomized concurrent or held-out production evaluation. The paid canary
warmed the candidate prefix before the 18 trials; the primary metric includes
cached tokens to avoid calling warm-cache effects uncached-token savings.
The exploratory six-task cluster-bootstrap interval for primary improvement is
**[0.4162394225534415, 0.47375078234211676]** (fractions;
small-sample description, not a causal/significance claim). The combined profile
was evaluated; individual mechanism attribution remains untested. None of the 18
paired candidates invoked factory_capture; capture/pagination was validated in
the separate canary, so these paired metrics do not quantify that mechanism.

## Deployment and canaries

- Deployed code/release: `850259ae877e0cb171310e95d11719d9882e879e`.
- New worker image: `sha256:d1d11b40384c2eb25d5d9d38a069abed9801a0a290c64101a24cc200a8a26e30`.
- Derived from accepted worker `026fbb4d…` with only the two reviewed capture modules
  and `OH_EXTRA_PYTHON_PATH`. Base layers and package versions are unchanged;
  source/image hashes and exact environment binding are retained. No dependency
  or shared gateway rule was added. Existing sandbox security residuals remain;
  this is not production promotion or exhaustive vulnerability assurance.
- Verified 998 immutable release files. Prebuilt Node runtime bytes were reused
  from the prior accepted release only after manifest verification and a zero
  source/lockfile diff. Exact-code CI previously passed 480 Python tests plus
  12 subtests, 25 Node tests, build, audit and secret scanning.
- Updated endpoint/ingress only. Deployment preserved the guarded DB/anchor and
  29 prior WorkUnit states. Shared gateway, database and Restate services were not
  restarted. Final preservation/health evidence is retained separately.

The initial dummy-credential canary passed startup and real confinement negatives
(nonroot, host paths/stores/socket denied, TCP/DNS denied, root write denied,
workspace writable). Its zero-request expectation was wrong: AgentServer creation
with `run=false` still starts LLM helper work. Two requests received gateway HTTP
401 with the dummy key; no real gateway credential was released. Its original
reservations, attempt and volumes remain retained. Both containers were stopped
and only its empty, label-verified network was removed. This was not silently
refunded, reused or counted among paid model trials.

The separately identified **paid canary passed** through normal Restate dispatch,
custom-module loading and independent exact-clean-revision checks. It executed
`factory_capture` capture followed by a nonzero-offset page read, recovered the
final marker and retained a complete 5,019-byte redacted artifact with verified
SHA256. It used **6 successful Flash requests**, **$0.305033 estimated reservations**;
actual charges are unknown. It is separate from the 18 paired candidate trials.
Its prior binding was restored and containers stopped before the paired suite.

## Retention, rollback and decision

Every paired trial's original worker/proxy was stopped; its empty, ownership-checked
network was released. Containers, workspaces, candidate archives and all bound
budget/anchor/telemetry volumes remain retained. No shared or historical network
was pruned. The experimental collector exported authorized redacted capture
artifacts **before** worker tmpfs disappeared; ordinary factory artifact export
is unchanged. Private source/tool action traces contain no model-thought stream.

All previous task service bindings are restored and the operator-active marker is
absent. Current factory listeners remain loopback and reject unsigned/unauthenticated
requests. The new release/profile remains available as an opt-in sandbox candidate;
the original default task binding is preserved. Rollback never rewinds ledgers,
reuses an attempt or replays a completed effect.

**Decision:** candidate meets scoped pilot thresholds; retain opt-in activation pending broader promotion gates and review provider-lane distributions.
Full Phase 9d.5 promotion remains incomplete: broader held-out evaluation and
individual-mechanism attribution were not performed. No push, merge, default-profile
switch or production promotion was performed. **Phase 9d.3 remains untouched.**

Evidence: `deployment.json`, `image-binding.json`, signed image approvals,
`preregistration.json`, `paid-canary.json`, `unpaid-canary.json`, original `trials/`
and `telemetry/`, `comparison.json`, `final-state-and-gateway.json`,
`final-preservation.json`, `health.json`, and the recorded operator scripts.
