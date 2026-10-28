# NUC deployment target

The user corrected the runtime target from Railway to the existing local NUC on
October 27. Source remains `swimbo/software-factory-stapled` on GitHub. All builds
and dependency installation run locally on the operator Mac, followed by source
and artifact transfer. No GitHub runners or remote provider builds are used.

## Verified control plane

- SSH alias `hermes-control`, host `nuc-hermes`, operator bo uid1000, Linux amd64,
  Docker29.8.0. Existing shared workloads were inventoried and left running.
- Frozen release:
  `/home/bo/.local/share/stapled-factory-control/releases/9f40ca50e50220248fd104ddda3bdaa4cc968b18`.
  All764 transferred file hashes matched. Release manifest SHA256:
  `8afa428f25c3e9b4b4e459d95ed03288be026cb3f53f474ddc4ce79c9f6b6dfe`.
- Enabled user services: `factory-restate`, `factory-endpoint`, `factory-ingress`.
  Restate admin19070/raw18080, signed endpoint19080 and bearer ingress19081 bind
  loopback only. Restate fabric5122 also binds loopback.
- Signing key and bearer token remain owner-only on the NUC. Signed endpoint
  registration/state read succeeded; missing bearer and unsigned endpoint were
  rejected401, forbidden ingress claim404. Actual state persisted across a
  dedicated Restate restart and the subsequent release retargeting.
- Durable attempt store and anchor use separate owner-only directories. No
  per-attempt `FACTORY_RUNTIME_CONFIG` is configured in the persistent service;
  dispatch remains blocked. This is control-plane deployment, not proof that
  arbitrary production work units can run.

## Worker verification artifacts

Locally built amd64 uid1000 images were transferred through approved SSH using
Docker save/load. Docker versions expose different config/manifest IDs. The
mapping in `docs/verification/nuc-image-identity.json` records computed local OCI
manifest hashes and their config digests; all four loaded NUC IDs matched those
manifest hashes. These identities prove transferred content, not upstream
publisher provenance.

The existing private gateway answered401 without credentials from the NUC.
`FACTORY_GATEWAY_KEY_FILE` references the existing owner-only NUC credential file;
no key is copied into source, arguments or the environment. Paid verification
uses best-effort reservations, not an actual-charge ceiling.

## Recovery scope

A real cold control-plane snapshot was streamed over SSH to the Mac, encrypted,
signed and restored with86 matching files. Backup keys stay on the Mac outside
the repository. Only dedicated factory services were stopped and restarted.
See `stapled-factory/deployment/nuc/BACKUP.md`. This first snapshot does not include
the subsequent worker recovery fixture, and it does not prove safe activation
from an older signed backup or whole-host recovery.

The earlier Railway persistent private Restate service and volume are preserved;
disposable capability sandboxes were removed. No further Railway deployment is
planned.

## Subsequent live and complete restore proof

The actual NUC live crash-recovery check passed. Source fencing, successor commit
and duplicate-invariant checks passed with five estimated reservations totaling
$1.579010. No actual charge ceiling is claimed. Unpaid worker/proxy negatives and
pinned TLS positive control passed; see `nuc-worker-verification.md`.

A subsequent exact joined snapshot retained the stopped probe Restate database
alongside its real stores/anchors/workspaces. Its signed off-host restore booted
successfully and matched WorkUnit successor revision, guarded result and Git HEAD,
with unchanged shared reservations. This supersedes the initial incomplete worker
backup limitation, while older-backup activation still requires quarantine.

## Trusted candidate acceptance

The actual NUC validator derived the source archive and diff from the selected
Git commits, verified the operator-signed local build and actual image config,
then ran the candidate with network-none, read-only root and no host mounts.
Good passed15/15, broken0/15, and secret-bearing source was rejected despite
passing15/15 wall checks. Archive/diff/signature/image substitutions were denied.
The validation CLI evaluated, signed and accepted through private Restate; stale,
tampered and replayed evidence was rejected. The public route stayed404.

Security engines were explicitly regex fallbacks/manifest flags; this does not
prove full external SAST coverage. Candidate validation grants no production
deploy/merge permission. See docs/verification/nuc-validation.json.

Actual release rollback9f40ca5→f5f18a4→9f40ca5 preserved validated state without
restoring Restate data. The current persistent worker still requires an explicit
per-attempt runtime configuration; arbitrary production dispatch stays blocked.
