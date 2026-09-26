"""Sector indices and the relative rotation graph (SPEC §6.8, §7.4).

The book's thesis is three to five sector rotations rather than a broad
altseason, so this page exists to make "which sector is turning up while cheap"
answerable.

**Indices are built two ways and they disagree usefully.** An equal-weight
sector index says what the average name in the sector did; a cap-weight one
says what the sector's money did. When a sector's cap-weight index rises and
its equal-weight index does not, one large name is carrying it — which is the
opposite of the broad participation a rotation needs.

**The RRG is an open approximation, not the JdK method.** §7.4 gives the
formulas to use and asks that the approximation is documented rather than
passed off as the proprietary construction:

    RS           = asset / benchmark * 100, weekly
    RS-Ratio     = 100 + z-score of RS over N weeks      (N = 10)
    RS-Momentum  = 100 + z-score of RS-Ratio's change over M weeks  (M = 4)

Quadrants follow from the two axes: leading (both above 100), weakening
(strong but losing momentum), lagging (both below), improving (weak but gaining).
The tail is the last few weeks, so rotation is visible as a path rather than a
point.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from pipeline import registry, store

RS_RATIO_WEEKS = 10
RS_MOMENTUM_WEEKS = 4
RRG_TAIL_WEEKS = 6     # long enough to show direction, short enough to read
MIN_WEEKS = RS_RATIO_WEEKS + RS_MOMENTUM_WEEKS + RRG_TAIL_WEEKS


def _weekly_closes(symbol: str) -> pl.DataFrame:
    """Weekly closes, excluding the week still running."""
    from pipeline.metrics import levels
    bars = store.read_ohlcv(symbol)
    if bars.is_empty():
        return pl.DataFrame()
    return levels.to_weekly(bars).select(["date", "close"])


def sector_members() -> dict[str, list[str]]:
    """Which assets make up each sector, from the registry."""
    out: dict[str, list[str]] = {}
    for asset in registry.tracked():
        if asset.kind == "book" and asset.sector:
            out.setdefault(asset.sector, []).append(asset.symbol)
    return out


def sector_indices(days: int = 400) -> dict:
    """Equal-weight and cap-weight indices per sector, rebased to 100.

    Both start at the first date every member of the sector has a price, so an
    index never jumps because a constituent appeared. Sectors whose members do
    not share enough history are reported as such rather than built from
    whoever happens to have data.
    """
    members = sector_members()
    caps = _latest_caps()
    out: dict[str, dict] = {}

    for sector, symbols in sorted(members.items()):
        frames = []
        for symbol in symbols:
            bars = store.read_ohlcv(symbol)
            if bars.height < 60:
                continue
            frames.append(bars.select(["date", pl.col("close").alias(symbol)]))
        if not frames:
            out[sector] = {"available": False, "members": symbols,
                           "reason": "no member has 60 days of price history"}
            continue

        joined = frames[0]
        for frame in frames[1:]:
            joined = joined.join(frame, on="date", how="inner")
        joined = joined.sort("date").tail(days)
        present = [c for c in joined.columns if c != "date"]
        if joined.height < 30:
            out[sector] = {
                "available": False, "members": symbols, "included": present,
                "reason": (f"members share only {joined.height} days of common "
                           f"history; an index over a shorter overlap would "
                           f"start when the newest listing did"),
            }
            continue

        matrix = joined.select(present).to_numpy().astype(float)
        base = matrix[0]
        rebased = matrix / base * 100.0

        equal = rebased.mean(axis=1)
        weights = np.array([caps.get(s, 0.0) for s in present], dtype=float)
        if weights.sum() > 0:
            weights = weights / weights.sum()
            cap_weighted = (rebased * weights).sum(axis=1)
        else:
            cap_weighted = equal

        dates = joined["date"].to_list()
        # The window is not the same for every sector, because each index starts
        # when its last constituent got a price -- unless that is further back
        # than the cap, in which case the cap is the start and saying "shared
        # history" would misdescribe it. Both the length and which of the two
        # reasons applies are emitted, because a return is meaningless without
        # the window it was measured over and the page ranks these side by side.
        span_days = (dates[-1] - dates[0]).days + 1
        capped = joined.height >= days
        out[sector] = {
            "available": True,
            "members": symbols,
            "included": present,
            "excluded": [s for s in symbols if s not in present],
            "start": str(dates[0]),
            "days": span_days,
            "start_capped": capped,
            "start_reason": (
                f"the {days}-day cap on how far back an index is drawn"
                if capped else
                "the first date every included member had a price"),
            "equal_weight": _thin(dates, equal),
            "cap_weight": _thin(dates, cap_weighted),
            "equal_return_pct": round(float(equal[-1] - 100), 1),
            "cap_return_pct": round(float(cap_weighted[-1] - 100), 1),
            "return_30d_pct": _window_return(equal, 30),
            "return_90d_pct": _window_return(equal, 90),
        }
    return out


def _thin(dates: list, values: np.ndarray) -> list[dict]:
    """Every other day, but never without the newest point.

    Plain slicing drops the last element on an even-length series, which put the
    chart's final point a day behind the return printed beside it.
    """
    points = [{"d": str(d), "v": round(float(v), 2)}
              for d, v in zip(dates, values, strict=False)]
    thinned = points[::2]
    if points and thinned[-1] is not points[-1]:
        thinned.append(points[-1])
    return thinned


def _window_return(series: np.ndarray, days: int) -> float | None:
    if len(series) <= days or series[-1 - days] == 0:
        return None
    return round(float((series[-1] / series[-1 - days] - 1) * 100), 1)


def _latest_caps() -> dict[str, float]:
    """Latest market cap per symbol, for the cap-weighted index."""
    snapshot = store.read_snapshot("supply")
    if snapshot.is_empty():
        return {}
    latest = (snapshot.sort("observed_at")
              .group_by("coingecko_id", maintain_order=True).last())
    by_id = dict(zip(latest["coingecko_id"].to_list(),
                     latest["market_cap"].to_list(), strict=False))
    return {a.symbol: by_id.get(a.coingecko_id) or 0.0
            for a in registry.tracked() if a.coingecko_id}


# ---------------------------------------------------------------------------
# RRG
# ---------------------------------------------------------------------------

def _zscore_tail(values: np.ndarray, window: int) -> np.ndarray:
    """Rolling z-score over `window`, one value per position.

    NaN-aware on purpose. An earlier version filled the leading NaNs of the
    momentum series with 0.0 before z-scoring, and 0 sits far outside the range
    of real week-on-week changes, so it dragged the mean and inflated the
    standard deviation for the first fourteen weeks. On the chart that produced
    tails that shot across the whole plot -- the rotation was invisible under
    the artefact.
    """
    out = np.full(len(values), np.nan)
    for i in range(window - 1, len(values)):
        segment = values[i - window + 1: i + 1]
        segment = segment[~np.isnan(segment)]
        if len(segment) < window or np.isnan(values[i]):
            continue
        sd = segment.std(ddof=0)
        if sd > 0:
            out[i] = (values[i] - segment.mean()) / sd
    return out


def _smooth(values: np.ndarray, window: int = 3) -> np.ndarray:
    """Centred-trailing mean, NaN-aware.

    Momentum is the z-score of a difference, which is noisy by construction; a
    short smoothing makes the rotation path readable without changing where a
    sector sits.
    """
    out = np.full(len(values), np.nan)
    for i in range(len(values)):
        segment = values[max(0, i - window + 1): i + 1]
        segment = segment[~np.isnan(segment)]
        if len(segment):
            out[i] = segment.mean()
    return out


def quadrant(ratio: float, momentum: float) -> str:
    if ratio >= 100 and momentum >= 100:
        return "leading"
    if ratio >= 100 and momentum < 100:
        return "weakening"
    if ratio < 100 and momentum < 100:
        return "lagging"
    return "improving"


def rrg(benchmark: str = "BTC", universe: str = "sector") -> dict:
    """Relative rotation of sectors (or assets) against a benchmark.

    Open approximation of the JdK method, per §7.4 — the real formulation is
    proprietary and this is not it.
    """
    bench = _weekly_closes(benchmark)
    if bench.height < MIN_WEEKS:
        return {"available": False,
                "reason": f"benchmark needs {MIN_WEEKS} weekly bars, has {bench.height}"}
    bench = bench.rename({"close": "bench"})

    series: dict[str, pl.DataFrame] = {}
    if universe == "sector":
        for sector, symbols in sector_members().items():
            frames = [
                _weekly_closes(s).rename({"close": s})
                for s in symbols if _weekly_closes(s).height >= MIN_WEEKS
            ]
            if not frames:
                continue
            joined = frames[0]
            for frame in frames[1:]:
                joined = joined.join(frame, on="date", how="inner")
            cols = [c for c in joined.columns if c != "date"]
            if joined.height < MIN_WEEKS or not cols:
                continue
            matrix = joined.select(cols).to_numpy().astype(float)
            index = (matrix / matrix[0] * 100).mean(axis=1)
            series[sector] = pl.DataFrame({"date": joined["date"], "close": index})
    else:
        for asset in registry.tracked():
            frame = _weekly_closes(asset.symbol)
            if frame.height >= MIN_WEEKS:
                series[asset.symbol] = frame

    points = []
    for name, frame in series.items():
        joined = bench.join(frame, on="date", how="inner").sort("date")
        if joined.height < MIN_WEEKS:
            continue
        rs = (joined["close"].to_numpy().astype(float)
              / joined["bench"].to_numpy().astype(float)) * 100.0

        rs_ratio = 100 + _zscore_tail(rs, RS_RATIO_WEEKS)

        # RS-Momentum: the z-score of RS-Ratio's M-week rate of change. The
        # leading positions stay NaN rather than being zero-filled -- see
        # _zscore_tail on why that mattered.
        change = np.full(len(rs_ratio), np.nan)
        change[RS_MOMENTUM_WEEKS:] = (
            rs_ratio[RS_MOMENTUM_WEEKS:] - rs_ratio[:-RS_MOMENTUM_WEEKS])
        rs_momentum = 100 + _smooth(_zscore_tail(change, RS_RATIO_WEEKS))
        rs_ratio = 100 + _smooth(rs_ratio - 100)

        tail = []
        dates = joined["date"].to_list()
        for i in range(max(0, len(rs) - RRG_TAIL_WEEKS), len(rs)):
            if np.isnan(rs_ratio[i]) or np.isnan(rs_momentum[i]):
                continue
            tail.append({"d": str(dates[i]),
                         "x": round(float(rs_ratio[i]), 2),
                         "y": round(float(rs_momentum[i]), 2)})
        if len(tail) < 2:
            continue
        points.append({
            "name": name,
            "tail": tail,
            "x": tail[-1]["x"], "y": tail[-1]["y"],
            "quadrant": quadrant(tail[-1]["x"], tail[-1]["y"]),
        })

    if not points:
        return {"available": False,
                "reason": f"no series has {MIN_WEEKS} weekly bars against {benchmark}"}

    return {
        "available": True,
        "benchmark": benchmark,
        "universe": universe,
        "ratio_weeks": RS_RATIO_WEEKS,
        "momentum_weeks": RS_MOMENTUM_WEEKS,
        "tail_weeks": RRG_TAIL_WEEKS,
        "points": sorted(points, key=lambda p: -p["x"]),
        "method_note": (
            "RS = asset / benchmark x 100 on weekly closes. RS-Ratio is 100 plus "
            f"the z-score of RS over {RS_RATIO_WEEKS} weeks; RS-Momentum is 100 "
            f"plus the z-score of RS-Ratio's {RS_MOMENTUM_WEEKS}-week change. "
            "This is an open approximation of the JdK method, not the "
            "proprietary formula."),
    }


def sector_fee_growth() -> list[dict]:
    """Sector fee growth, summed across members that have fee data."""
    from pipeline.metrics import valuation as V
    out = []
    for sector, symbols in sorted(sector_members().items()):
        total_recent = total_prior = 0.0
        covered = []
        for symbol in symbols:
            asset = registry.get(symbol)
            slug = asset.llama_parent or asset.llama_chain if asset else None
            if not slug:
                continue
            fees = store.read_fundamentals(slug, "dailyFees")
            if fees.is_empty():
                continue
            recent, _ = V._window_sum(fees, 90)
            end = fees["date"].max()
            import datetime as dt
            prior, _ = V._window_sum(fees, 90, end - dt.timedelta(days=90))
            if recent is None or prior is None:
                continue
            total_recent += recent
            total_prior += prior
            covered.append(symbol)
        if not covered or total_prior <= 0:
            out.append({"sector": sector, "available": False,
                        "reason": "no member has two comparable 90-day fee windows",
                        "members": symbols})
            continue
        out.append({
            "sector": sector, "available": True,
            "fees_90d": total_recent,
            "growth_pct": round((total_recent / total_prior - 1) * 100, 1),
            "covered": covered,
            "uncovered": [s for s in symbols if s not in covered],
        })
    return out
