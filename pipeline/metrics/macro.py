"""Macro and liquidity (SPEC §6.10).

Most of this is arithmetic over stored FRED series. Three parts are not, and
they are where the mistakes live.

**Net liquidity has a unit trap.** WALCL and WTREGEN are published in MILLIONS
of dollars; RRPONTSYD is published in BILLIONS. Subtracting them as they arrive
is wrong by a factor of a thousand and produces a number that looks plausible —
a few trillion — while being nonsense. §6.10 flags it and `net_liquidity()`
converts before it subtracts.

**The composites are ours, not anyone's.** §7.3 says to build them from the
expanding-percentile machinery in `risk.py`, show every component beside the
result, and give them descriptive names. They are not clones of a proprietary
model and are not named after one.

**Series arrive on different calendars.** Daily rates, weekly claims, monthly
CPI. Anything that compares two series aligns them on shared dates first; the
cross-venue lesson from D-series applies here too, for the same reason.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from pipeline import registry, store
from pipeline.metrics import risk

# WALCL and WTREGEN are in millions, RRPONTSYD in billions (§6.10).
MILLIONS_PER_BILLION = 1_000.0

NET_LIQUIDITY_PARTS = ("WALCL", "WTREGEN", "RRPONTSYD")
CORRELATION_DAYS = 90
CLAIMS_AVERAGE_WEEKS = 4
CLAIMS_LOW_WEEKS = 52


def _series(series_id: str) -> pl.DataFrame:
    """A stored FRED series as `date, value`, oldest first."""
    frame = store.read_macro(series_id)
    if frame.is_empty():
        return pl.DataFrame(schema={"date": pl.Date, "value": pl.Float64})
    return (frame.select([pl.col("obs_date").alias("date"), pl.col("value")])
            .drop_nulls().unique(subset=["date"], keep="last").sort("date"))


def _latest(frame: pl.DataFrame) -> tuple[dt.date, float] | None:
    if frame.is_empty():
        return None
    row = frame.tail(1)
    return row["date"][0], float(row["value"][0])


def _change_over(frame: pl.DataFrame, days: int) -> float | None:
    """Absolute change against the observation closest to `days` ago.

    Not `values[-days]`: a weekly series has 52 rows a year, not 365, so
    indexing by row count would silently compare the wrong horizon.
    """
    if frame.height < 2:
        return None
    target = frame["date"][-1] - dt.timedelta(days=days)
    earlier = frame.filter(pl.col("date") <= target)
    if earlier.is_empty():
        return None
    return round(float(frame["value"][-1]) - float(earlier["value"][-1]), 4)


def net_liquidity() -> dict:
    """WALCL − WTREGEN − RRPONTSYD, in billions, with its 13-week change."""
    frames = {name: _series(name) for name in NET_LIQUIDITY_PARTS}
    missing = [name for name, frame in frames.items() if frame.is_empty()]
    if missing:
        return {"available": False,
                "reason": ("needs a FRED API key; missing "
                           + ", ".join(missing)),
                "components": NET_LIQUIDITY_PARTS}

    # Align on shared dates. The three publish on different schedules, and
    # forward-filling each to a common index is what makes the subtraction
    # meaningful rather than a comparison of three different Wednesdays.
    joined = frames["WALCL"].rename({"value": "walcl"})
    for name, column in (("WTREGEN", "tga"), ("RRPONTSYD", "rrp")):
        joined = joined.join_asof(
            frames[name].rename({"value": column}), on="date", strategy="backward")
    joined = joined.drop_nulls()
    if joined.is_empty():
        return {"available": False, "reason": "the three series share no dates"}

    # To billions: the Fed's two are in millions, the RRP is already billions.
    net = (joined["walcl"].to_numpy() / MILLIONS_PER_BILLION
           - joined["tga"].to_numpy() / MILLIONS_PER_BILLION
           - joined["rrp"].to_numpy())
    series = pl.DataFrame({"date": joined["date"], "value": net})

    return {
        "available": True,
        "latest_bn": round(float(net[-1]), 1),
        "as_of": str(joined["date"][-1]),
        "change_13w_bn": _change_over(series, 91),
        "change_52w_bn": _change_over(series, 364),
        "parts_bn": {
            "Fed assets": round(float(joined["walcl"][-1]) / MILLIONS_PER_BILLION, 1),
            "Treasury account": round(float(joined["tga"][-1]) / MILLIONS_PER_BILLION, 1),
            "Reverse repo": round(float(joined["rrp"][-1]), 1),
        },
        "series": [{"d": str(d), "v": round(float(v), 1)}
                   for d, v in zip(joined["date"].to_list(), net, strict=False)][-520:],
        "how_to_read": (
            "Reserves the banking system can actually use: the Fed's balance "
            "sheet less what the Treasury has parked at the Fed and less what "
            "money funds have lent back to it overnight. The 13-week change is "
            "the direction that matters; the level drifts with the size of the "
            "system."),
        "unit_note": (
            "WALCL and WTREGEN publish in millions and RRPONTSYD in billions. "
            "All three are converted to billions before subtracting."),
    }


# label -> (series id, unit, whether a fall is the risk-on direction)
RATE_ROWS: list[tuple[str, str, str]] = [
    ("Effective fed funds", "DFF", "%"),
    ("SOFR", "SOFR", "%"),
    ("2-year Treasury", "DGS2", "%"),
    ("10-year Treasury", "DGS10", "%"),
    ("10-year real (TIPS)", "DFII10", "%"),
    ("10-year breakeven", "T10YIE", "%"),
    ("10y − 2y", "T10Y2Y", "pp"),
    ("10y − 3m", "T10Y3M", "pp"),
    ("Broad dollar", "DTWEXBGS", "index"),
    ("High-yield OAS", "BAMLH0A0HYM2", "pp"),
    ("Financial conditions (NFCI)", "NFCI", "index"),
    ("VIX", "VIXCLS", "index"),
]


def rates_table() -> dict:
    """Level, recent change and own-history percentile for each rate series."""
    rows = []
    for label, series_id, unit in RATE_ROWS:
        frame = _series(series_id)
        latest = _latest(frame)
        if latest is None:
            rows.append({"label": label, "series_id": series_id, "unit": unit,
                         "available": False,
                         "reason": "no data; needs a FRED API key"})
            continue
        as_of, value = latest
        values = frame["value"].to_numpy().astype(float)
        rows.append({
            "label": label, "series_id": series_id, "unit": unit,
            "available": True,
            "value": round(value, 3),
            "as_of": str(as_of),
            "change_30d": _change_over(frame, 30),
            "change_90d": _change_over(frame, 90),
            "change_365d": _change_over(frame, 365),
            "percentile": _percentile(values),
            "n": int(len(values)),
        })
    return {"available": any(r["available"] for r in rows), "rows": rows,
            "how_to_read": (
                "Percentile is against each series' own history since 1990, "
                "not against the others: a 4% funds rate and a 20 VIX are not "
                "comparable levels but each has its own distribution.")}


def _percentile(values: np.ndarray) -> float | None:
    """Where the last value sits in its own history, excluding itself."""
    if len(values) < 2:
        return None
    return round(float((values[:-1] < values[-1]).mean() * 100), 1)


# label -> (series id, unit)
COMMODITY_ROWS: list[tuple[str, str, str]] = [
    ("Brent crude", "DCOILBRENTEU", "USD"),
    ("WTI crude", "DCOILWTICO", "USD"),
    ("S&P 500", "SP500", "index"),
    ("USD/AUD", "DEXUSAL", "rate"),
]


def commodities() -> dict:
    """Commodities and the ratios §6.10 asks for.

    Gold is deliberately absent: FRED no longer carries it (the brief says so),
    and the yfinance fallback is not wired. The panel names the gap rather than
    dropping the row, so the missing ratios are visible as missing.
    """
    rows = []
    for label, series_id, unit in COMMODITY_ROWS:
        frame = _series(series_id)
        latest = _latest(frame)
        if latest is None:
            rows.append({"label": label, "series_id": series_id, "unit": unit,
                         "available": False,
                         "reason": "no data; needs a FRED API key"})
            continue
        as_of, value = latest
        rows.append({
            "label": label, "series_id": series_id, "unit": unit,
            "available": True, "value": round(value, 2), "as_of": str(as_of),
            "change_30d_pct": _pct_change(frame, 30),
            "change_365d_pct": _pct_change(frame, 365),
            "percentile": _percentile(frame["value"].to_numpy().astype(float)),
        })

    ratios = []
    btc = _btc_close()
    spx = _series("SP500")
    if btc is not None and not spx.is_empty():
        ratio = _ratio_series(btc, spx)
        if ratio is not None:
            ratios.append({
                "label": "BTC / S&P 500", "available": True,
                "value": round(float(ratio["value"][-1]), 3),
                "as_of": str(ratio["date"][-1]),
                "change_90d_pct": _pct_change(ratio, 90),
                "percentile": _percentile(ratio["value"].to_numpy().astype(float)),
            })
    ratios.append({
        "label": "Gold, gold/silver, SPX/gold", "available": False,
        "reason": ("FRED no longer carries gold and the yfinance fallback is "
                   "not wired, so these three ratios have no source"),
    })

    return {"available": any(r["available"] for r in rows), "rows": rows,
            "ratios": ratios}


def _pct_change(frame: pl.DataFrame, days: int) -> float | None:
    if frame.height < 2:
        return None
    target = frame["date"][-1] - dt.timedelta(days=days)
    earlier = frame.filter(pl.col("date") <= target)
    if earlier.is_empty() or float(earlier["value"][-1]) == 0:
        return None
    return round((float(frame["value"][-1]) / float(earlier["value"][-1]) - 1) * 100, 2)


def _btc_close() -> pl.DataFrame | None:
    bars = store.read_ohlcv("BTC")
    if bars.is_empty():
        return None
    return (bars.select([pl.col("date"), pl.col("close").alias("value")])
            .drop_nulls().sort("date"))


def _ratio_series(numerator: pl.DataFrame, denominator: pl.DataFrame):
    """A ratio of two series aligned on SHARED dates.

    Dividing each series' own last value is the error that made a venue
    cross-check read 1833% once. The same trap applies to any ratio of two
    calendars that do not match — markets close on different holidays.
    """
    joined = numerator.rename({"value": "a"}).join_asof(
        denominator.rename({"value": "b"}), on="date", strategy="backward").drop_nulls()
    joined = joined.filter(pl.col("b") != 0)
    if joined.is_empty():
        return None
    return pl.DataFrame({"date": joined["date"],
                         "value": joined["a"].to_numpy() / joined["b"].to_numpy()})


CORRELATION_TARGETS = [
    ("S&P 500", "SP500"),
    ("Broad dollar", "DTWEXBGS"),
    ("10-year real yield", "DFII10"),
]


def correlations() -> dict:
    """Rolling correlation of BTC daily returns against each macro series."""
    btc = _btc_close()
    if btc is None:
        return {"available": False, "reason": "no BTC bars"}

    rows = []
    for label, series_id in CORRELATION_TARGETS:
        other = _series(series_id)
        if other.is_empty():
            rows.append({"label": label, "available": False,
                         "reason": "no data; needs a FRED API key"})
            continue
        joined = (btc.rename({"value": "btc"})
                  .join_asof(other.rename({"value": "other"}),
                             on="date", strategy="backward").drop_nulls())
        if joined.height < CORRELATION_DAYS + 2:
            rows.append({"label": label, "available": False,
                         "reason": f"only {joined.height} shared days"})
            continue
        a = np.diff(np.log(joined["btc"].to_numpy().astype(float)[-(CORRELATION_DAYS + 1):]))
        raw = joined["other"].to_numpy().astype(float)[-(CORRELATION_DAYS + 1):]
        # A yield is already a rate, so it is DIFFERENCED; a price wants a log
        # return. Deciding that from the data rather than a hard-coded list of
        # series ids matters: a spread like T10Y2Y or an index like NFCI goes
        # negative, and log() of a negative is where a wrapper reaches for
        # abs() and silently turns a fall into a rise.
        b = np.diff(raw) if np.any(raw <= 0) else np.diff(np.log(raw))
        if np.std(a) == 0 or np.std(b) == 0:
            rows.append({"label": label, "available": False,
                         "reason": "one series does not move over the window"})
            continue
        rows.append({"label": label, "available": True,
                     "correlation": round(float(np.corrcoef(a, b)[0, 1]), 2),
                     "days": CORRELATION_DAYS})

    rows.append({"label": "Nasdaq 100, gold", "available": False,
                 "reason": "no free source is wired; FRED carries neither"})
    return {"available": any(r.get("available") for r in rows), "rows": rows,
            "days": CORRELATION_DAYS,
            "how_to_read": (
                "Correlation of daily log returns over the last "
                f"{CORRELATION_DAYS} days, on dates the two series share. A "
                "yield is differenced rather than log-returned, because it is "
                "already a rate.")}


def labour() -> dict:
    """Unemployment, the Sahm rule, claims and JOLTS."""
    rows = []
    for label, series_id, unit in (
            ("Unemployment rate", "UNRATE", "%"),
            ("Sahm rule (real time)", "SAHMREALTIME", "pp"),
            ("Job openings", "JTSLDL", "thousands"),
            ("Nonfarm payrolls", "PAYEMS", "thousands")):
        frame = _series(series_id)
        latest = _latest(frame)
        if latest is None:
            rows.append({"label": label, "available": False,
                         "reason": "no data; needs a FRED API key"})
            continue
        as_of, value = latest
        rows.append({"label": label, "unit": unit, "available": True,
                     "value": round(value, 2), "as_of": str(as_of),
                     "change_365d": _change_over(frame, 365)})

    return {
        "available": any(r.get("available") for r in rows),
        "rows": rows,
        "claims": claims(),
        "state_diffusion": {
            "available": False,
            "reason": ("needs the 50 state unemployment-rate series; the "
                       "fetcher pulls national series only so far"),
        },
        "how_to_read": (
            "The Sahm rule fires when the 3-month average unemployment rate is "
            "0.5pp above its low of the prior year. It is a recession "
            "indicator with a short history and a small sample of recessions."),
    }


def claims() -> dict:
    """Initial claims 4-week average against its 52-week low (§6.10).

    The comparison is the point: claims are noisy week to week, and the level
    matters far less than whether the smoothed series has turned up off its
    own floor.
    """
    frame = _series("ICSA")
    if frame.height < CLAIMS_LOW_WEEKS + CLAIMS_AVERAGE_WEEKS:
        return {"available": False,
                "reason": ("needs a FRED API key" if frame.is_empty()
                           else f"only {frame.height} weeks of claims")}

    values = frame["value"].to_numpy().astype(float)
    average = float(np.mean(values[-CLAIMS_AVERAGE_WEEKS:]))
    rolling = np.array([
        np.mean(values[i - CLAIMS_AVERAGE_WEEKS + 1:i + 1])
        for i in range(CLAIMS_AVERAGE_WEEKS - 1, len(values))])
    low = float(np.min(rolling[-CLAIMS_LOW_WEEKS:]))
    return {
        "available": True,
        "average_4w": round(average, 0),
        "low_52w": round(low, 0),
        "above_low_pct": round((average / low - 1) * 100, 1) if low else None,
        "as_of": str(frame["date"][-1]),
        "continuing": _latest_value("CCSA"),
    }


def _latest_value(series_id: str) -> dict:
    frame = _series(series_id)
    latest = _latest(frame)
    if latest is None:
        return {"available": False, "reason": "no data; needs a FRED API key"}
    as_of, value = latest
    return {"available": True, "value": round(value, 0), "as_of": str(as_of)}


def inflation() -> dict:
    rows = []
    for label, series_id in (("CPI, all items", "CPIAUCSL"),
                             ("Core CPI", "CPILFESL"),
                             ("Core PCE", "PCEPILFE")):
        frame = _series(series_id)
        latest = _latest(frame)
        if latest is None:
            rows.append({"label": label, "available": False,
                         "reason": "no data; needs a FRED API key"})
            continue
        as_of, value = latest
        rows.append({"label": label, "available": True,
                     "index_level": round(value, 3), "as_of": str(as_of),
                     "yoy_pct": _pct_change(frame, 365),
                     "change_90d_pct": _pct_change(frame, 91)})
    return {"available": any(r.get("available") for r in rows), "rows": rows,
            "how_to_read": (
                "These are index levels; the year-on-year change is the "
                "inflation rate people quote. A monthly series is published "
                "weeks after the month it describes.")}


def policy_path() -> dict:
    """EFFR, SOFR, the 2Y − EFFR spread, and the next FOMC date."""
    effr = _latest(_series("DFF"))
    sofr = _latest(_series("SOFR"))
    two_year = _latest(_series("DGS2"))

    spread = None
    if effr and two_year:
        spread = round(two_year[1] - effr[1], 3)

    return {
        "available": effr is not None,
        "effr": {"value": round(effr[1], 3), "as_of": str(effr[0])} if effr
                else {"available": False, "reason": "needs a FRED API key"},
        "sofr": {"value": round(sofr[1], 3), "as_of": str(sofr[0])} if sofr
                else {"available": False, "reason": "needs a FRED API key"},
        "two_year_minus_effr": spread,
        "next_fomc": next_fomc(),
        "how_to_read": (
            "The 2-year yield against the current policy rate is the market's "
            "direction of travel: below it the market expects cuts, above it "
            "hikes. It is a proxy, not a probability — the event-market odds "
            "on /geopolitics are the explicit version."),
    }


def next_fomc() -> dict:
    """The next scheduled decision, from the calendar the Fed publishes."""
    frame = store.read_snapshot("calendar")
    if frame.is_empty():
        return {"available": False, "reason": "the calendar has not been built"}
    today = dt.date.today()
    upcoming = (frame.filter((pl.col("title") == "FOMC decision")
                             & (pl.col("date") >= today.isoformat()))
                .sort("date"))
    if upcoming.is_empty():
        return {"available": False, "reason": "no future FOMC date on the calendar"}
    when = dt.date.fromisoformat(upcoming["date"][0])
    return {"available": True, "date": str(when), "days_away": (when - today).days}


# How a series is turned into something a percentile can rank.
#
#   "level"  score the value as published. Right for anything already
#            stationary: a rate, a spread, a ratio, an index like the VIX.
#   "yoy"    score the year-on-year change. Required for anything that
#            COMPOUNDS -- a price index, payrolls, a money aggregate.
#   "trend"  score the residual against a fitted log-log trend.
#
# The distinction between "yoy" and "trend" is the one that bites. `risk.
# detrended` fits log(value) against log(TIME), which is a power law, and that
# is the correct and deliberate model for Bitcoin -- it is what §7.2's quantile
# model is built on, and the BTC scorecard detrends cleanly under it. A series
# that grows at a constant RATE is exponential, not power-law, so the same fit
# under-models it, the residual grows without bound, and the expanding
# percentile pins at 1.000 and stays there carrying no information at all.
# Core PCE was doing exactly that on the first build with real data.
LEVEL, YOY, TREND = "level", "yoy", "trend"

LIQUIDITY_FAMILIES: dict[str, list[tuple[str, str, str, str]]] = {
    "Policy": [
        ("effr", "Effective fed funds", "DFF", LEVEL),
        ("real_10y", "10-year real yield", "DFII10", LEVEL),
    ],
    "Credit": [
        ("hy_oas", "High-yield OAS", "BAMLH0A0HYM2", LEVEL),
        ("nfci", "Financial conditions", "NFCI", LEVEL),
    ],
    "Dollar": [
        ("dollar", "Broad dollar", "DTWEXBGS", LEVEL),
    ],
    "Volatility": [
        ("vix", "VIX", "VIXCLS", LEVEL),
    ],
}

CYCLE_FAMILIES: dict[str, list[tuple[str, str, str, str]]] = {
    "Labour": [
        ("unrate", "Unemployment rate", "UNRATE", LEVEL),
        ("claims", "Initial claims", "ICSA", LEVEL),
    ],
    "Inflation": [
        # A price INDEX compounds; its year-on-year change is what "inflation"
        # means and is the only form of it a percentile can rank.
        ("core_pce", "Core PCE, year on year", "PCEPILFE", YOY),
        ("breakeven", "10-year breakeven", "T10YIE", LEVEL),
    ],
    "Curve": [
        ("t10y3m", "10y − 3m", "T10Y3M", LEVEL),
    ],
    "Activity": [
        # Payrolls is a level that only grows. Job GROWTH is the cycle signal.
        ("payrolls", "Payrolls, year on year", "PAYEMS", YOY),
    ],
}


def _yoy_frame(frame: pl.DataFrame) -> pl.DataFrame:
    """Turn a level series into its year-on-year percentage change.

    Matched by DATE rather than row offset, because these series are monthly
    and weekly: twelve rows back is a year on one and three months on another.
    """
    if frame.height < 2:
        return pl.DataFrame(schema={"date": pl.Date, "value": pl.Float64})
    shifted = frame.select([
        (pl.col("date") + pl.duration(days=365)).alias("date"),
        pl.col("value").alias("year_ago"),
    ])
    joined = frame.join_asof(shifted, on="date", strategy="backward").drop_nulls()
    joined = joined.filter(pl.col("year_ago") != 0)
    if joined.is_empty():
        return pl.DataFrame(schema={"date": pl.Date, "value": pl.Float64})
    return pl.DataFrame({
        "date": joined["date"],
        "value": (joined["value"].to_numpy() / joined["year_ago"].to_numpy() - 1) * 100,
    })


def _build_families(spec: dict) -> dict[str, list]:
    families: dict[str, list] = {}
    for family, members in spec.items():
        built = []
        for key, label, series_id, mode in members:
            frame = _series(series_id)
            if mode == YOY:
                frame = _yoy_frame(frame)
            if frame.is_empty():
                built.append(risk.unavailable(
                    key, label, family,
                    "no data; needs a FRED API key" if mode != YOY
                    else "not enough history for a year-on-year change"))
            else:
                built.append(risk.build_component(
                    key, label, family, frame, trending=(mode == TREND)))
        families[family] = built
    return families


def composites() -> dict:
    """The two 0-1 composites §6.10 asks for, with every component visible.

    These are this project's own constructions. They are built from the same
    expanding-percentile machinery as the BTC scorecard (§7.3), carry
    descriptive names, and are not modelled on or named after anyone's
    proprietary index.
    """
    weights = registry.composite_weights()
    out = {}
    for name, spec, key in (
            ("Liquidity conditions", LIQUIDITY_FAMILIES, "liquidity"),
            ("Business cycle", CYCLE_FAMILIES, "business_cycle")):
        families = _build_families(spec)
        result = risk.composite(families, (weights or {}).get(key))
        result["name"] = name
        result["components"] = risk.scorecard(
            [component for members in families.values() for component in members])
        out[key] = result
    out["how_to_read"] = (
        "Each component is its own expanding-window percentile: where today's "
        "reading sits against every reading up to today, never including the "
        "future. A family is the mean of its components and the composite is a "
        "weighted mean of families, with weights in config/composites.yaml. "
        "Higher means tighter conditions or later in the cycle. Components are "
        "always shown, and a family with nothing available is named rather "
        "than quietly dropped from the average.")
    out["warmup_note"] = (
        f"A component needs {risk.MIN_WARMUP_DAYS} days of history before it "
        "is scored at all, and a percentile over fewer than "
        f"{risk.MIN_RANK_OBSERVATIONS} observations is noise rather than a "
        "rank.")
    return out


__all__ = ["net_liquidity", "rates_table", "commodities", "correlations",
           "labour", "claims", "inflation", "policy_path", "next_fomc",
           "composites", "LIQUIDITY_FAMILIES", "CYCLE_FAMILIES"]
