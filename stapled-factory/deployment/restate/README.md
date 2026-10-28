# Private Railway Restate control service

This Dockerfile pins Restate 1.8.0 and uses `/data` for persistent state with a
stable node and cluster identity. It is a single-node control-plane foundation,
not a deployed factory, an HA configuration or a completed restore test.

Current Railway target:

- Workspace: `swimbo's Projects` (`b3f99763-4b12-4f06-85fd-cb0379a2ebde`).
- Project: `software-factory-stapled` (`a6f58ea9-2483-4606-bb6a-d51a9c9d2082`).
- Environment: `production` (`6cdfc7ba-4996-4670-961b-8a290c305ff8`), the provider's default name.
- Service: `restate-control` (`35534918-94f1-45b4-bc95-eb3002e21463`).
- Volume: `restate-control-volume` mounted at `/data`.

Service settings are managed through Railway's API, not legacy `railway.toml`:
healthcheck `/health`, timeout 120 seconds, restart `ON_FAILURE`, maximum three
retries. Variables contain only `PORT=9070` and `RUST_LOG=warn` in addition to
provider defaults. No public domain, TCP proxy, worker, model credential or
workflow deployment is configured.

Verify over the registered account SSH key: server version, actual `/data`
mount and `curl -fsS http://127.0.0.1:9070/deployments` (initially an empty list).
Use `railway ssh --identity-file` with the service/project/environment above.
Do not upload the SSH private key or forward its agent into a worker.

The local CLI's existing account login works when stale `RAILWAY_TOKEN` and
`RAILWAY_API_TOKEN` overrides are omitted for that invocation. The connector
OAuth session separately requires reauthentication. Never print either token.

The worker provisioner needs Railway Sandbox VMs or another trusted Docker host;
an ordinary application service is not a drop-in Docker host. See the deployment
plan in `docs/planning/stapled-factory/railway-target.md` for gateway connectivity,
remote dispatch, isolation and restore gates that remain unverified.
