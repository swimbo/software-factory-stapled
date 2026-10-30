# Operator abandonment verification

The local probe used actual Docker allocations and a real Restate 1.8.0 server.
The production WorkUnit abandonment handler, Python guarded-store transaction,
RSA verification and CLI signing/status code were exercised. SSH was replaced
with a local fixture transport; the SDK seed endpoint was isolated and test-only.
Production cluster request-identity enforcement was not reverified by this probe.

`local-runtime.json` records the successful final run: running workers and invalid
signatures refused, duplicate closure without ledger/watermark writes, real
Restate restart persistence, denial after a privileged container restart, original
result preserved and synthetic budget data unchanged. Only owned Docker fixtures
were removed. File evidence, including the test ledger, was retained privately.
No NUC state, paid model call, merge or deployment was involved.

Two earlier probe attempts failed because Docker reassigned the ephemeral ingress
port after restart. Reading the current mapping confirmed the abandoned state had
survived; refreshing that mapping fixed the harness. No persistence fix or state
reset was needed.

The SQLite-to-Restate commit gap is covered by idempotent guarded-store behavior
and independent code review, not a process-kill injection at that exact boundary.
The existing SQLite-to-watermark split remains fail-closed quarantine on a crash.
The command requires an already stopped allocation and no active observer/binding;
it does not automate worker interruption/fencing. The existing NUC scanner attempt
must remain unresolved until an operator merges/deploys this change and explicitly
runs abandonment.
