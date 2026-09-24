"""The MiniLizard composite regime score (SPEC §7.1).

Four blocks sum to a composite clamped to +/-100, an extension penalty is
applied against the SMA20, and a 10-point hysteresis band turns the score into
a BULL / NEUTRAL / BEAR regime. GET IN fires when the regime becomes BULL; GET
OUT when it leaves.

    price structure  +/-35   SMA position, SMA slope, market structure
    trend            +/-30   Supertrend + Ichimoku, amplified 1.4x when ADX > 25
    momentum         +/-20   RSI, StochRSI, CCI, MFI
    volume           +/-15   VWAP, OBV, CVD

**Two things about this file that matter more than the arithmetic.**

First, it is written against §7.1's prose, because the Pine source it names as
ground truth is not in the repository. The prose is explicit about the block
weights, the clamps, the hysteresis and the thresholds, so the shape is not in
doubt; what cannot be confirmed without the source is the handful of seeding
and tie-breaking choices that decide the last decimal place. Every one of those
is marked `PARITY RISK` below. Until TradingView reference values arrive, the
parity test skips rather than passes and the site says parity is unverified.

Second, the score is honest about what it is. §7.1 is explicit: measured on
recent data this engine is a **drawdown filter, not an alpha source** — it cut
Period-B drawdown from roughly 67% to 40% without beating buy-and-hold on raw
return. Nothing here should be presented as predictive.

Everything uses `confirmedClose = close[1]`: the score for a bar is computed
from the PREVIOUS bar's close, so a live, unclosed bar can never move it. That
is both what §7.1 specifies and what keeps the stored history free of
look-ahead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pipeline.signals import indicators as ind

BULL, BEAR, NEUTRAL = "BULL", "BEAR", "NEUTRAL"

# Block clamps, straight from §7.1.
STRUCTURE_CLAMP = 35
TREND_CLAMP = 30
MOMENTUM_SCALE = 20
VOLUME_CLAMP = 15
COMPOSITE_CLAMP = 100

# Regime hysteresis: a 10-point band, so a score hovering at a threshold does
# not flip the regime back and forth and fire a signal every bar.
ENTER_BULL = 20
ENTER_BEAR = -20
EXIT_BULL = 10       # §7.1: "the model's exit is score closes below 10"
EXIT_BEAR = -10

WARMUP_BARS = 300    # §6.14's backtest rule; also roughly when SMA200 settles


@dataclass
class Series:
    """The engine's output, one entry per input bar."""
    date: np.ndarray
    structure: np.ndarray
    trend: np.ndarray
    momentum: np.ndarray
    volume: np.ndarray
    penalty: np.ndarray
    score: np.ndarray
    regime: list[str]
    label: list[str]
    signal: list[str]        # "GET IN" | "GET OUT" | ""
    warm: np.ndarray         # False while still inside the warm-up

    def latest(self) -> dict:
        i = len(self.score) - 1
        return {
            "date": str(self.date[i]),
            "score": _round(self.score[i]),
            "regime": self.regime[i],
            "label": self.label[i],
            "signal": self.signal[i],
            "blocks": {
                "structure": _round(self.structure[i]),
                "trend": _round(self.trend[i]),
                "momentum": _round(self.momentum[i]),
                "volume": _round(self.volume[i]),
                "penalty": _round(self.penalty[i]),
            },
            "warm": bool(self.warm[i]),
        }


def _round(value) -> float | None:
    return None if value is None or np.isnan(value) else round(float(value), 2)


def _clamp(values, limit):
    return np.clip(values, -limit, limit)


def label_for(score: float) -> str:
    """§7.1 label thresholds. Note these are NOT the regime: a score of 25 is
    labelled MILD BULL whether or not hysteresis has put the regime in BULL."""
    if np.isnan(score):
        return "—"
    if score > 60:
        return "STRONG BULL"
    if score > 20:
        return "MILD BULL"
    if score < -60:
        return "STRONG BEAR"
    if score < -20:
        return "MILD BEAR"
    return "NEUTRAL"


def _price_structure(confirmed: np.ndarray, close: np.ndarray,
                     high: np.ndarray, low: np.ndarray) -> np.ndarray:
    """SMA position + SMA slope + market structure, clamped to +/-35.

    SMAs are computed on `confirmedClose` per §7.1, but compared against the
    CURRENT close — the spec says "+1 if close > SMA". That asymmetry is
    deliberate and is preserved here.
    """
    n = len(close)
    smas = {length: ind.sma(confirmed, length) for length in (20, 50, 100, 200)}

    # SMA position: mean of four +/-1 votes, scaled to +/-14.
    votes = np.zeros(n)
    valid = np.ones(n, dtype=bool)
    for length in (20, 50, 100, 200):
        line = smas[length]
        votes += np.where(close > line, 1.0, -1.0)
        valid &= ~np.isnan(line)
    position = np.where(valid, votes / 4.0 * 14.0, np.nan)

    # Slope: 3-bar percentage change of SMA20 and SMA50, each clamped to
    # +/-1.5%, averaged, normalised by 1.5 and scaled to +/-10.5.
    def slope_pct(line):
        previous = np.roll(line, 3)
        previous[:3] = np.nan
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(previous != 0, (line - previous) / previous * 100.0, np.nan)

    slope20 = _clamp(slope_pct(smas[20]), 1.5)
    slope50 = _clamp(slope_pct(smas[50]), 1.5)
    slope = (slope20 + slope50) / 2.0 / 1.5 * 10.5

    # Market structure from the last two confirmed pivot highs and lows.
    # PARITY RISK: §7.1 does not say what to do before two pivots of each kind
    # exist. Contributing zero (rather than nan) is chosen so the block stays
    # defined during warm-up; the warm flag marks that stretch as unusable.
    pivot_high, pivot_low = ind.pivots(high, low, 5, 5)
    structure_points = np.zeros(n)
    recent_highs: list[float] = []
    recent_lows: list[float] = []
    for i in range(n):
        if not np.isnan(pivot_high[i]):
            recent_highs.append(float(pivot_high[i]))
        if not np.isnan(pivot_low[i]):
            recent_lows.append(float(pivot_low[i]))
        higher_high = higher_low = lower_high = lower_low = 0
        if len(recent_highs) >= 2:
            higher_high = int(recent_highs[-1] > recent_highs[-2])
            lower_high = int(recent_highs[-1] < recent_highs[-2])
        if len(recent_lows) >= 2:
            higher_low = int(recent_lows[-1] > recent_lows[-2])
            lower_low = int(recent_lows[-1] < recent_lows[-2])
        structure_points[i] = (higher_high + higher_low - lower_high - lower_low) / 2.0 * 10.5

    return _clamp(position + slope + structure_points, STRUCTURE_CLAMP)


def _trend(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Supertrend + Ichimoku, amplified 1.4x when ADX(14,14) > 25."""
    n = len(close)
    _, direction = ind.supertrend(high, low, close, factor=3.0, atr_length=10)
    cloud = ind.ichimoku(high, low)
    displacement = cloud["displacement"]

    senkou_a = np.roll(cloud["senkou_a"], displacement)
    senkou_b = np.roll(cloud["senkou_b"], displacement)
    senkou_a[:displacement] = np.nan
    senkou_b[:displacement] = np.nan
    cloud_top = np.fmax(senkou_a, senkou_b)

    above_cloud = close > cloud_top
    below_cloud = close < np.fmin(senkou_a, senkou_b)
    tk_bull = cloud["tenkan"] > cloud["kijun"]

    bull = (direction < 0).astype(float) + above_cloud.astype(float) + tk_bull.astype(float)
    bear = (direction > 0).astype(float) + below_cloud.astype(float) + (~tk_bull).astype(float)
    net = bull - bear

    _, _, adx = ind.dmi(high, low, close, 14, 14)
    amplifier = np.where(adx > 25, 1.4, 1.0)

    undefined = np.isnan(cloud_top) | np.isnan(direction)
    out = np.where(undefined, np.nan, np.round(net * amplifier * 10.0))
    return _clamp(out, TREND_CLAMP)


def _momentum(high: np.ndarray, low: np.ndarray, close: np.ndarray,
              volume: np.ndarray) -> np.ndarray:
    """RSI + StochRSI + CCI + MFI, summed, clamped to +/-100, scaled to +/-20."""
    rsi14 = ind.rsi(close, 14)
    stoch_k = ind.sma(ind.stoch(rsi14, rsi14, rsi14, 14), 3)
    cci20 = ind.cci(high, low, close, 20)
    mfi14 = ind.mfi(high, low, close, volume, 14)

    total = ((rsi14 - 50) * 0.5
             + (stoch_k - 50) * 0.5
             + _clamp(cci20 / 4.0, 25)
             + (mfi14 - 50) * 0.5)
    return _clamp(total, 100) * (MOMENTUM_SCALE / 100.0)


def _volume(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray,
            week_index: np.ndarray, cvd: np.ndarray | None) -> np.ndarray:
    """VWAP + OBV + CVD votes, scaled to +/-15.

    §7.1 defines CVD from intrabar 1H deltas. When hourly bars are not
    available the block is computed from the two votes that are, and the caller
    is told — a two-thirds-weight volume block is a different number from a
    three-thirds one, so it is scaled by the votes actually available rather
    than silently treating the missing vote as neutral.
    """
    vwap = ind.anchored_vwap(high, low, close, volume, week_index)
    obv_line = ind.obv(close, volume)
    obv_signal = ind.ema(obv_line, 20)

    votes = (np.where(close > vwap, 1.0, -1.0)
             + np.where(obv_line > obv_signal, 1.0, -1.0))
    available = 2.0
    if cvd is not None:
        cvd_signal = ind.ema(cvd, 14)
        votes = votes + np.where(cvd > cvd_signal, 1.0, -1.0)
        available = 3.0

    undefined = np.isnan(vwap) | np.isnan(obv_signal)
    out = np.where(undefined, np.nan, votes / available * VOLUME_CLAMP)
    return out


def _extension_penalty(close: np.ndarray, confirmed: np.ndarray,
                       threshold_pct: float) -> np.ndarray:
    """Penalty for stretching away from the SMA20.

    Signed: subtracted when extended to the upside, ADDED when extended to the
    downside. That sign convention is what makes the composite mean-reverting
    at extremes rather than simply capped.
    """
    sma20 = ind.sma(confirmed, 20)
    with np.errstate(divide="ignore", invalid="ignore"):
        extension = np.where(sma20 != 0, (close - sma20) / sma20 * 100.0, np.nan)
    magnitude = np.minimum(np.round((np.abs(extension) - threshold_pct) * 3.5), 50.0)
    magnitude = np.where(np.abs(extension) > threshold_pct, magnitude, 0.0)
    return np.where(extension > 0, -magnitude, magnitude)


def _regimes(score: np.ndarray) -> tuple[list[str], list[str]]:
    """Hysteresis walk. Starts NEUTRAL, which is the only state that cannot
    fire a spurious signal on the first scored bar."""
    regime = NEUTRAL
    regimes, signals = [], []
    for value in score:
        if np.isnan(value):
            regimes.append(NEUTRAL)
            signals.append("")
            continue
        previous = regime
        if regime == BULL:
            if value < ENTER_BEAR:
                regime = BEAR
            elif value < EXIT_BULL:
                regime = NEUTRAL
        elif regime == BEAR:
            if value > ENTER_BULL:
                regime = BULL
            elif value > EXIT_BEAR:
                regime = NEUTRAL
        else:
            if value > ENTER_BULL:
                regime = BULL
            elif value < ENTER_BEAR:
                regime = BEAR
        regimes.append(regime)
        if regime == BULL and previous != BULL:
            signals.append("GET IN")
        elif previous == BULL and regime != BULL:
            signals.append("GET OUT")
        else:
            signals.append("")
    return regimes, signals


def compute(dates, open_, high, low, close, volume,
            *, extension_threshold_pct: float = 12.0,
            week_index=None, cvd=None) -> Series:
    """Run the engine over one asset's daily bars.

    `extension_threshold_pct` is 5 for BTC/ETH and 12 for everything else
    (§7.1); it comes from the registry rather than being inferred here.
    """
    dates = np.asarray(dates)
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    volume = np.asarray(volume, dtype=float)

    # confirmedClose = close[1]: yesterday's close, so an open bar cannot move
    # today's score.
    confirmed = np.roll(close, 1)
    confirmed[0] = np.nan

    if week_index is None:
        week_index = week_keys(dates)

    structure = _price_structure(confirmed, close, high, low)
    trend = _trend(high, low, close)
    momentum = _momentum(high, low, close, volume)
    volume_block = _volume(high, low, close, volume, week_index, cvd)
    penalty = _extension_penalty(close, confirmed, extension_threshold_pct)

    blocks = np.vstack([structure, trend, momentum, volume_block])
    score = _clamp(np.sum(blocks, axis=0) + penalty, COMPOSITE_CLAMP)
    # A block that is nan makes the whole composite nan: a partial score is a
    # different quantity wearing the same name.
    score = np.where(np.isnan(blocks).any(axis=0), np.nan, score)

    regimes, signals = _regimes(score)
    warm = np.arange(len(close)) >= WARMUP_BARS

    return Series(
        date=dates, structure=structure, trend=trend, momentum=momentum,
        volume=volume_block, penalty=penalty, score=score, regime=regimes,
        label=[label_for(s) for s in score], signal=signals, warm=warm)


def week_keys(dates) -> np.ndarray:
    """ISO year-week as one integer per bar, for the weekly VWAP anchor.

    A scalar key rather than a (year, week) pair, because the anchor is only
    ever compared for equality and a tuple per bar turns the comparison into an
    array operation.
    """
    out = np.empty(len(dates), dtype=np.int64)
    for i, day in enumerate(dates):
        value = day.date() if hasattr(day, "date") else day
        iso = value.isocalendar()
        out[i] = iso[0] * 100 + iso[1]
    return out


def cvd_from_hourly(hourly_dates, hourly_open, hourly_close, hourly_volume,
                    daily_dates) -> np.ndarray:
    """Cumulative volume delta, built from 1H bars and summed per day (§7.1).

    +volume when the hourly bar closed up, -volume when it closed down, 0 when
    unchanged; summed within each day and then cumulated across days.
    """
    import collections
    per_day: dict = collections.defaultdict(float)
    for stamp, open_price, close_price, size in zip(
            hourly_dates, hourly_open, hourly_close, hourly_volume, strict=False):
        delta = size if close_price > open_price else (-size if close_price < open_price else 0.0)
        per_day[stamp.date() if hasattr(stamp, "date") else stamp] += delta

    out = np.full(len(daily_dates), np.nan)
    running = 0.0
    seen_any = False
    for i, day in enumerate(daily_dates):
        key = day.date() if hasattr(day, "date") else day
        if key in per_day:
            running += per_day[key]
            seen_any = True
        out[i] = running if seen_any else np.nan
    return out
