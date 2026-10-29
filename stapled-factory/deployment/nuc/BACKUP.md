# Single-operator cold backups

`cold_backup.py` encrypts a quiesced snapshot using AES-256-CBC/PBKDF2 (200,000
iterations), signs encrypted bytes with RSA/SHA-256, verifies that signature
before decrypting or extracting, and verifies a signed internal SHA-256 file
manifest. Restore requires a new destination and rejects special files, links,
path traversal and mixed generations. Private passphrase and signing keys must
remain owner-only outside the repository and source host.

Stop only `factory-ingress`, `factory-endpoint`, and `factory-restate` using
`systemctl --user stop`; transfer the entire dedicated state root via SSH into
an owner-only operator temporary directory, and restart those services in a
`finally` block. Include attempt SQLite/anchor pairs, budget ledgers, and complete
source/successor workspaces in this same stopped snapshot. Do not run a live
filesystem copy or stop unrelated Hermes, Docker, or gateway services.

On the operator machine:

```sh
python3 cold_backup.py seal --quiesced --source PRIVATE_SNAPSHOT \
  --archive BACKUP.enc --password-file PRIVATE_PASSWORD --key PRIVATE_RSA_KEY
python3 cold_backup.py restore --archive BACKUP.enc \
  --password-file PRIVATE_PASSWORD --key PRIVATE_RSA_PUBLIC_KEY \
  --destination NEW_PRIVATE_DESTINATION
```

Keep the encrypted archive, detached `.sig`, public trust root and password
recoverable independently; a lost key makes encrypted backups unrecoverable.
After restoration, start an isolated controlplane and verify actual Restate
WorkUnit state plus guarded attempt/budget/workspace consistency before cutover.
These backups do not supply an independent anti-rollback witness: an old signed
backup remains authentic, and restoring it requires conservative fencing.

October 27, 2026 evidence: a real cold snapshot of the dedicated NUC control root
was streamed to the Mac; 86 files were encrypted, signed, decrypted and restored
with the full manifest matching. Dedicated services restarted and were active.
The snapshot contained persisted Restate state, request-signing key and operator
token, but no worker attempt stores/workspaces yet. Thus it proves off-host
encrypted control backup, not complete coordinated worker recovery. Keys and
archive live under `~/.local/share/stapled-factory-backups` on the operator Mac;
secret values are never printed. Three local real-OpenSSL tests cover restore,
existing-destination rejection, altered ciphertext, and signed mixed generations.

A second real snapshot, `2026-10-27-worker-run4.enc`, restored 158 files including
successful NUC synthetic recovery attempt DB/anchor, budget DB/anchor and both
Git workspaces. Failed earlier fixtures and code-release symlinks were explicitly
excluded; the frozen release is independently preserved. After authenticated
restore, SQLite integrity checks passed, `GuardedAttemptStore.lookup` accepted
the completed successor against its restored anchor, and both workspaces passed
`git fsck --full`. Restored Restate booted on random loopback ports with its
original `factory-restate-control` node name; readiness and a real admin SQL query
confirmed persisted `WorkUnit/nuc-install-check` state remained `claimed`.
Only restore-test processes were stopped afterward; unrelated local Restate was
untouched. Dedicated NUC services were restarted in the cold-copy `finally` path.

Limitation: the worker probe's disposable Restate was removed before snapshot.
The independently persisted control WorkUnit and actual worker stores both restore,
but this is not proof of restoring the exact joined worker workflow. Retain its
Restate data in a future coordinated drill before closing that narrower gap.

## Exact joined workflow restore — verified

A final unpaid NUC replay retained its stopped Restate host bind in
`recovery-fixture-complete/restate-data` together with attempt DB/anchor, budget
DB/exported anchor and both workspaces. That quiesced fixture was streamed via SSH
to the operator Mac, encrypted and signed as
`2026-10-27-joined-complete.enc`; 121 files restored with exact manifest equality.
Code-release symlinks were explicitly excluded; the frozen release is preserved
independently. This supersedes the preceding missing-joined-Restate limitation.

`verify_recovery.py` booted the restored Restate with its original
`factory-recovery-probe` name on random loopback ports. A real admin query returned
`WorkUnit/unit` in `needs_validation` with successor revision
`85201fcc2101cc827f439c1ef91aa8cd8ffbf41d`. The exact completed guarded-store result
and successor Git HEAD matched; both workspaces passed Git integrity checks.
The shared ledger remained byte-identical, with attempt `unit:0`, five synthetic
requests and 2,000,000 microdollars reserved; budget store identity/generation
matched its exported anchor. No model charge was incurred for this drill.

The first query immediately after admin readiness returned HTTP 500; allowing
partition startup to settle before the readback resolved it. The driver polls the read-only query for bounded HTTP 500/connection availability
failures; successful responses immediately undergo state/revision assertions.
Wrong payloads or states are never retried.
Only disposable restore-test processes were stopped afterward. This verifies cold
single-operator recovery, not automatic safe activation of older backups or an
independent anti-rollback witness. Quarantine/fencing remains required before
replaying uncertain effects from any selected recovery generation.
