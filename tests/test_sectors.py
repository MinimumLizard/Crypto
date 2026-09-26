"""Sector indices and the RRG approximation (§6.8, §7.4)."""

import numpy as np
import pytest

from pipeline.metrics import sectors


@pytest.mark.parametrize("ratio,momentum,expected", [
    (105, 105, "leading"),
    (105, 95, "weakening"),
    (95, 95, "lagging"),
    (95, 105, "improving"),
    (100, 100, "leading"),
])
def test_quadrants_follow_from_the_two_axes(ratio, momentum, expected):
    assert sectors.quadrant(ratio, momentum) == expected


def test_rolling_zscore_is_nan_until_the_window_fills():
    values = np.arange(20, dtype=float)
    out = sectors._zscore_tail(values, 10)
    assert np.isnan(out[:9]).all()
    assert not np.isnan(out[9])


def test_rolling_zscore_of_a_flat_series_is_undefined_not_zero():
    """A constant series has no dispersion, so a z-score is not defined.

    Returning 0 would place a flat series exactly on the RRG crosshair as
    though it had been measured there.
    """
    out = sectors._zscore_tail(np.full(20, 5.0), 10)
    assert np.isnan(out[-1])


def test_rising_series_ends_with_a_positive_zscore():
    out = sectors._zscore_tail(np.arange(30, dtype=float), 10)
    assert out[-1] > 1.0


def test_rrg_constants_match_the_spec():
    """§7.4: N = 10 weeks for RS-Ratio, M = 4 weeks for RS-Momentum."""
    assert sectors.RS_RATIO_WEEKS == 10
    assert sectors.RS_MOMENTUM_WEEKS == 4
