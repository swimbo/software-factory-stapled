# Telemetry deployment and 18 baseline trials — October 30, 2026

Telemetry is deployed on the NUC. All **18 real model trials** completed on the same frozen worker profile. The original evaluator scored **17/18**; a contract-corrected, independently reviewed re-evaluation of all saved candidates scored **18/18**, with **zero model reruns**. Phase 9d.2 has not started.

## Deployment and scope

- Frozen release: `974aece75d62644b35b5f4e51928bb61cce140e2`, built on current upstream `762ff53`. Its local Git branch is `codex/phase9d-telemetry-release`; no merge or push was performed.
- Verified 917 release files, signed runtime inputs, pinned proxy image/source binding and unchanged base-layer/package inventory. The source/image change adds no dependencies. Existing documented sandbox vulnerability residuals remain; this is not production promotion.
- Full exact-revision local CI passed: 456 Python tests plus 12 subtests, 25 Node tests, build, dependency audit and secret scanning. The later operator-grader regression also passed its six focused tests.
- An unpaid canary verified proxy-only telemetry storage, directory `0700`, log `0600`, worker network/host-path denials, original allocation binding, firewall checks and accounting from stopped volumes. It made zero model requests.
- Only factory endpoint/ingress services were updated. Shared gateway/database services were untouched. Ten pre-existing WorkUnit states remain unchanged.
- Seed revision: `e25eb19801c9a82b5255ecaccc35ce6e0677e91f`. Six tasks ran three times each, in a frozen round-robin order, with the existing router, 12-request admission limit and $2 best-effort estimated target per trial. Actual charges are not capped by these estimates.

## Results

| Task | Frozen evaluator | Contract-corrected audit | Requests in rounds 1, 2, 3 |
| --- | --- | --- | --- |
| discovery | 3/3 | 3/3 | 6, 5, 7 |
| small-edit | 3/3 | 3/3 | 6, 6, 7 |
| multi-file | 3/3 | 3/3 | 5, 5, 7 |
| failing-tests | 3/3 | 3/3 | 6, 6, 6 |
| noisy-output | 3/3 | 3/3 | 6, 6, 6 |
| stalled-run | 2/3 | 3/3 | 6, 6, 5 |
| **Total** | **17/18** | **18/18** | **107 worker requests** |

- All 107 proxy admissions match retained budget counters and independent gateway log rows. All wire responses have complete usage observations. Every routed request was served by `deepseek-v4.1-flash:1017` through the existing Bifrost route.
- Gateway-reported prompt tokens: **1,088,401**; completion tokens: **19,714**. Prompt accounting reconciles exactly to **79,889** wire input tokens plus **1,008,512** cached-read tokens and zero cache-creation tokens. The gateway-reported cached-read share is **92.66%**; it is not an SDK-inferred cache percentage or a billing claim.
- Median observed workflow elapsed time: **44.45s**. Median agent event span: **12.18s**. Workflow elapsed includes provisioning and durable reconciliation polling; it should not be attributed entirely to model execution.
- Corrected requests per accepted task: **5.944**, including consumption from every trial. Both frozen-score and corrected-score denominators are preserved in `summary.json`.
- Estimated reservations: **$7.610171**. Actual charges remain unknown, including classifier/fallback/provider billing differences.

## Evaluator correction, without changing generated results

The final stalled-run candidate supplied the required `status: blocked` and `dependency: factory_missing_dependency`, plus explanatory fields. The task never prohibited extra fields, but the original checker used whole-object equality. Separately, the trace counter matched the words `import factory_missing_dependency` inside a quoted JSON heredoc and counted that documentation as execution.

The retained executed command and observation records show one actual import, a genuine `ModuleNotFoundError`, and no installation commands in each of the three stalled runs. Independent review confirmed both defects and the contract interpretation. The corrected checker still rejects wrong status, wrong dependency and missing required fields. A parsed snippet audit distinguishes real imports from quoted data; it is an audit of these recorded samples, not a general shell security proof.

All 18 immutable candidate exports were checked again with the same corrected rules in networkless, read-only containers. No worker was restarted, no generated candidate was edited and no model call was repeated. The original trial JSONs and 17/18 score remain intact; `regrade-v2.json` appends the corrected verdicts. Use the v2 contract grader and executed-trace audit for subsequent comparisons, rather than treating the frozen substring counter as authoritative. These are measurement corrections, not a harness performance improvement.

## Preserved pre-dispatch infrastructure failure

Original launch `h9d-20261030-failing-tests-r1` encountered Docker bridge address-pool exhaustion before allocation binding and credential release. There was no worker/proxy container or network for that allocation and no gateway request for its trusted session. Its reserved attempt, retained empty volumes and `needs_reconciliation` WorkUnit remain preserved; no budget or attempt was reset.

After a signed no-dispatch proof and safe service-binding restoration, a separately signed `...-r1-p2` unit ran the same seed, task text and profile. It supplies the one actual model trial for that slot. Thus there are 18 model trials plus one preserved pre-model launch failure, not 19 model trials.

The batch wrapper now releases only this run's empty networks after checking that their bound containers are stopped. Containers, workspaces, candidate exports and all budget/anchor/telemetry volumes remain retained. No shared or historical network was pruned. This wrapper compensation does not add general network garbage collection to the ordinary operator workflow.

## Final state and evidence

All 18 bound worker/proxy pairs are stopped, their trial networks are released, all bound evidence volumes are retained, every prior service binding is restored and the operator-active marker is absent. The telemetry release remains deployed. Phase 9d.2 remains on hold.

- `summary.json`: raw and corrected acceptance counts, full token coverage, both accepted-task denominators and latency measures.
- `trials/` and `telemetry/`: original trial receipts and payload-free request observations with verified hashes.
- `final-state-and-gateway.json`: independent route/token metadata and stopped-resource identities.
- `regrade-v2.json`, `grader-correction-tests.json`, `diagnostic-audit-v2.json`: corrected offline evaluation and regression evidence.
- `stalled-action-audit.json`, `stalled-observation-audit.json`: relevant synthetic-task commands and actual observations; no model thoughts or credentials.
- `deployment.json`, `unpaid-canary.json`, `local-ci.json`, `image-source-binding.json`, `final-preservation.json`: deployment and boundary evidence.
- Signed approvals, replacement record and pre-dispatch failure/closure proofs preserve the exceptional launch history.

This is a six-task development baseline with three repetitions per task. Small sample size, shared infrastructure, dynamic routing policy and warm-cache/order effects limit generalization. It establishes neither production acceptance nor a vendor benchmark result. Preserve this dataset before any Phase 9d.2 context/cache changes; apply the same corrected grading to baseline and candidates.
