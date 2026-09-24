"""The single entry point (SPEC §10).

    uv run terminal fetch [--only prices|onchain|all] [--symbols BTC,ETH]
    uv run terminal build
    uv run terminal validate
    uv run terminal probe

Every command is idempotent: running it twice on the same day produces the same
store and the same artefacts. `fetch` re-reads its raw cache rather than the
network, and `build` recomputes everything from the store rather than updating
in place.
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys

import numpy as np
import polars as pl

from pipeline import artefacts, health, paths, registry, store
from pipeline.fetchers import coinmetrics, prices, sentiment
from pipeline.metrics import cycle, risk
from pipeline.signals import quantile as quantile_model


def _log(message: str) -> None:
    print(message, flush=True)


def btc_full_series() -> pl.DataFrame:
    """BTC back to 2010, splicing CoinMetrics closes in front of exchange bars.

    Ordered oldest-source-first with `keep="first"` so a real exchange bar
    always wins over the close-only CoinMetrics row on any date both cover.
    """
    parts = []
    for venue in ("coinbase", "binance", "coinmetrics"):
        frame = store.read_ohlcv("BTC", venue)
        if not frame.is_empty():
            parts.append(frame.select(["date", "close"]))
    if not parts:
        return pl.DataFrame(schema={"date": pl.Date, "close": pl.Float64})
    return pl.concat(parts).unique(subset=["date"], keep="first").sort("date")


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def cmd_fetch(args) -> int:
    paths.ensure_dirs()
    only = args.only
    symbols = args.symbols.split(",") if args.symbols else None

    if only in ("all", "onchain"):
        _log("on-chain: coinmetrics btc + eth")
        coinmetrics.fetch_asset("btc")
        coinmetrics.derived_metrics("btc")
        coinmetrics.mvrv_zscore("btc")
        coinmetrics.fetch_asset("eth", ["PriceUSD", "SplyCur", "IssTotUSD",
                                        "CapMrktCurUSD", "CapMVRVCur"])
        coinmetrics.derived_metrics("eth")

    if only in ("all", "prices"):
        _log("prices: daily bars, every venue")
        result = prices.fetch_all("1d", dt.date(2017, 1, 1), symbols)
        got = sum(1 for v in result.values() if v)
        _log(f"  {got}/{len(result)} assets have bars")
        prices.backfill_btc_from_coinmetrics()

    if only in ("all", "sentiment"):
        _log("sentiment: fear & greed, wikipedia page views")
        result = sentiment.fetch_all()
        _log("  " + ", ".join(f"{k}={v}" for k, v in result.items()))

    if only in ("all", "hourly"):
        _log("prices: hourly bars for the CVD block")
        for asset in registry.tracked():
            if asset.binance_spot:
                prices.fetch(asset, "1h", dt.date.today() - dt.timedelta(days=120),
                             venue_override=("binance", asset.binance_spot))

    written = health.flush()
    _log(f"source_health: {written} rows")
    return 0


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def cmd_build(args) -> int:
    paths.ensure_dirs()
    started = dt.datetime.now(dt.UTC)

    _log("building asset artefacts")
    payloads: dict[str, dict] = {}
    for asset in registry.tracked():
        payload = artefacts.build_asset(asset)
        payloads[asset.symbol] = payload
        artefacts.write(f"assets/{asset.symbol}", payload)
    _log(f"  {len(payloads)} assets")

    _log("building home")
    artefacts.write("home", artefacts.build_home(payloads))

    _log("building btc cycle")
    artefacts.write("btc-cycle", build_btc_cycle())

    _log("building source health")
    artefacts.write("source-health", artefacts.build_source_health())

    index = {
        "built_at": started.isoformat(timespec="seconds"),
        "assets": sorted(payloads),
        "counts": {
            "assets": len(payloads),
            "scored": sum(1 for p in payloads.values()
                          if (p.get("regime") or {}).get("available")),
        },
        "weights_configured": registry.weights() is not None,
    }
    artefacts.write("index", index)
    _log(f"built {index['counts']['scored']}/{index['counts']['assets']} scored "
         f"-> {paths.ARTEFACTS}")
    return 0


def build_btc_cycle() -> dict:
    """The BTC cycle page: levels, analogs, quantile bands, risk scorecard."""
    series = btc_full_series()
    if series.is_empty():
        return {"available": False, "reason": "no BTC history in the store"}

    dates = list(series["date"])
    closes = series["close"].to_numpy().astype(float)
    cycles = cycle.find_cycles(dates, closes)

    payload: dict = {
        "as_of": str(dates[-1]),
        "available": True,
        "cycles": cycles,
        "current": cycle.current_position(dates, closes, cycles),
        "roi_from_peak": cycle.roi_from_peak(dates, closes, cycles),
        "roi_from_bottom": cycle.roi_from_bottom(dates, closes, cycles),
        "midterm_monthly": cycle.midterm_monthly(dates, closes),
        "ytd_roi": cycle.ytd_roi(dates, closes),
        "roi_convention": (
            "ROI here means a normalised PRICE RATIO, not a percentage return: "
            "0.52 from the peak means price sits at 52% of the peak, a 48% "
            "decline."),
    }

    # Quantile bands, gated on the replication check (§7.2).
    try:
        fitted = quantile_model.fit(dates, closes)
        report = quantile_model.replication_report(
            quantile_model.fit(dates, closes, through=dt.date(2026, 5, 29)))
        today_days = quantile_model.days_since_genesis([dates[-1]])[0]
        bands = fitted.predict(np.asarray([today_days]))
        payload["quantile"] = {
            "available": bool(report["passed"]),
            "replication": report,
            "mu": round(fitted.mu, 4),
            "curvature": {k: round(v, 4) for k, v in fitted.curvature.items()},
            "n": fitted.n,
            "fitted_through": fitted.fitted_through,
            "curvature_note": (
                "These come from the fit over the FULL sample, through "
                f"{fitted.fitted_through}. The replication gate below refits "
                "through 2026-05-29 to match the published window, so its "
                "figures differ slightly. Both are shown rather than one being "
                "quietly reused for the other."),
            "bands_today": {str(tau): round(float(bands[tau][0]), 2)
                            for tau in quantile_model.TAUS},
            "position_pct": round(fitted.position(today_days, float(closes[-1])), 2),
            "how_to_read": (
                "Where price sits in the conditional distribution of price LEVEL "
                "given time. These are NOT return forecasts and NOT tail-risk "
                "estimates. The 1% band is not a floor -- about 1% of history has "
                "closed beneath it. Lower-tail curvature is not statistically "
                "distinguishable from zero."),
            "gate_note": (
                "The bands ship only because the fit reproduces the published "
                "values of mu, b_hi, b_lo and n. Every check is shown above."),
        }
        if not report["passed"]:
            payload["quantile"]["reason"] = (
                "replication gate failed; bands withheld per SPEC §7.2")
    except Exception as exc:  # noqa: BLE001
        payload["quantile"] = {"available": False, "reason": f"fit failed: {exc}"}

    payload["scorecard"] = build_scorecard()
    return payload


def build_scorecard() -> dict:
    """The 0-1 risk scorecard (§6.5), including the rows we cannot source."""
    def onchain(metric: str) -> pl.DataFrame:
        return store.read_onchain("btc", metric)

    components = [
        risk.build_component("btc_price", "Bitcoin price", "Headline",
                             onchain("PriceUSD"), trending=True),
        risk.build_component("btc_cap", "Bitcoin market cap", "Headline",
                             onchain("CapMrktCurUSD"), trending=True),
        risk.build_component("roi_1yr", "365-day running ROI", "Headline",
                             onchain("ROI1yr")),
        risk.build_component("mvrv", "MVRV", "Valuation and on-chain",
                             onchain("CapMVRVCur")),
        risk.build_component("mvrv_z", "MVRV Z-Score", "Valuation and on-chain",
                             onchain("MVRVZScore")),
        risk.build_component("nupl", "Net unrealised profit/loss",
                             "Valuation and on-chain", onchain("NUPL")),
        risk.build_component("puell", "Puell multiple", "Valuation and on-chain",
                             onchain("PuellMultiple")),
        # Detrended: thermocap compounds monotonically, so the raw ratio falls
        # through history and an untrended percentile would mostly measure what
        # year it is rather than whether the market is stretched.
        risk.build_component("cap_thermo", "Market cap to thermocap",
                             "Valuation and on-chain",
                             onchain("MarketCapToThermocap"), trending=True),
        risk.build_component("fees", "Transaction fees", "Valuation and on-chain",
                             onchain("FeeTotNtv")),
        risk.build_component("hashrate", "Hash rate", "Network",
                             onchain("HashRate"), trending=True),
        risk.build_component("active_addr", "Active addresses", "Network",
                             onchain("AdrActCnt"), trending=True),
        # Rows that cannot be sourced free. They stay visible: a missing row
        # says the picture is incomplete, an absent one implies it is complete.
        risk.unavailable("rhodl", "RHODL ratio", "Valuation and on-chain",
                         "needs UTXO age bands; not on the CoinMetrics free tier"),
        risk.unavailable("supply_pl", "Supply in profit / loss",
                         "Valuation and on-chain",
                         "needs per-UTXO cost basis; not on the free tier"),
        risk.unavailable("nvt", "NVT signal", "Valuation and on-chain",
                         "needs transfer VALUE; the free tier serves only counts"),
        risk.build_component("fear_greed", "Fear and greed", "Sentiment",
                             store.read_onchain("sentiment", "FearGreed")),
        risk.build_component("wiki_btc", "Wikipedia page views (Bitcoin)",
                             "Sentiment", store.read_onchain("sentiment", "WikiViews_btc"),
                             note="attention proxy; low-conviction on its own"),
    ]

    families: dict[str, list] = {}
    for component in components:
        families.setdefault(component.family, []).append(component)

    return {
        "rows": risk.scorecard(components),
        "composite": risk.composite(families),
        "how_to_read": (
            "Each row is the metric's own history expressed as a 0-1 percentile, "
            "computed on an expanding window so no reading ever used data from "
            "after its own date. Near 0 is historically low risk, near 1 is "
            "froth. Rows marked unavailable are listed rather than dropped, "
            "because a shorter list would imply a completeness we do not have."),
        "caveat": (
            "Percentiles of a series with roughly four completed cycles are "
            "descriptive, not predictive. Treat the columns as 'where this sat "
            "then' rather than as an estimate of what follows."),
    }


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def cmd_validate(args) -> int:
    """The honesty report (after Nelson-Siegel's `signals.validate`).

    Prints what is measured, with sample sizes, and says plainly where there is
    no measured edge. Numbers live here rather than in prose so the docs can
    cite the command instead of repeating figures that will later be wrong.
    """
    print("MiniLizard terminal — validation\n" + "=" * 62)

    print("\nSTORE COVERAGE")
    symbols = store.known_symbols()
    print(f"  {len(symbols)} symbols with bars")
    short = []
    for symbol in symbols:
        venues = store.venues(symbol)
        binance = store.read_ohlcv(symbol, "binance").height if "binance" in venues else 0
        longest = store.read_ohlcv(symbol).height
        if binance < 300:
            short.append((symbol, binance, longest, venues[0] if venues else "-"))
    print(f"  {len(short)} cannot be scored on Binance, the venue parity is defined on:")
    for symbol, binance, longest, venue in short:
        print(f"    {symbol:<8} binance={binance:<5} longest={longest:<5} ({venue})")

    print("\nQUANTILE REPLICATION (SPEC §7.2)")
    series = btc_full_series()
    if series.height < 5000:
        print("  skipped: BTC history too short")
    else:
        fitted = quantile_model.fit(list(series["date"]),
                                    series["close"].to_numpy(),
                                    through=dt.date(2026, 5, 29))
        report = quantile_model.replication_report(fitted)
        print(f"  gate: {'PASS' if report['passed'] else 'FAIL'}")
        for key, check in report["checks"].items():
            mark = "ok  " if check["passed"] else "FAIL"
            print(f"    {mark} {key:<5} expected {check['expected']:>9} "
                  f"observed {check['observed']:>9}  diff {check['difference']:>8}")

    print("\nMINILIZARD PARITY (SPEC §7.1)")
    golden = paths.ROOT / "tests" / "golden" / "minilizard_parity.yaml"
    import yaml
    cases = (yaml.safe_load(golden.read_text()).get("cases") or []) if golden.exists() else []
    if not cases:
        print("  UNVERIFIED — no TradingView reference values supplied.")
        print("  The engine follows §7.1's prose; the Pine source it names as")
        print("  ground truth is not in the repository. No parity claim is made.")
    else:
        print(f"  {len(cases)} golden cases present")

    print("\nMEASURED EDGE")
    print("  No backtest has been run in this repository yet, so no win rate,")
    print("  profit factor or drawdown figure is quoted anywhere on the site.")
    print("  SPEC §7.1 reports the engine as a DRAWDOWN FILTER rather than an")
    print("  alpha source; that framing is carried on the asset pages.")

    print("\nSOURCE HEALTH")
    latest = health.latest_by_source()
    if latest.is_empty():
        print("  no fetches recorded")
    else:
        counts = latest.group_by("status").len().sort("len", descending=True)
        for row in counts.iter_rows(named=True):
            print(f"  {row['status']:<14}{row['len']:>4}")
        stale = health.failing_for_hours(24)
        if stale.is_empty():
            print("  nothing failing beyond its expected lag")
        else:
            print(f"  {stale.height} source(s) with no success in 24h:")
            for row in stale.iter_rows(named=True):
                print(f"    {row['source']}/{row['dataset']}: {(row['error'] or '')[:60]}")
        keyed = latest.filter(pl.col("status") == "needs_key")
        if not keyed.is_empty():
            print(f"  {keyed.height} source(s) waiting on a key or a paid tier "
                  f"(known, not an outage)")

    print("\nCAVEATS THAT DO NOT GO AWAY")
    print("  - Roughly four completed BTC cycles exist. Any statement about")
    print("    'what happens at this point in the cycle' rests on that sample.")
    print("  - Cycle boundaries are detected from price, so they are approximate.")
    print("  - Quantile bands describe the distribution of price LEVEL given")
    print("    time. They are not forecasts and the 1% band is not a floor.")
    return 0


def cmd_probe(args) -> int:
    return subprocess.call([sys.executable, str(paths.ROOT / "tools" / "probe.py"),
                            *(args.rest or [])])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="terminal", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="pull sources into the store")
    fetch.add_argument("--only", default="all",
                       choices=["all", "prices", "onchain", "sentiment", "hourly"])
    fetch.add_argument("--symbols", default="")
    fetch.set_defaults(func=cmd_fetch)

    build = sub.add_parser("build", help="recompute artefacts from the store")
    build.set_defaults(func=cmd_build)

    validate = sub.add_parser("validate", help="the honesty report")
    validate.set_defaults(func=cmd_validate)

    probe = sub.add_parser("probe", help="run the source probe")
    probe.add_argument("rest", nargs=argparse.REMAINDER)
    probe.set_defaults(func=cmd_probe)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
