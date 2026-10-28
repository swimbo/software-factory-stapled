# Tech-guide debate

## Scope and participants

Systems architecture, security, reliability, and finance/cost examined the implementation consequences of the already-selected stack. The existing architecture debate and decisions remain intact. This is a synthesis of their positions, not proof that the proposed controls are deployed or effective.

## Consensus

1. Retain Restate and OpenHands. Pin and verify the actual server, SDK, runtime, and image versions together; establish a tested compatibility contract before relying on their interfaces.
2. Journal completed effects, but do not assume `ctx.run` prevents a duplicate external action when a process fails after acceptance and before the result is recorded. Use stable attempt identity and supported idempotency or reconcile uncertain dispatches; stop on ambiguity rather than silently launching another run. Test crash boundaries.
3. Use an enforced worker sandbox as the security boundary. Tool interception is defense in depth. Demonstrate isolation with a real worker canary against holdout filesystem and network access, including egress probes; an empty-directory simulation is insufficient.
4. Reserve budget and concurrency before dispatch, enforce a runtime ceiling where the provider or gateway supports one, and reconcile actual usage. Unknown usage or uncertain dispatch must remain explicit. A reservation alone does not establish a hard spend cap.
5. Treat streamed events as observations, and reconcile completion, artifacts, and usage through the verified server contract after disconnects. Avoid holding an exclusive virtual-object handler for the entire remote run; use a durable completion signal or another tested asynchronous pattern.

## Open acceptance gates

- Identify and pin the exact OpenHands Agent Server API, authentication, workspace lifecycle, approval mode, and event and usage schemas; the separate OpenHands app API documentation does not verify this contract.
- Demonstrate end-to-end behavior against the pinned worker and a real isolated workspace, including restart, duplicate-dispatch, egress, and holdout-denial tests.
- Establish whether the chosen gateway/provider supports an enforceable in-flight cost ceiling and complete usage accounting. Until demonstrated, cost enforcement and accounting remain provisional.
- Confirm the actual deployed Restate and SDK versions and their recovery and upgrade behavior.

Sources and limits are recorded in [tech-guide-evidence.md](tech-guide-evidence.md).
