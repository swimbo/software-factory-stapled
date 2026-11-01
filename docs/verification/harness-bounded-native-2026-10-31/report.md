# Bounded native execution rollout — October 31, 2026

The authorized bounded-native-v1 sandbox pilot completed: 18/18 independently accepted paired trials after the paid canary. Decision: retain opt-in sandbox bounded profile; scoped pilot gates passed. Default task configuration remains context-v1; Phase 9d.4 has not started. This completes the scoped deployment experiment, not the whole Phase 9d.5 or production promotion.

## Measured comparison

| Profile | Accepted | Requests | Total prompt tokens including reported cache | Median workflow seconds |
|---|---:|---:|---:|---:|
| Original baseline (corrected) | 18/18 | 107 | 1,088,401 | 44.451 |
| context-v1 | 18/18 | 103 | 605,900 | 44.433 |
| context-v1 + bounded-native-v1 | 18/18 | 105 | 620,807 | 44.397 |

Prompt tokens per accepted task were 43.0% lower than the corrected original baseline. Against context-v1 alone, they were 2.5% higher; this run demonstrated no incremental token saving from bounded execution. Median workflow latency changed by -0.1% versus the original baseline. Full wire usage coverage: True; gateway/reservation parity: True. Reported cached-read share: 84.2%. Reservations totaled $4.953947; actual provider charges remain unknown and this is not a hard charge cap. Paid canary consumption is separate: eight requests and $0.425619 estimated reservations.

All pairs used the same frozen task bytes, allowed paths and seed `e25eb19801c9a82b5255ecaccc35ce6e0677e91f`, six development tasks with three repeats, unchanged auto router, request/output limits and task order. Reported actual model identifiers match both historical references: True. Provider lane sets match 12/18 original-baseline pairs and 11/18 context-v1 pairs; `comparison.json` separates lanes. The raw baseline remains 17/18; its independently documented contract-corrected grade is 18/18. No original result was overwritten.

This small historical, nonrandomized development comparison is not a held-out or causal estimate. The model-facing context/tool schema was unchanged from context-v1. Existing TerminalTool batching remains the selected execution mechanism; these results do not attribute extra token gains to batching.

## Deployment and containment

Host release `049e51ab08af024b04ff8bbc2c2d9c25c4e2930f` was deployed to factory-endpoint and factory-ingress after exact-revision local CI and review. All 1,105 release files were verified, 60 pre-existing WorkUnit states and the guarded ledger/anchor were unchanged during the final deployment, and shared Restate/Bifrost services were not restarted. Final preservation and health receipts verify restored default task binding, no active operator marker, authenticated loopback listeners, stopped original worker/proxy lifecycles and retained evidence. All completed model-trial private empty networks were removed only after exact ownership and stopped-state checks; volumes/workspaces remain retained.

The worker image is fixed across all paid runs: `sha256:569b184e8c4b1f8015633628dba0b451b6e24618c6f533e0bec1758d0acdb99c`. It retains SDK 1.58.2 and the pinned upstream/package layers; only the reviewed capture/cancellation modules were added to the accepted context image. Signed approval binds images and operational inputs. No direct-provider route or new model-facing execution primitive was added.

One new preallocated private ext4 pool has actual capacity 1,020,702,720 bytes, with each trial workspace bound separately and retained. Read-only worker root, 512 MiB temporary filesystem, 64 MiB shared memory, 3 GiB memory/no extra swap, two CPUs and 512 PIDs remain enforced. HOME plus SDK conversations, bash events and persistence live under `/workspace/.git/factory-home`. Default profile selection is unchanged. This shared private pilot pool bounds aggregate capacity; it is not a per-trial 1 GiB reservation or automatic reboot/remount recovery.

The unpaid retained-bind canary exercised the actual packaged SDK without any model completion and survived the actual external 300-second deadline. Original namespaces and an escaped session child stopped; partial-effect counter remained one, no late effect occurred, fencing was confirmed and checkpoint hashes survived. Paid canary acceptance additionally required a complete hashed output artifact and nonzero-offset page read containing the final marker. All 18 paid candidates retained verified SDK base/event files after both original namespaces stopped: True. The host collector rejects symlinks, special files and multiply linked files, scans with directory file descriptors and bounded streaming, and exports aggregate hashes rather than raw thoughts. Seven adversarial collector tests passed.

## Admission failures and fixes

Five original admissions failed before durable allocation binding and before credential release. Signed receipts independently establish zero canonical gateway calls, healthy guarded state, no owned container/network and retained budget/anchor/telemetry volumes. Their reservations and uncertain WorkUnits remain intact. Fresh identities replaced four candidate admissions and one paid-canary admission only after those proofs; no paid trial was rerun, no reservation was refunded, and no uncertain mutation was replayed. The final experiment ledger therefore contains 19 completed model attempts plus five retained reserved originals. Model completion and operational admission reliability are reported separately.

Three operational corrections were qualified independently: bounded readiness waiting for asynchronous proxy startup (actual delayed-start red/green probes), releasing only owned stopped diagnostic networks after confirmed Docker default address-pool exhaustion, and exact duplicate-free environment comparison independent of Docker's nondeterministic override ordering. Eight never-started Docker probes had identical names/values but two differed in ordering; the corrected unpaid full admission probe passed with HTTP conversation creation suppressed and a dummy credential. Fixed allowlisted failure codes expose no raw arguments, stderr or credentials and never authorize replay.

The host admission implementation changed during this pilot: first four model trials used 645cb59, one used db96ddd, one used eea52b0, and the remaining twelve used 049e51a. The worker image, model prompt, schema, router and budgets stayed fixed. The first failures cannot all be retrospectively assigned to a single cause. These mixed host epochs limit any runtime reliability comparison.

## Decision and next phase

Scoped gates are recorded individually in `comparison.json`. Keep bounded-native-v1 opt-in and preserve all reservations, volumes and workspace mounts. No broader default promotion, production acceptance, host/daemon-loss recovery, merge or publication is claimed. Phase 9d.4 should next add conservative stall/progress detection and one bounded intervention through the existing lifecycle, with productive-read-only and long-running-test negative controls; repeated failure must fence and retain the same attempt rather than restart or replenish budget.

Evidence: `comparison.json`, `suite.json`, per-trial receipts and payload-free telemetry, final gateway/state preservation, signed failed-admission proofs, canary receipts, storage/image/release approvals, exact CI and review receipts. Prior native batching/limit qualification remains in `../harness-native-batching-2026-10-30/`.
