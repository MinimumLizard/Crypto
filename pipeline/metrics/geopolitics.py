"""Geopolitics, policy and the oil transmission chain (SPEC §6.11).

The one piece of real statistics here is the theme spike score, and it has a
trap worth naming. A "7-day versus 90-day z-score" computed with the 90-day
window *including* the last seven days measures the spike partly against
itself: a large enough spike drags the mean up and inflates the standard
deviation, so the z-score it produces is smaller than the truth, and the bigger
the spike the more it understates it. The baseline here therefore ends where the
recent window begins. That is the same no-look-ahead discipline PLAN §5.3 sets
out, applied backwards.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from pipeline import store

RECENT_DAYS = 7
BASELINE_DAYS = 90
MIN_BASELINE = 30          # below this a standard deviation is not worth having


def _series(table: str, name: str | None = None) -> pl.DataFrame:
    frame = store.read_snapshot(table)
    if frame.is_empty():
        return frame
    # A snapshot table accumulates one row per (key, run). The newest vintage of
    # each date is the one to use; older ones are kept so a revision is visible.
    if name is not None and "series" in frame.columns:
        frame = frame.filter(pl.col("series") == name)
    if frame.is_empty():
        return frame
    return (frame.sort("observed_at")
            .group_by("date", maintain_order=True).last()
            .sort("date"))


def theme_spikes() -> dict:
    """Per-theme article volume and tone, with a spike score against baseline."""
    frame = store.read_snapshot("gdelt_themes")
    if frame.is_empty():
        return {"available": False,
                "reason": "GDELT has not returned a timeline yet"}

    frame = (frame.sort("observed_at")
             .group_by(["theme", "date"], maintain_order=True).last()
             .sort(["theme", "date"]))

    rows = []
    for theme in sorted(frame["theme"].unique().to_list()):
        sub = frame.filter(pl.col("theme") == theme).sort("date")
        volume = sub["volume_pct"].to_numpy().astype(float)
        if len(volume) < MIN_BASELINE + RECENT_DAYS:
            rows.append({
                "theme": theme, "available": False, "n": int(len(volume)),
                "reason": (f"{len(volume)} days of history; a baseline needs "
                           f"{MIN_BASELINE + RECENT_DAYS}"),
            })
            continue

        recent = volume[-RECENT_DAYS:]
        baseline = volume[-(RECENT_DAYS + BASELINE_DAYS):-RECENT_DAYS]
        mean = float(np.mean(baseline))
        sd = float(np.std(baseline, ddof=1))
        # A theme with no variation in the baseline has no scale to score on.
        # Returning 0 would put it at "perfectly normal", which is a claim.
        spike = (float(np.mean(recent)) - mean) / sd if sd > 0 else None

        tone_values = sub["tone"].drop_nulls().to_numpy().astype(float)
        tone_recent = (float(np.mean(tone_values[-RECENT_DAYS:]))
                       if len(tone_values) >= RECENT_DAYS else None)
        tone_baseline = (float(np.mean(tone_values[-(RECENT_DAYS + BASELINE_DAYS):
                                                   -RECENT_DAYS]))
                         if len(tone_values) >= RECENT_DAYS + MIN_BASELINE else None)

        rows.append({
            "theme": theme, "available": True,
            "n": int(len(volume)),
            "recent_volume_pct": round(float(np.mean(recent)), 4),
            "baseline_volume_pct": round(mean, 4),
            "spike_z": round(spike, 2) if spike is not None else None,
            "tone_recent": round(tone_recent, 2) if tone_recent is not None else None,
            "tone_baseline": (round(tone_baseline, 2)
                              if tone_baseline is not None else None),
            "tone_shift": (round(tone_recent - tone_baseline, 2)
                           if tone_recent is not None and tone_baseline is not None
                           else None),
            "last_date": str(sub["date"].max()),
            "series": [{"d": d, "v": round(float(v), 4)}
                       for d, v in zip(sub["date"].to_list(), volume, strict=False)][-180:],
        })

    rows.sort(key=lambda r: (r.get("spike_z") is None, -(r.get("spike_z") or 0)))

    # Naming what is missing rather than showing a short list as if it were the
    # whole one. GDELT rate-limits per source IP, so a sweep can run out of
    # budget with themes still unfetched; the reader has to be able to tell the
    # difference between "quiet" and "never asked".
    from pipeline.fetchers.geopolitics import THEMES as CONFIGURED

    configured = [label for label, _ in CONFIGURED]
    present = {row["theme"] for row in rows}
    missing = [label for label in configured if label not in present]

    return {
        "available": True,
        "rows": rows,
        "configured": len(configured),
        "missing": missing,
        "recent_days": RECENT_DAYS,
        "baseline_days": BASELINE_DAYS,
        "method_note": (
            f"Spike is the last {RECENT_DAYS} days of coverage scored against the "
            f"{BASELINE_DAYS} days BEFORE them. The windows do not overlap on "
            "purpose: a baseline that contained the spike would be dragged up by "
            "it and would understate exactly the moves worth seeing. Volume is "
            "GDELT's share-of-coverage measure, already normalised for how much "
            "news there was that day."),
    }


def _percentile_of_last(values: np.ndarray) -> float | None:
    if len(values) < 2:
        return None
    return round(float((values[:-1] < values[-1]).mean() * 100), 1)


def risk_index(name: str, label: str, series: str | None = None) -> dict:
    """GPR or EPU: the level, where it sits in its own history, and a series."""
    frame = _series(f"risk_{name}", series)
    if frame.is_empty():
        return {"available": False, "label": label,
                "reason": f"{label} has not been fetched yet"}

    values = frame["value"].to_numpy().astype(float)
    dates = frame["date"].to_list()
    return {
        "available": True,
        "label": label,
        "latest": round(float(values[-1]), 1),
        "as_of": str(dates[-1]),
        "n": int(len(values)),
        "start": str(dates[0]),
        "percentile": _percentile_of_last(values),
        "mean_30d": round(float(np.mean(values[-30:])), 1) if len(values) >= 30 else None,
        "mean_365d": (round(float(np.mean(values[-365:])), 1)
                      if len(values) >= 365 else None),
        "series": [{"d": d, "v": round(float(v), 2)}
                   for d, v in zip(dates, values, strict=False)][-730:],
    }


def gpr_split() -> dict:
    """The threat/act decomposition, which is the reason to keep three series.

    A threat index above an act index is a market pricing a risk that has not
    happened. The reverse is coverage of something that already has. They move
    apart at exactly the moments the headline index is least informative.
    """
    parts = {key: risk_index("gpr", key, key)
             for key in ("GPRD", "GPRD_THREAT", "GPRD_ACT")}
    if not parts["GPRD"].get("available"):
        return {"available": False, "reason": parts["GPRD"].get("reason", "no data")}

    threat = parts["GPRD_THREAT"].get("latest")
    act = parts["GPRD_ACT"].get("latest")
    return {
        "available": True,
        "headline": parts["GPRD"],
        "threat": parts["GPRD_THREAT"],
        "act": parts["GPRD_ACT"],
        "gap": (round(threat - act, 1)
                if threat is not None and act is not None else None),
        "reading": _threat_act_reading(threat, act),
    }


def _threat_act_reading(threat: float | None, act: float | None) -> str:
    if threat is None or act is None:
        return "not measurable"
    if threat > act * 1.25:
        return "threats lead acts: risk is being priced, not realised"
    if act > threat * 1.25:
        return "acts lead threats: coverage is of events already underway"
    return "threats and acts are close"


# The chain §6.11 asks to draw. Each link names the series that would fill it and
# where that series comes from, so a missing one says which key is absent rather
# than rendering an empty box.
OIL_CHAIN = [
    {"key": "oil", "label": "Oil", "detail": "Brent crude, spot",
     "source": "FRED DCOILBRENTEU"},
    {"key": "inflation", "label": "Inflation", "detail": "CPI, year on year",
     "source": "FRED CPIAUCSL"},
    {"key": "fed", "label": "Fed", "detail": "Effective fed funds rate",
     "source": "FRED DFF"},
    {"key": "real_yields", "label": "Real yields", "detail": "10-year TIPS",
     "source": "FRED DFII10"},
    {"key": "dollar", "label": "Dollar", "detail": "Broad dollar index",
     "source": "FRED DTWEXBGS"},
    {"key": "liquidity", "label": "Liquidity", "detail": "Stablecoin supply",
     "source": "DefiLlama stablecoins"},
    {"key": "crypto", "label": "Crypto", "detail": "Bitcoin",
     "source": "Binance / Coinbase bars"},
]


def oil_chain() -> dict:
    """Oil to crypto, with the live value of each link that has a source.

    Five of the seven links are FRED series and FRED needs a key, so on a build
    without one this renders as a chain with its middle missing and each gap
    naming the series it wants. That is the §0.2 rule: a panel with no data says
    which source it has no access to, and never shows a placeholder number.
    """
    links = []
    for link in OIL_CHAIN:
        entry = dict(link)
        entry.update(_chain_value(link["key"]))
        links.append(entry)

    filled = sum(1 for link in links if link.get("available"))
    return {
        "links": links,
        "filled": filled,
        "total": len(links),
        "how_to_read": (
            "The chain the whole macro thesis runs through: oil sets inflation, "
            "inflation sets the Fed, the Fed sets real yields and the dollar, and "
            "those set the liquidity crypto trades on. A link with no value names "
            "the series it needs rather than showing a number it does not have."),
    }


def _chain_value(key: str) -> dict:
    if key == "crypto":
        bars = store.read_ohlcv("BTC")
        if bars.is_empty():
            return {"available": False, "reason": "no BTC bars"}
        bars = bars.sort("date")
        closes = bars["close"].to_numpy().astype(float)
        change = ((closes[-1] / closes[-31] - 1) * 100 if len(closes) > 31 else None)
        return {"available": True, "value": round(float(closes[-1]), 2),
                "unit": "USD", "change_30d_pct": round(change, 1) if change else None,
                "as_of": str(bars["date"].max())}

    if key == "liquidity":
        frame = store.read_onchain("market", "StablecoinSupply")
        if frame.is_empty():
            return {"available": False, "reason": "no stablecoin supply series"}
        frame = frame.sort("date")
        values = frame["value"].to_numpy().astype(float)
        change = ((values[-1] / values[-31] - 1) * 100 if len(values) > 31 else None)
        return {"available": True, "value": round(float(values[-1]) / 1e9, 1),
                "unit": "$bn", "change_30d_pct": round(change, 1) if change else None,
                "as_of": str(frame["date"].max())}

    series = {"oil": "DCOILBRENTEU", "inflation": "CPIAUCSL", "fed": "DFF",
              "real_yields": "DFII10", "dollar": "DTWEXBGS"}[key]
    frame = store.read_macro(series)
    if frame.is_empty():
        return {"available": False,
                "reason": "needs a FRED API key; no macro series is stored"}
    frame = frame.sort("obs_date")
    values = frame["value"].to_numpy().astype(float)
    return {"available": True, "value": round(float(values[-1]), 2),
            "as_of": str(frame["obs_date"].max())}


def event_odds() -> dict:
    """Event-market odds grouped by topic, newest snapshot per market.

    Odds history accumulates from the first build (§3): neither Polymarket nor
    Kalshi serves a price series free, so the trend column is blank until there
    are two snapshots to compare and says so.
    """
    frame = store.read_snapshot("event_markets")
    if frame.is_empty():
        return {"available": False,
                "reason": "no event-market snapshot has been taken yet"}

    latest = (frame.sort("observed_at")
              .group_by(["venue", "slug"], maintain_order=True).last())
    days = frame["as_of"].n_unique()

    # A previous snapshot, if one exists, so the move is real rather than implied.
    previous: dict[tuple[str, str], float] = {}
    if days > 1:
        prior_day = sorted(frame["as_of"].unique().to_list())[-2]
        older = (frame.filter(pl.col("as_of") == prior_day)
                 .sort("observed_at").group_by(["venue", "slug"], maintain_order=True).last())
        previous = {(r["venue"], r["slug"]): r["probability"]
                    for r in older.iter_rows(named=True)}

    # Re-derive the topic from the question at READ time rather than trusting
    # the label stored with the row. The classifier is derived data, not source
    # data: an early version matched "brent" as a substring and filed "Will
    # Brentford win the EPL?" under Oil, and because the store is append-only
    # that row kept its wrong label after the matcher was fixed. Classifying on
    # read means every stored row benefits from every later correction, and a
    # row that no longer matches any topic drops out instead of persisting.
    from pipeline.fetchers.geopolitics import _topic_for

    topics: dict[str, list[dict]] = {}
    for row in latest.iter_rows(named=True):
        topic = _topic_for(str(row.get("question") or ""))
        if topic is None:
            continue
        was = previous.get((row["venue"], row["slug"]))
        topics.setdefault(topic, []).append({
            "venue": row["venue"],
            "question": row["question"],
            "outcome": row.get("outcome") or "Yes",
            "probability": round(float(row["probability"]) * 100, 1),
            "change_pts": (round((float(row["probability"]) - float(was)) * 100, 1)
                           if was is not None else None),
            "volume": row.get("volume"),
            "end_date": row.get("end_date"),
        })

    for markets in topics.values():
        markets.sort(key=lambda m: -(m["volume"] or 0))

    return {
        "available": True,
        "topics": dict(sorted(topics.items())),
        "snapshot_days": int(days),
        "as_of": str(frame["as_of"].max()),
        "how_to_read": (
            "Prices are probabilities: a market at 20 means the market is paying "
            "20 cents for a dollar if it happens. Within a topic the markets are "
            "ranked by volume, because a thin market's price is an opinion rather "
            "than a consensus."),
    }


def narrative(path) -> dict:
    """The owner's own dated notes. Never read by any calculation (§0.2)."""
    if not path.exists():
        return {"available": False, "reason": "content/narrative.md is absent"}
    text = path.read_text(encoding="utf-8")
    entries = []
    for block in text.split("\n## ")[1:]:
        heading, _, body = block.partition("\n")
        entries.append({"date": heading.strip(), "body": body.strip()})
    entries.reverse()
    return {"available": True, "entries": entries,
            "note": "Written by hand. Nothing here is read by any computation."}


def as_of_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


__all__ = ["theme_spikes", "risk_index", "gpr_split", "oil_chain", "event_odds",
           "narrative", "OIL_CHAIN"]
