"""Per-page JSON the site reads. Rebuilt from the store on every build.

Nothing derived is stored: every number here is recomputed from raw history
with expanding windows, which is what makes the build idempotent and keeps
look-ahead out (PLAN §5.3).

Two conventions run through all of it:

* **A missing value is `None` with a reason beside it, never 0.** The site
  renders a reason; it has no way to render a fabricated zero honestly.
* **Every panel carries `as_of` and `how_to_read`.** §2.2 and §2.5 require both,
  and putting them in the artefact rather than the template means the page
  cannot accidentally show a number without its date.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from pipeline import health, paths, registry, store
from pipeline.metrics import levels, risk
from pipeline.signals import minilizard as ml


class Encoder(json.JSONEncoder):
    """numpy scalars and dates are not JSON; NaN is not JSON either.

    NaN becomes null rather than the literal `NaN`, which is invalid JSON and
    which `JSON.parse` rejects outright -- a silent way to break every page.
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            value = float(o)
            return None if np.isnan(value) else value
        if isinstance(o, np.ndarray):
            return [self.default(v) if isinstance(v, np.generic) else v for v in o.tolist()]
        if isinstance(o, dt.date | dt.datetime):
            return o.isoformat()
        if isinstance(o, np.bool_):
            return bool(o)
        return super().default(o)


def _clean(value: Any) -> Any:
    """Recursively replace NaN/inf with None before serialising."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    return value


def write(name: str, payload: dict) -> Path:
    paths.ARTEFACTS.mkdir(parents=True, exist_ok=True)
    path = paths.ARTEFACTS / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(payload), cls=Encoder, separators=(",", ":")))
    return path


# ---------------------------------------------------------------------------
# Per-asset
# ---------------------------------------------------------------------------

def _regime_for(asset: registry.Asset) -> dict:
    """The MiniLizard reading, or an honest statement of why there isn't one.

    Parity is defined on Binance bars, so the score is computed on the Binance
    series when one is long enough. Seven of the tracked names either have no
    Binance pair or were listed too recently to warm up, and for them the panel
    says so rather than quietly scoring a different venue's bars and presenting
    the result as if it were comparable to TradingView.
    """
    parity_venue = "binance" if asset.binance_spot else None
    bars = store.read_ohlcv(asset.symbol, parity_venue) if parity_venue else store._empty_ohlcv()

    if bars.height < ml.WARMUP_BARS:
        longest = store.venues(asset.symbol)
        fallback = store.read_ohlcv(asset.symbol, longest[0]) if longest else store._empty_ohlcv()
        reason = ("no Binance pair exists" if not asset.binance_spot
                  else f"Binance has only {bars.height} bars, and the engine needs "
                       f"{ml.WARMUP_BARS} to warm up")
        if fallback.height >= ml.WARMUP_BARS:
            series = _score(asset, fallback)
            series["parity"] = "substitute"
            series["parity_note"] = (
                f"Scored on {longest[0]} bars because {reason}. This score is real "
                f"but is NOT comparable to a TradingView reading, which is drawn "
                f"from Binance.")
            return series
        return {
            "available": False,
            "reason": f"{reason}; no other venue has {ml.WARMUP_BARS} bars either",
        }

    series = _score(asset, bars)
    series["parity"] = "binance"
    series["parity_note"] = (
        "Computed on Binance daily bars, the series TradingView draws. Parity "
        "itself is UNVERIFIED: no reference readings have been supplied.")
    return series


def _score(asset: registry.Asset, bars: pl.DataFrame) -> dict:
    hourly = store.read_ohlcv(asset.symbol, "binance", interval="1h")
    cvd = None
    if hourly.height > 100:
        cvd = ml.cvd_from_hourly(
            list(hourly["date"]), hourly["open"].to_numpy(),
            hourly["close"].to_numpy(), hourly["volume"].to_numpy(), list(bars["date"]))

    result = ml.compute(
        list(bars["date"]), bars["open"].to_numpy(), bars["high"].to_numpy(),
        bars["low"].to_numpy(), bars["close"].to_numpy(), bars["volume"].to_numpy(),
        extension_threshold_pct=asset.extension_threshold_pct, cvd=cvd)

    tail = 400
    history = [
        {"d": str(d), "s": None if np.isnan(s) else round(float(s), 2), "r": r}
        for d, s, r in zip(result.date[-tail:], result.score[-tail:],
                           result.regime[-tail:], strict=False)
    ]
    signals = [
        {"date": str(d), "signal": sig, "score": round(float(s), 2)}
        for d, sig, s in zip(result.date, result.signal, result.score, strict=False)
        if sig
    ][-12:]

    return {
        "available": True,
        "variant": asset.minilizard_variant,
        "extension_threshold_pct": asset.extension_threshold_pct,
        "cvd_source": "binance 1h" if cvd is not None else None,
        "cvd_note": (None if cvd is not None else
                     "Volume block uses VWAP and OBV only; no hourly bars stored, "
                     "so the CVD vote is absent and the block is scaled by the two "
                     "votes available rather than treating the third as neutral."),
        "latest": result.latest(),
        "history": history,
        "signals": signals,
        "no_measured_edge": asset.minilizard_no_measured_edge,
        "how_to_read": (
            "A composite of price structure, trend, momentum and volume, clamped "
            "to plus or minus 100, with a 10-point hysteresis band turning it into "
            "a regime. Measured as a DRAWDOWN FILTER, not a source of alpha: on "
            "recent data it cut drawdown from roughly 67% to 40% without beating "
            "buy-and-hold on return."),
    }


def build_asset(asset: registry.Asset) -> dict:
    venues = store.venues(asset.symbol)
    display = store.read_ohlcv(asset.symbol)          # longest history
    payload: dict[str, Any] = {
        "symbol": asset.symbol,
        "name": asset.name,
        "kind": asset.kind,
        "tier": asset.tier,
        "sector": asset.sector,
        "coingecko_id": asset.coingecko_id,
        "venues": venues,
        "display_venue": venues[0] if venues else None,
        "parity_venue": "binance" if asset.binance_spot else None,
        "perps": {"hyperliquid": bool(asset.hl_perp), "binance": bool(asset.binance_spot)},
        "data_quality_grade": asset.data_quality_grade,
        "quality_flags": asset.quality_flags,
        "open_item": asset.open_item or None,
        "thesis": asset.thesis or None,
        "watch_triggers": asset.watch_triggers,
        "fails_gas_threshold": asset.fails_gas_threshold,
        "gas_threshold_usd": asset.gas_threshold_usd,
    }

    if display.is_empty():
        payload["price"] = {"available": False, "reason": "no bars from any venue"}
        payload["regime"] = {"available": False, "reason": "no bars"}
        return payload

    payload["levels"] = levels.key_levels(display)
    payload["returns"] = levels.returns(display)
    payload["fifty_week"] = levels.fifty_week_tracker(display)
    payload["regime"] = _regime_for(asset)

    tail = display.tail(1200)
    payload["ohlc"] = [
        {"d": str(d), "o": o, "h": h, "l": low, "c": c}
        for d, o, h, low, c in zip(
            tail["date"], tail["open"], tail["high"], tail["low"], tail["close"],
            strict=False)
    ]

    # Relative strength against BTC, on a shared date index.
    btc = store.read_ohlcv("BTC")
    if not btc.is_empty() and asset.symbol != "BTC":
        joined = (display.select(["date", pl.col("close").alias("asset")])
                  .join(btc.select(["date", pl.col("close").alias("btc")]), on="date"))
        if joined.height > 60:
            ratio = (joined["asset"] / joined["btc"]).to_numpy()
            payload["rs_vs_btc"] = {
                "available": True,
                "series": [{"d": str(d), "v": round(float(v), 10)}
                           for d, v in zip(joined["date"][-500:], ratio[-500:], strict=False)],
                "sma50": _last(levels.sma(ratio, 50)),
                "sma200": _last(levels.sma(ratio, 200)),
                "how_to_read": ("The asset priced in BTC. Rising means it is "
                                "outperforming Bitcoin, whatever both are doing in "
                                "dollars."),
            }
    return payload


def _last(array) -> float | None:
    if len(array) == 0 or np.isnan(array[-1]):
        return None
    return round(float(array[-1]), 10)


# ---------------------------------------------------------------------------
# Source health
# ---------------------------------------------------------------------------

def build_source_health() -> dict:
    latest = health.latest_by_source()
    rows = []
    if not latest.is_empty():
        for row in latest.sort(["status", "source"]).iter_rows(named=True):
            rows.append({
                "source": row["source"], "dataset": row["dataset"],
                "status": row["status"], "as_of": row["as_of"],
                "fetched_at": row["fetched_at"], "rows": row["rows"],
                "error": (row["error"] or "")[:200],
            })
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "as_of": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "rows": rows,
        "counts": counts,
        "how_to_read": (
            "One row per source per dataset, showing the most recent attempt. "
            "'needs_key' is not a failure -- it is a source that works but has "
            "not been given credentials. A failing source never breaks the build; "
            "its last good value is carried forward and marked stale."),
    }


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

def build_home(asset_payloads: dict[str, dict]) -> dict:
    grid = []
    for asset in registry.tracked():
        payload = asset_payloads.get(asset.symbol, {})
        regime = payload.get("regime", {})
        returns = payload.get("returns", {})
        level = payload.get("levels", {})
        grid.append({
            "symbol": asset.symbol,
            "name": asset.name,
            "kind": asset.kind,
            "tier": asset.tier,
            "sector": asset.sector,
            "price": level.get("price"),
            "r1d": returns.get("r1d"), "r7d": returns.get("r7d"),
            "r30d": returns.get("r30d"), "r90d": returns.get("r90d"),
            "regime": (regime.get("latest") or {}).get("regime") if regime.get("available") else None,
            "score": (regime.get("latest") or {}).get("score") if regime.get("available") else None,
            "regime_reason": None if regime.get("available") else regime.get("reason"),
            "parity": regime.get("parity"),
            "dist_200d": level.get("sma200d_distance_pct"),
            "grade": asset.data_quality_grade,
        })

    btc = asset_payloads.get("BTC", {})
    return {
        "as_of": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "btc_levels": btc.get("levels", {}),
        "btc_fifty_week": btc.get("fifty_week", {}),
        "btc_regime": btc.get("regime", {}),
        "grid": grid,
        "unplaced_in_buy_order": registry.unplaced_in_buy_order(),
        "weights_configured": registry.weights() is not None,
        "how_to_read": (
            "What matters today, in one screen. The grid scores every tracked "
            "name; a blank regime means the engine has too little history at the "
            "venue that defines parity, which is stated rather than filled in."),
    }
