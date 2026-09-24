"""The normalised history store: partitioned Parquet, append-only, idempotent.

Re-running the pipeline on the same day must be safe (§10), so every write is
an upsert on the table's key rather than a blind append: new rows replace rows
with the same key and leave the rest alone. That is what lets a failed run be
simply re-run.

Partitioning is by year for daily series and by month for anything snapshotted
every two hours, so a day's commit touches one small file rather than rewriting
years of history (D008).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl

from pipeline import paths


def _write(path: Path, frame: pl.DataFrame, key: list[str]) -> int:
    """Upsert `frame` into the parquet at `path`, keyed on `key`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pl.read_parquet(path)
        # Drop rows the new frame replaces, then concat. An anti-join is the
        # cheapest correct way to express "new wins" without a merge engine.
        existing = existing.join(frame.select(key), on=key, how="anti")
        frame = pl.concat([existing, frame], how="diagonal_relaxed")
    frame = frame.unique(subset=key, keep="last").sort(key)
    frame.write_parquet(path)
    return len(frame)


# ---------------------------------------------------------------------------
# OHLCV — stored per VENUE, not merged
# ---------------------------------------------------------------------------
#
# Bars from different venues are different data and are never interleaved into
# one series. Probing on 2026-09-24 made the reason concrete: Binance listed
# HYPE that same day, AERO in July 2026 and CFG in March 2026, so a
# parity-first policy leaves those names with 0, 69 and 192 bars against the
# 300 the regime engine needs to warm up. Coinbase has years of history for all
# three.
#
# Splicing the two would produce a series with a seam in it -- different
# liquidity, different quote asset, a step in volume -- feeding an ATR and a
# pivot detector that would both read the seam as a real event. So both are
# kept, and callers choose:
#
#   parity venue  -> Binance, what MiniLizard is defined on (SPEC 7.1)
#   longest venue -> whatever actually has the history, for charts
#
# When they are not the same venue the asset page says so.

OHLCV_KEY = ["date", "interval"]


def write_ohlcv(symbol: str, venue: str, frame: pl.DataFrame) -> int:
    """Store bars for one symbol at one venue."""
    if frame.is_empty():
        return 0
    frame = frame.with_columns(pl.col("date").cast(pl.Date))
    total = 0
    for (year,), part in frame.group_by([pl.col("date").dt.year()], maintain_order=True):
        total += _write(paths.OHLCV / symbol / venue / f"{year}.parquet", part, OHLCV_KEY)
    return total


def _empty_ohlcv() -> pl.DataFrame:
    return pl.DataFrame(schema={
        "date": pl.Date, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
        "close": pl.Float64, "volume": pl.Float64, "interval": pl.Utf8,
        "source": pl.Utf8})


def venues(symbol: str) -> list[str]:
    """Which venues we hold bars for, longest history first."""
    directory = paths.OHLCV / symbol
    if not directory.exists():
        return []
    counts = []
    for venue_dir in directory.iterdir():
        if not venue_dir.is_dir():
            continue
        rows = sum(pl.read_parquet(p).height for p in venue_dir.glob("*.parquet"))
        counts.append((rows, venue_dir.name))
    return [name for _, name in sorted(counts, reverse=True)]


def read_ohlcv(symbol: str, venue: str | None = None,
               interval: str = "1d") -> pl.DataFrame:
    """Bars for a symbol. `venue=None` returns whichever venue has most history."""
    if venue is None:
        available = venues(symbol)
        if not available:
            return _empty_ohlcv()
        venue = available[0]
    directory = paths.OHLCV / symbol / venue
    if not directory.exists():
        return _empty_ohlcv()
    parts = sorted(directory.glob("*.parquet"))
    if not parts:
        return _empty_ohlcv()
    frame = pl.concat([pl.read_parquet(p) for p in parts], how="diagonal_relaxed")
    return (frame.filter(pl.col("interval") == interval)
                 .unique(subset=["date"], keep="last").sort("date"))


def known_symbols() -> list[str]:
    if not paths.OHLCV.exists():
        return []
    return sorted(p.name for p in paths.OHLCV.iterdir() if p.is_dir())


# ---------------------------------------------------------------------------
# Snapshots — things no free API serves historically (§3 "own history store")
# ---------------------------------------------------------------------------

def write_snapshot(table: str, frame: pl.DataFrame, key: list[str]) -> int:
    """Append a point-in-time observation to a monthly-partitioned table.

    These exist because the free APIs answer "what is circulating supply today"
    and never "what did you believe it was last March". Storing the answer daily
    is the only way a supply-change or unlock-overhang series can ever be
    honest about what was knowable at the time (PLAN §5.3).
    """
    if frame.is_empty():
        return 0
    stamp = dt.datetime.now(dt.UTC)
    if "as_of" not in frame.columns:
        frame = frame.with_columns(pl.lit(stamp.date().isoformat()).alias("as_of"))
    if "observed_at" not in frame.columns:
        frame = frame.with_columns(
            pl.lit(stamp.isoformat(timespec="seconds")).alias("observed_at"))
    month = stamp.strftime("%Y-%m")
    return _write(paths.SNAPSHOTS / table / f"{month}.parquet", frame, key)


def read_snapshot(table: str) -> pl.DataFrame:
    directory = paths.SNAPSHOTS / table
    if not directory.exists():
        return pl.DataFrame()
    parts = sorted(directory.glob("*.parquet"))
    if not parts:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(p) for p in parts], how="diagonal_relaxed").sort("as_of")


# ---------------------------------------------------------------------------
# Macro — revisable, so every row carries the vintage it was fetched at
# ---------------------------------------------------------------------------

def write_macro(series_id: str, frame: pl.DataFrame) -> int:
    """Store a macro series. `frame` needs obs_date and value.

    FRED revises. A row therefore records both the date it describes and the
    date we learned it, so a point-in-time backtest can ask what was published
    at the time rather than what the series says today (PLAN §5.3).
    """
    if frame.is_empty():
        return 0
    if "vintage_fetched_at" not in frame.columns:
        frame = frame.with_columns(
            pl.lit(dt.datetime.now(dt.UTC).date().isoformat()).alias("vintage_fetched_at"))
    return _write(paths.MACRO / f"{series_id}.parquet", frame, ["obs_date"])


def read_macro(series_id: str) -> pl.DataFrame:
    path = paths.MACRO / f"{series_id}.parquet"
    if not path.exists():
        return pl.DataFrame(schema={"obs_date": pl.Date, "value": pl.Float64})
    return pl.read_parquet(path).sort("obs_date")


# ---------------------------------------------------------------------------
# On-chain and protocol fundamentals
# ---------------------------------------------------------------------------

def write_onchain(asset: str, metric: str, frame: pl.DataFrame) -> int:
    if frame.is_empty():
        return 0
    return _write(paths.ONCHAIN / asset / f"{metric}.parquet", frame, ["date"])


def read_onchain(asset: str, metric: str) -> pl.DataFrame:
    path = paths.ONCHAIN / asset / f"{metric}.parquet"
    if not path.exists():
        return pl.DataFrame(schema={"date": pl.Date, "value": pl.Float64})
    return pl.read_parquet(path).sort("date")


def write_fundamentals(slug: str, data_type: str, frame: pl.DataFrame) -> int:
    if frame.is_empty():
        return 0
    return _write(paths.FUNDAMENTALS / slug / f"{data_type}.parquet", frame, ["date"])


def read_fundamentals(slug: str, data_type: str) -> pl.DataFrame:
    path = paths.FUNDAMENTALS / slug / f"{data_type}.parquet"
    if not path.exists():
        return pl.DataFrame(schema={"date": pl.Date, "value": pl.Float64})
    return pl.read_parquet(path).sort("date")
