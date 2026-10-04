"""Bitcoin cycle detection and the analogs built on it (§6.5).

This file had no tests. `find_cycles` carried a docstring asserting that
requiring all-time highs "is what makes the count come out as four cycles
rather than six", and the function returned six.
"""

import datetime as dt

import numpy as np
import pytest

from pipeline.metrics import cycle


def _series(points: list[tuple[int, float]], start=dt.date(2010, 1, 1)):
    """Daily dates and a close series interpolated between (day, price) points."""
    days, prices = zip(*points, strict=False)
    n = days[-1] + 1
    closes = np.interp(np.arange(n), days, prices)
    return [start + dt.timedelta(days=i) for i in range(n)], closes


# ---------------------------------------------------------------------------
# find_cycles
# ---------------------------------------------------------------------------

def test_two_all_time_highs_close_together_are_one_cycle():
    """The live defect, in miniature.

    April 2013's $231 was a genuine all-time high that retraced 71%, and a new
    all-time high followed 239 days later. The all-time-high rule alone counted
    it as a cycle of its own, splitting 2013 exactly as an earlier bug had
    split 2018.
    """
    dates, closes = _series([
        (0, 10.0), (200, 100.0),      # first ATH
        (280, 25.0),                  # -75%, inside MIN_CYCLE_DAYS of the next
        (400, 500.0),                 # new ATH, 200 days after the first peak
        (800, 60.0),                  # -88%
        (1600, 2000.0),               # next cycle's ATH, well clear
        (2000, 400.0),
    ])
    cycles = cycle.find_cycles(dates, closes)
    peaks = [c["peak_date"] for c in cycles]
    assert len(cycles) == 2, peaks
    # The later, higher high is the cycle peak -- not the first one.
    assert cycles[0]["peak_price"] == pytest.approx(500.0)


def test_peaks_further_apart_than_the_threshold_stay_separate():
    dates, closes = _series([
        (0, 10.0), (200, 100.0), (600, 25.0),
        (1200, 500.0), (1700, 100.0),
        (2600, 2000.0), (3000, 500.0),
    ])
    cycles = cycle.find_cycles(dates, closes)
    assert len(cycles) == 3
    gaps = [
        (dt.date.fromisoformat(b["peak_date"]) - dt.date.fromisoformat(a["peak_date"])).days
        for a, b in zip(cycles, cycles[1:], strict=False)
    ]
    assert all(gap >= cycle.MIN_CYCLE_DAYS for gap in gaps)


def test_a_lower_high_inside_a_drawdown_is_not_a_cycle():
    """The 2018 case: March 2018's lower high sat inside December 2017's
    drawdown and was once counted as a cycle of its own."""
    dates, closes = _series([
        (0, 10.0), (400, 100.0),      # the ATH
        (500, 40.0), (560, 58.0),     # a lower high during the bear
        (900, 15.0),
    ])
    cycles = cycle.find_cycles(dates, closes)
    assert len(cycles) == 1
    assert cycles[0]["peak_price"] == pytest.approx(100.0)


def test_a_shallow_pullback_from_a_high_does_not_open_a_cycle():
    """Only a retracement past CYCLE_DRAWDOWN separates a cycle from a
    correction."""
    dates, closes = _series([
        (0, 10.0), (400, 100.0), (500, 70.0),   # -30%, a correction
        (900, 200.0), (1000, 150.0),
    ])
    cycles = cycle.find_cycles(dates, closes)
    # Only the live cycle, opened by the most recent all-time high.
    assert len(cycles) == 1
    assert cycles[0]["peak_price"] == pytest.approx(200.0)


def test_the_live_cycle_is_visible_before_it_has_halved():
    """Otherwise the current cycle would not appear until it had already
    fallen by more than half."""
    dates, closes = _series([
        (0, 10.0), (400, 100.0), (800, 20.0),   # a completed cycle
        (1600, 500.0), (1700, 420.0),           # a new ATH, barely retraced
    ])
    cycles = cycle.find_cycles(dates, closes)
    assert cycles[-1]["peak_price"] == pytest.approx(500.0)
    assert cycles[-1]["drawdown_pct"] < 55


def test_the_low_is_the_minimum_close_before_the_next_peak():
    dates, closes = _series([
        (0, 10.0), (400, 100.0), (700, 12.0), (800, 30.0),
        (1800, 500.0), (2000, 200.0),
    ])
    cycles = cycle.find_cycles(dates, closes)
    assert cycles[0]["low_price"] == pytest.approx(12.0, abs=0.5)
    assert cycles[0]["low_date"] == str(dates[700])


def test_drawdown_is_measured_from_the_peak_to_the_low():
    dates, closes = _series([(0, 10.0), (400, 100.0), (700, 25.0), (1600, 500.0)])
    cycles = cycle.find_cycles(dates, closes)
    assert cycles[0]["drawdown_pct"] == pytest.approx(75.0, abs=0.5)


def test_find_cycles_on_an_empty_series_returns_nothing():
    assert cycle.find_cycles([], np.array([])) == []


def test_the_real_btc_history_resolves_to_four_completed_cycles():
    """The count the module's own docstring commits to.

    Synthesised at the published peak and trough dates rather than read from
    the store, so the test does not depend on a fetch having run.
    """
    anchors = [
        (dt.date(2010, 7, 18), 0.09),
        (dt.date(2011, 6, 8), 29.0), (dt.date(2011, 11, 18), 2.05),
        (dt.date(2013, 4, 9), 231.0), (dt.date(2013, 7, 6), 66.0),
        (dt.date(2013, 12, 4), 1135.0), (dt.date(2015, 1, 14), 176.0),
        (dt.date(2017, 12, 16), 19650.0), (dt.date(2018, 12, 15), 3183.0),
        (dt.date(2021, 11, 8), 67555.0), (dt.date(2022, 11, 21), 15760.0),
        (dt.date(2025, 10, 6), 124720.0), (dt.date(2026, 6, 30), 58524.0),
        (dt.date(2026, 9, 23), 84397.0),
    ]
    start = anchors[0][0]
    points = [((d - start).days, p) for d, p in anchors]
    dates, closes = _series(points, start=start)

    cycles = cycle.find_cycles(dates, closes)
    assert len(cycles) == 5           # four completed plus the live one
    assert [c["peak_date"][:4] for c in cycles] == [
        "2011", "2013", "2017", "2021", "2025"]
    # The 2013 peak is December's, not April's.
    assert cycles[1]["peak_price"] == pytest.approx(1135.0, rel=0.01)
    # And no 88-day "peak to low" survives into the comparison set.
    position = cycle.current_position(dates, closes, cycles)
    assert len(position["prior_peak_to_low_days"]) == 4
    assert min(position["prior_peak_to_low_days"]) > 100


# ---------------------------------------------------------------------------
# The analogs
# ---------------------------------------------------------------------------

def test_roi_from_peak_is_a_ratio_starting_at_one():
    dates, closes = _series([(0, 10.0), (400, 100.0), (800, 20.0),
                             (1700, 500.0), (2000, 300.0)])
    cycles = cycle.find_cycles(dates, closes)
    result = cycle.roi_from_peak(dates, closes, cycles)
    for points in result["series"].values():
        assert points[0]["v"] == pytest.approx(1.0)


def test_the_band_counts_only_completed_cycles():
    dates, closes = _series([(0, 10.0), (400, 100.0), (800, 20.0),
                             (1700, 500.0), (2100, 100.0),
                             (3000, 2000.0), (3200, 1500.0)])
    cycles = cycle.find_cycles(dates, closes)
    result = cycle.roi_from_peak(dates, closes, cycles)
    assert result["n_completed_cycles"] == len(cycles) - 1
    assert str(result["n_completed_cycles"]) in result["how_to_read"]


def test_the_band_is_omitted_with_fewer_than_two_completed_cycles():
    dates, closes = _series([(0, 10.0), (400, 100.0), (800, 20.0), (1000, 30.0)])
    cycles = cycle.find_cycles(dates, closes)
    result = cycle.roi_from_peak(dates, closes, cycles)
    assert result["n_completed_cycles"] < 2
    assert result["band"] == []


def test_roi_from_bottom_is_a_multiple_of_the_low():
    dates, closes = _series([(0, 10.0), (400, 100.0), (800, 20.0),
                             (1700, 500.0), (2000, 300.0)])
    cycles = cycle.find_cycles(dates, closes)
    result = cycle.roi_from_bottom(dates, closes, cycles)
    for points in result["series"].values():
        assert points[0]["v"] == pytest.approx(1.0)


def test_current_position_on_no_cycles_states_a_reason():
    result = cycle.current_position([], np.array([]), [])
    assert result["available"] is False and result["reason"]


# ---------------------------------------------------------------------------
# midterm_monthly / ytd_roi
# ---------------------------------------------------------------------------

def _four_years() -> tuple[list, np.ndarray]:
    start = dt.date(2013, 1, 1)
    end = dt.date(2026, 9, 23)
    n = (end - start).days + 1
    dates = [start + dt.timedelta(days=i) for i in range(n)]
    closes = np.linspace(100.0, 10000.0, n)
    return dates, closes


def test_the_running_month_is_flagged_partial():
    """A month still open is not a comparable observation."""
    dates, closes = _four_years()
    result = cycle.midterm_monthly(dates, closes)
    row = next(r for r in result["rows"] if r["year"] == 2026)
    assert row["partial"] == [str(dates[-1].month)]
    completed = [r for r in result["rows"] if r["year"] != 2026]
    assert all(r["partial"] == [] for r in completed)


def test_each_month_is_measured_from_the_previous_months_close():
    """Months must join up rather than each starting fresh."""
    dates, closes = _four_years()
    result = cycle.midterm_monthly(dates, closes)
    row = next(r for r in result["rows"] if r["year"] == 2018)
    assert row["months"]["1"] is not None
    assert row["months"]["6"] is not None


def test_midterm_monthly_names_no_source_of_its_own():
    """It used to assert "Prices are CoinMetrics reference rates", which is
    true of 2014 and false of 2018, 2022 and 2026."""
    dates, closes = _four_years()
    result = cycle.midterm_monthly(dates, closes)
    assert "CoinMetrics" not in result["how_to_read"]
    assert result["source_note"] == ""
    passed = cycle.midterm_monthly(dates, closes, source_note="spliced: x then y")
    assert passed["source_note"] == "spliced: x then y"


def test_midterm_monthly_is_order_independent():
    """`base` reads the previous row's close, so the frame must be sorted."""
    dates, closes = _four_years()
    ordered = cycle.midterm_monthly(dates, closes)
    index = list(range(len(dates)))[::-1]
    shuffled = cycle.midterm_monthly([dates[i] for i in index], closes[index])
    assert ordered["rows"] == shuffled["rows"]


def test_ytd_roi_is_a_fraction_of_the_january_level():
    dates, closes = _four_years()
    result = cycle.ytd_roi(dates, closes)
    for year, points in result["series"].items():
        assert points[0]["d"] == 1, year
        assert points[0]["v"] == pytest.approx(1.0), year


def test_ytd_roi_skips_a_year_with_too_little_data():
    dates = [dt.date(2022, 12, 20) + dt.timedelta(days=i) for i in range(10)]
    result = cycle.ytd_roi(dates, np.linspace(100.0, 110.0, 10))
    assert "2022" not in result["series"]


# ---------------------------------------------------------------------------
# halving
# ---------------------------------------------------------------------------

def test_halving_estimates_from_block_height():
    result = cycle.halving(900_000)
    assert result["available"]
    assert result["last_halving_block"] == 840_000
    assert result["next_halving_block"] == 1_050_000
    assert result["blocks_remaining"] == 150_000


def test_halving_without_a_height_states_a_reason():
    result = cycle.halving(None)
    assert result["available"] is False and result["reason"]
