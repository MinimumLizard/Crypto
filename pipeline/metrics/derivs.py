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
REALISED_VOL_WINDOWS = (30, 90)


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
    frame = frame.sort("observed_at")
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=window_days)
    recent = frame.filter(pl.col("observed_at") >= cutoff)
    if recent.height < MIN_FUNDING_OBSERVATIONS:
        recent = frame
    values = recent["value"].to_numpy().astype(float)
    current = float(values[-1])
    sd = float(values.std())
    if sd == 0:
        return {"available": False, "reason": "funding has not varied"}
    return {
        "available": True,
        "current_hourly": current,
        "current_annualised_pct": annualise_funding(current, 1.0),
        "mean_annualised_pct": annualise_funding(float(values.mean()), 1.0),
        "zscore": round(float((current - values.mean()) / sd), 2),
        "n": int(len(values)),
        "window_days": window_days,
    }


def open_interest(contexts: pl.DataFrame, caps: dict[str, float]) -> list[dict]:
    """OI, OI/market cap, and OI change once we have snapshots to compare.

    OI/market cap is the leverage gauge §6.9 asks for: how much notional is
    riding on a name relative to its size.
    """
    if contexts.is_empty():
        return []
    history = contexts.sort("observed_at")
    latest = history.group_by("coin", maintain_order=True).last()

    # Any earlier snapshot at least 20 hours back, so a "change" is a change.
    earliest_at = history["observed_at"].min()
    days_held = 0
    prior: dict[str, float] = {}
    if earliest_at is not None:
        span = history.select(pl.col("observed_at").n_unique()).item()
        days_held = int(span)
        cutoff = history["observed_at"].max()
        older = history.filter(pl.col("observed_at") < cutoff)
        if not older.is_empty():
            snapshot = older.group_by("coin", maintain_order=True).last()
            prior = dict(zip(snapshot["coin"].to_list(),
                             snapshot["open_interest_usd"].to_list(), strict=False))

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
            "snapshots_held": days_held,
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
    return {
        "available": True,
        "atr_pct": round(current, 2),
        "percentile": round(float((recent < current).mean() * 100), 1),
        "median_pct": round(float(np.median(recent)), 2),
        "n": int(len(recent)),
    }


def vol_premium(realised: float | None, implied: float | None) -> float | None:
    """Implied minus realised, in vol points. Positive: options look dear."""
    if realised is None or implied is None:
        return None
    return round(implied - realised, 2)
