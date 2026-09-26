"""DefiLlama: fees, revenue, holders' revenue and TVL, per PARENT protocol.

Read from the parent slug, never by summing children. DefiLlama nests
protocols -- Aave is `aave-v2`/`aave-v3`/`aave-v4` under `parent#aave`, Uniswap
has four versions, Fluid has four -- and the parent endpoint already aggregates
them, so adding the children on top double-counts (D004). Three parent slugs
are also not the obvious guess and would silently return the wrong protocol or
a 400: PUMP is `pump`, SYRUP is `maple-finance`, SKY is `sky`.

The three dataTypes are different quantities and the difference is the whole
point of §6.4:

    dailyFees            everything users pay the protocol      -> P/Fees
    dailyRevenue         the part the protocol keeps            -> P/Revenue
    dailyHoldersRevenue  what reaches token holders             -> P/E

Probing found five names -- AAVE, FLUID, MORPHO, ONDO, CFG -- with years of fee
history and holders' revenue of exactly zero. That is a measurement, not a gap,
and the grading in `metrics/valuation.py` keeps the two apart.

Unlocks and treasury are NOT here: `/emissions`, `/emission/{slug}` and
`/treasuries` all return 402 on the free tier (D007).
"""

from __future__ import annotations

import datetime as dt
import time

import polars as pl

from pipeline import health, http, registry, store

BASE = "https://api.llama.fi"
DATA_TYPES = ("dailyFees", "dailyRevenue", "dailyHoldersRevenue")


def _series(payload: dict) -> pl.DataFrame:
    """`totalDataChart` is [[unix_seconds, value], ...]."""
    chart = payload.get("totalDataChart") or []
    rows = [{
        "date": dt.datetime.fromtimestamp(int(t), dt.UTC).date(),
        "value": float(v or 0.0),
    } for t, v in chart]
    if not rows:
        return pl.DataFrame(schema={"date": pl.Date, "value": pl.Float64})
    return pl.DataFrame(rows).unique(subset=["date"], keep="last").sort("date")


def fees(slug: str, data_type: str) -> pl.DataFrame:
    """One fee series for one parent slug."""
    started = time.perf_counter()
    try:
        payload = http.get_json(f"{BASE}/summary/fees/{slug}",
                                params={"dataType": data_type}, cache_hours=12)
    except Exception as exc:  # noqa: BLE001
        message = str(exc)[:200]
        # A 400 here means DefiLlama has no fee data for this protocol at all,
        # which is a real answer about the asset rather than an outage.
        status = "empty" if "400" in message else "http_error"
        health.record(health.Record(
            source="defillama", endpoint=f"summary/fees/{slug}",
            dataset=f"{slug} {data_type}", status=status, error=message))
        return pl.DataFrame(schema={"date": pl.Date, "value": pl.Float64})

    frame = _series(payload)
    if frame.is_empty():
        health.record(health.Record(
            source="defillama", endpoint=f"summary/fees/{slug}",
            dataset=f"{slug} {data_type}", status="empty"))
        return frame

    store.write_fundamentals(slug, data_type, frame)
    health.record(health.Record(
        source="defillama", endpoint=f"summary/fees/{slug}",
        dataset=f"{slug} {data_type}", status="ok", rows=len(frame),
        as_of=str(frame["date"].max()), expected_lag_days=1.0,
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


def tvl(slug: str) -> pl.DataFrame:
    """Protocol TVL history, for the MC/TVL ratio (§6.4's P/B analogue)."""
    started = time.perf_counter()
    try:
        payload = http.get_json(f"{BASE}/protocol/{slug}", cache_hours=12)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="defillama", endpoint=f"protocol/{slug}",
            dataset=f"{slug} tvl", status="http_error", error=str(exc)[:200]))
        return pl.DataFrame(schema={"date": pl.Date, "value": pl.Float64})

    rows = [{
        "date": dt.datetime.fromtimestamp(int(r["date"]), dt.UTC).date(),
        "value": float(r.get("totalLiquidityUSD") or 0.0),
    } for r in (payload.get("tvl") or [])]
    if not rows:
        health.record(health.Record(
            source="defillama", endpoint=f"protocol/{slug}",
            dataset=f"{slug} tvl", status="empty"))
        return pl.DataFrame(schema={"date": pl.Date, "value": pl.Float64})

    frame = pl.DataFrame(rows).unique(subset=["date"], keep="last").sort("date")
    store.write_fundamentals(slug, "tvl", frame)
    health.record(health.Record(
        source="defillama", endpoint=f"protocol/{slug}", dataset=f"{slug} tvl",
        status="ok", rows=len(frame), as_of=str(frame["date"].max()),
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


def record_unavailable(asset: registry.Asset) -> None:
    """Note, once, that a name has no DefiLlama presence at all.

    TAO, AZTEC, DUSK and POLYX return nothing. That is grade F and it belongs
    on the source-health page as a known fact rather than as silence.
    """
    health.record(health.Record(
        source="defillama", endpoint="-", dataset=f"{asset.symbol} fundamentals",
        status="skipped",
        error="no DefiLlama protocol for this asset; revenue is unmeasurable"))


def fetch_all() -> dict[str, dict[str, int]]:
    """Fundamentals for everything that has any.

    A protocol is read from its parent slug; a CHAIN is read from the same
    `/summary/fees` path but keyed by chain name (`Ethereum`, `Solana`), which
    is a different endpoint shape wearing the same URL. Chains have fees but no
    holders' revenue, because there is no protocol treasury to distribute --
    §6.4 puts them on P/F rather than P/E for exactly that reason.
    """
    out: dict[str, dict[str, int]] = {}
    for asset in registry.tracked():
        target = asset.llama_parent or asset.llama_chain
        if not target:
            record_unavailable(asset)
            out[asset.symbol] = {}
            continue

        per_type: dict[str, int] = {}
        wanted = DATA_TYPES if asset.llama_parent else ("dailyFees", "dailyRevenue")
        for data_type in wanted:
            frame = fees(target, data_type)
            if not frame.is_empty():
                per_type[data_type] = len(frame)
        if asset.llama_parent:
            frame = tvl(asset.llama_parent)
            if not frame.is_empty():
                per_type["tvl"] = len(frame)
        out[asset.symbol] = per_type
    return out
