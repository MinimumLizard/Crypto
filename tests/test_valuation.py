"""§6.4's definitions, and the supply-series cleaning the headline depends on."""

import datetime as dt

import numpy as np
import polars as pl
import pytest

from pipeline.metrics import valuation as V


def _series(start: dt.date, values: list[float]) -> pl.DataFrame:
    return pl.DataFrame({
        "date": [start + dt.timedelta(days=i) for i in range(len(values))],
        "value": [float(v) for v in values],
    })


def test_annualisation_multipliers_match_the_spec():
    assert V.BASES["30d"][1] == pytest.approx(365 / 30)
    assert V.BASES["90d"][1] == pytest.approx(365 / 90)
    assert V.BASES["365d"][1] == pytest.approx(1.0)
    assert V.DEFAULT_BASIS == "90d"


def test_annualising_refuses_a_window_it_cannot_fill():
    """Summing 40 days and calling it 90 understates by more than half."""
    short = _series(dt.date(2026, 8, 1), [100.0] * 40)
    out = V.annualised(short, "90d")
    assert not out.ok
    assert "90 days" in out.reason


def test_annualising_a_full_window_is_exact():
    frame = _series(dt.date(2026, 7, 1), [100.0] * 120)
    out = V.annualised(frame, "90d")
    assert out.value == pytest.approx(100 * 90 * (365 / 90))


def test_ratio_names_its_degenerate_cases_instead_of_printing_them():
    assert not V.ratio(100, 0).ok
    assert "zero" in V.ratio(100, 0).reason
    assert "negative" in V.ratio(100, -5).reason
    # A near-zero denominator is not a large multiple, it is not a multiple.
    assert "near zero" in V.ratio(1e9, 1.0).reason


def test_clean_supply_drops_points_above_total_supply():
    """CoinGecko reporting the fully diluted cap makes implied supply = max."""
    values = [650.0] * 30 + [1000.0] + [650.0] * 30
    frame = _series(dt.date(2026, 1, 1), values)
    cleaned, info = V.clean_supply(frame, total_supply=1000.0)
    # 1000 is not ABOVE total, so the median filter is what must catch it.
    assert cleaned.height == 60
    assert info["dropped_outlier"] == 1


def test_clean_supply_catches_a_spike_sitting_exactly_on_max_supply():
    """AAVE's artefact was 16,000,000 against a 16,000,000 cap: 5.3% high."""
    values = [15_190_000.0] * 40 + [16_000_000.0] * 3 + [15_190_000.0] * 40
    frame = _series(dt.date(2026, 1, 1), values)
    cleaned, info = V.clean_supply(frame, total_supply=16_000_000.0)
    assert info["dropped_outlier"] == 3
    assert float(cleaned["value"].max()) == pytest.approx(15_190_000.0)


def test_dilution_is_robust_to_a_one_day_spike():
    """A point measurement on the spike gave VIRTUAL -138% annualised."""
    values = [1000.0] * 100
    values[10] = 2000.0          # the artefact, at the 90-day endpoint
    frame = _series(dt.date(2026, 6, 1), values)
    cleaned, _ = V.clean_supply(frame, total_supply=2000.0)
    out = V.net_dilution(cleaned, 90)
    assert out.ok
    assert out.value == pytest.approx(0.0, abs=0.01)


def test_dilution_measures_a_real_trend():
    values = list(np.linspace(1000.0, 1100.0, 100))     # +10% over ~99 days
    frame = _series(dt.date(2026, 6, 1), values)
    out = V.net_dilution(frame, 90)
    assert out.ok
    assert out.value > 0.3        # ~10% over 90d annualises to ~40%


def test_dilution_refuses_an_impossible_rate():
    """Supply cannot shrink faster than it exists; that is a restatement."""
    values = [1000.0] * 50 + [10.0] * 50
    frame = _series(dt.date(2026, 6, 1), values)
    out = V.net_dilution(frame, 90)
    assert not out.ok
    assert "implausible" in out.reason


def test_persistent_steps_are_reported_as_supply_events_not_as_errors():
    """MORPHO's +50% day is an unlock -- the most important fact about it."""
    values = [100.0] * 50 + [150.0] * 50
    frame = _series(dt.date(2026, 1, 1), values)
    quality = V.supply_quality(frame)
    assert quality["confidence"] == "high"
    assert quality["event_count"] == 1
    assert quality["events"][0]["kind"] == "unlock or issuance"


def test_a_reverting_spike_is_not_reported_as_an_event():
    values = [100.0] * 50 + [150.0] + [100.0] * 50
    frame = _series(dt.date(2026, 1, 1), values)
    assert V.supply_quality(frame)["event_count"] == 0


def test_fully_sourced_and_cross_checked_grades_A():
    grade = V.grade(has_fees=True, has_revenue=True, has_holders=True,
                    holders_is_zero=False, has_supply=True, cross_checked=True)
    assert grade == "A"


def test_headroom_turns_an_annualised_rate_into_something_readable():
    """AAVE issued 3.6% of supply in 90 days, which annualises to +14.7%.

    Only 570k tokens remain below its 16m cap, so that rate has about a
    quarter of a year left in it. Without the headroom the number reads as a
    trend rather than as a terminal event.
    """
    circulating, total, dilution = 15_430_000.0, 16_000_000.0, 0.1475
    remaining = total - circulating
    years = remaining / (circulating * dilution)
    assert years == pytest.approx(0.25, abs=0.05)


def test_zero_capture_grades_D_not_F():
    """AAVE has 2,123 days of data and pays holders nothing. That is not
    'unmeasurable' -- it is a measurement."""
    assert V.grade(has_fees=True, has_revenue=True, has_holders=True,
                   holders_is_zero=True, has_supply=True, cross_checked=True) == "D"


def test_no_revenue_data_at_all_grades_F():
    assert V.grade(has_fees=False, has_revenue=False, has_holders=False,
                   holders_is_zero=False, has_supply=True, cross_checked=True) == "F"


def test_implied_growth_is_undefined_without_positive_earnings():
    out = V.implied_growth(1e9, V.Value(0.0), 0.25, 20)
    assert not out.ok
    assert "zero or negative" in out.reason


def test_implied_growth_solves_the_stated_equation():
    """MC = E0*(1+g)^5*exit / (1+r)^5, solved for g."""
    market_cap, earnings, discount, exit_multiple = 1e9, 1e7, 0.25, 20
    out = V.implied_growth(market_cap, V.Value(earnings), discount, exit_multiple)
    assert out.ok
    implied = earnings * (1 + out.value) ** 5 * exit_multiple / (1 + discount) ** 5
    assert implied == pytest.approx(market_cap, rel=1e-6)


def test_payback_refuses_when_holders_get_nothing():
    assert not V.payback_years(1e9, V.Value(0.0), 0.1).ok
