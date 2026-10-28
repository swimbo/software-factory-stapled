# NUC deployment target

The user corrected the runtime target from Railway to the existing local NUC on
October 27. Source remains `swimbo/software-factory-stapled` on GitHub. All builds
and dependency installation run locally on the operator Mac, followed by source
and artifact transfer. No GitHub runners or remote provider builds are used.

## Verified control plane

- SSH alias `hermes-control`, host `nuc-hermes`, operator bo uid1000, Linux amd64,
  Docker29.8.0. Existing shared workloads were inventoried and left running.
- Frozen release:
  `/home/bo/.local/share/stapled-factory-control/releases/f5f18a4d848a687c9d9a2be7bd98dc3828bc4dd6`.
  All745 transferred file hashes matched. Release manifest SHA256:
  `b931dade2e2d4134baa84c433cf481409e28425fa50907ab2276b05e7a206653`.
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
