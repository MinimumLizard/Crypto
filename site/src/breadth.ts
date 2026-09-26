/** Breadth and market structure (SPEC §6.7). */

import './styles.css';
import { mountFrame } from './lib/frame';
import { load, panelError } from './lib/data';
import { compact, escapeHtml, dirClass } from './lib/format';
import { sortableTable } from './lib/table';

const pct1 = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`;

function panel(title: string, asOf: string | null, body: string, howto?: string): string {
  return `<section class="panel"><h2>${escapeHtml(title)}
    ${asOf ? `<span class="asof">${escapeHtml(asOf)}</span>` : ''}</h2>
    <div class="body">${howto ? `<p class="howto">${escapeHtml(howto)}</p>` : ''}${body}</div></section>`;
}

function dominancePanel(d: any): string {
  if (!d?.available) return panelError(d?.reason ?? 'no dominance data');
  const g = (k: string, label: string, note: string) => {
    const v = d[k];
    return `<div class="gauge"><div class="k">${escapeHtml(label)}</div>
      <div class="v">${v === null || v === undefined ? '—' : `${v.toFixed(1)}%`}</div>
      ${v !== null && v !== undefined ? `<div class="bar"><span style="width:${Math.min(v, 100)}%"></span></div>` : ''}
      <div class="n">${escapeHtml(note)}</div></div>`;
  };
  return `<div class="gauges">
    ${g('btc_dominance_ex_stables', 'BTC dominance (ex-stables)', 'the clean read on rotation')}
    ${g('btc_dominance_incl_stables', 'BTC dominance (incl. stables)', 'diluted by stablecoin cap')}
    ${g('stablecoin_dominance', 'Stablecoin dominance', 'dry powder as a share of the market')}
    <div class="gauge"><div class="k">Total market cap</div>
      <div class="v" style="font-size:20px">$${compact(d.total_market_cap)}</div>
      <div class="n">ex-stables $${compact(d.total_ex_stables)}
        · ex-BTC $${compact(d.total_ex_btc)}
        · ex-BTC/ETH $${compact(d.total_ex_btc_eth)}</div></div>
  </div>
  <p class="howto">Dominance is shown both ways because they answer different
  questions. Stablecoin market cap is now large enough that the include-stables
  number moves without any capital rotating, so the ex-stables reading is the one
  that says whether money has left Bitcoin for higher-risk assets.</p>
  <p class="howto caveat">Total cap and dominance have no free history, so this
  series is built from our own snapshots and currently holds
  <strong>${d.snapshots_held}</strong> ${d.snapshots_held === 1 ? 'day' : 'days'}.
  A level is shown; a trend is not, because there is not one yet.</p>`;
}

function stablecoinPanel(s: any, chains: any[]): string {
  if (!s?.available) return panelError(s?.reason ?? 'no stablecoin history');
  const chainRows = chains.slice(0, 10).map((c) => `<tr>
    <td>${escapeHtml(c.chain)}</td>
    <td class="num">$${compact(c.circulating)}</td></tr>`).join('');

  return `<div class="gauges">
    <div class="gauge"><div class="k">Stablecoin supply</div>
      <div class="v" style="font-size:22px">$${compact(s.supply)}</div>
      <div class="n">${s.n_days.toLocaleString()} days of history</div></div>
    <div class="gauge"><div class="k">30-day change</div>
      <div class="v ${dirClass(s.change_30d_pct)}">${pct1(s.change_30d_pct)}</div>
      <div class="n">90d ${pct1(s.change_90d_pct)} · 365d ${pct1(s.change_365d_pct)}</div></div>
  </div>
  <div class="grid2" style="margin-top:12px">
    <div><h3 style="font-size:12px;color:var(--ink-2);margin:0 0 6px">By chain</h3>
      <div class="tablewrap"><table><thead><tr><th>Chain</th>
        <th class="num">Circulating</th></tr></thead><tbody>${chainRows}</tbody></table></div></div>
  </div>
  <p class="howto">Stablecoin supply is dry powder: it grows when capital enters
  the system and has not yet been deployed. It is one of the few market-wide
  series with a real multi-year history on the free tier.</p>`;
}

function breadthPanel(b: any, beat: any): string {
  if (!b?.available) return panelError(b?.reason ?? 'no breadth data');
  return `<div class="gauges">
    <div class="gauge"><div class="k">Above 50-day</div>
      <div class="v">${b.pct_above_50d.toFixed(0)}%</div>
      <div class="bar"><span style="width:${b.pct_above_50d}%"></span></div>
      <div class="n">of ${b.universe} tracked names</div></div>
    <div class="gauge"><div class="k">Above 200-day</div>
      <div class="v">${b.pct_above_200d.toFixed(0)}%</div>
      <div class="bar"><span style="width:${b.pct_above_200d}%"></span></div>
      <div class="n">of ${b.universe} tracked names</div></div>
    <div class="gauge"><div class="k">90-day highs / lows</div>
      <div class="v" style="font-size:20px">
        <span class="up">${b.new_90d_highs}</span> / <span class="down">${b.new_90d_lows}</span></div>
      <div class="n">names at a 90-day extreme</div></div>
    ${beat?.available ? `<div class="gauge"><div class="k">Beating BTC (90d)</div>
      <div class="v">${beat.pct_beating_btc.toFixed(0)}%</div>
      <div class="bar"><span style="width:${beat.pct_beating_btc}%"></span></div>
      <div class="n">${beat.winners} of ${beat.universe} · BTC ${pct1(beat.btc_return_pct)}</div></div>` : ''}
  </div>
  <p class="howto caveat">${escapeHtml(b.universe_note)}</p>`;
}

function advanceDeclinePanel(ad: any): string {
  if (!ad?.available) {
    return `<span class="missing">${escapeHtml(ad?.reason ?? 'not available')}</span>`;
  }
  const values = ad.series.map((p: any) => p.v);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const span = max - min || 1;
  const points = ad.series.map((p: any, i: number) => {
    const x = (i / Math.max(ad.series.length - 1, 1)) * 100;
    const y = 100 - ((p.v - min) / span) * 100;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');

  return `<svg viewBox="0 0 100 100" preserveAspectRatio="none" class="chart short"
      role="img" aria-label="Cumulative advance-decline line">
      <polyline points="${points}" fill="none" stroke="var(--accent)"
        stroke-width="0.8" vector-effect="non-scaling-stroke"/></svg>
    <p class="howto">Today: <span class="up">${ad.latest_advances} advancing</span>,
      <span class="down">${ad.latest_declines} declining</span> of ${ad.universe}.
      ${ad.days} day${ad.days === 1 ? '' : 's'} of history.</p>
    <p class="howto caveat">${escapeHtml(ad.caveat)}</p>`;
}

function correlationPanel(c: any): string {
  if (!c?.available) return panelError(c?.reason ?? 'no correlation data');
  return `<p class="howto">Median correlation to BTC across ${c.universe} names:
    <strong>${c.median.toFixed(2)}</strong> over ${c.days} days. The book's thesis
    assumes roughly 0.8, so the useful question is which names have broken from it.</p>
    <div class="tablewrap"><table id="corr"><thead><tr>
      <th data-sort="str">Asset</th><th data-sort="num" class="num">Correlation to BTC</th>
      <th style="width:40%">&nbsp;</th></tr></thead>
    <tbody>${c.rows.map((r: any) => `<tr>
      <td><a href="asset.html?s=${r.symbol}">${escapeHtml(r.symbol)}</a></td>
      <td class="num" data-v="${r.correlation}">${r.correlation.toFixed(2)}</td>
      <td><span style="display:block;height:6px;background:var(--grid);border-radius:2px">
        <span style="display:block;height:100%;width:${Math.max(r.correlation, 0) * 100}%;
          background:${r.correlation > 0.8 ? 'var(--accent)' : 'var(--up)'};border-radius:2px"></span>
      </span></td></tr>`).join('')}</tbody></table></div>`;
}

async function main(): Promise<void> {
  const index = await load<{ assets: string[] }>('index');
  await mountFrame('breadth', index?.assets ?? []);
  const root = document.getElementById('main') as HTMLElement;
  const b = await load<any>('breadth');
  if (!b) {
    root.innerHTML = panel('Breadth', null, panelError('no breadth artefact'));
    return;
  }
  const stamp = b.as_of.slice(0, 10);

  root.innerHTML = [
    panel('Dominance and market size', stamp, dominancePanel(b.dominance), b.how_to_read),
    panel('Stablecoins', stamp, stablecoinPanel(b.stablecoins, b.stablecoins_by_chain)),
    panel('Breadth', stamp, breadthPanel(b.breadth, b.beating_btc),
      'How many names are participating, rather than where the index is.'),
    panel('Advance-decline line', stamp, advanceDeclinePanel(b.advance_decline),
      'Cumulative advances minus declines across the top 100, built forward from '
      + 'our own snapshots — never back-filled, because a basket chosen from '
      + 'today’s top 100 is a basket that survived.'),
    panel('Correlation to BTC', stamp, correlationPanel(b.correlations)),
  ].join('');

  sortableTable(document.getElementById('corr') as HTMLTableElement);
}

void main();
