"""The quantile band model, and the paper replication gate (SPEC §7.2).

Two layers. The fast tests use synthetic data and always run. The replication
gate needs BTC's real history back to 2010, so it skips when the store is empty
rather than reaching for the network from a unit test.
"""

import datetime as dt

import numpy as np
import pytest

from pipeline import store
from pipeline.signals import quantile as q


@pytest.fixture(scope="module")
def synthetic_fit():
    rng = np.random.default_rng(1)
    dates = [dt.date(2010, 7, 18) + dt.timedelta(days=i) for i in range(3000)]
    days = q.days_since_genesis(dates)
    x = np.log(days) - np.log(days).mean()
    y = 1.2 + 2.1 * x - 0.2 * x**2 + rng.normal(0, 0.3, len(x))
    return q.fit(dates, 10**y)


def test_model_has_seventeen_parameters(synthetic_fit):
    """Seven intercepts + seven slopes + three curvatures."""
    intercepts = len(synthetic_fit.coefficients)
    slopes = len(synthetic_fit.coefficients)
    curvatures = len(synthetic_fit.curvature)
    assert intercepts + slopes + curvatures == 17
    assert curvatures == 3


def test_curvature_is_shared_within_each_tail_group(synthetic_fit):
    """The shared b is the whole point of the model; if it drifts apart the
    group was fitted separately and the spec was not followed."""
    for group, taus in q.GROUPS.items():
        values = {synthetic_fit.coefficients[tau][2] for tau in taus}
        assert len(values) == 1, f"group {group} did not share its curvature"
        assert synthetic_fit.curvature[group] == pytest.approx(values.pop())


def test_bands_never_cross_after_rearrangement(synthetic_fit):
    """Chernozhukov-Fernandez-Val-Galichon: sorted at every x."""
    days = np.linspace(500, 8000, 400)
    bands = synthetic_fit.predict(days)
    stacked = np.vstack([bands[tau] for tau in q.TAUS])
    assert np.all(np.diff(stacked, axis=0) >= -1e-9)


def test_quantiles_cover_roughly_their_share(synthetic_fit):
    """A 25% band should sit above about a quarter of the observations."""
    rng = np.random.default_rng(1)
    dates = [dt.date(2010, 7, 18) + dt.timedelta(days=i) for i in range(3000)]
    days = q.days_since_genesis(dates)
    x = np.log(days) - np.log(days).mean()
    y = 1.2 + 2.1 * x - 0.2 * x**2 + rng.normal(0, 0.3, len(x))
    prices = 10**y
    bands = synthetic_fit.predict(days)
    for tau in (0.10, 0.25, 0.50, 0.75, 0.95):
        share = float(np.mean(prices <= bands[tau]))
        assert abs(share - tau) < 0.06, f"tau={tau} covered {share:.3f}"


def test_position_is_a_percentile_in_range(synthetic_fit):
    days = 5000.0
    bands = synthetic_fit.predict(np.array([days]))
    assert synthetic_fit.position(days, float(bands[0.50][0])) == pytest.approx(50.0, abs=0.5)
    # Below the lowest band clips to 1, not to something negative: the 1% band
    # is not a floor, and a reading there means "at or under it".
    assert synthetic_fit.position(days, float(bands[0.01][0]) * 0.1) == pytest.approx(1.0)
    assert synthetic_fit.position(days, float(bands[0.99][0]) * 10) == pytest.approx(99.0)


def test_fit_refuses_a_sample_too_short_to_mean_anything():
    dates = [dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(100)]
    with pytest.raises(ValueError, match="at least 500"):
        q.fit(dates, np.linspace(100, 200, 100))


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def test_replication_gate_reproduces_the_paper():
    """SPEC §7.2: reproduce mu, b_HI, b_LO and n, or do not ship the bands.

    Fitted on daily closes from 2010 through 2026-05-29, per the brief. The
    price series is CoinMetrics PriceUSD, which is not necessarily the series
    the paper used, so exact agreement is not expected -- the tolerances allow
    for a different source while still failing a wrong model.
    """
    series = store.read_onchain("btc", "PriceUSD")
    if series.is_empty() or series.height < 5000:
        pytest.skip("BTC price history not in the store; run: terminal fetch --only onchain")

    fitted = q.fit(list(series["date"]), series["value"].to_numpy(),
                   through=dt.date(2026, 5, 29))
    report = q.replication_report(fitted)
    failures = {k: v for k, v in report["checks"].items() if not v["passed"]}
    assert report["passed"], f"replication gate failed: {failures}"
