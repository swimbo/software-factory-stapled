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
