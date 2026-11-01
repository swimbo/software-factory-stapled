# Stall detection and bounded intervention — October 31, 2026

Phase 9d.4 is implemented and qualified for an opt-in sandbox profile. Offline regressions preceded live inference. The corrected immutable candidate passed its scoped canary after one failed initial candidate was retained and investigated. Default task configuration is restored; Phase 9d.5 and broader promotion have not started.

## Implemented behavior

`stall-v1` requires the existing context-v1 and bounded-native-v1 profiles. It reuses the pinned worker image and SDK loop. A host observer pairs completed SDK tool actions/results, hashes diagnostic evidence and checks bounded workspace progress. The versioned runtime policy requires three equivalent completed failures spanning at least four seconds, or a repeated A/B workspace oscillation. Unchanged workspace hashes alone are insufficient. Native soft timeouts, incomplete scans and unknown observations do not become failure evidence.

Workspace fingerprints cover HEAD, semantic index state, tracked content and relevant untracked content. Secret/volatile paths are excluded. No-follow file descriptors, file/link/type checks, node/depth/byte bounds and a private-config Git child prevent worker-controlled Git configuration from reaching the host. Git reads have bounded output, a five-second timeout, two-second CPU allowance and a 256 MiB Linux address-space limit.

Every unseen observation is accounted for before classifying the sampled latest result. Intermediate diagnostics or unknown observations conservatively clear the prior detection window; historical workspace hashes are not fabricated. The one-shot intervention claim survives such progress and process restart.

Before delivery, the observer rechecks that the original conversation is running. It saves and fsyncs one private intent before sending a fixed user diagnostic with `run=false`. No tool/repository text enters that message. Continued qualifying loops, uncertain delivery or inactivity after delivery cause original-allocation containment and reconciliation. Verified completion at a terminal boundary is preserved. No second conversation, `/run`, SDK resume, automatic tool retry, successor, reservation refund or request-limit increase is introduced.

`auto-common-v1` allows only the existing explicit model/route/dummy worker key/usage identity/output-limit fields. Unsupported vendor-specific parameters fail before credential access. The shared router and model-family selection remain unchanged.

## Offline evidence and review

Final exact source `3b1c3bbd89c553d6419d004d3256c0de07b625d9` passed **586 Python tests plus 12 subtests, 25 Node tests**, secret scanning, dependency audit and build. The new cases cover repeated errors, oscillation, productive reads, new diagnostics, long-running tools, staged/unstaged/untracked changes, secret/log exclusion, symlinks/special files, scan limits, replayed observations, crash after delivery claim, uncertain delivery, fence failure/recovery, original reservation retention and unsupported capabilities.

Full review found a real false positive: selecting only the latest error discarded productive diagnostics between polls. The new regression was observed failing, then passed after all unseen results were accounted for and intervening progress cleared history. Independent followup validated zero interventions/fences for productive bursts and exactly one intervention/fence when a genuine loop returned. The original review and resolved-finding receipt are both preserved. A focused review separately verified the live SDK running-state correction.

## Two immutable source candidates

| Candidate | Source | Outcome | Gateway requests | Estimated reservations |
|---|---|---|---:|---:|
| Initial | ad70b75 | Failed active-loop/adaptation qualification; retained and fenced | 6 | $0.278221 |
| Corrected | 3b1c3bb | One diagnostic and independently verified adaptation | 12 | $0.640921 |

Total consumption was **18 requests and $0.919142 in estimated reservations**. Actual provider charges remain unknown; reservations are not a hard charge cap. Neither original attempt was redispatched or resumed. Each candidate used a fresh, separately approved identity; the failed candidate is not omitted from totals.

The first candidate stored one diagnostic but did not establish active-loop consumption. The SDK was later observed idle; the original allocation was explicitly cancelled/fenced at the canary observation limit. Actual packaged bytecode showed that `run=false` never schedules continuation, while a message can reset FINISHED/STUCK to IDLE. It does not unconditionally pause a running loop. The exact first terminal transition is not asserted. The raw collector's `continued_loop_fenced` flag describes cleanup only; `candidate1/outcome-interpretation.json` explicitly distinguishes that from automatic continued-loop escalation.

The correction added a running-state precondition, terminal-boundary completion checks and containment without resume when the loop is inactive. Its earlier versioned warning window and slower controlled fixture allow intervention before terminal handling. The pinned native stuck detector still operates; a source=user diagnostic naturally starts its new history window, without disabling any native control.

The corrected canary recorded four expected failed terminal observations before exactly one diagnostic. The agent then changed only `canary.txt` from `pending` to `guarded`, committed that change and finished with a clean worktree. The operator verified the exact revision, allowed path and frozen candidate bytes independently. The guarded attempt is completed and the WorkUnit remains `needs_validation`; this is development-contract acceptance, not production/wall approval.

Initial collection saw only 11 gateway rows although the retained proxy showed 12 complete requests. A later read-only query of the same canonical session found the final asynchronously visible row. All 12 unique successful gateway rows now match reservations, wire requests, input/output/cache coverage and totals: **85,028 prompt tokens including cache and 1,977 output tokens**. The initial collector result remains unchanged; `candidate2/canary-reconciliation.json` appends the final evidence and acceptance decision. No model, tool or controller execution was repeated for this reconciliation.

## Deployment and preservation

Both releases used worker image `sha256:569b184e8c4b1f8015633628dba0b451b6e24618c6f533e0bec1758d0acdb99c`, SDK 1.58.2 and pinned upstream revision `d0d9d81324210414462fb676b377e0563a08a0cb`. No worker-image or dependency upgrade was needed. The host release was verified against all 1,209 manifest files and signed inputs before activation. Only factory-endpoint and factory-ingress were restarted; shared Restate/Bifrost services were untouched. During deployment, the existing guarded store and anchor were unchanged.

Final checks verified **73 prior WorkUnit states unchanged**, all 127 signed inputs still matching, healthy/authenticated loopback services, restored prior task binding and no active operator marker. Both original container lifecycles remain stopped. All bound volume identities and the two private retained workspace mounts remain intact in the existing bounded pool. Only the two owned, empty experiment networks were released after stopped-lifecycle, allocation and completed/fence-confirmation checks. No shared mounts, unrelated networks, databases or reservations were deleted/reset.

The corrected canary retained 46 SDK checkpoint files, including one base state and 42 event files, with a verified aggregate digest after namespace stop. General receipts contain metadata/hashes rather than raw model thoughts.

## Limits and next phase

This is a controlled development canary, with a timing-adjusted fixture, not a paired performance estimate or held-out/production acceptance. The first failure and asynchronous accounting visibility are part of the evidence. Rapid loops wholly between polls may be missed; workspace observations are bounded rather than atomic. Worker-reported events remain an untrusted heuristic. The independent sandbox, budgets and original 300-second allocation deadline remain the containment boundary.

The SDK running-state GET and diagnostic POST cannot be atomic through this API. A terminal transition can still race delivery; the response is containment/reconciliation, never automatic resume or a claim of consumption. Automatic continued-loop escalation was verified offline against the original guarded ledger/lifecycle; this live canary demonstrated adaptation. The initial canary's eventual cleanup is not claimed as live automatic escalation proof. Host/operator and Docker/systemd trust, reboot/daemon-loss limitations and actual-charge uncertainty remain unchanged.

Keep `stall-v1` opt-in. Next is Phase 9d.5: incorporate sanitized failures into regression tasks, keep a separate held-out set, compare mechanisms and combined profiles, and make a broader promotion decision only from those gates.
