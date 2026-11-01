# Phase 9d.2 context profile

`context-v1` is an **opt-in implementation candidate**, not the deployed default.
The 18-trial baseline and its v2 grading remain frozen. No paid candidate runs or
cache/latency/savings claim follows from the unpaid rendering probe.

Prepare a signed sandbox attempt with the existing `deployment/nuc/prepare_dispatch.py`
and `--context-profile context-v1`. All other required arguments, immutable image
approval and ordinary activation gates still apply. This only writes preparation
files; it never starts a worker. Rebuild `Dockerfile.agent` with the same pinned
upstream digest before enabling the profile. Do not use the old image: it lacks
the extension modules. The ordinary operator CLI still prepares `baseline`.
To roll back, prepare a **new** baseline attempt with the baseline image/profile;
never edit a dispatched request, reuse an attempt, rewind a ledger or rerun effects.

The supported AgentSettings persona seam replaces repeated persona/workflow prose,
while upstream capability, risk/security and process guidance stays in place.
Explicit ordered tools are native terminal/editor/tracker, the output-capture
extension and SDK built-in finish/think. Browser and model-switch schemas are not
selected for this offline coding profile. This is an authorized capability profile,
not on-demand discovery: the pinned SDK has no generic lazy-schema seam. Tasks
requiring other capabilities need a separately reviewed operator configuration.
No request-boundary rewrite, provider field canonicalization, history filtering,
tool/result reordering, caching directive or gateway route change is performed.

`context-profile.json` fingerprints only factory-owned template/spec content.
The same persona and tool definitions apply across task/session/time changes;
the task remains the initial user message. SDK dynamic runtime context stays after
its static system block. Project/user/public skills and memory auto-loading remain
disabled, preventing discovered documents from entering system instructions.
Files and tool results are still model-visible untrusted data; prompt separation
does not prove a model can never follow malicious content.

## Bounded noisy output

Use `factory_capture` operation `capture`, with an executable and argument list:

```json
{"operation":"capture","argv":["python","check_totals.py"],"timeout":30}
```

It runs in the conversation workspace as the existing worker user, within the
existing container/network/cgroup restrictions. It adds no credential or egress
access. It is a separate process, not the persistent terminal shell. Mutations are
possible; it is not a read-only tool or a language sandbox. The command is never
automatically retried. Timeout or cancellation can follow side effects.

Merged stdout/stderr is captured in bounded memory, decoded with UTF-8 replacement
and redacted **before storage/display**. Standard credential patterns and the SDK
registered-secret registry are masked; unknown unlabeled secrets cannot be
reliably detected. Incomplete captures withhold a registry-secret-length suffix.
Raw bytes are never saved. Digests identify retained redacted UTF-8, not original
binary output. Complete means all captured merged output, subject to that explicit
redaction/encoding; it does not mean the command succeeded.

Responses have a preview up to 4096 UTF-8 bytes, digest, retained size, opaque
artifact ID, truncation marker, `capture_complete`, exit code and reason. Read
more with `{"operation":"read","artifact_id":"…","offset":4096}`, using the
returned `next_offset`, not guessed offsets. Output stays a tool observation.
The extension rejects paths and IDs from other conversations.

Limits: 60 seconds per capture, 1 MiB per artifact, 8 MiB per conversation including
metadata. The process group is killed on timeout/output limit; output is explicitly
incomplete. A full store refuses execution before launching the command. Files are
0600 in a 0700 directory, with nofollow checks, binding and integrity checks. These
are protections against accidents and cross-task lookup, **not** an independent
boundary against hostile code with the same UID inside the worker.

Storage is in the worker's task-local `/tmp` filesystem outside Git. Retention is
at most 24 hours or the worker/container lifetime, whichever is shorter; expiration
is cleaned on access. Retrieve required pages before stopping the worker. These
artifacts are not an operator audit ledger or durable post-stop export.
The ordinary upstream terminal remains capped at 30,000 characters and may clip
before its own save step. Its saved output is not promised complete. File-editor
and other native result limits remain upstream-owned. Use capture for complete
bounded diagnostic output; do not infer that every native tool call uses it.

## Measurement

Run `python3 worker/context_inventory.py <telemetry-directory>` to summarize the
payload-free baseline/request shapes and cache coverage. Missing or incomplete
usage stays unknown. Report input + cached reads + cache creation as total prompt
tokens, alongside uncached input; do not compare only uncached tokens across
warm/cold runs. Provider cache eligibility/thresholds and actual billing remain
unknown. The baseline's observed cached-read share was already 92.66%.

The unpaid SDK probe `worker/probes/context_profile_sdk.py` runs through the exact
pinned AgentServer binary's `--extra-python-path` / `--import-modules` extension
seam, exits before starting the server and forbids LLM completion. It checks
rendered stable prompts/tools, private capture/pagination, malicious data separation,
registered-secret masking and cross-task read denial. Run with network none,
read-only root, dropped capabilities, a writable executable `/tmp` (the upstream
binary extracts libraries there), resource limits and no gateway credentials.
See the dated verification report for source hashes, results and limitations.
