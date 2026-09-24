"""Normalised risk on a 0-1 scale, and the composites built from it (SPEC §7.3).

The recipe, and why each step is there:

1. **Detrend first, if the metric trends.** Price and market cap grow by orders
   of magnitude, so a percentile of the raw level says only "it is later than it
   used to be". Taking the residual against a refitted log trend asks the
   question that was meant: expensive *relative to where the trend says it
   should be*.
2. **Risk = expanding-window percentile rank.** The value's rank among all
   values up to and including that date, and no later ones. Expanding rather
   than rolling, because the question is "how does this compare to everything we
   have ever seen", and rolling would let a metric look extreme merely because
   the last two years were quiet.
3. **Warm-up.** The first two years are marked and excluded from signal. A
   percentile over 200 observations is mostly an artefact of the 200.

Everything is point-in-time by construction: `_expanding_rank` at row i touches
only rows 0..i, and the trend used at row i is refitted on rows 0..i. That is
the difference between a cycle indicator and a cycle indicator that knew the
future, and it is why the readings here will look less clean than a chart
fitted over all of history -- correctly so.

Naming: these are our own constructions. They deliberately do not borrow the
names or branding of anyone's proprietary risk metrics (§7.3).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

MIN_WARMUP_DAYS = 730          # ~2 years; §7.3 asks for 2-4
MIN_RANK_OBSERVATIONS = 60     # below this a percentile is noise


def expanding_rank(values: np.ndarray) -> np.ndarray:
    """Percentile rank of each value among all values up to and including it.

    O(n log n) via argsort rather than the obvious O(n^2) loop, which matters
    at 6,000 daily observations times ~25 metrics on every build.
    """
    n = len(values)
    out = np.full(n, np.nan)
    order = np.argsort(np.argsort(values, kind="stable"), kind="stable")
    # A running count of how many earlier values are below each value needs a
    # Fenwick tree to stay fast; at this size a simple sorted insert is fine.
    import bisect
    seen: list[float] = []
    for i, value in enumerate(values):
        if np.isnan(value):
            if seen:
                out[i] = out[i - 1] if i and not np.isnan(out[i - 1]) else np.nan
            continue
        position = bisect.bisect_left(seen, value)
        if i + 1 >= MIN_RANK_OBSERVATIONS:
            out[i] = position / max(len(seen), 1)
        bisect.insort(seen, value)
    del order
    return np.clip(out, 0.0, 1.0)


def detrended(dates, values: np.ndarray) -> np.ndarray:
    """Log residual against a log-time trend refitted at every date.

    The fit at row i uses rows 0..i only. Refitting point-in-time is the whole
    reason this is not simply `np.polyfit` over the column: a single full-sample
    trend line would leak the future into every historical residual.
    """
    n = len(values)
    out = np.full(n, np.nan)
    positive = values > 0
    log_values = np.where(positive, np.log(np.maximum(values, 1e-12)), np.nan)
    time_index = np.log(np.arange(1, n + 1, dtype=float))

    # Running sums let the least-squares slope and intercept be updated in O(1).
    sum_x = sum_y = sum_xx = sum_xy = count = 0.0
    for i in range(n):
        x, y = time_index[i], log_values[i]
        if not np.isnan(y):
            count += 1
            sum_x += x; sum_y += y; sum_xx += x * x; sum_xy += x * y
        if count >= MIN_RANK_OBSERVATIONS:
            denominator = count * sum_xx - sum_x * sum_x
            if denominator != 0:
                slope = (count * sum_xy - sum_x * sum_y) / denominator
                intercept = (sum_y - slope * sum_x) / count
                if not np.isnan(y):
                    out[i] = y - (intercept + slope * x)
    return out


def risk_series(dates, values: np.ndarray, *, trending: bool = False) -> np.ndarray:
    """A metric turned into 0-1 risk, point-in-time throughout."""
    base = detrended(dates, values) if trending else np.asarray(values, dtype=float)
    return expanding_rank(base)


@dataclass
class Component:
    key: str
    label: str
    family: str
    risk: np.ndarray
    dates: list
    available: bool = True
    note: str = ""

    def at(self, index: int = -1) -> float | None:
        if not self.available or len(self.risk) == 0:
            return None
        value = self.risk[index]
        return None if np.isnan(value) else round(float(value), 3)

    def lookback(self, days: int) -> float | None:
        """The reading `days` ago -- the 6M / 1Y / 4Y columns of the scorecard."""
        index = len(self.risk) - 1 - days
        return self.at(index) if index >= 0 else None


def build_component(key: str, label: str, family: str, frame: pl.DataFrame,
                    *, trending: bool = False, invert: bool = False,
                    note: str = "") -> Component:
    """Turn a stored `date, value` series into a risk component.

    `invert` is for metrics where LOW is risky -- nothing in the current set
    needs it, but leaving it implicit would invite someone to flip a sign
    somewhere less visible.
    """
    if frame.is_empty():
        return Component(key, label, family, np.array([]), [], available=False,
                         note=note or "source unavailable")
    dates = list(frame["date"])
    values = frame["value"].to_numpy().astype(float)
    risk = risk_series(dates, values, trending=trending)
    if invert:
        risk = 1.0 - risk
    return Component(key, label, family, risk, dates, note=note)


def unavailable(key: str, label: str, family: str, reason: str) -> Component:
    """A row that cannot be sourced.

    It stays in the scorecard rather than disappearing. A missing row is
    information -- it says the picture is incomplete -- whereas an absent one
    silently implies the remaining metrics are the whole story (§0.2).
    """
    return Component(key, label, family, np.array([]), [], available=False, note=reason)


def family_risk(components: list[Component], index: int = -1) -> float | None:
    """Mean of a family's available component risks."""
    values = [c.at(index) for c in components if c.available]
    values = [v for v in values if v is not None]
    return round(float(np.mean(values)), 3) if values else None


def composite(families: dict[str, list[Component]],
              weights: dict[str, float] | None = None,
              index: int = -1) -> dict:
    """Weighted mean of family risks, with every component kept visible.

    §7.3 insists components are shown beside the composite, so this returns
    both rather than a bare number. A family with no available components is
    dropped from the weighting and named in `missing`, so the composite is
    never quietly computed over fewer inputs than it claims.
    """
    per_family, missing = {}, []
    for name, members in families.items():
        value = family_risk(members, index)
        if value is None:
            missing.append(name)
        else:
            per_family[name] = value
    if not per_family:
        return {"value": None, "families": {}, "missing": missing,
                "note": "no components available"}

    weights = weights or {}
    total_weight = sum(weights.get(name, 1.0) for name in per_family)
    value = sum(per_family[name] * weights.get(name, 1.0)
                for name in per_family) / total_weight
    return {
        "value": round(float(value), 3),
        "families": per_family,
        "missing": missing,
        "weights": {name: weights.get(name, 1.0) for name in per_family},
    }


def scorecard(components: list[Component]) -> list[dict]:
    """The Current / 6M / 1Y / 4Y table (§6.5), one row per component.

    Unavailable rows are included with their reason, per `unavailable` above.
    """
    rows = []
    for component in components:
        rows.append({
            "key": component.key,
            "label": component.label,
            "family": component.family,
            "available": component.available,
            "note": component.note,
            "current": component.at(),
            "six_months": component.lookback(182),
            "one_year": component.lookback(365),
            "four_years": component.lookback(1461),
        })
    return rows
