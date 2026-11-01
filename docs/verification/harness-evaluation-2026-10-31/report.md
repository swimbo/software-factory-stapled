# Held-out harness evaluation — October 31, 2026

Phase 9d.5 tooling and the 24-run pilot are complete. **Retain the baseline/default; keep candidate profiles opt-in.** The combined profile satisfied all six written task contracts after documented read-only grading reconciliation, but this run does not establish full-suite promotion eligibility. No broader promotion, merge, publication or private wall acceptance occurred.

## Frozen evaluation

Six new archive-service tasks covered expiry boundaries, quoted CSV, parcel rounding, configuration discovery, noisy-output filtering and an unavailable dependency. Each profile received the same seed revision and task text for its task. The candidate runtime was frozen before task creation. Workers saw their prompts and seed; independent checks and known-correct fixtures remained outside worker mounts and candidate optimization. These are development checks, not an adversarial private wall.

The matrix isolates successive extensions: baseline → context-v1 → context plus bounded-native-v1 → combined stall-v1. Baseline/context order was counterbalanced; the other extensions followed in fixed stages. Context-v1 is a bundle, so individual persona/tool/skill effects are not identified. There was one repeat per task, not enough to estimate within-task variance.

[Initial registration](preregistration-initial.json) and [amended collector registration](preregistration.json) retain the source, image, seed, task/oracle digests, schedule and thresholds. Runtime source remained `3b1c3bb`, SDK 1.58.2 and worker image `569b184e` throughout. The route stayed `auto`; actual provider/served-model metadata was recorded. No model-family settings were guessed or changed.

Each attempt retained its original identity and budget: at most 12 admitted inference requests, a requested output limit of 1,024 tokens per request and a 240-second operator observation window; bounded profiles also retained their existing 300-second external deadline. The $2 reservation target per attempt is not an actual provider charge cap. No paid attempt was rerun, resumed or replaced.

## Results and grading reconciliation

All consumption, including unsuccessful attempts, is included. “Contract reconciled” is an appended assessment of the original outputs and executed actions against the task wording; it does not overwrite the frozen scores.

| Profile | Frozen strict accepted | Frozen output oracle | Contract reconciled | Requests | Input tokens per reconciled accepted task | Median workflow time |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 4/6 | 4/6 | 5/6 | 43 | Unknown | 42.865s |
| Context | 4/6 | 5/6 | 5/6 | 49 | 69,658.2 | 42.909s |
| Context + bounded | 4/6 | 4/6 | 5/6 | 48 | 66,083.6 | 41.924s |
| Combined stall | 5/6 | 6/6 | 6/6 | 48 | 53,594.5 | 28.222s |

Input totals include reported input, cached reads and cache creation. Denominators count independently accepted tasks while numerators retain all trial consumption. The candidate totals were 348,291, 330,418 and 321,567 input tokens respectively. The full baseline total remains unknown.

Two grader constraints exceeded the written dependency task. It did not forbid extra JSON fields or require the overall shell command to exit nonzero. The frozen oracle rejected permitted fields in two outputs; the strict trace predicate rejected all four genuine single imports because a trailing status print made the shell exit zero. Read-only archive checks and an independent review of exact executed command hashes plus paired gateway tool outputs confirmed all four diagnostic contracts. No reasoning/message content was exported, and no model or tool execution was repeated.

The [frozen comparison](comparison-frozen.json), [contract reconciliation](contract-reconciliation.json), [required-field audit](output-contract-audit.json), [diagnostic evidence](supplemental-diagnostic-compliance.json) and [reconciled comparison](comparison-reconciled.json) remain separate. Original SDK/root completion, scoped revisions and archived bytes were preserved.

The CSV task reached no verified completion within 12 requests/240 seconds under context and bounded profiles. Baseline completed it in nine requests; combined completed it in eight. **No host diagnostic interventions occurred in this suite**, so the combined success cannot be credited to stall recovery. Its two-second in-call observation also differs from ordinary 30-second orchestration reconciliation; part of the workflow-latency contrast is observation cadence, not inference acceleration.

## Two collector canaries within the 24 runs

The first baseline sample failed because collector `52e64d6` incorrectly treated ordinary asynchronous `needs_reconciliation` as terminal and fenced an active original conversation after 9.823 seconds. Both gateway requests completed afterward, but their wire-end records were lost. The failed sample, two requests and $0.093990 estimated reservation remain included; its original reservation and fenced namespace were retained.

A new regression was observed failing before the one-line correction. Independent validation reproduced that failure, then verified corrected collector `7c5c506`. Candidate runtime, images and tasks were unchanged. The next already-frozen context attempt served as the second scoped canary: independently accepted, seven requests with complete accounting, stopped originals and restored binding. The remaining 22 attempts then ran once. [Failure analysis](canary-failure-analysis.json) and [fix review](collector-fix-review.json) preserve this collector epoch change; it is not a clean same-collector comparison for the first pair.

## Accounting, routes and promotion decision

The pilot consumed **188 routed requests and $10.727500 in estimated reservations**. Actual charges are unknown. Twenty-three trials, covering 186 requests, have complete wire/gateway/reservation and token/cache parity. The first sample has two successful canonical gateway rows but incomplete wire coverage; gateway-only usage is not substituted into the frozen primary metric. All failures remain in totals.

Observed routes were `ollama-flash-high` and `ollama-flash-low` serving the reported DeepSeek V4.1 Flash model. Baseline/combined route sets match in three pairs and differ in three. In the descriptive matched-route stratum, input tokens per accepted task were 86,599.7 → 55,630.0, 35.8% lower; requests were 24 → 25. This subset does not replace the full-suite primary, omit failed consumption from total reporting or prove identical model weights/settings. Both route strata are retained in the comparison JSON.

The prespecified primary was input tokens per accepted task, requiring at least 10% improvement, no acceptance decline and median latency no more than 10% worse, plus the safety/accounting gates. **Full-suite primary improvement cannot be established** because baseline usage is incomplete. Authoritative truncation coverage is unavailable from the pinned SDK contract. Route differences, the collector epoch change, post-run grader reconciliation and six tasks without replication further limit causal conclusions. Zero interventions/observed stall false positives do not establish recovery efficacy or a population false-positive rate. No cache-rate or actual-dollar saving is claimed.

All [decision receipts](decision.json), including adjacent-profile comparisons, retain the baseline. Before broader qualification: align grader predicates with visible task contracts, retain private executed-action evidence before temporary SDK state disappears, and run a fresh held-out cohort with complete telemetry and adequate replication. This scored set is retired from future untouched qualification.

## Verification and preservation

Exact operator source `7c5c506` passed **653 Python tests plus 12 subtests, 25 Node tests**, secret scanning, dependency audit and build. Full review found five issues that were fixed and independently validated. The later collector correction was separately reproduced and verified. An earlier exact run of `52e64d6` hit an unchanged Unix-socket test's broken pipe; its failed receipt is retained alongside the isolated pass and same-revision successful rerun. It was not erased or fixed by weakening the test.

Final checks verified 74 prior WorkUnit states and all 348 prior volume identities unchanged, every original trial lifecycle stopped, and all trial volumes/workspaces retained. Twelve bounded workspaces retained 414 hashed SDK checkpoint files. Baseline/context kept reduced event metadata before their original temporary SDK storage disappeared; full checkpoint retention is not claimed for them. Only the 24 owned, empty trial networks were released after identity/stopped-state checks. Original unresolved reservations remain reserved and fenced; completed attempts remain completed. No ledger reset/refund, successor or redispatch occurred.

All 249 signed inputs still match, the exact prior service binding is restored, no operator-active marker remains, and authenticated loopback health checks pass on existing runtime `3b1c3bb`. Shared Restate/Bifrost services were not restarted. Defaults remain unchanged. [Preservation](preservation.json), [health](health-final.json), [coverage](coverage.json) and the review receipts bound these claims.

Reproduce both metadata comparisons with `python3 docs/verification/harness-evaluation-2026-10-31/compare.py`. Hidden oracle answers, raw model thoughts and credentials are excluded from this evidence directory. Code and evidence remain local on `codex/phase9d-native-batching`.
