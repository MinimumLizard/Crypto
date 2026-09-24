# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Phase-0 source probe: does each candidate source in SPEC §5.1 actually work?

    uv run tools/probe.py                 # everything
    uv run tools/probe.py --only defillama macro
    uv run tools/probe.py --out docs/probe

Every endpoint, field name, rate limit and history limit in the brief came
from memory and may be stale, so nothing is built on a source until this has
hit it live and written down what came back. For each probe it records:
reachability, the HTTP status, whether auth was required, any rate-limit
headers, how far back the free tier's history goes, whether a browser could
call it directly (CORS), and the shape of the payload.

Deliberately dependency-light (httpx + stdlib, PEP 723 inline metadata) so
the identical file runs here and on a GitHub-hosted runner with no project
install. It writes probe.json (machine-readable, diffable between locations)
and probe.md (readable).

WHERE IT RUNS MATTERS. Binance.com and Bybit answer 451/403 from US egress,
which is where GitHub-hosted runners live. The `--location` label is stamped
into the output so a runner's results can be diffed against a home machine's.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import time
import datetime as dt
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

import httpx

UA = ("MiniLizardTerminal/0.1 (source probe; "
      "+https://github.com/MinimumLizard/Crypto; contact via repo issues)")
TIMEOUT = 30.0

# Rate-limit headers worth capturing, lowercased. Different vendors, same idea.
RATE_HEADERS = (
    "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
    "x-mbx-used-weight", "x-mbx-used-weight-1m", "ratelimit-limit",
    "ratelimit-remaining", "retry-after", "x-ratelimit-limit-minute",
    "x-ratelimit-limit-month", "x-cg-demo-api-key-limit",
)


@dataclass
class Spec:
    """One endpoint to test."""
    name: str
    group: str
    dataset: str          # what this source would FEED, not what it is
    url: str
    method: str = "GET"
    params: dict | None = None
    json_body: dict | None = None
    headers: dict = field(default_factory=dict)
    auth_env: str | None = None   # env var holding a key, if the source needs one
    auth_style: str = ""          # how the key is passed, for the write-up
    auth_optional: bool = False   # works without a key, just at a lower ceiling
    note: str = ""
    # Pulls the interesting facts out of a successful payload: history depth,
    # row counts, field names. Returns a small dict that lands in the report.
    summarise: Callable[[Any], dict] | None = None


@dataclass
class Result:
    name: str
    group: str
    dataset: str
    url: str
    status: str            # ok | http_error | auth_required | network_error | skipped
    http_code: int | None = None
    latency_ms: int | None = None
    bytes: int | None = None
    auth_required: bool = False
    auth_env: str | None = None
    auth_style: str = ""
    rate_limit: dict = field(default_factory=dict)
    cors: str | None = None        # access-control-allow-origin, or None
    summary: dict = field(default_factory=dict)
    note: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Summarisers. Each answers "how much history, and what shape?" for one source.
# They must never raise: a summariser blowing up would report a live source as
# broken, which is the one error that matters here.
# ---------------------------------------------------------------------------

def _iso(ms: float) -> str:
    try:
        return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date().isoformat()
    except Exception:
        return "?"


def _safe(fn):
    def wrapper(payload):
        try:
            return fn(payload)
        except Exception as exc:  # noqa: BLE001 - reporting tool, never crash
            return {"summarise_error": f"{type(exc).__name__}: {exc}"}
    return wrapper


@_safe
def sum_binance_klines(p):
    return {"rows": len(p), "first_bar": _iso(p[0][0]), "last_bar": _iso(p[-1][0]),
            "fields": "openTime,o,h,l,c,volume,closeTime,quoteVol,trades,takerBase,takerQuote"}


@_safe
def sum_coinbase_candles(p):
    ts = sorted(r[0] for r in p)
    return {"rows": len(p), "first_bar": _iso(ts[0] * 1000), "last_bar": _iso(ts[-1] * 1000),
            "fields": "time,low,high,open,close,volume", "max_per_call": 300}


@_safe
def sum_hl_candles(p):
    return {"rows": len(p), "first_bar": _iso(p[0]["t"]), "last_bar": _iso(p[-1]["t"]),
            "fields": ",".join(p[0].keys())}


@_safe
def sum_kraken_ohlc(p):
    key = next(k for k in p["result"] if k != "last")
    rows = p["result"][key]
    return {"rows": len(rows), "first_bar": _iso(rows[0][0] * 1000),
            "last_bar": _iso(rows[-1][0] * 1000), "pair_key": key}


@_safe
def sum_cm_timeseries(p):
    data = p.get("data", [])
    return {"rows": len(data), "first": data[0]["time"][:10] if data else None,
            "last": data[-1]["time"][:10] if data else None,
            "fields": ",".join(data[0].keys()) if data else "",
            "next_page": bool(p.get("next_page_token")),
            "note": "paged; full history needs next_page_token walking"}


@_safe
def sum_cm_catalog(p):
    data = p.get("data", [])
    btc = next((a for a in data if a.get("asset") == "btc"), None)
    metrics = btc.get("metrics", []) if btc else []
    names = [m.get("metric") for m in metrics]
    wanted = ["PriceUSD", "CapMrktCurUSD", "CapRealUSD", "CapMVRVCur", "SplyCur",
              "IssTotUSD", "FeeTotUSD", "RevUSD", "HashRate", "AdrActCnt",
              "TxTfrValAdjUSD", "NVTAdj"]
    return {"assets": len(data), "btc_metrics": len(names),
            "wanted_present": sorted(w for w in wanted if w in names),
            "wanted_missing": sorted(w for w in wanted if w not in names)}


@_safe
def sum_llama_fees(p):
    tc = p.get("totalDataChart") or []
    return {"name": p.get("name"), "rows": len(tc),
            "first": _iso(tc[0][0] * 1000) if tc else None,
            "last": _iso(tc[-1][0] * 1000) if tc else None,
            "total24h": p.get("total24h"), "total30d": p.get("total30d"),
            "keys": ",".join(sorted(k for k in p if not k.startswith("_"))[:14])}


@_safe
def sum_llama_protocols(p):
    return {"protocols": len(p), "fields": ",".join(list(p[0].keys())[:16])}


@_safe
def sum_llama_tvl(p):
    tvl = p.get("tvl") or []
    return {"name": p.get("name"), "rows": len(tvl),
            "first": _iso(tvl[0]["date"] * 1000) if tvl else None,
            "last": _iso(tvl[-1]["date"] * 1000) if tvl else None}


@_safe
def sum_llama_stablecoins(p):
    coins = p.get("peggedAssets", p if isinstance(p, list) else [])
    return {"assets": len(coins),
            "fields": ",".join(list(coins[0].keys())[:12]) if coins else ""}


@_safe
def sum_llama_yields(p):
    d = p.get("data", [])
    return {"pools": len(d), "fields": ",".join(list(d[0].keys())[:14]) if d else ""}


@_safe
def sum_llama_hacks(p):
    rows = p if isinstance(p, list) else p.get("data", [])
    return {"incidents": len(rows),
            "fields": ",".join(list(rows[0].keys())[:12]) if rows else ""}


@_safe
def sum_hl_meta_ctxs(p):
    universe = p[0]["universe"]
    ctxs = p[1]
    ex = {k: ctxs[0].get(k) for k in list(ctxs[0].keys())}
    return {"perps": len(universe), "ctx_fields": ",".join(ex.keys()),
            "first_coin": universe[0]["name"]}


@_safe
def sum_hl_predicted(p):
    venues = sorted({v[0] for row in p for v in row[1]})
    return {"coins": len(p), "venues": venues,
            "example": json.dumps(p[0])[:220]}


@_safe
def sum_hl_funding_history(p):
    return {"rows": len(p), "fields": ",".join(p[0].keys()) if p else "",
            "first": _iso(p[0]["time"]) if p else None}


@_safe
def sum_hl_l2(p):
    levels = p.get("levels", [])
    return {"sides": len(levels), "bid_levels": len(levels[0]) if levels else 0,
            "ask_levels": len(levels[1]) if len(levels) > 1 else 0,
            "level_fields": ",".join(levels[0][0].keys()) if levels and levels[0] else ""}


@_safe
def sum_hl_spot_meta(p):
    return {"spot_pairs": len(p[0].get("universe", [])),
            "tokens": len(p[0].get("tokens", []))}


@_safe
def sum_deribit_book(p):
    rows = p.get("result", [])
    return {"instruments": len(rows),
            "fields": ",".join(list(rows[0].keys())[:18]) if rows else ""}


@_safe
def sum_deribit_dvol(p):
    rows = p.get("result", {}).get("data", [])
    return {"rows": len(rows), "first": _iso(rows[0][0]) if rows else None,
            "last": _iso(rows[-1][0]) if rows else None, "fields": "t,o,h,l,c"}


@_safe
def sum_okx_instruments(p):
    d = p.get("data", [])
    return {"instruments": len(d), "example": d[0].get("instId") if d else None}


@_safe
def sum_coingecko_markets(p):
    return {"rows": len(p), "fields": ",".join(list(p[0].keys())[:18]) if p else ""}


@_safe
def sum_coingecko_ping(p):
    return {"payload": json.dumps(p)[:120]}


@_safe
def sum_fng(p):
    d = p.get("data", [])
    first = dt.datetime.fromtimestamp(int(d[-1]["timestamp"]), dt.timezone.utc).date().isoformat() if d else None
    return {"rows": len(d), "earliest": first, "latest_value": d[0]["value"] if d else None,
            "fields": ",".join(d[0].keys()) if d else ""}


@_safe
def sum_mempool_fees(p):
    return {"fields": ",".join(p.keys()), "sample": json.dumps(p)[:160]}


@_safe
def sum_text_head(p):
    text = p if isinstance(p, str) else json.dumps(p)
    lines = text.splitlines()
    return {"lines": len(lines), "head": lines[0][:200] if lines else "",
            "second": lines[1][:200] if len(lines) > 1 else ""}


@_safe
def sum_gdelt(p):
    series = p.get("timeline", [])
    pts = series[0].get("data", []) if series else []
    return {"series": len(series), "points": len(pts),
            "first": pts[0].get("date") if pts else None,
            "last": pts[-1].get("date") if pts else None}


@_safe
def sum_polymarket(p):
    rows = p if isinstance(p, list) else p.get("data", [])
    return {"markets": len(rows),
            "fields": ",".join(list(rows[0].keys())[:16]) if rows else "",
            "example_q": (rows[0].get("question") or "")[:110] if rows else ""}


@_safe
def sum_kalshi(p):
    rows = p.get("markets", [])
    return {"markets": len(rows),
            "fields": ",".join(list(rows[0].keys())[:16]) if rows else "",
            "example": rows[0].get("ticker") if rows else ""}


@_safe
def sum_snapshot(p):
    d = (p.get("data") or {}).get("proposals") or []
    return {"proposals": len(d), "fields": ",".join(d[0].keys()) if d else "",
            "errors": json.dumps(p.get("errors"))[:200] if p.get("errors") else ""}


@_safe
def sum_rss(p):
    text = p if isinstance(p, str) else ""
    import re
    items = len(re.findall(r"<item[ >]|<entry[ >]", text))
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S)
    return {"items": items, "feed_title": (m.group(1)[:90] if m else "")}


@_safe
def sum_wikimedia(p):
    items = p.get("items", [])
    return {"rows": len(items), "first": items[0]["timestamp"][:8] if items else None,
            "last": items[-1]["timestamp"][:8] if items else None,
            "views_last": items[-1]["views"] if items else None}


@_safe
def sum_upbit(p):
    return {"rows": len(p), "trade_price": p[0].get("trade_price") if p else None}


@_safe
def sum_growthepie(p):
    rows = p.get("data", p if isinstance(p, list) else [])
    return {"type": type(p).__name__, "top_keys": ",".join(list(p.keys())[:12]) if isinstance(p, dict) else "",
            "rows": len(rows) if isinstance(rows, list) else "n/a"}


@_safe
def sum_beaconcha(p):
    d = p.get("data", {})
    return {"fields": ",".join(list(d.keys())[:16]) if isinstance(d, dict) else "",
            "status": p.get("status")}


@_safe
def sum_json_shape(p):
    if isinstance(p, dict):
        return {"type": "dict", "keys": ",".join(list(p.keys())[:20])}
    if isinstance(p, list):
        inner = p[0] if p else None
        return {"type": "list", "rows": len(p),
                "item_keys": ",".join(list(inner.keys())[:18]) if isinstance(inner, dict) else str(type(inner).__name__)}
    return {"type": type(p).__name__}


# ---------------------------------------------------------------------------
# The probe list. Grouped by the page/dataset each one would feed.
# ---------------------------------------------------------------------------

HL = "https://api.hyperliquid.xyz/info"
LLAMA = "https://api.llama.fi"
CM = "https://community-api.coinmetrics.io/v4"

SPECS: list[Spec] = [
    # ---- Spot OHLCV -------------------------------------------------------
    Spec("binance.vision klines 1d", "ohlcv", "Spot OHLCV (MiniLizard parity)",
         "https://data-api.binance.vision/api/v3/klines",
         params={"symbol": "BTCUSDT", "interval": "1d", "limit": "1000"},
         summarise=sum_binance_klines,
         note="Market-data-only host. The parity source: MiniLizard is defined on Binance bars."),
    Spec("binance.vision klines 1h", "ohlcv", "1H bars for the CVD block (§7.1)",
         "https://data-api.binance.vision/api/v3/klines",
         params={"symbol": "BTCUSDT", "interval": "1h", "limit": "1000"},
         summarise=sum_binance_klines),
    Spec("binance.vision exchangeInfo", "ohlcv", "Which spot pairs exist",
         "https://data-api.binance.vision/api/v3/exchangeInfo",
         params={"symbols": '["BTCUSDT","SOLUSDT","AAVEUSDT","UNIUSDT"]'},
         summarise=sum_json_shape),
    Spec("binance.com klines (geoblock test)", "ohlcv", "Control: is .com blocked here?",
         "https://api.binance.com/api/v3/klines",
         params={"symbol": "BTCUSDT", "interval": "1d", "limit": "5"},
         summarise=sum_binance_klines,
         note="Expected 451 from US egress. Proves whether the .vision workaround is needed."),
    Spec("Coinbase Exchange candles", "ohlcv", "Spot OHLCV fallback + Coinbase premium",
         "https://api.exchange.coinbase.com/products/BTC-USD/candles",
         params={"granularity": "86400"}, summarise=sum_coinbase_candles),
    Spec("Hyperliquid candleSnapshot", "ohlcv", "Perp/spot OHLCV for HL-only names",
         HL, method="POST",
         json_body={"type": "candleSnapshot",
                    "req": {"coin": "BTC", "interval": "1d",
                            "startTime": 1704067200000, "endTime": 1758672000000}},
         summarise=sum_hl_candles),
    Spec("Kraken OHLC", "ohlcv", "Spot OHLCV fallback",
         "https://api.kraken.com/0/public/OHLC",
         params={"pair": "XBTUSD", "interval": "1440"}, summarise=sum_kraken_ohlc),
    Spec("CoinMetrics PriceUSD (BTC, 2010-)", "ohlcv", "BTC long history back to 2010",
         f"{CM}/timeseries/asset-metrics",
         params={"assets": "btc", "metrics": "PriceUSD", "frequency": "1d",
                 "start_time": "2010-07-01", "page_size": "10000"},
         summarise=sum_cm_timeseries,
         note="Needed for the §7.2 quantile fit, which starts in 2010."),

    # ---- Market caps / supply --------------------------------------------
    Spec("CoinGecko ping (no key)", "marketcap", "Auth shape check",
         "https://api.coingecko.com/api/v3/ping", summarise=sum_coingecko_ping),
    Spec("CoinGecko /coins/markets", "marketcap", "Caps, supply, rank, volume",
         "https://api.coingecko.com/api/v3/coins/markets",
         params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": "100",
                 "page": "1", "sparkline": "false"},
         auth_env="COINGECKO_DEMO_KEY", auth_style="x-cg-demo-api-key header",
         auth_optional=True, summarise=sum_coingecko_markets,
         note="Works unauthenticated at low rates; Demo key raises the ceiling."),
    Spec("DefiLlama coins/prices", "marketcap", "Cross-check price source",
         "https://coins.llama.fi/prices/current/coingecko:bitcoin,coingecko:solana",
         summarise=sum_json_shape),
    Spec("CoinMarketCap (key required)", "marketcap", "Second cap source for the 10% cross-check",
         "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest",
         params={"limit": "10"}, auth_env="CMC_API_KEY",
         auth_style="X-CMC_PRO_API_KEY header", summarise=sum_json_shape,
         note="Basic plan. The RAY cap disagreement is exactly what this is for."),

    # ---- DefiLlama fundamentals ------------------------------------------
    Spec("Llama fees: aave dailyFees", "defillama", "Fees (P/F numerator)",
         f"{LLAMA}/summary/fees/aave", params={"dataType": "dailyFees"},
         summarise=sum_llama_fees),
    Spec("Llama fees: aave dailyRevenue", "defillama", "Revenue (P/S)",
         f"{LLAMA}/summary/fees/aave", params={"dataType": "dailyRevenue"},
         summarise=sum_llama_fees),
    Spec("Llama fees: aave dailyHoldersRevenue", "defillama", "Holders' revenue (P/E, the headline)",
         f"{LLAMA}/summary/fees/aave", params={"dataType": "dailyHoldersRevenue"},
         summarise=sum_llama_fees),
    Spec("Llama fees: hyperliquid holdersRevenue", "defillama", "HYPE Assistance Fund buybacks",
         f"{LLAMA}/summary/fees/hyperliquid", params={"dataType": "dailyHoldersRevenue"},
         summarise=sum_llama_fees),
    Spec("Llama fees: sky holdersRevenue", "defillama", "SKY holders' revenue",
         f"{LLAMA}/summary/fees/sky", params={"dataType": "dailyHoldersRevenue"},
         summarise=sum_llama_fees),
    Spec("Llama overview/fees", "defillama", "Cross-sectional fee screen",
         f"{LLAMA}/overview/fees",
         params={"excludeTotalDataChart": "true", "excludeTotalDataChartBreakdown": "true"},
         summarise=sum_json_shape),
    Spec("Llama summary/dexs/uniswap", "defillama", "DEX volume KPI",
         f"{LLAMA}/summary/dexs/uniswap", summarise=sum_llama_fees),
    Spec("Llama protocol/aave TVL", "defillama", "MC/TVL (P/B analogue)",
         f"{LLAMA}/protocol/aave", summarise=sum_llama_tvl),
    Spec("Llama protocols list", "defillama", "Slug resolution for the registry",
         f"{LLAMA}/protocols", summarise=sum_llama_protocols),
    Spec("Llama treasuries", "defillama", "EV-analogue (MC − treasury)",
         f"{LLAMA}/treasuries", summarise=sum_json_shape),
    Spec("Llama stablecoins", "defillama", "Stablecoin supply by chain",
         "https://stablecoins.llama.fi/stablecoins", params={"includePrices": "true"},
         summarise=sum_llama_stablecoins),
    Spec("Llama yields pools", "defillama", "Staking / real yield",
         "https://yields.llama.fi/pools", summarise=sum_llama_yields),
    Spec("Llama emissions (unlocks)", "defillama", "Unlock schedule — Pro-only?",
         f"{LLAMA}/emissions", summarise=sum_json_shape,
         note="If this is Pro-gated, §6.3 falls back to a hand-maintained config/unlocks.yaml."),
    Spec("Llama emission/aave", "defillama", "Per-protocol unlock schedule",
         f"{LLAMA}/emission/aave", summarise=sum_json_shape),
    Spec("Llama fees: pump (parent) holdersRevenue", "defillama", "PUMP buybacks",
         f"{LLAMA}/summary/fees/pump", params={"dataType": "dailyHoldersRevenue"},
         summarise=sum_llama_fees,
         note="Parent slug is 'pump' (pump.fun + pumpswap), not 'pump-fun'."),
    Spec("Llama fees: morpho (parent) holdersRevenue", "defillama", "MORPHO fee switch",
         f"{LLAMA}/summary/fees/morpho", params={"dataType": "dailyHoldersRevenue"},
         summarise=sum_llama_fees),
    Spec("Llama fees: bittensor (control, expect 400)", "defillama",
         "Confirms TAO has no fee data -> grade F",
         f"{LLAMA}/summary/fees/bittensor", params={"dataType": "dailyHoldersRevenue"},
         summarise=sum_llama_fees),
    Spec("Llama hacks", "defillama", "Security incident feed",
         f"{LLAMA}/hacks", summarise=sum_llama_hacks),

    # ---- BTC / ETH on-chain ----------------------------------------------
    Spec("CoinMetrics catalog (community tier)", "onchain", "Which metrics are actually free",
         f"{CM}/catalog/assets", params={"assets": "btc"}, summarise=sum_cm_catalog,
         note="Confirms community availability of MVRV, realised cap, hashrate etc."),
    Spec("CoinMetrics BTC on-chain bundle", "onchain", "MVRV / realised cap / hashrate",
         f"{CM}/timeseries/asset-metrics",
         params={"assets": "btc",
                 "metrics": "CapMrktCurUSD,CapMVRVCur,SplyCur,IssTotUSD,FeeTotNtv,HashRate,AdrActCnt,FlowInExUSD,SplyExUSD,ROI1yr",
                 "frequency": "1d", "start_time": "2024-01-01", "page_size": "100"},
         summarise=sum_cm_timeseries),
    Spec("CoinMetrics paywalled metrics (control)", "onchain",
         "Confirms which §5.1 metrics the community tier withholds",
         f"{CM}/timeseries/asset-metrics",
         params={"assets": "btc", "metrics": "CapRealUSD,RevUSD,TxTfrValAdjUSD,NVTAdj",
                 "frequency": "1d", "start_time": "2025-01-01", "page_size": "10"},
         summarise=sum_cm_timeseries,
         note="Expected 403. Realised cap and NVT are NOT free; see SOURCES.md for what is derivable instead."),
    Spec("CoinMetrics ETH supply", "onchain", "ETH net issuance",
         f"{CM}/timeseries/asset-metrics",
         params={"assets": "eth", "metrics": "SplyCur,IssTotUSD", "frequency": "1d",
                 "start_time": "2025-01-01", "page_size": "100"},
         summarise=sum_cm_timeseries),
    Spec("mempool.space fees", "onchain", "BTC fee pressure",
         "https://mempool.space/api/v1/fees/recommended", summarise=sum_mempool_fees),
    Spec("mempool.space block tip", "onchain", "Halving countdown",
         "https://mempool.space/api/blocks/tip/height", summarise=sum_text_head),
    Spec("mempool.space hashrate", "onchain", "Hash ribbons / difficulty",
         "https://mempool.space/api/v1/mining/hashrate/1y", summarise=sum_json_shape),
    Spec("beaconcha.in epoch", "onchain", "ETH staking ratio",
         "https://beaconcha.in/api/v1/epoch/latest", summarise=sum_beaconcha,
         note="Free tier is heavily rate-limited; may need a key."),
    Spec("growthepie fundamentals", "onchain", "L2 + blob fees",
         "https://api.growthepie.xyz/v1/fundamentals.json", summarise=sum_growthepie),
    Spec("Etherscan gas oracle", "onchain", "Gas-threshold check (§6.12)",
         "https://api.etherscan.io/api",
         params={"module": "gastracker", "action": "gasoracle"},
         auth_env="ETHERSCAN_API_KEY", auth_style="apikey query param",
         auth_optional=True, summarise=sum_json_shape),

    # ---- Derivatives ------------------------------------------------------
    Spec("HL metaAndAssetCtxs", "derivs", "Funding, OI, mark, premium, volume",
         HL, method="POST", json_body={"type": "metaAndAssetCtxs"},
         summarise=sum_hl_meta_ctxs),
    Spec("HL predictedFundings", "derivs", "HL + Binance + Bybit funding in one call",
         HL, method="POST", json_body={"type": "predictedFundings"},
         summarise=sum_hl_predicted,
         note="The documented way round the Binance/Bybit US geoblock."),
    Spec("HL fundingHistory", "derivs", "Funding z-scores need history",
         HL, method="POST",
         json_body={"type": "fundingHistory", "coin": "BTC", "startTime": 1748736000000},
         summarise=sum_hl_funding_history),
    Spec("HL l2Book", "derivs", "Depth at ±1% / ±2%, slippage",
         HL, method="POST", json_body={"type": "l2Book", "coin": "BTC"},
         summarise=sum_hl_l2),
    Spec("HL spotMetaAndAssetCtxs", "derivs", "HL spot names",
         HL, method="POST", json_body={"type": "spotMetaAndAssetCtxs"},
         summarise=sum_hl_spot_meta),
    Spec("Deribit book summary (BTC options)", "derivs", "Options OI, IV, put/call, skew",
         "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
         params={"currency": "BTC", "kind": "option"}, summarise=sum_deribit_book),
    Spec("Deribit DVOL history", "derivs", "BTC implied vol index",
         "https://www.deribit.com/api/v2/public/get_volatility_index_data",
         params={"currency": "BTC", "start_timestamp": "1735689600000",
                 "end_timestamp": "1758672000000", "resolution": "43200"},
         summarise=sum_deribit_dvol),
    Spec("Deribit futures (basis)", "derivs", "Annualised futures basis",
         "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
         params={"currency": "BTC", "kind": "future"}, summarise=sum_deribit_book),
    Spec("OKX instruments (perp availability)", "derivs", "Perp availability matrix",
         "https://www.okx.com/api/v5/public/instruments",
         params={"instType": "SWAP"}, summarise=sum_okx_instruments),
    Spec("Bybit tickers (geoblock test)", "derivs", "Control: is Bybit blocked here?",
         "https://api.bybit.com/v5/market/tickers", params={"category": "linear"},
         summarise=sum_json_shape, note="Expected 403 from US egress."),

    # ---- Macro ------------------------------------------------------------
    Spec("FRED series (key required)", "macro", "WALCL/RRP/DGS10/… the whole macro page",
         "https://api.stlouisfed.org/fred/series/observations",
         params={"series_id": "WALCL", "file_type": "json"},
         auth_env="FRED_API_KEY", auth_style="api_key query param",
         summarise=sum_json_shape),
    Spec("FRED releases/dates (key required)", "macro", "CPI/PCE/NFP calendar",
         "https://api.stlouisfed.org/fred/releases/dates",
         params={"file_type": "json"}, auth_env="FRED_API_KEY",
         auth_style="api_key query param", summarise=sum_json_shape),
    Spec("Stooq CSV (^SPX)", "macro", "Equities/DXY/gold fallback, no key",
         "https://stooq.com/q/d/l/", params={"s": "^spx", "i": "d"},
         summarise=sum_text_head),
    Spec("Stooq CSV (gold XAUUSD)", "macro", "Gold — no longer on FRED",
         "https://stooq.com/q/d/l/", params={"s": "xauusd", "i": "d"},
         summarise=sum_text_head),
    Spec("Stooq CSV over http (control)", "macro", "Does Stooq answer at all from here?",
         "http://stooq.com/q/d/l/", params={"s": "^spx", "i": "d"},
         summarise=sum_text_head,
         note="HTTPS was refused outright from US cloud egress; testing the plain-http path."),
    Spec("Yahoo chart API (^GSPC)", "macro", "yfinance's actual backend",
         "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC",
         params={"range": "1y", "interval": "1d"}, summarise=sum_json_shape,
         note="Unofficial. Rate-limited on shared runner IPs; Stooq is the fallback."),
    Spec("Yahoo chart API (^MOVE)", "macro", "MOVE index",
         "https://query1.finance.yahoo.com/v8/finance/chart/%5EMOVE",
         params={"range": "1y", "interval": "1d"}, summarise=sum_json_shape),

    # ---- Sentiment / attention -------------------------------------------
    Spec("alternative.me Fear & Greed", "sentiment", "F&G full history",
         "https://api.alternative.me/fng/", params={"limit": "0"}, summarise=sum_fng),
    Spec("Wikimedia pageviews (Bitcoin)", "sentiment", "Attention proxy",
         "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/Bitcoin/daily/20240101/20260920",
         summarise=sum_wikimedia, note="Requires a descriptive User-Agent or it 403s."),
    Spec("Upbit KRW-BTC (kimchi premium)", "sentiment", "Korean retail premium",
         "https://api.upbit.com/v1/ticker", params={"markets": "KRW-BTC"},
         summarise=sum_upbit),

    # ---- ETFs -------------------------------------------------------------
    Spec("Farside BTC ETF flows", "etf", "Spot ETF flows (scrape)",
         "https://farside.co.uk/bitcoin-etf-flow-all-data/", summarise=sum_text_head,
         note="HTML scrape, best-effort, brittle by nature."),

    # ---- Geopolitics / policy --------------------------------------------
    Spec("GDELT timelinevolraw", "geo", "Theme article volume",
         "https://api.gdeltproject.org/api/v2/doc/doc",
         params={"query": '"Strait of Hormuz"', "mode": "timelinevolraw",
                 "format": "json", "timespan": "3m"}, summarise=sum_gdelt),
    Spec("GDELT timelinetone", "geo", "Theme tone",
         "https://api.gdeltproject.org/api/v2/doc/doc",
         params={"query": '"Strait of Hormuz"', "mode": "timelinetone",
                 "format": "json", "timespan": "3m"}, summarise=sum_gdelt),
    Spec("GPR index (Caldara-Iacoviello)", "geo", "Geopolitical Risk index, daily",
         "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls",
         summarise=sum_text_head, note="URL changes between revisions; verify shape."),
    Spec("Polymarket Gamma markets", "geo", "Event odds",
         "https://gamma-api.polymarket.com/markets",
         params={"closed": "false", "limit": "20", "order": "volumeNum", "ascending": "false"},
         summarise=sum_polymarket),
    Spec("Kalshi markets", "geo", "Event odds (US-regulated)",
         "https://api.elections.kalshi.com/trade-api/v2/markets",
         params={"limit": "20", "status": "open"}, summarise=sum_kalshi),

    # ---- Governance / news ------------------------------------------------
    Spec("Snapshot GraphQL", "gov", "Governance votes",
         "https://hub.snapshot.org/graphql", method="POST",
         json_body={"query": "{proposals(first:5,where:{space_in:[\"aave.eth\",\"uniswapgovernance.eth\"]},orderBy:\"created\",orderDirection:desc){id title state start end space{id}}}"},
         summarise=sum_snapshot),
    Spec("Aave governance forum RSS", "gov", "Discourse forum feed",
         "https://governance.aave.com/latest.rss", summarise=sum_rss),
    Spec("rekt.news RSS", "gov", "Security incidents",
         "https://rekt.news/rss.xml", summarise=sum_rss),
    Spec("CoinDesk RSS", "news", "News feed",
         "https://www.coindesk.com/arc/outboundfeeds/rss/", summarise=sum_rss),
    Spec("SEC press releases RSS", "news", "Regulatory feed",
         "https://www.sec.gov/news/pressreleases.rss", summarise=sum_rss,
         note="SEC requires a contactable User-Agent."),
    Spec("Fed press releases RSS", "news", "FOMC statements",
         "https://www.federalreserve.gov/feeds/press_all.xml", summarise=sum_rss),
    Spec("GitHub API (dev activity)", "news", "Developer activity per project",
         "https://api.github.com/repos/aave/aave-v3-core", summarise=sum_json_shape,
         note="60 req/h unauthenticated; 5000/h with the Actions token."),
    Spec("FOMC calendar page", "calendar", "FOMC dates",
         "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
         summarise=sum_text_head),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def check_cors(client: httpx.Client, spec: Spec) -> str | None:
    """Would a browser be allowed to call this directly?

    Only meaningful for the handful of sources the live layer wants to hit
    from the page (§3 'Live layer'). A missing header is the normal answer and
    simply means the pipeline must proxy it.
    """
    try:
        response = client.request(
            spec.method, spec.url, params=spec.params, json=spec.json_body,
            headers={**spec.headers, "Origin": "https://minimumlizard.github.io"},
            timeout=15.0)
        return response.headers.get("access-control-allow-origin")
    except Exception:
        return None


def run_one(client: httpx.Client, spec: Spec, want_cors: bool) -> Result:
    result = Result(name=spec.name, group=spec.group, dataset=spec.dataset,
                    url=spec.url, status="skipped", note=spec.note,
                    auth_env=spec.auth_env, auth_style=spec.auth_style)

    params = dict(spec.params or {})
    headers = {"User-Agent": UA, **spec.headers}

    # Keys are read from the environment only. A missing key is a reportable
    # outcome ("this source needs a key you have not registered"), not a crash.
    if spec.auth_env:
        key = os.environ.get(spec.auth_env, "").strip()
        if not key and not spec.auth_optional:
            result.status = "auth_required"
            result.auth_required = True
            result.error = f"{spec.auth_env} not set"
            return result
        if not key:
            # Probe the unauthenticated path anyway: the interesting question
            # for an optional key is what the ceiling is WITHOUT it.
            result.note = (result.note + " [probed without a key]").strip()
            key = ""
        if key and "query param" in spec.auth_style:
            params[spec.auth_style.split()[0]] = key
        elif key:
            headers[spec.auth_style.split()[0]] = key

    started = time.perf_counter()
    try:
        response = client.request(spec.method, spec.url, params=params or None,
                                  json=spec.json_body, headers=headers)
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        result.http_code = response.status_code
        result.bytes = len(response.content)
        result.rate_limit = {k: v for k, v in
                             ((h, response.headers.get(h)) for h in RATE_HEADERS) if v}

        if response.status_code >= 400:
            result.status = "http_error"
            result.error = response.text[:200].replace("\n", " ")
            if response.status_code in (401, 403) and not spec.auth_env:
                result.error = f"possible geoblock/auth: {result.error}"
            return result

        result.status = "ok"
        if spec.summarise:
            ctype = response.headers.get("content-type", "")
            if "json" in ctype:
                payload = response.json()
            else:
                try:
                    payload = response.json()
                except Exception:
                    payload = response.text
            result.summary = spec.summarise(payload)
        else:
            result.summary = {"bytes": result.bytes}

    except Exception as exc:  # noqa: BLE001
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        result.status = "network_error"
        result.error = f"{type(exc).__name__}: {exc}"[:220]
        return result

    if want_cors:
        result.cors = check_cors(client, spec)
    return result


STATUS_MARK = {"ok": "ok", "http_error": "FAIL", "auth_required": "key needed",
               "network_error": "FAIL", "skipped": "skipped"}


def to_markdown(results: list[Result], meta: dict) -> str:
    lines = [
        "# Source probe results",
        "",
        f"Run {meta['ran_at']} from **{meta['location']}** "
        f"(egress {meta.get('egress_ip', '?')}, {meta.get('egress_geo', '?')}).",
        "",
        "Generated by `uv run tools/probe.py` — do not hand-edit. Every row is a live",
        "call made at the time above, not a recollection of how the API used to behave.",
        "",
    ]

    ok = sum(1 for r in results if r.status == "ok")
    keyed = sum(1 for r in results if r.status == "auth_required")
    bad = len(results) - ok - keyed
    lines += [f"**{ok} reachable · {keyed} need a key · {bad} failing** "
              f"out of {len(results)} probes.", ""]

    for group in dict.fromkeys(r.group for r in results):
        lines += [f"## {group}", "",
                  "| source | feeds | status | code | ms | history / shape |",
                  "|---|---|---|---|---|---|"]
        for r in (x for x in results if x.group == group):
            shape = ", ".join(f"{k}={v}" for k, v in list(r.summary.items())[:4])
            if r.status != "ok":
                shape = r.error[:110]
            lines.append(
                f"| `{r.name}` | {r.dataset} | {STATUS_MARK[r.status]} | "
                f"{r.http_code or '–'} | {r.latency_ms or '–'} | {shape[:150]} |")
        lines.append("")

    lines += ["## Full detail", "", "```json",
              json.dumps([asdict(r) for r in results], indent=1)[:200000], "```", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", help="limit to these groups")
    parser.add_argument("--location", default=os.environ.get("PROBE_LOCATION", "unknown"),
                        help="label for where this ran, e.g. 'github-runner'")
    parser.add_argument("--out", default="docs/probe", help="output path stem")
    parser.add_argument("--no-cors", action="store_true")
    args = parser.parse_args()

    specs = [s for s in SPECS if not args.only or s.group in args.only]

    egress_ip = egress_geo = "?"
    try:
        info = httpx.get("https://ipinfo.io/json", timeout=15).json()
        egress_ip = info.get("ip", "?")
        egress_geo = f"{info.get('city','?')}, {info.get('country','?')}"
    except Exception:
        pass

    print(f"probing {len(specs)} sources from {args.location} ({egress_geo})\n")
    results: list[Result] = []
    with httpx.Client(follow_redirects=True, timeout=TIMEOUT) as client:
        for index, spec in enumerate(specs, 1):
            result = run_one(client, spec, want_cors=not args.no_cors)
            results.append(result)
            print(f"  [{index:>2}/{len(specs)}] {STATUS_MARK[result.status]:<10} "
                  f"{result.http_code or '---':>4}  {spec.name}"
                  + (f"  -- {result.error[:70]}" if result.error else ""))
            time.sleep(0.25)   # be a polite citizen of free APIs

    meta = {
        "ran_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "location": args.location, "egress_ip": egress_ip, "egress_geo": egress_geo,
        "python": sys.version.split()[0], "platform": platform.platform(),
        "hostname": socket.gethostname(),
    }
    payload = {"meta": meta, "results": [asdict(r) for r in results]}

    stem = Path(args.out)
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix(".json").write_text(json.dumps(payload, indent=1))
    stem.with_suffix(".md").write_text(to_markdown(results, meta))

    ok = sum(1 for r in results if r.status == "ok")
    keyed = sum(1 for r in results if r.status == "auth_required")
    print(f"\n{ok} ok, {keyed} need keys, {len(results) - ok - keyed} failing")
    print(f"wrote {stem.with_suffix('.json')} and {stem.with_suffix('.md')}")


if __name__ == "__main__":
    main()
