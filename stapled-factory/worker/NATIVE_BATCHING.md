# Native execution batching

Phase 9d.3 reuses the pinned OpenHands SDK 1.58.2 TerminalTool. No new model-facing execution primitive is added. `baseline/batching.py` contains operator-authored capability recipes for the six frozen tasks; it is not injected as a solution into paid agent trials.

Group independent reads/searches in one terminal action, filter output locally, then use explicit preconditions and `&&` ordering for mutations, checks, scoped commits and clean-worktree postconditions. A failed check must stop subsequent effects. Do not retry a batch after an uncertain result: shell effects may already have happened. Native terminal timeout returns control while the command may still run. Interrupt is cooperative; neither timeout nor cancellation proves rollback.

## Opt-in bounded profile

`deployment/nuc/prepare_dispatch.py --execution-profile bounded-native-v1` prepares configuration only. The default deployed context-v1 profile is unchanged. Before activation, the operator must supply a private mode-0700 workspace that is an exact, retained Linux filesystem mount with total capacity at most 1 GiB and no descendant mounts. Ordinary host directories are refused before credential reads and Docker provisioning. This code never formats or mounts a host filesystem.

The profile retains existing 3 GiB memory/no extra swap, 2 CPU and 512 PID worker limits; adds read-only root, 512 MiB /tmp, and durable HOME plus SDK conversations, bash events and persistence beneath `/workspace/.git/factory-home`. Docker shared memory is limited to 64 MiB. Existing network, mount, gateway, credential and holdout restrictions remain in force. One immutable allocation reservation prevents concurrent reprovisioning.

After durable allocation binding, a private external systemd timer is armed and validated before credential release. The lifetime is at most 300 seconds. Reconciliation validates the original timer, boot, workspace identity and container lifecycle; it never extends the deadline. Deadline expiry or explicit `deadline_watchdog.py --record PATH --cancel` fences the original attempt and stops its original worker and proxy, including escaped child sessions. Namespace containment precedes ledger access, so a failed or locked ledger cannot keep the verified containers running. Durable fencing can still fail or remain pending; late publication is not excluded in that failure case. It retains ledgers, volume evidence and partial effects. Host Docker, systemd and the operator remain trusted; this is not host-daemon-loss recovery.

The existing capture tool now honors SDK cancellation before launch and while running. Output remains artifact-bounded with a small preview. Outer namespace fencing is still required for escaped process groups. Bounded-profile successors are refused; no blind SDK action replay, duplicate paid-call retry, or budget refund is authorized. GET-only reconciliation preserves uncertain attempts.

## Qualification

Run `python3 worker/probes/qualify_native_batching.py --mode batching --output NEW_DIRECTORY` from stapled-factory, or `--mode limits`, against the configured `hermes-control` host. These operator probes use pinned existing images, no network or credentials, actual packaged SDK tool execution, and forbid LLM completion. The independent checker runs in a separate container and is not mounted into the native worker. Test-only tmpfs workspace containment is distinct from activating the production retained bind-mount profile.

The authorized retained-bind canary and 18 paired sandbox trials are recorded in [the bounded rollout report](../../docs/verification/harness-bounded-native-2026-10-31/report.md). Docker can reorder explicit environment overrides; bounded identity requires exact unique names and values independent of that ordering. The default profile retains its original identity checks. The profile remains opt-in, and Phase 9d.4 has not started.
