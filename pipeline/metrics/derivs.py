"""Funding, open interest and volatility (SPEC §6.9).

**The funding interval is the whole game.** A rate of 0.0001 is 8.76%/yr on a
venue that funds hourly and 1.10%/yr on one that funds every eight hours. On
2026-09-26 BTC funding was +1.25e-05 hourly on Hyperliquid (+10.9% annualised)
and −7.75e-06 on Binance's 8-hour schedule (−0.85% annualised) — opposite
signs. Annualising both with one constant would not just be imprecise, it would
invert the comparison. Every rate here is annualised with its own venue's
`fundingIntervalHours`.

Open interest has no free history, so OI change and OI z-scores are computed
from our own snapshots and are empty until those accumulate. The page says how
many days it holds rather than drawing a change from a single observation.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from pipeline import store

HOURS_PER_YEAR = 24 * 365
MIN_FUNDING_OBSERVATIONS = 200      # ~8 days of hourly funding before a z-score
MIN_OI_CHANGE_HOURS = 20            # below this an "OI change" is not a daily one
REALISED_VOL_WINDOWS = (30, 90)


def _as_datetime(frame: pl.DataFrame, column: str = "observed_at") -> pl.DataFrame:
    """Coerce an observation timestamp to a tz-aware datetime, however stored.

    The snapshot tables disagree with each other. `perp_contexts` and `global`
    hold ISO-8601 STRINGS; `funding_hourly_*` holds a tz-aware Datetime. Every
    other consumer only sorts, groups, takes `.last()` or `str()`s the column,
    and all four work on either type -- which is exactly why the mismatch sat
    there unnoticed, and why the first arithmetic on it raised TypeError on
    real data while passing against a fixture that happened to use datetimes.

    An ISO-8601 string with a fixed offset also sorts correctly
    lexicographically, so the existing `sort`/`max` calls are right. Only
    subtraction needs the real type.
    """
    if frame.is_empty() or column not in frame.columns:
        return frame
    if frame.schema[column] == pl.String:
        return frame.with_columns(
            pl.col(column).str.to_datetime(time_zone="UTC").alias(column))
    return frame


def annualise_funding(rate: float | None, interval_hours: float) -> float | None:
    """A per-interval funding rate as an annual percentage.

    periods per year = 8760 / interval_hours
    """
    if rate is None or not interval_hours:
        return None
    return rate * (HOURS_PER_YEAR / interval_hours) * 100.0


def funding_table(venues: pl.DataFrame) -> list[dict]:
    """Cross-venue funding, annualised on each venue's own schedule."""
    if venues.is_empty():
        return []
    latest = (venues.sort("observed_at")
                    .group_by(["coin", "venue"], maintain_order=True).last())
    out: dict[str, dict] = {}
    for row in latest.iter_rows(named=True):
        entry = out.setdefault(row["coin"], {"coin": row["coin"], "venues": {}})
        entry["venues"][row["venue"]] = {
            "rate": row["funding_rate"],
            "interval_hours": row["interval_hours"],
            "annualised_pct": annualise_funding(
                row["funding_rate"], row["interval_hours"]),
        }
    for entry in out.values():
        rates = [v["annualised_pct"] for v in entry["venues"].values()
                 if v["annualised_pct"] is not None]
        entry["mean_annualised_pct"] = float(np.mean(rates)) if rates else None
        # A gap between venues is a real dislocation, not rounding: it is what
        # a cross-venue basis trade is priced off.
        entry["spread_pct"] = (max(rates) - min(rates)) if len(rates) > 1 else None
    return sorted(out.values(),
                  key=lambda e: -(e["mean_annualised_pct"] or -1e9))


def funding_zscore(coin: str, window_days: int = 90) -> dict:
    """Today's funding against its own recent distribution.

    §6.9 asks for a z-score against funding's own history. Hourly, because
    collapsing to daily first discards most of the distribution the z-score is
    measured against.
    """
    frame = store.read_snapshot(f"funding_hourly_{coin}")
    if frame.is_empty() or frame.height < MIN_FUNDING_OBSERVATIONS:
        return {"available": False,
                "reason": f"needs {MIN_FUNDING_OBSERVATIONS} hourly observations, "
                          f"has {frame.height}"}
    frame = _as_datetime(frame).sort("observed_at")
    # The window is measured back from the newest OBSERVATION, not from the
    # clock. `datetime.now()` makes the window shrink as the store goes stale
    # -- with snapshots 8 days behind, a "90-day" window held 82 days of data
    # and said 90. Same class as the 50-week confirmation bug (D033).
    newest_at = frame["observed_at"].max()
    cutoff = newest_at - dt.timedelta(days=window_days)
    recent = frame.filter(pl.col("observed_at") >= cutoff)
    fell_back = recent.height < MIN_FUNDING_OBSERVATIONS
    if fell_back:
        recent = frame
    values = recent["value"].to_numpy().astype(float)
    current = float(values[-1])

    # Today is not part of the distribution it is scored against (PLAN §5.3),
    # matching `radar._volume_zscore`, which does the same thing for volume.
    prior = values[:-1]
    if len(prior) < 2:
        return {"available": False, "reason": "no prior funding to score against"}
    sd = float(prior.std(ddof=1))
    if sd == 0:
        return {"available": False, "reason": "funding has not varied"}

    span_days = (newest_at - recent["observed_at"].min()).total_seconds() / 86400
    return {
        "available": True,
        "current_hourly": current,
        "current_annualised_pct": annualise_funding(current, 1.0),
        "mean_annualised_pct": annualise_funding(float(prior.mean()), 1.0),
        "zscore": round(float((current - prior.mean()) / sd), 2),
        "n": int(len(prior)),
        # The window actually used. Reporting the REQUESTED window while the
        # fallback had quietly widened it to all of history was a claim about
        # work that did not happen.
        "window_days": round(span_days, 1),
        "requested_window_days": window_days,
        "widened_to_full_history": bool(fell_back),
        "as_of": str(newest_at),
    }


def open_interest(contexts: pl.DataFrame, caps: dict[str, float]) -> list[dict]:
    """OI, OI/market cap, and OI change once we have snapshots to compare.

    OI/market cap is the leverage gauge §6.9 asks for: how much notional is
    riding on a name relative to its size.
    """
    if contexts.is_empty():
        return []
    history = _as_datetime(contexts).sort("observed_at")
    latest = history.group_by("coin", maintain_order=True).last()

    # Any earlier snapshot at least MIN_OI_CHANGE_HOURS back, so a "change" is
    # a change. The comment claimed this and the code did not do it: it took
    # the newest snapshot strictly before the latest, which on a manual
    # dispatch minutes after a build is a "24h OI change" measured over
    # minutes. `concurrency: daily-build` queues dispatches rather than
    # dropping them, so that pair is reachable.
    snapshots_held = 0
    prior: dict[str, float] = {}
    oi_change_hours: float | None = None
    if history.height:
        snapshots_held = int(history.select(
            pl.col("observed_at").n_unique()).item())
        newest_at = history["observed_at"].max()
        older = history.filter(
            pl.col("observed_at")
            <= newest_at - dt.timedelta(hours=MIN_OI_CHANGE_HOURS))
        if not older.is_empty():
            snapshot = (older.sort("observed_at")
                        .group_by("coin", maintain_order=True).last())
            prior = dict(zip(snapshot["coin"].to_list(),
                             snapshot["open_interest_usd"].to_list(), strict=False))
            oi_change_hours = round(
                (newest_at - older["observed_at"].max()).total_seconds() / 3600, 1)

    out = []
    for row in latest.iter_rows(named=True):
        coin = row["coin"]
        oi = row["open_interest_usd"]
        cap = caps.get(coin)
        previous = prior.get(coin)
        out.append({
            "coin": coin,
            "open_interest_usd": oi,
            "day_volume_usd": row["day_volume_usd"],
            "oi_to_market_cap": (oi / cap) if oi and cap else None,
            "oi_to_volume": (oi / row["day_volume_usd"]
                             if oi and row["day_volume_usd"] else None),
            "oi_change_pct": ((oi / previous - 1) * 100
                              if oi and previous and previous > 0 else None),
            "price_change_pct": ((row["mark"] / row["prev_day_px"] - 1) * 100
                                 if row["mark"] and row["prev_day_px"] else None),
            "funding_hourly": row["funding_hourly"],
            "snapshots_held": snapshots_held,
            # How long the OI change actually spans, so it is never read as a
            # day when it is not one.
            "oi_change_hours": oi_change_hours,
        })
    return sorted(out, key=lambda r: -(r["open_interest_usd"] or 0))


def oi_price_quadrant(oi_change: float | None, price_change: float | None) -> str:
    """The §6.9 quadrant: what new positioning is doing.

    Rising OI with rising price is fresh longs; rising OI with falling price is
    fresh shorts; falling OI with falling price is longs being closed out;
    falling OI with rising price is shorts covering. Naming it is the point —
    the same OI number means opposite things depending on the price leg.
    """
    if oi_change is None or price_change is None:
        return "unknown"
    if oi_change > 0 and price_change > 0:
        return "longs building"
    if oi_change > 0 and price_change < 0:
        return "shorts building"
    if oi_change < 0 and price_change < 0:
        return "long liquidation"
    if oi_change < 0 and price_change > 0:
        return "short covering"
    return "flat"


def realised_volatility(closes: np.ndarray, window: int) -> float | None:
    """Annualised standard deviation of daily log returns."""
    if len(closes) < window + 1:
        return None
    returns = np.diff(np.log(closes[-(window + 1):]))
    if len(returns) < 2:
        return None
    return float(returns.std(ddof=1) * np.sqrt(365) * 100)


def atr_percentile(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                   length: int = 14, lookback: int = 365) -> dict:
    """ATR as a percentage of price, ranked against its own year.

    The level of ATR says little across assets; where it sits in its OWN
    distribution says whether this name is unusually quiet or unusually busy.
    """
    if len(close) < length + 30:
        return {"available": False, "reason": f"needs {length + 30} bars"}
    from pipeline.signals import indicators as ind
    atr = ind.atr(high, low, close, length)
    pct = atr / close * 100
    series = pct[~np.isnan(pct)]
    if len(series) < 60:
        return {"available": False, "reason": "not enough ATR history"}
    recent = series[-lookback:] if len(series) > lookback else series
    current = float(series[-1])
    # Today is excluded from the distribution it is ranked against, as
    # PLAN §5.3 requires and `macro._percentile`, `geopolitics.
    # _percentile_of_last` and `radar._volume_zscore` already do. Including it
    # caps the reading at (n-1)/n, so a name at its busiest ATR in a year could
    # never print 100.
    prior = recent[:-1]
    if len(prior) < 2:
        return {"available": False, "reason": "not enough prior ATR history"}
    return {
        "available": True,
        "atr_pct": round(current, 2),
        "percentile": round(float((prior < current).mean() * 100), 1),
        "median_pct": round(float(np.median(prior)), 2),
        "n": int(len(prior)),
    }


def vol_premium(realised: float | None, implied: float | None) -> float | None:
    """Implied minus realised, in vol points. Positive: options look dear."""
    if realised is None or implied is None:
        return None
    return round(implied - realised, 2)
