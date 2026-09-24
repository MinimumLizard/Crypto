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
