"""CoinGecko: market caps, supply, rank, and the supply HISTORY nothing else gives.

The interesting part is `supply_history`. SPEC §6.4's headline metric is **net
holder yield = holder yield - net dilution**, and dilution needs circulating
supply as it was in the past. No free API serves that series, which is why §3
tells the pipeline to start snapshotting supply daily -- an answer that only
becomes useful a year from now.

It turns out the series already exists, implicitly. `/coins/{id}/market_chart`
returns `prices` and `market_caps` on the same timestamps, and

    circulating supply = market cap / price

So a year of dilution history is available immediately. Two honest caveats,
both surfaced on the page:

* CoinGecko's historical market cap is itself a derived series and gets
  restated, so the implied supply inherits that. It is not an on-chain read.
* The ratio is noisy day to day (both legs are rounded), so dilution is
  measured between endpoints of a window rather than differenced daily.

Snapshotting continues regardless, because a series we store ourselves is the
only one that cannot be restated under us.
"""

from __future__ import annotations

import datetime as dt
import os
import time

import polars as pl

from pipeline import health, http, registry, store

BASE = "https://api.coingecko.com/api/v3"


def _auth() -> dict[str, str]:
    """Demo key if present. Unauthenticated works but 429s within seconds."""
    key = os.environ.get("COINGECKO_DEMO_KEY", "").strip()
    return {"x-cg-demo-api-key": key} if key else {}


def markets(ids: list[str]) -> pl.DataFrame:
    """Current cap, supply, rank and volume for a batch of ids."""
    started = time.perf_counter()
    try:
        rows = http.get_json(f"{BASE}/coins/markets", params={
            "vs_currency": "usd", "ids": ",".join(ids), "per_page": "250",
            "page": "1", "sparkline": "false",
        }, headers=_auth(), cache_hours=2)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="coingecko", endpoint="coins/markets", dataset="caps and supply",
            status="http_error", error=str(exc)[:200]))
        return pl.DataFrame()

    if not rows:
        health.record(health.Record(
            source="coingecko", endpoint="coins/markets",
            dataset="caps and supply", status="empty"))
        return pl.DataFrame()

    frame = pl.DataFrame([{
        "coingecko_id": r["id"],
        "price_usd": r.get("current_price"),
        "market_cap": r.get("market_cap"),
        "fully_diluted_valuation": r.get("fully_diluted_valuation"),
        "circulating_supply": r.get("circulating_supply"),
        "total_supply": r.get("total_supply"),
        "max_supply": r.get("max_supply"),
        "rank": r.get("market_cap_rank"),
        "volume_24h": r.get("total_volume"),
    } for r in rows], infer_schema_length=None)

    # Snapshot it: §3 asks the pipeline to store daily anything the free APIs
    # do not serve historically. Supply is the main one.
    store.write_snapshot("supply", frame, ["as_of", "coingecko_id"])

    health.record(health.Record(
        source="coingecko", endpoint="coins/markets", dataset="caps and supply",
        status="ok", rows=len(frame), as_of=dt.date.today().isoformat(),
        expected_lag_days=0.5,
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


def supply_history(coingecko_id: str, days: int = 365) -> pl.DataFrame:
    """Daily price, market cap and IMPLIED circulating supply.

    365 days is the hard ceiling: the public API answers 401 above it, with
    "Public API users are limited to querying historical data within the past
    365 days". So a 365-day dilution window has no room either side and is
    measured across roughly 358 days of medians, which understates the
    annualised rate by about 2%. The 90-day window has ample room and is the
    default the page shows.

    This is also the reason the pipeline keeps snapshotting supply daily even
    though the history is available: in a year, our own series will reach
    further back than CoinGecko will serve.

    supply = market cap / price, on matched timestamps. See the module
    docstring for why this is worth doing and what it is not.
    """
    started = time.perf_counter()
    try:
        payload = http.get_json(f"{BASE}/coins/{coingecko_id}/market_chart", params={
            "vs_currency": "usd", "days": str(days), "interval": "daily",
        }, headers=_auth(), cache_hours=12)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="coingecko", endpoint=f"market_chart/{coingecko_id}",
            dataset=f"{coingecko_id} supply history", status="http_error",
            error=str(exc)[:200]))
        return pl.DataFrame()

    prices = payload.get("prices") or []
    caps = payload.get("market_caps") or []
    if not prices or not caps:
        health.record(health.Record(
            source="coingecko", endpoint=f"market_chart/{coingecko_id}",
            dataset=f"{coingecko_id} supply history", status="empty"))
        return pl.DataFrame()

    by_timestamp = {int(t): c for t, c in caps}
    rows = []
    for stamp, price in prices:
        cap = by_timestamp.get(int(stamp))
        if not cap or not price:
            continue
        rows.append({
            "date": dt.datetime.fromtimestamp(stamp / 1000, dt.UTC).date(),
            "price": float(price),
            "market_cap": float(cap),
            "supply": float(cap) / float(price),
        })
    if not rows:
        return pl.DataFrame()

    frame = pl.DataFrame(rows).unique(subset=["date"], keep="last").sort("date")
    store.write_onchain(f"cg_{coingecko_id}", "SupplyImplied",
                        frame.select(["date", pl.col("supply").alias("value")]))
    store.write_onchain(f"cg_{coingecko_id}", "MarketCap",
                        frame.select(["date", pl.col("market_cap").alias("value")]))

    health.record(health.Record(
        source="coingecko", endpoint=f"market_chart/{coingecko_id}",
        dataset=f"{coingecko_id} supply history", status="ok", rows=len(frame),
        as_of=str(frame["date"].max()), expected_lag_days=1.0,
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


def fetch_all() -> dict[str, int]:
    """Current markets for everything, plus a year of supply history each."""
    assets = [a for a in registry.tracked() if a.coingecko_id]
    ids = [a.coingecko_id for a in assets]

    out: dict[str, int] = {}
    # /coins/markets takes up to 250 ids, so one call covers the whole book.
    out["markets"] = len(markets(ids))
    for asset in assets:
        frame = supply_history(asset.coingecko_id)
        out[asset.symbol] = len(frame)
    return out
