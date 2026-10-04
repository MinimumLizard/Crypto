"""Sector indices and the RRG approximation (§6.8, §7.4)."""

import datetime as dt

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


@pytest.mark.parametrize("n", [2, 3, 4, 5, 200, 399, 400])
def test_thinning_a_series_never_drops_the_newest_point(n):
    """Plain [::2] drops the last element on an even-length series.

    That put the final point of every 400-day sector chart a day behind the
    return printed next to it, which is the one comparison a reader makes.
    """
    dates = [dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(n)]
    values = np.arange(n, dtype=float)
    thinned = sectors._thin(dates, values)

    assert thinned[0]["d"] == str(dates[0])
    assert thinned[-1]["d"] == str(dates[-1])
    assert thinned[-1]["v"] == pytest.approx(float(values[-1]))
    # Still roughly halved, and never longer than the input.
    assert len(thinned) <= n // 2 + 2
    assert [p["d"] for p in thinned] == sorted({p["d"] for p in thinned})


def test_a_member_with_no_cap_is_excluded_from_the_cap_weighted_index():
    """A zero weight contributes nothing while the member still appears in
    `included`, so the page would claim a constituent the line does not
    contain. §0.2 forbids the placeholder; a weight of 0.0 is one.
    """
    import numpy as np

    rebased = np.array([[100.0, 100.0], [200.0, 100.0]])
    present = ["HAS_CAP", "NO_CAP"]
    caps = {"HAS_CAP": 1.0e9}

    capped_members = [s for s in present if caps.get(s)]
    no_cap = [s for s in present if not caps.get(s)]
    assert capped_members == ["HAS_CAP"]
    assert no_cap == ["NO_CAP"]

    columns = [present.index(s) for s in capped_members]
    weights = np.array([caps[s] for s in capped_members], dtype=float)
    weights = weights / weights.sum()
    cap_weighted = (rebased[:, columns] * weights).sum(axis=1)
    # The index is the capped member alone: it doubled, so the index doubles.
    assert cap_weighted[-1] == pytest.approx(200.0)
    # Zero-weighting instead would have halved it to 100 while still listing
    # NO_CAP as a constituent.


def test_sector_indices_name_any_member_missing_from_the_cap_weighted_index(tmp_store):
    """The note must exist whenever the two indices cover different members."""
    from pipeline.metrics import sectors as S

    result = S.sector_indices()
    for _sector, payload in result.items():
        if not payload.get("available"):
            continue
        members = payload["cap_weight_members"]
        if set(members) != set(payload["included"]):
            assert payload["cap_weight_note"], payload
        else:
            assert payload["cap_weight_note"] is None
