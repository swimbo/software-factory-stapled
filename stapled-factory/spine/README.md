# The Restate orchestration spine

Phase 9 is in progress. Production OpenHands dispatch is **blocked**; the current
workflow records a completed revision at `needs_validation` until a trusted
binding proves that the wall target serves that revision. It does not presently
run the wall, security pipeline, approval gate, or merge from this workflow.

`WorkUnit` stores the claim, attempt identity and recovery context. An uncertain
build schedules a bounded, read-only reconciliation workflow. The
`Reconciliation` object also supports repeatable single observations, and
accepts completion only if the stored attempt and conversation still match.
A missing or blocked runtime remains `needs_reconciliation`; no automatic
redispatch is permitted.

The worker's guarded ledger and provisioner offer one-shot allocation and
resource-identity checks, but these are not yet connected to the production
entry point. A local watermark cannot detect simultaneous rollback of both
store and watermark. Trusted image provenance, workspace fencing, recovery of
interrupted tool effects, verified gateway-only egress, and a hard provider
billing ceiling remain open. See `../worker/README.md` and
`../../docs/planning/stapled-factory/progress.md` for tested boundaries.

The SDK endpoint binds to `127.0.0.1:9080` and requires comma-separated Restate
request identity public keys in `FACTORY_RESTATE_IDENTITY_KEYS`. Set
`FACTORY_ENDPOINT_PORT` to choose a different local port. The trusted Restate
server must sign its requests with the corresponding private key. Restate
ingress and admin listeners still need separate caller access controls;
loopback and request identity do not authorize ingress callers.

For local checks:

```sh
cd spine
npm ci
npm run build
node --test tests/*.test.mjs
```

An isolated Restate process can be used for integration experiments; use a
separate temporary data directory and ports and do not modify a shared server.

An optional local ingress gateway is available with `node dist/ingress_main.js`.
Set `FACTORY_INGRESS_TOKEN_FILE` to an owner-only regular file containing a
cryptographically random base64url token generated from at least 32 random bytes (43 characters), and configure
`FACTORY_INGRESS_GATEWAY_PORT` and `FACTORY_RESTATE_INGRESS_PORT` as needed.
It accepts bearer-authenticated JSON POSTs only for `orchestrate/<key>/run`,
`Reconciliation/<key>/observe`, and `WorkUnit/<key>/getState`. It does not
forward the bearer token. The direct Restate ingress, admin, and internal
listeners must be isolated from untrusted clients independently; loopback alone
is not a boundary against other local processes. The gateway is not a substitute
for that isolation or for authorization over filesystem paths supplied to a
workflow. A disposable signed Restate instance was also tested through this gateway; this
did not test isolation of its direct ingress or admin listeners.

WorkUnit stage changes are restricted to the presently implemented build path;
caller-supplied receipts are rejected until trusted target binding exists.
