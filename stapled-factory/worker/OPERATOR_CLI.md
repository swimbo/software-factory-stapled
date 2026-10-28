# Operator-configured worker

`worker.py` runs only when `FACTORY_RUNTIME_CONFIG` names a JSON file owned by the service account with mode `0600`. The operator must trust the Docker daemon, the host, and each pinned image and command in this file. Do not let a work-unit request or sandbox write the file. A separate configuration and isolated workspaces are required per attempt. The CLI does not attest image provenance.

The JSON object has exactly these fields:

- `attempt_id`, `task_file`, and `repo`: the same values submitted by Restate. The repo is the canonical path of a clean isolated Git workspace.
- `baseline`: its full Git commit ID; `successor_workspace`: the canonical path of a separate clean standalone clone at that commit.
- `payload`: the OpenHands Agent Server conversation payload. `agent_settings.llm` must use `model: anthropic/ollama-cloud/deepseek-v4.1-flash:1017`, `base_url: http://127.0.0.1:8123`, `api_key: dummy`, and `max_output_tokens` no greater than 1024. The CLI supplies the initial message from `task_file`.
- `provision`: the exact operator-approved `worker_image`, `proxy_image`, `helper_image` immutable SHA-256 IDs, `worker_command`, `proxy_command`, `workspace`, `uid`, `worker_environment`, and `firewall` (`helper_image` ID and `gateway_ip`). See `RUNTIME_IMAGES.md` and the disposable probe for construction details.

The proxy command must bind the same attempt ID and model, pin the firewall gateway IP, and configure its budget store with `--pricing-policy-id ollama-peak-2026-10-27-best-effort`, `--ceiling-microdollars 2000000`, a future `--pricing-policy-expires-at`, `--worst-case-request-microdollars` at least the policy estimate, and an allowed `--max-requests`. The policy reserves $0.315802 per Flash request, at most six requests across the original and successor. It is a **best-effort estimate**, not a provider charge guarantee. The proxy's IP pin, egress firewall, and store checks still apply.

Initialize the guarded attempt database and anchor once before starting Restate. Protect and retain both, and configure `FACTORY_ATTEMPT_DB` and `FACTORY_ATTEMPT_ANCHOR` as separate absolute paths. This local pair does not resist a coordinated rollback. The source dispatch reads `refs.BIFROST_API_KEY` from the service account's `~/.dsh/.credentials.yaml` (mode `0600`); it passes the key to the proxy over stdin, not in command arguments, configuration, or the worker container. The successor reuses the original proxy and does not read the key again.

The configuration path is process-wide. This is a single-attempt integration, not a multi-tenant scheduler. Test a deployment's isolation, recovery, gateway routing and actual spend before relying on it.

### Deployment scope gate

The private operator configuration accepts `deployment_scope: "sandbox"` (the
compatibility default when omitted). All other scopes, including `production`,
fail before credentials are loaded or a worker is dispatched. This is an
explicit limitation of the current terminal runtime, not provenance verification:
OpenHands terminal operations are not mediated by the standalone guard. Immutable
Docker IDs identify bytes but do not authenticate their publisher. Privileged
external effects and production deployment require a separately reviewed tool
mediation path and independently verified build/promotion evidence. No config
boolean can waive this gate. Sandbox scope still assumes an honest operator and
Docker daemon; a dishonest operator relabeling production is outside this gate.
