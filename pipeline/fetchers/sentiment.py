"""Attention and sentiment: Fear & Greed, and Wikipedia page views.

Both are free and need no key. Wikimedia returns 403 to a generic User-Agent
and 200 to one carrying contact details, which is why `pipeline.http` sets a
specific one -- the difference was found by probing, not by reading the docs.

These are attention proxies, not valuation. §6.5 marks follower-style series as
low-conviction; page views and Fear & Greed are steadier than follower counts
but they still measure interest, and interest is not a forecast.
"""

from __future__ import annotations

import datetime as dt
import time

import polars as pl

from pipeline import health, http, store

FNG = "https://api.alternative.me/fng/"
WIKI = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        "en.wikipedia/all-access/user/{article}/daily/{start}/{end}")

ARTICLES = {"Bitcoin": "btc", "Ethereum": "eth", "Solana_(blockchain_platform)": "sol"}


def fear_and_greed() -> pl.DataFrame:
    """The whole Fear & Greed history in one call (limit=0 means everything)."""
    started = time.perf_counter()
    try:
        payload = http.get_json(FNG, params={"limit": "0"}, cache_hours=6)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="alternative.me", endpoint="fng", dataset="fear and greed",
            status="http_error", error=str(exc)[:200]))
        return pl.DataFrame()

    rows = payload.get("data", [])
    if not rows:
        health.record(health.Record(
            source="alternative.me", endpoint="fng", dataset="fear and greed",
            status="empty"))
        return pl.DataFrame()

    frame = pl.DataFrame([{
        "date": dt.datetime.fromtimestamp(int(r["timestamp"]), dt.UTC).date(),
        "value": float(r["value"]),
    } for r in rows]).sort("date")
    store.write_onchain("sentiment", "FearGreed", frame)
    health.record(health.Record(
        source="alternative.me", endpoint="fng", dataset="fear and greed",
        status="ok", rows=len(frame), as_of=str(frame["date"].max()),
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


def wikipedia_views(article: str = "Bitcoin", key: str = "btc",
                    start: str = "20150701") -> pl.DataFrame:
    """Daily page views for one article."""
    started = time.perf_counter()
    end = (dt.date.today() - dt.timedelta(days=2)).strftime("%Y%m%d")
    url = WIKI.format(article=article, start=start, end=end)
    try:
        payload = http.get_json(url, cache_hours=12)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="wikimedia", endpoint=article, dataset=f"{key} page views",
            status="http_error", error=str(exc)[:200]))
        return pl.DataFrame()

    items = payload.get("items", [])
    if not items:
        health.record(health.Record(
            source="wikimedia", endpoint=article, dataset=f"{key} page views",
            status="empty"))
        return pl.DataFrame()

    frame = pl.DataFrame([{
        "date": dt.datetime.strptime(i["timestamp"][:8], "%Y%m%d").date(),
        "value": float(i["views"]),
    } for i in items]).sort("date")
    store.write_onchain("sentiment", f"WikiViews_{key}", frame)
    health.record(health.Record(
        source="wikimedia", endpoint=article, dataset=f"{key} page views",
        status="ok", rows=len(frame), as_of=str(frame["date"].max()),
        expected_lag_days=2.0,   # Wikimedia publishes page views ~2 days late
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


def fetch_all() -> dict[str, int]:
    out = {"fear_greed": len(fear_and_greed())}
    for article, key in ARTICLES.items():
        out[f"wiki_{key}"] = len(wikipedia_views(article, key))
    return out
