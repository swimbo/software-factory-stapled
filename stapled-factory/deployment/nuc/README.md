# NUC control plane

Sandbox scope; production tool dispatch stays denied. These services trust bo,
the NUC host and Docker daemon. Other processes under bo can reach loopback
admin/raw ingress; untrusted worker containers must remain network-none.

Build spine and install its locked runtime dependencies locally, then transfer
that immutable release and prebuilt Linux Restate image to the NUC. No npm install,
Docker build or package download runs on the deployment host. Record source
revision, runtime dependency lock digest and image ID with the release.

`install.py` requires an existing 0700 state directory, owner-only Ed25519 PEM
`request-signing.pem`, its matching Restate `publickeyv1_` identity and preloaded
`sha256:` image. It installs new user units without overwriting or starting them.
Generate the key only on the trusted host using `openssl genpkey -algorithm
ed25519 -out request-signing.pem` under umask 077. Obtain the public key from an
isolated signing-enabled Restate bootstrap, then stop that bootstrap before using
the persistent data directory. Never print or transfer its private key.

Start only the factory units after checking ports: Restate raw ingress18080,
admin19070, signed endpoint19080 and bearer ingress19081, all loopback. Verify
`/health` on admin, reject unsigned endpoint traffic and unauthenticated ingress,
register endpoint through private admin, then invoke authenticated WorkUnit
getState and verify durable state across factory-only restart. Do not restart
existing Bifrost, databases or other workloads. Persist the bearer token path,
never its contents, in operator configuration.

The systemd user manager has linger enabled on the selected NUC. Stop/rollback
only these new units; preserve state and signing identity during release rollback.
A release rollback is not permission to restore an old live attempt snapshot.

## Verified NUC installation (2026-10-27)

Dedicated systemd user services were installed, started and enabled. Request
signing discovery registered `dp_13U5LTsGFcoZk7EG9Y3wQlH`. An authenticated
WorkUnit read returned 200; missing bearer returned 401; attempting claim via
allowlisted ingress returned 404; unsigned HTTP/2 endpoint discovery returned
401. An operator-only raw claim on `nuc-install-check` survived an actual
factory Restate restart. All five listeners (including fabric5122) were loopback.
Existing gateway and database workloads were untouched.

This verifies persistent signed control-plane operation, not an enabled worker:
worker runtime config, guarded attempt paths and final immutable release still
need binding. No live model key was uploaded by this installation. The current
trusted operator account can directly access raw/admin listeners.
