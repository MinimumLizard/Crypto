# Decisions log

Append-only. Newest last. Every entry records what was decided, what evidence
forced it, and what it costs. A decision without a cost stated is usually a
decision that has not been thought about.

---

## 2026-09-24 — D001: `private/` is gitignored before anything else is written

The brief's §13 material is confidential and §14 forbids committing it. The
`.gitignore` covering `private/`, `.env`, `config/portfolio.yaml` and
`content/holdings*.json` was committed as the first act of the repository, and
`git check-ignore` was run to prove it matches rather than assumed to.

**Cost:** none.

---

## 2026-09-24 — D002: the probe is a standalone PEP 723 script, not pipeline code

§0.1 says write no application code in the first session, but §0.1.c requires
running the source probe. Those are only compatible if the probe is not
application code. `tools/probe.py` therefore carries its own inline dependency
metadata, imports nothing from the project, and will not be imported by the
pipeline when the pipeline exists.

**Cost:** the probe duplicates a little HTTP handling that `pipeline/fetchers`
will later have. Worth it: the probe must be runnable on a bare runner with no
project install, which is exactly what makes it a fair test of the runner.

---

## 2026-09-24 — D003: this container is NOT a substitute for your local machine

The brief asks for probe results "locally and on a GitHub-hosted runner". This
session runs in an Anthropic cloud container that egresses from **Columbus,
Ohio (AS396982, Google LLC)**. Verified: `api.binance.com` returns 451 and
`api.bybit.com` returns 403 from here, exactly as they do from a GitHub runner.

So this session can produce only ONE of the two datapoints. What is labelled
"US cloud" here stands in for the runner; the genuine non-US comparison has to
come from your own machine, wherever you are running it.

**Cost:** one of the two probe locations the brief asked for is outstanding.
`uv run tools/probe.py --location melbourne` produces it in about four minutes.

---

## 2026-09-24 — D004: DefiLlama value capture is read from the PARENT slug

Probing `/protocols` (8,352 entries) showed the book's names are not one slug
each. Aave is `aave-v2`/`aave-v3`/`aave-v4` under `parent#aave`; Uniswap has
four versions; Fluid has four; Sky's children sit under `parent#maker`.
Enumerating children and summing them double-counts wherever the parent already
aggregates. Querying `/summary/fees/{parent}` returns the aggregate directly and
was verified to respond for every book name that has fee data at all.

Three parent slugs are NOT the obvious guess and would have silently returned
the wrong protocol or a 400:

| Asset | Wrong guess | Correct parent slug |
|---|---|---|
| PUMP | `pump-fun` | `pump` (pump.fun + pumpswap) |
| SYRUP | `maple` | `maple-finance` |
| SKY | `maker` | `sky` |

**Cost:** `llama_children` must still be stored so the aggregate can be audited
against its parts, which is one extra call per asset per day.

---

## 2026-09-24 — D005: CoinMetrics community tier is far thinner than §5.1 assumed

The catalog returns **31** BTC metrics, not the full set. Of the twelve the
brief names, five are paywalled: `CapRealUSD`, `RevUSD`, `TxTfrValAdjUSD`,
`FeeTotUSD` and `NVTAdj` all return HTTP 403 "not available with supplied
credentials".

Most of the BTC cycle page survives anyway, because the paywalled quantities
are *derivable* from free ones:

| Wanted | Free route |
|---|---|
| Realised cap | `CapMrktCurUSD / CapMVRVCur` |
| Realised price | realised cap ÷ `SplyCur` |
| NUPL | `1 − 1/CapMVRVCur` |
| Miner revenue USD | `IssTotUSD + FeeTotNtv × PriceUSD` |
| Thermocap | cumulative miner revenue, as above |
| Puell multiple | `IssTotUSD` ÷ its own 365d mean |

**NVT and NVT signal do not survive.** They need `TxTfrValAdjUSD` (transfer
*value*), and the community tier exposes only `TxCnt`/`TxTfrCnt` (transfer
*counts*). No free substitute has been found.

**Cost:** NVT is dropped from §6.5 and §6.4 unless you want to pay for it. The
page will say so rather than showing a count-based lookalike, which would be a
different metric wearing the same name.

---

## 2026-09-24 — D006: three book names have no Binance pair, so parity cannot hold

§7.1 defines MiniLizard on Binance spot bars, because that is what your
TradingView charts use. Checking `exchangeInfo` for TRADING status:
**FLUID, GEOD and AZTEC have no Binance pair at all.** XMR, on the watchlist,
has none either.

Their regime scores will be computed on a documented substitute (Coinbase for
FLUID and GEOD, Hyperliquid for AZTEC and XMR) and flagged `parity: substitute`
in the registry. Those scores are *not* comparable to a TradingView reading and
the asset page will say so in words.

**Cost:** three of twenty-one names cannot be parity-tested. There is no fix:
the bars do not exist.

---

## 2026-09-24 — D007: unlocks come from hand-maintained YAML; DefiLlama's are paid

Verified: `/emissions` and `/emission/{slug}` both return **HTTP 402, "Upgrade
to the paid API plan"**. `/treasuries` is 402 as well.

So §6.3's unlock panel and §6.4's `unlock load` and EV-analogue columns are fed
by `config/unlocks.yaml` and `config/treasuries.yaml`, maintained by you from
Tokenomist and project docs, and snapshotted daily by the pipeline so that "the
schedule as it was believed on date X" becomes a real history.

**Cost:** manual upkeep, and the unlock data is only as current as the last time
you edited it. The panel will show the age of its source file, so a stale
schedule looks stale instead of looking authoritative.

---

## 2026-09-24 — D009: target weights are kept out of the public repository

`github.com/MinimumLizard/Crypto` is **public** (verified via the API, not
assumed). The brief's §12 asks that a public copy of the spec have "§1.1 and the
weights in §4.2" removed — so you already regard the book's weights as something
that should not be published.

A registry carrying `target_pct` for all 21 names would have put exactly that in
a public repository, where git history makes it permanent. Committing it and
asking afterwards is not a reversible order of operations, so:

- `config/assets.yaml` — public. Identity, contracts, venues, slugs, sectors,
  tiers, flags. None of it is sensitive and the pipeline needs all of it.
- `config/weights.yaml` — **gitignored**. The 21 target weights, tier targets
  and the sector cap.
- `config/weights.example.yaml` — public. Same schema, all zeros, so a clone
  still documents the shape.

The sector roll-up that previously sat in a comment at the foot of the registry
was removed too: a roll-up naming each sector's total is the same disclosure as
the weights.

Weight-dependent panels (drift, tier totals, sector cap, next buy) read this
file when it is present and render "not configured" when it is not — never zeros.

**Cost:** you must keep a local `config/weights.yaml`, and Actions needs it as a
secret for the weight-dependent panels to build. If you would rather the weights
were simply public, deleting one `.gitignore` line and moving the block back
undoes this entirely. §12 Q1 settles it.

---

## 2026-09-24 — D010: answers to the §12 questions (round 1)

| Q | Answer | Consequence |
|---|---|---|
| Q1 Hosting | **Public repo + GitHub Pages** | Actions minutes are unlimited, so the 2-hourly refresh costs nothing. Portfolio is browser-only: localStorage + WebCrypto, with JSON import/export. `config/portfolio.yaml` is never used. A P5 test greps the built site for any holding or dollar amount and fails the build if one appears. |
| D009 Weights | **Stay private** | `config/weights.yaml` remains gitignored; `weights.example.yaml` is committed. Actions needs the real file as a secret before any weight-dependent panel can build. |
| ETF flows | **Keep hunting** | The panel reads "source unavailable" until a free source is found. Hunting is a P2 task, timeboxed; if it comes back empty the panel stays as-is and this entry is updated rather than the panel being quietly filled with a scrape that breaks. |
| Q4 Currency | **USD default, AUD toggle** | Every source is USD-native, so the headline numbers carry no conversion error. AUD conversion uses FRED `DEXUSAL` with `open.er-api.com` as the verified fallback, and the rate's own as-of date is shown, because a stale FX rate silently mis-states every figure on the page. |

Because public mode was chosen, `docs/SPEC.md` now holds a redacted copy of the
brief: §1.1 removed in full, the §4.2 target-weight column removed, and the
tier-target percentages removed. Verified by diff, not by assumption.

Note: "Melbourne" and "Colombo" still appear twice in the public copy, in §3
(schedule timing) and §6.1 (the three clocks). Those are outside §1.1 and are
product requirements — the site shows those clocks on every page — so they were
left. Say if you want them generalised.

---

## 2026-09-24 — D011: the Cowen memos arrived; what they changed

Five memos are now in `private/reference/` (gitignored, never published). They
are confidential: they are read **only** to learn which metrics and framings
matter. No memo text, chart, figure or proprietary metric name is reproduced on
the site. In particular the site does **not** use the names "Cowen corridor",
"ITC Liquidity Risk", "Terminal price" or any ITC branding; our own
constructions get descriptive names, per §7.3.

They were PDFs with an ordinary text layer, not the ZIP-of-pages the brief
warned about, so extraction was straightforward.

What they changed, concretely:

1. **ROI means a price ratio, not a percentage return.** 0.520 from peak means
   price is at 52% of the peak. Every ROI-style figure on the cycle page follows
   that convention and says so, because the two readings differ by a sign and a
   rebase and would otherwise be silently wrong.
2. **The risk scorecard's shape is confirmed** — rows normalised 0–1, columns
   Current / 6M / 1Y / 4Y, grouped into headline, valuation-and-on-chain, and
   sentiment-and-social families. That is what `/btc-cycle` builds.
3. **The liquidity composite's components are named**: policy rates, balance
   sheet, monetary aggregates, the front end of the curve, the dollar, and real
   yields — with the memo's own emphasis that **level and direction are
   different things**. Our composite therefore shows the level *and* the 13-week
   change, never just one.
4. **The business-cycle composite splits into three families** — employment,
   income and output, production and investment — all FRED-sourceable.
5. **New cycle analogs worth building**, all pure price maths and therefore free:
   ROI-from-peak across cycles with mean and ±1σ bands; ROI-from-prior-bottom;
   days-since-peak and days-since-bottom counters against prior cycles;
   midterm-year monthly return table; presidential-term paths rebased to 100.
6. **Dominance must exclude stablecoins** to be readable, because stablecoin cap
   is now large enough to dilute the trend. We show both and default to ex-stables.
7. **A cumulative advance-decline line trends down by construction** in a long
   decline, so its level is partly a function of duration. The panel says so.

Metrics the memos use that we **cannot** source free, and will not fake:
`RHODL ratio`, `supply in profit/loss`, `HODL waves`, `terminal price` and
`balanced price` all need UTXO age bands or transferred value, which the
CoinMetrics community tier does not serve. Those rows appear in the scorecard as
**"source unavailable: needs paid on-chain data"** rather than being dropped
silently or approximated with something that merely resembles them.

---

## 2026-09-24 — D012: remaining §12 questions, decided autonomously

The owner asked me to use full autonomy rather than answer the rest. Decided:

| Question | Decision | Reversible by |
|---|---|---|
| Q2 Keys | Nothing is built that *requires* a key. Every keyed source degrades to "needs a key: <NAME>" on `/source-health` with the exact registration URL. FRED, CoinGecko Demo, CMC and Telegram are wired and read from env; absent, their panels say so. | Adding the secret |
| Q3 Parity values | None exist yet. The engine is implemented exactly to §7.1 and ships with a golden-test harness holding **zero** golden values, so the parity test is `skip`, never `pass`. The site says "parity unverified — no TradingView reference supplied". | Dropping values into `tests/golden/minilizard_parity.yaml` |
| Q4 Order size | `$2,000` default in `config/portfolio_prefs.yaml`, used only for slippage and gas-threshold display. | Editing one line |
| Q5 Markets/themes | The brief's default GDELT themes; Polymarket and Kalshi slugs are **discovered at fetch time** by searching their APIs for the brief's four topics, rather than hard-coded slugs that rot. | `config/markets.yaml` |
| Q6 Alerts | Quiet hours 22:00–07:00 in the owner's local zone, digest 07:30. | `config/alerts.yaml` |
| Q7 Geoblocks | **Answered by evidence: no self-hosted runner or Worker is needed.** `data-api.binance.vision` and HL `predictedFundings` cover everything US egress blocks. | n/a |
| Buy-order gap | **Not invented.** HYPE, LINK and SYRUP are absent from §4.2's buy order, so the deployment tracker renders them as `unplaced — no position in the buy order` and the next-buy calculation skips them. Inventing an order would be inventing a trading rule, which §14 forbids. | Adding them to `buy_order` |

---

## 2026-09-24 — D013: P1 shipped; what is real and what is not

The pipeline, both engines and the site are built and the daily workflow runs
end to end. What matters is being precise about which claims are backed by
measurement and which are not.

**Measured and verified**

- **The quantile replication gate PASSES.** Fitting CoinMetrics BTC `PriceUSD`
  from 2010 through 2026-05-29 gives μ 7.9928 (published 7.9914), b_HI −0.3245
  (−0.326), b_LO −0.0236 (−0.024), n 5795 (5788). That is an independent
  reproduction from a price series the paper did not necessarily use, so §7.2's
  gate is satisfied and the bands ship. It runs in CI on every daily build.
- **Prices cross-check across venues.** All 26 assets agree to within 0.21% on
  a shared date, and UNI's 90-day return was separately confirmed against
  CoinGecko (+212.7% theirs, +220.5% ours over a slightly different window).
- **Cycle detection lands on the right dates** — lows at 2015-01-14, 2018-12-15
  and 2022-11-21, peak 2025-10-06 at ~$124.7k — derived from price alone with
  no dates hard-coded.
- **The pipeline runs cold.** From an empty store: 26/26 assets fetched, the
  2010 backfill spliced, artefacts built, gate passed.

**Explicitly NOT verified**

- **MiniLizard parity.** The engine implements §7.1's prose exactly, including
  Pine's RMA seeding, mean-absolute-deviation CCI and late pivot confirmation.
  But no TradingView reference values exist, so the golden file is empty, its
  test SKIPS rather than passes, and every surface that shows a score says
  parity is unverified. A green suite must never be readable as evidence of a
  parity that was never measured.
- **No backtest has been run**, so no win rate, profit factor or drawdown
  figure appears anywhere. §7.1's framing — a drawdown filter, not an alpha
  source — is carried on the asset pages as the brief's own characterisation,
  attributed as such.

**Coverage gaps, stated rather than filled**

Seven of 26 names cannot be scored on Binance, the venue parity is defined on:
HYPE, FLUID, GEOD, AZTEC and XMR have no pair at all; AERO has 69 bars and CFG
192 against the 300 the engine needs. Five fall back to a substitute venue and
say so on their page; GEOD and AZTEC have no venue with enough history and show
no score. Binance listed HYPE on 2026-09-24 — the day of this build.

**What screenshot review caught that code review did not**

Three defects were invisible in the source and obvious in the render: the asset
page claiming a score "uses binance" for a name with zero Binance bars, the
parity note rendered twice under different headings, and "1th percentile". The
brief's insistence on inspecting the images is doing real work.

---

## 2026-09-26 — D014: circulating supply history is derived, not waited for

§6.4's headline metric is **net holder yield = holder yield − net dilution**,
and dilution needs circulating supply as it was in the past. No free API serves
that series, which is why §3 tells the pipeline to snapshot supply daily — an
answer that only becomes useful a year from now.

The series already exists implicitly. CoinGecko's `/coins/{id}/market_chart`
returns `prices` and `market_caps` on matched timestamps, so

    circulating supply = market cap / price

A year of dilution history is therefore available today. Verified on UNI:
+3.24% over 365 days, which is consistent with its ongoing unlocks.

**Cost, and it is not small.** CoinGecko's historical market cap is itself a
derived series that gets restated, so implied supply inherits that; it is not
an on-chain read, and the page says so. The public API also caps history at
365 days — 400 returns 401 — so the 365-day window is measured across roughly
358 days of medians and understates by about 2%.

Daily snapshotting continues regardless. In a year our own series will reach
further back than CoinGecko will serve, and it cannot be restated under us.

---

## 2026-09-26 — D015: cleaning the implied supply series, and what NOT to clean

Raw implied supply has two kinds of large one-day move, and they are opposites.

**Artefacts, which are removed.** For a day or two CoinGecko reports the FULLY
DILUTED cap, so implied supply jumps to max supply and returns. VIRTUAL hits
exactly 1,000,000,000 on 2026-02-10 and comes back the next day. Measured
point-to-point across one of these, VIRTUAL's dilution read **−138% annualised**
— a rate supply cannot produce.

Two filters, in order of how defensible they are: circulating cannot exceed
total supply; and anything more than 5% from a centred rolling median is an
artefact, since the fastest diluter in the book adds 0.13% a day. The 5% is
needed because the spikes land *exactly on* max supply rather than above it —
AAVE's 16,000,000 is only 5.3% above its neighbours, survived a 10% test, and
put +14.7% annualised dilution on a token whose supply grew 1.2% that year.

**Supply events, which are the signal.** A move that persists is a real unlock:
MORPHO +50% on 2025-10-10, ONDO +54% on 2026-01-18, and HYPE's supply falling
270m → 222m over a year as the Assistance Fund buys back. An earlier version of
this code marked these "low confidence" and flagged 19 of 26 names, burying the
most important fact about a token as though it were noise. They are now
reported as observed supply events — and they are the **only unlock data
available at all**, since DefiLlama's emissions endpoint is 402 (D007).

Also: a round trip has two legs, and judging a move against the value it
started from only catches the outbound one. On the way back the "before" value
IS the spike, so one artefact was reported as two events. Reversion is now
judged on the levels either side of the excursion.

---

## 2026-09-26 — D016: grade D, and why an annualised rate needs a headroom

Two presentation decisions that changed what the numbers mean.

**Grade D — "measured, capture is zero".** The brief's scale runs A to F, but
AAVE, CFG, FLUID, MORPHO, ONDO and VIRTUAL have years of fee and revenue
history and holders' revenue of exactly zero. Grading that F would call a name
with 2,123 days of data unmeasurable. They keep a net holder yield, and it is
negative, because dilution is real. Zero capture is judged on the CURRENT
window, not on all history — Aave distributed to holders at some point, so an
all-history sum is non-zero and graded it A while today's capture is flatly zero.

**Dilution headroom.** AAVE genuinely issued 3.6% of supply in 90 days, which
annualises to +14.7%. Only 570k tokens remain below its 16m cap, so that rate
has about a quarter of a year left in it. The rate alone reads as a trend; with
the headroom beside it, it reads as the terminal event it is.

Verified independently: HYPE 2.82%, PUMP 12.27% and UNI 1.92% holder yields
recomputed by hand from the stored series match the page exactly. HYPE reads
3.46% on a 30-day basis against 2.82% on 90-day, which is why §6.4 insists the
annualisation basis is labelled on every figure.

---

## 2026-09-26 — D017: funding is annualised per venue, and that changes signs

§6.9 asks for funding across Hyperliquid, Binance and Bybit. The trap is that
they do not fund on the same schedule: Hyperliquid funds hourly, Binance and
Bybit every four or eight hours. A rate of 0.0001 is **87.6%/yr** hourly and
**10.95%/yr** on an eight-hour venue.

Measured on 2026-09-26, BTC funding was +1.25e-05 hourly on Hyperliquid and
−7.75e-06 on Binance. Annualised correctly those are **+10.95% and −0.85%** —
opposite signs. A single multiplier would not merely have been imprecise, it
would have reported the two venues as agreeing when they disagreed.

Every rate carries its venue's own `fundingIntervalHours` from
`predictedFundings`, which is also the call that answers the geoblock: Binance
and Bybit refuse US addresses, and every GitHub runner is one.

The dispersion this exposes is itself the signal. SYRUP reads +82.0% annualised
on Hyperliquid against +2.9% on Binance — a 79-point spread, which is what a
cross-venue basis trade is priced off. The table shows the spread as a column.

---

## 2026-09-27 — D018: two bugs the P4 tests caught before they shipped

**Booleans sum as unsigned.** polars returns `u32` from `(col > 0).sum()`, so
`advances - declines` underflowed the moment more names fell than rose: 0 − 3
became 4,294,967,294. That is most down days, which is precisely when an
advance-decline line is worth reading. Caught by a test asserting the
cumulative series over two constructed days; both counts are now cast to
`Int64` before subtracting.

**Zero-filling NaN corrupted the RRG.** RS-Momentum is the z-score of
RS-Ratio's four-week change, and the first four positions have no change to
measure. Filling them with `0.0` put a value far outside the range of real
week-on-week moves into the rolling window, dragging the mean and inflating the
standard deviation for the first fourteen weeks. On the chart the tails shot
across the whole plot and the rotation was invisible underneath the artefact.
`_zscore_tail` is now NaN-aware and refuses a window that is not full.

A flat series now returns NaN rather than 0 as well: a constant has no
dispersion, so its z-score is undefined, and returning 0 would place it exactly
on the RRG crosshair as though it had been measured there.

---

## 2026-09-27 — D019: what P4 can and cannot show today

Three of the four P4 deliverables are complete now. The fourth is complete in
mechanism and empty in data, and the difference is worth stating rather than
hiding behind a chart.

**Real today:** funding across three venues with z-scores against each coin's
own hourly history; open interest, OI/market cap and OI/volume; realised vol
and ATR percentile per name; Deribit DVOL against realised; the futures basis
curve; the options term structure and put/call ratio; stablecoin supply with
3,224 days of history and a per-chain split; breadth and correlations over the
tracked universe; sector indices equal- and cap-weighted; the RRG.

**Accumulating from the first build**, because no free source serves the
history and §3 says to start storing it:

- **Open interest change and the OI-vs-price quadrant.** `metaAndAssetCtxs`
  gives the current level only. The quadrant reads "needs history" rather than
  being computed from a single observation.
- **Total market cap and dominance over time.** CoinGecko's `/global` has no
  free history. A level is shown; a trend is not, because there is not one.
- **The advance-decline line.** §6.7 requires it be built forward. A basket
  chosen from today's top 100 is a basket that survived, so a line drawn back
  through it rises in periods the real market fell. It is not back-filled at
  all and the panel says how many days it holds.

**Deliberately not built:** 25-delta skew. Deribit's summary carries mark IV
but no greeks, and inferring delta without the forward and rate would be a
worse number wearing a precise-sounding name. §6.9 marks it optional.

**Breadth is measured over the tracked universe, not the top 100.** "% of the
top 100 above their 200-day average" needs 200 days of prices for 100 coins,
which the free tier does not serve. The panel names its universe rather than
implying a wider one.

**Forward unlocks remain hand-maintained** in `config/unlocks.yaml`, empty on
purpose (D007). Backward-looking unlocks do not need entering: the valuation
pipeline already detects them from circulating supply (D015).
