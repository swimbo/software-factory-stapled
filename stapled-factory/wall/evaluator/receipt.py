"""UsageRecord — per-run resource usage, independent of pricing model.

The wall reports usage (tokens / requests / wall-clock / tier), not dollars.
Dollar `cost_estimate` is added only for metered tiers by the caller.
"""
from dataclasses import dataclass, asdict, field
from typing import Optional


@dataclass
class UsageRecord:
    tokens_in: int = 0
    tokens_out: int = 0
    requests: int = 0
    retries: int = 0
    wall_clock_ms: int = 0
    tier: str = "unknown"
    pricing_mode: str = "subscription"  # "subscription" | "metered"
    cost_estimate_usd: Optional[float] = None
    ceiling_violation: str = ""  # one-line message when a ceiling was exceeded, else ""

    def to_dict(self) -> dict:
        return asdict(self)


def new_subscription_record(**overrides) -> UsageRecord:
    return UsageRecord(pricing_mode="subscription", **overrides)


def new_metered_record(cost_estimate_usd: float, **overrides) -> UsageRecord:
    return UsageRecord(pricing_mode="metered", cost_estimate_usd=cost_estimate_usd, **overrides)
