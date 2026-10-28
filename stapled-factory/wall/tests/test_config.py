"""Tests for dual pricing-mode config + ceiling enforcement."""
from config import Ceiling, TierConfig, check_ceiling
from receipt import UsageRecord


def test_subscription_enforces_quota_but_not_dollars():
    tier = TierConfig("cheap", "subscription", Ceiling(max_wall_clock_ms=100, max_cost_usd=None))
    # wall-clock ceiling applies
    assert check_ceiling(UsageRecord(wall_clock_ms=200), tier) != ""
    # dollar cost is ignored on a subscription tier
    assert check_ceiling(UsageRecord(wall_clock_ms=10, cost_estimate_usd=999.0), tier) == ""


def test_metered_enforces_dollars():
    tier = TierConfig("frontier", "metered", Ceiling(max_cost_usd=2.0))
    assert check_ceiling(UsageRecord(cost_estimate_usd=3.5, wall_clock_ms=10), tier) != ""
    assert check_ceiling(UsageRecord(cost_estimate_usd=1.0, wall_clock_ms=10), tier) == ""


def test_token_ceiling_applies_in_both_modes():
    sub = TierConfig("cheap", "subscription", Ceiling(max_tokens=100))
    met = TierConfig("frontier", "metered", Ceiling(max_tokens=100))
    over = UsageRecord(tokens_in=60, tokens_out=60)
    assert check_ceiling(over, sub) != ""
    assert check_ceiling(over, met) != ""
