# Wall request accounting factory run — 2026-10-30

The wall receipt counted configured request steps, including steps skipped after
an early failure. A real local HTTP check reproduced 3 requests reported as 6.
The candidate counts each attempted request, accumulates counts across repetitions
in ScenarioResult, and sums them into UsageRecord. Verdict/quorum behavior stays
unchanged; existing ScenarioResult constructors keep working.

## Real factory outcome

The existing NUC Restate/OpenHands integration produced worker commit
`5a403f4dd1d713cd806edf81b80d67b911110bcc` from baseline
`34ab4c8e3211ee7749f4b16417a64d42cfea7652` through the live Bifrost/complexity-router
path. Worker status was `completed`, naturally, using **12/12 requests**. No
manual runtime recovery, successor, allowance reset, or extra model run occurred.
The observer exported the committed result, stopped this allocation, and restored
the previous endpoint binding. Original bundle, result and ledgers remain intact.

The original worker regression incorrectly expected six requests for mixed
200/500/200 repetitions. Independent review confirmed the actual count is five
(2 + 1 + 2). An operator follow-up commit corrects only those two assertions;
this is a test correction, not natural worker verification success.

Estimated reservation was **$1.399713** against a $2 best-effort target. Actual
charges are unknown and were not hard capped. Machine-readable metrics are in
[run.json](run.json).

## Verification boundaries

Four operator-owned acceptance cases exercise a real ephemeral HTTP server:
early failure, later failure, mixed repeated outcomes plus delay steps, and
all-success counts. Three fail on the original baseline; all four pass with the
factory production changes. The corrected focused E2E/gate suite passes all 16
tests. Independent review checks the final source and tests; exact committed
local CI is required before publication. Private operator receipts retain the
external check, review and exact-CI logs.

Restate `needs_validation` means repository completion has no separate signed
application validation. These local checks do not establish deployment or new
production isolation/recovery guarantees. Merge and deployment remain operator
controlled; neither is performed by this PR.
