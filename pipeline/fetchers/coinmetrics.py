"""CoinMetrics community API: BTC's long history, and the free on-chain set.

This is the only free source that reaches back to 2010, which the §7.2 quantile
fit requires -- Binance's own history starts in 2017.

The community tier is thinner than SPEC §5.1 assumed: 31 BTC metrics, with
realised cap, transferred value, RevUSD and NVTAdj all behind the paid tier
(D005). Most of what the cycle page needs survives because it can be DERIVED
from what is free, and `derived_metrics()` below does that explicitly rather
than leaving the arithmetic scattered through the metrics layer.

NVT does not survive. It needs transfer VALUE and the free tier serves only
transfer COUNTS, which is a different quantity. It is omitted rather than
approximated.
"""

from __future__ import annotations

import time

import polars as pl

from pipeline import health, http, store

BASE = "https://community-api.coinmetrics.io/v4"

# Verified available on the community tier, 2026-09-24.
FREE_METRICS = [
    "PriceUSD", "CapMrktCurUSD", "CapMVRVCur", "SplyCur",
    "IssTotUSD", "IssTotNtv", "FeeTotNtv", "HashRate",
    "AdrActCnt", "TxCnt", "TxTfrCnt", "ROI1yr", "ROI30d",
    "FlowInExUSD", "FlowOutExUSD", "SplyExUSD",
]

# Verified 403 on the community tier. Listed so `/source-health` can say
# "withheld by the free tier" rather than "failed", which are different facts.
MIN_ZSCORE_BARS = 730   # ~2 years, per SPEC 7.3's warm-up rule

PAYWALLED = ["CapRealUSD", "RevUSD", "TxTfrValAdjUSD", "NVTAdj", "FeeTotUSD"]


def fetch_asset(asset: str, metrics: list[str] | None = None,
                start: str = "2010-01-01") -> dict[str, pl.DataFrame]:
    """Pull metrics for one asset, walking the paging cursor to the end."""
    metrics = metrics or FREE_METRICS
    started = time.perf_counter()
    rows: list[dict] = []
    token = None
    page = 0
    try:
        while True:
            params = {
                "assets": asset, "metrics": ",".join(metrics), "frequency": "1d",
                "start_time": start, "page_size": "10000",
            }
            if token:
                params["next_page_token"] = token
            payload = http.get_json(f"{BASE}/timeseries/asset-metrics",
                                    params=params, cache_hours=12)
            rows.extend(payload.get("data", []))
            token = payload.get("next_page_token")
            page += 1
            if not token or page > 60:
                break
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="coinmetrics", endpoint="timeseries/asset-metrics",
            dataset=f"{asset} on-chain", status="http_error", error=str(exc)[:200],
            latency_ms=int((time.perf_counter() - started) * 1000)))
        return {}

    if not rows:
        health.record(health.Record(
            source="coinmetrics", endpoint="timeseries/asset-metrics",
            dataset=f"{asset} on-chain", status="empty"))
        return {}

    # CoinMetrics returns every value as a STRING, and a metric that only
    # starts part-way through history is simply absent from earlier rows. Both
    # defeat schema inference, so the frame is built as all-Utf8 and cast per
    # column afterwards.
    keys = {"time", *metrics}
    rows = [{k: (str(v) if v is not None else None) for k, v in row.items() if k in keys}
            for row in rows]
    frame = pl.DataFrame(rows, schema={k: pl.Utf8 for k in sorted(keys)},
                         infer_schema_length=None)
    frame = frame.with_columns(
        pl.col("time").str.slice(0, 10).str.to_date().alias("date"))

    out: dict[str, pl.DataFrame] = {}
    for metric in metrics:
        if metric not in frame.columns:
            continue
        series = (frame.select(["date", pl.col(metric).cast(pl.Float64, strict=False)
                                .alias("value")])
                       .drop_nulls().sort("date"))
        if series.is_empty():
            continue
        store.write_onchain(asset, metric, series)
        out[metric] = series

    health.record(health.Record(
        source="coinmetrics", endpoint="timeseries/asset-metrics",
        dataset=f"{asset} on-chain", status="ok", rows=len(frame),
        as_of=str(frame["date"].max()),
        latency_ms=int((time.perf_counter() - started) * 1000)))

    for metric in PAYWALLED:
        health.record(health.Record(
            source="coinmetrics", endpoint=f"metric/{metric}",
            dataset=f"{asset} {metric}", status="needs_key",
            error="withheld by the community tier; paid plan required"))
    return out


def price_history(asset: str = "btc") -> pl.DataFrame:
    """Daily PriceUSD, from the store, fetching if absent."""
    series = store.read_onchain(asset, "PriceUSD")
    if series.is_empty():
        fetch_asset(asset, ["PriceUSD"])
        series = store.read_onchain(asset, "PriceUSD")
    return series


def derived_metrics(asset: str = "btc") -> dict[str, pl.DataFrame]:
    """The metrics the paid tier withholds, rebuilt from free ones (D005).

        realised cap   = market cap / MVRV
        realised price = realised cap / circulating supply
        NUPL           = 1 - 1/MVRV
        miner revenue  = issuance USD + fees (native) * price
        thermocap      = cumulative miner revenue
        Puell multiple = issuance USD / its own trailing 365d mean

    Each is exact given its inputs, not an approximation -- MVRV is by
    definition market cap over realised cap, so dividing back out recovers the
    realised cap that CoinMetrics itself would have served.
    """
    def read(metric: str) -> pl.DataFrame:
        return store.read_onchain(asset, metric).rename({"value": metric})

    frame = read("CapMrktCurUSD")
    for metric in ("CapMVRVCur", "SplyCur", "IssTotUSD", "FeeTotNtv", "PriceUSD"):
        other = read(metric)
        if other.is_empty():
            continue
        frame = frame.join(other, on="date", how="inner")
    if frame.is_empty() or "CapMVRVCur" not in frame.columns:
        return {}

    frame = frame.sort("date").with_columns(
        (pl.col("CapMrktCurUSD") / pl.col("CapMVRVCur")).alias("RealisedCapUSD"),
        (1 - 1 / pl.col("CapMVRVCur")).alias("NUPL"),
    )
    if "SplyCur" in frame.columns:
        frame = frame.with_columns(
            (pl.col("RealisedCapUSD") / pl.col("SplyCur")).alias("RealisedPriceUSD"))
    if {"IssTotUSD", "FeeTotNtv", "PriceUSD"} <= set(frame.columns):
        frame = frame.with_columns(
            (pl.col("IssTotUSD") + pl.col("FeeTotNtv") * pl.col("PriceUSD"))
            .alias("MinerRevenueUSD"))
        frame = frame.with_columns(
            pl.col("MinerRevenueUSD").cum_sum().alias("ThermocapUSD"),
            (pl.col("CapMrktCurUSD") / pl.col("MinerRevenueUSD").cum_sum())
            .alias("MarketCapToThermocap"))
    if "IssTotUSD" in frame.columns:
        frame = frame.with_columns(
            (pl.col("IssTotUSD")
             / pl.col("IssTotUSD").rolling_mean(window_size=365, min_periods=365))
            .alias("PuellMultiple"))

    derived = ["RealisedCapUSD", "NUPL", "RealisedPriceUSD", "MinerRevenueUSD",
               "ThermocapUSD", "MarketCapToThermocap", "PuellMultiple"]
    out: dict[str, pl.DataFrame] = {}
    for name in derived:
        if name not in frame.columns:
            continue
        series = frame.select(["date", pl.col(name).alias("value")]).drop_nulls()
        if series.is_empty():
            continue
        store.write_onchain(asset, name, series)
        out[name] = series
    return out


def mvrv_zscore(asset: str = "btc") -> pl.DataFrame:
    """MVRV Z-Score: (market cap - realised cap) / stdev(market cap), expanding.

    The window is EXPANDING, not full-sample: a z-score computed over all of
    history would have known the future at every past date, and §2.3 forbids
    that. It also means early values are noisy, which the warm-up flag marks.
    """
    cap = store.read_onchain(asset, "CapMrktCurUSD").rename({"value": "cap"})
    realised = store.read_onchain(asset, "RealisedCapUSD").rename({"value": "realised"})
    if cap.is_empty() or realised.is_empty():
        return pl.DataFrame(schema={"date": pl.Date, "value": pl.Float64})
    frame = cap.join(realised, on="date", how="inner").sort("date")

    # Expanding standard deviation, computed by hand because polars has no
    # cum_std. Expanding rather than full-sample: a z-score divided by the
    # standard deviation of ALL history would have known the future at every
    # past date, which §2.3 forbids and which is exactly how a cycle indicator
    # ends up looking prescient in a backtest.
    import numpy as np
    caps = frame["cap"].to_numpy()
    counts = np.arange(1, len(caps) + 1)
    means = np.cumsum(caps) / counts
    mean_squares = np.cumsum(caps ** 2) / counts
    variance = np.maximum(mean_squares - means ** 2, 0.0)
    sd = np.sqrt(variance * counts / np.maximum(counts - 1, 1))
    sd[sd == 0] = np.nan

    frame = frame.with_columns(pl.Series("cap_sd", sd))
    frame = frame.with_columns(
        ((pl.col("cap") - pl.col("realised")) / pl.col("cap_sd")).alias("value"))
    # MIN_ZSCORE_BARS of warm-up: a z-score over a handful of observations is
    # noise, and §7.3 asks for 2-4 years before a reading counts as signal.
    out = frame.select(["date", "value"]).drop_nulls().slice(MIN_ZSCORE_BARS)
    store.write_onchain(asset, "MVRVZScore", out)
    return out
