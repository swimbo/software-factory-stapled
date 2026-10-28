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

## Upstream verification (October 27, 2026)

`verify_upstream_image.py IMAGE@sha256:DIGEST --identity EXACT_PUBLISHER_IDENTITY
--issuer EXACT_OIDC_ISSUER` invokes Cosign with exact publisher and issuer checks,
requires a verified signature for the requested digest, and fails on unavailable
or unsigned inputs. Establish both trust values independently; never learn a
trusted identity from an unverified artifact. Successful image signature checks
still do not prove build provenance.

Live `cosign tree` discovery found no Cosign-layout artifacts for the current
pinned OpenHands, Python, and Restate digests. This did not cover embedded OCI
BuildKit attestations; the authenticated publication checks below supersede the
earlier broader absence claim. Discovery is not signature verification. Upstream
portable signatures remain unverified; operator signing cannot replace them.

### Authenticated upstream publication evidence (October 28, 2026)

The earlier `cosign tree` result was incomplete: it does not discover BuildKit
attestation manifests embedded in an OCI index. Both the pinned OpenHands and
Restate indexes contain these provenance records. They are unsigned metadata;
image publisher signatures remain unverified.

We independently authenticated the exact pinned digests against official upstream
records over GitHub HTTPS, rather than trusting identities claimed in images:

- OpenHands: [official successful build 40352978807](https://github.com/OpenHands/software-agent-sdk/actions/runs/40352978807),
  source `d0d9d81324210414462fb676b377e0563a08a0cb`, workflow
  `.github/workflows/server.yml`; logs publish the exact `90da82…` index.
- Restate: [official successful refresh 39179905022](https://github.com/restatedev/restate/actions/runs/39179905022),
  source `954797e4a36cf146ab4261cf0b6622375d1f5ae6`, workflow
  `.github/workflows/docker-refresh.yml`; logs publish the exact `2614a4…` index.
  Its embedded provenance records the original 1.8.0 image `24d9c3…` as an input;
  the original release is [38536370248](https://github.com/restatedev/restate/actions/runs/38536370248).
- Python: [Docker Official Images metadata at a pinned revision](https://github.com/docker-library/repo-info/blob/f33f60f13d5546d9b944e228248a6563be4986a2/repos/python/remote/3.11-slim.md)
  publishes the exact `f9fa7f…` index and platform-specific manifests/config IDs.
  [Official image source policy](https://github.com/docker-library/official-images/blob/1d9e44084e553b7f77c58a1a775535092116d22c/library/python)
  binds Python 3.11.15 slim-trixie to source
  `c859f2b9e567f72c94e00c969f916d3f92ae52a7`.

Compact publication receipts are in `docs/planning/stapled-factory/evidence/`.
`verify_docker_official.py` rechecks the pinned Docker metadata and source map.
`verify_upstream_workflow.py` accepts only its two reviewed upstream policies.
It rechecks the official repository, source revision, successful workflow, event,
main branch, actor, exact publication job/step and image reference/digest inside
that step's timestamp interval. It independently hashes the registry index bytes
and records platform child manifests. Unrelated image references, echoed digests,
PR runs, missing logs and registry substitutions fail closed. Restate masks its
owner in logs; the reviewed workflow's owner expression plus exact official
registry index check provides the namespace binding. This verifies online provider publication records. It does not
verify a portable signature, independently reproduce image bytes, audit every
transitive dependency or automatically approve activation. The independent
approval policy must identify this evidence level explicitly.

For the pinned Python index, Linux amd64 is manifest
`sha256:a01e48f10f90ac3ee65ef6937b9d7c831a3570c81b98d39a547ff09fb359de7f`
and config/image ID
`sha256:a8a3e0a84b0d5fab2b3b4b32e89715a7384b7af2f81b5e82d203d12828cb2578`.
A different initializer image ID must not be described as this base without a
separate verified platform/derivation binding.

The router proxy adds `pricing_policy.py` to its fixed copied files and requests the existing gateway `auto` alias. The new proxy build/audit receipt and NUC unpaid isolation evidence are in planning evidence/router-proxy-build-audit.json and nuc-router-images-unpaid-isolation-2026-10-28.json. See ROUTING.md for session identity and best-effort estimate boundaries.
