"""Asymmetric quadratic quantile bands for Bitcoin (SPEC §7.2).

Fits the conditional distribution of log price against log time, allowing the
upper and lower tails to curve differently:

    y = log10(price)
    t = days since 2009-01-01
    x = ln(t) - mu,    mu = mean of ln(t) over the sample
    Q_tau(y) = c_tau + a_tau*x + b_tau*x^2        for tau in
               {0.01, 0.10, 0.25, 0.50, 0.75, 0.95, 0.99}

Curvature is SHARED within each tail group, which is the whole point of the
model: b_LO for the three lower quantiles, a free b_MED for the median, b_HI
for the three upper ones. Seven intercepts + seven slopes + three curvatures =
17 parameters.

Estimation is a linear program. Quantile regression minimises the check loss
rho_tau(u) = u*(tau - 1{u<0}), which is piecewise linear, so writing each
residual as the difference of two non-negative slacks turns the whole thing
into an LP that HiGHS solves exactly — no iterative reweighting, no starting
values, no local minima. Each tail group is estimated JOINTLY, because the
shared b couples its quantiles.

**What these bands are not.** They describe the conditional distribution of the
price LEVEL given time. They are not return forecasts and not tail-risk
estimates, and the lower tail's curvature is not statistically distinguishable
from zero. The page says all of that; so does this docstring, because the
difference between "price is at its 1st percentile" and "price will rise" is
exactly the misreading this model invites.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
from scipy import sparse
from scipy.optimize import linprog

GENESIS = dt.date(2009, 1, 1)

TAUS = (0.01, 0.10, 0.25, 0.50, 0.75, 0.95, 0.99)
GROUP_LO = (0.01, 0.10, 0.25)
GROUP_MED = (0.50,)
GROUP_HI = (0.75, 0.95, 0.99)
GROUPS = {"lo": GROUP_LO, "med": GROUP_MED, "hi": GROUP_HI}

# The paper's reported values, used as a pass/fail gate before the bands may
# ship (§7.2). Tolerances are loose enough for a different price series to
# still pass and tight enough that a wrong model cannot.
PAPER = {"mu": 7.9914, "b_hi": -0.326, "b_lo": -0.024, "n": 5788}
PAPER_TOLERANCE = {"mu": 0.02, "b_hi": 0.03, "b_lo": 0.02, "n": 60}


@dataclass
class Fit:
    mu: float
    n: int
    coefficients: dict[float, tuple[float, float, float]]   # tau -> (c, a, b)
    curvature: dict[str, float]                             # group -> b
    fitted_through: str

    def predict(self, days: np.ndarray | float) -> dict[float, np.ndarray]:
        """Band values (in price, not log price) at the given day counts.

        Predictions are rearranged so the bands never cross: at each x the
        seven values are sorted. That is the Chernozhukov-Fernandez-Val-
        Galichon rearrangement, and it is a genuine estimator rather than a
        cosmetic sort -- it weakly improves the fit.
        """
        days = np.atleast_1d(np.asarray(days, dtype=float))
        x = np.log(np.maximum(days, 1.0)) - self.mu
        raw = np.vstack([
            self.coefficients[tau][0] + self.coefficients[tau][1] * x
            + self.coefficients[tau][2] * x**2
            for tau in TAUS])
        rearranged = np.sort(raw, axis=0)
        return {tau: 10 ** rearranged[i] for i, tau in enumerate(TAUS)}

    def position(self, day: float, price: float) -> float:
        """Where a price sits within the bands, as a percentile in [0, 100].

        Linearly interpolated between adjacent bands in log space, and clipped
        outside the outer bands -- the 1% band is not a floor, and roughly 1%
        of history has closed beneath it, so a reading pinned at 1 means "at or
        below the lowest band" rather than "cannot go lower".
        """
        bands = self.predict(np.array([day]))
        levels = np.array([float(bands[tau][0]) for tau in TAUS])
        percentiles = np.array(TAUS) * 100.0
        value = np.log10(price)
        logs = np.log10(levels)
        if value <= logs[0]:
            return float(percentiles[0])
        if value >= logs[-1]:
            return float(percentiles[-1])
        return float(np.interp(value, logs, percentiles))


def days_since_genesis(dates) -> np.ndarray:
    return np.array([(d - GENESIS).days for d in dates], dtype=float)


def _fit_group(x: np.ndarray, y: np.ndarray, taus: tuple[float, ...]
               ) -> tuple[dict[float, tuple[float, float, float]], float]:
    """Joint check-loss minimisation for one tail group, sharing curvature.

    Variables, in order:
        c_1..c_k, a_1..a_k, b            (free)
        u+_1..u+_kn, u-_1..u-_kn         (non-negative)

    One equality row per (tau, observation):
        c_tau + a_tau*x_i + b*x_i^2 + u+ - u- = y_i
    """
    n = len(x)
    k = len(taus)
    n_params = 2 * k + 1
    n_slack = k * n

    cost = np.concatenate([
        np.zeros(n_params),
        np.repeat(np.array(taus), n),            # u+ costs tau
        np.repeat(1.0 - np.array(taus), n),      # u- costs (1 - tau)
    ])

    rows, cols, values = [], [], []
    for j, _tau in enumerate(taus):
        offset = j * n
        index = np.arange(n) + offset
        # c_tau
        rows.extend(index); cols.extend([j] * n); values.extend(np.ones(n))
        # a_tau
        rows.extend(index); cols.extend([k + j] * n); values.extend(x)
        # shared b
        rows.extend(index); cols.extend([2 * k] * n); values.extend(x**2)
        # +u+
        rows.extend(index)
        cols.extend(n_params + index)
        values.extend(np.ones(n))
        # -u-
        rows.extend(index)
        cols.extend(n_params + n_slack + index)
        values.extend(-np.ones(n))

    a_eq = sparse.coo_matrix(
        (values, (rows, cols)), shape=(k * n, n_params + 2 * n_slack)).tocsr()
    b_eq = np.tile(y, k)

    bounds = [(None, None)] * n_params + [(0, None)] * (2 * n_slack)
    result = linprog(cost, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not result.success:
        raise RuntimeError(f"quantile LP failed: {result.message}")

    shared_b = float(result.x[2 * k])
    coefficients = {
        tau: (float(result.x[j]), float(result.x[k + j]), shared_b)
        for j, tau in enumerate(taus)
    }
    return coefficients, shared_b


def fit(dates, prices, *, through: dt.date | None = None) -> Fit:
    """Fit all seven quantiles. `through` truncates the sample (for the gate)."""
    dates = list(dates)
    prices = np.asarray(prices, dtype=float)

    keep = prices > 0
    if through is not None:
        keep &= np.array([d <= through for d in dates])
    dates = [d for d, k in zip(dates, keep, strict=False) if k]
    prices = prices[keep]
    if len(prices) < 500:
        raise ValueError(f"need at least 500 observations, got {len(prices)}")

    days = days_since_genesis(dates)
    log_days = np.log(np.maximum(days, 1.0))
    mu = float(np.mean(log_days))
    x = log_days - mu
    y = np.log10(prices)

    coefficients: dict[float, tuple[float, float, float]] = {}
    curvature: dict[str, float] = {}
    for name, taus in GROUPS.items():
        group_coefficients, shared_b = _fit_group(x, y, taus)
        coefficients.update(group_coefficients)
        curvature[name] = shared_b

    return Fit(mu=mu, n=len(prices), coefficients=coefficients,
               curvature=curvature, fitted_through=str(dates[-1]))


def replication_report(fitted: Fit) -> dict:
    """Compare a fit against the paper's published values (§7.2's gate).

    Returns a dict with a `passed` flag and every comparison, so the validation
    page can show the whole table rather than a bare pass/fail. The brief is
    explicit: if the fit does not reproduce these, find out why BEFORE using
    the bands for anything.
    """
    observed = {
        "mu": fitted.mu,
        "b_hi": fitted.curvature["hi"],
        "b_lo": fitted.curvature["lo"],
        "n": float(fitted.n),
    }
    checks = {}
    for key, expected in PAPER.items():
        got = observed[key]
        tolerance = PAPER_TOLERANCE[key]
        checks[key] = {
            "expected": expected,
            "observed": round(got, 4),
            "tolerance": tolerance,
            "difference": round(got - expected, 4),
            "passed": bool(abs(got - expected) <= tolerance),
        }
    return {"passed": all(c["passed"] for c in checks.values()), "checks": checks}
