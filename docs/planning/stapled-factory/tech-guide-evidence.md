# Tech-guide debate evidence

- [Restate service types](https://docs.restate.dev/concepts/services/): documents virtual objects' single-writer-per-key behavior and workflow execution by ID. These properties do not by themselves establish exactly-once execution of an external OpenHands action.
- [Restate awakeables](https://docs.restate.dev/develop/python/awakeables/): describes a durable trigger and callback pattern, including `ctx.run` around the trigger. The page illustrates the pattern; implementation and failure-window behavior must be verified for the pinned TypeScript SDK and worker.
- [Restate service configuration](https://docs.restate.dev/operate/configuration/services): identifies idempotency-key retention and timeout settings. These settings do not prove the downstream worker accepts an idempotency key.
- [OpenHands app conversation API](https://docs.openhands.dev/api-reference/get-conversation): describes the hosted application API, including a session key and status fields. It is not evidence that the separately deployed Agent Server has the same endpoints or authentication.
- [OpenHands app settings API](https://docs.openhands.dev/api-reference/store-settings): documents app settings including confirmation mode and a task budget. It does not establish equivalent enforcement in the pinned Agent Server or gateway.

The duplicate-effect crash window and need to reconcile uncertain outcomes are architectural inferences, not a claim that a specific downstream idempotency API exists. Sandbox isolation, actual egress restrictions, version compatibility, provider-side cost ceilings, usage completeness, and recovery remain unverified until tested in the deployed configuration.
