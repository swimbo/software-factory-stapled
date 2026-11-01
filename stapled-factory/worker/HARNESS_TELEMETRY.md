# Harness baseline and telemetry (Phase 9d.1)

Deployed on the NUC on October 30, 2026 for the explicitly authorized 18-trial baseline. See [deployment and results](../../docs/verification/harness-baseline-2026-10-30/report.md). This does not automatically enable optimization profiles, new routing or retries; Phase 9d.2 has not started.

## Freeze a source baseline

From the repository root:

```sh
python3 stapled-factory/worker/harness_baseline.py snapshot --output /absolute/private/baseline.json
```

The output records exact source file hashes (including dirty implementation bytes), the development-suite hash, six task identities, configured upstream image/revision and the primary metric selected before trials. It never claims that local source equals a deployed runtime. Pass `--runtime-config /absolute/private/runtime.json` and `--task-file /absolute/private/task.txt` to fingerprint explicit owner-only inputs without exporting their contents or paths. No credential, Docker, SSH or model call is needed. Output uses exclusive creation and mode 0600; preserve earlier baselines.

`baseline/seed/` is the disposable development repository input. Copy **only that directory** into a fresh task workspace; initialize/commit it to freeze its Git baseline. `baseline/tasks.json` defines six independent tasks, each starting from the same seed. `baseline/check.py` remains outside the generator workspace and takes task ID plus candidate directory. It executes candidate code: use a disposable sandbox, not an unreviewed candidate on a trusted host. Enforce each task's `allowed_paths` through the existing operator collector. The stalled task's `blocker.json` checks explicit blocked behavior; actual tool attempt counts and bounded termination additionally require trace/lifecycle inspection. Artifact acceptance alone does not prove correct stall handling.

The suite is developmental, not the private final holdout. Three authorized live baseline trials per task are still required (18 total); local fixture checks do not count as model runs. There is no batch dispatch command or authority to repeat an uncertain attempt.

## Opt in at the confined proxy

Provision an owner-only directory (0700) outside all worker mounts and separate from the socket volume, ledgers and anchors. Supply `--telemetry-dir /private/telemetry` to `egress_proxy.py` through the existing signed runtime preparation. For containerized operation this needs a private **proxy-only** writable mount and a newly built, audited, approved immutable proxy image. The provisioner now recognizes the exact opt-in `--telemetry-dir /telemetry` and creates a labeled, proxy-only volume. The ordinary operator prepare command does not append this option automatically; include it in the signed attempt configuration. The baseline image and volume path passed the unpaid live isolation canary. Do not use worker-writable paths, credential directories, or a shared log directory. Use one directory per immutable attempt and retain its original binding on recovery.

The opt-in observer records only hashes, sizes, counts, allowlisted statuses and usage numbers. It never records raw prompts, messages, tools, tool results, response text, exception strings, headers or credentials. Component hashes use canonical JSON and are diagnostic fingerprints, **not** provider cache keys or proof of byte-identical prefixes. Hashes can still identify known content; treat the log as private.

The proxy streams the original request and response bytes without rewriting them. Each admitted reservation emits a random-ID start record; its end records elapsed request time, transport state, HTTP status and bounded JSON/SSE usage observations. Reservations survive missing telemetry, stream failures and cancellation. The proxy makes no retry. `OpenHandsAdapter(..., telemetry=sink)` optionally observes allowlisted lifecycle states after conversation identity validation, without adding network calls or changing completion/revision checks. The production coordinator does not enable this optional sink yet.

Each log is bounded to 4 MiB, owner-only, bound to the attempt hash and input fingerprint, and protected against concurrent appends, symlink file replacement and incomplete final records. Parse buffers are bounded to 256 KiB per JSON response/SSE event. Oversized/malformed frames retain a coverage limitation. Later usage values replace earlier cumulative values; they are not added twice. Final output totals require an explicit output count in JSON usage or an SSE message_delta, not merely the initial message_start estimate. Observer failure leaves forwarding/admission unchanged and emits a fixed content-free diagnostic. Process death may omit an end; a failed start may omit a request entirely. Compare log counts against the authoritative reservation ledger before treating coverage as complete. Diagnostics are not tamper-proof or a budget ledger.

Retain logs with their operator run until reconciliation and evaluation finish. Then remove them under the operator's retention policy; no automatic deletion or rotation rewinds history. At quota exhaustion stop relying on telemetry for that run; preserve the log and retain ledger evidence. Do not reset a budget or start a replacement attempt to obtain cleaner measurements.

## Export observations

```sh
python3 stapled-factory/worker/harness_baseline.py summarize \
  --log /absolute/private/telemetry/telemetry.jsonl \
  --output /absolute/private/summary.json
```

The export validates a single binding and unique start/end IDs. It reports request counts, incomplete responses, token coverage and partial observed sums. Full totals stay null unless every observed admission has a complete, valid, fully transported response with the field reported. A present zero is distinct from an absent field. Failed/exhausted attempts must remain in paired-run comparisons and their reservations count even when usage is unknown.

Anthropic wire `input_tokens`, cached reads and cache creation remain separate; no denominator is guessed. `cached_input_share`, actual charges, actual selected router model and SDK/provider retry counts stay null. The response model is only fingerprinted; a gateway alias does not establish the selected model. A trusted gateway-log join and provider-specific usage semantics are needed for route attribution and cache economics. Request elapsed time is not total task elapsed time. Tool-result bytes count historical result blocks in each request, not unique output bytes across a run. Tool-call count covers response `tool_use` blocks, not proof that each tool executed. SDK metrics are not ingested because upstream defaults missing cache usage/cost fields to zero.

This diagnostic sidecar does not alter existing WorkUnit/acceptance receipt contracts. Operator collection is the explicit local export above; automatic remote collection, runtime provenance binding, retries/tool-event coverage and paid paired trials remain open work.

## Baseline evaluator calibration

The frozen deployment checker produced a false negative on a valid blocker record with extra explanatory fields. The source checker now enforces required JSON fields without forbidding extras. The frozen trace substring counter also counted a documented import inside a quoted heredoc as execution. Preserve that raw metric as a heuristic; inspect actual executed TerminalObservations and use the recorded v2 audit for baseline comparisons. The baseline report preserves both original and contract-corrected scores, with no model reruns. The deployed release remains frozen at `974aece`; operator-side corrected grading did not alter model execution.
