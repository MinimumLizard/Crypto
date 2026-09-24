"""Key price levels, and the 50-week confirmation tracker (SPEC §6.2, §6.5).

The 50W rule is the one the brief is most specific about, and the one most
easily got wrong: the signal is **two consecutive WEEKLY CLOSES** above the
50-week SMA, not a daily close, not an intraweek touch. A daily series crossing
the level several times inside one week is still zero confirmations.

So weekly bars are resampled from daily here rather than fetched, and the
in-progress week is excluded: a week that has not closed cannot have closed
above anything.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl


def to_weekly(daily: pl.DataFrame, *, drop_open_week: bool = True) -> pl.DataFrame:
    """Resample daily bars to weekly (Monday-anchored), dropping the open week."""
    if daily.is_empty():
        return daily
    frame = daily.sort("date").with_columns(
        pl.col("date").dt.truncate("1w").alias("week"))
    weekly = frame.group_by("week", maintain_order=True).agg([
        pl.col("open").drop_nulls().first().alias("open"),
        pl.col("high").max().alias("high"),
        pl.col("low").min().alias("low"),
        pl.col("close").last().alias("close"),
        pl.col("volume").sum().alias("volume"),
        pl.col("date").max().alias("last_day"),
    ]).rename({"week": "date"})

    if drop_open_week and not weekly.is_empty():
        this_week = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
        weekly = weekly.filter(pl.col("date") < this_week)
    return weekly


def sma(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for i in range(length - 1, len(values)):
        window = values[i - length + 1:i + 1]
        if not np.isnan(window).any():
            out[i] = float(window.mean())
    return out


def ema(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) < length:
        return out
    alpha = 2.0 / (length + 1)
    out[length - 1] = float(np.nanmean(values[:length]))
    for i in range(length, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def fifty_week_tracker(daily: pl.DataFrame) -> dict:
    """How many consecutive weekly closes sit above the 50-week SMA.

    Returns the count, the level, the distance, and the history of past
    confirmations, so the page can show "1 of 2" and also show that the rule
    has fired before and what happened.
    """
    weekly = to_weekly(daily)
    if weekly.height < 51:
        return {"available": False,
                "reason": f"needs 51 weekly bars, have {weekly.height}"}

    closes = weekly["close"].to_numpy().astype(float)
    line = sma(closes, 50)
    above = closes > line

    # Count the run of consecutive weekly closes above, ending at the last
    # CLOSED week.
    streak = 0
    for value in reversed(above):
        if np.isnan(line[len(above) - 1 - streak]):
            break
        if value:
            streak += 1
        else:
            break

    # Every past instance of the rule completing, for the "it has fired before"
    # history the brief asks for.
    confirmations = []
    run = 0
    for i, value in enumerate(above):
        if np.isnan(line[i]):
            continue
        run = run + 1 if value else 0
        if run == 2:
            confirmations.append({
                "date": str(weekly["date"][i]),
                "close": round(float(closes[i]), 2),
                "sma50w": round(float(line[i]), 2),
            })

    current_close = float(closes[-1])
    current_line = float(line[-1]) if not np.isnan(line[-1]) else None
    return {
        "available": True,
        "confirmations_needed": 2,
        "consecutive_closes_above": int(streak),
        "confirmed": bool(streak >= 2),
        "sma50w": round(current_line, 2) if current_line else None,
        "last_weekly_close": round(current_close, 2),
        "distance_pct": (round((current_close / current_line - 1) * 100, 2)
                         if current_line else None),
        "week_ending": str(weekly["date"][-1]),
        "past_confirmations": confirmations[-8:],
        "how_to_read": ("Two consecutive weekly closes above the 50-week average is "
                        "the confirmation rule. A daily close above it, or an "
                        "intraweek touch, counts for nothing."),
    }


def key_levels(daily: pl.DataFrame) -> dict:
    """The levels the home page and asset pages quote."""
    if daily.is_empty():
        return {"available": False, "reason": "no bars"}

    closes = daily["close"].to_numpy().astype(float)
    weekly = to_weekly(daily)
    weekly_closes = weekly["close"].to_numpy().astype(float) if weekly.height else np.array([])

    def last(array):
        if len(array) == 0:
            return None
        value = array[-1]
        return None if np.isnan(value) else round(float(value), 4)

    levels = {
        "price": round(float(closes[-1]), 4),
        "as_of": str(daily["date"][-1]),
        "sma200d": last(sma(closes, 200)),
        "sma50d": last(sma(closes, 50)),
        "sma20d": last(sma(closes, 20)),
    }
    if len(weekly_closes) >= 200:
        levels["sma200w"] = last(sma(weekly_closes, 200))
    if len(weekly_closes) >= 50:
        levels["sma50w"] = last(sma(weekly_closes, 50))
    if len(weekly_closes) >= 21:
        # The 20W SMA / 21W EMA band the brief asks for.
        levels["sma20w"] = last(sma(weekly_closes, 20))
        levels["ema21w"] = last(ema(weekly_closes, 21))

    for key in ("sma200d", "sma50w", "sma200w"):
        value = levels.get(key)
        levels[f"{key}_distance_pct"] = (
            round((levels["price"] / value - 1) * 100, 2) if value else None)

    if levels.get("sma200d"):
        levels["mayer_multiple"] = round(levels["price"] / levels["sma200d"], 3)

    # Previous week and month extremes and opens (§6.3 chart overlays).
    frame = daily.sort("date")
    if frame.height > 40:
        this_week = frame["date"][-1] - dt.timedelta(days=frame["date"][-1].weekday())
        previous_week = frame.filter(
            (pl.col("date") >= this_week - dt.timedelta(days=7))
            & (pl.col("date") < this_week))
        if previous_week.height:
            levels["prev_week_high"] = round(float(previous_week["high"].max()), 4)
            levels["prev_week_low"] = round(float(previous_week["low"].min()), 4)
        month_start = frame["date"][-1].replace(day=1)
        previous_month = frame.filter(pl.col("date") < month_start)
        if previous_month.height:
            last_month = previous_month.filter(
                pl.col("date") >= (month_start - dt.timedelta(days=31)).replace(day=1))
            if last_month.height:
                levels["prev_month_high"] = round(float(last_month["high"].max()), 4)
                levels["prev_month_low"] = round(float(last_month["low"].min()), 4)
    return {"available": True, **levels}


def returns(daily: pl.DataFrame, periods=(1, 7, 30, 90, 365)) -> dict:
    """Trailing returns as percentages. None where history is too short."""
    if daily.is_empty():
        return {}
    closes = daily["close"].to_numpy().astype(float)
    out = {}
    for period in periods:
        if len(closes) > period and closes[-1 - period] > 0:
            out[f"r{period}d"] = round((closes[-1] / closes[-1 - period] - 1) * 100, 2)
        else:
            out[f"r{period}d"] = None
    return out
