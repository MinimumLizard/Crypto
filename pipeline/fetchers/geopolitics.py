"""Geopolitics, policy and event markets (SPEC §6.11).

Four sources, each with a different reason for being awkward:

* **GDELT** enforces one request every five seconds and answers 429 otherwise.
  From a shared egress the five-second budget is contended, so the spacing here
  is deliberately slower than the documented minimum and the retry budget is
  larger. Ten themes need twenty calls (volume and tone are separate modes), so
  this is the slowest part of a fetch by some margin.
* **GPR** is a 3MB Excel file published by Caldara and Iacoviello, not an API.
  It is re-read whole each day because there is no incremental endpoint.
* **EPU** is a CSV whose date arrives as three separate integer columns.
* **Event markets have no odds history at all.** Polymarket and Kalshi both
  answer "what is the price now". §3 says to store our own, so every run
  snapshots the markets we track and the odds series starts at the first build.
"""

from __future__ import annotations

import io
import re
import time

import polars as pl

from pipeline import health, http, store

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
GPR_URL = "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls"
EPU_URL = "https://www.policyuncertainty.com/media/All_Daily_Policy_Data.csv"
POLYMARKET = "https://gamma-api.polymarket.com/markets"
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# §6.11's default themes. The query is what GDELT actually matches on, so it is
# kept next to the label rather than derived from it: "OPEC" as a bare word also
# matches "OPEC-like" in unrelated coverage, and quoting changes the result.
#
# Two of GDELT's query rules are not optional and it enforces both with a
# plain-text 200 rather than an error status, so a malformed query looks like a
# transport failure until the body is read:
#
#   * a QUOTED phrase must be at least five characters. '"Iran"', '"OPEC"',
#     '"SEC"' and '"CFTC"' are all rejected as "the specified phrase is too
#     short". Unquoted, the same words are fine.
#   * OR'd terms must be wrapped in parentheses, or it answers "queries
#     containing OR'd terms must be surrounded by ()".
#
# Five of these ten queries broke one of those rules and returned nothing for
# days. Each form below was run against the live API before being committed.
THEMES: list[tuple[str, str]] = [
    ("Strait of Hormuz", '"Strait of Hormuz"'),
    ("Iran", 'iran (sanctions OR nuclear OR oil)'),
    ("Red Sea / Bab al-Mandab", '("Red Sea" OR "Bab al-Mandab")'),
    ("OPEC", 'opec (production OR output OR quota)'),
    ("Sanctions", '"sanctions"'),
    ("Tariffs", '"tariffs"'),
    ("Taiwan", '"Taiwan" (China OR military OR strait)'),
    ("US midterms", '("midterm elections" OR "midterm election")'),
    ("SEC / CFTC crypto", 'crypto (regulation OR lawsuit OR enforcement)'),
    ("Stablecoin legislation", '"stablecoin" (bill OR legislation OR Congress OR law)'),
]

# Markets we want odds for. Matching is on PHRASES that were verified to exist
# in the live question text, not on single words: "house" alone matches a dozen
# sports and weather markets, "control the house" matches the midterm market and
# nothing else. §6.11 names the first four; the rest are the links in the oil
# chain the same page draws, so they belong on it.
EVENT_TOPICS: list[tuple[str, list[str]]] = [
    # The next meeting and the year's cut count are different questions, so they
    # are different topics: mixing them puts a distribution over twelve outcomes
    # into the same bucket as one binary and makes both unreadable.
    ("Fed — next meeting", ["interest rates after the", "fed increase interest",
                            "fed decrease interest", "change in fed interest"]),
    ("Fed — 2026 cuts", ["fed rate cut", "fed rate cuts"]),
    ("Hormuz reopening", ["strait of hormuz"]),
    ("US-Iran escalation", ["invade iran", "iranian regime", "strike iran",
                            "war with iran"]),
    # "d house" also matches the joint Balance of Power markets, which resolve on
    # BOTH chambers and so are neither a House nor a Senate market. They get
    # their own topic instead of contaminating one.
    ("Balance of power", ["balance of power"]),
    ("House control", ["control the house"]),
    ("Senate control", ["control the senate"]),
    ("Crypto market structure", ["clarity act", "market structure bill",
                                 "crypto market structure"]),
    ("Taiwan", ["invade taiwan", "taiwan military clash", "china x taiwan"]),
    ("Oil", ["crude oil", "wti crude", "brent"]),
    ("US recession", ["us recession", "recession by"]),
]

POLYMARKET_PAGES = 8      # 100 per page, ordered by volume: the liquid end
KALSHI_PAGES = 5          # 200 per page


# GDELT's rate limit is per source IP, so from a shared egress the documented
# one-per-five-seconds budget is contended and a run can spend minutes in
# backoff per theme without ever succeeding. A daily build must not hang on
# that, so the whole theme sweep gets a wall-clock budget and gives up cleanly.
# Themes it did not reach are recorded as skipped, and because the store
# accumulates, tomorrow's run starts with the ones that did land.
GDELT_BUDGET_SECONDS = 420.0
GDELT_RETRIES = 3


def _gdelt_timeline(query: str, mode: str) -> list[dict]:
    """One GDELT timeline. Raises so the caller writes a single health row.

    GDELT does not always signal a refusal with a status code: it will answer
    200 and a plain-text rate-limit notice. Passing that to a JSON decoder
    produced the health row "Expecting value: line 1 column 1", which tells a
    reader nothing about what actually happened. The body is checked first so
    the row names the real cause.
    """
    response = http.request(
        GDELT,
        params={"query": query, "mode": mode, "format": "json", "timespan": "6m"},
        cache_hours=6, retries=GDELT_RETRIES, timeout=60)

    body = response.text.lstrip()
    if not body.startswith("{"):
        # GDELT reports rate limits AND query syntax errors the same way, so the
        # body is the only thing that distinguishes "come back later" from "this
        # query is malformed and always will be". Both go in the health row
        # verbatim; an opaque decoder error hid four broken queries for a while.
        note = " ".join(body.split())[:140]
        raise http.FetchError(
            f"GDELT answered {response.status_code} with a non-JSON body: {note}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise http.FetchError(f"GDELT returned undecodable JSON: {exc}") from exc

    timeline = payload.get("timeline") or []
    if not timeline:
        return []
    return timeline[0].get("data") or []


def _stalest_first() -> list[tuple[str, str]]:
    """THEMES ordered by how long ago each last landed; never-fetched first."""
    try:
        stored = store.read_snapshot("gdelt_themes")
    except Exception:  # noqa: BLE001 - a missing or unreadable store is "all stale"
        return list(THEMES)
    if stored.is_empty() or "theme" not in stored.columns:
        return list(THEMES)

    freshest = (stored.group_by("theme")
                .agg(pl.col("observed_at").max().alias("seen")))
    seen = dict(zip(freshest["theme"].to_list(), freshest["seen"].to_list(),
                    strict=False))
    # "" sorts before any ISO timestamp, so a theme never fetched goes first.
    return sorted(THEMES, key=lambda item: seen.get(item[0], ""))


def gdelt_themes(budget_seconds: float = GDELT_BUDGET_SECONDS) -> int:
    """Article volume and average tone per theme, stored as a daily series.

    Volume is GDELT's percentage-of-coverage measure, not a raw count, so it is
    already normalised for how much news there was that day. That matters: a raw
    count rises every year simply because GDELT monitors more outlets.

    Themes are fetched STALEST FIRST until the budget runs out. A fixed order
    would mean the themes at the top always refresh and the ones at the bottom
    never do, which on a contended egress is the difference between ten themes
    that are each a few days old and two that are current beside eight that are
    empty forever.
    """
    written = 0
    deadline = time.monotonic() + budget_seconds
    for label, query in _stalest_first():
        if time.monotonic() >= deadline:
            health.record(health.Record(
                source="gdelt", endpoint="doc/timeline", dataset=f"theme {label}",
                status="skipped",
                error=(f"the {budget_seconds:.0f}s budget for the theme sweep was "
                       "spent before this theme; it carries forward and is "
                       "attempted first-come next run")))
            continue
        started = time.perf_counter()
        try:
            volume = _gdelt_timeline(query, "timelinevol")
            tone = _gdelt_timeline(query, "timelinetone")
        except Exception as exc:  # noqa: BLE001
            health.record(health.Record(
                source="gdelt", endpoint="doc/timeline", dataset=f"theme {label}",
                status="http_error", error=str(exc)[:200],
                latency_ms=int((time.perf_counter() - started) * 1000)))
            continue

        if not volume:
            health.record(health.Record(
                source="gdelt", endpoint="doc/timeline", dataset=f"theme {label}",
                status="empty", error="no timeline rows returned"))
            continue

        tone_by_date = {row["date"][:8]: row.get("value") for row in tone}
        rows = [{
            "theme": label,
            "date": f"{row['date'][:4]}-{row['date'][4:6]}-{row['date'][6:8]}",
            "volume_pct": float(row.get("value") or 0.0),
            "tone": (float(tone_by_date[row["date"][:8]])
                     if tone_by_date.get(row["date"][:8]) is not None else None),
        } for row in volume]

        frame = pl.DataFrame(rows)
        written += store.write_snapshot("gdelt_themes", frame, key=["theme", "date"])
        health.record(health.Record(
            source="gdelt", endpoint="doc/timeline", dataset=f"theme {label}",
            status="ok", rows=len(rows), as_of=rows[-1]["date"],
            # GDELT's last day is partial until that day closes in UTC.
            expected_lag_days=2.0,
            latency_ms=int((time.perf_counter() - started) * 1000)))
    return written


def _risk_index(name: str, url: str, parse) -> int:
    started = time.perf_counter()
    try:
        response = http.request(url, cache_hours=12, timeout=90)
        frame = parse(response.content)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="geo", endpoint=name, dataset=f"{name} daily",
            status="http_error", error=str(exc)[:200]))
        return 0

    if frame.is_empty():
        health.record(health.Record(
            source="geo", endpoint=name, dataset=f"{name} daily",
            status="empty", error="parsed to no rows"))
        return 0

    written = store.write_snapshot(f"risk_{name}", frame, key=["date", "series"])
    health.record(health.Record(
        source="geo", endpoint=name, dataset=f"{name} daily", status="ok",
        rows=frame.height, as_of=str(frame["date"].max()),
        # Both indices publish with a few days' lag and neither updates weekends.
        expected_lag_days=5.0,
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return written


# The workbook splits the index into coverage of THREATS and coverage of ACTS.
# That split is the whole point of keeping all three: a threat index rising on a
# flat acts index is a market pricing a risk, and the reverse is a market
# reacting to one that already happened.
GPR_SERIES = {"GPRD": "GPRD", "GPRD_THREAT": "GPRD_THREAT", "GPRD_ACT": "GPRD_ACT"}


def _parse_gpr(content: bytes) -> pl.DataFrame:
    """The Caldara-Iacoviello daily workbook. No CSV or API exists for it."""
    raw = pl.read_excel(io.BytesIO(content))
    available = {c.upper(): c for c in raw.columns}
    date_col = available.get("DATE")
    if date_col is None:
        return pl.DataFrame()

    frames = []
    for wanted, label in GPR_SERIES.items():
        column = available.get(wanted)
        if column is None:
            continue
        frames.append(raw.select([
            pl.col(date_col).cast(pl.Utf8).str.slice(0, 10).alias("date"),
            pl.col(column).cast(pl.Float64, strict=False).alias("value"),
            pl.lit(label).alias("series"),
        ]).drop_nulls())
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames)


def _parse_epu(content: bytes) -> pl.DataFrame:
    """policyuncertainty.com serves year, month and day as separate columns."""
    raw = pl.read_csv(io.BytesIO(content), ignore_errors=True)
    columns = {c.lower(): c for c in raw.columns}
    needed = ("year", "month", "day")
    if not all(k in columns for k in needed):
        return pl.DataFrame()
    value_col = next((columns[k] for k in columns
                      if "policy" in k or "epu" in k or "index" in k), None)
    if value_col is None:
        return pl.DataFrame()
    frame = raw.select([
        pl.col(columns["year"]).cast(pl.Int64, strict=False),
        pl.col(columns["month"]).cast(pl.Int64, strict=False),
        pl.col(columns["day"]).cast(pl.Int64, strict=False),
        pl.col(value_col).cast(pl.Float64, strict=False).alias("value"),
    ]).drop_nulls()
    frame = frame.with_columns(
        pl.format("{}-{}-{}",
                  pl.col(columns["year"]).cast(pl.Utf8),
                  pl.col(columns["month"]).cast(pl.Utf8).str.zfill(2),
                  pl.col(columns["day"]).cast(pl.Utf8).str.zfill(2)).alias("date"))
    return frame.select(["date", "value"]).with_columns(pl.lit("EPU").alias("series"))


def risk_indices() -> int:
    """GPR and EPU, the two published daily risk indices §6.11 asks for."""
    return (_risk_index("gpr", GPR_URL, _parse_gpr)
            + _risk_index("epu", EPU_URL, _parse_epu))


def _topic_for(question: str) -> str | None:
    """Match on whole words only.

    A bare substring test put "Will Brentford win the 2026-27 EPL Championship?"
    under Oil, because "brent" is a substring of "Brentford". Every phrase here
    is matched at word boundaries for that reason.
    """
    lowered = question.lower()
    for label, phrases in EVENT_TOPICS:
        for phrase in phrases:
            if re.search(rf"\b{re.escape(phrase)}\b", lowered):
                return label
    return None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _outcome_price(market: dict) -> tuple[str, float] | None:
    """Pair Gamma's outcomePrices with its outcomes.

    Both arrive as JSON-encoded strings of a list. Taking prices[0] blind would
    be right only while outcomes[0] happens to be "Yes", so the outcome name is
    carried through to the page rather than assumed.
    """
    import json

    def decode(value):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return None
        return value

    prices = decode(market.get("outcomePrices"))
    outcomes = decode(market.get("outcomes"))
    if not isinstance(prices, list) or not prices:
        return None
    price = _as_float(prices[0])
    if price is None:
        return None
    name = str(outcomes[0]) if isinstance(outcomes, list) and outcomes else "Outcome 1"
    return name, price


def _polymarket_rows() -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for page in range(POLYMARKET_PAGES):
        markets = http.get_json(
            POLYMARKET, cache_hours=1,
            params={"limit": "100", "closed": "false", "order": "volumeNum",
                    "ascending": "false", "offset": str(page * 100)})
        if not isinstance(markets, list) or not markets:
            break
        for market in markets:
            question = str(market.get("question") or "")
            topic = _topic_for(question)
            if topic is None:
                continue
            slug = str(market.get("slug") or "")
            if slug in seen:
                continue
            priced = _outcome_price(market)
            if priced is None:
                continue
            outcome, probability = priced
            seen.add(slug)
            rows.append({
                "venue": "polymarket", "topic": topic, "question": question[:180],
                "slug": slug, "outcome": outcome, "probability": probability,
                "liquidity": _as_float(market.get("liquidityNum")),
                "volume": _as_float(market.get("volumeNum")),
                "end_date": str(market.get("endDate") or "")[:10],
            })
    return rows


def _kalshi_rows() -> list[dict]:
    """Kalshi through /events, because /markets is mostly multi-leg sports.

    The open markets list is dominated by parlay legs whose title is a comma
    -joined string of selections, which no phrase match should ever hit. Events
    carry a clean human title, so topics are matched there and the event's own
    markets are then priced.
    """
    matched: list[tuple[str, str]] = []
    cursor = ""
    for _ in range(KALSHI_PAGES):
        params = {"limit": "200", "status": "open"}
        if cursor:
            params["cursor"] = cursor
        payload = http.get_json(f"{KALSHI_BASE}/events", params=params, cache_hours=1)
        events = payload.get("events") or []
        if not events:
            break
        for event in events:
            title = str(event.get("title") or "")
            topic = _topic_for(title)
            if topic is not None:
                matched.append((topic, str(event.get("event_ticker") or "")))
        cursor = payload.get("cursor") or ""
        if not cursor:
            break

    rows: list[dict] = []
    for topic, ticker in matched[:12]:    # a bounded number of follow-up calls
        if not ticker:
            continue
        payload = http.get_json(f"{KALSHI_BASE}/markets", cache_hours=1,
                                params={"event_ticker": ticker, "limit": "50"})
        for market in payload.get("markets") or []:
            # Kalshi renamed its price fields to *_dollars; last_price is gone.
            price = _as_float(market.get("last_price_dollars"))
            if price is None:
                bid = _as_float(market.get("yes_bid_dollars"))
                ask = _as_float(market.get("yes_ask_dollars"))
                price = (bid + ask) / 2 if bid is not None and ask is not None else None
            if price is None:
                continue
            rows.append({
                "venue": "kalshi", "topic": topic,
                "question": str(market.get("title") or "")[:180],
                "slug": str(market.get("ticker") or ""), "outcome": "Yes",
                "probability": price,
                "liquidity": _as_float(market.get("liquidity_dollars")),
                "volume": _as_float(market.get("volume_fp")),
                "end_date": str(market.get("close_time") or "")[:10],
            })
    return rows


def event_markets() -> int:
    """Snapshot event-market odds so a history accumulates (§3).

    Neither venue serves an odds series on the free tier, so the only way this
    page ever shows a trend is to store today's price every day. The page labels
    it as accumulating rather than drawing a line through one point.
    """
    rows: list[dict] = []
    for venue, endpoint, collect in (
            ("polymarket", "gamma/markets", _polymarket_rows),
            ("kalshi", "trade-api/events", _kalshi_rows)):
        started = time.perf_counter()
        try:
            found = collect()
        except Exception as exc:  # noqa: BLE001
            health.record(health.Record(
                source=venue, endpoint=endpoint, dataset="event odds",
                status="http_error", error=str(exc)[:200],
                latency_ms=int((time.perf_counter() - started) * 1000)))
            continue
        rows += found
        health.record(health.Record(
            source=venue, endpoint=endpoint, dataset="event odds",
            status="ok" if found else "empty",
            error="" if found else "no open market matched a tracked topic",
            rows=len(found), expected_lag_days=1.0,
            latency_ms=int((time.perf_counter() - started) * 1000)))

    if not rows:
        return 0
    return store.write_snapshot(
        "event_markets", pl.DataFrame(rows), key=["venue", "slug", "as_of"])


def fetch_all() -> dict[str, int]:
    return {
        "gdelt_themes": gdelt_themes(),
        "risk_indices": risk_indices(),
        "event_markets": event_markets(),
    }


__all__ = ["fetch_all", "gdelt_themes", "risk_indices", "event_markets",
           "THEMES", "EVENT_TOPICS"]
