"""Market-wide aggregates: total cap, dominance, stablecoin supply, the top 100.

What is and is not available historically drives the whole breadth page:

* **Stablecoin supply has 3,224 days of history** from DefiLlama, back to 2017.
  So stablecoin supply and its 30/90-day change are real series today.
* **Total market cap and dominance are CURRENT ONLY.** CoinGecko's `/global`
  serves no history on the free tier. They are snapshotted every run and the
  series starts from the first build — §3's "own history store", and the page
  says how many days it has rather than drawing a line through one point.
* **The top-100 advance-decline line is built forward**, per §6.7. Back-filling
  it from today's constituents would measure a basket chosen for having
  survived, which is a survivorship-biased line that always looks better than
  the market did. The snapshot is one call a day and the line starts now.
"""

from __future__ import annotations

import datetime as dt
import time

import polars as pl

from pipeline import health, http, store
from pipeline.fetchers.coingecko import BASE as CG
from pipeline.fetchers.coingecko import _auth

STABLES = "https://stablecoins.llama.fi"


def global_snapshot() -> dict:
    """Total market cap, volume and dominance, snapshotted for later."""
    started = time.perf_counter()
    try:
        payload = http.get_json(f"{CG}/global", headers=_auth(), cache_hours=1)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="coingecko", endpoint="global", dataset="total cap and dominance",
            status="http_error", error=str(exc)[:200]))
        return {}

    data = payload.get("data") or {}
    dominance = data.get("market_cap_percentage") or {}
    row = {
        "total_market_cap": data.get("total_market_cap", {}).get("usd"),
        "total_volume": data.get("total_volume", {}).get("usd"),
        "btc_dominance": dominance.get("btc"),
        "eth_dominance": dominance.get("eth"),
        "market_cap_change_24h": data.get("market_cap_change_percentage_24h_usd"),
        "active_cryptocurrencies": data.get("active_cryptocurrencies"),
    }
    store.write_snapshot("global", pl.DataFrame([row]), ["as_of"])
    health.record(health.Record(
        source="coingecko", endpoint="global", dataset="total cap and dominance",
        status="ok", rows=1, as_of=dt.date.today().isoformat(),
        expected_lag_days=0.5,
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return row


def top_market(limit: int = 100) -> pl.DataFrame:
    """The top N by market cap, snapshotted daily for the advance-decline line."""
    started = time.perf_counter()
    try:
        rows = http.get_json(f"{CG}/coins/markets", params={
            "vs_currency": "usd", "order": "market_cap_desc",
            "per_page": str(limit), "page": "1", "sparkline": "false",
            "price_change_percentage": "24h,7d,30d",
        }, headers=_auth(), cache_hours=2)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="coingecko", endpoint="coins/markets top100",
            dataset="top 100", status="http_error", error=str(exc)[:200]))
        return pl.DataFrame()

    if not rows:
        return pl.DataFrame()

    frame = pl.DataFrame([{
        "coingecko_id": r["id"],
        "symbol": (r.get("symbol") or "").upper(),
        "rank": r.get("market_cap_rank"),
        "market_cap": r.get("market_cap"),
        "price": r.get("current_price"),
        "volume_24h": r.get("total_volume"),
        "change_24h": r.get("price_change_percentage_24h_in_currency"),
        "change_7d": r.get("price_change_percentage_7d_in_currency"),
        "change_30d": r.get("price_change_percentage_30d_in_currency"),
    } for r in rows], infer_schema_length=None)

    store.write_snapshot("top100", frame, ["as_of", "coingecko_id"])
    health.record(health.Record(
        source="coingecko", endpoint="coins/markets top100", dataset="top 100",
        status="ok", rows=len(frame), as_of=dt.date.today().isoformat(),
        expected_lag_days=0.5,
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


def stablecoin_supply() -> pl.DataFrame:
    """Total stablecoin supply, daily, back to 2017."""
    started = time.perf_counter()
    try:
        rows = http.get_json(f"{STABLES}/stablecoincharts/all", cache_hours=12)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="defillama", endpoint="stablecoincharts/all",
            dataset="stablecoin supply", status="http_error", error=str(exc)[:200]))
        return pl.DataFrame()

    out = []
    for row in rows or []:
        total = (row.get("totalCirculatingUSD") or {}).get("peggedUSD")
        if total is None:
            continue
        out.append({
            "date": dt.datetime.fromtimestamp(int(row["date"]), dt.UTC).date(),
            "value": float(total),
        })
    if not out:
        return pl.DataFrame()

    frame = pl.DataFrame(out).unique(subset=["date"], keep="last").sort("date")
    store.write_onchain("market", "StablecoinSupply", frame)
    health.record(health.Record(
        source="defillama", endpoint="stablecoincharts/all",
        dataset="stablecoin supply", status="ok", rows=len(frame),
        as_of=str(frame["date"].max()),
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


def stablecoins_by_chain() -> pl.DataFrame:
    """Current stablecoin supply per chain, and the largest issuers."""
    started = time.perf_counter()
    try:
        payload = http.get_json(f"{STABLES}/stablecoins",
                                params={"includePrices": "true"}, cache_hours=6)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="defillama", endpoint="stablecoins",
            dataset="stablecoins by chain", status="http_error",
            error=str(exc)[:200]))
        return pl.DataFrame()

    assets = payload.get("peggedAssets") or []
    by_chain: dict[str, float] = {}
    issuers = []
    for asset in assets:
        circulating = float((asset.get("circulating") or {}).get("peggedUSD") or 0.0)
        if circulating <= 0:
            continue
        issuers.append({
            "symbol": asset.get("symbol"), "name": asset.get("name"),
            "circulating": circulating, "price": asset.get("price"),
            # A peg is only news when it breaks; §8 alerts above 0.5%.
            "depeg_pct": ((float(asset["price"]) - 1.0) * 100
                          if asset.get("price") else None),
        })
        for chain, amount in (asset.get("chainCirculating") or {}).items():
            value = (amount.get("current") or {}).get("peggedUSD")
            if value:
                by_chain[chain] = by_chain.get(chain, 0.0) + float(value)

    frame = pl.DataFrame(
        [{"chain": c, "circulating": v} for c, v in sorted(
            by_chain.items(), key=lambda kv: -kv[1])], infer_schema_length=None)
    if not frame.is_empty():
        store.write_snapshot("stablecoins_by_chain", frame, ["as_of", "chain"])

    issuer_frame = pl.DataFrame(issuers, infer_schema_length=None)
    if not issuer_frame.is_empty():
        store.write_snapshot("stablecoin_issuers", issuer_frame, ["as_of", "symbol"])

    health.record(health.Record(
        source="defillama", endpoint="stablecoins", dataset="stablecoins by chain",
        status="ok", rows=len(frame), as_of=dt.date.today().isoformat(),
        expected_lag_days=0.5,
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


def fetch_all() -> dict[str, int]:
    return {
        "global": 1 if global_snapshot() else 0,
        "top100": len(top_market()),
        "stablecoin_supply": len(stablecoin_supply()),
        "stablecoins_by_chain": len(stablecoins_by_chain()),
    }
