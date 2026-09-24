# MiniLizard Crypto Terminal — build plan

Status: **Phase 0, awaiting your answers.** No application code exists yet, by
design (brief §0.1). What exists is the probe, the draft registry, and this.

This plan is written against evidence collected on 2026-09-24 by
`tools/probe.py`, not against what the brief remembered the APIs doing. Where
the two disagree, the probe wins and the disagreement is logged in
[DECISIONS.md](DECISIONS.md). Section 9 below lists every deviation.

---

## 1. What this thing is

A static site, rebuilt by GitHub Actions, that answers three questions each
morning: what is BTC/ETH and the 21-name book doing, what are the macro and
liquidity conditions around them, and what does the deployment plan say to do
about it. It executes nothing, holds no keys that can trade, and connects to no
wallet.

It is the Nelson-Siegel site's bigger sibling, and §2 says what that inheritance
does and does not extend to.

---

## 2. What is inherited from Nelson-Siegel, and what is not

I read the whole repository. These conventions are sound and are adopted:

1. **Numbers live in code, never in prose.** `signals/validate.py` says it
   outright: *"A number pasted into a document is a number that will be wrong
   later, so the document should cite this command instead of repeating it."*
   This becomes a hard rule here, reinforced by brief §0.4: no market number is
   hard-coded in logic or UI, and the methodology pages cite `terminal validate`
   rather than quoting its output.
2. **Carry-forward is labelled, and stops being a lag at some point.** NS carries
   a bond's last print for up to 10 days, draws it open rather than solid, tips
   it with its own date, and past a threshold prints *"3 days stale"* as a fault
   rather than showing a number as though it were today's. That is exactly the
   treatment brief §2.2 asks for, and it is copied wholesale.
3. **Name what is excluded and why.** NS's *"Trading, but not scored yet — a
   z-score needs about 30 days of history and these have less"* is the model for
   every warm-up and missing-data state here. Silence is the failure mode to
   avoid, not emptiness.
4. **Point-in-time by construction.** `_rolling_z` does `values.shift(1)` with
   the comment *"excluding today: no lookahead"*. §5.3 below generalises this.
5. **Gate before you rank.** NS refuses to rank on z-score alone because *"ranking
   on z-score alone selects for illiquid bonds, whose stale quotes jump and so
   score highest."* The catalyst radar (§6.13) has the identical failure mode —
   a volume z-score of 4 on a dead microcap — and gets the same liquidity gate.
6. **Colour is a role, and never carries meaning alone.** `dashboard/palette.py`
   separates categorical / polarity / status and pairs every status colour with a
   word. Adopted verbatim, which also satisfies the brief's accessibility ask.
7. **Findings that do not survive out of sample are reported and then not used.**
   NS measures the tenor split, finds it fails out of sample, says so, and
   bucketes nothing by tenor. That is the standard for every signal here.
8. **Committed data, so a clone is immediately useful**, and a scheduled job that
   makes no commit on a day with no news.

**What is NOT inherited: the rendering stack.** NS is a Python program that
writes one self-contained `docs/index.html` with an inline SVG and no external
requests. That is the right answer for one page with one chart. It is the wrong
answer here, and I am deviating deliberately:

| Requirement | Why the NS approach cannot carry it |
|---|---|
| ~40 routes (23+ asset pages, 12 section pages, methodology) | One generator function per page shape stops being maintainable |
| Live WebSocket ticker (§3, §6.1) | Needs real client-side code |
| Portfolio computed in the browser, localStorage + WebCrypto (§6.12) | Cannot be server-rendered — that is the entire privacy model |
| Sortable tables, command palette, AUD/USD toggle (§6.1, §9) | Client-side state |
| Lightweight Charts / ECharts (§3) | JS libraries |

So: **Vite + TypeScript, multi-page, no framework** — the brief's own default.
Static HTML per route, per-page JSON, no SPA router, no hydration. The NS
*conventions* above all survive the change of stack; only the renderer differs.

---

## 3. Architecture

```
  fetchers ──► raw response cache ──► normalised history store (Parquet)
                                            │
                                            ▼
                                     metrics + signals
                                   (recomputed each build,
                                    expanding windows only)
                                            │
                                            ▼
                                  per-page JSON artefacts
                                            │
                                            ▼
                              Vite build ──► GitHub Pages
                                            │
                                     browser live layer
                                (Hyperliquid WS, Coinbase WS)
```

**Pipeline:** Python 3.12 via uv. httpx, polars, pydantic, scipy, statsmodels,
pyarrow; ruff and pytest. Polars over pandas for the store (lazy scans over
partitioned Parquet, and it is strict about schema, which matters when the same
column arrives from three sources).

**Single CLI**, as §10 asks:

```
uv run terminal probe                    # tools/probe.py, unchanged
uv run terminal fetch --only macro       # or: prices, defillama, derivs, geo, news
uv run terminal build                    # metrics -> artefacts -> site JSON
uv run terminal site                     # vite build
uv run terminal serve                    # local preview
uv run terminal validate                 # the NS-style honesty report
uv run terminal alerts --dry-run
```

`fetch` and `build` are idempotent: re-running on the same day overwrites the
same partition rows and produces byte-identical artefacts.

### 3.1 The history store

Partitioned Parquet, append-only, one directory per dataset:

```
data/
  ohlcv/{symbol}/{year}.parquet            date, o,h,l,c,v, source, interval
  snapshots/supply/{yyyy-mm}.parquet       as_of, symbol, circulating, total, max, source
  snapshots/derivs/{yyyy-mm}.parquet       as_of, symbol, venue, funding, interval_h, oi, mark
  snapshots/depth/{yyyy-mm}.parquet        as_of, symbol, venue, bid_1pct, ask_1pct, ...
  snapshots/odds/{yyyy-mm}.parquet         as_of, market_id, outcome, probability
  snapshots/unlocks_asseen/{yyyy-mm}.parquet
  fundamentals/{parent_slug}/{dataType}.parquet
  macro/{series_id}.parquet                obs_date, value, vintage_fetched_at
  onchain/{asset}/{metric}.parquet
  source_health.parquet                    source, endpoint, as_of, fetched_at, rows, status, error
```

Monthly partitions for anything snapshotted every 2h, yearly for daily series.
That keeps each commit's diff to the current partition instead of rewriting
years of history.

**Deviation (D008): the store lives on an orphan `data` branch, not on `main`.**
The brief allowed either. NS puts its SQLite on main and its `data/` is already
21MB after nine months — but NS writes once a day, and this pipeline writes the
HL perp universe (234 names) every two hours, roughly 1M snapshot rows a year.
Parquet does not delta-compress in git, so on main that is unbounded growth in
every developer clone forever. On a `data` branch, `main` stays code-only and
the data history can be squashed on a schedule without touching code history.

**Cost, stated plainly:** the build checks out two refs instead of one, and
after a squash the per-day audit trail of the store is gone. Mitigation: a
monthly immutable Parquet archive is kept unsquashed, so any month can still be
reconstructed as it stood.

### 3.2 Schedules

Off-the-hour, as the brief requires, because Actions cron slips badly at `:00`.

| Workflow | Cron (UTC) | Does |
|---|---|---|
| `daily.yml` | `20 0 * * *` | Full build after the crypto daily close, so 1D regime scores use confirmed bars |
| `light.yml` | `35 */2 * * *` | Prices, funding, OI, depth, odds, unlock countdowns |
| `macro.yml` | `40 19 * * *` | Macro, news, governance — timed to land for your morning |
| `keepalive.yml` | `15 3 * * 1` | Commits a timestamp so the 60-day public-repo scheduler cutoff never fires |

### 3.3 Live layer

The browser subscribes to Hyperliquid `allMids` and Coinbase `ticker` for
ticking prices, falling back to the last built snapshot with a stale dot. No key
ever reaches the client. CORS and WS reachability from a `github.io` origin is
an explicit P1 acceptance test, not an assumption — the probe records the
`access-control-allow-origin` header for every source but a WS handshake from a
real browser origin is a different question, and Playwright will answer it.

---

## 4. What the probe found

Full results: [SOURCES.md](SOURCES.md), raw JSON in `probe-*.json`.
Headline: **49 of 69 sources reachable, 5 need a free key, 15 failing** from US
egress. The failures that change the design:

| Finding | Consequence |
|---|---|
| `api.binance.com` → **451**, `api.bybit.com` → **403** from US | Confirmed. `data-api.binance.vision` works (200) and carries spot klines, so **no self-hosted runner is needed for prices** |
| HL `predictedFundings` returns `BinPerp`, `HlPerp`, `BybitPerp` for 234 coins | The whole Binance/Bybit funding requirement is met in one call. Note the intervals differ (HL 1h, Binance/Bybit 4–8h) — annualisation must use each venue's `fundingIntervalHours`, not a constant |
| DefiLlama `/emissions`, `/emission/{slug}`, `/treasuries` → **402 paid** | Unlocks and treasury move to hand-maintained YAML (D007) |
| CoinMetrics community = 31 metrics; realised cap, NVT, transfer value paywalled | Realised cap / NUPL / thermocap / Puell all derivable from free metrics; **NVT is dropped** (D005) |
| Holders' revenue probed at **exactly 0** for AAVE, FLUID, MORPHO, ONDO, CFG | Not the same as "no data" — see §5.4. AAVE in particular needs resolving before any P/E is shown |
| FLUID, GEOD, AZTEC have **no Binance pair** | MiniLizard parity impossible for them (D006) |
| Stooq refused all connections from US cloud egress | The yfinance fallback may not exist where the pipeline runs. **Awaiting the runner result** before choosing a macro fallback |
| Wikimedia 403 → 200 once the User-Agent carries contact details | Fixed; the exact UA string is recorded in SOURCES.md |
| growthepie: `/v1/fundamentals.json`, not `fundamentals_full.json` | Fixed |
| rekt.news RSS → 500 / dead | Dropped. DefiLlama `/hacks` (200) becomes the security source |
| Farside → 403 (Cloudflare); SoSoValue → 404 | **ETF flows have no working free source.** See §9 |
| CoinGecko unauthenticated 429s within a handful of calls | The Demo key is not optional, it is required |
| GDELT → 429 under rapid calls | Needs spacing and backoff, not a different source |

---

## 5. Data model and the correctness rules

### 5.1 Identity

Resolved by CoinGecko id and contract address, never ticker — and this is not
theoretical. The 21,550-coin list has **7 live coins with symbol SKY** (one a
Solana memecoin), 4 with PUMP, 5 with HYPE, 3 with TAO. Three of the book's ids
do not resemble the ticker: `FLUID → instadapp`, `CFG → centrifuge-2`,
`PUMP → pump-fun`. See `config/assets.yaml`, which is drafted and awaiting your
confirmation.

### 5.2 Every fetch writes `source_health`

`source, endpoint, as_of, fetched_at, rows, status, error` — appended on success
and on failure. A failing source never raises out of the build; it writes a row,
the last good value is carried forward with a stale flag, and the failure
appears on the source-health page. That is brief §2.8 and NS's practice both.

### 5.3 Point-in-time, and where look-ahead actually gets in

Derived series (percentiles, z-scores, normalised risk, composites, the quantile
fit used for signals) are **recomputed from raw history on every build** with
expanding or trailing windows that exclude the observation being scored. That is
idempotent and cannot silently drift the way stored derived values can.

The subtle part is that this alone does **not** give point-in-time correctness,
and it is worth being explicit because it is the easiest thing to get wrong:
look-ahead mostly enters through *revised source data*, not through the window
maths. FRED revises macro series; CoinGecko restates circulating supply; an
unlock schedule is edited after the fact. Recomputing history from today's
values of those series produces a backtest that quietly knew the future.

So the store keeps **vintages** for anything revisable: macro rows carry
`vintage_fetched_at`, supply and unlock schedules are snapshotted as-seen daily
from day one, and any series used in a validated signal is read at the vintage
available on the signal date. Where no vintage exists yet (everything before
today), the affected panel says so rather than implying a clean backtest.

### 5.4 Data-quality grades — a third category the brief does not have

The brief's scale runs A (fully sourced and cross-checked) to F (no revenue
data: TAO, AZTEC, DUSK, POLYX — all four confirmed by probe). But probing turned
up a case that is neither: **AAVE, FLUID, MORPHO, ONDO and CFG have years of
fee and revenue series and a holders' revenue of exactly zero.**

That is a *measurement*, not a gap, and it must not be graded F — "unmeasurable"
would be a lie about a name with 2,121 days of data. Proposed third state:

- **A–C** — sourced, cross-checked, value capture measurable and non-zero.
- **D — "measured, capture is zero."** P/E and PEG show `n/m`; holder yield shows
  `0.0%`; **net holder yield is still computed and is negative**, because dilution
  is real and that is the honest headline for these names.
- **F — unmeasurable.** No revenue data at all. Shows "unmeasurable — venture
  tail" and no multiples, per the brief.

AAVE is flagged as an open item rather than assigned: a public buyback programme
and a DefiLlama zero most likely means DefiLlama books it under another slug, and
I will not publish a P/E either way until that is resolved.

---

## 6. Pages

| Route | Contents | Phase |
|---|---|---|
| `/` | Regime banner (6 gauges), what-changed diff, BTC key levels, 23-asset grid, 7-day catalyst radar, alerts, tranche status | P1 → P6 |
| `/asset/{symbol}` | Header, price chart + overlays, MiniLizard pane, RS vs BTC, valuation / supply / derivs / liquidity / sector cards, thesis | P1 → P4 |
| `/btc-cycle` | 50W tracker, quantile bands, on-chain, cycle analogs, ETF, risk scorecard | P2 |
| `/eth` | ETH/BTC, net issuance, staking, L2+blob fees, ETF, P/F | P2 |
| `/valuation` | The §6.4 cross-sectional screen, grades, cap cross-check, scatter | P3 |
| `/breadth` | Caps, dominance, A-D line, breadth %, correlations | P4 |
| `/sectors` | Sector indices, RRG, sector breadth and fee growth | P4 |
| `/derivatives` | Funding, OI quadrant, options/basis, vol, perp matrix | P4 |
| `/macro` | Net liquidity, rates, commodities, labour, inflation, policy path, composites | P2 |
| `/geopolitics` | GDELT themes, GPR/EPU, the oil transmission chain, event markets, narrative | P6 |
| `/portfolio` | Client-side book monitor, deployment tracker, open items, exit mechanism | P5 |
| `/radar` | Catalyst radar, low-cap screener, seed tracker, feeds | P6 |
| `/calendar` | Unified calendar + `.ics` export | P5 |
| `/signals` | Parity results, backtest stats, composite and quantile validation | P7 |
| `/source-health` | Status, freshness, failure history, call budget | P0 skeleton → P7 |
| `/methodology/*` | One page per composite and metric family | ongoing |

---

## 7. Algorithms — what is settled and what is blocked

### 7.1 MiniLizard port — **BLOCKED**

`private/reference/` does not exist in this repository and no Pine source is on
disk. §7.1 says the Pine source is ground truth and the brief's prose is "a map,
not a substitute". I can build the engine from the map, but I cannot verify the
things that decide parity: `ta.ema` seeding, pivot confirmation lag, `na`
handling, the V8/V10 tier symbol lists, and the exact V8-vs-V10 differences.

I will not guess at those and call it a port. **The Pine sources and your notes
are the single biggest blocker to P1.**

Settled regardless: the composite blocks and clamps, the 10-point hysteresis
band, the label thresholds, the GET IN / GET OUT rules, and the framing — the
engine is presented as a **drawdown filter, not an alpha source**, cutting
Period-B drawdown from ~67% to ~40% without beating buy-and-hold on return.

### 7.2 Quantile bands — **ready, with a hard gate**

Check-loss LP via scipy HiGHS, tail-grouped curvature (17 parameters),
CFG rearrangement for non-crossing. The replication gate is a pass/fail test:
μ ≈ 7.9914, b_HI ≈ −0.326, b_LO ≈ −0.024, n ≈ 5,788 on 2010 → 2026-05-29.
CoinMetrics `PriceUSD` supplies the 2010 history (verified reachable). If the
fit misses those values the bands do not ship, per the brief.

The page will state that the bands describe the conditional distribution of
price level given time — not a return forecast, not a tail-risk estimate — and
that lower-tail curvature is not statistically distinguishable from zero.

### 7.3 Normalised risk and composites — ready

Expanding-window percentile rank, log-residual-against-refit-trend for trending
inputs, 2–4 year warm-up marked and excluded from signal, family means, weights
in `config/composites.yaml`, components always shown beside the composite.
Descriptive names only; no borrowed branding.

### 7.4 RRG — ready

RS-Ratio and RS-Momentum as specified, documented as an open approximation of
the JdK method rather than the proprietary formula.

### 7.5 Formatting — ready

Significant digits by magnitude, never fixed 2dp; tabular numerals; units and
annualisation basis labelled on every figure.

---

## 8. Things in the brief I think are wrong, weak, or not worth building

Brief §0.7 asks me to say so plainly.

1. **The composite-validation table has almost no statistical power, and should
   say so louder than the brief suggests.** "Forward 90/180/365-day BTC returns by
   composite-risk quintile" over 2010–2026 looks like ~5,800 daily observations,
   but daily observations of a 365-day forward return overlap almost completely.
   The effective sample is roughly **four independent cycles**, and quintiles of
   four cycles is not an estimate of anything. I will build it because it is a
   useful *description* of where past cycles sat, but it gets a caveat stating
   the effective n, not just the "only ~4 cycles" note, and it will not be
   expressed as an expected return.

2. **Reverse DCF is undefined for most of the book.** It solves for the
   holders'-revenue CAGR justifying today's price — but holders' revenue is zero
   for AAVE, FLUID, MORPHO, ONDO and CFG, and absent for TAO, AZTEC, DUSK and
   POLYX. That is 9 of 21 names where the grid cannot be computed at all, and
   several more where a near-zero denominator makes the implied CAGR explode.
   It ships for the names where it means something and prints "not computable —
   holders' revenue is zero" for the rest, rather than a very large number.

3. **PEG will be noise for almost everything.** It needs positive P/E *and*
   positive growth, on 30d/90d windows of a series that routinely swings by
   multiples. Building it as specified; expect it to be the least useful column
   on the page and it will be marked low-conviction.

4. **ETF flows have no working free source.** Farside is behind Cloudflare (403)
   and SoSoValue's endpoint is gone. This is a genuine gap, not a rate limit. My
   recommendation is to leave the ETF panel as "source unavailable" rather than
   scrape a site that is actively refusing us — see the question in §10.

5. **Metcalfe for SOL** is kept, flagged fragile, as the brief already says. I
   would not draw any conclusion from it.

6. **"Follower-count style series"** — I am not building them. They are noisy,
   the free APIs for them are unreliable, and the brief already marks them
   low-conviction. If you want them, say so and they go in P6.

7. **The buy order is missing three names.** §4.2 lists 21 book names but the buy
   order names 18: **HYPE (6%), LINK (6%) and SYRUP (4%)** have no position in it,
   which is 16% of the book. I have not invented positions for them. Question in §10.

---

## 9. Deviations from the brief, with reasons

| # | Brief says | Plan does | Why |
|---|---|---|---|
| D002 | No application code in session 1 | Probe is a standalone PEP 723 script | §0.1.c requires running the probe; keeping it out of `pipeline/` honours both |
| D003 | Probe "locally and on a runner" | US-cloud + runner; **your local run is outstanding** | This container egresses from Ohio; it cannot produce a non-US datapoint |
| D005 | CoinMetrics gives 12 named metrics | 7 free + 5 derived; **NVT dropped** | Community tier is 31 metrics; transfer *value* is paywalled |
| D006 | MiniLizard on Binance bars for all | 3 names on documented substitutes | FLUID, GEOD, AZTEC have no Binance pair |
| D007 | DefiLlama unlocks / treasury | Hand-maintained YAML, snapshotted daily | Both endpoints are 402 |
| D008 | Data in `data/` or a data branch | Orphan `data` branch, monthly squash | 2-hourly snapshots on main would grow clones without bound |
| — | Default Vite + TS, or match NS | Vite + TS, MPA, no framework | NS's single-file generator does not extend to 40 routes, a live layer and browser-side portfolio state |
| — | Grades A–F | A–C, **D (capture measured at zero)**, F | Five names have full data and zero capture; grading that F would misdescribe it |
| — | pandas and/or polars | polars for the store | Lazy partitioned scans and strict schemas |
| — | Security via rekt.news RSS | DefiLlama `/hacks` | rekt feed returns 500 |

---

## 10. Phase plan with acceptance criteria

Every phase ends: tests pass → full pipeline run cold → site built → Playwright
screenshots of every page at 1440px and 390px, inspected → short report.

**P0 — Scaffold and probe** *(in progress)*
- Skeleton deployed to Pages: header ticker, three clocks, command palette,
  every route present and marked "not built yet".
- `tools/probe.py`, `SOURCES.md`, draft registry, `PLAN.md`, the questions.
- **Accept when:** the skeleton is live; you have confirmed `config/assets.yaml`;
  you have answered §11; no route shows a zero or a placeholder number.

**P1 — Prices, assets, regime engine**
- OHLCV for all names; asset pages with overlays and RS vs BTC; the 23-asset
  grid; the live WS ticker; MiniLizard port; Home v1.
- **Accept when:** parity passes within ±0.5 points on every asset/date you
  supply; the three `parity: substitute` names say so on their pages; a killed
  source degrades to stale rather than breaking the build (tested by fault
  injection); WS reconnects and falls back to snapshot, verified in a browser.

**P2 — BTC cycle and macro**
- **Accept when:** the quantile replication gate passes (μ, b_HI, b_LO, n);
  net liquidity is unit-correct (RRPONTSYD in billions against WALCL/WTREGEN in
  millions — asserted by a unit test, since this is the single easiest error to
  make on the page); composites are point-in-time with warm-up marked.

**P3 — Fundamentals and valuation**
- **Accept when:** every multiple is labelled with its annualisation basis; the
  two-source cap cross-check flags a >10% disagreement in red (RAY is the test
  case); grade D vs F is applied correctly; supply history begins accumulating;
  no grade-F name displays a multiple.

**P4 — Supply, derivatives, breadth, sectors**
- **Accept when:** funding is annualised using each venue's own interval;
  the A-D line is forward-built and any backfill is labelled with its
  survivorship bias; RRG documented as an approximation.

**P5 — Portfolio, deployment, alerts**
- **Accept when:** no holding or dollar amount exists anywhere in the repo or the
  built site (asserted by a test that greps the build output); import/export and
  passphrase encryption round-trip; tranche rules show live status; the exit
  panel reads "not worked" because `config/exits.yaml` is empty; Telegram
  alerts dedupe across runs.

**P6 — Geopolitics, catalysts, news, screener**
- **Accept when:** the catalyst radar is liquidity-gated before ranking; GDELT
  backs off cleanly on 429; odds history accumulates.

**P7 — Validation and polish**
- **Accept when:** `terminal validate` prints sample sizes and states plainly
  where there is no measured edge; WCAG AA contrast verified; every panel has a
  "how to read this" and an as-of badge.

---

## 11. Open blockers

1. **`private/reference/` is absent.** No Pine sources, no notes, no memos, no
   quantile paper. This blocks the P1 parity port (§7.1), the backtest tables
   (§6.14), and the V8/V10 tier lists. Everything else can proceed.
2. **Your local probe run** is the missing half of §0.1.c.
3. **The §11 questions** below.
