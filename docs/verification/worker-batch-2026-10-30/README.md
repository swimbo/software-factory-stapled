# Batching worker round trips — 2026-10-30

One appended task paragraph asks the worker to batch independent reads/searches
and combine final checks, the scoped commit and final status, stopping on any
failure. Required inspection, verification and the offline restrictions remain.
No request allowance or budget policy changes.

## Trace and replay

The original wall request-count task completed at 12/12 requests. Its event trace
shows four pre-edit reading/search responses, three edit responses, one later
readback, separate final-check and commit responses, then a final answer. There
are 11 visible model responses; the proxy ledger records 12 requests and is the
authoritative request counter. No unsupported explanation for that difference
is assumed.

The replay retains the original task text byte-for-byte, appending only the
new paragraph. Its generator baseline remains `34ab4c8`; all three allowed
paths, acceptance criteria, worker images and 12-request allowance are unchanged.
The old baseline exception exists only in an operator experiment adapter pinned
to this exact unit/SHA, with historical-output publishing disabled. The production
CLI's current-main baseline check is unchanged.

| Measure | Original worker output | Batched replay output |
| --- | --- | --- |
| Completion | Natural | Natural |
| Ledger requests | 12/12 | 11/12 |
| Visible model responses | 11 | 10 |
| External real-HTTP acceptance | 4 passed | 4 passed |
| Raw focused tests | 1 failure, 15 passed | 17 passed |
| Operator test corrections | 1 | 0 |
| Manual runtime recovery | None | None |
| Estimated reservation | $1.399713 | $1.394777 |

The replay combined syntax/diff checks and commit/status into one terminal call;
its initial reading still took six responses. This is a measured one-request
reduction on one task, not proof of a general optimization. Correctness is
measured against the same four external real-HTTP checks, not just new tests.
The original output needed two expected-count assertions changed from 6 to 5;
the untouched replay output expects the correct five requests.

Original worker commit: `5a403f4dd1d713cd806edf81b80d67b911110bcc`.
Replay worker commit: `3b31356bd48c6076e751d1362aed77eb5f512900`.
The replay ran through the live NUC Restate/OpenHands/Bifrost route. The existing
host runtime changed from `34ab4c8` to the already deployed `c15acee` between runs,
so this is not a controlled same-runtime trial. Model variability and unrelated
host revisions prevent attributing the improvement solely to the instruction.

Both observer runs restored the previous endpoint binding and stopped only their
own worker/proxy. Original results, bundles, stores, ledgers and raw events are
retained privately. Sanitized action summaries and machine-readable comparison
are alongside this file. Actual charges are unknown; $2 remains a best-effort
target, not a hard cap. No recovery, allowance reset or successor was used.

## Verification and scope

The transmitted-task regression checks the actual appended batching and
failure-handling guidance. Full exact-commit local CI gates the instruction PR;
separate exact-commit local CI verifies the untouched historical replay output.
Independent review covers the instruction, retained production baseline guard,
experiment adapter, replay source/tests and comparison claims. Private operator
receipts retain exact CI logs and file hashes.

The PR contains the instruction, its transmitted-task test and evidence only.
The historical replay's duplicate request-count fix is not published. Its Restate
stage `needs_validation` records the absence of separate signed app acceptance.
This run does not claim new deployment, isolation, crash recovery or enterprise
readiness. Merge and deployment remain operator controlled.
