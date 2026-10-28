"""Tier pricing config — run the same factory in per-token OR subscription mode.

A tier's `pricing_mode` selects the billing model. Ceilings are enforced in the
tier's own unit: quota/time always; dollars only for metered tiers.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from receipt import UsageRecord


@dataclass
class Ceiling:
    max_tokens: int | None = None
    max_retries: int | None = None
    max_wall_clock_ms: int | None = None
    max_cost_usd: float | None = None  # enforced ONLY when pricing_mode == "metered"


@dataclass
class TierConfig:
    name: str
    pricing_mode: str  # "subscription" | "metered"
    ceiling: Ceiling = field(default_factory=Ceiling)


def load_tiers(path: str | Path) -> dict[str, TierConfig]:
    raw = json.loads(Path(path).read_text())
    tiers = {}
    for name, spec in raw.items():
        ceiling = Ceiling(**spec.get("ceiling", {}))
        tiers[name] = TierConfig(
            name=name,
            pricing_mode=spec.get("pricing_mode", "subscription"),
            ceiling=ceiling,
        )
    return tiers


def check_ceiling(usage: UsageRecord, tier: TierConfig) -> str:
    """Return a one-line violation message, or "" when within the ceilings."""
    c = tier.ceiling
    if c.max_wall_clock_ms is not None and usage.wall_clock_ms > c.max_wall_clock_ms:
        return f"{tier.name}: wall-clock {usage.wall_clock_ms}ms exceeds {c.max_wall_clock_ms}ms"
    tokens = usage.tokens_in + usage.tokens_out
    if c.max_tokens is not None and tokens > c.max_tokens:
        return f"{tier.name}: {tokens} tokens exceed {c.max_tokens}"
    if c.max_retries is not None and usage.retries > c.max_retries:
        return f"{tier.name}: {usage.retries} retries exceed {c.max_retries}"
    # dollar ceiling applies only to metered tiers
    if (
        tier.pricing_mode == "metered"
        and c.max_cost_usd is not None
        and (usage.cost_estimate_usd or 0.0) > c.max_cost_usd
    ):
        return f"{tier.name}: cost ${usage.cost_estimate_usd:.2f} exceeds ${c.max_cost_usd:.2f}"
    return ""
