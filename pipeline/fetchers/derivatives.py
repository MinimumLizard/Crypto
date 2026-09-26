"""Perp funding and open interest (Hyperliquid), volatility and basis (Deribit).

**Funding intervals are not the same across venues and this is the trap.**
Hyperliquid funds hourly; Binance and Bybit fund on 4- or 8-hour schedules.
A rate of 0.0001 means 8.76%/yr on Hyperliquid and 1.10%/yr on an 8-hour venue,
so annualising every venue with one constant is wrong by up to 8x and wrong in
the direction that makes Hyperliquid look cheap. `predictedFundings` returns
each venue's own `fundingIntervalHours` and it is carried through to the metric.

That call is also the whole answer to the geoblock: Binance and Bybit refuse US
addresses, which is every GitHub runner, but Hyperliquid reports their rates.

**Open interest has no free history.** `metaAndAssetCtxs` gives the current
level and nothing else, so OI change and OI z-scores need our own snapshots and
start accumulating from the first run. That is stated on the page rather than
back-filled from anything.
"""

from __future__ import annotations

import datetime as dt
import re
import time

import polars as pl

from pipeline import health, http, registry, store

HL = "https://api.hyperliquid.xyz/info"
DERIBIT = "https://www.deribit.com/api/v2/public"

# HL returns at most 500 hourly rows per call, about 21 days.
HL_FUNDING_PAGE_HOURS = 500
FUNDING_HISTORY_DAYS = 120       # enough for a 90-day z-score with warm-up


def perp_contexts() -> pl.DataFrame:
    """Current funding, OI, mark, premium and day volume for every HL perp."""
    started = time.perf_counter()
    try:
        payload = http.post_json(HL, {"type": "metaAndAssetCtxs"}, cache_hours=0.5)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="hyperliquid", endpoint="metaAndAssetCtxs",
            dataset="perp contexts", status="http_error", error=str(exc)[:200]))
        return pl.DataFrame()

    def number(ctx: dict, key: str) -> float | None:
        value = ctx.get(key)
        return float(value) if value not in (None, "") else None

    universe, contexts = payload[0]["universe"], payload[1]
    rows = []
    for meta, ctx in zip(universe, contexts, strict=False):
        if not ctx:
            continue
        rows.append({
            "coin": meta["name"],
            "funding_hourly": number(ctx, "funding"),
            "open_interest": number(ctx, "openInterest"),
            "mark": number(ctx, "markPx"),
            "oracle": number(ctx, "oraclePx"),
            "premium": number(ctx, "premium"),
            "day_volume_usd": number(ctx, "dayNtlVlm"),
            "prev_day_px": number(ctx, "prevDayPx"),
            "max_leverage": meta.get("maxLeverage"),
        })
    frame = pl.DataFrame(rows, infer_schema_length=None)
    # OI in contracts; value it at the mark so it is comparable across coins.
    frame = frame.with_columns(
        (pl.col("open_interest") * pl.col("mark")).alias("open_interest_usd"))

    store.write_snapshot("perp_contexts", frame, ["as_of", "coin"])
    health.record(health.Record(
        source="hyperliquid", endpoint="metaAndAssetCtxs", dataset="perp contexts",
        status="ok", rows=len(frame), as_of=dt.date.today().isoformat(),
        expected_lag_days=0.1,
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


def predicted_fundings() -> pl.DataFrame:
    """Funding on Hyperliquid, Binance and Bybit, with each venue's interval.

    The interval is the point. Without it a cross-venue funding table compares
    quantities that are not in the same units.
    """
    started = time.perf_counter()
    try:
        payload = http.post_json(HL, {"type": "predictedFundings"}, cache_hours=0.5)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="hyperliquid", endpoint="predictedFundings",
            dataset="cross-venue funding", status="http_error", error=str(exc)[:200]))
        return pl.DataFrame()

    rows = []
    for coin, venues in payload:
        for venue, info in venues:
            if not info:
                continue
            rate = info.get("fundingRate")
            rows.append({
                "coin": coin,
                "venue": venue,
                "funding_rate": float(rate) if rate not in (None, "") else None,
                "interval_hours": float(info.get("fundingIntervalHours") or 8),
                "next_funding_ms": info.get("nextFundingTime"),
            })
    frame = pl.DataFrame(rows, infer_schema_length=None)
    store.write_snapshot("funding_by_venue", frame, ["as_of", "coin", "venue"])
    health.record(health.Record(
        source="hyperliquid", endpoint="predictedFundings",
        dataset="cross-venue funding", status="ok", rows=len(frame),
        as_of=dt.date.today().isoformat(), expected_lag_days=0.1,
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


def funding_history(coin: str, days: int = FUNDING_HISTORY_DAYS) -> pl.DataFrame:
    """Hourly funding for one coin, paged back `days`.

    500 rows a call, so a 120-day window is six calls. Stored as an hourly
    series because the z-score in §6.9 is against funding's own distribution,
    and collapsing to daily first would throw away most of it.
    """
    started = time.perf_counter()
    end = dt.datetime.now(dt.UTC)
    cursor = end - dt.timedelta(days=days)
    rows: list[dict] = []
    try:
        for _ in range(12):
            batch = http.post_json(HL, {
                "type": "fundingHistory", "coin": coin,
                "startTime": int(cursor.timestamp() * 1000),
            }, cache_hours=6)
            if not batch:
                break
            rows.extend(batch)
            last = batch[-1]["time"]
            if len(batch) < HL_FUNDING_PAGE_HOURS:
                break
            cursor = dt.datetime.fromtimestamp(last / 1000, dt.UTC) + dt.timedelta(seconds=1)
            if cursor >= end:
                break
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="hyperliquid", endpoint="fundingHistory",
            dataset=f"{coin} funding history", status="http_error",
            error=str(exc)[:200]))
        return pl.DataFrame()

    if not rows:
        health.record(health.Record(
            source="hyperliquid", endpoint="fundingHistory",
            dataset=f"{coin} funding history", status="empty"))
        return pl.DataFrame()

    frame = pl.DataFrame([{
        "ts": dt.datetime.fromtimestamp(r["time"] / 1000, dt.UTC),
        "value": float(r["fundingRate"]),
    } for r in rows]).unique(subset=["ts"], keep="last").sort("ts")

    store.write_onchain(f"funding_{coin}", "HourlyRate",
                        frame.select([pl.col("ts").dt.date().alias("date"),
                                      pl.col("value")]).group_by("date")
                             .agg(pl.col("value").mean()).sort("date"))
    store.write_snapshot(f"funding_hourly_{coin}", frame.rename({"ts": "observed_at"}),
                         ["observed_at"])

    health.record(health.Record(
        source="hyperliquid", endpoint="fundingHistory",
        dataset=f"{coin} funding history", status="ok", rows=len(frame),
        as_of=str(frame["ts"].max().date()), expected_lag_days=0.1,
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


def order_book_depth(coin: str) -> dict | None:
    """Depth at +/-1% and +/-2%, and the dollars to move price 1% (§6.3).

    Depth is a point-in-time fact with no history anywhere, so it is snapshotted
    like supply: what we do not store today we cannot ask about later.
    """
    try:
        payload = http.post_json(HL, {"type": "l2Book", "coin": coin}, cache_hours=0.25)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="hyperliquid", endpoint="l2Book", dataset=f"{coin} depth",
            status="http_error", error=str(exc)[:200]))
        return None

    levels = payload.get("levels") or []
    if len(levels) < 2 or not levels[0] or not levels[1]:
        return None
    bids, asks = levels[0], levels[1]
    best_bid, best_ask = float(bids[0]["px"]), float(asks[0]["px"])
    mid = (best_bid + best_ask) / 2

    def side_depth(side, within, is_bid):
        limit = mid * (1 - within) if is_bid else mid * (1 + within)
        total = 0.0
        for level in side:
            price, size = float(level["px"]), float(level["sz"])
            if (is_bid and price < limit) or (not is_bid and price > limit):
                break
            total += price * size
        return total

    out = {
        "coin": coin, "mid": mid,
        "spread_bps": (best_ask - best_bid) / mid * 10_000,
        "bid_1pct": side_depth(bids, 0.01, True),
        "ask_1pct": side_depth(asks, 0.01, False),
        "bid_2pct": side_depth(bids, 0.02, True),
        "ask_2pct": side_depth(asks, 0.02, False),
    }
    store.write_snapshot("depth", pl.DataFrame([out]), ["as_of", "coin"])
    health.record(health.Record(
        source="hyperliquid", endpoint="l2Book", dataset=f"{coin} depth",
        status="ok", rows=1, as_of=dt.date.today().isoformat(),
        expected_lag_days=0.1))
    return out


# ---------------------------------------------------------------------------
# Deribit: implied volatility, term structure, basis
# ---------------------------------------------------------------------------

def dvol(currency: str = "BTC", days: int = 540) -> pl.DataFrame:
    """The Deribit implied-volatility index, daily."""
    started = time.perf_counter()
    end = dt.datetime.now(dt.UTC)
    start = end - dt.timedelta(days=days)
    try:
        payload = http.get_json(f"{DERIBIT}/get_volatility_index_data", params={
            "currency": currency,
            "start_timestamp": str(int(start.timestamp() * 1000)),
            "end_timestamp": str(int(end.timestamp() * 1000)),
            "resolution": "43200",
        }, cache_hours=6)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="deribit", endpoint="volatility_index",
            dataset=f"{currency} DVOL", status="http_error", error=str(exc)[:200]))
        return pl.DataFrame()

    rows = payload.get("result", {}).get("data") or []
    if not rows:
        health.record(health.Record(
            source="deribit", endpoint="volatility_index",
            dataset=f"{currency} DVOL", status="empty"))
        return pl.DataFrame()

    frame = pl.DataFrame([{
        "date": dt.datetime.fromtimestamp(r[0] / 1000, dt.UTC).date(),
        "value": float(r[4]),
    } for r in rows]).group_by("date").agg(pl.col("value").last()).sort("date")

    store.write_onchain(f"deribit_{currency.lower()}", "DVOL", frame)
    health.record(health.Record(
        source="deribit", endpoint="volatility_index", dataset=f"{currency} DVOL",
        status="ok", rows=len(frame), as_of=str(frame["date"].max()),
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return frame


EXPIRY = re.compile(r"^[A-Z]+-(\d{1,2}[A-Z]{3}\d{2})(?:-(\d+)-([CP]))?$")
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def _parse_expiry(name: str) -> dt.date | None:
    match = EXPIRY.match(name)
    if not match:
        return None
    token = match.group(1)
    try:
        day = int(token[:-5])
        return dt.date(2000 + int(token[-2:]), MONTHS[token[-5:-2]], day)
    except Exception:  # noqa: BLE001
        return None


def futures_basis(currency: str = "BTC") -> list[dict]:
    """Annualised basis per dated future: (future/spot - 1) scaled to a year."""
    try:
        payload = http.get_json(f"{DERIBIT}/get_book_summary_by_currency", params={
            "currency": currency, "kind": "future"}, cache_hours=1)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="deribit", endpoint="book_summary/future",
            dataset=f"{currency} basis", status="http_error", error=str(exc)[:200]))
        return []

    rows = payload.get("result") or []
    spot = None
    for row in rows:
        if row["instrument_name"].endswith("PERPETUAL"):
            spot = row.get("mark_price") or row.get("last")
    if not spot:
        prices = [r.get("mark_price") for r in rows if r.get("mark_price")]
        spot = min(prices) if prices else None
    if not spot:
        return []

    today = dt.date.today()
    out = []
    for row in rows:
        name = row["instrument_name"]
        if name.endswith("PERPETUAL"):
            continue
        expiry = _parse_expiry(name)
        mark = row.get("mark_price")
        if not expiry or not mark:
            continue
        days = (expiry - today).days
        if days <= 0:
            continue
        simple = mark / spot - 1
        out.append({
            "instrument": name, "expiry": expiry.isoformat(), "days": days,
            "mark": mark, "basis_pct": simple * 100,
            "annualised_pct": simple * (365 / days) * 100,
            "open_interest": row.get("open_interest"),
        })
    out.sort(key=lambda r: r["days"])
    health.record(health.Record(
        source="deribit", endpoint="book_summary/future", dataset=f"{currency} basis",
        status="ok", rows=len(out), as_of=today.isoformat(), expected_lag_days=0.1))
    return out


def options_summary(currency: str = "BTC") -> dict:
    """Put/call open interest and the IV term structure.

    25-delta skew is NOT computed: Deribit's summary carries mark IV but no
    greeks, and inferring delta from strike and IV without the forward and rate
    would be a worse number wearing a precise name. §6.9 marks it optional and
    it is left out rather than approximated.
    """
    try:
        payload = http.get_json(f"{DERIBIT}/get_book_summary_by_currency", params={
            "currency": currency, "kind": "option"}, cache_hours=1)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="deribit", endpoint="book_summary/option",
            dataset=f"{currency} options", status="http_error", error=str(exc)[:200]))
        return {}

    rows = payload.get("result") or []
    calls = puts = 0.0
    by_expiry: dict[str, dict] = {}
    underlying = None
    for row in rows:
        name = row["instrument_name"]
        match = EXPIRY.match(name)
        if not match or not match.group(3):
            continue
        oi = float(row.get("open_interest") or 0.0)
        if match.group(3) == "C":
            calls += oi
        else:
            puts += oi
        underlying = row.get("underlying_price") or underlying
        expiry = _parse_expiry(name)
        iv = row.get("mark_iv")
        if not expiry or not iv:
            continue
        bucket = by_expiry.setdefault(expiry.isoformat(), {"ivs": [], "oi": 0.0})
        bucket["ivs"].append(float(iv))
        bucket["oi"] += oi

    today = dt.date.today()
    term = []
    for expiry, bucket in sorted(by_expiry.items()):
        if not bucket["ivs"]:
            continue
        days = (dt.date.fromisoformat(expiry) - today).days
        if days <= 0:
            continue
        # Median across strikes at the expiry: a mean is dragged by deep wings
        # whose quotes are wide and barely traded.
        ivs = sorted(bucket["ivs"])
        term.append({"expiry": expiry, "days": days,
                     "iv": ivs[len(ivs) // 2], "open_interest": bucket["oi"]})

    out = {
        "currency": currency,
        "call_oi": calls, "put_oi": puts,
        "put_call_ratio": (puts / calls) if calls else None,
        "underlying": underlying,
        "term_structure": term[:12],
        "instruments": len(rows),
        "skew_note": ("25-delta skew is not shown: Deribit's summary has mark IV "
                      "but no greeks, and inferring delta without the forward "
                      "would be a worse number with a precise-sounding name."),
    }
    health.record(health.Record(
        source="deribit", endpoint="book_summary/option", dataset=f"{currency} options",
        status="ok", rows=len(rows), as_of=today.isoformat(), expected_lag_days=0.1))
    return out


def fetch_all() -> dict:
    """Everything the derivatives page needs."""
    out: dict = {}
    contexts = perp_contexts()
    out["perp_contexts"] = len(contexts)
    out["funding_by_venue"] = len(predicted_fundings())

    # Funding history only for names we track that actually have an HL perp.
    tracked = {a.hl_perp for a in registry.tracked() if a.hl_perp}
    got = 0
    for coin in sorted(tracked):
        if not funding_history(coin).is_empty():
            got += 1
    out["funding_history"] = got

    for coin in sorted(tracked):
        order_book_depth(coin)
    out["depth"] = len(tracked)

    for currency in ("BTC", "ETH"):
        out[f"dvol_{currency}"] = len(dvol(currency))
    return out
