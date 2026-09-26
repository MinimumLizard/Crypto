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

**P1, P3 (valuation) and P4 (derivatives, breadth, sectors) done and LIVE at
https://minimumlizard.github.io/Crypto/.** P2 (macro) is next and needs a FRED key.

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

Not started: portfolio and alerts (P5), geopolitics and radar (P6), validation
pages (P7). Those routes exist and say what they will contain and what is
blocking them.

## Blockers

1. **No TradingView parity values.** The engine follows §7.1's prose exactly,
   but the Pine source it names as ground truth is still absent, and so are
   reference readings. `tests/golden/minilizard_parity.yaml` is empty on
   purpose and its test SKIPS — a green suite must never imply a parity that
   was never measured. The site says "parity unverified" everywhere it matters.
   Drop cases into that file to activate it. Most useful: BTC and ETH (V8) plus
   SOL, AAVE, UNI (V10), ~5 dates each spanning a regime flip, a trend and a
   chop stretch.
2. **No FRED key**, so the whole macro page (§6.10) has no data and is not
   built. Everything else degrades gracefully; macro simply has no source.
3. **No local (non-US) probe run.** See `docs/SOURCES.md`, "The missing datapoint".
4. **Pages source is still "Deploy from a branch" and MUST be changed to
   "GitHub Actions"** at Settings → Pages. Until it is, the site keeps
   reverting to 404 and there is no way to fix it from here: the Pages API
   returns 403 through this environment's proxy.

   The mechanism, because it is genuinely confusing. Two publishers are
   fighting over the same site:

   - `daily.yml` runs `actions/deploy-pages`, which publishes the real built
     site. This works and has been verified serving live data.
   - The legacy `pages build and deployment` workflow (it shows up with
     `event: dynamic` and is not a file in this repo) fires on **every push to
     the default branch** and republishes that branch's ROOT. The root has no
     `index.html` — the site lives in `site/` and its build output is
     gitignored — so it publishes nothing and every path 404s.

   Whichever ran last wins. On 2026-09-25 the Actions deploy put the site up at
   03:27, and a routine commit at 03:33 triggered the legacy builder, which
   took it straight back down.

   **So: after any push, the site is 404 until `daily.yml` runs again.** Do not
   report the site as live off the back of an earlier check — re-verify with
   `curl -s -o /dev/null -w '%{http_code}' https://minimumlizard.github.io/Crypto/`
   AFTER the last push of the session. Changing the source to GitHub Actions
   retires the legacy builder and ends this permanently.
5. **The scheduled daily run has not fired yet.** `daily.yml`'s 00:20 UTC cron
   did not trigger on its first night; the build that deployed was a manual
   `workflow_dispatch`. GitHub delays the first schedule on a new repository and
   drops crons under load, so this may simply settle. If the data branch has no
   `Data update` commit for a given day, the schedule is the thing to check
   first, not the pipeline.

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
- **GDELT 429s** above one request per five seconds.
- **rekt.news RSS is 500.** Use DefiLlama `/hacks`.
- **Three book names have no Binance pair** (FLUID, GEOD, AZTEC) so MiniLizard
  parity cannot hold for them. XMR too, on the watchlist.
- **Actions cron slips at `:00`** — all schedules use off-the-hour minutes.
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
  the source and obvious in the render. Always look at the images.

## Commands

```
uv sync --all-extras                       # once
uv run terminal fetch --only all           # prices | onchain | sentiment | hourly
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
