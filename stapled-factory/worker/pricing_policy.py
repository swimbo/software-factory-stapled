"""Conservative, best-effort reservations for the current Ollama price table.

These estimates do not enforce an actual-charge ceiling: prices, model routing,
context accounting, hidden calls and retries may differ from these assumptions.
The caller must use the proxy that enforces the 1024-token output limit.
"""

POLICY_ID = 'ollama-peak-2026-10-27-best-effort'
BUDGET_MICRODOLLARS = 2_000_000
INPUT_TOKENS = 1_048_576  # generous rounding of the published 1M context
OUTPUT_TOKENS = 1_024
ROUTER_MODEL = 'auto'
ROUTED_POLICY_ID = 'request-size-pro-2026-10-28-best-effort'
# Explicit allowance only: embedding/classifier retries and fallback-provider
# charges are unknown, so this is not a bound on the router's actual charges.
ROUTER_CLASSIFICATION_MICRODOLLARS = 10_000

# Prices are USD per million tokens; equivalently microdollars per token.
# Integer hundredths avoid floating-point under-reservation.
RATES = {
    'deepseek-v4.1-flash:1017': (30, 120),
    'ollama-cloud/deepseek-v4.1-flash:1017': (30, 120),
    'deepseek-v4-pro:0813': (132, 396),
}


def reservation_microdollars(model):
    """Round the assumed peak-rate input and output estimate up to a microdollar."""
    if model == ROUTER_MODEL:
        return router_reservation_microdollars(INPUT_TOKENS)
    try:
        input_rate, output_rate = RATES[model]
    except KeyError:
        raise ValueError('model has no configured best-effort pricing policy') from None
    hundredths = INPUT_TOKENS * input_rate + OUTPUT_TOKENS * output_rate
    return (hundredths + 99) // 100


def router_reservation_microdollars(body_bytes):
    """Estimate routed work at Pro peak using one UTF-8 byte per input token.

    Serialized JSON includes message history, system text and tools. This is a
    deliberately conservative estimate, not verified provider token accounting.
    The fixed output allowance remains 1024 tokens even for smaller requests.
    """
    if type(body_bytes) is not int or not 0 < body_bytes <= INPUT_TOKENS:
        raise ValueError('invalid router request size')
    input_rate, output_rate = RATES['deepseek-v4-pro:0813']
    return ((body_bytes * input_rate + OUTPUT_TOKENS * output_rate + 99) // 100
            + ROUTER_CLASSIFICATION_MICRODOLLARS)


def request_limit(model):
    if model == ROUTER_MODEL:
        return 6
    return BUDGET_MICRODOLLARS // reservation_microdollars(model)


def policy_id(model):
    reservation_microdollars(model)  # Reject models without a configured policy.
    return ROUTED_POLICY_ID if model == ROUTER_MODEL else POLICY_ID
