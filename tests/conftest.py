import datetime as dt

import numpy as np
import pytest


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
