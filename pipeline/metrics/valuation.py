"""The equity lens (SPEC §6.4): multiples, dilution, yield, growth, reverse DCF.

Tokens are not equities, but several equity concepts map cleanly and the brief
sets out the mapping precisely. This module implements those definitions and
nothing else — where a quantity cannot be sourced it returns None with a
reason, never a zero.

**Annualisation is always labelled**, because the same "P/E" computed on 30
days and on a trailing year are different numbers and the difference is often
larger than the gap between two assets. Three bases are produced — 30d, 90d and
trailing 365d — and the default shown is 90d, per the brief.

**The headline is net holder yield**: holders' revenue yield minus the rate at
which supply is growing. Positive means the token is being bought back faster
than it is diluted. It is the one number that cannot be gamed by looking only
at the flattering half of the ledger, which is why a protocol with real revenue
and heavy emissions still reads negative here.

Three states a metric can be in, and they are NOT the same:

    a number        measured
    None + reason   not sourceable (e.g. treasury is behind DefiLlama's paywall)
    0.0             genuinely measured as zero

Five names — AAVE, FLUID, MORPHO, ONDO, CFG — have years of fee history and
holders' revenue of exactly zero. That is the third case, and collapsing it
into the second would misdescribe a protocol that simply does not pay holders.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import polars as pl

from pipeline import registry, store

# Windows and their annualisation multipliers, per §6.4.
BASES = {"30d": (30, 365 / 30), "90d": (90, 365 / 90), "365d": (365, 1.0)}
DEFAULT_BASIS = "90d"

# A multiple above this is not information, it is a near-zero denominator.
MAX_MEANINGFUL_MULTIPLE = 100_000.0


@dataclass
class Value:
    """A number, or a stated reason there isn't one."""
    value: float | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.value is not None

    def as_dict(self) -> dict:
        return {"v": None if self.value is None else round(float(self.value), 6),
                "reason": self.reason or None}


def missing(reason: str) -> Value:
    return Value(None, reason)


def _window_sum(frame: pl.DataFrame, days: int, end: dt.date | None = None
                ) -> tuple[float | None, int]:
    """Sum of the last `days` of a daily series, and how many days it had.

    Returns None when the series covers less than 80% of the window: summing
    40 days and annualising as though they were 90 understates by more than
    half, and doing it silently is worse than not doing it.
    """
    if frame.is_empty():
        return None, 0
    end = end or frame["date"].max()
    start = end - dt.timedelta(days=days - 1)
    window = frame.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    if window.height < days * 0.8:
        return None, window.height
    return float(window["value"].sum()), window.height


def annualised(frame: pl.DataFrame, basis: str) -> Value:
    """A daily flow series annualised on one of the three bases."""
    days, multiplier = BASES[basis]
    total, have = _window_sum(frame, days)
    if total is None:
        return missing(f"needs {days} days of history, has {have}")
    return Value(total * multiplier)


def ratio(numerator: float | None, denominator: Value | float | None,
          *, allow_negative: bool = False) -> Value:
    """A multiple, with the degenerate cases named rather than printed.

    A P/E on near-zero earnings is not a large number, it is not a number, and
    printing 4,000,000x invites reading it as "expensive" rather than as
    "undefined".
    """
    if isinstance(denominator, Value):
        if not denominator.ok:
            return missing(denominator.reason)
        denominator = denominator.value
    if numerator is None or denominator is None:
        return missing("missing input")
    if denominator == 0:
        return missing("denominator is zero")
    if denominator < 0 and not allow_negative:
        return missing("n/m — denominator is negative")
    value = numerator / denominator
    if abs(value) > MAX_MEANINGFUL_MULTIPLE:
        return missing("n/m — denominator is near zero")
    return Value(value)


# ---------------------------------------------------------------------------
# Supply and dilution
# ---------------------------------------------------------------------------

SPIKE_TOLERANCE = 0.03      # a day-on-day supply move beyond this is not issuance
# Deviation from the local median past which a point is an artefact, not
# issuance. The fastest diluter in the book (PUMP, ~60%/yr) adds 0.13% a day,
# so 5% is two orders of magnitude above anything real. It has to be this tight
# because the FDV spikes land exactly ON max supply rather than above it --
# AAVE's 16,000,000 is only 5.3% above its neighbours and survived a 10% test,
# which put +14.7% annualised dilution on a token that grew 1.2% that year.
OUTLIER_TOLERANCE = 0.05
ENDPOINT_WINDOW = 7         # days either side of an endpoint to take a median over


def clean_supply(supply: pl.DataFrame, total_supply: float | None) -> tuple[pl.DataFrame, dict]:
    """Drop the points that cannot be circulating supply, before measuring it.

    Two filters, in order of how defensible they are:

    1. **Circulating cannot exceed total.** When CoinGecko reports the fully
       diluted cap for a day, implied supply lands on exactly the max supply —
       VIRTUAL at 1,000,000,000, AAVE at 16,000,000. That is arithmetically
       impossible as a circulating figure, so it is removed rather than
       smoothed. A 7-day median does NOT rescue this case, because the spike
       sometimes persists for several days: AAVE sat at max supply across the
       window that a 90-day measurement happened to start in, which produced
       +14.7% annualised dilution on a token whose supply grew 1.2% that year.

    2. **Isolated outliers against a centred rolling median.** For names with
       no max supply the first filter cannot fire, so anything more than 10%
       away from the local median is dropped as an artefact of the two rounded
       legs the series is derived from.

    Returns the cleaned frame and a count of what went, so the page can say how
    much of the series survived rather than implying it was clean all along.
    """
    if supply.is_empty():
        return supply, {"dropped_over_total": 0, "dropped_outlier": 0, "kept": 0}

    values = supply["value"].to_numpy().astype(float)
    keep = np.ones(len(values), dtype=bool)

    over_total = 0
    if total_supply and total_supply > 0:
        impossible = values > total_supply * 1.005
        over_total = int(impossible.sum())
        keep &= ~impossible

    # Centred rolling median, computed on what survived the first filter.
    outliers = 0
    if keep.sum() > 15:
        surviving = np.where(keep, values, np.nan)
        half = 7
        for i in range(len(values)):
            if not keep[i]:
                continue
            window = surviving[max(0, i - half): i + half + 1]
            window = window[~np.isnan(window)]
            if len(window) < 5:
                continue
            local = float(np.median(window))
            if local > 0 and abs(values[i] / local - 1) > OUTLIER_TOLERANCE:
                keep[i] = False
                outliers += 1

    cleaned = supply.filter(pl.Series("keep", keep))
    return cleaned, {"dropped_over_total": over_total,
                     "dropped_outlier": outliers, "kept": int(keep.sum())}


def supply_quality(supply: pl.DataFrame) -> dict:
    """Describe an implied supply series, and separate artefacts from events.

    The distinction matters and an earlier version got it backwards. A large
    one-day move in implied supply is one of two things:

    * **A transient spike that reverts.** CoinGecko reported the fully diluted
      cap for a day or two; implied supply jumps to max supply and returns.
      This is an artefact and `clean_supply` removes it.
    * **A persistent step.** Supply moves and stays moved. MORPHO steps +50%
      on 2025-10-10, ONDO +54% on 2026-01-18. These are **real unlock events**,
      not errors — they are the single most important thing a holder wants to
      know about, and §6.4 asks for exactly this under "unlock load".

    Marking the second kind low-confidence, as this function first did, buried
    the signal as though it were noise. Persistent steps are now reported as
    observed supply events. They are also the only unlock data available at
    all: DefiLlama's emissions endpoint is 402 on the free tier (D007), so an
    event inferred from supply is what we have.

    Confidence is about the SERIES, not about the events in it: it degrades
    when points had to be discarded or the history is short.
    """
    if supply.height < 30:
        return {"days": int(supply.height), "events": [], "confidence": "none",
                "note": "too little supply history to judge"}

    values = supply["value"].to_numpy()
    dates = supply["date"].to_list()
    changes = np.diff(values) / np.where(values[:-1] == 0, np.nan, values[:-1])

    events = []
    for i, change in enumerate(changes):
        if np.isnan(change) or abs(change) <= SPIKE_TOLERANCE:
            continue
        # A round trip is judged on the levels either SIDE of the excursion,
        # not on the value the move started from. Comparing against the start
        # value only catches the outbound leg: on the way back down, the
        # "before" value IS the spike, so the return looks like a fresh event
        # and one artefact gets reported as two.
        before = values[max(0, i - 3): i + 1]
        after = values[i + 2: i + 6]
        if len(before) and len(after):
            baseline, resumed = float(np.median(before)), float(np.median(after))
            if baseline > 0 and abs(resumed / baseline - 1) <= SPIKE_TOLERANCE:
                continue      # round trip: an artefact, not a supply event
        events.append({
            "date": str(dates[i + 1]),
            "change_pct": round(float(change * 100), 2),
            "kind": "unlock or issuance" if change > 0 else "burn or buyback",
        })

    return {"days": int(supply.height), "events": events[:8],
            "event_count": len(events), "confidence": "high"}


def _robust_level(supply: pl.DataFrame, on: dt.date) -> float | None:
    """Supply around a date, as the median of a window rather than one point.

    A single day is exactly what the FDV spikes corrupt; a seven-day median
    ignores a one- or two-day excursion entirely while tracking a real trend.
    """
    low = on - dt.timedelta(days=ENDPOINT_WINDOW // 2)
    high = on + dt.timedelta(days=ENDPOINT_WINDOW // 2)
    window = supply.filter((pl.col("date") >= low) & (pl.col("date") <= high))
    if window.is_empty():
        earlier = supply.filter(pl.col("date") <= on)
        if earlier.is_empty():
            return None
        window = earlier.tail(ENDPOINT_WINDOW)
    return float(np.median(window["value"].to_numpy()))


def net_dilution(supply: pl.DataFrame, days: int) -> Value:
    """Annualised rate of change in circulating supply over `days`.

    Measured between MEDIANS of seven-day windows at each endpoint, not between
    single days. Both legs of the implied series are rounded, and CoinGecko's
    cap occasionally reports fully-diluted for a day, so a point-to-point read
    is dominated by artefacts: it gave VIRTUAL -138% annualised, which is not a
    number supply can produce.

    Positive means supply grew — dilution. §6.4 asks for the split into
    unlocks, emissions and burns "where the data allows"; it does not allow
    here, because the implied series shows only the net, so the split is
    omitted rather than guessed at.
    """
    if supply.is_empty():
        return missing("no supply history")
    end = supply["date"].max()
    start = end - dt.timedelta(days=days)
    # Allow the window to begin on the first stored day: with exactly N days
    # held, min(date) is end - (N-1), which a strict test rejects forever.
    if supply["date"].min() > start + dt.timedelta(days=ENDPOINT_WINDOW):
        return missing(f"needs {days} days of supply history")

    first = _robust_level(supply, start)
    last = _robust_level(supply, end)
    if not first or not last or first <= 0:
        return missing("supply history has no usable endpoint")

    change = last / first - 1
    annual = change * (365.0 / days)
    # Supply cannot shrink faster than it exists, and nothing in this book
    # emits more than a few hundred percent a year. Beyond that the series is
    # describing a restatement, not issuance.
    if annual < -1.0 or annual > 5.0:
        return missing(f"implausible ({annual * 100:.0f}% annualised) — the "
                       f"supply series has a restatement in this window")
    return Value(annual)


def float_pct(circulating: float | None, total: float | None) -> Value:
    if not circulating or not total or total <= 0:
        return missing("no total supply")
    return Value(circulating / total * 100.0)


# ---------------------------------------------------------------------------
# Growth
# ---------------------------------------------------------------------------

def growth(frame: pl.DataFrame, days: int) -> Value:
    """This window against the one before it, as a rate of change."""
    if frame.is_empty():
        return missing("no series")
    end = frame["date"].max()
    recent, have_recent = _window_sum(frame, days, end)
    prior_end = end - dt.timedelta(days=days)
    prior, have_prior = _window_sum(frame, days, prior_end)
    if recent is None or prior is None:
        return missing(f"needs {days * 2} days, has {have_recent + have_prior}")
    if prior <= 0:
        return missing("prior period was zero or negative")
    return Value(recent / prior - 1)


# ---------------------------------------------------------------------------
# Reverse DCF and payback
# ---------------------------------------------------------------------------

DISCOUNT_RATES = (0.15, 0.25, 0.35)
EXIT_MULTIPLES = (10, 20, 30)


def implied_growth(market_cap: float, holders_revenue: Value,
                   discount: float, exit_multiple: float) -> Value:
    """The 5-year holders'-revenue CAGR that justifies today's price.

    Solve for g in:  MC = (E0 * (1+g)^5 * exit) / (1+r)^5

    It tells you what the price already assumes, which is a more useful
    question than what the asset is "worth". Undefined when holders' revenue is
    zero or negative, which is 9 of 21 book names — the grid says so per name
    instead of printing a very large number.
    """
    if not holders_revenue.ok:
        return missing(holders_revenue.reason)
    earnings = holders_revenue.value
    if earnings is None or earnings <= 0:
        return missing("not computable — holders' revenue is zero or negative")
    target = market_cap * (1 + discount) ** 5 / (earnings * exit_multiple)
    if target <= 0:
        return missing("no solution")
    return Value(target ** (1 / 5) - 1)


def payback_years(market_cap: float, holders_revenue: Value,
                  growth_rate: float | None, decay: float = 0.8,
                  horizon: int = 50) -> Value:
    """Years until cumulative holders' revenue equals market cap.

    Growth decays toward zero each year (default 20% decay), because
    extrapolating a protocol's current growth for fifty years is not a
    forecast, it is an arithmetic accident.
    """
    if not holders_revenue.ok:
        return missing(holders_revenue.reason)
    earnings = holders_revenue.value
    if earnings is None or earnings <= 0:
        return missing("not computable — holders' revenue is zero or negative")
    rate = max(min(growth_rate or 0.0, 2.0), -0.5)
    cumulative, annual = 0.0, earnings
    for year in range(1, horizon + 1):
        cumulative += annual
        if cumulative >= market_cap:
            return Value(float(year))
        annual *= 1 + rate
        rate *= decay
    return missing(f"beyond {horizon} years at current growth")


# ---------------------------------------------------------------------------
# Data quality grade
# ---------------------------------------------------------------------------

GRADE_NOTES = {
    "A": "fees, revenue, holders' revenue and supply all sourced and cross-checked",
    "B": "fees, revenue and holders' revenue sourced; one input thin",
    "C": "partial — some inputs missing",
    "D": "measured, and value capture is zero",
    "F": "unmeasurable — no revenue data of any kind",
}


def grade(has_fees: bool, has_revenue: bool, has_holders: bool,
          holders_is_zero: bool, has_supply: bool, cross_checked: bool) -> str:
    """A-F, plus the D the brief does not have but the data demands.

    The brief's scale runs A (fully sourced and cross-checked) to F (no revenue
    data). Probing found a case that is neither: AAVE, FLUID, MORPHO, ONDO and
    CFG have years of fee and revenue history and holders' revenue of exactly
    zero. Grading that F would call a name with 2,121 days of data
    "unmeasurable", which is a lie about the data rather than a judgement about
    the asset.

    So D means "measured, capture is zero". Those names still get a net holder
    yield, and it is negative, because dilution is real.
    """
    if not has_fees and not has_revenue:
        return "F"
    if has_holders and holders_is_zero:
        return "D"
    if has_fees and has_revenue and has_holders and has_supply and cross_checked:
        return "A"
    if has_fees and has_revenue and has_holders:
        return "B"
    return "C"


# ---------------------------------------------------------------------------
# The per-asset computation
# ---------------------------------------------------------------------------

@dataclass
class AssetValuation:
    symbol: str
    name: str
    sector: str
    tier: int | None
    kind: str
    is_chain: bool
    grade: str
    grade_note: str
    market_cap: float | None = None
    fdv: float | None = None
    price: float | None = None
    circulating: float | None = None
    total_supply: float | None = None
    rank: int | None = None
    volume_24h: float | None = None
    metrics: dict = field(default_factory=dict)
    flows: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "name": self.name, "sector": self.sector,
            "tier": self.tier, "kind": self.kind, "is_chain": self.is_chain,
            "grade": self.grade, "grade_note": self.grade_note,
            "market_cap": self.market_cap, "fdv": self.fdv, "price": self.price,
            "circulating": self.circulating, "total_supply": self.total_supply,
            "rank": self.rank, "volume_24h": self.volume_24h,
            "metrics": self.metrics, "flows": self.flows, "notes": self.notes,
        }


def _latest_market(markets: pl.DataFrame, coingecko_id: str) -> dict | None:
    if markets.is_empty():
        return None
    row = markets.filter(pl.col("coingecko_id") == coingecko_id)
    return row.to_dicts()[0] if row.height else None


def compute(asset: registry.Asset, markets: pl.DataFrame,
            basis: str = DEFAULT_BASIS) -> AssetValuation:
    """Everything §6.4 asks for, for one asset."""
    slug = asset.llama_parent or asset.llama_chain
    is_chain = bool(asset.llama_chain and not asset.llama_parent)

    fees_series = store.read_fundamentals(slug, "dailyFees") if slug else pl.DataFrame()
    revenue_series = store.read_fundamentals(slug, "dailyRevenue") if slug else pl.DataFrame()
    holders_series = store.read_fundamentals(slug, "dailyHoldersRevenue") if slug else pl.DataFrame()
    tvl_series = store.read_fundamentals(asset.llama_parent, "tvl") if asset.llama_parent else pl.DataFrame()
    supply = store.read_onchain(f"cg_{asset.coingecko_id}", "SupplyImplied")

    market = _latest_market(markets, asset.coingecko_id) or {}
    market_cap = market.get("market_cap")
    fdv = market.get("fully_diluted_valuation")
    circulating = market.get("circulating_supply")
    total_supply = market.get("total_supply")

    # Zero capture is judged on the CURRENT window, not on all history. Aave
    # distributed to holders at some point in 2,123 days, so an all-history sum
    # is non-zero and would grade it A while today's capture is flatly zero.
    holders_now = annualised(holders_series, basis)
    holders_zero = (not holders_series.is_empty()
                    and holders_now.ok and holders_now.value == 0.0)
    asset_grade = asset.data_quality_grade or grade(
        has_fees=not fees_series.is_empty(),
        has_revenue=not revenue_series.is_empty(),
        has_holders=not holders_series.is_empty(),
        holders_is_zero=holders_zero,
        has_supply=not supply.is_empty(),
        cross_checked=bool(market_cap),
    )

    out = AssetValuation(
        symbol=asset.symbol, name=asset.name, sector=asset.sector,
        tier=asset.tier, kind=asset.kind, is_chain=is_chain,
        grade=asset_grade, grade_note=GRADE_NOTES.get(asset_grade, ""),
        market_cap=market_cap, fdv=fdv, price=market.get("price_usd"),
        circulating=circulating, total_supply=total_supply,
        rank=market.get("rank"), volume_24h=market.get("volume_24h"),
    )

    if asset_grade == "F":
        out.notes.append(
            "No revenue data of any kind for this asset, so no multiple is "
            "shown. The brief holds these deliberately as an unmeasurable "
            "venture tail; a multiple here would be invented, not computed.")

    # Annualised flows on all three bases, so the page can switch without a
    # rebuild and the basis is never ambiguous.
    for label in BASES:
        out.flows[label] = {
            "fees": annualised(fees_series, label).as_dict(),
            "revenue": annualised(revenue_series, label).as_dict(),
            "holders_revenue": annualised(holders_series, label).as_dict(),
        }

    fees_a = annualised(fees_series, basis)
    revenue_a = annualised(revenue_series, basis)
    holders_a = holders_now

    metrics: dict = {"basis": basis}
    metrics["p_fees"] = ratio(market_cap, fees_a).as_dict()
    metrics["p_revenue"] = ratio(market_cap, revenue_a).as_dict()
    metrics["p_holders_revenue"] = ratio(market_cap, holders_a).as_dict()
    metrics["fdv_p_fees"] = ratio(fdv, fees_a).as_dict()
    metrics["fdv_p_revenue"] = ratio(fdv, revenue_a).as_dict()

    if is_chain:
        metrics["p_holders_revenue"] = missing(
            "a chain has no protocol treasury to distribute; §6.4 puts chains "
            "on P/F rather than P/E").as_dict()

    # Dilution and yield — the headline.
    supply, cleaning = clean_supply(supply, total_supply or market.get("max_supply"))
    quality = supply_quality(supply)
    quality.update(cleaning)
    dropped = cleaning["dropped_over_total"] + cleaning["dropped_outlier"]
    if quality.get("confidence") == "high" and dropped > 10:
        quality["confidence"] = "medium"
        quality["note"] = f"{dropped} days discarded as artefacts before measuring"
    metrics["supply_quality"] = quality
    if cleaning["dropped_over_total"]:
        out.notes.append(
            f"{cleaning['dropped_over_total']} day(s) of the supply series "
            f"exceeded total supply and were dropped — CoinGecko reported the "
            f"fully diluted market cap on those days, which is not a "
            f"circulating figure.")
    if quality.get("event_count"):
        biggest = max(quality["events"], key=lambda e: abs(e["change_pct"]))
        out.notes.append(
            f"{quality['event_count']} discrete supply event(s) in the last "
            f"year, the largest {biggest['change_pct']:+.1f}% on "
            f"{biggest['date']} ({biggest['kind']}). These are inferred from "
            f"circulating supply, and are the only unlock data available: "
            f"DefiLlama's emissions endpoint is paid.")

    dilution = net_dilution(supply, 90)
    dilution_365 = net_dilution(supply, 365)
    metrics["net_dilution_90d"] = dilution.as_dict()
    metrics["net_dilution_365d"] = dilution_365.as_dict()

    holder_yield = (Value(holders_a.value / market_cap)
                    if holders_a.ok and market_cap else
                    missing(holders_a.reason or "no market cap"))
    metrics["holder_yield"] = holder_yield.as_dict()

    if holder_yield.ok and dilution.ok:
        metrics["net_holder_yield"] = Value(
            holder_yield.value - dilution.value).as_dict()
    else:
        metrics["net_holder_yield"] = missing(
            holder_yield.reason or dilution.reason).as_dict()

    # Token incentives: §6.4 estimates them as the USD value of new supply.
    # Only the NET change is observable from the implied series, so what this
    # measures is net issuance in dollars, which is named as such.
    if dilution.ok and market_cap:
        metrics["incentives_usd"] = Value(dilution.value * market_cap).as_dict()
        if holders_a.ok:
            earnings = holders_a.value - dilution.value * market_cap
            metrics["adjusted_earnings"] = Value(earnings).as_dict()
            metrics["dilution_adjusted_pe"] = ratio(market_cap, earnings).as_dict()
    else:
        metrics["incentives_usd"] = missing(dilution.reason).as_dict()
        metrics["adjusted_earnings"] = missing(dilution.reason).as_dict()
        metrics["dilution_adjusted_pe"] = missing(dilution.reason).as_dict()

    # Overhang. An annualised dilution rate says nothing about whether it can
    # continue: AAVE genuinely issued 3.6% of supply in 90 days, which
    # annualises to +14.7%, but only 570k tokens remain below its 16m cap, so
    # that rate has about a quarter of a year left in it. Showing the rate
    # without the headroom invites reading a terminal event as a trend.
    if dilution.ok and circulating and total_supply and dilution.value > 0:
        remaining = max(total_supply - circulating, 0.0)
        annual_issuance = circulating * dilution.value
        if remaining <= total_supply * 0.001:
            # Already fully circulating: there is no cap left to run into, so
            # "0 years of headroom" would read as imminent danger when it means
            # the opposite.
            metrics["dilution_headroom_years"] = missing(
                "fully circulating — no supply left to unlock").as_dict()
        elif annual_issuance > 0:
            metrics["dilution_headroom_years"] = Value(
                remaining / annual_issuance).as_dict()
        else:
            metrics["dilution_headroom_years"] = missing("no issuance").as_dict()
    else:
        metrics["dilution_headroom_years"] = missing(
            "only meaningful when supply is growing toward a cap").as_dict()

    metrics["float_pct"] = float_pct(circulating, total_supply).as_dict()
    metrics["fdv_mc"] = (ratio(fdv, market_cap).as_dict() if fdv and market_cap
                         else missing("no FDV").as_dict())

    # Growth and PEG
    revenue_growth = growth(revenue_series, 90)
    metrics["revenue_growth_30d"] = growth(revenue_series, 30).as_dict()
    metrics["revenue_growth_90d"] = revenue_growth.as_dict()
    metrics["fees_growth_90d"] = growth(fees_series, 90).as_dict()

    pe = ratio(market_cap, holders_a)
    if pe.ok and revenue_growth.ok and revenue_growth.value and revenue_growth.value > 0:
        metrics["peg"] = Value(pe.value / (revenue_growth.value * 100)).as_dict()
    else:
        metrics["peg"] = missing(
            "needs a positive P/E and positive growth").as_dict()

    # MC/TVL — the P/B analogue, DeFi only.
    if not tvl_series.is_empty():
        latest_tvl = float(tvl_series["value"][-1])
        metrics["mc_tvl"] = ratio(market_cap, latest_tvl).as_dict()
        metrics["tvl"] = Value(latest_tvl).as_dict()
    else:
        reason = ("chains do not have protocol TVL" if is_chain
                  else "no TVL series for this protocol")
        metrics["mc_tvl"] = missing(reason).as_dict()
        metrics["tvl"] = missing(reason).as_dict()

    # Treasury and runway are behind DefiLlama's paywall (D007).
    metrics["ev"] = missing(
        "needs treasury data; DefiLlama's /treasuries is 402 on the free tier").as_dict()
    metrics["runway"] = missing("needs treasury data (402)").as_dict()

    # Reverse DCF grid and payback
    if market_cap:
        grid = {}
        for discount in DISCOUNT_RATES:
            for multiple in EXIT_MULTIPLES:
                grid[f"r{int(discount * 100)}_x{multiple}"] = implied_growth(
                    market_cap, holders_a, discount, multiple).as_dict()
        metrics["reverse_dcf"] = grid
        metrics["payback_years"] = payback_years(
            market_cap, holders_a,
            revenue_growth.value if revenue_growth.ok else None).as_dict()
    else:
        metrics["reverse_dcf"] = {}
        metrics["payback_years"] = missing("no market cap").as_dict()

    out.metrics = metrics
    return out


# ---------------------------------------------------------------------------
# Cross-sectional: sector medians and own-history percentiles
# ---------------------------------------------------------------------------

def sector_medians(valuations: list[AssetValuation], keys: list[str]) -> dict:
    """Median of each metric within each sector, for the relative-value column."""
    out: dict[str, dict[str, float]] = {}
    by_sector: dict[str, list[AssetValuation]] = {}
    for item in valuations:
        if item.sector:
            by_sector.setdefault(item.sector, []).append(item)

    for sector, members in by_sector.items():
        row: dict[str, float] = {}
        for key in keys:
            values = [m.metrics.get(key, {}).get("v") for m in members]
            values = [v for v in values if v is not None]
            if values:
                row[key] = round(float(np.median(values)), 6)
        out[sector] = row
    return out


def own_history_percentile(symbol: str, coingecko_id: str, slug: str | None,
                           basis: str = DEFAULT_BASIS) -> dict:
    """Where today's P/Fees sits in its own trailing-year distribution.

    Computed point-in-time: at each date the multiple uses that date's market
    cap and the fee window ENDING that date, so no reading uses a fee that had
    not been reported yet. §6.4 asks for percentile and z-score; both are over
    a year, which is roughly 250 observations — enough to rank against, not
    enough to call a distribution.
    """
    if not slug:
        return {"available": False, "reason": "no protocol or chain slug"}
    caps = store.read_onchain(f"cg_{coingecko_id}", "MarketCap")
    fees_series = store.read_fundamentals(slug, "dailyFees")
    if caps.is_empty() or fees_series.is_empty():
        return {"available": False, "reason": "needs market cap and fee history"}

    days, multiplier = BASES[basis]
    fee_map = dict(zip(fees_series["date"].to_list(),
                       fees_series["value"].to_list(), strict=False))
    dates = caps["date"].to_list()
    cap_values = caps["value"].to_list()

    series: list[float] = []
    for day, cap in zip(dates, cap_values, strict=False):
        window = [fee_map.get(day - dt.timedelta(days=i)) for i in range(days)]
        present = [v for v in window if v is not None]
        if len(present) < days * 0.8 or not cap:
            continue
        annual = sum(present) * multiplier
        if annual > 0:
            series.append(cap / annual)

    if len(series) < 60:
        return {"available": False,
                "reason": f"only {len(series)} comparable days in the last year"}

    values = np.array(series)
    current = values[-1]
    percentile = float((values < current).mean() * 100)
    sd = float(values.std())
    z = float((current - values.mean()) / sd) if sd > 0 else 0.0
    return {
        "available": True, "metric": "p_fees", "basis": basis,
        "current": round(float(current), 4),
        "percentile": round(percentile, 1),
        "zscore": round(z, 2),
        "n": len(values),
        "median": round(float(np.median(values)), 4),
    }
