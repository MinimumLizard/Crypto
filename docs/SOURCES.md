# Sources: what works, from where, and what we use

Evidence, not recollection. Every claim here comes from a live call made by
`tools/probe.py` on **2026-09-24** from two locations:

| Label | Egress | Result |
|---|---|---|
| `us-cloud` | Columbus, Ohio (AS396982 Google) — this build session | 54 ok · 3 need keys · 17 failing |
| `runner` | Chicago — `ubuntu-latest` GitHub-hosted runner | 57 ok · 3 need keys · 14 failing |
| `melbourne` | **not yet run — see "The missing datapoint" below** | — |

Raw results: `probe-uscloud.json`, `probe-runner.json` (and their `.md` twins).
Regenerate with `uv run tools/probe.py --location <label>`.

---

## The two US locations agree on everything that matters

Running the identical probe file from both produced only **three** differences,
none of them a property of the source:

| Probe | us-cloud | runner | Explanation |
|---|---|---|---|
| Wikimedia pageviews | 403 | 200 | This container's proxy alters the request; a contactable User-Agent is what Wikimedia actually requires, and it works on the runner |
| Kalshi markets | 429 | 200 | Rate-limit timing, not access |
| GitHub API | 403 | 200 | This session's GitHub access is scoped to one repo; the runner has none of that restriction |

Everything else — including every geoblock and every paywall — behaved
identically. **The runner is the operative environment**, and its failure list
below is the one the design is built against.

## The missing datapoint

The brief asked for local *and* runner results. This session cannot supply the
"local" half: it runs in a US cloud container, so it is a second runner, not a
second location. `api.binance.com` returns 451 and `api.bybit.com` returns 403
from here exactly as they do from Chicago.

To close it, on your own machine:

```
uv run tools/probe.py --location melbourne --out docs/probe-melbourne
```

Four minutes, no keys needed. It answers exactly one open question: whether
Binance.com, Bybit and Stooq are reachable from where you are. **Nothing in the
plan depends on the answer** — the workarounds below already avoid all three —
but it would tell us what a self-hosted runner would buy, which is §12 Q7.

---

## Geoblocks: confirmed, and already worked around

| Host | From US | Workaround | Verified |
|---|---|---|---|
| `api.binance.com` | **451** Service unavailable from a restricted location | `data-api.binance.vision` — Binance's market-data-only host | 200, 1000 daily bars, 1000 hourly bars |
| `api.bybit.com` | **403** CloudFront | Hyperliquid `predictedFundings` returns Bybit's rate | 200, 234 coins |
| Binance perp funding | (same 451) | Same call — `predictedFundings` returns `BinPerp` too | 200 |

`predictedFundings` returns, per coin, a list of `[venue, {fundingRate,
nextFundingTime, fundingIntervalHours}]` for **`BinPerp`, `BybitPerp`,
`HlPerp`**. Note the intervals differ — Hyperliquid funds hourly, Binance and
Bybit on 4h or 8h — so annualising with a single constant would be wrong by up
to 8×. Use each venue's own `fundingIntervalHours`.

**Conclusion: no self-hosted runner and no Cloudflare Worker is needed for
prices or funding.** The only sources still lost to US egress are Stooq and
Farside, and neither is critical (see below).

---

## Primary and fallback, per dataset

### Prices and OHLCV

| Dataset | Primary | Fallback | Notes |
|---|---|---|---|
| Daily spot bars (MiniLizard parity) | `data-api.binance.vision` klines | Coinbase Exchange candles | Parity is *defined* on Binance bars; a fallback bar is not parity |
| 1H bars (CVD block, §7.1) | `data-api.binance.vision` 1h | Hyperliquid `candleSnapshot` | 1000 bars/call, so ~41 days per request |
| FLUID, GEOD | Coinbase `FLUID-USD`, `GEOD-USD` | — | **No Binance pair exists.** Flagged `parity: substitute` |
| AZTEC, XMR | Hyperliquid perp | Coinbase (AZTEC only) | Same |
| BTC history pre-2017 | CoinMetrics `PriceUSD` | — | Required for the §7.2 fit; Binance starts 2017 |

Verified limits: Binance klines max 1000 rows/call. Coinbase candles max 300.
Hyperliquid `candleSnapshot` returned 632 daily bars for a 2024→2026 window.

### Market cap, supply, rank

| Dataset | Primary | Fallback |
|---|---|---|
| Caps, supply, rank, volume | CoinGecko `/coins/markets` (**Demo key required**) | DefiLlama `coins.llama.fi` prices |
| Second cap source for the >10% cross-check | CoinMarketCap Basic (**key required**) | CoinGecko vs DefiLlama price × our own stored supply |

Unauthenticated CoinGecko returned **429 within a handful of calls** and exposes
no rate-limit headers. The Demo key is required, not optional. Per §5.1 neither
CoinLore nor CoinPaprika is used.

### Protocol fundamentals — DefiLlama

Working (200): `/summary/fees/{parent}` for `dailyFees`, `dailyRevenue`,
`dailyHoldersRevenue`; `/overview/fees` (2,757 protocols); `/summary/dexs/{slug}`;
`/protocol/{slug}`; `stablecoins.llama.fi`; `yields.llama.fi`; `/hacks`.

**Paywalled (402 "Upgrade to the paid API plan"):** `/emissions`,
`/emission/{slug}`, `/treasuries`.

Consequences: unlocks and treasury data come from `config/unlocks.yaml` and
`config/treasuries.yaml`, hand-maintained and snapshotted daily (D007). Read
fees from the **parent** slug — see D004 for why, and for the three slugs that
are not the obvious guess (`pump`, `maple-finance`, `sky`).

Probed coverage across the book: fee data exists for 17 of 21 names. It does not
exist at all for **TAO, AZTEC, DUSK, POLYX** — the brief's grade-F list,
confirmed exactly. Holders' revenue probed at **zero** for AAVE, FLUID, MORPHO,
ONDO and CFG; see PLAN.md §5.4 for why that is grade D, not F.

### BTC and ETH on-chain — CoinMetrics community

The community tier serves **31** BTC metrics. Free and used:
`PriceUSD`, `PriceBTC`, `CapMrktCurUSD`, `CapMVRVCur`, `SplyCur`, `IssTotUSD`,
`IssTotNtv`, `FeeTotNtv`, `HashRate`, `AdrActCnt`, `TxCnt`, `TxTfrCnt`,
`FlowInExUSD`, `FlowOutExUSD`, `SplyExUSD`, `ROI1yr`, `ROI30d`.

Paywalled (403): `CapRealUSD`, `RevUSD`, `TxTfrValAdjUSD`, `FeeTotUSD`, `NVTAdj`.

Derivations that recover most of it (D005): realised cap = `CapMrktCurUSD /
CapMVRVCur`; realised price = that ÷ `SplyCur`; NUPL = `1 − 1/CapMVRVCur`;
miner revenue = `IssTotUSD + FeeTotNtv × PriceUSD`; thermocap = its cumulative
sum; Puell = `IssTotUSD` ÷ its own 365-day mean.

**NVT and NVT signal are dropped.** They need transfer *value*; only transfer
*counts* are free. No free substitute found.

Supporting: mempool.space (fees, hashrate, block tip — all 200, no key).
**beaconcha.in now returns 401** and needs a key; ETH staking will come from
DefiLlama or the beacon key if you register one. growthepie serves L2 and blob
fees at `/v1/fundamentals.json` (not `fundamentals_full.json`).

### Derivatives

All Hyperliquid endpoints 200: `metaAndAssetCtxs` (234 perps, with funding, OI,
mark, oracle, premium, day volume), `predictedFundings`, `fundingHistory`,
`l2Book`, `spotMetaAndAssetCtxs` (503 spot tokens). Deribit 200 for option book
summary, DVOL history and futures basis. OKX instruments 200.

Per §5.1, no free liquidation source is used. None is invented.

### Macro

| Dataset | Primary | Fallback |
|---|---|---|
| All FRED series, release dates | FRED (**key required**) | — |
| Gold, DXY, MOVE, NDX, equity comps | Yahoo chart API (`GC=F`, `DX-Y.NYB`, `^MOVE`) | **Stooq is unusable — see below** |
| Gold, crypto-native check | PAXG on `data-api.binance.vision` / Coinbase | Labelled: PAXG trades near, not at, spot |
| USD/AUD for the currency toggle | FRED `DEXUSAL` | `open.er-api.com` (200, no key) |

**Stooq times out from both US locations** — `ConnectTimeout` on the runner,
connection reset here, over https and plain http alike. The brief named it as
the yfinance fallback; it is not available where this pipeline runs. Yahoo is
verified working for gold, DXY and MOVE, but it is unofficial and rate-limits on
shared IPs, so the honest position is that **the macro equity/commodity feed has
one working source and no true fallback**. FRED covers what it covers
(`DTWEXBGS` for the broad dollar, `SP500`, `DCOILBRENTEU`) and gold is the real
exposure, mitigated by the PAXG cross-check.

### Sentiment

alternative.me Fear & Greed: 200, full history in one call. Wikimedia pageviews:
200 **only with a User-Agent that carries contact details** — the exact string
in `tools/probe.py` works; a generic one returns 403 with a link to the robot
policy. Upbit 200 (kimchi premium). Coinbase premium is computed from sources
already fetched. pytrends and YouTube remain optional and skippable.

### Geopolitics and events

GDELT 200, but **429 under rapid calls** — "limit requests to one every 5
seconds". Needs spacing and backoff, not replacement. GPR daily file: 200.
Polymarket Gamma: 200. Kalshi: 200 on the runner.

**Re-probed 2026-09-26, building P6.** GDELT's limit is per source IP and this
container's egress is shared, so five seconds still returns 429 and a first
call needed ~40s of backoff. Spacing is now 8s with a wall-clock budget on the
theme sweep and stalest-first ordering (D023). GPR is a 3.2MB `.xls` — no CSV,
no API — and carries GPRD, GPRD_THREAT and GPRD_ACT, 15,239 days back to 1985.
EPU's daily CSV at policyuncertainty.com is 200 and serves 15,243 days from the
same start; it was not in the original probe and is now used.

Polymarket Gamma pages 100 at a time and `order=volumeNum&ascending=false`
works, so eight pages reach the liquid end of the book. Kalshi's `/markets`
list is dominated by multi-leg sports parlays whose title is a comma-joined
string of selections, and its price fields are `*_dollars` — `last_price` no
longer exists. Its `/events` endpoint carries clean titles, so topics are
matched there and the event's markets are priced afterwards.

### Governance, security, news

Snapshot GraphQL 200. Aave Discourse RSS 200. CoinDesk, SEC and Fed RSS all 200
(SEC and Wikimedia both need the contactable UA). GitHub API 200 on the runner,
60 req/h unauthenticated and 5,000/h with the Actions token — use the token.

**Snapshot space slugs, confirmed 2026-09-26.** A guessed slug returns an empty
proposal list, which reads exactly like a DAO with nothing open, so every slug
was checked against the API. Real: `aavedao.eth` (981 proposals),
`uniswapgovernance.eth` (199), `lido-snapshot.eth` (423). NOT real, and removed:
`aave.eth`, `skyecosystem.eth`, `aerodromefinance.eth`. AERO, SKY, MORPHO and
ONDO have no Snapshot space — they govern on-chain, which the radar page states
rather than leaving them looking uncovered.

The Fed's FOMC calendar page has no API but parses reliably: year panels, then
a month block and a date range per meeting. 54 meetings across the published
years. The decision lands on the final day of a two-day meeting.

**rekt.news RSS returns 500 and is dropped.** DefiLlama `/hacks` (200) becomes
the security incident source.

### ETFs — no working free source

Farside returns **403** (Cloudflare) from both locations. The SoSoValue endpoint
returns 404. CoinGecko's public-treasury endpoint exists but was rate-limited
during probing and needs re-testing with the Demo key.

Per brief §0.2 the ETF panel will read **"source unavailable: Farside blocks
automated access; no free alternative found"** rather than showing anything. It
is not fabricated and not quietly omitted.

---

## Keys required

| Key | Needed for | Without it |
|---|---|---|
| **FRED** | Every macro and liquidity series, release calendar | The entire `/macro` page is dead |
| **CoinGecko Demo** | Caps, supply, rank, screener | 429s within seconds |
| **CoinMarketCap Basic** | The second cap source for the >10% cross-check | Cross-check degrades to price-source comparison only |
| Etherscan | Gas-threshold check | Works unauthenticated at low rates; key raises the ceiling |
| Telegram bot | §8 alerts | No alerts |
| beaconcha.in | ETH staking ratio | Falls back to DefiLlama |
| *optional* YouTube, Anthropic | Attention series, LLM brief | Both off by default |

Keys live in GitHub Actions secrets and a gitignored `.env`. None ever reaches
the browser.

## Attribution

CoinGecko free tier and TradingView Lightweight Charts (Apache-2.0) both require
attribution; both go in the site footer, along with DefiLlama, CoinMetrics
Community, FRED, GDELT and the Caldara–Iacoviello GPR index.
