# Existing NUC complexity router integration

User chose the existing complexity router. Reuse the live gateway alias `auto`; do not implement or change the shared router. This continues Phase9 after the verified single-operator deployment.

## Current evidence ledger

- Live NUC config.db scoped routing rules and approved-key equality: aliasauto routes COMPLEX to Ollama DeepSeek Pro0813; MEDIUM/default to Flash1017. Ordinary provider fallback includes Vercel/OpenRouter; no trial header is sent.
- Live complexity semantic/LLM config: Vercel embedding and Luna classification count toward gateway budgets. Pricing/charged fallback totals are not verified and some logged costs are null.
- Live bifrost-context/relay.py credential-scopes the trusted session header and preserves it to Bifrost. Proxy must generate its own stable per-attempt session ID.
- Repository pricing_policy/egress_proxy enforce1MiB bodies,1024 output tokens and pre-forward reservations. Worker direct network remains denied; proxy pinned gateway TLS443 unchanged.

## Concise Mode B review

Finance reviewer: accept actual forwarded UTF8-body bytes estimated at one byte/token with known Pro peak rates,1024 output allowance and$0.01 classifier allowance. These are heuristics, not a total-charge guarantee. Keep six-request maximum and$2 estimated-total admission target; retain failed/unknown request reservations and null cost as unknown.

Security/operations reviewer: stable trusted session identity and signed modelalias are essential; do not accept arbitrary worker routing headers or enlarge egress. Actual simple/complex route logs must prove the gateway decision; historical config is not sufficient. Native gateway classifier/fallback charges remain unknown.

Implementation reviewer: preserve fixed-model policy behavior, give auto a separate policy ID and actual-body reservation path, reject incompatible fixed-reservation authority mode. Tests must reject direct-model bypass, changed headers, invalid costs and exhausted/expired budgets.

Moderator decision: use existingautoalias with above controls. Rejected alternatives: fixedFlash estimate hides changed pricing assumptions; fixed full-contextPro reservation permits only one call and prevents ordinary multistep tasks; local bespoke classification duplicates the existing router. This decision changes reservation estimates, not the user-approved best-effort/no-hard-cap billing policy.

## Verification required before deployment claim

- Exact local CI/build and updated proxy image identity/package audit.
- Signed operator runtime/artifact binding; preserve existing stores and shared workloads.
- Actual gateway logs correlated by stable session demonstrate simple/complex routes.
- Real Restate/OpenHands routed task commits; repeat changes no state/resources/accounting.
- Gateway-only containment remains verified; no enterprise/main-merge approval implied.
