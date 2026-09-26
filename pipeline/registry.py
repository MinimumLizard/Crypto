"""Reading config/assets.yaml, and the weights file that is deliberately absent.

Identity is resolved by CoinGecko id and contract address, never by ticker
(D004, and SPEC §4.1). This module is the only place that knows the shape of
the registry, so a field rename happens once.

Target weights live in a separate, gitignored file (D009). When it is missing —
a fresh clone, CI without the secret — `weights()` returns None and every
weight-dependent panel renders "not configured" rather than guessing or
showing zeros.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml

from pipeline import paths


@dataclass(frozen=True)
class Asset:
    symbol: str
    name: str
    coingecko_id: str
    kind: str                       # book | benchmark | watchlist
    tier: int | None = None
    sector: str = ""
    chains: dict[str, str] = field(default_factory=dict)
    binance_spot: str | None = None
    coinbase: str | None = None
    hl_perp: str | None = None
    hl_spot: str | None = None
    llama_parent: str | None = None
    llama_children: list[str] = field(default_factory=list)
    llama_chain: str | None = None   # DefiLlama chain name; chains are not protocols
    coinmetrics_id: str | None = None
    minilizard_variant: str = "V10"
    extension_threshold_pct: float = 12.0
    parity: str = "binance"         # binance | substitute
    data_quality_grade: str | None = None
    quality_flags: list[str] = field(default_factory=list)
    sector_kpis: list[str] = field(default_factory=list)
    equity_comps: list[str] = field(default_factory=list)
    chain_metrics: bool = False
    fails_gas_threshold: bool = False
    gas_threshold_usd: float | None = None
    custody_class: str = ""
    open_item: str = ""
    thesis: str = ""
    watch_triggers: list[str] = field(default_factory=list)
    minilizard_no_measured_edge: bool = False

    @property
    def price_source(self) -> tuple[str, str]:
        """Which venue supplies this asset's bars, and under what symbol.

        Binance where a pair exists, because MiniLizard is defined on Binance
        bars. Three book names have no Binance pair at all (D006), so they fall
        to Coinbase or Hyperliquid and are flagged `parity: substitute`.
        """
        if self.binance_spot:
            return ("binance", self.binance_spot)
        if self.coinbase:
            return ("coinbase", self.coinbase)
        if self.hl_perp:
            return ("hyperliquid", self.hl_perp)
        return ("none", "")


def _build(raw: dict[str, Any], kind: str) -> Asset:
    known = {f for f in Asset.__dataclass_fields__}
    fields = {k: v for k, v in raw.items() if k in known}
    fields.setdefault("name", raw.get("symbol", ""))
    fields.setdefault("coingecko_id", "")
    return Asset(kind=kind, **fields)


@lru_cache(maxsize=1)
def load() -> dict[str, Asset]:
    """Every asset the terminal knows, keyed by symbol."""
    raw = yaml.safe_load((paths.CONFIG / "assets.yaml").read_text())
    assets: dict[str, Asset] = {}
    for entry in raw.get("benchmarks", []):
        asset = _build(entry, "benchmark")
        assets[asset.symbol] = asset
    for entry in raw.get("book", []):
        asset = _build(entry, "book")
        assets[asset.symbol] = asset
    for entry in (raw.get("watchlist") or {}).get("names", []):
        asset = _build(entry, "watchlist")
        assets.setdefault(asset.symbol, asset)
    return assets


def book() -> list[Asset]:
    return [a for a in load().values() if a.kind == "book"]


def benchmarks() -> list[Asset]:
    return [a for a in load().values() if a.kind == "benchmark"]


def tracked() -> list[Asset]:
    """Everything with a price: book, benchmarks and watchlist."""
    return list(load().values())


def get(symbol: str) -> Asset | None:
    return load().get(symbol.upper())


@lru_cache(maxsize=1)
def buy_order() -> list[str]:
    raw = yaml.safe_load((paths.CONFIG / "assets.yaml").read_text())
    return list(raw.get("buy_order") or [])


def unplaced_in_buy_order() -> list[str]:
    """Book names with no position in the buy order.

    SPEC §4.2 lists 21 book names but only 18 in the buy order: HYPE, LINK and
    SYRUP are absent. Inventing positions for them would be inventing a trading
    rule, which §14 forbids, so they are surfaced as unplaced instead (D012).
    """
    ordered = set(buy_order())
    return [a.symbol for a in book() if a.symbol not in ordered]


@lru_cache(maxsize=1)
def unlocks() -> dict[str, Any]:
    """The hand-maintained forward unlock schedule (D007).

    Returns the parsed file plus how old it is, because an unlock schedule that
    has not been reviewed in months is a different object from a current one
    and the page has to be able to say so.
    """
    import datetime as dt
    path = paths.CONFIG / "unlocks.yaml"
    if not path.exists():
        return {"available": False, "reason": "config/unlocks.yaml is absent",
                "unlocks": {}, "age_days": None}
    raw = yaml.safe_load(path.read_text()) or {}
    reviewed = (raw.get("meta") or {}).get("last_reviewed")
    age = None
    if reviewed:
        try:
            age = (dt.date.today() - dt.date.fromisoformat(str(reviewed))).days
        except ValueError:
            age = None
    entries = raw.get("unlocks") or {}
    return {
        "available": bool(entries),
        "unlocks": entries,
        "last_reviewed": str(reviewed) if reviewed else None,
        "age_days": age,
        "reason": (None if entries else
                   "no unlocks entered yet. DefiLlama's emissions endpoint is "
                   "402 on the free tier, so the forward schedule is "
                   "hand-maintained in config/unlocks.yaml and nothing is "
                   "invented here."),
    }


@lru_cache(maxsize=1)
def weights() -> dict[str, Any] | None:
    """Target weights, or None when the private file is absent (D009)."""
    path = paths.CONFIG / "weights.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())
