# Real example task: completed and accepted

On 2026-10-28 the NUC OpenHands worker added a read-only `/version` endpoint and two stdlib regression tests to an existing HTTP fixture. The task ran through real Restate orchestration, the production sandbox provisioning path, and the existing NUC Bifrost complexity router. No worker code was supplied by the operator and no candidate test code ran on the operator host outside a network-none container.

## Result

- Factory runtime: `e636f92636cd19688bed62129ab2f0f52b55aa49`, locally verified, locally built, pushed to GitHub, and frozen on the NUC. No GitHub runners.
- Work unit: `nuc-version-example-20261028-02`, attempt `:0`.
- Worker commit: `d8bf750a3c8c0b03c3481b9d8c07b508fc3fc3f3`; clean committed workspace; naturally finished OpenHands conversation. Restate reconciled the same attempt to `needs_validation` without redispatch.
- Diff: only two routing lines in `app.py`, plus `test_version.py`. Removing those two lines reproduces the original app exactly, including all authentication and timing behavior.
- Two worker regression tests passed inside the exact locally built, network-none, nonroot, read-only candidate image.
- Operator-private six-scenario wall: baseline **15/18**, known-correct control **18/18**, actual worker candidate **all six scenario quorums passed** with three runs per scenario (the receipt omits individual-run totals). Existing five auth scenarios passed unchanged.
- Source security pipeline: `pass`, no findings; actual engines were **regex-fallback** SAST/secrets and **manifest-flag** dependency scanning. This does not establish a full enterprise security assessment.
- Local build attestation binds exact commit, baseline, archive, diff, Dockerfile and image config; the existing operator Ed25519 public key verified it on the NUC. Signing private key stayed on the Mac outside the build context.
- NUC candidate image: `sha256:0e94798bb030eb1e6ce2dd58e447cc804e09ad8b46d3d38c812002e2308ce5de`; signed config `sha256:af9121ffd48a13abcfd66ddb0a3f89d2e8c36e94bcccd2aea86e5d428634bd77`. These differ because the NUC daemon identifies the OCI manifest; validation re-derived the saved config digest.
- Trusted validation submitted the HMAC-signed exact artifact/scenario evidence through private Restate ingress. State is **validated**. Duplicate dispatch left worker identity, commit and budget unchanged; duplicate valid acceptance returned false; invalid signature raised the expected terminal error.
- Sequential restarts of **factory-endpoint** and **factory-restate** preserved the entire validated state, selected commit/image/scenario identities, attempt store/anchor, worker lifecycle and budget ledger. No shared Bifrost, Docker daemon or unrelated service was restarted.
- Afterwards only this completed task's worker and proxy were stopped. Containers, workspaces, ledgers, volumes, candidate image and Restate journal were retained. The transient validation app container was removed by the adapter.

## Real gap found and resolved

The first work unit (`nuc-version-example-20261028-01`) committed a candidate but exhausted the old six-request canary allowance before OpenHands could finish. It remained unresolved rather than being falsely declared completed. The operator checked allocation identities, stopped both bound containers, and preserved its ledger and state (`first-attempt-fence.json`).

The routed-task default now allows **12** model requests while retaining the existing **$2 best-effort estimated target**. Production preparation derives both proxy allowance and agent iteration count from the pricing policy. Regression tests verify calls beyond six, denial of the thirteenth, and cumulative reservations. The new proxy passed actual NUC containment, cgroup and gateway-only TLS egress probes without model calls (`runtime-isolation.json`).

The successful run was a separately identified, operator-controlled new work unit from the original baseline. It was **not** an automatic shared-budget successor, and the exhausted ledger was never reset or increased. The second run happened to finish in six requests; the independent regression establishes the newly permitted seventh through twelfth requests.

## Routing and spending

The successful run made six calls through `model:auto` using the trusted attempt-scoped session. Existing Bifrost rules selected Flash low for SIMPLE and Flash high for MEDIUM. No direct provider egress or temporary router rule was introduced.

First run reservations: **$0.449232**. Successful run reservations: **$0.446516**. Combined recorded reservations: **$0.895748** across both explicit work units. These are conservative policy estimates, **not actual charges**. Gateway costs were null; actual charges remain unknown and no monetary hard cap is claimed.

## Evidence and scope

`candidate.bundle` preserves the exact worker Git history. `candidate.diff`, the copied example source under `docs/examples/version-endpoint/`, `build.json`/`build.sig`, `validation-result.json`, `verified-dispatch.json`, `gateway-routing.json`, `persistence.json`, and `cleanup.json` retain the corresponding proof.

This proves one real coding task through durable **disposable-candidate validation**, including restarts after acceptance. It does not merge protected main, publish this fixture as an application, prove a new in-flight crash recovery case, or close the broader enterprise release gates.
