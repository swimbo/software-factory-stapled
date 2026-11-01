# Phase 9d.2 context and cache implementation — October 30, 2026

Implemented an opt-in `context-v1` candidate. The deployed telemetry/baseline
release remains unchanged; no paid candidate trial or production promotion occurred.

## Baseline-guided decision

The frozen 18 actual trials used 107 requests. Of those, 89 carried 20 tool schemas
(31,305 wire JSON bytes) and 16,302 bytes of full system content; 18 schema-free
startup/helper requests carried 439 system bytes. Tools had one canonical hash;
full system content had 18 hashes, consistent with dynamic conversation context.
Historical tool results in an individual request peaked at 4,184 bytes. All 107
responses report complete usage: 1,088,401 total prompt tokens, 1,008,512 cached
reads (92.66%). This supports reducing unused startup schema/instruction content;
it does not establish a cache-miss problem or noisy-output savings.

The candidate uses the exact SDK's supported persona and explicit-tool settings.
Native terminal/editor/tracker and finish/think remain; browser/model-switch schemas
are omitted from this offline coding profile. A small registered capture tool
provides bounded complete redacted output and paginated retrieval where native
terminal capture cannot. No generic lazy-schema seam exists in the pinned SDK;
no artificial two-tool interface, fork or second agent loop was added.

Upstream already separates static system content from dynamic runtime context.
We preserve that composition and all provider message ordering, tool/result pairs,
opaque provider fields and existing proxy byte-forwarding. No provider-specific
cache headers/thresholds, shared routing rules, retries or admission budgets change.
Task text stays in the user role; discovered documents and outputs do not become
system instructions. Automatic skill/memory loading stays disabled.

## Unpaid exact-image verification

The recorded probe uses worker image
`sha256:026fbb4d6f9e1a6eae146e25a107ea003a067d9a214201e5b8de75b0af3c9f77`
and SDK 1.58.2. Factory-owned modules were mounted read-only into a disposable
container. It had network none, no gateway keys, read-only root, dropped
capabilities, numeric unprivileged UID and bounded resources. It exited before
server launch and made zero model requests. This is an extension integration
check against the existing image, not a rebuilt/deployed candidate image.

| Rendered SDK component | Baseline reconstruction | Candidate |
| --- | ---: | ---: |
| Static system JSON bytes | 16,164 | 7,762 |
| Tool schema JSON bytes | 28,059 | 17,511 |
| Expanded tool count | 20 | 6 |

These are SDK OpenAI-schema serialization sizes, not the proxy's Anthropic wire
sizes or token counts. Baseline reconstruction explicitly selects the serving
layer's browser/switch tool set. Both compared sides use the same image and
serialization. Candidate static system and tool hashes match across separate
conversation/session identities. Factory fixtures separately vary task/time fields.
The malicious-output fixture remains tool data, never injected system content.
These structural checks do not establish model-level prompt-injection immunity.

## Output handling and limits

Capture runs an argument vector in the existing worker workspace and permissions.
It merges stdout/stderr, bounds memory/output/time, kills the process group on
limits and marks incomplete captures. Redaction precedes private persistence;
SHA256 identifies redacted UTF-8 with explicit replacement of invalid bytes.
Pages/previews are at most 4096 UTF-8 bytes. Artifacts have opaque IDs, 0600 files,
0700 task directories, symlink/binding checks, a 1 MiB artifact limit, 8 MiB task
quota and 24-hour maximum retention. Access triggers expiration cleanup.

Artifacts live outside Git in task-local `/tmp`, ending at container stop. Retrieve
needed pages before stopping; durable post-stop export is not provided. Same-UID
worker code can read/tamper with its own files, so this is not a new security
boundary. Credential patterns are heuristic; registered-secret masking is also
applied, including partial tails on incomplete captures. No raw unredacted record
is retained. The native terminal's existing 30,000-character cap remains; it can
truncate before saving, so complete output requires the explicit capture tool.
Other native tool rendering remains upstream-owned.

## Verification and rollout status

Proof-first tests observed missing modules/unavailable preparation option before
implementation. They cover opt-in baseline compatibility, opaque-field retention,
stable templates and malicious task separation, conflicting configuration rejection,
private capture/reconstruction, split secrets, UTF-8 pagination, incomplete output,
timeout, quotas, symlinks, workspace identity, expiration and unknown cache usage.
An independent simplification review found an Authorization/Bearer masking-order
defect; its new regression failed before correction and then passed.

Final exact-code CI passed **480 Python tests plus 12 subtests and 25 Node tests**,
including the masking and descendant-cleanup regressions. Build, dependency audit
and clean-checkout secret scanning passed. CI log digests and logs are retained. Final exact-revision validation is recorded in verification.json. Independent
review completed with seven local lenses and no actionable findings. Both external
review routes were unavailable before egress; no independent model corroboration
is claimed. The optional remaining coverage gap is injected write/fsync failure
between data and metadata saves; orphaned output remains quota-accounted and the
error path does not replay the command. review.json preserves the full receipt.
The original baseline evidence and v2 grading are unchanged. Follow the runbook
`stapled-factory/worker/CONTEXT_PROFILE.md` for opt-in preparation and rollback.

Phase 9d.2 implementation is ready for candidate image approval/canary and paired
evaluation; live cache behavior, acceptance, token/request improvement and latency
remain unmeasured. Phase 9d.3 and the 9d.5 promotion gate remain pending. Do not
promote based on these byte reductions. Paired trials must count unsuccessful
consumption and use corrected baseline grading, actual routed models, total prompt
and uncached tokens, complete cache coverage and the frozen acceptance criteria.

## Source audit

Inspected the exact official archive revision
`d0d9d81324210414462fb676b377e0563a08a0cb`, rather than current docs:

- [AgentSettings persona/tools](https://github.com/OpenHands/software-agent-sdk/blob/d0d9d81324210414462fb676b377e0563a08a0cb/openhands-sdk/openhands/sdk/settings/model.py)
- [Static/dynamic prompt composition](https://github.com/OpenHands/software-agent-sdk/blob/d0d9d81324210414462fb676b377e0563a08a0cb/openhands-sdk/openhands/sdk/agent/agent.py)
- [Custom module loading in the binary](https://github.com/OpenHands/software-agent-sdk/blob/d0d9d81324210414462fb676b377e0563a08a0cb/openhands-agent-server/openhands/agent_server/__main__.py)
- [Native terminal rendering](https://github.com/OpenHands/software-agent-sdk/blob/d0d9d81324210414462fb676b377e0563a08a0cb/openhands-tools/openhands/tools/terminal/definition.py)

`source-audit.json`, `baseline-inventory.json`, `profile.json` and
`sdk-probe.json` preserve exact hashes and structural evidence. GitHub web fetching
was unavailable during this turn; exact source archive inspection and the pinned
binary probe supply the actual verification.

Code commit: `850259ae877e0cb171310e95d11719d9882e879e`.
Implementation and evidence remain local; no push, merge, service change or candidate
activation was performed. Existing workspace/index changes are preserved.
