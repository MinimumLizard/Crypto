"""Governance, security and news feeds (SPEC §6.13).

Tagging news to an asset is the part that can quietly go wrong. A naive
substring match on a ticker turns every article containing the word "sky" into
a SKY governance item and every mention of a uniform into UNI news. So a
headline is tagged only when it matches a whole word from an explicit alias
list, and single-letter or dictionary-word tickers get no bare-ticker alias at
all. An untagged headline stays in the feed untagged, which is the honest
outcome; a wrongly tagged one would put a story on an asset page that has
nothing to do with it.
"""

from __future__ import annotations

import datetime as dt
import re
import time
import xml.etree.ElementTree as ET

import polars as pl

from pipeline import health, http, registry, store

LLAMA_HACKS = "https://api.llama.fi/hacks"
SNAPSHOT_GQL = "https://hub.snapshot.org/graphql"

NEWS_FEEDS = [
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("sec", "https://www.sec.gov/news/pressreleases.rss"),
    ("federalreserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
]

GOVERNANCE_FEEDS = [
    ("aave", "https://governance.aave.com/latest.rss"),
]

# Snapshot spaces for the names in the book that actually govern there. Every
# slug below was confirmed to exist and to carry proposals; a guessed slug
# returns an empty list, which is indistinguishable from a quiet DAO and so is
# worse than not listing it at all.
SNAPSHOT_SPACES = {
    "AAVE": "aavedao.eth",
    "UNI": "uniswapgovernance.eth",
    "LDO": "lido-snapshot.eth",
}

# Names that govern somewhere other than Snapshot, so their absence above is a
# fact about where they vote rather than a gap in coverage. Surfaced on the page
# for the same reason.
GOVERNS_ELSEWHERE = {
    "AERO": "veAERO voting on Base, on-chain only",
    "SKY": "on-chain polling and executive votes",
    "MORPHO": "on-chain governance",
    "ONDO": "on-chain governance",
}

# Tickers that are ordinary English words or too short to match safely. These
# are tagged only by their full project name, never by the bare symbol.
UNSAFE_BARE_TICKERS = {"SKY", "UNI", "RAY", "TAO", "CFG", "PUMP", "DUSK",
                       "FLUID", "LINK", "AERO", "GEOD", "TRX"}

# Aliases that are ordinary English words even when they are the project's real
# name. Whole-word matching is not enough for these -- "Sky is blue today"
# matches \bsky\b perfectly well -- so they additionally require the headline to
# read like a crypto story. Getting this wrong puts an unrelated article on an
# asset page, which is worse than leaving a real one untagged.
AMBIGUOUS_ALIASES = {"sky", "fluid", "virtual", "dusk", "aztec", "ray", "link",
                     "pump", "aero", "tao", "uni", "morpho", "ondo", "trx",
                     "pump fun", "pump.fun"}

CONTEXT_TERMS = (
    "crypto", "token", "defi", "protocol", "blockchain", "dao", "stablecoin",
    "onchain", "on-chain", "web3", "ethereum", "solana", "bitcoin", "staking",
    "tvl", "airdrop", "wallet", "smart contract", "treasury", "governance",
    "exchange", "coin", "l2", "layer 2", "rollup", "validator", "liquidity",
)


def _aliases() -> dict[str, list[str]]:
    """Symbol -> the phrases that may tag a headline to it."""
    out: dict[str, list[str]] = {}
    for asset in registry.tracked():
        names = {asset.name.lower()}
        if asset.symbol not in UNSAFE_BARE_TICKERS:
            names.add(asset.symbol.lower())
        if asset.coingecko_id:
            names.add(asset.coingecko_id.replace("-", " ").lower())
        out[asset.symbol] = sorted(n for n in names if len(n) >= 3)
    return out


def _has_context(lowered: str) -> bool:
    return any(term in lowered for term in CONTEXT_TERMS)


def _tag(text: str, aliases: dict[str, list[str]]) -> list[str]:
    lowered = text.lower()
    context = _has_context(lowered)
    hits = []
    for symbol, names in aliases.items():
        for name in names:
            # Whole-word only: "uni" must not match "uniform", "sky" must not
            # match "Skyscraper". \b on both sides is what makes that true.
            if not re.search(rf"\b{re.escape(name)}\b", lowered):
                continue
            # ...and a name that is also an English word needs the headline to
            # be about crypto at all before it counts.
            if name in AMBIGUOUS_ALIASES and not context:
                continue
            hits.append(symbol)
            break
    return hits


def _parse_rss(xml_text: str) -> list[dict]:
    """RSS 2.0 and Atom, because the three news feeds are not all the same."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    items = []
    for item in root.iter():
        tag = item.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {child.tag.split("}")[-1]: child for child in item}
        title = (fields.get("title").text or "").strip() if "title" in fields else ""
        if not title:
            continue
        link_node = fields.get("link")
        link = ""
        if link_node is not None:
            link = (link_node.text or link_node.attrib.get("href") or "").strip()
        published = ""
        for key in ("pubDate", "published", "updated", "date"):
            if key in fields and fields[key].text:
                published = fields[key].text.strip()
                break
        items.append({"title": title[:300], "link": link[:400],
                      "published": published[:40]})
    return items


def _normalise_date(raw: str) -> str:
    """RSS dates arrive in at least three formats; store one."""
    raw = raw.strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _rss_source(kind: str, name: str, url: str, aliases: dict) -> list[dict]:
    started = time.perf_counter()
    try:
        response = http.request(url, cache_hours=2, timeout=45)
        items = _parse_rss(response.text)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source=name, endpoint="rss", dataset=f"{kind} feed",
            status="http_error", error=str(exc)[:200]))
        return []

    rows = [{
        "kind": kind, "source": name, "title": item["title"], "link": item["link"],
        "published": _normalise_date(item["published"]),
        "assets": ",".join(_tag(item["title"], aliases)),
    } for item in items]

    health.record(health.Record(
        source=name, endpoint="rss", dataset=f"{kind} feed",
        status="ok" if rows else "empty", rows=len(rows),
        as_of=max((r["published"] for r in rows if r["published"]), default=None),
        expected_lag_days=3.0,
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return rows


def news_and_governance() -> int:
    aliases = _aliases()
    rows: list[dict] = []
    for name, url in NEWS_FEEDS:
        rows += _rss_source("news", name, url, aliases)
    for name, url in GOVERNANCE_FEEDS:
        rows += _rss_source("governance", name, url, aliases)
    if not rows:
        return 0
    return store.write_snapshot("feeds", pl.DataFrame(rows),
                                key=["source", "title"])


def security_incidents() -> int:
    """DefiLlama /hacks, because rekt.news RSS returns 500 (docs/SOURCES.md)."""
    started = time.perf_counter()
    try:
        payload = http.get_json(LLAMA_HACKS, cache_hours=12)
    except Exception as exc:  # noqa: BLE001
        health.record(health.Record(
            source="defillama", endpoint="hacks", dataset="security incidents",
            status="http_error", error=str(exc)[:200]))
        return 0

    aliases = _aliases()
    rows = []
    for hack in payload or []:
        stamp = hack.get("date")
        try:
            when = dt.datetime.fromtimestamp(int(stamp), dt.UTC).date().isoformat()
        except (TypeError, ValueError):
            continue
        name = str(hack.get("name") or "")
        rows.append({
            "date": when,
            "name": name[:160],
            "amount_usd": _as_float(hack.get("amount")),
            "chain": ",".join(hack.get("chain") or [])[:120],
            "technique": str(hack.get("technique") or "")[:120],
            "classification": str(hack.get("classification") or "")[:80],
            "link": str(hack.get("link") or "")[:400],
            "assets": ",".join(_tag(name, aliases)),
        })
    if not rows:
        health.record(health.Record(
            source="defillama", endpoint="hacks", dataset="security incidents",
            status="empty", error="no incidents parsed"))
        return 0

    health.record(health.Record(
        source="defillama", endpoint="hacks", dataset="security incidents",
        status="ok", rows=len(rows), as_of=max(r["date"] for r in rows),
        expected_lag_days=30.0,
        latency_ms=int((time.perf_counter() - started) * 1000)))
    return store.write_snapshot("security_incidents", pl.DataFrame(rows),
                                key=["date", "name"])


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


SNAPSHOT_QUERY = """
query Proposals($space: String!) {
  proposals(first: 12, where: {space: $space}, orderBy: "created", orderDirection: desc) {
    id title state start end scores_total choices
  }
}
"""


def governance_proposals() -> int:
    """Open and recent Snapshot proposals for the spaces we track."""
    rows: list[dict] = []
    now = int(dt.datetime.now(dt.UTC).timestamp())
    for symbol, space in SNAPSHOT_SPACES.items():
        started = time.perf_counter()
        try:
            payload = http.get_json(
                SNAPSHOT_GQL, method="POST",
                json_body={"query": SNAPSHOT_QUERY, "variables": {"space": space}},
                cache_hours=3, timeout=45)
            proposals = ((payload.get("data") or {}).get("proposals")) or []
        except Exception as exc:  # noqa: BLE001
            health.record(health.Record(
                source="snapshot", endpoint=space, dataset=f"{symbol} proposals",
                status="http_error", error=str(exc)[:200]))
            continue

        for proposal in proposals:
            end = proposal.get("end") or 0
            rows.append({
                "symbol": symbol, "space": space,
                "id": str(proposal.get("id") or "")[:80],
                "title": str(proposal.get("title") or "")[:200],
                "state": str(proposal.get("state") or ""),
                "start": _stamp(proposal.get("start")),
                "end": _stamp(end),
                "closes_in_days": (round((int(end) - now) / 86400, 1)
                                   if end else None),
                "votes_total": _as_float(proposal.get("scores_total")),
            })
        health.record(health.Record(
            source="snapshot", endpoint=space, dataset=f"{symbol} proposals",
            status="ok" if proposals else "empty",
            error="" if proposals else "space returned no proposals",
            rows=len(proposals), expected_lag_days=7.0,
            latency_ms=int((time.perf_counter() - started) * 1000)))

    if not rows:
        return 0
    return store.write_snapshot("governance", pl.DataFrame(rows),
                                key=["space", "id"])


def _stamp(value) -> str:
    try:
        return dt.datetime.fromtimestamp(int(value), dt.UTC).date().isoformat()
    except (TypeError, ValueError):
        return ""


def fetch_all() -> dict[str, int]:
    return {
        "feeds": news_and_governance(),
        "security_incidents": security_incidents(),
        "governance": governance_proposals(),
    }


__all__ = ["fetch_all", "news_and_governance", "security_incidents",
           "governance_proposals", "_tag", "_aliases", "_parse_rss",
           "SNAPSHOT_SPACES"]
