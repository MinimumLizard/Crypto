"""Pine Script v6 indicator semantics, reproduced exactly.

SPEC §7.1 sets a parity target of ±0.5 points against TradingView, and that
target is decided almost entirely here rather than in the scoring arithmetic.
The composite is simple addition; what makes two implementations disagree is
whether `ta.rsi` smooths with RMA or with a plain mean, whether `ta.cci` divides
by mean absolute deviation or by standard deviation, and where a series is
seeded before it has enough bars.

Every function below therefore follows Pine's definition, not the textbook one,
and says where the two differ. The differences that actually bite:

* **RMA, not EMA, not SMA.** `ta.rsi`, `ta.atr` and `ta.dmi` all smooth with
  Pine's `ta.rma`: alpha = 1/length, seeded with the SMA of the first `length`
  values. Using the Wilder-equivalent EMA (alpha = 2/(len+1)) is a different
  number, and the gap does not wash out — it persists for hundreds of bars.
* **`ta.cci` uses mean absolute deviation**, not standard deviation. The two
  differ by roughly 25% on typical data, which is 6 points of CCI/4.
* **`ta.ema` is seeded with an SMA**, not with the first value. Seeding with the
  first value leaves a visible offset for about 3x the length.
* **Pivots confirm late.** `ta.pivothigh(5, 5)` is only knowable five bars after
  the pivot bar, so using it on the current bar is look-ahead. `pivots()`
  returns values placed at the bar they were CONFIRMED on, not the bar they
  describe.

`nan` stands in for Pine's `na` throughout: a series is nan until it has enough
history, and arithmetic on it stays nan rather than silently becoming zero.
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Moving averages and smoothing
# ---------------------------------------------------------------------------


def sma(values: np.ndarray, length: int) -> np.ndarray:
    """ta.sma — simple mean of the last `length` bars, nan until full."""
    out = np.full(len(values), np.nan)
    if length <= 0 or len(values) < length:
        return out
    cumulative = np.cumsum(np.insert(np.nan_to_num(values, nan=0.0), 0, 0.0))
    windows = (cumulative[length:] - cumulative[:-length]) / length
    out[length - 1:] = windows
    # Any window containing a nan must itself be nan; cumsum above hid that.
    if np.isnan(values).any():
        bad = np.isnan(values).astype(float)
        bad_cum = np.cumsum(np.insert(bad, 0, 0.0))
        has_nan = (bad_cum[length:] - bad_cum[:-length]) > 0
        out[length - 1:][has_nan] = np.nan
    return out


def rma(values: np.ndarray, length: int) -> np.ndarray:
    """ta.rma — Wilder smoothing: alpha = 1/length, seeded with an SMA.

    This is the one that decides RSI, ATR and DMI parity. Note the seed: Pine
    starts the recursion from the SMA of the first `length` values, so the
    series is nan before bar `length - 1`.
    """
    out = np.full(len(values), np.nan)
    if length <= 0 or len(values) < length:
        return out
    alpha = 1.0 / length
    seed_window = values[:length]
    if np.isnan(seed_window).any():
        # Pine skips leading na and seeds once enough real values exist.
        first = int(np.argmax(~np.isnan(values)))
        if len(values) - first < length:
            return out
        seed_window = values[first:first + length]
        start = first + length - 1
    else:
        start = length - 1
    out[start] = float(np.mean(seed_window))
    for i in range(start + 1, len(values)):
        current = values[i]
        out[i] = out[i - 1] if np.isnan(current) else alpha * current + (1 - alpha) * out[i - 1]
    return out


def ema(values: np.ndarray, length: int) -> np.ndarray:
    """ta.ema — alpha = 2/(length+1), seeded with the SMA of the first window.

    Pine seeds with an SMA rather than with values[0]. Seeding with the first
    value biases the series for roughly 3x the length, which is well outside a
    0.5-point parity budget on a 200-length average.
    """
    out = np.full(len(values), np.nan)
    if length <= 0 or len(values) < length:
        return out
    alpha = 2.0 / (length + 1)
    out[length - 1] = float(np.mean(values[:length]))
    for i in range(length, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def highest(values: np.ndarray, length: int) -> np.ndarray:
    """Rolling max. A window that is entirely na stays na, quietly.

    numpy warns on an all-nan slice; that case is expected here (any rolling
    window over the warm-up of another indicator hits it) and is not a fault,
    so it is tested for rather than warned about.
    """
    out = np.full(len(values), np.nan)
    for i in range(length - 1, len(values)):
        window = values[i - length + 1:i + 1]
        if not np.isnan(window).all():
            out[i] = np.nanmax(window)
    return out


def lowest(values: np.ndarray, length: int) -> np.ndarray:
    """Rolling min; see `highest` on all-na windows."""
    out = np.full(len(values), np.nan)
    for i in range(length - 1, len(values)):
        window = values[i - length + 1:i + 1]
        if not np.isnan(window).all():
            out[i] = np.nanmin(window)
    return out


def change(values: np.ndarray, length: int = 1) -> np.ndarray:
    out = np.full(len(values), np.nan)
    out[length:] = values[length:] - values[:-length]
    return out


# ---------------------------------------------------------------------------
# Oscillators
# ---------------------------------------------------------------------------


def rsi(values: np.ndarray, length: int = 14) -> np.ndarray:
    """ta.rsi — RMA of gains over RMA of losses.

    Pine's edge cases matter: when the loss average is zero RSI is 100, and
    when the gain average is zero RSI is 0. Computing 100 - 100/(1+u/d)
    naively divides by zero in a market that only went up.
    """
    delta = np.diff(values, prepend=np.nan)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    gains[0] = losses[0] = np.nan
    up, down = rma(gains, length), rma(losses, length)
    out = np.full(len(values), np.nan)
    valid = ~np.isnan(up) & ~np.isnan(down)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(down == 0, np.inf, up / np.where(down == 0, 1, down))
        computed = 100 - 100 / (1 + ratio)
    out[valid] = computed[valid]
    out[valid & (down == 0)] = 100.0
    out[valid & (up == 0)] = 0.0
    return out


def stoch(source: np.ndarray, high: np.ndarray, low: np.ndarray, length: int) -> np.ndarray:
    """ta.stoch — 100 * (source - lowest(low)) / (highest(high) - lowest(low)).

    For StochRSI all three arguments are the RSI series, which is what §7.1
    means by "ta.stoch applied to RSI".
    """
    top, bottom = highest(high, length), lowest(low, length)
    span = top - bottom
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 * (source - bottom) / np.where(span == 0, np.nan, span)
    # Pine returns 0 rather than na when the range collapses to a point.
    out = np.where((span == 0) & ~np.isnan(span), 0.0, out)
    return out


def cci(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 20) -> np.ndarray:
    """ta.cci — (hlc3 - SMA) / (0.015 * mean ABSOLUTE deviation).

    Pine's `ta.dev` is the mean absolute deviation about the SMA, NOT the
    standard deviation. Substituting the standard deviation shrinks CCI by
    roughly a fifth, which §7.1 then divides by 4 and feeds into momentum.
    """
    typical = (high + low + close) / 3.0
    mean = sma(typical, length)
    out = np.full(len(close), np.nan)
    for i in range(length - 1, len(close)):
        window = typical[i - length + 1:i + 1]
        if np.isnan(window).any() or np.isnan(mean[i]):
            continue
        deviation = float(np.mean(np.abs(window - mean[i])))
        out[i] = 0.0 if deviation == 0 else (typical[i] - mean[i]) / (0.015 * deviation)
    return out


def mfi(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        volume: np.ndarray, length: int = 14) -> np.ndarray:
    """ta.mfi on hlc3 — volume-weighted RSI of the typical price.

    Pine sums raw money flow over the window (not an RMA), splitting each bar
    by whether the typical price rose or fell.
    """
    typical = (high + low + close) / 3.0
    delta = np.diff(typical, prepend=np.nan)
    raw = typical * volume
    positive = np.where(delta > 0, raw, 0.0)
    negative = np.where(delta < 0, raw, 0.0)
    out = np.full(len(close), np.nan)
    for i in range(length, len(close)):
        up = float(np.nansum(positive[i - length + 1:i + 1]))
        down = float(np.nansum(negative[i - length + 1:i + 1]))
        if down == 0:
            out[i] = 100.0
        elif up == 0:
            out[i] = 0.0
        else:
            out[i] = 100 - 100 / (1 + up / down)
    return out


# ---------------------------------------------------------------------------
# Range, trend and directional movement
# ---------------------------------------------------------------------------


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Pine's tr(true) — the first bar is high - low, having no previous close."""
    previous = np.roll(close, 1)
    previous[0] = np.nan
    candidates = np.vstack([high - low, np.abs(high - previous), np.abs(low - previous)])
    out = np.nanmax(candidates, axis=0)
    out[0] = high[0] - low[0]
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray:
    """ta.atr — RMA of true range."""
    return rma(true_range(high, low, close), length)


def dmi(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        length: int = 14, adx_length: int = 14) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ta.dmi — returns (+DI, -DI, ADX), all RMA-smoothed.

    Only ADX is used by §7.1, as the 1.4x trend amplifier above 25, but the
    directional components are returned because getting them wrong is the usual
    reason ADX is wrong.
    """
    up_move = np.diff(high, prepend=np.nan)
    down_move = -np.diff(low, prepend=np.nan)
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm[0] = minus_dm[0] = np.nan

    smoothed_tr = rma(true_range(high, low, close), length)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * rma(plus_dm, length) / smoothed_tr
        minus_di = 100.0 * rma(minus_dm, length) / smoothed_tr
        total = plus_di + minus_di
        dx = np.abs(plus_di - minus_di) / np.where(total == 0, 1.0, total)
    adx = rma(dx, adx_length) * 100.0
    return plus_di, minus_di, adx


def supertrend(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               factor: float = 3.0, atr_length: int = 10) -> tuple[np.ndarray, np.ndarray]:
    """ta.supertrend — returns (line, direction); direction < 0 means bullish.

    Pine's band-ratchet is the fiddly part: each band only tightens while price
    stays on its side, and resets when the previous close breaks it. A plain
    recomputation each bar produces a visibly different line.
    """
    atr_values = atr(high, low, close, atr_length)
    mid = (high + low) / 2.0
    upper_basic = mid + factor * atr_values
    lower_basic = mid - factor * atr_values

    n = len(close)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    trend = np.full(n, np.nan)

    start = int(np.argmax(~np.isnan(atr_values))) if (~np.isnan(atr_values)).any() else n
    if start >= n:
        return trend, direction

    upper[start], lower[start] = upper_basic[start], lower_basic[start]
    direction[start] = 1.0          # Pine starts short-biased until proven otherwise
    trend[start] = upper[start]

    for i in range(start + 1, n):
        previous_close = close[i - 1]
        lower[i] = (max(lower_basic[i], lower[i - 1])
                    if previous_close > lower[i - 1] else lower_basic[i])
        upper[i] = (min(upper_basic[i], upper[i - 1])
                    if previous_close < upper[i - 1] else upper_basic[i])
        if close[i] > upper[i - 1]:
            direction[i] = -1.0
        elif close[i] < lower[i - 1]:
            direction[i] = 1.0
        else:
            direction[i] = direction[i - 1]
        trend[i] = lower[i] if direction[i] == -1.0 else upper[i]
    return trend, direction


def ichimoku(high: np.ndarray, low: np.ndarray,
             conversion: int = 9, base: int = 26, span_b: int = 52,
             displacement: int = 26) -> dict[str, np.ndarray]:
    """Ichimoku lines. Senkou spans are returned UNDISPLACED.

    §7.1 compares close against `max(senkouA[26], senkouB[26])` — that is, the
    cloud plotted at the current bar, which was computed 26 bars ago. Callers
    index back by `displacement` themselves so the shift is visible at the call
    site rather than buried here.
    """
    tenkan = (highest(high, conversion) + lowest(low, conversion)) / 2.0
    kijun = (highest(high, base) + lowest(low, base)) / 2.0
    senkou_a = (tenkan + kijun) / 2.0
    senkou_b = (highest(high, span_b) + lowest(low, span_b)) / 2.0
    return {"tenkan": tenkan, "kijun": kijun,
            "senkou_a": senkou_a, "senkou_b": senkou_b,
            "displacement": displacement}


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


def obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """ta.obv — cumulative volume signed by the close-to-close change."""
    delta = np.diff(close, prepend=np.nan)
    signed = np.where(delta > 0, volume, np.where(delta < 0, -volume, 0.0))
    signed[0] = 0.0
    return np.cumsum(np.nan_to_num(signed))


def anchored_vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                  volume: np.ndarray, anchor_index: np.ndarray) -> np.ndarray:
    """VWAP that restarts whenever `anchor_index` changes.

    §7.1 specifies a WEEKLY anchor on daily charts, so `anchor_index` is the
    ISO week number. A session-anchored VWAP on a daily chart would reset every
    bar and equal hlc3, which is a different indicator entirely.
    """
    typical = (high + low + close) / 3.0
    out = np.full(len(close), np.nan)
    cumulative_pv = cumulative_v = 0.0
    previous_anchor = None
    for i in range(len(close)):
        if previous_anchor is None or anchor_index[i] != previous_anchor:
            cumulative_pv = cumulative_v = 0.0
            previous_anchor = anchor_index[i]
        cumulative_pv += typical[i] * volume[i]
        cumulative_v += volume[i]
        out[i] = cumulative_pv / cumulative_v if cumulative_v > 0 else np.nan
    return out


# ---------------------------------------------------------------------------
# Pivots
# ---------------------------------------------------------------------------


def pivots(high: np.ndarray, low: np.ndarray, left: int = 5, right: int = 5
           ) -> tuple[np.ndarray, np.ndarray]:
    """ta.pivothigh / ta.pivotlow, placed at the bar they were CONFIRMED on.

    A pivot high at bar i needs `right` later bars to confirm it, so it is not
    knowable until bar i + right. Returning it at bar i would be look-ahead,
    and §2.3 forbids that. The arrays here hold the pivot PRICE at the
    confirmation bar and nan everywhere else; the pivot's own bar index is
    recoverable as (confirmation bar - right).
    """
    n = len(high)
    pivot_high = np.full(n, np.nan)
    pivot_low = np.full(n, np.nan)
    for i in range(left, n - right):
        window_high = high[i - left:i + right + 1]
        if high[i] == np.nanmax(window_high) and np.sum(window_high == high[i]) == 1:
            pivot_high[i + right] = high[i]
        window_low = low[i - left:i + right + 1]
        if low[i] == np.nanmin(window_low) and np.sum(window_low == low[i]) == 1:
            pivot_low[i + right] = low[i]
    return pivot_high, pivot_low
