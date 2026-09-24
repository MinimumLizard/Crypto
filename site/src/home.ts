/** Home: "what matters today" in one screen (SPEC §6.2). */

import './styles.css';
import { mountFrame } from './lib/frame';
import { load, panelError } from './lib/data';
import { price, pct, dirClass, escapeHtml, staleness } from './lib/format';
import { sortableTable } from './lib/table';

interface GridRow {
  symbol: string; name: string; kind: string; tier: number | null; sector: string;
  price: number | null; r1d: number | null; r7d: number | null;
  r30d: number | null; r90d: number | null;
  regime: string | null; score: number | null; regime_reason: string | null;
  parity: string | null; dist_200d: number | null; grade: string | null;
}

interface Home {
  as_of: string;
  btc_levels: Record<string, number | string | null> & { as_of?: string };
  btc_fifty_week: any;
  btc_regime: any;
  grid: GridRow[];
  unplaced_in_buy_order: string[];
  weights_configured: boolean;
  how_to_read: string;
}

function panel(title: string, asOf: string | null, body: string, howto?: string): string {
  const stale = asOf ? staleness(asOf) : null;
  return `<section class="panel"><h2>${escapeHtml(title)}
    ${stale ? `<span class="asof"><span class="badge ${stale.cls}"><i></i>${escapeHtml(stale.text)}</span></span>` : ''}
    </h2><div class="body">
    ${howto ? `<p class="howto">${escapeHtml(howto)}</p>` : ''}${body}</div></section>`;
}

function fiftyWeek(data: any): string {
  if (!data?.available) {
    return panelError(data?.reason ?? 'no weekly history');
  }
  const done = data.consecutive_closes_above;
  const need = data.confirmations_needed;
  const cls = data.confirmed ? 'ok' : (done > 0 ? 'warn' : 'unknown');
  return `
    <div class="gauges">
      <div class="gauge">
        <div class="k">50-week confirmation</div>
        <div class="v">${done} <span style="font-size:14px;color:var(--muted)">of ${need}</span></div>
        <div class="bar"><span style="width:${(done / need) * 100}%"></span></div>
        <div class="n"><span class="badge ${cls}"><i></i>${data.confirmed ? 'confirmed' : 'not confirmed'}</span></div>
      </div>
      <div class="gauge">
        <div class="k">50-week SMA</div>
        <div class="v">${price(data.sma50w, '$')}</div>
        <div class="n">last weekly close ${price(data.last_weekly_close, '$')}
          · <span class="${dirClass(data.distance_pct)}">${pct(data.distance_pct)}</span></div>
      </div>
      <div class="gauge">
        <div class="k">Week ending</div>
        <div class="v" style="font-size:17px">${escapeHtml(data.week_ending)}</div>
        <div class="n">${data.past_confirmations?.length ?? 0} past confirmations on record</div>
      </div>
    </div>`;
}

function levelsPanel(levels: Record<string, any>): string {
  if (!levels || levels.available === false) return panelError('no BTC bars');
  const rows: [string, number | null, number | null][] = [
    ['20-week SMA', levels.sma20w, null],
    ['21-week EMA', levels.ema21w, null],
    ['50-week SMA', levels.sma50w, levels.sma50w_distance_pct],
    ['200-week SMA', levels.sma200w, levels.sma200w_distance_pct],
    ['200-day SMA', levels.sma200d, levels.sma200d_distance_pct],
  ];
  return `<div class="tablewrap"><table>
    <thead><tr><th>Level</th><th class="num">Value</th><th class="num">Distance</th></tr></thead>
    <tbody>${rows.map(([label, value, distance]) => `
      <tr><td>${label}</td><td class="num">${price(value, '$')}</td>
      <td class="num ${dirClass(distance)}">${distance === null || distance === undefined ? '—' : pct(distance)}</td></tr>`).join('')}
      <tr><td>Mayer multiple</td><td class="num">${levels.mayer_multiple ?? '—'}</td><td class="num">—</td></tr>
    </tbody></table></div>`;
}

function gridPanel(rows: GridRow[]): string {
  const head = `<tr>
    <th data-sort="str">Asset</th><th data-sort="num" class="num">Price</th>
    <th data-sort="num" class="num">1D</th><th data-sort="num" class="num">7D</th>
    <th data-sort="num" class="num">30D</th><th data-sort="num" class="num">90D</th>
    <th data-sort="str">Regime</th><th data-sort="num" class="num">Score</th>
    <th data-sort="num" class="num">vs 200D</th><th>Sector</th></tr>`;

  const body = rows.map((row) => {
    const regimeCell = row.regime
      ? `<span class="regime ${row.regime}">${row.regime}</span>${
          row.parity === 'substitute' ? ' <span class="badge warn" title="Scored on a substitute venue; not comparable to TradingView"><i></i>sub</span>' : ''}`
      : `<span class="na" title="${escapeHtml(row.regime_reason ?? '')}">no score</span>`;
    return `<tr>
      <td><a href="pages/asset.html?s=${row.symbol}">${escapeHtml(row.symbol)}</a>
        ${row.grade === 'F' ? '<span class="badge" title="No revenue data: unmeasurable">F</span>' : ''}</td>
      <td class="num" data-v="${row.price ?? ''}">${price(row.price, '$')}</td>
      <td class="num ${dirClass(row.r1d)}" data-v="${row.r1d ?? ''}">${pct(row.r1d)}</td>
      <td class="num ${dirClass(row.r7d)}" data-v="${row.r7d ?? ''}">${pct(row.r7d)}</td>
      <td class="num ${dirClass(row.r30d)}" data-v="${row.r30d ?? ''}">${pct(row.r30d)}</td>
      <td class="num ${dirClass(row.r90d)}" data-v="${row.r90d ?? ''}">${pct(row.r90d)}</td>
      <td data-v="${row.regime ?? ''}">${regimeCell}</td>
      <td class="num" data-v="${row.score ?? ''}">${row.score ?? '—'}</td>
      <td class="num ${dirClass(row.dist_200d)}" data-v="${row.dist_200d ?? ''}">${pct(row.dist_200d)}</td>
      <td>${escapeHtml(row.sector || '—')}</td></tr>`;
  }).join('');

  return `<div class="tablewrap"><table id="grid"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

async function main(): Promise<void> {
  const home = await load<Home>('home');
  const root = document.getElementById('main') as HTMLElement;

  if (!home) {
    await mountFrame('');
    root.innerHTML = panel('Home', null,
      panelError('the build produced no home.json — run `terminal build`'));
    return;
  }

  await mountFrame('', home.grid.map((g) => g.symbol));

  const noScore = home.grid.filter((g) => !g.regime);
  const notes: string[] = [];
  if (noScore.length) {
    notes.push(`<p class="howto caveat"><strong>${noScore.length} of ${home.grid.length} names
      carry no regime score.</strong> The engine is defined on Binance bars, and these
      either have no Binance pair or were listed too recently to warm up:
      ${noScore.map((g) => escapeHtml(g.symbol)).join(', ')}. Each asset page states its
      own reason. They are left blank rather than scored on another venue and
      presented as if comparable.</p>`);
  }
  if (home.unplaced_in_buy_order.length) {
    notes.push(`<p class="howto caveat"><strong>${home.unplaced_in_buy_order.join(', ')}
      have no position in the buy order.</strong> The book lists 21 names and the buy
      order names 18. No position has been invented for them.</p>`);
  }
  if (!home.weights_configured) {
    notes.push(`<p class="howto">Target weights are not configured here, so
      weight-dependent panels are not shown. They live in a private file
      (<code>config/weights.yaml</code>) that is deliberately not published.</p>`);
  }

  root.innerHTML = [
    panel('What matters today', home.as_of, notes.join(''), home.how_to_read),
    panel('Bitcoin: the 50-week confirmation', home.btc_levels?.as_of ?? null,
      fiftyWeek(home.btc_fifty_week),
      'Two consecutive weekly closes above the 50-week average is the confirmation '
      + 'rule. A daily close above it, or an intraweek touch, counts for nothing.'),
    `<div class="grid2">
      ${panel('Bitcoin key levels', home.btc_levels?.as_of ?? null, levelsPanel(home.btc_levels),
        'Distance is how far spot sits above or below each level.')}
      ${panel('Bitcoin regime', home.btc_regime?.latest?.date ?? null,
        home.btc_regime?.available
          ? `<div class="gauges"><div class="gauge">
               <div class="k">MiniLizard 1D</div>
               <div class="v"><span class="regime ${home.btc_regime.latest.regime}">${home.btc_regime.latest.regime}</span></div>
               <div class="n">score ${home.btc_regime.latest.score} · ${escapeHtml(home.btc_regime.latest.label)}</div>
             </div></div>
             <p class="howto caveat">Measured as a drawdown filter, not a source of alpha.
             Parity against TradingView is UNVERIFIED — no reference values have been supplied.</p>`
          : panelError(home.btc_regime?.reason ?? 'no score'),
        'The composite regime score on confirmed daily bars.')}
    </div>`,
    panel('The grid', home.as_of, gridPanel(home.grid),
      'Every tracked name: returns, regime and distance from the 200-day average. '
      + 'Click a column to sort. A blank regime is stated, not zero-filled.'),
  ].join('');

  sortableTable(document.getElementById('grid') as HTMLTableElement);
}

void main();
