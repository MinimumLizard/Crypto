"""The catalyst radar and the low-cap screener (SPEC §6.13).

**The gate is the whole design.** PLAN §2 records the lesson from Nelson-Siegel:
*"ranking on z-score alone selects for illiquid bonds, whose stale quotes jump
and so score highest."* A volume z-score has the identical failure mode here. A
microcap that trades $40k on a normal day and $400k on a day someone noticed it
scores a z of 6 and would sit at the top of a list headed "where is volatility
likely" — while telling you nothing you could act on, because the reason the
z-score is large is that the denominator is tiny.

So liquidity gates membership BEFORE anything is ranked, and a name that fails
the gate is listed as gated with its reason rather than dropped silently. A
score is only computed for names that pass.

Every component is bounded to [0, 1] and the total is a plain sum with the
weights visible in `COMPONENTS`. There is no fitted model here and none is
implied: this ranks attention, it does not predict return.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from pipeline import registry, store
from pipeline.metrics import derivs, levels

# A name must clear both to be scored. The floors are deliberately low: this is
# a personal book, not an institutional one, and the point is to exclude names
# whose z-scores are arithmetic rather than to demand deep liquidity.
MIN_DAY_VOLUME_USD = 1_000_000.0
MIN_MARKET_CAP_USD = 10_000_000.0

# Component -> weight. Visible because §6.13 asks for a transparent score and
# because a weight nobody can see is a fitted parameter wearing a disguise.
COMPONENTS: dict[str, float] = {
    "funding_extreme": 1.0,
    "volume_spike": 1.0,
    "vol_expansion": 1.0,
    "near_key_level": 0.75,
    "oi_heavy": 0.75,
    "unlock_soon": 1.5,
}

UNLOCK_HORIZON_DAYS = 14
VOLUME_WINDOW = 60
NEAR_LEVEL_PCT = 3.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _volume_zscore(bars: pl.DataFrame) -> float | None:
    """Volume z against its own trailing window, excluding today (PLAN §5.3)."""
    if bars.is_empty() or "volume" not in bars.columns:
        return None
    volume = bars.sort("date")["volume"].to_numpy().astype(float)
    if len(volume) < VOLUME_WINDOW + 1:
        return None
    window = volume[-(VOLUME_WINDOW + 1):-1]
    sd = float(np.std(window, ddof=1))
    if sd <= 0:
        return None
    return float((volume[-1] - float(np.mean(window))) / sd)


def _realised_vol(bars: pl.DataFrame, days: int) -> float | None:
    if bars.is_empty():
        return None
    closes = bars.sort("date")["close"].to_numpy().astype(float)
    if len(closes) < days + 1:
        return None
    returns = np.diff(np.log(closes[-(days + 1):]))
    return float(np.std(returns, ddof=1) * np.sqrt(365) * 100)


def _near_level(level_data: dict) -> tuple[float | None, str]:
    """Closest key level as a percentage distance, and which one it is."""
    if not level_data.get("available"):
        return None, ""
    price = level_data.get("price")
    if not price:
        return None, ""
    candidates = {
        "200-day": level_data.get("sma200d"),
        "50-week": level_data.get("sma50w"),
        "200-week": level_data.get("sma200w"),
        "prior month high": level_data.get("prev_month_high"),
        "prior month low": level_data.get("prev_month_low"),
    }
    best, best_name = None, ""
    for name, value in candidates.items():
        if not value:
            continue
        distance = abs(price / value - 1) * 100
        if best is None or distance < best:
            best, best_name = distance, name
    return best, best_name


def _unlocks_within(horizon_days: int) -> dict[str, dict]:
    """Unlocks from config. D007 leaves the file empty until it is filled in."""
    configured = registry.unlocks() or {}
    today = dt.date.today()
    out: dict[str, dict] = {}
    for symbol, events in (configured.get("unlocks") or {}).items():
        for event in events or []:
            raw = str(event.get("date", ""))
            try:
                when = dt.date.fromisoformat(raw)
            except ValueError:
                continue
            days = (when - today).days
            if 0 <= days <= horizon_days:
                out[symbol] = {"date": raw, "days": days,
                               "pct_of_float": event.get("pct_of_float")}
    return out


def catalyst_radar() -> dict:
    """Rank the tracked universe by how likely it is to move, gated first."""
    funding_frame = store.read_snapshot("funding_by_venue")
    contexts = store.read_snapshot("perp_contexts")
    supply = store.read_snapshot("supply")

    caps: dict[str, float] = {}
    if not supply.is_empty():
        latest = (supply.sort("observed_at")
                  .group_by("coingecko_id", maintain_order=True).last())
        by_id = dict(zip(latest["coingecko_id"].to_list(),
                         latest["market_cap"].to_list(), strict=False))
        for asset in registry.tracked():
            if asset.coingecko_id and by_id.get(asset.coingecko_id):
                caps[asset.symbol] = float(by_id[asset.coingecko_id])

    oi_by_coin: dict[str, dict] = {}
    if not contexts.is_empty():
        newest = (contexts.sort("observed_at")
                  .group_by("coin", maintain_order=True).last())
        oi_by_coin = {r["coin"]: r for r in newest.iter_rows(named=True)}

    # funding_table does not attach the z-score; the derivatives builder adds it
    # in a second pass. Reusing the table alone silently dropped the funding
    # component from every row, which is how the two names beyond three sigma
    # ended up ranked below names with no funding signal at all.
    funding_rows: dict[str, dict] = {}
    if not funding_frame.is_empty():
        for row in derivs.funding_table(funding_frame):
            row["zscore"] = derivs.funding_zscore(row["coin"])
            funding_rows[row["coin"]] = row

    unlocks = _unlocks_within(UNLOCK_HORIZON_DAYS)

    scored: list[dict] = []
    gated: list[dict] = []

    for asset in registry.tracked():
        bars = store.read_ohlcv(asset.symbol)
        if bars.is_empty():
            gated.append({"symbol": asset.symbol, "reason": "no price bars"})
            continue
        bars = bars.sort("date")

        cap = caps.get(asset.symbol)
        context = oi_by_coin.get(asset.hl_perp or "")
        day_volume = float(context["day_volume_usd"]) if context and \
            context.get("day_volume_usd") else None

        # --- the gate, before any ranking -------------------------------
        if day_volume is None and "volume" in bars.columns:
            # Spot bar volume is in base units; price it to compare like for like.
            last = bars.tail(1)
            day_volume = float(last["volume"][0]) * float(last["close"][0])
        if day_volume is None or day_volume < MIN_DAY_VOLUME_USD:
            gated.append({
                "symbol": asset.symbol, "day_volume_usd": day_volume,
                "reason": (f"24h volume {_money(day_volume)} is below the "
                           f"{_money(MIN_DAY_VOLUME_USD)} floor; a z-score on a "
                           "denominator this small measures the denominator")})
            continue
        if cap is not None and cap < MIN_MARKET_CAP_USD:
            gated.append({
                "symbol": asset.symbol, "market_cap": cap,
                "reason": (f"market cap {_money(cap)} is below the "
                           f"{_money(MIN_MARKET_CAP_USD)} floor")})
            continue

        # --- components, each bounded to [0, 1] -------------------------
        parts: dict[str, float] = {}
        detail: dict[str, object] = {}

        funding = funding_rows.get(asset.hl_perp or "")
        zscore = (funding or {}).get("zscore") or {}
        if zscore.get("available") and zscore.get("zscore") is not None:
            z = abs(float(zscore["zscore"]))
            parts["funding_extreme"] = _clamp(z / 3.0)
            detail["funding_z"] = round(float(zscore["zscore"]), 2)
            detail["funding_annualised_pct"] = zscore.get("current_annualised_pct")

        volume_z = _volume_zscore(bars)
        if volume_z is not None:
            parts["volume_spike"] = _clamp(volume_z / 3.0)
            detail["volume_z"] = round(volume_z, 2)

        short_vol = _realised_vol(bars, 10)
        long_vol = _realised_vol(bars, 60)
        if short_vol and long_vol and long_vol > 0:
            ratio = short_vol / long_vol
            parts["vol_expansion"] = _clamp((ratio - 1.0) / 1.0)
            detail["vol_ratio_10_60"] = round(ratio, 2)

        level_data = levels.key_levels(bars)
        distance, level_name = _near_level(level_data)
        if distance is not None:
            parts["near_key_level"] = _clamp(1.0 - distance / NEAR_LEVEL_PCT)
            detail["nearest_level"] = level_name
            detail["distance_to_level_pct"] = round(distance, 2)

        if context and cap:
            oi = float(context.get("open_interest_usd") or 0.0)
            ratio = oi / cap if cap else 0.0
            parts["oi_heavy"] = _clamp(ratio / 0.10)
            detail["oi_to_market_cap_pct"] = round(ratio * 100, 2)

        unlock = unlocks.get(asset.symbol)
        if unlock:
            parts["unlock_soon"] = 1.0
            detail["unlock_in_days"] = unlock["days"]
            detail["unlock_pct_of_float"] = unlock.get("pct_of_float")

        if not parts:
            gated.append({"symbol": asset.symbol,
                          "reason": "passed the liquidity gate but no component "
                                    "had enough history to score"})
            continue

        score = sum(COMPONENTS[key] * value for key, value in parts.items())
        possible = sum(COMPONENTS[key] for key in parts)
        scored.append({
            "symbol": asset.symbol,
            "name": asset.name,
            "sector": asset.sector,
            "kind": asset.kind,
            "score": round(score, 3),
            "score_pct": round(score / possible * 100, 1) if possible else None,
            "components": {k: round(v, 3) for k, v in parts.items()},
            "measured": sorted(parts),
            "missing": sorted(set(COMPONENTS) - set(parts)),
            "day_volume_usd": day_volume,
            "market_cap": cap,
            **detail,
        })

    scored.sort(key=lambda r: -r["score"])
    return {
        "available": bool(scored),
        "rows": scored,
        "gated": sorted(gated, key=lambda r: r["symbol"]),
        "weights": COMPONENTS,
        "gate": {"min_day_volume_usd": MIN_DAY_VOLUME_USD,
                 "min_market_cap_usd": MIN_MARKET_CAP_USD},
        "how_to_read": (
            "This ranks where a move is likely to SHOW UP, not which way it "
            "goes. Liquidity gates membership before anything is ranked, "
            "because a volume z-score of 4 on a name that trades $40k a day is "
            "a fact about the denominator. Names that failed the gate are "
            "listed with the reason rather than quietly dropped."),
        "score_note": (
            "Each component is scaled to 0-1 and summed with the weights shown. "
            "Score % is the score against what the components that COULD be "
            "measured for that name are worth, so a name missing a component is "
            "not penalised for a gap in the data. Nothing here is fitted."),
    }


def _money(value: float | None) -> str:
    if value is None:
        return "unknown"
    for unit, size in (("bn", 1e9), ("m", 1e6), ("k", 1e3)):
        if abs(value) >= size:
            return f"${value / size:.1f}{unit}"
    return f"${value:.0f}"


def screener() -> dict:
    """The low-cap screen (§6.13), over whatever the top-100 snapshot holds.

    The brief asks for CoinGecko down to roughly rank 1,000. The free tier's
    markets endpoint pages 250 at a time and each page is a call, so the daily
    build takes the top 100 it already fetches for breadth and screens that.
    The panel says so rather than implying a universe it does not have.
    """
    frame = store.read_snapshot("top100")
    if frame.is_empty():
        return {"available": False, "reason": "no top-100 snapshot yet"}

    latest_day = frame["as_of"].max()
    latest = (frame.filter(pl.col("as_of") == latest_day)
              .sort("observed_at").group_by("coingecko_id", maintain_order=True).last())

    tracked = {a.coingecko_id for a in registry.tracked() if a.coingecko_id}
    perps = {a.coingecko_id for a in registry.tracked() if a.hl_perp and a.coingecko_id}

    rows = []
    for row in latest.iter_rows(named=True):
        cap = row.get("market_cap")
        volume = row.get("volume_24h") or row.get("total_volume")
        fdv = row.get("fully_diluted_valuation")
        if not cap:
            continue
        rows.append({
            "symbol": str(row.get("symbol") or "").upper(),
            "name": row.get("name"),
            "coingecko_id": row.get("coingecko_id"),
            "market_cap": cap,
            "volume_24h": volume,
            "volume_to_cap": round(float(volume) / float(cap), 4) if volume else None,
            "fdv": fdv,
            "fdv_to_cap": round(float(fdv) / float(cap), 3) if fdv and cap else None,
            "change_24h": row.get("change_24h"),
            "tracked": row.get("coingecko_id") in tracked,
            "has_perp": row.get("coingecko_id") in perps,
        })

    rows.sort(key=lambda r: -(r["market_cap"] or 0))
    return {
        "available": True,
        "rows": rows,
        "as_of": str(latest_day),
        "universe": len(rows),
        "universe_note": (
            "The top 100 by market cap, which is the slice the daily build "
            "already fetches. §6.13 asks for rank 1,000; that is 4 more paged "
            "calls against a rate-limited free key and is not fetched, so this "
            "names the universe it has rather than implying a wider one."),
        "how_to_read": (
            "FDV/MC above about 3 means most of the supply has not been issued "
            "yet, so today's price is being set by a small float. Volume/MC is "
            "how much of the cap changes hands in a day: very low is a name "
            "nobody is trading, very high is usually an event."),
    }


__all__ = ["catalyst_radar", "screener", "COMPONENTS", "MIN_DAY_VOLUME_USD",
           "MIN_MARKET_CAP_USD"]
