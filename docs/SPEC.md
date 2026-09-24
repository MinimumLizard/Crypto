<!-- PUBLIC COPY. Redacted per SPEC §12: section 1.1 removed and the
     target weights in §4.2 removed. The unredacted brief is kept in
     private/SPEC.md, which is gitignored. -->

# Build brief: MiniLizard Crypto Terminal

You are building a personal crypto market terminal for me. It is a statically hosted website that rebuilds itself automatically. I open it every morning to monitor three things: BTC, ETH, and a 21-name altcoin book; the macro, liquidity, on-chain, derivatives, supply, governance and geopolitical conditions around them; and my deployment plan against all of that.

Read this entire brief before doing anything. Your first action is to save it verbatim to `private/SPEC.md`, which is gitignored. If we choose public mode (§12), also save a redacted copy to `docs/SPEC.md`: remove §1.1 and the weights in §4.2.

---

## 0. How I want you to work

1. **Plan first. Write no application code in the first session.** Instead:
   - a. Clone my existing project read-only: `https://github.com/minimumlizard/Nelson-Siegel` (live at https://minimumlizard.github.io/Nelson-Siegel/). It is a Sri Lankan government bond relative-value site, rebuilt daily from PDMO data. Study how it's built: pipeline, scheduling, history storage, rendering, validation (`python -m signals.validate`), and the tone of its annotations. This terminal is its bigger sibling. Reuse its conventions where they're sound, and say where you deviate and why.
   - b. Read everything in `private/reference/` (§13).
   - c. Run the source probe (§5.0) locally **and on a GitHub-hosted runner**. Report what works, from where, and with what limits.
   - d. Write `docs/PLAN.md`: architecture, repo layout, data model, page list, the phase plan with acceptance criteria, and every deviation from this brief with its reason.
   - e. Ask me the §12 questions in one batch. Then stop and wait.
2. **Build in phases (§11).** Every phase ends deployed and working. Never ship dead panels. A panel without data says "not built yet" or "source unavailable: <reason>". It never shows zeros or placeholder numbers.
3. **Verify, don't assume.** Every endpoint, field name, rate limit and free-tier history limit in this brief comes from memory and may be stale. Probe each one live before building on it. If a source is dead or unsuitable, find the best free replacement and log it in `docs/DECISIONS.md`.
4. **Never fabricate data.** Never silently interpolate. Never hard-code a market number into logic or UI. Narrative context lives in an editable `content/context.md`.
5. Maintain `CLAUDE.md` at the repo root: conventions, commands, gotchas you've hit, current phase, and open issues. Future sessions start from it. Keep `docs/DECISIONS.md` as an append-only log.
6. **End every phase the same way:**
   - run the tests;
   - run the full pipeline cold;
   - build the site;
   - screenshot every page at 1440px and 390px with Playwright and inspect the screenshots yourself;
   - then report briefly: what shipped, what's approximated or broken, and what's next.
7. Be terse and direct. If something I've asked for is a bad idea, impossible for free, or statistically meaningless, say so plainly and propose the alternative.

---

## 1. Context

### 1.1 [redacted]

This section describes the owner personally and is removed from the public
copy at his request. It covered his base currency, where he is based, what he
trades, the tools he uses, and his portfolio philosophy. Nothing in the build
depends on reading it here.

### 1.2 Market context as of 25 Sep 2026

This goes in `content/context.md` so I can edit it. It is never used in logic. It tells you what to prioritise.

- **BTC:** testing its 50-week SMA (~$81.8k). My confirmation rule is two consecutive weekly closes above it.
- **Fed:** hiked to 3.75–4.00% on 16 Sep 2026, the first hike since 2023, and more hikes are expected. Core PCE is ~3.4%.
- **Oil shock:** the 2026 Iran war closed the Strait of Hormuz from late Feb and also closed Bab al-Mandab. Brent was ~$109 in early Sep. **Hormuz reopening is the highest-leverage variable for an alt book.**
- **Regulation:** the CLARITY Act failed. The SEC issued an innovation exemption on 17 Sep 2026, which favours DEXs and tokenization. US midterms are on 3 Nov 2026, and midterm-year seasonality is a live factor in the cycle view.
- **Market structure:** the market has split into three regimes: hard money (BTC, ZEC, XMR, gold), cash-generating protocols, and 2021-era liquidity beta that is correctly priced rather than cheap.
- **What this means for the terminal:** the oil/geopolitics → inflation → Fed → real yields/USD → liquidity chain; the BTC 50W confirmation; sector rotation; supply overhang; and protocol revenue quality matter more than generic indicators.

---

## 2. Product principles

1. **The home page answers "what matters today?" in 30 seconds.** Everything else is drill-down.
2. **Every number carries a source and an as-of timestamp.** Stale data is visibly marked, like the Nelson-Siegel site's "last known, carried forward" treatment.
3. **Point-in-time correctness.** Any historical series of a derived metric must use only data available at each date: percentiles, z-scores, normalised risk, composites, fits used for signals. Use expanding or rolling windows. No look-ahead.
4. **Score things against their own history and against peers.** Same idea as the bond site's "cheap/rich vs its own norm". A raw level is not a signal.
5. **Every panel gets a 1–3 sentence plain-English "how to read this", plus its caveats.** Put the detail on the methodology pages. State sample sizes, and say explicitly when a signal has no measured edge. Honesty over salesmanship.
6. **It's a research tool, not advice.** Put a site-wide disclaimer on it. It is read-only: no order execution, no exchange keys with trading rights, no wallet connection.
7. **$0 running cost by default.** Ask before adding anything paid.
8. **Graceful degradation.** One failing source never breaks the build. The last good value is carried forward with a stale flag, and the failure appears on the source-health page.

---

## 3. Architecture (defaults; challenge them in PLAN.md if you have a better idea)

- **Pipeline:** Python 3.12, managed with uv. Libraries: httpx, pandas and/or polars, pydantic, scipy, statsmodels, pyarrow; ruff for linting and pytest for tests. Flow: fetchers → raw response cache → normalised history store → metrics → JSON artefacts consumed by the site.
- **Frontend:** a static multi-page site. My default is Vite + TypeScript with no heavy framework. If the Nelson-Siegel stack serves this better, match it. Charts:
  - TradingView Lightweight Charts for OHLC (Apache-2.0, keep the attribution);
  - Apache ECharts or uPlot for everything else.
- **Hosting:** GitHub Pages, deployed by GitHub Actions. There is also a gated alternative (§12 Q1).
- **Schedules:** use off-the-hour minutes, because Actions cron slips at :00.
  - Full daily build at ~00:20 UTC, after the crypto daily close, so 1D regime scores use confirmed bars.
  - Light refresh every 2h: prices, funding, OI, depth, Polymarket odds, unlock countdowns.
  - Macro and news refresh at ~19:40 UTC, which is Melbourne morning.
- **Live layer:** in the browser, subscribe to public WebSockets (Hyperliquid `allMids` and perp contexts; Coinbase ticker) for ticking prices and funding. Fall back to the last snapshot. Verify browser CORS/WS access. Never ship an API key to the client.
- **Own history store:** from day one, the pipeline snapshots daily anything the free APIs don't provide historically. That includes circulating supply, holders' revenue splits, funding, OI, order-book depth, unlock schedules as seen, cross-source market-cap checks, and Polymarket odds. Store Parquet (or CSV) in a `data/` directory or a dedicated `data` branch, with bounded repo growth. Document the choice.
- **Gotchas you must handle:**
  - **GitHub-hosted runners run in the US.** Binance.com and Bybit block US IPs (HTTP 451/403). Test `data-api.binance.vision` (Binance's market-data-only host) for spot klines. For Binance and Bybit perp funding, Hyperliquid's `predictedFundings` returns HL, Binance and Bybit rates in one call. If a critical source only works outside the US, propose either a self-hosted runner on my machine or a Cloudflare Worker, with the trade-offs.
  - **yfinance is unofficial** and gets rate-limited on shared runner IPs. Use Stooq CSV or FRED as fallbacks.
  - **Scheduled workflows on public repos are disabled after 60 days without activity.** Add a keepalive.
  - **Rate limits:**
    - CoinGecko Demo allows ~30 calls/min and has a monthly cap. Batch calls, cache raw responses, back off exponentially, and track a per-source call budget.
    - Wikipedia's API requires a User-Agent header.
  - **Every fetcher writes to a `source_health` table:** source, endpoint, as_of, fetched_at, rows, status, error.

---

## 4. Asset universe

### 4.1 Registry: `config/assets.yaml`

**Fields per asset:**
- symbol, name, CoinGecko id, DefiLlama slug(s), CoinMetrics id (if any)
- chain(s) and contract address(es)
- Binance spot pair, Coinbase product, Hyperliquid perp name, Hyperliquid spot name
- sector, tier, target weight
- custody class and gas-threshold class
- MiniLizard tier (§7.1), watch triggers, thesis notes

**Rules:**
- **Resolve identity by CoinGecko id and contract address, never by ticker.** JUP and PUMP collide across providers.
- Draft the registry, show it to me, and get my confirmation before any metric depends on it.

### 4.2 The book (locked 24 Sep 2026; BTC and ETH are held separately)

| Token | Tier | Sector |
|---|---|---|
| SKY | 1 Core | Stablecoin & credit |
| SOL | 1 Core | Layer 1 |
| AERO | 1 Core | DeFi – DEX |
| UNI | 1 Core | DeFi – DEX |
| ZEC | 1 Core | Privacy |
| AAVE | 1 Core | Lending |
| HYPE | 1 Core | Exchange / perp DEX |
| LINK | 1 Core | Tokenization |
| RAY | 1 Core | DeFi – DEX |
| LDO | 2 Satellite | Liquid staking |
| SYRUP | 2 Satellite | Stablecoin & credit |
| CFG | 2 Satellite | Tokenization |
| PUMP | 2 Satellite | Consumer |
| FLUID | 2 Satellite | Lending |
| MORPHO | 2 Satellite | Lending |
| ONDO | 2 Satellite | Tokenization |
| TAO | 2 Satellite | AI & compute |
| GEOD | 3 Venture tail | DePIN |
| AZTEC | 3 Venture tail | Privacy |
| DUSK | 3 Venture tail | Tokenization |
| POLYX | 3 Venture tail | Tokenization |
**Tier and sector rules:**
- Tier targets are set in a private config file.
- The sector cap is 20% per sector. DeFi–DEX currently sits exactly at the cap.

**Buy order:** SOL → SKY → AERO → UNI → ZEC → AAVE → RAY (only after its market cap is verified) → GEOD → LDO → CFG → PUMP → AZTEC → DUSK → POLYX → TAO → MORPHO → FLUID/ONDO last, because they fall below the gas threshold.

### 4.3 Benchmarks and watchlist

- **Benchmarks:** BTC, ETH, total crypto market cap, total ex-BTC, total ex-BTC/ETH, stablecoin market cap. Also BTC dominance both including and excluding stablecoins.
- **Watchlist:** everything config-driven so I can add names.
  - VIRTUAL, with triggers: two consecutive quarters where its revenue run-rate stops falling, OR holders' revenue rising above $0.
  - TRX, kept as the honest counterexample.
  - XMR, the hard-money peer.
  - Names cut from earlier versions of the book: PENDLE, ENA, JUP, RENDER, PYTH, RAIL, CC, 2Z, ZAMA, KITE.
- **Degen universe:** every Hyperliquid perp (from HL `meta`), plus a CoinGecko low-cap scan (§6.13).

---

## 5. Data sources

### 5.0 Source probe (Phase 0)

Build `probe` as a CLI and an Actions workflow. For every candidate source below, it records:
- reachability from local and from the runner;
- auth required;
- rate limit;
- how much history the free tier gives;
- CORS status (for browser use);
- sample payload shape.

Write the results to `docs/SOURCES.md`, then pick a primary and a fallback for each dataset.

### 5.1 Candidates (verify all of them)

- **Spot OHLCV:**
  - Binance spot klines via `data-api.binance.vision`. This is primary for anything MiniLizard-related, because TradingView parity requires Binance data.
  - Coinbase Exchange candles.
  - Hyperliquid `candleSnapshot`.
  - Kraken.
  - CoinGecko market_chart.
  - BTC long history back to 2010: CoinMetrics community API `PriceUSD`.
- **Market caps, supply, rankings:**
  - CoinGecko (Demo key) as primary.
  - Cross-check against CoinMarketCap (Basic key) and DefiLlama `coins` prices.
  - **Do not use CoinLore** (confirmed unreliable on supply, with errors in both directions) **or CoinPaprika** (its circulating supply doesn't reconcile with its own caps) as primaries.
- **Protocol fundamentals: DefiLlama.**
  - `api.llama.fi/summary/fees/{slug}?dataType=dailyFees|dailyRevenue|dailyHoldersRevenue`
  - `/overview/fees`, `/summary/dexs/{slug}`, `/protocol/{slug}` (TVL)
  - treasury endpoints
  - `stablecoins.llama.fi` (supply by chain)
  - `yields.llama.fi`
  - The emissions/unlocks endpoint may be Pro-only. Check it. If it is, fall back to a manual `config/unlocks.yaml` that I maintain from Tokenomist and project docs, and snapshot it daily.
- **BTC on-chain:**
  - CoinMetrics community API. Confirm community availability via the catalog. Candidate metrics: PriceUSD, CapMrktCurUSD, CapRealUSD, CapMVRVCur, SplyCur, IssTotUSD, FeeTotUSD, RevUSD, HashRate, AdrActCnt, TxTfrValAdjUSD, NVTAdj.
  - mempool.space: fees, hashrate, difficulty, block height for halving estimates.
- **ETH:**
  - CoinMetrics (supply, for net issuance).
  - Staking data from beaconcha.in or DefiLlama.
  - L2 and blob fees from growthepie (verify).
  - Etherscan gas oracle, which feeds the gas-threshold checks.
- **Derivatives:**
  - Hyperliquid `POST api.hyperliquid.xyz/info`: `metaAndAssetCtxs` (funding, OI, mark, oracle, premium, day volume), `fundingHistory`, `predictedFundings`, `l2Book`, `spotMetaAndAssetCtxs`.
  - Deribit public API: DVOL index, `get_book_summary_by_currency` for options OI and IV, futures for basis.
  - Binance/Bybit/OKX instrument lists for perp availability, where reachable. Otherwise use the registry manually.
  - There is no good free liquidation data. Skip it rather than fake it.
- **Macro:**
  - FRED (free key): WALCL, WTREGEN, RRPONTSYD, DFF, SOFR, DGS2, DGS10, DFII10, T10YIE, T10Y2Y, T10Y3M, DTWEXBGS, BAMLH0A0HYM2, NFCI, VIXCLS, DCOILBRENTEU, DCOILWTICO, M2SL, CPIAUCSL, CPILFESL, PCEPILFE, UNRATE, SAHMREALTIME, ICSA, CCSA, JTSLDL, PAYEMS, UNDCONTSA, MEDLISPRIUS, USEPUINDXD, SP500, DEXUSAL, and the state unemployment-rate series (for diffusion).
  - Gold, silver, DXY, NDX, MOVE and equity comparables via yfinance with Stooq fallback. Gold is no longer on FRED.
  - A global M2 proxy: build it from the best free series, convert to USD, and document. If it's unreliable, say so and show US M2 only.
- **Sentiment and attention:**
  - alternative.me Fear & Greed (`/fng/?limit=0`).
  - Wikimedia pageviews API (Bitcoin, Ethereum, Solana, …).
  - Optional: YouTube Data API views and Google Trends via pytrends. pytrends is unstable, so it must be skippable.
  - Coinbase premium (Coinbase BTC-USD vs Binance BTCUSDT, USDT-adjusted).
  - Kimchi premium (Upbit KRW-BTC vs USDKRW).
- **ETFs and treasuries:**
  - BTC/ETH spot ETF flows and holdings: Farside scrape (best-effort) or SoSoValue (check for a free key). Fall back to manual.
  - Public-company BTC holdings: best-effort.
- **Geopolitics and policy:**
  - GDELT DOC 2.0 API (timelinevolraw, timelinetone; free, no key) for themes in `config/themes.yaml`.
  - Caldara–Iacoviello Geopolitical Risk index (daily file, verify the URL).
  - FRED EPU.
  - Polymarket Gamma API and Kalshi public API for event odds, chosen in `config/markets.yaml`.
- **Governance, security, news:**
  - Snapshot GraphQL (`hub.snapshot.org/graphql`) for watched spaces.
  - Tally, if a free key works.
  - Discourse forum `.rss` feeds for each protocol's governance forum.
  - Security incidents: DefiLlama hacks data if free, plus the rekt.news RSS.
  - News RSS: CoinDesk, The Block, Decrypt, Blockworks, plus Fed, SEC and CFTC press releases.
  - GitHub API for developer activity on each project's main repos.
- **Calendar:**
  - FRED `releases/dates` for CPI/PCE/NFP/claims.
  - FOMC dates from federalreserve.gov (verify and store yearly).
  - Deribit expiries (monthly and quarterly, 08:00 UTC).
  - Halving estimate from block height.
  - Token unlocks.
  - Governance deadlines.
  - My manual events in `config/events.yaml`.

**Secrets:** keys go in GitHub Actions secrets and a gitignored `.env`. Attribution: CoinGecko free tier and Lightweight Charts both require it, so add both to the footer.

---

## 6. Pages and modules

### 6.1 Global frame (every page)

- **Header ticker:** BTC, ETH, SOL, total market cap, BTC dominance, ETH/BTC, DXY, US10Y, real 10Y, Brent, gold, SPX, Fear & Greed, and average HL funding across the book. Each shows live or snapshot status and a freshness dot.
- **Clocks:** UTC, Melbourne, Colombo.
- **Command palette on `/`:** type a ticker, then Enter, to open its asset page; type a page name to jump to it. Plus `g`-prefixed shortcuts.
- **Toggles:** AUD/USD everywhere money appears.
- **Footer:** the site-wide disclaimer, source attributions, and the last build time.

### 6.2 Home: "What matters today"

- **Regime banner.** Six gauges, each 0–1 with a trend arrow and a one-line generated read:
  - Cycle risk
  - Liquidity
  - Business cycle / labour
  - Crypto breadth & participation
  - BTC technical regime (MiniLizard 1D)
  - Geopolitical stress

  The overall stance label is one of Preserve / Selective / Deploy. Its derivation is transparent and shown on hover. No false precision.
- **What changed since yesterday.** An automatic diff: regime flips, trigger changes, new highs/lows, big valuation moves, new unlock or governance events. Written as short templated sentences, in the tone of the bond site.
- **BTC key levels:** 20W SMA / 21W EMA band, 50W SMA with the confirmation counter ("weekly closes above: 1 of 2"), 200W SMA, 200D SMA, quantile-band position.
- **The grid.** A heatmap of the 23 assets against:
  - returns (1D/7D/30D/90D) versus USD and versus BTC;
  - regime state;
  - distance from 200D;
  - funding;
  - valuation percentile vs own history;
  - unlock within 30 days.
- **Catalyst radar for the next 7 days** (§6.13).
- **Alerts fired in the last 24h.**
- **Deployment tranche status** (§6.12).

### 6.3 Asset pages (templated; one per asset in the book, the watchlist and the benchmarks)

- **Header:** price, market cap, FDV, float, rank, 24h volume, where it has perps (HL / Binance / Bybit / OKX), data-quality grade.
- **Price chart:** 1D default, with 4H and 1W toggles. Overlays:
  - SMAs 20/50/100/200;
  - 20W SMA / 21W EMA band, 50W and 200W SMAs;
  - previous week and month highs/lows, plus week and month opens;
  - fib levels ported from the Pine source (later phase).
- **Regime panel:** a MiniLizard score sub-pane, with regime shading on the price chart and the GET IN / GET OUT markers. Beside it, the measured backtest stats for this coin (win %, profit factor, net return, max drawdown, Period A/B). Where the backtest found no measured edge, the panel says so in words; currently that's ONDO, SYRUP and PEPE.
- **Relative strength vs BTC:** ratio chart with its own 50/200 averages.
- **Cards:**
  - **Valuation** (§6.4).
  - **Supply & unlocks:** supply history, next unlocks, overhang.
  - **Derivatives:** funding (HL, Binance, Bybit), OI, OI/market cap, basis.
  - **Liquidity:** depth at ±1% and ±2% from HL `l2Book` or Coinbase; dollars needed to move price 1%; 30-day average daily volume; estimated slippage for my typical order size.
  - **Sector KPIs** (§6.4).
- **Thesis:** "why this weight" and watch triggers from YAML, open items with automated checks where possible, and filtered governance, security and news feeds.

### 6.4 Valuation: the equity lens (cross-sectional screen plus a per-asset card)

Tokens aren't equities, but several equity concepts map onto them cleanly. Implement these definitions exactly, and label the annualisation basis everywhere. Show 30d×(365/30), 90d×(365/90) and trailing-365d figures; the default is 90d annualised.

| Equity concept | Token analogue | Definition |
|---|---|---|
| Market cap / fully diluted | MC / FDV | price × circulating supply / price × max supply (or total supply where there is no max) |
| Enterprise value | EV-analogue | MC − treasury in non-native assets (stables, ETH, BTC), where treasury data exists |
| Gross revenue (GMV) | Fees | Everything users pay the protocol (DefiLlama `dailyFees`) |
| Net revenue | Revenue | The part the protocol keeps (`dailyRevenue`) |
| Earnings to shareholders | Holders' revenue | Buybacks, burns and distributions to holders (`dailyHoldersRevenue`) |
| P/S, P/GMV | P/Revenue, P/Fees | MC ÷ annualised revenue or fees. Also show on FDV |
| P/E | P/Holders' revenue | MC ÷ annualised holders' revenue |
| Stock-based compensation | Token incentives | USD value of new supply issued to non-holders (emissions, unlocks, liquidity mining), estimated as Δcirculating × average price over the period, or from emission schedules where available |
| Adjusted earnings | Earnings | Revenue − incentives (Token Terminal's definition) |
| Dilution-adjusted P/E | — | MC ÷ (holders' revenue − incentives). Shown as n/m when ≤ 0 |
| Share-count change | Net dilution | Circulating supply change over 30d/90d/365d, annualised. Split into unlocks, emissions and burns where the data allows |
| Shareholder yield | Holder yield | Annualised holders' revenue ÷ MC |
| **Net shareholder yield** | **Net holder yield** | Holder yield − net dilution rate. **The headline number.** Positive means the token is bought back faster than it's diluted |
| Growth | Revenue growth | 30d vs prior 30d, 90d vs prior 90d, year-on-year |
| PEG | PEG-analogue | P/E ÷ annualised growth, only when both are positive and meaningful |
| P/B | MC/TVL | For DeFi. For lenders use MC/active loans; for stablecoin issuers, MC/stablecoin supply |
| Cash / runway | Treasury/MC, runway | Treasury stablecoins ÷ trailing monthly spend, where known. Otherwise n/a |
| Share overhang | Float, FDV/MC, unlock load | Circulating ÷ total supply; FDV ÷ MC; unlocks over the next 30/90/180/365 days as % of circulating supply and as **days of 30-day average spot volume** needed to absorb them |
| Reverse DCF | Implied growth | Given MC and holders' revenue, solve the 5-year holders'-revenue CAGR needed to justify today's price. Use discount rates of 15/25/35% and exit multiples of 10/20/30× year-5 holders' revenue. Show the grid. It tells me what the price already assumes |
| Payback | Payback years | Years until cumulative holders' revenue (at current growth, decaying) equals MC |

**Chain-level metrics (SOL, ETH, BTC, ZEC):**
- P/F on chain fees, including priority fees and MEV tips where available.
- Real staking yield (staking yield − inflation).
- Stablecoin supply on the chain, as a level and its 30-day change.
- DEX volume and active addresses.
- NVT and NVT signal.
- For SOL only, Metcalfe-style MC vs active addresses², flagged as fragile.

**Sector-specific KPI panels:**
- **DEX (UNI, AERO, RAY):** volume, take rate, fee-switch or holder capture, share of the chain's DEX volume.
- **Lending (AAVE, FLUID, MORPHO):** deposits, active loans, utilisation, reserve-factor revenue, buybacks.
- **Stablecoin & credit (SKY, SYRUP):** stablecoin supply, surplus buffer against target, buyback allocation, loan book.
- **Perp DEX (HYPE):** volume, OI, fees, Assistance Fund buybacks versus emissions, share of Binance perp volume, USDC bridged in.
- **Tokenization (LINK, ONDO, CFG, DUSK, POLYX):** RWA TVL and product AUM, where measurable.
- **Liquid staking (LDO):** stETH share of staked ETH, revenue.
- **Consumer (PUMP):** launchpad revenue, buybacks, share of Solana launches.
- **DePIN (GEOD):** on-chain revenue and burns.
- **AI (TAO):** issuance in USD versus revenue.
- **Privacy (ZEC, AZTEC):** shielded-supply metrics where sourced.

**Relative value.** Every multiple is shown three ways:
- (a) against its own trailing 1-year distribution, as a percentile and z-score, computed point-in-time;
- (b) against the sector median;
- (c) optionally, against listed-equity comparables from yfinance, clearly labelled as rough orientation. Pairings: HYPE vs CME, ICE, COIN, HOOD; UNI/AERO/RAY vs exchanges; SKY/SYRUP vs CRCL and specialty lenders; LINK vs SPGI, MSCI, FDS.

Include a scatter of P/holders' revenue against revenue growth (value vs growth quadrants).

**Data-quality grade (A–F) for each asset:**
- **A:** fees, revenue, holders' revenue and supply history all sourced and cross-checked.
- **F:** no revenue data. Currently TAO, AZTEC, DUSK and POLYX. Grade-F names show "unmeasurable — venture tail" instead of fake multiples.
- Show the grade next to every multiple.

**Market-cap cross-check.** Compute price × circulating supply from at least two sources and flag in red when they disagree by more than 10%. This is exactly the RAY problem: one source put it at $163m, another at $504m.

**Manual quality flags** (YAML, shown as badges):
- governance-switchable value capture: AAVE, UNI and SKY all have capture a vote could switch off;
- audit and admin-key status (FLUID especially);
- vesting-contract verification (ONDO, MORPHO, TAO);
- wash-volume suspicion.

### 6.5 BTC cycle

- **Price structure:** weekly and daily price with the 20W SMA / 21W EMA band, 50W SMA (two-weekly-close confirmation tracker with history), 200W SMA, and Mayer multiple.
- **Quantile bands (§7.2):** show where price sits in the conditional distribution. Overlay the plain OLS power law, labelled with the paper's finding that it has a systematic out-of-sample optimistic bias.
- **On-chain:** MVRV, MVRV Z-score, realized price, NUPL, Puell multiple, market cap vs thermocap (cumulative miner revenue), NVT signal, hash rate and hash ribbons, miner revenue, fees.
- **Cycle analogs:**
  - drawdown from ATH aligned on days since peak, for every cycle since 2013;
  - the mid-2019 apathy-top analog (the Cowen memos in `private/reference/` explain why this matters);
  - running 365-day ROI;
  - year-to-date ROI against prior midterm years (2014, 2018, 2022);
  - days since halving and a halving countdown.
- **ETF and treasuries:** ETF holdings and flows, public-company holdings.
- **Cycle risk scorecard.** My own construction (§7.3), laid out like the memos' table: rows are metrics normalised to 0–1; columns are Current, 6M ago, 1Y ago, 4Y ago.
  - Families: valuation/on-chain, price-based, sentiment/attention, participation.
  - Rows: every metric above that can be sourced for free, plus Fear & Greed, Wikipedia views, optional Google Trends and YouTube, total-market-cap risk, running ROI, and stablecoin dominance.
  - Follower-count style series are noisy. Flag them as low-conviction.

### 6.6 ETH

- ETH/BTC with its 50W average.
- Net issuance (issuance − burn) and supply trend.
- Staking ratio and real staking yield.
- L2 and blob fees.
- ETF flows.
- MVRV, if it can be sourced.
- Fees-based P/F.

### 6.7 Breadth and market structure

- Total market cap, total ex-BTC, total ex-BTC/ETH.
- BTC dominance including and excluding stablecoins.
- Stablecoin dominance; stablecoin supply and its 30- and 90-day change, by chain.
- A top-100 (ex-stablecoin) advance-decline line, built going forward. Any backfill from current constituents must be labelled with its survivorship bias.
- Breadth: % of the top 100 above their 50D and 200D, a new-highs/new-lows count, and an altseason-style reading (% of the top 50 beating BTC over 90 days).
- Rolling 90-day correlations to BTC, cross-asset and within the book. The thesis assumes alt correlation around 0.8, so show when it breaks.

### 6.8 Sector rotation

- Equal-weight and cap-weight sector indices, built from the book and watchlist constituents in the registry.
- A relative rotation graph (RRG) against BTC on weekly data with an 8–12 week tail (§7.4).
- Sector breadth, sector fees growth, and 30/90-day performance.

The thesis is 3–5 sector rotations rather than a broad altseason, so this page should make "which sector is turning up while cheap" obvious.

### 6.9 Derivatives and volatility

- **Funding:** HL, Binance and Bybit for every book and watchlist name that has a perp, annualised and as a z-score against its own history.
- **Open interest:** OI and its change; OI/market cap as a leverage gauge; an OI-change vs price-change quadrant (longs building / shorts building / long liquidation / short covering).
- **Options and basis:** Deribit DVOL for BTC and ETH, the IV term structure, put/call OI, 25-delta skew if it can be computed economically, the futures basis annualised, and the expiry calendar.
- **Volatility:** realised vol (30/90-day) against implied; an ATR% percentile for each asset.
- **Perp availability matrix** across venues.

### 6.10 Macro and liquidity

- **Fed net liquidity** = WALCL − WTREGEN − RRPONTSYD. **Unit alignment matters**: RRPONTSYD is in billions, while WALCL and WTREGEN are in millions. Show the 13-week change.
- **Money and rates:** global M2 proxy YoY, DXY / broad dollar, real 10Y, 2s10s and 3m10y, HY OAS, NFCI, VIX, MOVE.
- **Commodities and ratios:** Brent/WTI, gold, gold/silver, SPX/gold ratio, BTC/SPX.
- **Rolling correlations:** BTC against SPX, NDX, gold, DXY and real yields.
- **Labour:**
  - unemployment and the Sahm rule;
  - initial and continuing claims (4-week average vs its 52-week low);
  - JOLTS layoffs;
  - state-level diffusion: % of states whose unemployment is up at least 0.5pp from their 12-month low.
- **Inflation:** CPI, core CPI, core PCE.
- **Policy path:**
  - EFFR, SOFR, and the 2Y − EFFR spread as a market-implied direction proxy;
  - Polymarket/Kalshi odds for the next FOMC meeting;
  - an FOMC countdown.
- **Composites:** a Liquidity composite and a Business-cycle composite, each 0–1 and point-in-time, with every component visible (§7.3). These are my own transparent constructions, not clones of anyone's proprietary model.

### 6.11 Geopolitics, policy and events

- **Theme trackers:** GDELT article volume and average tone per theme, with a 7-day vs 90-day spike z-score.
  - Default themes: Strait of Hormuz, Iran, Red Sea / Bab al-Mandab, OPEC, sanctions, tariffs, Taiwan, US midterms, SEC/CFTC crypto regulation, stablecoin legislation.
- **Risk indices:** the daily Geopolitical Risk index and EPU.
- **Oil as the transmission variable:** a small annotated diagram of the chain oil → inflation → Fed → real yields/USD → liquidity → crypto, with the live value of each link.
- **Event markets:** selected Polymarket/Kalshi markets with their odds history (which the pipeline snapshots). Search the APIs for current slugs covering the next FOMC decision, Hormuz reopening, midterm House/Senate control, and a US crypto market-structure bill.
- **Narrative log:** my own dated notes from `content/narrative.md`.
- **Unified event calendar** with an `.ics` export I can subscribe to on my phone. It includes: FOMC, CPI, PCE, NFP, claims, options expiries, token unlocks, governance deadlines, the halving estimate, 3 Nov 2026 midterms, and manual events.

### 6.12 Portfolio and deployment

**Privacy model (default "public-safe"):**
- The repo and the published site contain no holdings or dollar amounts.
- Holdings, cost bases and lot dates are entered client-side and stored in localStorage. Provide JSON import/export and optional passphrase encryption via WebCrypto.
- If I choose gated mode (§12), `config/portfolio.yaml` can hold more.

**Book monitor (computed in the browser):**
- **Weights:** actual vs target weights and drift, tier totals against 65/22/13, and the sector-cap check (20%).
- **Next buy:** the next purchase according to the buy order.
- **Gas-threshold check:** each position's size against the current cost of a swap on its chain. Thresholds are ~$300 on Ethereum L1, ~$50 on Solana and ~$100 on a CEX. FLUID and ONDO currently fail the threshold at small book sizes.
- **5× free-carry rule:** flag any position at 5× its cost basis, where the rule says sell the cost basis.
- **Risk:**
  - a drawdown-budget scenario (core −60%, tail −90%) showing total book drawdown;
  - a "what the tail buys" calculator (all tail names to zero vs one 50×) with the market cap each name needs for 10× and 50×;
  - beta to BTC, 90-day correlation matrix, realised vol, and each name's contribution to risk.
- **Tax:** an AU CGT 12-month discount tracker per lot, showing days until eligible. It's informational, not tax advice.

**Deployment tracker.** Each rule shows live status:
- **Tranche A (35%):** fortnightly and unconditional for 26 weeks. Show the next date.
- **Tranche B (40%):** a ladder at BTC −15%, −25% and −35% from the reference price in config (≈ $69k / $61k / $53k). Show the distance to each rung and whether it has fired.
- **Tranche C (25%):** event-triggered. Show the status of each condition:
  - 50W confirmation AND a lower high in BTC dominance;
  - the first Fed cut OR Brent below $70;
  - a US market-structure law passing.
- **Anti-stubbornness rule:** if BTC makes a new ATH above $126k and no Tranche B rung has fired, deploy the remaining B over 12 weeks.

**Open-items tracker,** with an automated check wherever possible:
- verify RAY's market cap;
- SKY surplus buffer reaching its $150m target (the buyback allocation steps back up after that);
- VIRTUAL watchlist triggers;
- the ONDO governance vote and MORPHO fee switch;
- FLUID audit and admin keys;
- vesting contracts for ONDO, MORPHO and TAO;
- governance concentration in AAVE, UNI and SKY;
- net-supply data still missing for 16 of the names.

**Exit plan:** build the mechanism (configurable distribution rules, bands, per-position sell ladders), but **do not invent my exit rules**. It isn't designed yet; show it as "not worked" until I fill in the config. Also surface the horizon mismatch: the plan says "hold until the next halving" (~Apr 2028), but BTC has historically topped about 18 months after a halving.

### 6.13 Catalyst radar, screener and news

- **Catalyst radar ("where is volatility likely").** Universe: the book, the watchlist, and every HL perp. Rank names by a transparent score built from:
  - unlocks within 14 days (sized against volume and float);
  - governance votes opening or closing;
  - extreme funding z-scores;
  - OI spikes;
  - volume z-score above 2;
  - realised-vol expansion;
  - new HL listings;
  - price near a key level (50W, 200D, prior month high/low).
- **Low-cap screener:** CoinGecko markets down to roughly rank 1,000. Filters: MC range, float, FDV/MC, volume/MC, perp availability, and whether revenue data exists.
- **Seed-sleeve pipeline:** a manual YAML tracker for pre-TGE projects (stage, source, notes, next milestone). It's a tracker only; don't try to automate discovery.
- **Feeds:**
  - Governance: Snapshot, Tally, forum RSS for watched spaces.
  - Security incidents that mention held protocols.
  - News, tagged by asset with an alias list.
  - GitHub developer activity per project.
- **Optional LLM daily brief:** off by default. It would summarise the day's changes, strictly grounded in the generated JSON and with citations to panels. It needs an Anthropic key in secrets and has a cost cap. The default is deterministic templated text.

### 6.14 Signals, validation, methodology, source health

- **MiniLizard parity and performance:**
  - the Python engine's regime state per asset;
  - the parity test results against TradingView;
  - backtest statistics recomputed in Python with my harness rules: long while BULL, flat otherwise; fills at the signal-bar close; 0.1% fee + slippage per side; 300-bar warm-up; history split 70/30 into Period A and Period B. They should match my TradingView tables in `private/reference/`.
- **Composite validation:** forward 90/180/365-day BTC returns by composite-risk quintile, computed point-in-time. Show n per bucket, and state the "only ~4 cycles" caveat prominently.
- **Quantile model validation** against the paper (§7.2).
- **Methodology pages:** one per composite and per metric family.
- **Source-health page:** status, freshness, failure history, and the call budget per source.

---

## 7. Specific algorithms

### 7.1 MiniLizard composite regime score: port to Python exactly

The ground truth is the Pine v6 source in `private/reference/`. The spec below is a map, not a substitute; if they disagree, the source wins.

**Data and parity:**
- Binance spot daily bars; confirmed bars only.
- Replicate Pine semantics exactly:
  - `ta.rsi`, `ta.atr` and `ta.dmi` use RMA;
  - `ta.cci` uses mean absolute deviation;
  - `ta.stoch` applied to RSI;
  - `ta.mfi` on hlc3;
  - `ta.ema` seeding;
  - `ta.pivothigh`/`ta.pivotlow` confirmation lag;
  - `na` handling.
- Parity target: my TradingView values to within ±0.5 points after warm-up.

**Price structure (clamped to ±35):**
- `confirmedClose = close[1]`. SMAs of lengths 20/50/100/200 are computed on `confirmedClose`.
- **SMA position** = (Σ over the four SMAs of +1 if close > SMA, else −1) / 4 × 14.
- **Slope:** % change of SMA20 and SMA50 over 3 bars, each clamped to ±1.5. Average them, divide by 1.5, multiply by 10.5.
- **Market structure:** pivots with left = right = 5. Keep the last two confirmed pivot highs and the last two confirmed pivot lows; derive HH/HL/LH/LL from them. MS = (HH + HL − LH − LL) / 2 × 10.5.

**Trend (clamped to ±30):**
- Supertrend(factor 3, ATR 10); bullish when direction < 0.
- Ichimoku 9/26/52 with 26-bar displacement: above-cloud = close > max(senkouA[26], senkouB[26]); TK bull = tenkan > kijun.
- net = bull points − bear points.
- Amplifier = 1.4 if ADX(14,14) > 25, else 1.0.
- Trend = clamp(round(net × amplifier × 10), ±30).

**Momentum (scaled to ±20):**
- Components: (RSI14 − 50) × 0.5; (StochRSI K − 50) × 0.5, where K = SMA3 of stoch(RSI, 14); clamp(CCI20 / 4, ±25); (MFI14 − 50) × 0.5.
- Sum the four, clamp to ±100, then multiply by 20/100.

**Volume (±15):**
- VWAP with a **weekly anchor on daily charts**.
- OBV vs its EMA20.
- CVD from **intrabar delta using 1H bars**: +volume if close > open, −volume if close < open, 0 otherwise; summed per day and cumulated. Compare CVD vs its EMA14.
- Volume = (bull count − bear count) / 3 × 15.

**Extension penalty:**
- ext = (close − SMA20) / SMA20 × 100.
- Threshold on 1D: 5% for BTC/ETH, 12% for everything else.
- Penalty = min(round((|ext| − threshold) × 3.5), 50). Subtract it when over-extended to the upside; add it when over-extended to the downside.

**Composite and regime:**
- Composite = clamp(sum of the four blocks ± penalty, ±100).
- Regime uses hysteresis with a 10-point band:
  - from BULL: → BEAR if score < −20; → NEUTRAL if score < 10;
  - from BEAR: → BULL if score > 20; → NEUTRAL if score > −10;
  - from NEUTRAL: BULL if score > 20, BEAR if score < −20.
- Labels: > 60 STRONG BULL; > 20 MILD BULL; < −60 STRONG BEAR; < −20 MILD BEAR; otherwise NEUTRAL.
- Signals: **GET IN** when the regime becomes BULL. **GET OUT** when it leaves BULL (the model's exit is "score closes below 10").
- Optional V12 filter, off by default: long entries only above SMA200.

**Tiers and variants:**
- Auto tier comes from the symbol lists in the Pine source.
- This configuration is the live engine: V8 on BTC/ETH, V10 on everything else.

**Framing on the site.** The engine is measured as **a drawdown filter, not an alpha source**. In recent data (Period B) it cuts drawdown from ~67% to ~40% but doesn't beat buy-and-hold on raw return. Say that on the page.

**Parity tests:** I will supply TradingView scores for specific assets and dates. Encode them as golden tests.

**Later phase:** port the fib, level ladder and confluence-zone logic. Pivot length is scaled by timeframe; on 1D the tier pivot lengths are 6 / 10 / 20.

### 7.2 Asymmetric quadratic quantile bands for BTC

This follows Cowen (2026), "Asymmetric Tail Curvature in Bitcoin Price Quantiles", which is in `private/reference/`.

**Model:**
- y = log₁₀(price). t = days since 1 Jan 2009. x = ln(t) − μ, where μ is the sample mean of ln(t).
- Q_τ(y) = c_τ + a_τ·x + b_τ·x² for τ ∈ {0.01, 0.10, 0.25, 0.50, 0.75, 0.95, 0.99}.
- Curvature is shared within each tail group:
  - b_LO for {0.01, 0.10, 0.25};
  - a free b_MED for 0.50;
  - b_HI for {0.75, 0.95, 0.99}.
- That gives 17 parameters.

**Estimation:**
- Estimate each tail group jointly by minimising the summed check-loss ρ_τ(u) = u·(τ − 1{u<0}). This is a linear program; use scipy's HiGHS or equivalent.
- Enforce non-crossing with the Chernozhukov–Fernández-Val–Galichon rearrangement: sort the seven predictions at each x.

**Validation gate:**
- Fit on daily data from 2010 to 29 May 2026.
- It must reproduce the paper's values: μ ≈ 7.9914, b_HI ≈ −0.326, b_LO ≈ −0.024, n ≈ 5,788.
- If it doesn't, find out why before using it.

**Display and caveats:**
- Show price's current percentile position within the bands.
- The bands describe the conditional distribution of price level given time. They are **not** return forecasts or tail-risk estimates. Lower-tail curvature isn't statistically distinguishable from zero, so say so on the page.
- For signal use, refit on an expanding window.

### 7.3 Normalised risk (0–1) and composites

**Per-metric risk:**
1. If the metric trends (price, market cap), first take its log residual against a fitted trend, where the trend is refit point-in-time.
2. Risk = the **expanding-window percentile rank** of the value among all values up to that date.
3. Require a minimum warm-up of 2–4 years of daily data. Mark the warm-up period and don't show it as signal.

**Composites:**
- Family risk = the mean of its component risks. The overall composite = a weighted mean of family risks, with weights in `config/composites.yaml`.
- Always show components alongside the composite.
- Show 6M, 1Y and 4Y-ago columns.
- Everything is point-in-time.

**Naming:** use descriptive names for my own constructions. Don't borrow ITC or Cowen proprietary metric names or branding.

### 7.4 RRG (relative rotation graph)

- RS = asset/benchmark × 100 (weekly).
- RS-Ratio = 100 + z-score of RS against its N-week mean and standard deviation. Default N = 10.
- RS-Momentum = 100 + z-score of RS-Ratio's rate of change over M weeks. Default M = 4.
- Document that this is an open approximation of the JdK method, not the proprietary formula.

### 7.5 Formatting rule

Format prices to significant digits by magnitude, never a fixed 2 decimal places (tiny prices must not display as 0). Always use tabular numerals. Always label units and annualisation.

---

## 8. Alerts (Telegram bot; alert state and dedupe stored in the repo)

- **Regime:** GET IN / GET OUT on 1D close for every book and watchlist name, with score, nearest support and resistance, and the model's exit rule. This replaces my TradingView alerts, which expire and are capped by my plan.
- **BTC levels:** weekly close vs the 50W SMA (confirmation count), 200W SMA, and quantile-band crossings.
- **Deployment:** Tranche B rungs, Tranche C conditions, the anti-stubbornness rule.
- **Portfolio:** sector-cap breach, 5× free-carry, gas cost dropping below a position's threshold ("cheap to buy the EVM names today").
- **Supply:** an unlock of ≥1% of circulating supply within 7 days for a held name.
- **Governance:** a vote opening or closing in a watched space.
- **Derivatives:** funding |z| > 2, or annualised funding beyond a configurable threshold.
- **Stablecoins:** a depeg above 0.5% (USDC, USDT, USDS, USDe).
- **Security:** an incident mentioning a held protocol.
- **Data:** a critical source failing for more than 24h.
- **Delivery:** a morning digest message plus immediate alerts for critical events. Quiet hours are configurable.

---

## 9. Design and UX

- **Look:** a dense, dark terminal aesthetic that's still readable. Restrained palette with one accent (amber). Monospace tabular numerals (e.g. JetBrains Mono or IBM Plex Mono) and a clean sans for prose. Colour semantics are consistent and always paired with a sign or arrow, never colour alone. It should feel related to the Nelson-Siegel site. Avoid the generic AI-dashboard look (gradients, cards everywhere, emoji).
- **Tables are the primary surface:** sortable, with sparklines in cells, sticky headers, and CSV download.
- **Help:** every metric has a hover definition. Every panel has a "how to read this" line and an as-of badge.
- **Mobile-first for Home, Asset, Portfolio and Radar.** I check these on my phone. The other pages must stay usable at 390px.
- **Performance:** per-page JSON, lazy-loaded charts, and a fast first paint on mobile.
- **Accessibility:** WCAG AA contrast and keyboard navigation throughout.

---

## 10. Engineering standards

- **Repo layout:** propose it in PLAN.md. It should cleanly separate `pipeline/` (fetchers, store, metrics, composites, signals, alerts), `site/`, `config/`, `content/`, `data/`, `docs/`, `private/` (gitignored) and `tests/`.
- **Single CLI:** e.g. `uv run terminal probe | fetch --only macro | build | serve | validate | alerts --dry-run`.
- **Idempotent pipeline:** re-running on the same day is safe.
- **Tests:**
  - unit tests for every metric definition (annualisation, units, dilution maths);
  - golden tests for the MiniLizard parity and the quantile paper replication;
  - data validation: monotone timestamps, no duplicate dates, non-negative where required, and flagged jumps above a threshold.
- **CI** runs lint and tests on every push. The deploy happens only after the pipeline and tests pass.
- **Secrets** live only in Actions secrets and `.env`. Nothing sensitive is ever committed.

---

## 11. Phases (each ends deployed, tested, screenshotted and reported)

**P0 — Scaffold and probe**
- Repo, CI, Pages deploy of a skeleton (header, clocks, command palette, empty pages marked "not built yet").
- Source probe and `SOURCES.md`, draft registry, `PLAN.md`, the §12 questions.
- Acceptance: the skeleton is live, and I've confirmed the registry and answered the questions.

**P1 — Prices, assets, regime engine**
- OHLCV for all assets; asset pages with charts, moving averages and RS vs BTC; the heatmap grid; the live WebSocket ticker.
- The MiniLizard port with parity tests; Home v1.
- Acceptance: parity passes on every date I supply.

**P2 — BTC cycle and macro**
- BTC cycle page including the 50W tracker, quantile bands (replication gate passed), on-chain metrics, analogs and the risk scorecard.
- Macro and liquidity page; composites v1; the regime banner.

**P3 — Fundamentals and valuation**
- DefiLlama fundamentals; own supply history begins; the full §6.4 screen and cards; grades; cap cross-checks; sector KPIs; reverse DCF; equity comparables.

**P4 — Supply, derivatives, breadth, sectors**
- Unlocks (manual YAML if needed), derivatives page, breadth page, sector indices and the RRG.

**P5 — Portfolio, deployment, alerts**
- Client-side holdings; the book monitor; the deployment tracker; the open-items tracker; Telegram alerts; `.ics` export.

**P6 — Geopolitics, catalysts, news, screener**
- GDELT, GPR and EPU; Polymarket/Kalshi; governance, security and news feeds; the catalyst radar; the low-cap screener; the seed tracker.

**P7 — Validation and polish**
- Validation pages, methodology, source health, performance and accessibility passes, and final docs.

---

## 12. Ask me these in one batch at the end of planning

1. **Public or gated?**
   - **Public (default):** GitHub Pages, and the portfolio lives only in my browser.
   - **Gated:** e.g. Cloudflare Pages + Cloudflare Access (free) behind my email, which allows server-side portfolio config.

   Give me the trade-offs, including Actions minutes on a private repo.
2. **Keys to register:** list exactly which free keys you need and where each goes. Expected: FRED, CoinGecko Demo, CoinMarketCap Basic, Etherscan, Telegram bot; optionally YouTube Data and Anthropic.
3. **Parity values:** which assets and dates you want TradingView MiniLizard scores for.
4. Default display currency (AUD or USD) and my typical order size for the slippage and gas calculations.
5. Polymarket/Kalshi markets and GDELT themes to add beyond the defaults.
6. Alert quiet hours and the digest time.
7. If geoblocks bite: self-hosted runner, Cloudflare Worker, or accept degraded sources?

---

## 13. Reference material in `private/reference/` (gitignored; never commit or publish)

- **MiniLizard Pine sources:** ML Levels v4.3 (indicator) and the Regime Backtest harness. These are the ground truth for §7.1.
- **My notes:**
  - `claude_minilizard-levels-v4.md`, `claude_minilizard-v41-daily.md`, `claude_minilizard-winrate-sep2026.md`, `claude_minilizard-handoff.md`: engine history, backtest method and results tables.
  - `claude_alt-portfolio-final-allocation.md`: the book, rationale, open items and data-hygiene findings.
- **The quantile paper and Cowen memos (Feb, Q1, Mar, Q2, Jul 2026).**
  - These "PDFs" are actually ZIP archives of per-page `.txt` and `.jpeg` files. Standard PDF parsers fail on them, so unzip them.
  - The memos are confidential. Use them only to understand which metrics and framework matter. Never reproduce their content, charts or metric branding on the site.

---

## 14. Don'ts

- Don't fabricate, interpolate or back-fill without labelling it.
- Don't compute any percentile, z-score or fit with look-ahead.
- Don't let one source failure break the build.
- Don't add paid services, trading execution, private keys or wallet connections.
- Don't commit anything from `private/`, any holdings, or any secret.
- Don't clone proprietary metrics or branding.
- Don't invent my exit plan or trading rules. Build the mechanisms, and leave the rules for me to fill in.
- Don't pad the UI with metrics that have no source or no stated purpose. Every panel must earn its place with a "how to read this".
