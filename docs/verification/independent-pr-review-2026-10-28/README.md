# Independent review of PR1 and PR2

An independent agent reviewed exact live heads PR1 `c97b52ef4ffe2322b9d504d8731b3bdbfcc69ab4` and PR2 `115cf150b65f5132c5e6a0fb62c73edfa9cb8b0e`. The review covered changed validator, build attestation, signed acceptance, dispatch, routing budget, firewall, backup/recovery code, and the PR2 default-field fix. Large historical inventory evidence was not exhaustively re-audited; this was one independent source reviewer, not a full enterprise or multi-reviewer assessment. Agent review does not replace protected-main human approval.

## Finding and correction

PR1 had one confirmed P2 correctness issue: the candidate-validation bridge stripped custom request headers and returned its own501 for methods other than GET/POST. A scenario expecting rejection of an invalid API key could pass against missing-key behavior even if the candidate accepted the invalid key. Method-error expectations could similarly pass without reaching the candidate.

The bridge now preserves end-to-end headers, removes only hop-by-hop/framing headers, and forwards candidate-specific methods to the existing fixed loopback destination inside the network-none candidate. Host cannot redirect that transport. Nine regression cases exercise missing versus invalid custom credentials, Cookie preservation, PUT/PATCH/DELETE/HEAD/OPTIONS/PURGE, framing and Connection tokens, and a forged Host. The reviewer independently reran all nine tests and confirmed closure without new findings.

A separate unpaid real Docker-exec probe passed eight methods against an isolated candidate, preserving exact path, body, custom key/Cookie/Host inputs. Missing credentials returned401 while supplied invalid credentials reached the deliberately broken candidate and returned200, so a rejection expectation would now correctly fail. The probe used local Docker with network-none, read-only filesystem, nonroot user, no mounts and bounded CPU/memory/PIDs. It did not deploy the updated validator to the NUC or make model requests. Its exact production CLIENT bytes were used through docker exec.

PR2 had no actionable findings; all five added regressions passed on its exported exact head. It remains unchanged.

The JSON receipts distinguish the original-head source review, independent local-delta re-review and Docker probe. The updated PR1 code still requires full exact-commit local CI before push. No main merge, approval bypass, runtime promotion or fresh in-flight crash-recovery claim is made. The existing NUC deployment retains its prior frozen source until separately promoted; rollback of this proposed change is a source revert.
