"""Pine semantics, checked against hand-computed values.

These are the tests that protect parity. Each one pins a property that
distinguishes Pine's definition from the textbook alternative, so swapping in a
"standard" implementation fails here rather than silently 5 points out on the
composite.
"""

import numpy as np
import pytest

from pipeline.signals import indicators as ind


def test_sma_is_nan_until_the_window_is_full():
    values = np.arange(1, 11, dtype=float)
    out = ind.sma(values, 5)
    assert np.isnan(out[:4]).all()
    assert out[4] == pytest.approx(3.0)      # mean(1..5)
    assert out[9] == pytest.approx(8.0)      # mean(6..10)


def test_rma_seeds_with_an_sma_not_with_the_first_value():
    """The seed is what separates RMA from an EMA started at values[0]."""
    values = np.arange(1, 21, dtype=float)
    out = ind.rma(values, 5)
    assert np.isnan(out[:4]).all()
    assert out[4] == pytest.approx(3.0)      # SMA(1..5), the seed
    # Then alpha = 1/5 recursion: 3 + (6-3)/5 = 3.6
    assert out[5] == pytest.approx(3.6)
    assert out[6] == pytest.approx(3.6 + (7 - 3.6) / 5)


def test_rma_differs_materially_from_ema_of_the_same_length():
    """If these ever agree, one of them is implemented wrong."""
    rng = np.random.default_rng(1)
    values = 100 + np.cumsum(rng.normal(0, 1, 300))
    assert abs(ind.rma(values, 14)[-1] - ind.ema(values, 14)[-1]) > 0.1


def test_ema_seeds_with_an_sma():
    values = np.arange(1, 11, dtype=float)
    out = ind.ema(values, 5)
    assert out[4] == pytest.approx(3.0)
    assert out[5] == pytest.approx(3.0 + (6 - 3.0) * (2 / 6))


def test_rsi_is_100_when_price_only_rises_and_0_when_it_only_falls():
    rising = np.arange(1, 40, dtype=float)
    assert ind.rsi(rising, 14)[-1] == pytest.approx(100.0)
    assert ind.rsi(rising[::-1].copy(), 14)[-1] == pytest.approx(0.0)


def test_rsi_stays_inside_0_and_100():
    rng = np.random.default_rng(5)
    values = 100 + np.cumsum(rng.normal(0, 2, 500))
    out = ind.rsi(values, 14)
    assert np.nanmin(out) >= 0.0
    assert np.nanmax(out) <= 100.0


def test_cci_uses_mean_absolute_deviation():
    """Hand-computed against MAD; the std-dev version gives a different number."""
    n = 25
    close = np.arange(1, n + 1, dtype=float)
    out = ind.cci(close, close, close, 20)
    typical = close
    window = typical[-20:]
    mean = window.mean()
    mad = np.mean(np.abs(window - mean))
    expected = (typical[-1] - mean) / (0.015 * mad)
    assert out[-1] == pytest.approx(expected)
    # And it is NOT the standard-deviation form.
    std_version = (typical[-1] - mean) / (0.015 * window.std())
    assert abs(out[-1] - std_version) > 1.0


def test_true_range_first_bar_has_no_previous_close():
    high = np.array([10.0, 12.0])
    low = np.array([8.0, 9.0])
    close = np.array([9.0, 11.0])
    out = ind.true_range(high, low, close)
    assert out[0] == pytest.approx(2.0)                # high - low only
    assert out[1] == pytest.approx(max(3.0, 3.0, 0.0))


def test_pivots_are_placed_at_the_confirmation_bar_not_the_pivot_bar():
    """A pivot must not be knowable before its right-hand bars exist."""
    high = np.array([1, 2, 3, 4, 9, 4, 3, 2, 1, 1, 1], dtype=float)
    low = np.full(11, 0.5)
    pivot_high, _ = ind.pivots(high, low, left=4, right=4)
    # The peak is at index 4; with right=4 it is only confirmed at index 8.
    assert np.isnan(pivot_high[4])
    assert pivot_high[8] == pytest.approx(9.0)


def test_anchored_vwap_restarts_on_a_new_anchor():
    high = low = close = np.array([10.0, 20.0, 30.0, 40.0])
    volume = np.ones(4)
    anchors = np.array([1, 1, 2, 2])
    out = ind.anchored_vwap(high, low, close, volume, anchors)
    assert out[1] == pytest.approx(15.0)     # mean of 10, 20
    assert out[2] == pytest.approx(30.0)     # reset: just 30
    assert out[3] == pytest.approx(35.0)     # mean of 30, 40


def test_rolling_extremes_tolerate_all_nan_windows():
    values = np.array([np.nan] * 5 + [1.0, 2.0, 3.0])
    out = ind.highest(values, 3)
    assert np.isnan(out[2])
    assert out[-1] == pytest.approx(3.0)
