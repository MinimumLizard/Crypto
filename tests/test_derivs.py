"""Funding annualisation, OI readings and volatility (§6.9)."""

import numpy as np
import polars as pl
import pytest

from pipeline import store
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


# ---------------------------------------------------------------------------
# Self-inclusion and window honesty (audit, D031)
# ---------------------------------------------------------------------------

def test_atr_percentile_excludes_today_from_its_own_distribution():
    """Including it caps the reading at (n-1)/n, so a name at its busiest ATR
    in a year could never print 100 -- the same defect as D035."""
    import numpy as np

    n = 400
    rng = np.random.default_rng(5)
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + 1.0
    low = close - 1.0
    # Make the final bar by far the widest range in the series.
    high[-1] = close[-1] + 40.0
    low[-1] = close[-1] - 40.0

    result = derivs.atr_percentile(high, low, close)
    assert result["available"]
    assert result["percentile"] == 100.0


def test_atr_percentile_bottoms_at_zero():
    import numpy as np

    n = 400
    rng = np.random.default_rng(6)
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + 5.0
    low = close - 5.0
    high[-1] = close[-1] + 0.001
    low[-1] = close[-1] - 0.001
    result = derivs.atr_percentile(high, low, close)
    assert result["percentile"] < 2.0


def test_funding_window_is_measured_from_the_newest_observation(tmp_store):
    """`datetime.now()` made the window shrink as the store went stale: with
    snapshots 8 days behind, a "90-day" window held 82 days and said 90."""
    import datetime as dt

    import numpy as np

    n = 400
    # Every observation is a year old, so a now()-based cutoff would exclude
    # all of them and silently fall back to the full history.
    base = dt.datetime(2025, 9, 1, tzinfo=dt.UTC)
    rng = np.random.default_rng(3)
    store.write_snapshot("funding_hourly_TEST", pl.DataFrame({
        "observed_at": [base + dt.timedelta(hours=i) for i in range(n)],
        "value": rng.normal(1e-5, 2e-6, n),
    }), ["observed_at"])

    result = derivs.funding_zscore("TEST", window_days=90)
    assert result["available"]
    assert result["widened_to_full_history"] is False
    # The series spans ~16 days, so the window used is that, not the 90 asked for.
    assert result["window_days"] < 90
    assert result["requested_window_days"] == 90
    assert result["as_of"].startswith("2025-09")


def test_funding_zscore_excludes_today(tmp_store):
    import datetime as dt

    n = 400
    base = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    values = [1.0e-5] * (n - 1) + [9.0e-5]
    store.write_snapshot("funding_hourly_TEST", pl.DataFrame({
        "observed_at": [base + dt.timedelta(hours=i) for i in range(n)],
        "value": values,
    }), ["observed_at"])
    result = derivs.funding_zscore("TEST")
    # The prior window has zero variance, so no z-score can be formed from it.
    assert result["available"] is False
    assert "varied" in result["reason"]


def test_oi_change_needs_a_snapshot_a_day_back_not_merely_an_earlier_one(tmp_store):
    """The comment claimed "at least 20 hours back" and the code took the
    newest snapshot strictly before the latest -- on a manual dispatch minutes
    after a build, a "24h OI change" measured over minutes."""
    import datetime as dt

    base = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)
    contexts = pl.DataFrame({
        "observed_at": [base, base + dt.timedelta(minutes=5)],
        "coin": ["BTC", "BTC"],
        "open_interest_usd": [1.0e9, 2.0e9],
        "day_volume_usd": [1.0e9, 1.0e9],
        "mark": [100.0, 110.0],
        "prev_day_px": [100.0, 100.0],
        "funding_hourly": [1e-5, 1e-5],
    })
    rows = derivs.open_interest(contexts, {"BTC": 1.0e12})
    assert len(rows) == 1
    # Five minutes apart is not a daily change, so none is reported.
    assert rows[0]["oi_change_pct"] is None
    assert rows[0]["oi_change_hours"] is None
    assert rows[0]["snapshots_held"] == 2


def test_oi_change_is_reported_once_a_snapshot_is_far_enough_back(tmp_store):
    import datetime as dt

    base = dt.datetime(2026, 9, 25, 12, 0, tzinfo=dt.UTC)
    contexts = pl.DataFrame({
        "observed_at": [base, base + dt.timedelta(hours=24)],
        "coin": ["BTC", "BTC"],
        "open_interest_usd": [1.0e9, 2.0e9],
        "day_volume_usd": [1.0e9, 1.0e9],
        "mark": [100.0, 110.0],
        "prev_day_px": [100.0, 100.0],
        "funding_hourly": [1e-5, 1e-5],
    })
    rows = derivs.open_interest(contexts, {"BTC": 1.0e12})
    assert rows[0]["oi_change_pct"] == pytest.approx(100.0)
    assert rows[0]["oi_change_hours"] == pytest.approx(24.0)


def test_open_interest_accepts_a_string_observed_at(tmp_store):
    """The snapshot tables disagree: `perp_contexts` and `global` store
    `observed_at` as an ISO-8601 STRING, `funding_hourly_*` as a tz-aware
    Datetime. Sorting and grouping work on both, so the mismatch was invisible
    until the first subtraction -- which passed against a datetime fixture and
    raised TypeError on the real frame.
    """
    contexts = pl.DataFrame({
        "observed_at": ["2026-09-25T12:00:00+00:00", "2026-09-26T12:00:00+00:00"],
        "coin": ["BTC", "BTC"],
        "open_interest_usd": [1.0e9, 2.0e9],
        "day_volume_usd": [1.0e9, 1.0e9],
        "mark": [100.0, 110.0],
        "prev_day_px": [100.0, 100.0],
        "funding_hourly": [1e-5, 1e-5],
    })
    rows = derivs.open_interest(contexts, {"BTC": 1.0e12})
    assert rows[0]["oi_change_pct"] == pytest.approx(100.0)
    assert rows[0]["oi_change_hours"] == pytest.approx(24.0)


def test_funding_zscore_accepts_a_string_observed_at(tmp_store):
    import datetime as dt

    import numpy as np

    n = 400
    base = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    rng = np.random.default_rng(4)
    store.write_snapshot("funding_hourly_STR", pl.DataFrame({
        "observed_at": [(base + dt.timedelta(hours=i)).isoformat() for i in range(n)],
        "value": rng.normal(1e-5, 2e-6, n),
    }), ["observed_at"])
    result = derivs.funding_zscore("STR")
    assert result["available"]
    assert result["window_days"] > 0
