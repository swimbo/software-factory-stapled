# Runtime images

Build the three images locally from the repository root:

```sh
docker build --network none -f worker/Dockerfile.agent --build-arg WORKER_UID="$(id -u)" -t factory-agent:local worker
docker build --network none -f worker/Dockerfile.proxy -t factory-proxy:local worker
docker build -f worker/Dockerfile.firewall -t factory-firewall:local worker
```

The firewall build downloads distribution packages; inspect and approve the build inputs before using the image. The agent and proxy builds use pinned base images and require those bases to be present locally when network access is disabled. The initializer image is the same Python base used by the proxy image. Supply the full local image IDs, not tags, to the owner-controlled runtime configuration; inspect them with `docker image inspect IMAGE --format '{{.Id}}'`. Read the worker image's exact environment with `docker image inspect factory-agent:local --format '{{json .Config.Env}}'`.

The worker command is `tini -- python /opt/factory/agent_entrypoint.py`; the proxy command starts `python3 /opt/factory/egress_proxy.py` followed by its attempt-specific options. Provisioning checks the IDs and container topology before releasing the gateway credential. The proxy image and firewall helper remain trusted operator inputs: a content-addressed ID does not by itself verify provenance, package versions, or a privileged Docker daemon.

The disposable `python3 worker/probes/verify_firewalled_provision.py` builds these images, uses a dummy gateway credential, checks network denial and removes its resources. It does not demonstrate actual billing controls.

## Operator-signed sandbox approval

`operator_cli` supports `image_approval: {document: ABSOLUTE_PATH,
signature: ABSOLUTE_PATH}` in its private operator configuration. Provision the
public trust root independently through `FACTORY_IMAGE_APPROVAL_KEY`; all three
files must be operator-owned private regular files. Once this service setting is
present, missing approval fails closed before credential access or Docker effects.
The detached RSA/SHA-256 signature covers the exact document bytes; verify with
OpenSSL. The version 1 document contains exactly `version`, `scope` (`sandbox`),
`expires_at` (Unix seconds), `images` (worker_image, proxy_image, helper_image,
and firewall_image when configured), and `build_inputs` (absolute reviewed source
paths mapped to SHA-256 hex digests). Images must match immutable Docker IDs and
build-input content must remain unchanged. Expired, altered, mismatched and
unsigned approvals fail closed.

This records operator review and prevents substitution after review. It is not
an upstream publisher signature, reproducible-build proof, or evidence that the
review itself occurred. Without independently provisioning the service trust root,
the sandbox remains operator-trusted as before. Production remains denied.
