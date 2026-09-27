/** Macro and liquidity (SPEC §6.10). */

import './styles.css';
import { mountFrame } from './lib/frame';
import { load, panelError } from './lib/data';
import { compact, escapeHtml, dirClass } from './lib/format';
import { sortableTable } from './lib/table';

const num = (n: number | null | undefined, dp = 2) =>
  n === null || n === undefined ? '—' : n.toFixed(dp);
const signed = (n: number | null | undefined, dp = 2) =>
  n === null || n === undefined ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(dp)}`;

// A unit belongs to a number. Appending one to the em-dash that stands in for
// a missing value produced "—pp", which reads as a measurement rather than as
// an absence.
// 99.6 shown as "100%" claims a record that has not happened; 0.3 shown as
// "0%" claims a floor. Extremes keep a decimal so they read as near-extremes.
const pctile = (n: number | null | undefined) => {
  if (n === null || n === undefined) return '—';
  return n > 99 || (n < 1 && n > 0) ? `${n.toFixed(1)}%` : `${n.toFixed(0)}%`;
};

// A count published in thousands reads badly at six figures: "159075.00".
const thousands = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : Math.round(n).toLocaleString('en-GB');

const withUnit = (n: number | null | undefined, unit: string, dp = 2) =>
  n === null || n === undefined
    ? '—' : `${signed(n, dp)}<span class="u">${unit}</span>`;

function panel(title: string, asOf: string | null, body: string, howto?: string): string {
  return `<section class="panel"><h2>${escapeHtml(title)}
    ${asOf ? `<span class="asof">${escapeHtml(asOf)}</span>` : ''}</h2>
    <div class="body">${howto ? `<p class="howto">${escapeHtml(howto)}</p>` : ''}${body}</div></section>`;
}

function gauge(label: string, value: string, note: string, cls = ''): string {
  return `<div class="gauge"><div class="k">${escapeHtml(label)}</div>
    <div class="v ${cls}">${value}</div>
    <div class="n">${note}</div></div>`;
}

function netLiquidity(nl: any): string {
  if (!nl?.available) return panelError(nl?.reason ?? 'no data');
  const parts = Object.entries(nl.parts_bn)
    .map(([k, v]) => `${escapeHtml(k)} $${compact((v as number) * 1e9)}`)
    .join(' · ');
  return `<div class="gauges">
    ${gauge('Net liquidity', `$${num(nl.latest_bn, 0)}<span class="u">bn</span>`,
    escapeHtml(nl.as_of))}
    ${gauge('13-week change', withUnit(nl.change_13w_bn, 'bn', 0),
    'the direction that matters', dirClass(nl.change_13w_bn))}
    ${gauge('52-week change', withUnit(nl.change_52w_bn, 'bn', 0),
    'against a year ago', dirClass(nl.change_52w_bn))}
  </div>
  <p class="howto">${parts}</p>
  <p class="howto caveat">${escapeHtml(nl.unit_note)}</p>`;
}

function ratesTable(rates: any): string {
  if (!rates?.available) {
    return panelError(rates?.rows?.[0]?.reason ?? 'no rate series stored');
  }
  const rows = rates.rows.map((r: any) => {
    if (!r.available) {
      return `<tr><td>${escapeHtml(r.label)}</td>
        <td colspan="6" class="na">${escapeHtml(r.reason)}</td></tr>`;
    }
    return `<tr>
      <td>${escapeHtml(r.label)} <span class="dim">${escapeHtml(r.series_id)}</span></td>
      <td class="num"><strong>${num(r.value, 2)}</strong>
        <span class="dim">${escapeHtml(r.unit)}</span></td>
      <td class="num ${dirClass(r.change_30d)}">${signed(r.change_30d)}</td>
      <td class="num ${dirClass(r.change_90d)}">${signed(r.change_90d)}</td>
      <td class="num ${dirClass(r.change_365d)}">${signed(r.change_365d)}</td>
      <td class="num">${pctile(r.percentile)}</td>
      <td class="num dim">${escapeHtml(r.as_of)}</td>
    </tr>`;
  }).join('');
  return `<div class="tablewrap"><table id="rates"><thead><tr>
    <th data-sort="str">Series</th><th data-sort="num" class="num">Level</th>
    <th data-sort="num" class="num">30d</th><th data-sort="num" class="num">90d</th>
    <th data-sort="num" class="num">1y</th>
    <th data-sort="num" class="num" title="Where this level sits in the series' own history since 1990">Pctile</th>
    <th class="num">As of</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="howto caveat">${escapeHtml(rates.how_to_read)}</p>`;
}

function commoditiesPanel(c: any): string {
  const rows = (c?.rows ?? []).map((r: any) => r.available
    ? `<tr><td>${escapeHtml(r.label)}</td>
       <td class="num">${num(r.value, 2)}</td>
       <td class="num ${dirClass(r.change_30d_pct)}">${signed(r.change_30d_pct, 1)}%</td>
       <td class="num ${dirClass(r.change_365d_pct)}">${signed(r.change_365d_pct, 1)}%</td>
       <td class="num">${pctile(r.percentile)}</td></tr>`
    : `<tr><td>${escapeHtml(r.label)}</td>
       <td colspan="4" class="na">${escapeHtml(r.reason)}</td></tr>`).join('');

  const ratios = (c?.ratios ?? []).map((r: any) => r.available
    ? `<li><strong>${escapeHtml(r.label)}</strong> ${num(r.value, 3)}
       <span class="${dirClass(r.change_90d_pct)}">${signed(r.change_90d_pct, 1)}% 90d</span>
       <span class="dim">· ${r.percentile === null ? '' : `${pctile(r.percentile)} pctile`}</span></li>`
    : `<li class="na">${escapeHtml(r.label)} — ${escapeHtml(r.reason)}</li>`).join('');

  if (!rows) return panelError('no commodity series stored');
  return `<div class="tablewrap"><table><thead><tr>
    <th>Series</th><th class="num">Level</th><th class="num">30d</th>
    <th class="num">1y</th><th class="num">Pctile</th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <h3 class="subhead">Ratios</h3><ul class="feed">${ratios}</ul>`;
}

function correlationsPanel(c: any): string {
  const rows = (c?.rows ?? []).map((r: any) => r.available
    ? `<tr><td>${escapeHtml(r.label)}</td>
       <td class="num">${num(r.correlation, 2)}</td>
       <td class="num dim">${r.days}d</td></tr>`
    : `<tr><td>${escapeHtml(r.label)}</td>
       <td colspan="2" class="na">${escapeHtml(r.reason)}</td></tr>`).join('');
  if (!rows) return panelError(c?.reason ?? 'no correlations');
  return `<div class="tablewrap"><table><thead><tr>
    <th>BTC against</th><th class="num">Correlation</th><th class="num">Window</th>
    </tr></thead><tbody>${rows}</tbody></table></div>
    <p class="howto caveat">${escapeHtml(c.how_to_read ?? '')}</p>`;
}

function labourPanel(l: any): string {
  const rows = (l?.rows ?? []).map((r: any) => r.available
    ? `<tr><td>${escapeHtml(r.label)}</td>
       <td class="num">${r.unit === 'thousands' ? thousands(r.value) : num(r.value, 2)}
         <span class="dim">${escapeHtml(r.unit ?? '')}</span></td>
       <td class="num ${dirClass(r.change_365d)}">${signed(r.change_365d)}</td>
       <td class="num dim">${escapeHtml(r.as_of)}</td></tr>`
    : `<tr><td>${escapeHtml(r.label)}</td>
       <td colspan="3" class="na">${escapeHtml(r.reason)}</td></tr>`).join('');

  const claims = l?.claims?.available
    ? `<div class="gauges">
        ${gauge('Initial claims, 4-week average', compact(l.claims.average_4w),
    escapeHtml(l.claims.as_of))}
        ${gauge('Against its 52-week low', `${signed(l.claims.above_low_pct, 1)}%`,
      `low ${compact(l.claims.low_52w)} &middot; up means weakening`)}
        ${l.claims.continuing?.available
    ? gauge('Continuing claims', compact(l.claims.continuing.value),
      escapeHtml(l.claims.continuing.as_of)) : ''}
      </div>`
    : `<p class="na">Claims: ${escapeHtml(l?.claims?.reason ?? 'no data')}</p>`;

  const diffusion = `<p class="howto caveat"><strong>State diffusion:</strong>
    ${escapeHtml(l?.state_diffusion?.reason ?? 'not built')}</p>`;

  return `${claims}
    <div class="tablewrap"><table><thead><tr>
    <th>Series</th><th class="num">Level</th><th class="num">1y</th>
    <th class="num">As of</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${diffusion}
    <p class="howto caveat">${escapeHtml(l?.how_to_read ?? '')}</p>`;
}

function inflationPanel(i: any): string {
  const rows = (i?.rows ?? []).map((r: any) => r.available
    ? `<tr><td>${escapeHtml(r.label)}</td>
       <td class="num ${dirClass(r.yoy_pct)}"><strong>${signed(r.yoy_pct, 2)}%</strong></td>
       <td class="num ${dirClass(r.change_90d_pct)}">${signed(r.change_90d_pct, 2)}%</td>
       <td class="num dim">${num(r.index_level, 2)}</td>
       <td class="num dim">${escapeHtml(r.as_of)}</td></tr>`
    : `<tr><td>${escapeHtml(r.label)}</td>
       <td colspan="4" class="na">${escapeHtml(r.reason)}</td></tr>`).join('');
  if (!rows) return panelError('no inflation series stored');
  return `<div class="tablewrap"><table><thead><tr>
    <th>Series</th><th class="num">Year on year</th><th class="num">90d</th>
    <th class="num">Index</th><th class="num">As of</th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <p class="howto caveat">${escapeHtml(i?.how_to_read ?? '')}</p>`;
}

function policyPanel(p: any): string {
  const fomc = p?.next_fomc?.available
    ? gauge('Next FOMC', `${p.next_fomc.days_away}<span class="u">d</span>`,
      escapeHtml(p.next_fomc.date))
    : gauge('Next FOMC', '—', escapeHtml(p?.next_fomc?.reason ?? ''));

  const rate = (entry: any, label: string) => entry?.value !== undefined
    ? gauge(label, `${num(entry.value, 2)}<span class="u">%</span>`,
      escapeHtml(entry.as_of))
    : gauge(label, '—', escapeHtml(entry?.reason ?? 'no data'));

  return `<div class="gauges">
    ${rate(p?.effr, 'Effective fed funds')}
    ${rate(p?.sofr, 'SOFR')}
    ${gauge('2-year − EFFR', withUnit(p?.two_year_minus_effr, 'pp'),
    'negative implies cuts priced', dirClass(p?.two_year_minus_effr))}
    ${fomc}
  </div>
  <p class="howto caveat">${escapeHtml(p?.how_to_read ?? '')}</p>`;
}

/**
 * A composite, with every component beside it.
 *
 * §7.3 requires the components to be visible, so the composite value is never
 * rendered on its own. A family with nothing available is named rather than
 * silently dropped from the weighted mean.
 */
function compositePanel(c: any): string {
  if (!c || c.value === null || c.value === undefined) {
    return `<p class="na">Not computable: ${escapeHtml(
      (c?.missing ?? []).length
        ? `no component available in ${(c.missing as string[]).join(', ')}`
        : (c?.note ?? 'no components available'))}</p>`;
  }

  const families = Object.entries(c.families).map(([name, value]) =>
    `<div class="gauge"><div class="k">${escapeHtml(name)}
      <span class="dim">×${c.weights?.[name] ?? 1}</span></div>
      <div class="v">${(value as number).toFixed(2)}</div></div>`).join('');

  const rows = (c.components ?? []).map((row: any) => row.available === false
    ? `<tr><td>${escapeHtml(row.label)}</td><td class="dim">${escapeHtml(row.family)}</td>
       <td colspan="4" class="na">${escapeHtml(row.note ?? 'unavailable')}</td></tr>`
    : `<tr><td>${escapeHtml(row.label)}</td><td class="dim">${escapeHtml(row.family)}</td>
       <td class="num"><strong>${num(row.current, 2)}</strong></td>
       <td class="num dim">${num(row.six_months, 2)}</td>
       <td class="num dim">${num(row.one_year, 2)}</td>
       <td class="num dim">${num(row.four_years, 2)}</td></tr>`).join('');

  const missing = (c.missing ?? []).length
    ? `<p class="howto caveat">Families with no available component, dropped
       from the weighted mean rather than counted as zero:
       ${escapeHtml((c.missing as string[]).join(', '))}.</p>`
    : '';

  return `<div class="gauges">
      <div class="gauge"><div class="k">${escapeHtml(c.name)}</div>
        <div class="v">${c.value.toFixed(2)}</div>
        <div class="n">0 to 1, higher is tighter</div></div>
      ${families}
    </div>
    ${missing}
    <div class="tablewrap"><table><thead><tr>
    <th>Component</th><th>Family</th><th class="num">Now</th>
    <th class="num">6m</th><th class="num">1y</th><th class="num">4y</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function main(): Promise<void> {
  await mountFrame('macro');
  const root = document.getElementById('main') as HTMLElement;
  const data = await load<any>('macro');
  if (!data) {
    root.innerHTML = `<section class="panel"><h2>Macro</h2>
      <div class="body">${panelError('the macro artefact has not been built')}</div>
      </section>`;
    return;
  }
  const stamp = data.as_of?.slice(0, 10) ?? null;
  const comps = data.composites ?? {};

  root.innerHTML = [
    panel('Fed net liquidity', stamp, netLiquidity(data.net_liquidity),
      data.net_liquidity?.how_to_read),
    panel('Policy path', stamp, policyPanel(data.policy)),
    panel('Rates, the dollar and conditions', stamp, ratesTable(data.rates)),
    panel('Inflation', stamp, inflationPanel(data.inflation)),
    panel('Labour', stamp, labourPanel(data.labour)),
    panel('Commodities and ratios', stamp, commoditiesPanel(data.commodities)),
    panel('BTC correlations', stamp, correlationsPanel(data.correlations)),
    panel('Liquidity conditions composite', stamp, compositePanel(comps.liquidity),
      comps.how_to_read),
    panel('Business cycle composite', stamp, compositePanel(comps.business_cycle),
      comps.warmup_note),
  ].join('');

  const table = document.getElementById('rates');
  if (table) sortableTable(table as HTMLTableElement);
}

void main();
