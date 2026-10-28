import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pricing_policy import reservation_microdollars, request_limit


def test_peak_estimates_round_up_and_fit_budget():
    assert reservation_microdollars('deepseek-v4.1-flash:1017') == 315802
    assert request_limit('deepseek-v4.1-flash:1017') == 6
    assert reservation_microdollars('deepseek-v4-pro:0813') == 1388176
    assert request_limit('deepseek-v4-pro:0813') == 1


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError):
        reservation_microdollars('unrecognized-model')


def test_observed_bifrost_flash_route_has_same_policy():
    assert reservation_microdollars('ollama-cloud/deepseek-v4.1-flash:1017') == reservation_microdollars('deepseek-v4.1-flash:1017')
    assert request_limit('ollama-cloud/deepseek-v4.1-flash:1017') == 6


def test_router_reserves_pro_peak_plus_explicit_allowance_by_body_size():
    from pricing_policy import router_reservation_microdollars, policy_id, POLICY_ID, ROUTED_POLICY_ID
    assert reservation_microdollars('auto') == 1398176
    assert router_reservation_microdollars(1000) == 15376
    assert router_reservation_microdollars(2000) > router_reservation_microdollars(1000)
    assert request_limit('auto') == 6
    assert policy_id('auto') == ROUTED_POLICY_ID
    assert policy_id('deepseek-v4.1-flash:1017') == POLICY_ID
    for invalid in (True, 0, -1, 1_048_577, 1.5):
        with pytest.raises(ValueError):
            router_reservation_microdollars(invalid)
