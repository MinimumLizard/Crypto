"""Bitcoin cycle analogs and the risk scorecard (SPEC §6.5).

A note on the word ROI, because two conventions collide here and the difference
is a sign and a rebase. Throughout the cycle page ROI means a **normalised
price ratio**: current price divided by a reference price. 0.52 from the peak
means price is at 52% of the peak, i.e. 48% below it. It is not a percentage
return. Every figure that uses it says so.

Cycle boundaries are detected from price rather than hard-coded, because §0.4
forbids hard-coding a market number into logic. They are therefore approximate,
and the page says so: a "cycle low" is the lowest close between two peaks, and
which peak counts depends on the threshold used to find it.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

# A drawdown this deep separates a cycle from a correction. It is a choice, not
# a fact, and it is stated on the page as one.
CYCLE_DRAWDOWN = 0.55
MIDTERM_YEARS = (2014, 2018, 2022, 2026)


def find_cycles(dates: list, closes: np.ndarray) -> list[dict]:
    """Peak/trough pairs: each peak is an all-time high, each low follows it.

    A cycle peak must be an ALL-TIME high, not merely a local one. An earlier
    version only required a running maximum that was later retraced, and it
    split the 2018 bear in two: March 2018's $11.5k was a lower high inside the
    drawdown from December 2017's $19.6k, and got counted as a cycle of its own.
    Requiring each peak to exceed every previous peak is what makes the count
    come out as four cycles rather than six.

    The low is then the minimum close between one peak and the next. A cycle is
    only recorded once price has retraced CYCLE_DRAWDOWN from the peak, so a
    shallow pullback from a new high does not open one.
    """
    peaks: list[int] = []
    all_time_high = -np.inf
    candidate = 0
    for i, price in enumerate(closes):
        if price > all_time_high:
            all_time_high = price
            candidate = i
        elif price < all_time_high * (1 - CYCLE_DRAWDOWN) and candidate not in peaks:
            peaks.append(candidate)
    # The most recent all-time high opens the current cycle even if the
    # retracement has not yet reached the threshold -- otherwise the live cycle
    # would be invisible until it had already fallen by more than half.
    if candidate not in peaks:
        peaks.append(candidate)
    peaks = sorted(set(peaks))

    cycles = []
    for n, peak in enumerate(peaks):
        end = peaks[n + 1] if n + 1 < len(peaks) else len(closes) - 1
        segment = closes[peak:end + 1]
        if len(segment) < 2:
            continue
        low_offset = int(np.argmin(segment))
        cycles.append({
            "peak_index": peak, "peak_date": str(dates[peak]),
            "peak_price": round(float(closes[peak]), 2),
            "low_index": peak + low_offset, "low_date": str(dates[peak + low_offset]),
            "low_price": round(float(segment[low_offset]), 2),
            "drawdown_pct": round((1 - float(segment[low_offset]) / float(closes[peak])) * 100, 1),
        })
    return cycles


def roi_from_peak(dates, closes: np.ndarray, cycles: list[dict], max_days: int = 500
                  ) -> dict:
    """Price as a ratio of each cycle's peak, aligned on days since that peak."""
    series = {}
    for cycle in cycles:
        start = cycle["peak_index"]
        segment = closes[start:start + max_days]
        if len(segment) < 30:
            continue
        ratio = segment / closes[start]
        series[cycle["peak_date"][:4]] = [
            {"d": i, "v": round(float(v), 4)} for i, v in enumerate(ratio)
            if i % 2 == 0          # every other day keeps the payload small
        ]

    # Mean and +/-1 standard deviation across the completed cycles, which is
    # what makes "shallower than typical at this elapsed time" a measurable
    # statement rather than an impression.
    completed = [c for c in cycles[:-1]]
    band = []
    if len(completed) >= 2:
        matrix = []
        for cycle in completed:
            start = cycle["peak_index"]
            segment = closes[start:start + max_days]
            padded = np.full(max_days, np.nan)
            padded[:len(segment)] = segment / closes[start]
            matrix.append(padded)
        stack = np.vstack(matrix)
        with np.errstate(invalid="ignore"):
            mean = np.nanmean(stack, axis=0)
            sd = np.nanstd(stack, axis=0)
        band = [
            {"d": i, "mean": round(float(mean[i]), 4),
             "lo": round(float(mean[i] - sd[i]), 4),
             "hi": round(float(mean[i] + sd[i]), 4)}
            for i in range(0, max_days, 2)
            if not np.isnan(mean[i])
        ]
    return {
        "series": series, "band": band, "n_completed_cycles": len(completed),
        "how_to_read": (
            "Each line is price as a fraction of that cycle's peak, lined up on "
            "days since the peak. 0.52 means price is at 52% of the peak, a 48% "
            "decline. The band is the mean and one standard deviation across "
            f"{len(completed)} completed cycles -- too few for the band to be a "
            "confidence interval, so read it as a rough envelope."),
    }


def roi_from_bottom(dates, closes: np.ndarray, cycles: list[dict],
                    max_days: int = 1600) -> dict:
    """Price as a multiple of each cycle's low, from that low forward."""
    series = {}
    for cycle in cycles:
        start = cycle["low_index"]
        segment = closes[start:start + max_days]
        if len(segment) < 60:
            continue
        series[cycle["low_date"][:4]] = [
            {"d": i, "v": round(float(v), 3)}
            for i, v in enumerate(segment / closes[start]) if i % 4 == 0
        ]
    return {
        "series": series,
        "how_to_read": ("Price as a multiple of each cycle's low. A flattening "
                        "family of curves is the diminishing-returns thesis made "
                        "visible."),
    }


def midterm_monthly(dates, closes: np.ndarray) -> dict:
    """Monthly returns for midterm election years (§6.5 cycle analogs)."""
    frame = pl.DataFrame({"date": dates, "close": closes}).with_columns(
        pl.col("date").dt.year().alias("year"), pl.col("date").dt.month().alias("month"))
    rows = []
    for year in MIDTERM_YEARS:
        year_frame = frame.filter(pl.col("year") == year)
        if year_frame.is_empty():
            continue
        months: dict[str, float | None] = {}
        for month in range(1, 13):
            month_frame = year_frame.filter(pl.col("month") == month)
            if month_frame.height < 2:
                months[str(month)] = None
                continue
            first = float(month_frame["close"][0])
            last = float(month_frame["close"][-1])
            # The month's return is measured from the previous month's close
            # where one exists, so months join up rather than each starting fresh.
            previous = frame.filter(
                (pl.col("date") < month_frame["date"][0]))
            base = float(previous["close"][-1]) if previous.height else first
            months[str(month)] = round((last / base - 1) * 100, 2)
        rows.append({"year": year, "months": months})
    return {
        "rows": rows,
        "how_to_read": ("Bitcoin's monthly returns in midterm election years. "
                        "Three completed observations is enough to describe a "
                        "tendency and not enough to establish one; treat the "
                        "pattern as a prior, not a rule."),
    }


def ytd_roi(dates, closes: np.ndarray) -> dict:
    """Year-to-date price ratio for each midterm year, aligned on day of year."""
    frame = pl.DataFrame({"date": dates, "close": closes})
    series = {}
    for year in MIDTERM_YEARS:
        year_frame = frame.filter(pl.col("date").dt.year() == year)
        if year_frame.height < 30:
            continue
        base = float(year_frame["close"][0])
        series[str(year)] = [
            {"d": int(d.timetuple().tm_yday), "v": round(float(c) / base, 4)}
            for d, c in zip(year_frame["date"], year_frame["close"], strict=False)
        ]
    return {
        "series": series,
        "how_to_read": ("Price as a fraction of its 1 January level, for each "
                        "midterm year. 0.73 means 27% down on the year."),
    }


def current_position(dates, closes: np.ndarray, cycles: list[dict]) -> dict:
    """Where this cycle sits against the previous ones, in days and in ratio."""
    if not cycles:
        return {"available": False, "reason": "no cycles detected"}
    current = cycles[-1]
    completed = cycles[:-1]
    last_index = len(closes) - 1

    days_since_peak = last_index - current["peak_index"]
    ratio_from_peak = float(closes[-1]) / current["peak_price"]

    previous_lows = [c["low_index"] - c["peak_index"] for c in completed]
    previous_gains = [
        (c["peak_index"] - completed[i - 1]["low_index"])
        for i, c in enumerate(cycles) if i > 0 and i - 1 < len(completed)
    ]
    return {
        "available": True,
        "peak_date": current["peak_date"],
        "peak_price": current["peak_price"],
        "days_since_peak": int(days_since_peak),
        "roi_from_peak": round(ratio_from_peak, 4),
        "drawdown_pct": round((1 - ratio_from_peak) * 100, 1),
        "prior_peak_to_low_days": previous_lows,
        "prior_low_to_peak_days": previous_gains,
        "how_to_read": (
            "Days elapsed since this cycle's peak, against how long prior peaks "
            f"took to reach their low ({', '.join(str(d) for d in previous_lows) or 'n/a'} "
            "days). Cycle boundaries are detected from price, so they are "
            "approximate."),
    }


def halving(block_height: int | None) -> dict:
    """Days since the last halving and an estimate of the next.

    Estimated from block height at ten minutes a block. It drifts with hash
    rate, so it is shown as an estimate with its assumption stated.
    """
    if not block_height:
        return {"available": False, "reason": "block height unavailable"}
    interval = 210_000
    last = (block_height // interval) * interval
    next_height = last + interval
    blocks_remaining = next_height - block_height
    minutes = blocks_remaining * 10
    estimate = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=minutes)
    return {
        "available": True,
        "height": block_height,
        "last_halving_block": last,
        "next_halving_block": next_height,
        "blocks_remaining": blocks_remaining,
        "estimated_date": estimate.date().isoformat(),
        "how_to_read": ("Estimated at ten minutes a block. The estimate moves "
                        "with hash rate and is not a date to plan around."),
    }
