"""Daily and hourly bars for every tracked asset.

Venue choice is not a preference, it is a parity requirement. MiniLizard (§7.1)
is DEFINED on Binance spot bars because that is what the TradingView charts it
must match are drawn from, so Binance is used wherever a pair exists. Three book
names have no Binance pair at all -- FLUID, GEOD and AZTEC, plus XMR on the
watchlist -- and fall back to Coinbase or Hyperliquid. Their scores are real but
they are NOT comparable to a TradingView reading, and the registry marks them
`parity: substitute` so the asset page can say so (D006).

`data-api.binance.vision` is used rather than `api.binance.com`, which returns
451 from US addresses and therefore from every GitHub-hosted runner. The
market-data-only host serves the same klines without the geoblock.
"""

from __future__ import annotations

import datetime as dt
import time

import polars as pl

from pipeline import health, http, registry, store

BINANCE = "https://data-api.binance.vision/api/v3/klines"
COINBASE = "https://api.exchange.coinbase.com/products"
HYPERLIQUID = "https://api.hyperliquid.xyz/info"

BINANCE_MAX = 1000       # rows per call, verified
COINBASE_MAX = 300       # rows per call, verified


def _empty() -> pl.DataFrame:
    return pl.DataFrame(schema={
        "date": pl.Date, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
        "close": pl.Float64, "volume": pl.Float64, "interval": pl.Utf8,
        "source": pl.Utf8})


def _binance(symbol: str, interval: str, start: dt.date) -> pl.DataFrame:
    """Walk Binance klines forward from `start`, 1000 bars at a time."""
    rows: list[dict] = []
    cursor = int(dt.datetime.combine(start, dt.time()).replace(
        tzinfo=dt.UTC).timestamp() * 1000)
    for _ in range(200):
        batch = http.get_json(BINANCE, params={
            "symbol": symbol, "interval": interval,
            "startTime": str(cursor), "limit": str(BINANCE_MAX)}, cache_hours=6)
        if not batch:
            break
        for bar in batch:
            rows.append({
                "date": dt.datetime.fromtimestamp(bar[0] / 1000, dt.UTC),
                "open": float(bar[1]), "high": float(bar[2]), "low": float(bar[3]),
                "close": float(bar[4]), "volume": float(bar[5]),
            })
        if len(batch) < BINANCE_MAX:
            break
        cursor = batch[-1][0] + 1
    if not rows:
        return _empty()
    frame = pl.DataFrame(rows)
    return frame.with_columns(
        pl.col("date").dt.date().alias("date") if interval == "1d"
        else pl.col("date").alias("date"))


def _coinbase(product: str, granularity: int, start: dt.date) -> pl.DataFrame:
    """Coinbase candles. Only 300 bars per call, and returned newest-first."""
    rows: list[dict] = []
    window = dt.timedelta(seconds=granularity * COINBASE_MAX)
    cursor = dt.datetime.combine(start, dt.time(), tzinfo=dt.UTC)
    now = dt.datetime.now(dt.UTC)
    for _ in range(200):
        if cursor >= now:
            break
        end = min(cursor + window, now)
        batch = http.get_json(f"{COINBASE}/{product}/candles", params={
            "granularity": str(granularity),
            "start": cursor.isoformat(), "end": end.isoformat()}, cache_hours=6)
        for bar in batch or []:
            rows.append({
                "date": dt.datetime.fromtimestamp(bar[0], dt.UTC),
                "low": float(bar[1]), "high": float(bar[2]),
                "open": float(bar[3]), "close": float(bar[4]), "volume": float(bar[5]),
            })
        cursor = end
    if not rows:
        return _empty()
    return pl.DataFrame(rows)


def _hyperliquid(coin: str, interval: str, start: dt.date) -> pl.DataFrame:
    """Hyperliquid candleSnapshot; one call covers a long window."""
    start_ms = int(dt.datetime.combine(start, dt.time(), tzinfo=dt.UTC).timestamp() * 1000)
    end_ms = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    batch = http.post_json(HYPERLIQUID, {
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval,
                "startTime": start_ms, "endTime": end_ms}}, cache_hours=6)
    if not batch:
        return _empty()
    rows = [{
        "date": dt.datetime.fromtimestamp(bar["t"] / 1000, dt.UTC),
        "open": float(bar["o"]), "high": float(bar["h"]), "low": float(bar["l"]),
        "close": float(bar["c"]), "volume": float(bar["v"]),
    } for bar in batch]
    return pl.DataFrame(rows)


def venues_for(asset: registry.Asset) -> list[tuple[str, str]]:
    """Every venue we can get this asset's bars from, parity venue first.

    All of them are fetched, not just the parity one, because Binance's listing
    dates leave several book names with less history than the regime engine
    needs to warm up (see store.py). Which series a panel uses is decided when
    it is drawn, not here.
    """
    out: list[tuple[str, str]] = []
    if asset.binance_spot:
        out.append(("binance", asset.binance_spot))
    if asset.coinbase:
        out.append(("coinbase", asset.coinbase))
    if asset.hl_perp:
        out.append(("hyperliquid", asset.hl_perp))
    return out


def fetch(asset: registry.Asset, interval: str = "1d",
          start: dt.date = dt.date(2017, 1, 1),
          venue_override: tuple[str, str] | None = None) -> pl.DataFrame:
    """Bars for one asset from one venue.

    Returns an empty frame and writes a health row on failure. It never raises:
    one dead source must not take the build with it (§2.8).
    """
    venue, symbol = venue_override or asset.price_source
    if venue == "none":
        health.record(health.Record(
            source="prices", endpoint="-", dataset=f"{asset.symbol} {interval}",
            status="skipped", error="no venue in the registry"))
        return _empty()

    started = time.perf_counter()
    try:
        if venue == "binance":
            frame = _binance(symbol, interval, start)
        elif venue == "coinbase":
            frame = _coinbase(symbol, 86400 if interval == "1d" else 3600, start)
        else:
            frame = _hyperliquid(symbol, interval, start)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source=venue, endpoint=symbol, dataset=f"{asset.symbol} {interval}",
            status="http_error", error=str(exc)[:200],
            latency_ms=int((time.perf_counter() - started) * 1000)))
        return _empty()

    if frame.is_empty():
        health.record(health.Record(
            source=venue, endpoint=symbol, dataset=f"{asset.symbol} {interval}",
            status="empty"))
        return frame

    frame = frame.with_columns(
        pl.lit(interval).alias("interval"), pl.lit(venue).alias("source"))
    frame = frame.with_columns(pl.col("date").cast(pl.Date))
    if interval == "1d":
        # The newest daily bar is still open. §7.1 scores confirmed bars only,
        # so it is dropped here rather than downstream, where forgetting would
        # quietly put a live bar into the history.
        today = dt.datetime.now(dt.UTC).date()
        frame = frame.filter(pl.col("date") < today)
    if frame.is_empty():
        health.record(health.Record(
            source=venue, endpoint=symbol, dataset=f"{asset.symbol} {interval}",
            status="empty", error="no confirmed bars yet at this venue"))
        return frame
    store.write_ohlcv(asset.symbol, venue, frame)

    health.record(health.Record(
        source=venue, endpoint=symbol, dataset=f"{asset.symbol} {interval}",
        status="ok", rows=len(frame), as_of=str(frame["date"].max()),
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


def fetch_all(interval: str = "1d", start: dt.date = dt.date(2017, 1, 1),
              symbols: list[str] | None = None) -> dict[str, dict[str, int]]:
    """Bars for every tracked asset, from every venue that has them.

    Returns symbol -> {venue: rows}. A symbol with an empty dict has no bars
    anywhere, which is a real answer and is rendered as such.
    """
    out: dict[str, dict[str, int]] = {}
    for asset in registry.tracked():
        if symbols and asset.symbol not in symbols:
            continue
        per_venue: dict[str, int] = {}
        for venue, symbol in venues_for(asset):
            frame = fetch(asset, interval, start, venue_override=(venue, symbol))
            if not frame.is_empty():
                per_venue[venue] = len(frame)
        out[asset.symbol] = per_venue
    return out


def backfill_btc_from_coinmetrics() -> int:
    """Splice CoinMetrics' 2010-2017 closes onto the Binance history.

    Binance starts in 2017. The cycle page needs 2010, and CoinMetrics serves a
    close-only daily series back to 2010-07-18. Those early rows have NO open,
    high, low or volume, so they are stored with nulls rather than with the
    close repeated into every field -- a synthetic OHLC bar would silently feed
    the regime engine and the ATR with fabricated ranges.

    Marked `source = coinmetrics-close-only` so anything that needs a real bar
    can exclude them.
    """
    series = store.read_onchain("btc", "PriceUSD")
    if series.is_empty():
        return 0
    existing = store.read_ohlcv("BTC", "binance")
    earliest = existing["date"].min() if not existing.is_empty() else dt.date(2100, 1, 1)
    early = series.filter(pl.col("date") < earliest)
    if early.is_empty():
        return 0
    frame = early.select([
        pl.col("date"),
        pl.lit(None, dtype=pl.Float64).alias("open"),
        pl.lit(None, dtype=pl.Float64).alias("high"),
        pl.lit(None, dtype=pl.Float64).alias("low"),
        pl.col("value").alias("close"),
        pl.lit(None, dtype=pl.Float64).alias("volume"),
        pl.lit("1d").alias("interval"),
        pl.lit("coinmetrics-close-only").alias("source"),
    ])
    store.write_ohlcv("BTC", "coinmetrics", frame)
    health.record(health.Record(
        source="coinmetrics", endpoint="PriceUSD", dataset="BTC pre-2017 closes",
        status="ok", rows=len(frame), as_of=str(frame["date"].max()),
        # A one-off historical import. Its newest row is from 2017 by design,
        # so measuring it against today would report a permanent fault.
        archival=True))
    return len(frame)
