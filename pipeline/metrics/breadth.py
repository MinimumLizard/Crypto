"""Market structure: dominance, breadth, correlations (SPEC §6.7).

Two honesty constraints shape this page.

**The advance-decline line is built forward.** §6.7 is explicit that any
backfill from today's constituents must be labelled with its survivorship bias,
and the reason is worth stating: a top-100 basket chosen today is a basket that
survived, so a line drawn back through it rises in periods the real market fell.
It is not backfilled at all. The line starts at the first snapshot and the page
reports how many days it holds.

**Breadth is measured over the universe we actually have history for.** "% of
the top 100 above their 200-day average" needs 200 days of prices for 100
coins; the free tier does not serve that. So breadth is computed over the
tracked book, benchmarks and watchlist, and the panel says which universe it
is rather than implying the top 100.

A cumulative A-D line also trends down by construction through a long decline,
so its LEVEL is partly a function of how long the decline has run. What is
informative is whether it keeps making new lows while price rallies.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from pipeline import registry, store
from pipeline.metrics import levels

MIN_CORRELATION_DAYS = 60


def dominance(global_snapshots: pl.DataFrame, stablecoin_supply: pl.DataFrame) -> dict:
    """BTC dominance including and excluding stablecoins.

    §6.7 wants both, and the exclude-stables reading is the one that answers
    "has capital rotated out of Bitcoin", because stablecoin cap is now large
    enough to dilute the include-stables number without anything rotating.
    """
    if global_snapshots.is_empty():
        return {"available": False, "reason": "no global snapshot yet"}

    latest = global_snapshots.sort("observed_at").tail(1).to_dicts()[0]
    total = latest.get("total_market_cap")
    btc_dom = latest.get("btc_dominance")
    if not total or btc_dom is None:
        return {"available": False, "reason": "snapshot has no cap or dominance"}

    btc_cap = total * btc_dom / 100.0
    out = {
        "available": True,
        "as_of": latest.get("as_of"),
        "total_market_cap": total,
        "btc_dominance_incl_stables": round(btc_dom, 2),
        "eth_dominance": latest.get("eth_dominance"),
        "snapshots_held": int(global_snapshots.height),
    }

    if not stablecoin_supply.is_empty():
        stables = float(stablecoin_supply["value"][-1])
        ex_stables = total - stables
        out.update({
            "stablecoin_supply": stables,
            "stablecoin_dominance": round(stables / total * 100, 2),
            "total_ex_stables": ex_stables,
            "btc_dominance_ex_stables": (round(btc_cap / ex_stables * 100, 2)
                                         if ex_stables > 0 else None),
        })
        eth_dom = latest.get("eth_dominance")
        if eth_dom is not None:
            eth_cap = total * eth_dom / 100.0
            out["total_ex_btc"] = total - btc_cap
            out["total_ex_btc_eth"] = total - btc_cap - eth_cap
    else:
        out["stablecoin_note"] = "stablecoin supply unavailable"

    return out


def stablecoin_trend(supply: pl.DataFrame) -> dict:
    """Stablecoin supply and its 30- and 90-day change.

    Stablecoin supply is dry powder: it grows when capital enters the system
    and has not yet been deployed. It is one of the few market-wide series with
    a real multi-year history on the free tier.
    """
    if supply.is_empty() or supply.height < 100:
        return {"available": False, "reason": "needs 100 days of supply history"}
    values = supply["value"].to_numpy().astype(float)
    dates = supply["date"].to_list()

    def change(days: int) -> float | None:
        if len(values) <= days or values[-1 - days] <= 0:
            return None
        return float((values[-1] / values[-1 - days] - 1) * 100)

    return {
        "available": True,
        "as_of": str(dates[-1]),
        "supply": float(values[-1]),
        "change_30d_pct": change(30),
        "change_90d_pct": change(90),
        "change_365d_pct": change(365),
        "series": [{"d": str(d), "v": float(v)}
                   for d, v in zip(dates[-540:], values[-540:], strict=False)][::3],
        "n_days": int(len(values)),
    }


def advance_decline(top100: pl.DataFrame) -> dict:
    """Cumulative advances minus declines, built forward from our snapshots."""
    if top100.is_empty():
        return {"available": False, "reason": "no top-100 snapshots yet"}

    # Cast to a SIGNED integer before subtracting. polars sums booleans as
    # u32, so advances - declines underflows the moment more names fall than
    # rise: 0 - 3 becomes 4,294,967,294. That is most down days, which is
    # exactly when this line is worth reading.
    per_day = (top100.group_by("as_of")
               .agg([
                   (pl.col("change_24h") > 0).sum().cast(pl.Int64).alias("advances"),
                   (pl.col("change_24h") < 0).sum().cast(pl.Int64).alias("declines"),
                   pl.len().cast(pl.Int64).alias("n"),
               ]).sort("as_of"))

    if per_day.height < 2:
        return {
            "available": False,
            "days": int(per_day.height),
            "reason": (f"the advance-decline line is built forward and has "
                       f"{per_day.height} day(s) so far. It is not back-filled: "
                       f"a basket chosen from today's top 100 is a basket that "
                       f"survived, and a line drawn through it would rise in "
                       f"periods the market fell."),
        }

    net = (per_day["advances"] - per_day["declines"]).to_numpy()
    cumulative = np.cumsum(net)
    return {
        "available": True,
        "days": int(per_day.height),
        "series": [{"d": str(d), "v": int(v)} for d, v in
                   zip(per_day["as_of"].to_list(), cumulative, strict=False)],
        "latest_advances": int(per_day["advances"][-1]),
        "latest_declines": int(per_day["declines"][-1]),
        "universe": int(per_day["n"][-1]),
        "caveat": ("A cumulative line trends down by construction through a long "
                   "decline, so its level is partly a function of duration. What "
                   "matters is whether it keeps making lows while price rallies."),
    }


def breadth_over_tracked() -> dict:
    """% of the tracked universe above its own 50- and 200-day averages."""
    above_50 = above_200 = counted = 0
    new_highs = new_lows = 0
    rows = []
    for asset in registry.tracked():
        bars = store.read_ohlcv(asset.symbol)
        if bars.height < 200:
            continue
        closes = bars["close"].to_numpy().astype(float)
        sma50 = levels.sma(closes, 50)
        sma200 = levels.sma(closes, 200)
        if np.isnan(sma50[-1]) or np.isnan(sma200[-1]):
            continue
        counted += 1
        over50 = bool(closes[-1] > sma50[-1])
        over200 = bool(closes[-1] > sma200[-1])
        above_50 += over50
        above_200 += over200

        window = closes[-90:]
        is_high = bool(closes[-1] >= window.max())
        is_low = bool(closes[-1] <= window.min())
        new_highs += is_high
        new_lows += is_low
        rows.append({"symbol": asset.symbol, "above_50d": over50,
                     "above_200d": over200, "new_90d_high": is_high,
                     "new_90d_low": is_low})

    if not counted:
        return {"available": False, "reason": "no asset has 200 bars"}
    return {
        "available": True,
        "universe": counted,
        "pct_above_50d": round(above_50 / counted * 100, 1),
        "pct_above_200d": round(above_200 / counted * 100, 1),
        "new_90d_highs": new_highs,
        "new_90d_lows": new_lows,
        "rows": rows,
        "universe_note": ("Measured over the tracked book, benchmarks and "
                          "watchlist — not the top 100, which would need 200 "
                          "days of prices for 100 coins that the free tier does "
                          "not serve."),
    }


def beating_btc(days: int = 90) -> dict:
    """The altseason-style reading: how many names are outperforming BTC."""
    btc = store.read_ohlcv("BTC")
    if btc.height < days + 1:
        return {"available": False, "reason": "not enough BTC history"}
    btc_return = float(btc["close"][-1] / btc["close"][-1 - days] - 1)

    winners, total, rows = 0, 0, []
    for asset in registry.tracked():
        if asset.symbol == "BTC":
            continue
        bars = store.read_ohlcv(asset.symbol)
        if bars.height < days + 1:
            continue
        change = float(bars["close"][-1] / bars["close"][-1 - days] - 1)
        total += 1
        beat = change > btc_return
        winners += beat
        rows.append({"symbol": asset.symbol, "return_pct": round(change * 100, 1),
                     "beat_btc": beat})

    if not total:
        return {"available": False, "reason": "no comparable asset"}
    rows.sort(key=lambda r: -r["return_pct"])
    return {
        "available": True,
        "days": days,
        "btc_return_pct": round(btc_return * 100, 1),
        "pct_beating_btc": round(winners / total * 100, 1),
        "winners": winners, "universe": total, "rows": rows,
    }


def correlations(days: int = 90) -> dict:
    """Rolling correlation of daily returns against BTC.

    The book's thesis assumes alt correlation around 0.8, so the useful
    question is not the level but when a name breaks from it.
    """
    btc = store.read_ohlcv("BTC").select(["date", pl.col("close").alias("btc")])
    if btc.height < days + 1:
        return {"available": False, "reason": "not enough BTC history"}

    rows = []
    for asset in registry.tracked():
        if asset.symbol == "BTC":
            continue
        bars = store.read_ohlcv(asset.symbol).select(
            ["date", pl.col("close").alias("asset")])
        joined = btc.join(bars, on="date", how="inner").sort("date").tail(days + 1)
        if joined.height < MIN_CORRELATION_DAYS:
            continue
        btc_returns = np.diff(np.log(joined["btc"].to_numpy().astype(float)))
        asset_returns = np.diff(np.log(joined["asset"].to_numpy().astype(float)))
        if btc_returns.std() == 0 or asset_returns.std() == 0:
            continue
        rows.append({
            "symbol": asset.symbol,
            "correlation": round(float(np.corrcoef(btc_returns, asset_returns)[0, 1]), 3),
            "n": int(len(btc_returns)),
        })

    if not rows:
        return {"available": False, "reason": "no asset shares enough history"}
    rows.sort(key=lambda r: -r["correlation"])
    values = [r["correlation"] for r in rows]
    return {
        "available": True, "days": days, "rows": rows,
        "median": round(float(np.median(values)), 3),
        "universe": len(rows),
    }
