# Railway target and access plan

Status: control-plane foundation deployed and approved-key SSH verified, 2026-10-27. Project `software-factory-stapled` (`a6f58ea9-2483-4606-bb6a-d51a9c9d2082`) in workspace `swimbo's Projects` (`b3f99763-4b12-4f06-85fd-cb0379a2ebde`); remote https://github.com/swimbo/software-factory-stapled. No worker sandbox or model workflow is deployed.

## Verified foundation

Environment `production` (provider default name) is `6cdfc7ba-4996-4670-961b-8a290c305ff8`. Private `restate-control` service `35534918-94f1-45b4-bc95-eb3002e21463` has volume `bf9371fb-4138-4bf1-a6a8-1975b9c5c65d` mounted at `/data`. Deployment `952d7e5c-add6-4049-b619-7aeec0d0d943` is SUCCESS with image `sha256:2967e561a0603e09f1cc6656e79e0d557ff45132f8db26d48fdb0fda9a05a602`. Source Dockerfile pins the Restate 1.8.0 base digest. Approved account SSH key authentication and actual remote commands verified server version, the ext4 volume mount and empty `/deployments` API. Agent forwarding was disabled on direct SSH verification.

Railway readback verifies `/health` with 120-second timeout, three failure restarts, and no public/custom domains. A first redeploy failed while legacy config settings were being corrected; a fresh upload without deprecated `railway.toml` succeeded. Configuration is now explicit service API settings. CLI account access works when stale environment token overrides are omitted; connector OAuth remains independently expired. No private key or Bifrost credential was uploaded.

## Control plane and worker placement

Use ordinary Railway services for durable Restate and the authenticated spine/validation endpoint, with persistent volumes and explicit restore verification. Pin source revision and image identity in the deployment evidence. Keep Restate admin and raw ingress private; expose only the authenticated allowlisted application ingress. The existing local worker CLI cannot simply become an ordinary service: it requires an operator-trusted Docker daemon, filesystem paths, Unix sockets, firewall setup and an operator-owned runtime JSON.

Railway Sandboxes are Linux VMs suitable for the Docker worker provisioner. Railway's June 12 changelog explicitly documents a preinstalled Docker daemon and CLI. Use one disposable VM per attempt, with separate original/successor Docker workspaces and a retained attempt identity. Destroy the VM only after results and recovery evidence are durably exported. A checkpoint of a live attempt is not a coordinated backup; do not restore it as an active worker without fencing and reconciliation.

Current sandbox docs distinguish default ISOLATED mode (internet access, no environment private network) from PRIVATE mode (environment private network plus internet). Neither mode is a gateway-only egress policy. Preserve the worker's network-none setup, dedicated proxy, IP/TLS binding, and negative network checks inside the VM. PRIVATE adds access to trusted services at the VM boundary and must not be granted to untrusted worker containers.

## Access and credentials

An approved Railway account SSH public key permits sandbox SSH and port forwarding. Use the existing private key through SSH locally; never upload it, forward its agent into a worker, or store it in project variables. Successful SSH proves session access only. SDK/API project management needs its own authenticated capability; an account key does not establish a general API token. Parent session should verify account/workspace and target IDs from authenticated reads before mutation.

The Bifrost hostname currently resolves through the private tailnet. Railway internet access does not imply tailnet reachability. Verify a trusted host-side transport to that gateway before any model run. Do not publish the gateway, disable certificate checks, or reuse unrelated Tailscale credentials as an expedient. Keep the actual Bifrost key in the trusted proxy's stdin path; never expose it to the worker or logs.

## Implementation and acceptance sequence

1. Verify approved-key session access and record only account, workspace, project, environment and sandbox IDs; never record tokens.
2. Create the dedicated project and isolated disposable sandbox. Check Docker version, daemon confinement support, kernel firewall behavior and user-namespace/security defaults before running the existing provisioner.
3. Build or pull immutable trusted images. Verify provenance against the intended source revision; transfer the operator JSON as owner-only 0600 data outside every worker mount. Default deployment scope remains sandbox; current code explicitly denies production tool effects.
4. Run current provision-check and isolation negatives in the Railway VM: worker network/DNS, host fixtures, budget stores, Docker socket and proxy non-gateway destinations; verify workspace access as positive control.
5. Establish durable Restate/spine service connectivity to the provisioner through a narrow authenticated dispatcher. Existing local subprocess and filesystem integration needs an explicit remote adapter or co-location in the sandbox for a disposable probe; SSH health alone is not that adapter.
6. Bind validation to the deployed service identity/revision, not a caller-supplied URL. Run live in-flight kill/restart and duplicate replay. Require a clean verified descendant commit and unchanged duplicate state/container/ledger.
7. Exercise coordinated restore and old-worker fencing against independent persisted evidence. Record best-effort reservations separately from actual provider charges; the user withdrew the hard $2 ceiling.
8. Reassess enterprise High findings only after the corresponding real evidence exists. A project, SSH connection or sandbox hello-world does not close those findings.

## Official sources checked

- [Railway Sandboxes](https://docs.railway.com/sandboxes): VM lifecycle, SSH keys, environment modes, SDK, idle timeout and forwarding. Current docs supersede the older changelog's statement that sandboxes never join private networks.
- [Docker in Sandboxes](https://railway.com/changelog/2026-06-12-docker-in-sandboxes): preinstalled Docker daemon/CLI.
- [Railway over SSH](https://railway.com/changelog/2026-06-26-railway-over-ssh): approved-key terminal access.

The official documentation supports this design but does not verify this account's enrollment, runtime capabilities, private gateway route, or deployed isolation. Those require live checks.
