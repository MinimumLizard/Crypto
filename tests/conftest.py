import datetime as dt

import numpy as np
import pytest

from pipeline import health, paths


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    """Point every path at a temp dir so a test never touches real data.

    Opt-in rather than autouse: the indicator and quantile tests are pure and
    do not want the cost, and a test that DOES touch the store should have to
    say so.
    """
    for name in ("DATA", "RAW", "OHLCV", "SNAPSHOTS", "MACRO", "ONCHAIN",
                 "FUNDAMENTALS"):
        monkeypatch.setattr(paths, name, tmp_path / name.lower())
    monkeypatch.setattr(paths, "HEALTH", tmp_path / "source_health.parquet")
    monkeypatch.setattr(paths, "ARTEFACTS", tmp_path / "artefacts")
    health._pending.clear()
    yield tmp_path


@pytest.fixture
def synthetic_bars():
    """800 daily bars that trend up then down, so every regime is exercised."""
    rng = np.random.default_rng(3)
    n = 800
    drift = np.concatenate([np.full(n // 2, 0.004), np.full(n // 2, -0.004)])
    close = 100 * np.cumprod(1 + drift + rng.normal(0, 0.02, n))
    high = close * (1 + abs(rng.normal(0, 0.008, n)))
    low = close * (1 - abs(rng.normal(0, 0.008, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = rng.uniform(1e5, 1e6, n)
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(n)]
    return dates, open_, high, low, close, volume
