"""The 0-1 risk machinery of §7.3, shared by the BTC scorecard and the macro page.

Every test here exists because a bug written for daily data behaved differently
on a monthly one. `risk.py` is the one module in the pipeline that is fed series
of four different cadences (daily rates, weekly claims, monthly CPI, quarterly
GDP) through a single code path, so "rows" and "elapsed time" diverge silently.
"""

import datetime as dt

import numpy as np
import polars as pl
import pytest

from pipeline.metrics import risk


def _monthly(n: int, start=dt.date(1990, 1, 31)) -> list[dt.date]:
    """`n` month-ends. Approximated at 30-day steps; the cadence is the point."""
    return [start + dt.timedelta(days=30 * i) for i in range(n)]


def _daily(n: int, start=dt.date(2020, 1, 1)) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range(n)]


# ---------------------------------------------------------------------------
# expanding_rank: the no-look-ahead rule
# ---------------------------------------------------------------------------

def test_rank_excludes_the_observation_being_scored():
    """A rank that counted itself would score today partly against today.

    With 60 strictly-increasing values the last one is above all 59 before it,
    so the rank is 1.0. Including itself would give 59/60.
    """
    values = np.arange(60, dtype=float)
    out = risk.expanding_rank(values)
    assert out[-1] == pytest.approx(1.0)
    # And the mirror: the smallest value last ranks at the bottom.
    descending = np.arange(60, 0, -1, dtype=float)
    assert risk.expanding_rank(descending)[-1] == pytest.approx(0.0)


def test_rank_is_nan_before_the_minimum_observation_count():
    values = np.arange(100, dtype=float)
    out = risk.expanding_rank(values)
    assert np.isnan(out[:risk.MIN_RANK_OBSERVATIONS - 1]).all()
    assert not np.isnan(out[risk.MIN_RANK_OBSERVATIONS - 1])


def test_rank_stays_inside_zero_and_one():
    rng = np.random.default_rng(11)
    out = risk.expanding_rank(rng.normal(0, 1, 500))
    finite = out[~np.isnan(out)]
    assert finite.min() >= 0.0 and finite.max() <= 1.0


# ---------------------------------------------------------------------------
# risk_series: the warm-up §7.3 asks for
# ---------------------------------------------------------------------------

def test_warmup_is_enforced_and_measured_in_elapsed_days():
    """§7.3 asks for two to four years before a reading counts as signal.

    MIN_WARMUP_DAYS existed for that and was never applied; the only gate was
    MIN_RANK_OBSERVATIONS, which is 60 OBSERVATIONS — two months on a daily
    series. The macro page told the reader a component needs 730 days before
    it is scored, which was not true of the code underneath it.
    """
    n = 900
    dates = _daily(n)
    values = np.arange(n, dtype=float)
    out = risk.risk_series(dates, values)

    # Day 60 has enough observations for a percentile but nowhere near enough
    # history for it to mean anything. This is the reading that used to publish.
    assert np.isnan(out[100])
    cutoff = next(i for i, d in enumerate(dates)
                  if (d - dates[0]).days >= risk.MIN_WARMUP_DAYS)
    assert np.isnan(out[:cutoff]).all()
    assert not np.isnan(out[-1])


def test_warmup_counts_days_not_rows_on_a_monthly_series():
    """730 rows is two years of daily data and sixty years of monthly data.

    A monthly series crosses 730 ELAPSED days at its 25th observation. Counting
    rows would have demanded 730 months.
    """
    n = 400
    dates = _monthly(n)
    values = np.arange(n, dtype=float)
    out = risk.risk_series(dates, values)
    # The binding constraint here is MIN_RANK_OBSERVATIONS (60 months), not the
    # warm-up, and the series must still eventually score.
    assert not np.isnan(out[-1])
    # Rows-as-days would have left a 400-row monthly series entirely unscored.
    assert np.isfinite(out).sum() > 300


def test_a_series_shorter_than_the_warmup_scores_nothing():
    dates = _daily(300)
    out = risk.risk_series(dates, np.arange(300, dtype=float))
    assert np.isnan(out).all()


def test_risk_series_tolerates_no_dates():
    out = risk.risk_series(None, np.arange(100, dtype=float))
    assert len(out) == 100


# ---------------------------------------------------------------------------
# Component.lookback: the 6M / 1Y / 4Y columns
# ---------------------------------------------------------------------------

def _component(dates: list[dt.date]) -> risk.Component:
    """Risk that encodes its own index, so a lookback's answer names its row."""
    n = len(dates)
    return risk.Component("k", "label", "family",
                          np.arange(n, dtype=float) / 1000.0, dates)


def test_lookback_matches_by_date_on_a_monthly_series():
    """The bug: `lookback(182)` walked back 182 OBSERVATIONS on a monthly
    series — fifteen years — under a column headed "6m". Core PCE, payrolls
    and the unemployment rate are all monthly and all sit in the business-cycle
    composite, so three of its four families published readings from the 1990s
    as though they were from six months ago.
    """
    n = 300
    component = _component(_monthly(n))
    six_months = component.lookback(182)
    assert six_months is not None
    # 182 days back on a 30-day cadence is ~6 rows, so ~row 294 of 300.
    assert six_months == pytest.approx((n - 1 - 6) / 1000.0, abs=0.002)
    # The old positional read would have landed on row 117.
    assert six_months > ((n - 1 - 20) / 1000.0)


def test_lookback_is_unchanged_on_a_daily_series():
    """The fix must not move the case that was already right."""
    n = 900
    component = _component(_daily(n))
    assert component.lookback(182) == pytest.approx((n - 1 - 182) / 1000.0)
    assert component.lookback(365) == pytest.approx((n - 1 - 365) / 1000.0)


def test_lookback_takes_the_newest_observation_at_or_before_the_target():
    """A monthly series answers with the month that was current then.

    Interpolating a day that was never published would be fabrication (§0.2),
    and picking the NEXT observation would be look-ahead.
    """
    dates = [dt.date(2026, 1, 31), dt.date(2026, 4, 30), dt.date(2026, 7, 31)]
    component = risk.Component("k", "l", "f", np.array([0.1, 0.2, 0.3]), dates)
    # 92 days before 2026-07-31 is 2026-04-30 exactly.
    assert component.lookback(92) == pytest.approx(0.2)
    # 80 days back lands between April and July: the April reading stands.
    assert component.lookback(80) == pytest.approx(0.2)


def test_lookback_returns_none_when_the_target_predates_the_series():
    component = _component(_daily(30))
    assert component.lookback(365) is None
    assert component.lookback(1461) is None


def test_lookback_on_an_unavailable_component_is_none():
    component = risk.unavailable("k", "l", "f", "source unavailable")
    assert component.lookback(182) is None
    assert component.at() is None


def test_scorecard_row_keeps_an_unavailable_component_and_its_reason():
    """§0.2: a missing row says the picture is incomplete. An absent one
    implies the remaining metrics are the whole story."""
    rows = risk.scorecard([risk.unavailable("k", "Label", "Family", "402 paid")])
    assert len(rows) == 1
    assert rows[0]["available"] is False
    assert rows[0]["note"] == "402 paid"
    assert rows[0]["current"] is None and rows[0]["six_months"] is None


# ---------------------------------------------------------------------------
# detrended / composite
# ---------------------------------------------------------------------------

def test_detrend_is_refitted_point_in_time():
    """A single full-sample trend line would leak the future into every
    historical residual. Appending a bar must not move an earlier one."""
    n = 400
    dates = _daily(n)
    values = 100 * np.exp(np.linspace(0, 2, n))
    first = risk.detrended(dates, values)
    extended = risk.detrended(dates + _daily(1, dates[-1] + dt.timedelta(days=1)),
                              np.append(values, values[-1] * 3))
    assert np.allclose(first, extended[:n], equal_nan=True)


def test_composite_drops_a_missing_family_and_names_it():
    dates = _daily(900)
    good = risk.Component("a", "A", "Fam1", risk.risk_series(
        dates, np.arange(900, dtype=float)), dates)
    bad = risk.unavailable("b", "B", "Fam2", "no source")
    result = risk.composite({"Fam1": [good], "Fam2": [bad]})
    assert result["value"] is not None
    assert result["missing"] == ["Fam2"]
    assert "Fam2" not in result["families"]
    # The weight denominator covered only the family that computed.
    assert set(result["weights"]) == {"Fam1"}


def test_composite_with_nothing_available_is_none_not_zero():
    result = risk.composite({"Fam": [risk.unavailable("b", "B", "Fam", "no source")]})
    assert result["value"] is None
    assert result["families"] == {}
    assert result["note"]


def test_build_component_on_an_empty_frame_is_unavailable():
    frame = pl.DataFrame({"date": [], "value": []},
                         schema={"date": pl.Date, "value": pl.Float64})
    component = risk.build_component("k", "L", "F", frame)
    assert component.available is False
    assert component.note
