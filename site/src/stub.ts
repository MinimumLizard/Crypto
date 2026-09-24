/**
 * Pages that are not built yet.
 *
 * SPEC §0.2 is explicit: a panel without data says "not built yet" or names the
 * reason it has none. It never shows zeros or placeholder numbers. So these
 * routes exist, are linked, and say exactly what they will contain and what is
 * blocking them -- which is more useful than a 404 and more honest than a
 * skeleton full of dashes pretending to be a dashboard.
 */

import './styles.css';
import { mountFrame, ROUTES } from './lib/frame';
import { escapeHtml } from './lib/format';

const PLANNED: Record<string, { phase: string; what: string; blocked?: string }> = {
  valuation: {
    phase: 'P3',
    what: 'The equity-lens screen: P/fees, P/revenue, P/holders-revenue, net holder '
      + 'yield, dilution, reverse DCF and the sector KPI panels, each against its own '
      + 'history and its sector median.',
    blocked: 'Five names (AAVE, FLUID, MORPHO, ONDO, CFG) have full revenue histories '
      + 'and holders’ revenue of exactly zero, and four (TAO, AZTEC, DUSK, POLYX) '
      + 'have no revenue data at all. The grading scheme has to distinguish those two '
      + 'before any multiple is published.',
  },
  breadth: { phase: 'P4', what: 'Total market cap, dominance including and excluding '
    + 'stablecoins, an advance-decline line built forward, and rolling correlations to BTC.' },
  sectors: { phase: 'P4', what: 'Equal- and cap-weighted sector indices and a relative '
    + 'rotation graph against BTC on weekly data.' },
  derivatives: { phase: 'P4', what: 'Funding across Hyperliquid, Binance and Bybit '
    + '(annualised on each venue’s own interval), open interest, the OI-vs-price '
    + 'quadrant, Deribit DVOL and the futures basis.' },
  macro: { phase: 'P2', what: 'Fed net liquidity, rates and real yields, the dollar, '
    + 'labour and inflation, and the liquidity and business-cycle composites.',
    blocked: 'Needs a free FRED API key. Without it the whole page has no data, so it '
      + 'is not shipped half-built.' },
  geopolitics: { phase: 'P6', what: 'GDELT theme volume and tone, the geopolitical risk '
    + 'index, the oil → inflation → Fed → liquidity chain with live values, '
    + 'and event-market odds.' },
  portfolio: { phase: 'P5', what: 'The book monitor, deployment tranches, the open-items '
    + 'tracker and an AU CGT discount tracker — all computed in your browser, with '
    + 'holdings never leaving the device.' },
  radar: { phase: 'P6', what: 'Where volatility is likely: unlocks, governance votes, '
    + 'funding extremes, OI and volume spikes, and names sitting on a key level.' },
};

async function main(): Promise<void> {
  const path = window.location.pathname.split('/').pop()?.replace('.html', '') ?? '';
  await mountFrame(path);
  const info = PLANNED[path];
  const label = ROUTES.find((r) => r.path === path)?.label ?? path;
  const root = document.getElementById('main') as HTMLElement;

  root.innerHTML = `<section class="panel">
    <h2>${escapeHtml(label)}<span class="asof"><span class="badge"><i></i>not built yet</span></span></h2>
    <div class="body">
      <p class="howto"><strong>This page is not built yet.</strong> It is listed so the
      shape of the terminal is visible, and so that nothing here can be mistaken for
      data. There are no placeholder numbers on this page.</p>
      ${info ? `<p>Planned for <strong>${escapeHtml(info.phase)}</strong>: ${escapeHtml(info.what)}</p>` : ''}
      ${info?.blocked ? `<p class="howto caveat"><strong>Blocked:</strong> ${escapeHtml(info.blocked)}</p>` : ''}
    </div></section>`;
}

void main();
