# CLAUDE.md — working notes for this repository

Read this first. It is the handover between sessions.

## What this is

A personal crypto market terminal: a static site rebuilt by GitHub Actions,
covering BTC, ETH and a 21-name altcoin book, the macro/liquidity/on-chain/
derivatives/supply/governance/geopolitical conditions around them, and a
deployment plan measured against all of it.

The full brief is `private/SPEC.md` (**gitignored, never commit it**). The build
plan is `docs/PLAN.md`. Decisions are appended to `docs/DECISIONS.md`. Source
evidence is `docs/SOURCES.md`.

## Current phase

**P1, P2 (macro), P3 (valuation), P4 (derivatives, breadth, sectors) and P6
(geopolitics, radar) done and LIVE at https://minimumlizard.github.io/Crypto/.**
P5 (portfolio) and P7 (validation) are what remain.

**A code and model audit ran 2026-10-04 (D031-D035).** Eleven real defects
fixed in the first pass and six more in a second (D036), including two that
changed a published signal: the 50-week rule was confirmed by a Wednesday
close (D033) and the cycle detector returned six cycles while claiming four
(D034). Four modules had no tests and held six of the first eleven defects;
the suite went 175 -> 290. Read D031 first — it lists what was verified SOUND
as well as what was wrong, so the next session does not re-audit the quantile
model or the store. D036 records the three recurring CLASSES, which is the
more useful artefact.

Not yet audited: the fetchers, `health.py`, `registry.py`, and the site's
TypeScript beyond the three files these fixes touched.

Shipped: the probe and `docs/SOURCES.md`; `docs/PLAN.md`; the registry; the
pipeline (store, fetchers, metrics, artefacts, CLI); Pine-exact indicators; the
MiniLizard engine; the quantile model **with its replication gate passing**;
the site (home, BTC cycle, assets, asset pages, source health) with CI and a
daily build that deploys to Pages. Decisions D001–D012.

P3 shipped the valuation page: the §6.4 screen, net holder yield ranked,
grades, observed supply events and the reverse-DCF grid, fed by CoinGecko
caps/supply and DefiLlama fees, revenue, holders' revenue and TVL.

P4 shipped three pages: derivatives (funding per venue, OI, vol, basis,
options), breadth (dominance, stablecoins, participation, correlations) and
sectors (equal/cap-weight indices, fee growth, RRG). See D019 for exactly which
parts are real today and which accumulate forward.

P6 shipped two pages: geopolitics (the oil chain, GPR with its threat/act
split, EPU, GDELT theme spikes, event-market odds, the calendar with an .ics
export and the narrative log) and radar (the liquidity-gated catalyst score,
the screener, governance, security incidents and tagged news). See D026 for
exactly which parts are real today and which are blocked or accumulating.

P2 shipped the macro page: Fed net liquidity with its 13-week change, the
policy path and FOMC countdown, rates/dollar/conditions with own-history
percentiles, inflation, labour with the claims-against-52-week-low read,
commodities and ratios, BTC correlations, and the two 0-1 composites (§7.3)
with every component visible. It reads the FRED series `fred.py` fetches; with
no key every panel names the series it wants and shows nothing.

Not started: portfolio and alerts (P5), validation pages (P7). Those routes
exist and say what they will contain and what is blocking them.

## Blockers

1. **No TradingView parity values.** The engine follows §7.1's prose exactly,
   but the Pine source it names as ground truth is still absent, and so are
   reference readings. `tests/golden/minilizard_parity.yaml` is empty on
   purpose and its test SKIPS — a green suite must never imply a parity that
   was never measured. The site says "parity unverified" everywhere it matters.
   Drop cases into that file to activate it. Most useful: BTC and ETH (V8) plus
   SOL, AAVE, UNI (V10), ~5 dates each spanning a regime flip, a trend and a
   chop stretch.
2. **FRED: the fetcher now exists; the macro PAGE does not.** `pipeline/
   fetchers/fred.py` pulls 32 series (`fetch --only macro`) and is wired into
   `fetch --only all`. `daily.yml` already passes `FRED_API_KEY` through.

   Until 2026-09-27 there was no FRED code at all — only `store.write_macro`
   waiting and the env var being passed to nothing. The oil chain on
   /geopolitics was written to read `store.read_macro` and so could never have
   filled, key or no key. Adding the secret alone changes nothing; the fetcher
   was the missing half.

   Whether the key is actually set is visible on `/source-health`: with it,
   32 `fred/*` rows read ok; without it they read `needs_key`. Nothing is ever
   substituted from another source — see D028 for why DXY is not DTWEXBGS.

   The macro page (§6.10) is now built and reads these series. With no key it
   renders every panel naming the series it wants; the oil chain on
   /geopolitics fills as soon as the five in `fred.OIL_CHAIN_SERIES` land.
3. **No local (non-US) probe run.** See `docs/SOURCES.md`, "The missing datapoint".
4. **RESOLVED 2026-09-27: Pages source is now "GitHub Actions".** The legacy
   `pages build and deployment` workflow is retired and no longer fires on a
   push, so the site no longer 404s between a push and the next `daily.yml`
   run. Settings -> Pages confirms the source and that the last deploy came
   from the Daily build workflow.

   Kept for the record, because the failure mode was genuinely confusing and
   cost several deploy cycles: with the source set to "Deploy from a branch",
   two publishers fought over the same site. `daily.yml` ran
   `actions/deploy-pages` and published the real build; the legacy builder
   fired on every push to the default branch and republished that branch's
   ROOT, which has no `index.html` because the site lives in `site/` and its
   output is gitignored. Whichever ran last won. On 2026-09-25 the Actions
   deploy put the site up at 03:27 and an ordinary commit at 03:33 took it
   straight back down.

   Re-verifying liveness after the session's last push is still the right
   habit, but it is no longer a race. Note the Pages API returns 403 through
   this environment's proxy, so the setting cannot be read or changed from
   here -- only observed through its effects.

   Outstanding and cosmetic: **Enforce HTTPS is off.** `http://` serves 200
   without redirecting. One checkbox on the same settings page.

5. **Taking the repo PRIVATE unpublishes the Pages site, and making it public
   again does not restore it.** GitHub Pages from a private repository needs a
   paid plan (Pro or above); on a free plan going private sets `has_pages` to
   false and takes the site down. Flipping back to public leaves it false, so
   the site stays 404 and `deploy-pages` fails against a site that no longer
   exists. Seen 2026-09-29.

   `daily.yml`'s deploy job now runs `actions/configure-pages@v5` with
   `enablement: true`, which turns Pages back on and sets the source to GitHub
   Actions, so the next daily build repairs this by itself.

   Worth knowing before flipping visibility again: on Pro the repo is private
   but **the Pages site is still publicly reachable** — private source, public
   site. Access-controlled Pages is Enterprise only. And public repos get
   unlimited Actions minutes where private ones on free get 2,000/month, which
   a ~20-minute daily build plus manual dispatches will visibly consume.

6. **The schedule fires, but ~4h40m late.** Resolved as a blocker: `daily.yml`
   ran on `event: schedule` on 2026-09-25 (run 5) and 2026-09-26 (run 6), so
   the cron is live and the build no longer depends on a manual dispatch. It
   does not run at 00:20 UTC though — both started at 05:02–05:05 UTC. GitHub
   queues cron-triggered runs on public repos behind paid load and does not
   guarantee the minute. Treat 00:20 as "some time after 00:20", and if the
   `data` branch has no `Data update` commit by ~06:00 UTC, then check the
   schedule.

## Hard rules

- **This repository is PUBLIC.** Assume anything committed is permanent and
  world-readable. Target weights live in the gitignored `config/weights.yaml`.
- **Never commit** anything under `private/`, any holding, any dollar amount, any
  secret. `.gitignore` covers `private/`, `.env*`, `config/portfolio.yaml`,
  `content/holdings*.json`. Verify with `git check-ignore -v <path>`.
- **Never fabricate or interpolate.** A panel with no data says "not built yet"
  or "source unavailable: <reason>". It never shows a zero or a placeholder.
- **No look-ahead.** Percentiles, z-scores and fits use expanding or trailing
  windows that exclude the observation being scored. See PLAN.md §5.3 — the
  subtle part is source *revisions*, not the window maths.
- **No market number is hard-coded** in logic or UI. Narrative lives in
  `content/context.md`, which is never read by any computation.
- **One failing source never breaks the build.** It writes a `source_health` row,
  the last good value carries forward with a stale flag, and it shows up on
  `/source-health`.
- **No paid services, no trading execution, no wallet connection, no key with
  trading rights.** Ask before adding anything paid.
- **Don't invent the owner's exit rules.** Build the mechanism; leave
  `config/exits.yaml` empty until it is filled in.

## Conventions inherited from Nelson-Siegel

The sibling project is `github.com/minimumlizard/Nelson-Siegel`. Adopted:
numbers cited from a command rather than pasted into prose; carry-forward that
is labelled with its age and becomes a *fault* past a threshold; naming what is
excluded and why instead of dropping it silently; liquidity-gate before ranking;
colour as a role that never carries meaning alone; findings that fail out of
sample get reported and then not used.

Not adopted: the rendering stack. NS generates one self-contained HTML file from
Python. This project needs ~40 routes, a live WebSocket layer and browser-side
portfolio state, so it uses Vite + TypeScript, multi-page, no framework.
Reasoning in PLAN.md §2.

## Gotchas hit so far

- **US egress is the operative environment.** GitHub runners are US-based;
  `api.binance.com` → 451 and `api.bybit.com` → 403. Use
  `data-api.binance.vision` for spot bars and Hyperliquid `predictedFundings`
  for Binance/Bybit funding.
- **Funding intervals differ by venue** (HL 1h, Binance/Bybit 4–8h). Annualise
  with each venue's `fundingIntervalHours`, never a constant.
- **DefiLlama nests protocols under a parent.** Query the parent slug; summing
  children double-counts. `PUMP → pump`, `SYRUP → maple-finance`, `SKY → sky`.
- **DefiLlama `/emissions`, `/emission/{slug}` and `/treasuries` are 402 (paid).**
- **CoinMetrics community is 31 metrics.** Realised cap, transfer value, NVT and
  `RevUSD` are paywalled. Most are derivable — see D005. NVT is not.
- **CoinGecko unauthenticated 429s within seconds.** The Demo key is required.
- **Wikimedia and SEC 403 a generic User-Agent.** It must carry contact details.
- **Stooq is unreachable from US egress** (both locations, https and http).
- **GDELT signals a BAD QUERY with a 200 and a plain-text body** (D027), which
  a JSON decoder turns into "Expecting value: line 1 column 1" and which reads
  like a transport failure. Two rules it enforces: a quoted phrase must be at
  least 5 characters (`"Iran"`, `"OPEC"`, `"SEC"` are all rejected — unquoted
  they are fine), and OR'd terms must be inside parentheses. Five of the ten
  theme queries broke one of these and returned nothing. `tests/test_geopolitics.py`
  asserts both rules for every theme without a network call.
- **GDELT 429s** above one request per five seconds, and the limit is PER
  SOURCE IP — from a shared egress five seconds is not enough and a first call
  can need ~40s of backoff (D023). Spacing is 8s, the theme sweep runs to a
  wall-clock budget so a build never hangs on it, and themes are fetched
  stalest-first so the set fills in across builds instead of the same two
  always winning.
- **rekt.news RSS is 500.** Use DefiLlama `/hacks`.
- **Three book names have no Binance pair** (FLUID, GEOD, AZTEC) so MiniLizard
  parity cannot hold for them. XMR too, on the watchlist.
- **Actions cron slips at `:00`** — all schedules use off-the-hour minutes.
  It slips far more than that anyway: the 00:20 UTC daily fired at 05:02 and
  05:05 on its two scheduled nights. Never assume a cron ran on time.
- **Scheduled workflows on public repos stop after 60 days idle** — keepalive.
- **Bars are stored PER VENUE and never merged.** Binance for parity, longest
  series for charts. Splicing would put a seam into an ATR and a pivot detector.
  `store.read_ohlcv(sym)` gives the longest; pass a venue for a specific one.
- **7 of 26 names cannot be scored on Binance** (no pair: HYPE, FLUID, GEOD,
  AZTEC, XMR; too short: AERO 69 bars, CFG 192). Five fall back to a substitute
  venue and say so; GEOD and AZTEC have no venue with 300 bars and show no score.
- **Cycle peaks must be ALL-TIME highs.** Requiring only a retraced running
  maximum split the 2018 bear in two.
- **A cross-check must align venues on a shared date.** Comparing each venue's
  own last row made BTC's pre-2017 backfill look like a 1833% disagreement.
- **Staleness is cadence-aware.** A daily series ending yesterday is correct,
  not stale; flagging it trains the eye to ignore the badge.
- **CoinMetrics returns every value as a string**, and a metric that starts late
  is simply absent from earlier rows. Build the frame as Utf8 and cast after.
- **`ruff` treats a bare `package.json` gitignore line as matching every level** —
  it silently excluded `site/package.json` and broke `npm ci` in CI.
- **Circulating supply history = CoinGecko market cap / price** (D014). No
  free API serves the series; this derives it. Capped at 365 days (400 → 401).
- **Implied supply needs cleaning before it is measured** (D015). Transient
  FDV spikes land exactly ON max supply; a point-to-point read across one gave
  VIRTUAL −138% annualised dilution. But a move that PERSISTS is a real unlock
  and is the signal — do not filter those out, they are the only unlock data
  available since DefiLlama's emissions endpoint is paid.
- **Zero capture is judged on the CURRENT window**, not all history: Aave paid
  holders at some point in 2,123 days, so an all-history sum graded it A while
  today's capture is zero.
- **Funding must be annualised per venue** (D017). HL funds hourly, Binance and
  Bybit every 4-8h. The same raw rate is 87.6%/yr vs 10.95%/yr, and on
  2026-09-26 a constant multiplier would have flipped BTC's sign between
  venues. Use `predictedFundings`' own `fundingIntervalHours`.
- **polars sums booleans as u32.** `(col > 0).sum() - (col < 0).sum()`
  underflows to ~4.29e9 whenever the second exceeds the first. Cast to Int64
  first. This broke the advance-decline line on exactly the days it matters.
- **Never zero-fill NaN before a rolling z-score** (D018). It drags the mean
  and inflates the sd for the length of the window; on the RRG it made every
  tail shoot across the plot.
- **Screenshot review catches what code review does not.** Three real defects
  (a false venue claim, a duplicated note, "1th percentile") were invisible in
  the source and obvious in the render, and a second pass over P4 found six more
  (D020). Always look at the images, at BOTH viewports, zoomed in enough to read.
- **Derivatives OI and volume are HYPERLIQUID ONLY** (`perp_contexts`), because
  it is the one venue that serves OI to a US address. OI/market cap ranks
  leverage on that venue, not the market's (D021). Anything summed across venues
  would silently omit Binance and Bybit.
- **A return is not safe to show until its window is on the row.** The sector
  table ranked a 224-day return against a 400-day one in the same sortable
  column. Same lesson as the valuation basis (D016); it will recur.
- **`series[::2]` drops the last point when the length is even**, which put every
  400-day sector chart a day behind the return printed beside it. Thin with
  `sectors._thin`, which keeps the newest point.
- **The HTTP cache is binary-safe now, and was not** (D022). It stored
  `response.text`, so every non-text body came back with each invalid byte
  replaced by U+FFFD — the 3.2MB GPR workbook returned as 4.0MB of rubbish.
  It stores base64 now. Old `body` entries still read as text.
- **The GPR index is a .xls file**, no CSV and no API, so `fastexcel` is a
  dependency. The workbook also carries GPRD_THREAT and GPRD_ACT; the split is
  the interesting part.
- **Never trust a derived label read back from the store** (D024). The event
  classifier matched "brent" inside "Brentford" and filed a football market
  under Oil; fixing the matcher did not fix the page, because the append-only
  store still held the row with its old label. Topics are recomputed from the
  question on read. Anything this pipeline computed rather than fetched should
  be recomputed, not trusted.
- **Tagging news to an asset needs a whole word AND crypto context** (D025).
  "Uniform" is not UNI and "Sky is blue today" is not SKY. Ambiguous aliases
  live in `feeds.AMBIGUOUS_ALIASES`.
- **Three of five Snapshot slugs were wrong and returned empty**, which is
  indistinguishable from a quiet DAO. Aave is `aavedao.eth`, not `aave.eth`.
  AERO, SKY, MORPHO and ONDO have no Snapshot space at all — they govern
  on-chain, and the page says so.
- **Net liquidity has a unit trap** (§6.10). WALCL and WTREGEN publish in
  MILLIONS, RRPONTSYD in BILLIONS. Subtracting them raw is wrong by 1000x and
  still looks like a plausible few trillion. `macro.net_liquidity` converts
  first; a test pins it.
- **`daily.yml` has `concurrency: daily-build`**, so a second dispatch QUEUES
  behind the first rather than running beside it. A build stuck on a step
  blocks every later one; cancel the stuck run to release the lock. Seen
  2026-09-27 with `npm run build` hanging 20+ minutes on a step that takes two
  seconds locally.
- **A unit must never be appended to the em-dash that stands for a missing
  value.** "—pp" reads as a measurement. `macro.ts` has `withUnit` for this.
- **"Is this period over" is a question about the DATA, never about today.**
  `to_weekly` dropped the open week by comparing against `dt.date.today()`.
  With the store 11 days behind, a Monday-to-Wednesday stub passed the filter
  and its Wednesday close was published as a WEEKLY close — which confirmed the
  50-week rule when the real answer was 1 of 2 (D033). A clock cannot tell a
  stale store from a running week; the series can.
- **A row count is not a measurement count.** Hyperliquid serves real oracle
  candles from before an asset listed there with volume of exactly zero, so XMR
  has 1,251 bars and 252 scoreable ones. Gating on `height` published a regime
  on bars that had no score, and labelled them NEUTRAL (D032). Check
  `~isnan(score)`, not `len`.
- **An unmeasurable bar is not neutral, and the fix is to disclose, not to
  suppress.** Requiring 300 *scored* bars is stricter than §6.14's 300-bar
  warm-up and withheld sound readings from MORPHO, FLUID and four others. Keep
  the spec's number, add `short_history_note` (D032).
- **A docstring that states a result is not a test of it.** `find_cycles` said
  "four cycles rather than six" and returned six for months (D034). The four
  modules with no tests — `levels.py`, `cycle.py`, `risk.py`, `artefacts.py`'s
  regime selection — held six of the eleven defects the audit found.
- **Month arithmetic: step back ONE day, never 31.** `(month_start - 31d)
  .replace(day=1)` lands in the month before last whenever the intervening
  month is shorter than 31 days — March, May, July, October and December, five
  months in twelve, each drawing a two-month extreme labelled "previous month".
- **A percentile must exclude the observation it scores**, in every module, not
  just `risk.py`. `own_history_percentile` included today, so its maximum
  possible reading was (n-1)/n = 99.73% and an asset at its own one-year high
  could never print 100% (D035).
- **Thin a chart series with `series.keep_newest`, never `[::n]`.** A plain
  slice drops the newest point unless `len-1` is a multiple of the step, and
  the newest point is the one the panel prints beside the chart. Found four
  times in four modules (D036).
- **Once a defect appears twice, grep for the third.** The audit's second pass
  swept for three classes rather than reading for new bugs and found six more
  (D036). A percentile that includes the observation it scores is always wrong
  (it caps at (n-1)/n); a rolling z-score that does is a convention — pick one
  and apply it everywhere.
- **`observed_at` is String in `perp_contexts` and `global`, Datetime in
  `funding_hourly_*`.** Sorting, grouping and `str()` work on both, so the
  mismatch is invisible until the first subtraction. Coerce before arithmetic.
- **Never attach a provenance claim a function cannot check.** `_regime_for`
  pasted "Computed on Binance daily bars" onto payloads holding no score, and
  `midterm_monthly` asserted CoinMetrics prices for three years that are 100%
  Coinbase. Derive the claim from the frame, or take it from the layer that
  knows.

## Commands

```
uv sync --all-extras                       # once
uv run terminal fetch --only all           # prices | onchain | fundamentals |
                                           # derivatives | market | sentiment |
                                           # geopolitics | feeds | hourly
uv run terminal build                      # artefacts -> site/public/data
uv run terminal validate                   # the honesty report; cite it, don't paste it
uv run tools/probe.py --location <label>   # source probe

cd site && npm ci && npm run build         # the site
cd site && npx vite preview --port 4173    # then: node tools/shoot.mjs
```

The store defaults to `data/`; override with `TERMINAL_DATA`. Tests and the
pipeline both respect it, so a test never touches real data.

Screenshots need this container's Chromium:
`CHROMIUM_PATH=/opt/pw-browsers/chromium-1194/chrome-linux/chrome node tools/shoot.mjs`

## Repository layout

`config/` you edit · `content/` prose you edit · `pipeline/` fetch→store→metrics
→signals→artefacts · `site/` Vite MPA · `data/` history store (on the orphan
`data` branch, D008) · `tools/` one-off diagnostics · `docs/` PLAN, SOURCES,
DECISIONS, probe output · `private/` gitignored · `tests/`.

## Ending a phase

Tests → full pipeline cold → site build → Playwright screenshots of every page
at 1440px **and** 390px, inspected by eye → short report of what shipped, what
is approximated or broken, and what is next.

## Branch

Work on `claude/minilizard-crypto-terminal-e6g100`. Push with
`git push -u origin <branch>`. Do not open a pull request unless asked.
