"""Conservative, best-effort reservations for the current Ollama price table.

These estimates do not enforce an actual-charge ceiling: prices, model routing,
context accounting, hidden calls and retries may differ from these assumptions.
The caller must use the proxy that enforces the 1024-token output limit.
"""

POLICY_ID = 'ollama-peak-2026-10-27-best-effort'
BUDGET_MICRODOLLARS = 2_000_000
INPUT_TOKENS = 1_048_576  # generous rounding of the published 1M context
OUTPUT_TOKENS = 1_024

# Prices are USD per million tokens; equivalently microdollars per token.
# Integer hundredths avoid floating-point under-reservation.
RATES = {
    'deepseek-v4.1-flash:1017': (30, 120),
    'ollama-cloud/deepseek-v4.1-flash:1017': (30, 120),
    'deepseek-v4-pro:0813': (132, 396),
}


def reservation_microdollars(model):
    """Round the assumed peak-rate input and output estimate up to a microdollar."""
    try:
        input_rate, output_rate = RATES[model]
    except KeyError:
        raise ValueError('model has no configured best-effort pricing policy') from None
    hundredths = INPUT_TOKENS * input_rate + OUTPUT_TOKENS * output_rate
    return (hundredths + 99) // 100


def request_limit(model):
    return BUDGET_MICRODOLLARS // reservation_microdollars(model)
