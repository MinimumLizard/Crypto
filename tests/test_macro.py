"""Macro and liquidity (§6.10), and the composite construction (§7.3)."""

import datetime as dt

import numpy as np
import polars as pl
import pytest

from pipeline import store
from pipeline.metrics import macro


def _write(series_id: str, start: dt.date, values: list[float], step_days: int = 1):
    store.write_macro(series_id, pl.DataFrame({
        "obs_date": [start + dt.timedelta(days=i * step_days)
                     for i in range(len(values))],
        "value": [float(v) for v in values],
    }))


def test_net_liquidity_converts_units_before_subtracting(tmp_store):
    """WALCL and WTREGEN publish in MILLIONS, RRPONTSYD in BILLIONS.

    Subtracting them as they arrive is wrong by a factor of a thousand and
    produces a number that still looks like a plausible few trillion, which is
    why this needs a test rather than a careful read.
    """
    day = dt.date(2026, 1, 1)
    _write("WALCL", day, [7_000_000.0] * 5)      # $7.0tn, in millions
    _write("WTREGEN", day, [800_000.0] * 5)      # $0.8tn, in millions
    _write("RRPONTSYD", day, [200.0] * 5)        # $0.2tn, in BILLIONS

    result = macro.net_liquidity()
    assert result["available"]
    # 7000 - 800 - 200 = 6000 billion. The un-converted answer would be
    # 7,000,000 - 800,000 - 200 = 6,199,800, which is not a number of dollars
    # in any unit.
    assert result["latest_bn"] == pytest.approx(6000.0)
    assert result["parts_bn"]["Reverse repo"] == pytest.approx(200.0)
    assert result["parts_bn"]["Fed assets"] == pytest.approx(7000.0)


def test_net_liquidity_names_every_missing_component(tmp_store):
    _write("WALCL", dt.date(2026, 1, 1), [7_000_000.0] * 3)
    result = macro.net_liquidity()
    assert result["available"] is False
    assert "WTREGEN" in result["reason"] and "RRPONTSYD" in result["reason"]


def test_change_is_measured_by_date_not_by_row(tmp_store):
    """A weekly series has 52 rows a year, not 365.

    Indexing back by row count would compare a seven-times-longer horizon than
    the label claims, and silently.
    """
    weekly = pl.DataFrame({
        "date": [dt.date(2026, 1, 1) + dt.timedelta(days=7 * i) for i in range(60)],
        "value": [float(i) for i in range(60)],
    })
    # 91 days back is 13 weeks, so the change should be about 13 units.
    assert macro._change_over(weekly, 91) == pytest.approx(13.0, abs=1.0)
    # Row-indexing would have given 91.
    assert macro._change_over(weekly, 91) < 20


def test_change_over_returns_none_without_enough_history(tmp_store):
    short = pl.DataFrame({"date": [dt.date(2026, 9, 1)], "value": [1.0]})
    assert macro._change_over(short, 30) is None


def test_percentile_excludes_the_observation_being_ranked():
    values = np.array([1.0, 2.0, 3.0, 4.0, 10.0])
    assert macro._percentile(values) == 100.0
    values = np.array([10.0, 2.0, 3.0, 4.0, 1.0])
    assert macro._percentile(values) == 0.0
    assert macro._percentile(np.array([1.0])) is None


def test_ratio_is_computed_on_shared_dates(tmp_store):
    """Dividing each series' own last value is the 1833% cross-check bug.

    Here the denominator stops a week early; a naive last-over-last would use
    two different dates and quietly produce a ratio that never existed.
    """
    a = pl.DataFrame({
        "date": [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(14)],
        "value": [100.0 + i for i in range(14)],
    })
    b = pl.DataFrame({
        "date": [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(7)],
        "value": [10.0] * 7,
    })
    ratio = macro._ratio_series(a, b)
    assert ratio is not None
    # The last shared reading carries the stale denominator forward rather
    # than pairing day 14 of a with day 7 of b as if they were the same day.
    assert ratio["date"][-1] == dt.date(2026, 1, 14)
    assert float(ratio["value"][-1]) == pytest.approx(113.0 / 10.0)


def test_ratio_refuses_a_zero_denominator(tmp_store):
    a = pl.DataFrame({"date": [dt.date(2026, 1, 1)], "value": [5.0]})
    b = pl.DataFrame({"date": [dt.date(2026, 1, 1)], "value": [0.0]})
    assert macro._ratio_series(a, b) is None


def test_claims_compares_the_smoothed_series_to_its_own_low(tmp_store):
    """Claims are noisy weekly; the level matters less than the turn."""
    base = [200_000.0] * 60 + [260_000.0] * 4
    _write("ICSA", dt.date(2025, 1, 1), base, step_days=7)
    result = macro.claims()
    assert result["available"]
    assert result["average_4w"] == pytest.approx(260_000.0)
    assert result["low_52w"] == pytest.approx(200_000.0)
    assert result["above_low_pct"] == pytest.approx(30.0)


def test_claims_says_why_when_history_is_short(tmp_store):
    _write("ICSA", dt.date(2026, 1, 1), [200_000.0] * 5, step_days=7)
    result = macro.claims()
    assert result["available"] is False
    assert "weeks" in result["reason"] or "FRED" in result["reason"]


def test_every_panel_degrades_without_data_and_never_invents_one(tmp_store):
    """§0.2: a panel with no data states a reason. Never a zero."""
    for name, payload in (
            ("net_liquidity", macro.net_liquidity()),
            ("rates", macro.rates_table()),
            ("commodities", macro.commodities()),
            ("labour", macro.labour()),
            ("inflation", macro.inflation()),
    ):
        assert payload["available"] is False, name
        blob = repr(payload)
        assert "FRED" in blob or "reason" in blob, name


def test_composites_name_the_families_they_could_not_compute(tmp_store):
    """A composite must never be quietly averaged over fewer inputs."""
    result = macro.composites()
    for key in ("liquidity", "business_cycle"):
        assert result[key]["value"] is None
        assert result[key]["missing"], key
        # Every family is named, not just the count.
        assert all(isinstance(name, str) for name in result[key]["missing"])


def test_composite_families_cover_every_configured_weight():
    """A weight for a family that does not exist would silently do nothing."""
    from pipeline import registry

    weights = registry.composite_weights() or {}
    for key, spec in (("liquidity", macro.LIQUIDITY_FAMILIES),
                      ("business_cycle", macro.CYCLE_FAMILIES)):
        for family in (weights.get(key) or {}):
            assert family in spec, f"{key}: weight for unknown family {family}"


def test_every_composite_component_names_a_real_fred_series():
    from pipeline.fetchers import fred

    for spec in (macro.LIQUIDITY_FAMILIES, macro.CYCLE_FAMILIES):
        for members in spec.values():
            for _key, _label, series_id, _trending in members:
                assert series_id in fred.SERIES, series_id


def test_correlation_differences_a_series_that_goes_negative(tmp_store):
    """log() of a negative is where a wrapper reaches for abs().

    A spread like 10y-2y is routinely negative, and abs() would turn a fall
    through zero into a rise. The branch is chosen from the data, so this pins
    that a negative series never reaches log().
    """
    dates = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(200)]
    store.write_ohlcv("BTC", "test", pl.DataFrame({
        "date": dates, "interval": ["1d"] * 200,
        "open": [100.0] * 200, "high": [101.0] * 200, "low": [99.0] * 200,
        "close": [100.0 + i for i in range(200)], "volume": [1.0] * 200,
    }))
    _write("DFII10", dt.date(2026, 1, 1), [-0.5 + i * 0.01 for i in range(200)])

    result = macro.correlations()
    row = next(r for r in result["rows"] if r["label"] == "10-year real yield")
    # The point is that it computed at all: with abs()+log this raised or
    # returned a correlation with the sign flipped.
    assert row["available"] is True
    assert -1.0 <= row["correlation"] <= 1.0


def test_a_compounding_series_is_not_scored_on_its_level(tmp_store):
    """A price index detrends to a percentile pinned at 1.000 and stays there.

    `risk.detrended` fits log(value) against log(TIME), a power law. That is
    the right model for Bitcoin and the BTC scorecard detrends cleanly under
    it. A series growing at a constant RATE is exponential, not power-law, so
    the residual grows without bound and the rank pins at the maximum forever,
    carrying no information. Core PCE did exactly that on the first build with
    real data. Scoring the year-on-year change instead is both correct and
    what "inflation" means.
    """
    n = 400
    start = dt.date(1990, 1, 1)
    index = 100 * np.cumprod(np.full(n, 1 + 0.02 / 12))
    _write("PCEPILFE", start, list(index), step_days=30)

    families = macro._build_families({
        "Inflation": [("core_pce", "Core PCE", "PCEPILFE", macro.YOY)]})
    yoy_now = families["Inflation"][0].at()

    families_trend = macro._build_families({
        "Inflation": [("core_pce", "Core PCE", "PCEPILFE", macro.TREND)]})
    trend_now = families_trend["Inflation"][0].at()

    # The trend form is the broken one this test exists to prevent.
    assert trend_now == pytest.approx(1.0)
    # The year-on-year form of a CONSTANT growth rate is flat, so it ranks in
    # the middle rather than at an extreme -- it is not screaming.
    assert yoy_now is not None
    assert yoy_now < 0.99


def test_yoy_is_matched_by_date_not_row_offset(tmp_store):
    """Twelve rows back is a year on a monthly series, a quarter on a weekly."""
    weekly = pl.DataFrame({
        "date": [dt.date(2024, 1, 1) + dt.timedelta(days=7 * i) for i in range(120)],
        "value": [100.0 * (1.10 ** (i / 52.0)) for i in range(120)],
    })
    yoy = macro._yoy_frame(weekly)
    assert not yoy.is_empty()
    # 10% a year, so the last reading should be about +10%, not +10%*(52/12).
    assert float(yoy["value"][-1]) == pytest.approx(10.0, abs=0.5)


def test_yoy_of_a_short_series_is_empty_not_wrong(tmp_store):
    short = pl.DataFrame({
        "date": [dt.date(2026, 1, 1) + dt.timedelta(days=30 * i) for i in range(3)],
        "value": [100.0, 101.0, 102.0],
    })
    assert macro._yoy_frame(short).is_empty()


def test_every_composite_member_declares_a_known_transform():
    for spec in (macro.LIQUIDITY_FAMILIES, macro.CYCLE_FAMILIES):
        for members in spec.values():
            for _key, _label, _series_id, mode in members:
                assert mode in (macro.LEVEL, macro.YOY, macro.TREND), mode
