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

**P0 — scaffold and probe. Blocked on the owner.**

Done: probe built and run from two US locations; `docs/SOURCES.md`;
`docs/PLAN.md`; draft `config/assets.yaml`; decisions D001–D008.

Answered (D010): public repo + GitHub Pages; weights stay private; USD default
with an AUD toggle; ETF source hunt continues into P2.

Waiting on: registry confirmation, the remaining §12 questions (keys, parity
values, markets/themes, quiet hours, the buy-order gap), and the two blockers
below.

## Blockers

1. **`private/reference/` does not exist.** No Pine sources, no notes, no Cowen
   memos, no quantile paper. This blocks the MiniLizard port (SPEC §7.1), the
   parity tests, the backtest tables (§6.14) and the V8/V10 tier lists. Do not
   attempt the port from the brief's prose alone — the brief itself says the
   source wins where they disagree, and the details that decide parity (`ta.ema`
   seeding, pivot confirmation lag, `na` handling) are not in the prose.
2. **No local (non-US) probe run.** See `docs/SOURCES.md`, "The missing datapoint".

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

## Commands

```
uv run tools/probe.py --location <label>    # source probe; the only thing that runs today
```

Planned once the pipeline exists (PLAN.md §3):

```
uv run terminal fetch --only macro
uv run terminal build
uv run terminal site
uv run terminal validate
uv run terminal alerts --dry-run
```

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
