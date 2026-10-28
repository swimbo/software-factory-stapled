# Existing Bifrost complexity routing

New operator preparations request `anthropic/auto` from OpenHands. The local bridge and trusted proxy forward Anthropic messages to the existing NUC gateway `/anthropic/v1/messages` with `model:auto`. No classifier, shared routing rule or direct-provider route is created in this factory.

The current approved virtual key's gateway rules select DeepSeek Pro0813 for COMPLEX and Flash1017 for other tiers. The gateway may fall back through Vercel/OpenRouter and uses Vercel embedding/Luna classification. Those are gateway-side calls; factory egress remains pinned to its existing gateway TLS443 only. No experimental Flash-trial header is sent.

The proxy generates `x-bf-session-id` as UUID5(NAMESPACE_URL,`stapled-factory-attempt:`+original budget attempt). Worker headers cannot supply or override it. It stays stable across reconciliation/proxy reopen and differs across attempts, allowing safe correlation with gateway logs without logging prompts or credentials. A successor that shares the original proxy also shares its routing session.

## Best-effort reservation policy

For auto only, reserve before forwarding:

`ceil((actual serialized UTF8 body bytes ×132 +1024×396)/100) +10000 microdollars`

This estimates input at one byte per token using known Pro peak rates, permits1024 output tokens and adds$0.01 as a heuristic classifier allowance. Six requests maximum and$2 estimated-total admission remain enforced. Large requests consume more of that estimate; a full1MiB request reserves1,398,176microdollars. Failed or uncertain requests retain their reservation. The route has its own policy ID; fixed-model policies remain compatible. The fixed-reservation authority backend does not support auto and rejects it before credential read.

These heuristics do not measure actual provider charges or guarantee a monetary cap. Hidden reasoning tokens, classifier/embedding calls, charged fallback attempts, gateway context changes and unverified external pricing may differ. Null logged costs are unknown, never zero.

## Verification

Tests cover unchanged body forwarding, exact auto admission, spoofed header rejection, stable/distinct session identity, variable durable reservations, expiry and exhausted-budget rejection. Real route checks must join the trusted session ID to gateway log classification tier and actual provider/model; the requested alias alone is not proof. Each new release still requires local CI, exact image audit/identity, signed operator code/runtime/task acceptance and private service binding. Preserve prior attempts and ledgers; never alter an existing attempt's pricing or fingerprint.
