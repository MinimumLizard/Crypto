"""Key levels and the 50-week confirmation rule (§6.2, §6.3, §6.5).

The 50W rule is the one the brief is most specific about: **two consecutive
WEEKLY CLOSES** above the 50-week SMA. Not a daily close, not an intraweek
touch. This file had no tests, and the module was publishing a confirmation it
had not earned.
"""

import datetime as dt

import numpy as np
import polars as pl
import pytest

from pipeline.metrics import levels

MONDAY = dt.date(2024, 1, 1)        # a Monday, so weeks align with the anchor


def _daily(n: int, closes=None, *, start: dt.date = MONDAY) -> pl.DataFrame:
    close = (np.asarray(closes, dtype=float) if closes is not None
             else np.full(n, 100.0))
    return pl.DataFrame({
        "date": [start + dt.timedelta(days=i) for i in range(n)],
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": np.full(n, 1000.0),
        "interval": ["1d"] * n, "source": ["test"] * n,
    })


# ---------------------------------------------------------------------------
# to_weekly: what counts as a closed week
# ---------------------------------------------------------------------------

def test_a_partial_trailing_week_is_not_published_as_a_weekly_close():
    """The live defect: on 2026-10-04 the BTC series ended Wednesday
    2026-09-23. The calendar week had long passed, so a clock-based filter kept
    it, and Wednesday's close was published as that week's weekly close.
    """
    # Three weeks, then a Monday-Wednesday stub.
    frame = _daily(24)
    weekly = levels.to_weekly(frame)
    assert weekly.height == 3
    assert weekly["date"].to_list()[-1] == MONDAY + dt.timedelta(days=14)
    # The stub week never appears.
    assert MONDAY + dt.timedelta(days=21) not in weekly["date"].to_list()


def test_a_week_is_closed_once_the_data_reaches_its_final_day():
    frame = _daily(21)      # exactly three complete weeks
    assert levels.to_weekly(frame).height == 3
    frame = _daily(20)      # the third week is missing its Sunday
    assert levels.to_weekly(frame).height == 2


def test_resampling_is_deterministic_and_does_not_consult_the_clock():
    """The same stored bars resampled twice must give the same rows.

    A clock-based filter returned a different count on a Sunday than on a
    Monday, so a rebuild could move the SMA50W with no new data behind it.
    """
    frame = _daily(200)
    first = levels.to_weekly(frame)
    second = levels.to_weekly(frame)
    assert first["date"].to_list() == second["date"].to_list()
    assert first["close"].to_list() == second["close"].to_list()
    # And nothing in the result depends on today's date: a series that ended
    # years ago still yields all of its complete weeks.
    old = _daily(210, start=dt.date(2019, 1, 7))
    assert levels.to_weekly(old).height == 30


def test_an_interior_week_with_a_gap_is_kept_not_dropped():
    """Only the tail is trimmed. An interior missing Sunday is a data gap, and
    dropping the week outright would lose more than it protects."""
    frame = _daily(28)
    frame = frame.filter(pl.col("date") != MONDAY + dt.timedelta(days=13))
    weekly = levels.to_weekly(frame)
    assert weekly.height == 4
    assert MONDAY + dt.timedelta(days=7) in weekly["date"].to_list()


def test_weekly_aggregation_takes_the_extremes_of_the_week():
    closes = np.array([10.0, 20.0, 15.0, 30.0, 25.0, 12.0, 18.0])
    weekly = levels.to_weekly(_daily(7, closes))
    assert weekly.height == 1
    assert float(weekly["close"][0]) == pytest.approx(18.0)       # Sunday's close
    assert float(weekly["high"][0]) == pytest.approx(30.0 * 1.01)
    assert float(weekly["low"][0]) == pytest.approx(10.0 * 0.99)
    assert float(weekly["volume"][0]) == pytest.approx(7000.0)


def test_to_weekly_on_an_empty_frame_returns_empty():
    empty = pl.DataFrame(schema={"date": pl.Date, "open": pl.Float64,
                                 "high": pl.Float64, "low": pl.Float64,
                                 "close": pl.Float64, "volume": pl.Float64})
    assert levels.to_weekly(empty).is_empty()


# ---------------------------------------------------------------------------
# The 50-week confirmation rule
# ---------------------------------------------------------------------------

def _weeks_of(values: list[float]) -> pl.DataFrame:
    """Daily bars whose weekly closes are exactly `values` (Sunday closes)."""
    closes = []
    for value in values:
        closes.extend([value] * 7)
    return _daily(len(closes), closes)


def test_a_partial_week_cannot_complete_the_confirmation():
    """This is the signal flip the clock-based filter caused on BTC.

    Fifty weeks at 100, one closed week at 120, then a Monday-Wednesday stub at
    130. The rule is at 1 of 2, not confirmed. Counting the stub as a weekly
    close made it 2 of 2 and published CONFIRMED.
    """
    closes = [100.0] * 50 + [120.0]
    frame = _weeks_of(closes)
    stub = _daily(3, [130.0] * 3,
                  start=MONDAY + dt.timedelta(days=7 * len(closes)))
    frame = pl.concat([frame, stub])

    result = levels.fifty_week_tracker(frame)
    assert result["available"]
    assert result["consecutive_closes_above"] == 1
    assert result["confirmed"] is False
    assert result["last_weekly_close"] == pytest.approx(120.0)
    assert result["week_ending"] == str(MONDAY + dt.timedelta(days=7 * 50))


def test_two_consecutive_weekly_closes_above_confirm():
    frame = _weeks_of([100.0] * 50 + [120.0, 121.0])
    result = levels.fifty_week_tracker(frame)
    assert result["consecutive_closes_above"] == 2
    assert result["confirmed"] is True


def test_one_weekly_close_above_does_not_confirm():
    frame = _weeks_of([100.0] * 50 + [120.0])
    result = levels.fifty_week_tracker(frame)
    assert result["consecutive_closes_above"] == 1
    assert result["confirmed"] is False


def test_a_week_that_closes_back_below_resets_the_streak():
    frame = _weeks_of([100.0] * 50 + [120.0, 121.0, 90.0])
    result = levels.fifty_week_tracker(frame)
    assert result["consecutive_closes_above"] == 0
    assert result["confirmed"] is False


def test_an_intraweek_spike_that_closes_below_counts_for_nothing():
    """"Not an intraweek touch" is the module's own promise."""
    closes = []
    for value in [100.0] * 50:
        closes.extend([value] * 7)
    # The final week spikes to 400 midweek but closes at 95.
    closes.extend([95.0, 400.0, 400.0, 95.0, 95.0, 95.0, 95.0])
    result = levels.fifty_week_tracker(_daily(len(closes), closes))
    assert result["consecutive_closes_above"] == 0
    assert result["confirmed"] is False


def test_past_confirmations_record_one_entry_per_episode():
    """A five-week run above is one confirmation, not four."""
    frame = _weeks_of([100.0] * 50 + [120.0] * 5 + [90.0] * 3 + [130.0] * 4)
    result = levels.fifty_week_tracker(frame)
    assert len(result["past_confirmations"]) == 2
    for entry in result["past_confirmations"]:
        assert entry["close"] > entry["sma50w"]


def test_the_tracker_says_why_when_history_is_short():
    result = levels.fifty_week_tracker(_weeks_of([100.0] * 10))
    assert result["available"] is False
    assert "51" in result["reason"]


def test_distance_is_measured_against_the_level_it_names():
    frame = _weeks_of([100.0] * 50 + [110.0])
    result = levels.fifty_week_tracker(frame)
    expected = (result["last_weekly_close"] / result["sma50w"] - 1) * 100
    assert result["distance_pct"] == pytest.approx(expected, abs=0.01)


# ---------------------------------------------------------------------------
# key_levels
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("month", range(1, 13))
def test_previous_month_window_is_exactly_one_month(month):
    """Stepping back 31 days and truncating lands in the month BEFORE last
    whenever the intervening month is shorter than 31 days.

    That is March, May, July, October and December -- five months in twelve --
    each drawing a two-month extreme on the chart under the label "previous
    month".
    """
    start = dt.date(2025, 1, 1)
    last = dt.date(2026, month, 15)
    n = (last - start).days + 1
    # A ramp, so a two-month window has a provably different low from a
    # one-month window.
    closes = [100.0 + i for i in range(n)]
    frame = _daily(n, closes, start=start)

    result = levels.key_levels(frame)
    month_start = dt.date(2026, month, 1)
    want_start = (month_start - dt.timedelta(days=1)).replace(day=1)
    expected_low = 100.0 + (want_start - start).days
    assert result["prev_month_low"] == pytest.approx(expected_low * 0.99, rel=1e-9)
    expected_high = 100.0 + ((month_start - dt.timedelta(days=1)) - start).days
    assert result["prev_month_high"] == pytest.approx(expected_high * 1.01, rel=1e-9)


def test_previous_week_window_is_the_week_before_the_current_one():
    n = 60
    closes = [100.0 + i for i in range(n)]
    frame = _daily(n, closes)
    result = levels.key_levels(frame)
    last = MONDAY + dt.timedelta(days=n - 1)
    this_week = last - dt.timedelta(days=last.weekday())
    prev_start = this_week - dt.timedelta(days=7)
    expected_low = 100.0 + (prev_start - MONDAY).days
    expected_high = 100.0 + ((this_week - dt.timedelta(days=1)) - MONDAY).days
    assert result["prev_week_low"] == pytest.approx(expected_low * 0.99, rel=1e-9)
    assert result["prev_week_high"] == pytest.approx(expected_high * 1.01, rel=1e-9)


def test_key_levels_omits_a_level_it_cannot_compute_rather_than_zeroing_it():
    """§0.2: never a placeholder."""
    result = levels.key_levels(_daily(30))
    assert result["available"]
    assert result["sma20d"] is not None
    assert result.get("sma200d") is None          # 30 bars cannot make an SMA200
    assert "sma200w" not in result
    assert result["sma200d_distance_pct"] is None


def test_key_levels_on_an_empty_frame_states_a_reason():
    empty = pl.DataFrame(schema={"date": pl.Date, "open": pl.Float64,
                                 "high": pl.Float64, "low": pl.Float64,
                                 "close": pl.Float64, "volume": pl.Float64})
    result = levels.key_levels(empty)
    assert result["available"] is False and result["reason"]


def test_mayer_multiple_is_price_over_the_200_day():
    n = 300
    closes = [100.0 + i for i in range(n)]
    result = levels.key_levels(_daily(n, closes))
    assert result["mayer_multiple"] == pytest.approx(
        result["price"] / result["sma200d"], abs=0.001)


# ---------------------------------------------------------------------------
# sma / ema / returns
# ---------------------------------------------------------------------------

def test_sma_refuses_a_window_containing_a_gap():
    values = np.array([1.0, 2.0, np.nan, 4.0, 5.0, 6.0])
    out = levels.sma(values, 3)
    assert np.isnan(out[2]) and np.isnan(out[3]) and np.isnan(out[4])
    assert out[5] == pytest.approx(5.0)


def test_sma_is_nan_before_its_window_is_full():
    out = levels.sma(np.arange(10, dtype=float), 4)
    assert np.isnan(out[:3]).all()
    assert out[3] == pytest.approx(1.5)


def test_ema_is_seeded_on_the_first_full_window():
    values = np.arange(1, 21, dtype=float)
    out = levels.ema(values, 5)
    assert np.isnan(out[:4]).all()
    assert out[4] == pytest.approx(3.0)        # mean of 1..5
    assert out[-1] > out[4]


def test_ema_shorter_than_its_length_is_all_nan():
    assert np.isnan(levels.ema(np.arange(3, dtype=float), 5)).all()


def test_returns_are_none_where_history_is_too_short():
    out = levels.returns(_daily(10, [100.0 + i for i in range(10)]))
    assert out["r1d"] is not None
    assert out["r30d"] is None and out["r365d"] is None


def test_returns_refuse_a_zero_denominator():
    closes = [0.0] + [100.0] * 9
    out = levels.returns(_daily(10, closes), periods=(9,))
    assert out["r9d"] is None


def test_returns_on_an_empty_frame_are_empty():
    empty = pl.DataFrame(schema={"date": pl.Date, "close": pl.Float64})
    assert levels.returns(empty) == {}
