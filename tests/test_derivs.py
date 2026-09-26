"""Funding annualisation, OI readings and volatility (§6.9)."""

import numpy as np
import pytest

from pipeline.metrics import derivs


def test_funding_annualises_on_the_venues_own_interval():
    """The trap: the same raw rate is a different annual cost per venue.

    0.0001 hourly is 87.6%/yr; 0.0001 every eight hours is 10.95%/yr.
    """
    assert derivs.annualise_funding(0.0001, 1) == pytest.approx(87.6)
    assert derivs.annualise_funding(0.0001, 8) == pytest.approx(10.95)
    assert derivs.annualise_funding(0.0001, 4) == pytest.approx(21.9)


def test_a_constant_multiplier_can_invert_the_comparison():
    """Observed on 2026-09-26: BTC was +1.25e-05 hourly on Hyperliquid and
    -7.75e-06 on Binance's 8-hour schedule. Correctly annualised those are
    +10.95% and -0.85% -- opposite signs, which a shared multiplier hides."""
    hl = derivs.annualise_funding(1.25e-05, 1)
    binance = derivs.annualise_funding(-7.75e-06, 8)
    assert hl > 0 and binance < 0
    assert hl == pytest.approx(10.95, abs=0.01)
    assert binance == pytest.approx(-0.85, abs=0.01)


def test_annualise_returns_none_rather_than_zero_for_a_missing_rate():
    assert derivs.annualise_funding(None, 8) is None
    assert derivs.annualise_funding(0.0001, 0) is None


@pytest.mark.parametrize("oi,price,expected", [
    (5.0, 2.0, "longs building"),
    (5.0, -2.0, "shorts building"),
    (-5.0, -2.0, "long liquidation"),
    (-5.0, 2.0, "short covering"),
    (None, 2.0, "unknown"),
    (5.0, None, "unknown"),
])
def test_oi_quadrant_names_what_positioning_is_doing(oi, price, expected):
    assert derivs.oi_price_quadrant(oi, price) == expected


def test_realised_volatility_is_annualised():
    """A series with a known daily sigma should annualise by sqrt(365)."""
    rng = np.random.default_rng(4)
    daily_sigma = 0.02
    closes = 100 * np.exp(np.cumsum(rng.normal(0, daily_sigma, 400)))
    out = derivs.realised_volatility(closes, 90)
    assert out is not None
    expected = daily_sigma * np.sqrt(365) * 100
    assert out == pytest.approx(expected, rel=0.35)


def test_realised_volatility_refuses_a_window_it_cannot_fill():
    assert derivs.realised_volatility(np.array([100.0] * 10), 90) is None


def test_vol_premium_is_implied_minus_realised():
    assert derivs.vol_premium(40.0, 34.0) == pytest.approx(-6.0)
    assert derivs.vol_premium(None, 34.0) is None


def test_atr_percentile_ranks_against_the_assets_own_history():
    rng = np.random.default_rng(9)
    n = 400
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + 1.0
    low = close - 1.0
    # A final bar with a far wider range should land at a high percentile.
    high[-1] = close[-1] + 25
    low[-1] = close[-1] - 25
    out = derivs.atr_percentile(high, low, close)
    assert out["available"]
    assert out["percentile"] > 80
