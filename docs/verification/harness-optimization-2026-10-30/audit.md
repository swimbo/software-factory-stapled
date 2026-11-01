# Phase 9d.1 baseline audit — October 30, 2026

## Scope and result

Implemented the local audit/export tools, opt-in confined-proxy usage observer, optional adapter lifecycle sink and six frozen development tasks. No live model request, NUC change, image build/deployment or gateway rule change was performed. Optimization mechanisms (9d.2–9d.5), live baseline trials and rollout remain pending. This audit does not claim Phase 9d complete.

## Pinned upstream evidence

The existing image publication receipt maps the configured upstream digest `sha256:90da82d4dd57f713e2b9f5bc9444256b06c8bc709ca5ac794048ea9e1952b466` to revision `d0d9d81324210414462fb676b377e0563a08a0cb`. Read that exact official source archive; `upstream-source-audit.json` records file hashes. Both package versions declare **1.58.2**; do not use current `main` documentation or the 1.58.0 tag as a substitute. This is source inspection and prior publication evidence, not a fresh attestation of the deployed image.

| Seam | Exact-revision source | Finding and decision |
| --- | --- | --- |
| Prompt construction | [agent.py](https://github.com/OpenHands/software-agent-sdk/blob/d0d9d81324210414462fb676b377e0563a08a0cb/openhands-sdk/openhands/sdk/agent/agent.py) | Agent already creates static system prompt and separate dynamic context. Measure actual emitted requests before changing this. |
| Prompt profiles/settings | [settings/model.py](https://github.com/OpenHands/software-agent-sdk/blob/d0d9d81324210414462fb676b377e0563a08a0cb/openhands-sdk/openhands/sdk/settings/model.py), [presets.py](https://github.com/OpenHands/software-agent-sdk/blob/d0d9d81324210414462fb676b377e0563a08a0cb/openhands-sdk/openhands/sdk/context/prompts/presets.py) | Supported settings/presets are the first extension route; do not replace policy wholesale or infer provider family from auto alias. |
| Tools/batching | [default.py](https://github.com/OpenHands/software-agent-sdk/blob/d0d9d81324210414462fb676b377e0563a08a0cb/openhands-tools/openhands/tools/preset/default.py), [terminal/definition.py](https://github.com/OpenHands/software-agent-sdk/blob/d0d9d81324210414462fb676b377e0563a08a0cb/openhands-tools/openhands/tools/terminal/definition.py) | Existing terminal tool supports executable commands; preset includes terminal/editor/tracker with optional browser/delegation. Benchmark read batching here before adding a JS runtime or forcing two tools. This audit does not prove a separate script primitive is needed. |
| Usage | [metrics.py](https://github.com/OpenHands/software-agent-sdk/blob/d0d9d81324210414462fb676b377e0563a08a0cb/openhands-sdk/openhands/sdk/llm/utils/metrics.py), [models.py](https://github.com/OpenHands/software-agent-sdk/blob/d0d9d81324210414462fb676b377e0563a08a0cb/openhands-agent-server/openhands/agent_server/models.py) | Conversation info exposes a metrics snapshot. TokenUsage defaults missing fields to zero; cost also defaults to zero. Observe wire usage separately so absence remains unknown. No SDK-derived cache percentage or billed-cost claim. |
| Stalls | [stuck_detector.py](https://github.com/OpenHands/software-agent-sdk/blob/d0d9d81324210414462fb676b377e0563a08a0cb/openhands-sdk/openhands/sdk/conversation/stuck_detector.py) | Native detector exists; configuration includes stuck detection. Later compare its coverage with bounded churn fixtures before extending it. |
| Retry/cache/completion logs | [llm.py](https://github.com/OpenHands/software-agent-sdk/blob/d0d9d81324210414462fb676b377e0563a08a0cb/openhands-sdk/openhands/sdk/llm/llm.py) | SDK has retry/cache controls and full completion logging. Leave raw completion logs disabled; proxy request count includes admitted retries but cannot identify their cause or gateway-internal calls. |

## Factory-owned boundaries

- `agent_entrypoint.py` launches only bridge + AgentServer; no new execution service is added.
- `openhands_adapter.py` validates conversation identity before optional lifecycle observation and still requires an independently verified clean revision. Error/stuck/unknown remain reconcilable; no extra POST or event route is enabled.
- `egress_proxy.py` observes the exact admitted worker request and streams response bytes unchanged. JSON/SSE payloads are reduced to allowlisted metrics; no content is persisted. Telemetry failure cannot refund or retry requests. SDK/provider/gateway-internal retry attribution and actual route remain unknown.
- `harness_baseline.py snapshot` fingerprints tracked and nonignored untracked factory/runtime, scripts and CI files, including dirty source. Explicit private config/task inputs can be fingerprinted without exporting their contents. Runtime image, rendered prompts/tools and deployment remain unknown until observed and bound independently.
- Existing orchestrator/result receipts are unchanged. A versioned diagnostic sidecar plus explicit operator export avoids silently changing signed acceptance contracts. Automatic remote telemetry collection and private proxy mount provisioning are not enabled yet.

## Verification and next gates

Tests start from the missing telemetry module (observed collection failure), then cover chunk-split SSE, JSON, present-zero versus absent/malformed usage, oversized frames, incomplete responses, private bounded append/reopen, mixed identity rejection, source manifests, fixture acceptance negatives and byte-preserving proxy integration. Worker regression results are recorded in `verification.json` after completion.

`baseline-manifest.json` preserves the pre-review implementation snapshot; `baseline-manifest-final.json` fingerprints the corrected source, including dirty bytes. Neither is performance data. All six task run counts remain zero. Each task needs three independently identified authorized model runs, with reservations retained for failures. Capture a fresh immutable post-commit source/config/image binding before those trials. Do not claim savings or promote any optimization from local fixture results.

Runtime enablement requires a proxy-only private telemetry volume, audited/rebuilt immutable images, signed approval and a disposable canary with forwarding/isolation/accounting checks. Keep the baseline profile until paired acceptance and performance evidence passes the Phase 9d.5 gate.

## Review follow-up

Six local code-review lenses and an independent validator confirmed one defect: initial SSE output usage could be promoted to a final count when message_delta omitted output usage. Added explicit final-output provenance and a regression that failed before the fix and passes afterward. Additional real-proxy tests cover failed start/end diagnostic writes and an interrupted upstream read; forwarding remains byte-identical, no second upstream request occurs and the original reservation stays spent. The review covered the pre-fix staged tree; final tests cover the corrected tree. Cross-model inference was excluded and no runtime activity occurred.
