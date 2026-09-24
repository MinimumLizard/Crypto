"""The source-health table: one row per fetch attempt, success or failure.

SPEC §3 requires every fetcher to write here, and §2.8 requires that a failing
source never breaks the build. Those two rules are the same rule seen from
either end: a fetcher records what happened and returns, and the caller decides
what to do with a gap. Nothing in this module raises.

The table is append-only. It is the evidence behind `/source-health`, and it is
also how "this source has been failing for more than 24h" (§8) is answered
without anyone having to remember.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

import polars as pl

from pipeline import paths

SCHEMA = {
    "source": pl.Utf8,
    "endpoint": pl.Utf8,
    "dataset": pl.Utf8,
    "as_of": pl.Utf8,        # the date the DATA describes, not when we asked
    "fetched_at": pl.Utf8,   # when we asked
    "rows": pl.Int64,
    "status": pl.Utf8,       # ok | empty | http_error | network_error | needs_key | skipped
    "error": pl.Utf8,
    "latency_ms": pl.Int64,
}


@dataclass
class Record:
    source: str
    endpoint: str
    dataset: str
    status: str
    as_of: str | None = None
    rows: int = 0
    error: str = ""
    latency_ms: int = 0
    fetched_at: str = field(
        default_factory=lambda: dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))

    def ok(self) -> bool:
        return self.status == "ok"


_pending: list[Record] = []


def record(rec: Record) -> Record:
    """Queue a row. Returns it, so callers can `return health.record(...)`."""
    _pending.append(rec)
    return rec


def flush() -> int:
    """Write queued rows to the store. Safe to call when nothing is queued."""
    if not _pending:
        return 0
    frame = pl.DataFrame([asdict(r) for r in _pending], schema=SCHEMA)
    paths.DATA.mkdir(parents=True, exist_ok=True)
    if paths.HEALTH.exists():
        frame = pl.concat([pl.read_parquet(paths.HEALTH), frame], how="vertical_relaxed")
    frame.write_parquet(paths.HEALTH)
    written = len(_pending)
    _pending.clear()
    return written


def load() -> pl.DataFrame:
    if not paths.HEALTH.exists():
        return pl.DataFrame(schema=SCHEMA)
    return pl.read_parquet(paths.HEALTH)


def latest_by_source() -> pl.DataFrame:
    """Most recent attempt per (source, dataset) — what `/source-health` shows."""
    frame = load()
    if frame.is_empty():
        return frame
    return (frame.sort("fetched_at")
                 .group_by(["source", "dataset"], maintain_order=True)
                 .last())


def failing_for_hours(hours: float = 24.0) -> pl.DataFrame:
    """Sources whose most recent success is older than `hours`, or absent.

    Used by the §8 data alert. A source that has never succeeded counts as
    failing: "we have never got this" is not a healthier state than "we got it
    and then stopped".
    """
    frame = load()
    if frame.is_empty():
        return frame
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)
    latest = latest_by_source()
    good = (frame.filter(pl.col("status") == "ok")
                 .sort("fetched_at")
                 .group_by(["source", "dataset"], maintain_order=True)
                 .last()
                 .select(["source", "dataset", pl.col("fetched_at").alias("last_ok")]))
    joined = latest.join(good, on=["source", "dataset"], how="left")
    return joined.filter(
        pl.col("last_ok").is_null()
        | (pl.col("last_ok") < cutoff.isoformat(timespec="seconds")))
