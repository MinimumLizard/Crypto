"""The store's contracts: idempotence, per-venue separation, schema growth."""

import datetime as dt

import polars as pl
import pytest

from pipeline import health, paths, store


@pytest.fixture(autouse=True)
def temp_store(tmp_path, monkeypatch):
    """Point every path at a temp dir so a test never touches real data."""
    for name in ("DATA", "RAW", "OHLCV", "SNAPSHOTS", "MACRO", "ONCHAIN",
                 "FUNDAMENTALS"):
        monkeypatch.setattr(paths, name, tmp_path / name.lower())
    monkeypatch.setattr(paths, "HEALTH", tmp_path / "source_health.parquet")
    health._pending.clear()
    yield


def _bars(start: dt.date, n: int, close: float = 100.0) -> pl.DataFrame:
    return pl.DataFrame({
        "date": [start + dt.timedelta(days=i) for i in range(n)],
        "open": [close] * n, "high": [close * 1.01] * n,
        "low": [close * 0.99] * n, "close": [close] * n,
        "volume": [1000.0] * n, "interval": ["1d"] * n, "source": ["test"] * n,
    })


def test_writing_the_same_bars_twice_is_idempotent():
    """Re-running the pipeline on the same day must be safe (§10)."""
    frame = _bars(dt.date(2025, 1, 1), 10)
    store.write_ohlcv("TEST", "binance", frame)
    store.write_ohlcv("TEST", "binance", frame)
    assert store.read_ohlcv("TEST", "binance").height == 10


def test_a_rewrite_replaces_rather_than_duplicates():
    store.write_ohlcv("TEST", "binance", _bars(dt.date(2025, 1, 1), 5, close=100.0))
    store.write_ohlcv("TEST", "binance", _bars(dt.date(2025, 1, 1), 5, close=200.0))
    out = store.read_ohlcv("TEST", "binance")
    assert out.height == 5
    assert out["close"].to_list() == [200.0] * 5


def test_venues_are_kept_apart():
    """Merging venues would splice a seam into ATR and pivot detection."""
    store.write_ohlcv("TEST", "binance", _bars(dt.date(2025, 1, 1), 5, close=100.0))
    store.write_ohlcv("TEST", "coinbase", _bars(dt.date(2025, 1, 1), 50, close=101.0))
    assert store.read_ohlcv("TEST", "binance").height == 5
    assert store.read_ohlcv("TEST", "coinbase").height == 50
    # No venue given: the longest history wins.
    assert store.venues("TEST")[0] == "coinbase"
    assert store.read_ohlcv("TEST").height == 50


def test_reading_an_unknown_symbol_returns_an_empty_frame_not_an_error():
    out = store.read_ohlcv("NOPE")
    assert out.is_empty()
    assert "close" in out.columns      # schema present, so callers can chain


def test_health_table_migrates_when_columns_are_added():
    """A stored table written before a column existed must not crash the build.

    `expected_lag_days` and `archival` were added once it was clear that judging
    every source against "today" flagged a deliberate historical backfill as
    3326 days stale. Older files lack them.
    """
    health.record(health.Record(source="s", endpoint="e", dataset="d", status="ok"))
    health.flush()

    # Simulate a file written by the previous version.
    old = pl.read_parquet(paths.HEALTH).drop(["expected_lag_days", "archival"])
    old.write_parquet(paths.HEALTH)

    health.record(health.Record(source="s2", endpoint="e2", dataset="d2",
                                status="ok", expected_lag_days=2.0, archival=True))
    assert health.flush() == 1

    loaded = health.load()
    assert loaded.height == 2
    assert "archival" in loaded.columns
    assert loaded.filter(pl.col("source") == "s2")["archival"][0] is True


def test_a_failing_fetch_is_recorded_rather_than_raised():
    health.record(health.Record(source="dead", endpoint="x", dataset="d",
                                status="http_error", error="HTTP 500"))
    health.flush()
    latest = health.latest_by_source()
    assert latest.filter(pl.col("source") == "dead")["status"][0] == "http_error"
