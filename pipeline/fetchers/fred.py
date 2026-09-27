"""FRED macro series (SPEC §6.10, and the oil chain on §6.11).

Two things here are not boilerplate.

**Vintages.** FRED revises. CPI, payrolls and the H.4.1 balance-sheet series are
all restated after first publication, so "what does the series say today" and
"what was knowable on the day" are different questions, and only the second one
is legitimate input to anything backward-looking. `store.write_macro` records the
date we learned a value alongside the date it describes, which is what makes a
point-in-time read possible later (PLAN §5.3). This fetcher does not yet pull
ALFRED's full vintage history — it stamps each observation with the date this
run saw it, so vintages accumulate forward from the first build.

**A missing key is not a failure.** Without `FRED_API_KEY` every series records
`needs_key` and returns nothing. It never substitutes another source silently:
the dollar index on FRED is a 26-currency trade-weighted index and ICE's DXY is
six currencies and mostly euro, so quietly swapping one for the other would put
a different number under the same label (D021's lesson, from a different angle).
"""

from __future__ import annotations

import datetime as dt
import os
import time

import polars as pl

from pipeline import health, http, store

BASE = "https://api.stlouisfed.org/fred"

# series id -> (human label, why it is here). The "why" is not decoration: a
# series nobody can justify is a series nobody will notice has gone stale.
SERIES: dict[str, tuple[str, str]] = {
    # Fed balance sheet -> net liquidity = WALCL - WTREGEN - RRPONTSYD.
    # UNIT TRAP (§6.10): WALCL and WTREGEN are in MILLIONS, RRPONTSYD is in
    # BILLIONS. Subtracting them raw is off by a factor of a thousand.
    "WALCL": ("Fed total assets", "net liquidity"),
    "WTREGEN": ("Treasury General Account", "net liquidity"),
    "RRPONTSYD": ("Overnight reverse repo", "net liquidity"),
    # Rates and the curve.
    "DFF": ("Effective fed funds rate", "policy rate; the oil chain's Fed link"),
    "SOFR": ("SOFR", "secured overnight funding"),
    "DGS2": ("2-year Treasury", "curve"),
    "DGS10": ("10-year Treasury", "curve"),
    "DFII10": ("10-year TIPS real yield", "the oil chain's real-yield link"),
    "T10YIE": ("10-year breakeven inflation", "inflation expectations"),
    "T10Y2Y": ("10y minus 2y", "curve shape"),
    "T10Y3M": ("10y minus 3m", "curve shape; recession signal"),
    # Dollar and credit.
    "DTWEXBGS": ("Broad dollar index", "the oil chain's dollar link"),
    "BAMLH0A0HYM2": ("High-yield OAS", "credit stress"),
    "NFCI": ("Chicago Fed financial conditions", "composite conditions"),
    "VIXCLS": ("VIX", "equity volatility"),
    # Commodities.
    "DCOILBRENTEU": ("Brent crude, spot", "the oil chain's first link"),
    "DCOILWTICO": ("WTI crude, spot", "oil cross-check"),
    # Money and inflation.
    "M2SL": ("M2 money stock", "monetary aggregate"),
    "CPIAUCSL": ("CPI, all items", "the oil chain's inflation link"),
    "CPILFESL": ("Core CPI", "inflation ex food and energy"),
    "PCEPILFE": ("Core PCE", "the Fed's preferred measure"),
    # Labour.
    "UNRATE": ("Unemployment rate", "labour"),
    "SAHMREALTIME": ("Sahm rule, real time", "recession trigger"),
    "ICSA": ("Initial claims", "weekly labour"),
    "CCSA": ("Continuing claims", "weekly labour"),
    "PAYEMS": ("Nonfarm payrolls", "labour"),
    "JTSLDL": ("Job openings", "labour demand"),
    # Housing and other.
    "UNDCONTSA": ("Housing units under construction", "business cycle"),
    "MEDLISPRIUS": ("Median listing price", "housing"),
    "USEPUINDXD": ("Economic policy uncertainty, daily", "cross-check on EPU"),
    "SP500": ("S&P 500", "risk benchmark"),
    "DEXUSAL": ("USD/AUD", "the owner reports in AUD"),
}

# Series the oil chain on /geopolitics needs, so a partial fetch can say whether
# the chain will fill rather than leaving the page to work it out.
OIL_CHAIN_SERIES = ("DCOILBRENTEU", "CPIAUCSL", "DFF", "DFII10", "DTWEXBGS")

OBSERVATION_START = "1990-01-01"


def _key() -> str | None:
    key = os.environ.get("FRED_API_KEY", "").strip()
    return key or None


def _parse(payload: dict) -> pl.DataFrame:
    """FRED writes a missing observation as "." — not null, not zero."""
    rows = []
    for observation in payload.get("observations") or []:
        raw = observation.get("value")
        if raw in (None, "", "."):
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        try:
            obs_date = dt.date.fromisoformat(observation["date"])
        except (KeyError, ValueError):
            continue
        rows.append({"obs_date": obs_date, "value": value})
    if not rows:
        return pl.DataFrame(schema={"obs_date": pl.Date, "value": pl.Float64})
    return pl.DataFrame(rows).unique(subset=["obs_date"], keep="last").sort("obs_date")


def fetch_series(series_id: str, label: str) -> int:
    key = _key()
    if key is None:
        health.record(health.Record(
            source="fred", endpoint=series_id, dataset=label,
            status="needs_key",
            error="FRED_API_KEY is not set; no value is substituted from "
                  "another source"))
        return 0

    started = time.perf_counter()
    try:
        payload = http.get_json(
            f"{BASE}/series/observations",
            params={"series_id": series_id, "api_key": key, "file_type": "json",
                    "observation_start": OBSERVATION_START},
            cache_hours=6, timeout=60)
        frame = _parse(payload)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="fred", endpoint=series_id, dataset=label,
            status="http_error", error=str(exc)[:200],
            latency_ms=int((time.perf_counter() - started) * 1000)))
        return 0

    if frame.is_empty():
        health.record(health.Record(
            source="fred", endpoint=series_id, dataset=label,
            status="empty", error="no usable observations returned"))
        return 0

    written = store.write_macro(series_id, frame)
    health.record(health.Record(
        source="fred", endpoint=series_id, dataset=label, status="ok",
        rows=frame.height, as_of=str(frame["obs_date"].max()),
        # FRED series publish on wildly different cadences. Weekly and monthly
        # series are not stale at 3 days old, and flagging them as such trains
        # the eye to ignore the badge.
        expected_lag_days=_expected_lag(series_id),
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return written


# Monthly series are published with a lag measured in weeks; weekly ones in
# days. These are the publication lags, not the observation frequencies.
_MONTHLY = {"CPIAUCSL", "CPILFESL", "PCEPILFE", "UNRATE", "PAYEMS", "M2SL",
            "JTSLDL", "UNDCONTSA", "MEDLISPRIUS", "SAHMREALTIME"}
_WEEKLY = {"WALCL", "ICSA", "CCSA"}


def _expected_lag(series_id: str) -> float:
    if series_id in _MONTHLY:
        return 45.0
    if series_id in _WEEKLY:
        return 12.0
    return 5.0


def fetch_all() -> dict[str, int]:
    """Every configured series. A missing key records and returns zero."""
    if _key() is None:
        for series_id, (label, _) in SERIES.items():
            health.record(health.Record(
                source="fred", endpoint=series_id, dataset=label,
                status="needs_key",
                error="FRED_API_KEY is not set; no value is substituted from "
                      "another source"))
        return {"series": 0, "rows": 0, "key": 0}

    rows = 0
    ok = 0
    for series_id, (label, _) in SERIES.items():
        written = fetch_series(series_id, label)
        rows += written
        ok += 1 if written else 0
    return {"series": ok, "rows": rows, "key": 1}


def oil_chain_ready() -> bool:
    """Whether every series the §6.11 chain draws on is in the store."""
    return all(not store.read_macro(s).is_empty() for s in OIL_CHAIN_SERIES)


__all__ = ["fetch_all", "fetch_series", "oil_chain_ready", "SERIES",
           "OIL_CHAIN_SERIES", "_parse"]
