"""Shared series helpers for the artefact layer.

One function, and it exists because the same defect was written four times in
four modules: thinning a chart series with a plain slice.
"""

from __future__ import annotations


def keep_newest(points: list, step: int) -> list:
    """Every `step`-th point, but never without the newest one.

    `points[::step]` keeps index 0, step, 2*step, ... and so drops the last
    element unless `len(points) - 1` happens to be a multiple of `step`. The
    newest point is the one the panel prints beside the chart, so dropping it
    puts the line behind the number next to it -- by up to `step - 1` days,
    silently, and only on some rebuilds.

    It was found once on the 400-day sector charts with `[::2]` and fixed
    locally; the audit found it again in `breadth.stablecoin_trend` with
    `[::3]`, where the panel printed the 2026-09-26 supply and the chart ended
    2026-09-24, and twice more in the cycle ROI series with `[::2]` and
    `[::4]`. Hence one implementation rather than four.
    """
    if not points:
        return []
    thinned = points[::step]
    if thinned[-1] is not points[-1]:
        thinned.append(points[-1])
    return thinned
