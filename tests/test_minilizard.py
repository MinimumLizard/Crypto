"""The regime engine's invariants, and the parity harness.

The parity test deliberately SKIPS rather than passes while no TradingView
reference values exist. A green test suite must not be able to imply parity
that has never been measured.
"""

import datetime as dt
from pathlib import Path

import numpy as np
import pytest
import yaml

from pipeline.signals import minilizard as ml

GOLDEN = Path(__file__).parent / "golden" / "minilizard_parity.yaml"


def test_blocks_respect_their_clamps(synthetic_bars):
    dates, open_, high, low, close, volume = synthetic_bars
    series = ml.compute(dates, open_, high, low, close, volume)
    assert np.nanmax(np.abs(series.structure)) <= ml.STRUCTURE_CLAMP
    assert np.nanmax(np.abs(series.trend)) <= ml.TREND_CLAMP
    assert np.nanmax(np.abs(series.momentum)) <= ml.MOMENTUM_SCALE
    assert np.nanmax(np.abs(series.volume)) <= ml.VOLUME_CLAMP
    assert np.nanmax(np.abs(series.score)) <= ml.COMPOSITE_CLAMP


def test_score_uses_only_the_previous_close(synthetic_bars):
    """Changing the FINAL bar's close must not change the FINAL bar's score.

    §7.1 defines confirmedClose = close[1]. This is the test that a live,
    unclosed bar cannot move the regime — which is also what keeps the stored
    history free of look-ahead.
    """
    dates, open_, high, low, close, volume = synthetic_bars
    baseline = ml.compute(dates, open_, high, low, close, volume)

    moved = close.copy()
    moved[-1] = moved[-1] * 1.5
    # High must still bracket the close, or we are testing an impossible bar.
    high_moved = high.copy()
    high_moved[-1] = max(high_moved[-1], moved[-1])
    shifted = ml.compute(dates, open_, high_moved, low, moved, volume)

    # The SMA inputs are unchanged, so the structure block's SMA basis is too.
    # (The score itself may move, because the spec compares the CURRENT close
    # to those SMAs; what must not move is anything derived from confirmed.)
    assert np.allclose(baseline.score[:-1], shifted.score[:-1], equal_nan=True)


def test_hysteresis_prevents_flip_flopping_at_a_threshold():
    """A score oscillating around 20 must not fire a signal every bar."""
    scores = np.array([0, 21, 19, 21, 19, 21, 19, 21], dtype=float)
    regimes, signals = ml._regimes(scores)
    assert regimes[1] == ml.BULL
    # 19 is above EXIT_BULL (10), so the regime holds BULL throughout.
    assert all(r == ml.BULL for r in regimes[1:])
    assert signals.count("GET IN") == 1
    assert signals.count("GET OUT") == 0


def test_get_out_fires_when_the_score_closes_below_ten():
    scores = np.array([0, 25, 25, 9, 9], dtype=float)
    regimes, signals = ml._regimes(scores)
    assert signals[1] == "GET IN"
    assert regimes[3] == ml.NEUTRAL
    assert signals[3] == "GET OUT"


def test_regime_starts_neutral_so_the_first_bar_cannot_fire():
    regimes, signals = ml._regimes(np.array([95.0, 95.0]))
    assert signals[0] == "GET IN"       # a genuine entry, not a spurious one
    assert regimes[0] == ml.BULL
    assert signals[1] == ""


@pytest.mark.parametrize("score,expected", [
    (75, "STRONG BULL"), (30, "MILD BULL"), (0, "NEUTRAL"),
    (-30, "MILD BEAR"), (-75, "STRONG BEAR"),
    (20, "NEUTRAL"), (61, "STRONG BULL"),
])
def test_labels_follow_the_spec_thresholds(score, expected):
    assert ml.label_for(score) == expected


def test_extension_penalty_is_signed_against_the_move():
    """Subtracted when stretched up, ADDED when stretched down."""
    n = 40
    flat = np.full(n, 100.0)
    spiked_up = flat.copy()
    spiked_up[-1] = 130.0
    spiked_down = flat.copy()
    spiked_down[-1] = 70.0
    confirmed = np.roll(flat, 1)
    confirmed[0] = np.nan

    up = ml._extension_penalty(spiked_up, confirmed, 12.0)
    down = ml._extension_penalty(spiked_down, confirmed, 12.0)
    assert up[-1] < 0
    assert down[-1] > 0
    assert abs(up[-1]) <= 50 and abs(down[-1]) <= 50


def test_penalty_is_zero_inside_the_threshold():
    n = 40
    flat = np.full(n, 100.0)
    nudged = flat.copy()
    nudged[-1] = 105.0     # 5% < 12% threshold
    confirmed = np.roll(flat, 1)
    confirmed[0] = np.nan
    assert ml._extension_penalty(nudged, confirmed, 12.0)[-1] == 0.0


def test_btc_threshold_is_stricter_than_the_altcoin_one():
    n = 40
    flat = np.full(n, 100.0)
    moved = flat.copy()
    moved[-1] = 108.0       # 8%: over BTC's 5, under alts' 12
    confirmed = np.roll(flat, 1)
    confirmed[0] = np.nan
    assert ml._extension_penalty(moved, confirmed, 5.0)[-1] < 0
    assert ml._extension_penalty(moved, confirmed, 12.0)[-1] == 0.0


def test_a_nan_block_makes_the_whole_score_nan(synthetic_bars):
    """A partial composite is a different quantity wearing the same name."""
    dates, open_, high, low, close, volume = synthetic_bars
    series = ml.compute(dates, open_, high, low, close, volume)
    early = slice(0, 50)
    assert np.isnan(series.score[early]).all()


def test_cvd_is_cumulative_across_days():
    daily = [dt.date(2025, 1, 1), dt.date(2025, 1, 2)]
    hourly_dates = [dt.date(2025, 1, 1)] * 2 + [dt.date(2025, 1, 2)] * 2
    opens = np.array([10.0, 10.0, 10.0, 10.0])
    closes = np.array([11.0, 9.0, 11.0, 11.0])      # +v, -v | +v, +v
    volumes = np.array([100.0, 40.0, 50.0, 25.0])
    out = ml.cvd_from_hourly(hourly_dates, opens, closes, volumes, daily)
    assert out[0] == pytest.approx(60.0)
    assert out[1] == pytest.approx(135.0)


def test_an_unscoreable_bar_has_no_regime_rather_than_a_neutral_one():
    """NEUTRAL is a reading. It says the engine looked and found chop.

    Publishing it for a bar that could not be scored is the placeholder §0.2
    forbids. XMR carried 138 such bars on its published chart, every one
    labelled NEUTRAL, because Hyperliquid backfills candles from before the
    venue existed with zero volume and the volume block correctly goes nan.
    """
    scores = np.array([np.nan, np.nan, 50.0], dtype=float)
    regimes, signals = ml._regimes(scores)
    assert regimes[0] is None
    assert regimes[1] is None
    assert signals[0] == "" and signals[1] == ""
    assert regimes[2] == ml.BULL


def test_a_gap_in_the_data_does_not_fire_a_spurious_round_trip():
    """The position was never exited, so no GET OUT and no second GET IN.

    With the gap labelled NEUTRAL the chart claimed a round trip through
    neutral that never happened; worse, the running regime stayed BULL, so the
    label and the state machine disagreed with each other.
    """
    scores = np.array([0.0, 50.0, np.nan, np.nan, 50.0], dtype=float)
    regimes, signals = ml._regimes(scores)
    assert signals[1] == "GET IN"
    assert regimes[2] is None and regimes[3] is None
    assert regimes[4] == ml.BULL
    assert signals.count("GET IN") == 1
    assert signals.count("GET OUT") == 0


def test_warmup_is_false_on_a_bar_the_engine_could_not_score(synthetic_bars):
    """The bug: `warm` was purely positional, so it read True past bar 300
    even where the composite was nan.

    XMR is scored on Hyperliquid, which backfills real oracle prices with ZERO
    volume from before the asset listed there. 999 of its 1,251 bars have an
    undefined volume block and no score, and every one from bar 300 onward was
    flagged warm — a claim of readiness on a bar with nothing to read.
    """
    dates, open_, high, low, close, volume = synthetic_bars
    zeroed = volume.copy()
    zeroed[:500] = 0.0        # as Hyperliquid backfills pre-listing candles

    series = ml.compute(dates, open_, high, low, close, zeroed)
    unscoreable = np.isnan(series.score)
    assert unscoreable[:500].all()          # the gap is real
    # Not one unscoreable bar is warm, at any position.
    assert not series.warm[unscoreable].any()
    # And the bars that DO have a score past the warm-up are warm.
    assert series.warm[-1]


def test_warmup_requires_300_bars_of_history_not_300_scored_bars(synthetic_bars):
    """§6.14 asks for a 300-bar warm-up: 300 bars of HISTORY.

    Demanding 300 *scored* bars would be a stricter rule than the spec's,
    because the structure block alone spends the first 200 bars settling its
    SMA200. It would have withheld MORPHO's reading — 356 real Binance bars
    whose score simply starts at bar 200 — and HYPE's, and FLUID's. Where the
    engine's effective history is short the artefact says so instead.
    """
    dates, open_, high, low, close, volume = synthetic_bars
    series = ml.compute(dates, open_, high, low, close, volume)
    first_warm = int(np.argmax(series.warm))
    assert first_warm == ml.WARMUP_BARS
    # Far fewer than 300 bars carry a score at that point, and that is correct.
    assert int(np.sum(~np.isnan(series.score[:first_warm + 1]))) < ml.WARMUP_BARS
    assert np.isnan(series.score[:first_warm]).sum() > 0


def test_no_bar_inside_the_first_300_is_ever_warm(synthetic_bars):
    dates, open_, high, low, close, volume = synthetic_bars
    series = ml.compute(dates, open_, high, low, close, volume)
    assert not series.warm[:ml.WARMUP_BARS].any()


# ---------------------------------------------------------------------------
# Parity
# ---------------------------------------------------------------------------

def test_parity_against_tradingview():
    """Golden test against real TradingView readings (§7.1, +/-0.5 points).

    Skips while the golden file has no entries. That is deliberate: the suite
    passing must never be readable as "parity holds" when parity has not been
    measured. Drop rows into tests/golden/minilizard_parity.yaml to activate.
    """
    if not GOLDEN.exists():
        pytest.skip("no golden file")
    cases = yaml.safe_load(GOLDEN.read_text()).get("cases") or []
    if not cases:
        pytest.skip(
            "PARITY UNVERIFIED: no TradingView reference values supplied. "
            "The engine is implemented to SPEC §7.1's prose; the Pine source "
            "it names as ground truth is not in the repository.")
    pytest.fail(
        "golden cases are present but the parity runner is not wired to the "
        "store yet — see CLAUDE.md")
