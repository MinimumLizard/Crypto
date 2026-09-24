/** BTC cycle page: analogs, quantile bands and the risk scorecard (SPEC §6.5). */

import './styles.css';
import { mountFrame } from './lib/frame';
import { load, panelError } from './lib/data';
import { price, risk as fmtRisk, escapeHtml, dirClass } from './lib/format';

function panel(title: string, asOf: string | null, body: string, howto?: string): string {
  return `<section class="panel"><h2>${escapeHtml(title)}
    ${asOf ? `<span class="asof">${escapeHtml(asOf)}</span>` : ''}</h2>
    <div class="body">${howto ? `<p class="howto">${escapeHtml(howto)}</p>` : ''}${body}</div></section>`;
}

/** 1st, 2nd, 3rd, 10th — "1th percentile" is the kind of detail that makes a
 *  page look unfinished even when the number behind it is right. */
function ordinal(value: number): string {
  const n = Math.round(value);
  const tens = n % 100;
  if (tens >= 11 && tens <= 13) return `${n}th`;
  return `${n}${['th', 'st', 'nd', 'rd'][n % 10] ?? 'th'}`;
}

function quantilePanel(q: any): string {
  if (!q) return panelError('not computed');
  if (!q.available) {
    return `${panelError(q.reason ?? 'the replication gate did not pass')}
      <p class="howto caveat">The bands are withheld deliberately. They only ship if
      the fit reproduces the published parameters; a model that does not replicate is
      not shown just because it produced numbers.</p>`;
  }
  const checks = Object.entries(q.replication.checks).map(([key, c]: [string, any]) => `
    <tr><td>${escapeHtml(key)}</td><td class="num">${c.expected}</td>
      <td class="num">${c.observed}</td><td class="num">${c.difference}</td>
      <td><span class="badge ${c.passed ? 'ok' : 'bad'}"><i></i>${c.passed ? 'pass' : 'fail'}</span></td></tr>`).join('');

  const bands = Object.entries(q.bands_today).map(([tau, value]: [string, any]) => `
    <tr><td>${ordinal(Number(tau) * 100)} percentile</td>
        <td class="num">${price(value, '$')}</td></tr>`).join('');

  return `
    <div class="gauges">
      <div class="gauge"><div class="k">Position in the distribution</div>
        <div class="v">${q.position_pct}<span style="font-size:14px;color:var(--muted)">th</span></div>
        <div class="bar"><span style="width:${q.position_pct}%"></span></div>
        <div class="n">percentile of the fitted conditional distribution</div></div>
      <div class="gauge"><div class="k">Sample</div>
        <div class="v" style="font-size:18px">${q.n.toLocaleString()}</div>
        <div class="n">daily closes · μ = ${q.mu}</div></div>
      <div class="gauge"><div class="k">Tail curvature</div>
        <div class="v" style="font-size:16px">lo ${q.curvature.lo} · hi ${q.curvature.hi}</div>
        <div class="n">shared within each tail group · full sample to ${escapeHtml(q.fitted_through ?? '—')}</div></div>
    </div>
    <div class="grid2" style="margin-top:12px">
      <div><h3 style="font-size:12px;color:var(--ink-2);margin:0 0 6px">Bands today</h3>
        <div class="tablewrap"><table><tbody>${bands}</tbody></table></div></div>
      <div><h3 style="font-size:12px;color:var(--ink-2);margin:0 0 6px">Replication gate</h3>
        <div class="tablewrap"><table>
          <thead><tr><th>Parameter</th><th class="num">Published</th><th class="num">Ours</th>
            <th class="num">Diff</th><th></th></tr></thead>
          <tbody>${checks}</tbody></table></div></div>
    </div>
    ${q.curvature_note ? `<p class="howto">${escapeHtml(q.curvature_note)}</p>` : ''}
    <p class="howto caveat">${escapeHtml(q.how_to_read)}</p>`;
}

function scorecardPanel(s: any): string {
  if (!s) return panelError('not computed');
  const families: Record<string, any[]> = {};
  for (const row of s.rows) (families[row.family] ??= []).push(row);

  const bar = (value: number | null) => value === null || value === undefined
    ? '<span class="na">—</span>'
    : `<span style="display:inline-flex;align-items:center;gap:6px">
         <span style="display:inline-block;width:52px;height:4px;background:var(--grid);border-radius:2px;overflow:hidden">
           <span style="display:block;height:100%;width:${value * 100}%;background:${
             value > 0.66 ? 'var(--down)' : value > 0.33 ? 'var(--warn)' : 'var(--up)'}"></span>
         </span>${fmtRisk(value)}</span>`;

  const body = Object.entries(families).map(([family, rows]) => `
    <tr><td colspan="5" style="padding-top:12px;color:var(--accent);font-size:11px;
        text-transform:uppercase;letter-spacing:.06em">${escapeHtml(family)}</td></tr>
    ${rows.map((r) => r.available ? `
      <tr><td>${escapeHtml(r.label)}</td>
        <td class="num">${bar(r.current)}</td>
        <td class="num">${fmtRisk(r.six_months)}</td>
        <td class="num">${fmtRisk(r.one_year)}</td>
        <td class="num">${fmtRisk(r.four_years)}</td></tr>`
      : `<tr><td>${escapeHtml(r.label)}</td>
        <td colspan="4" class="na">source unavailable: ${escapeHtml(r.note)}</td></tr>`).join('')}`).join('');

  const composite = s.composite;
  return `
    <div class="gauges">
      <div class="gauge"><div class="k">Composite</div>
        <div class="v">${fmtRisk(composite.value)}</div>
        <div class="bar"><span style="width:${(composite.value ?? 0) * 100}%"></span></div>
        <div class="n">${Object.keys(composite.families ?? {}).length} families, equally weighted</div></div>
      ${Object.entries(composite.families ?? {}).map(([name, value]: [string, any]) => `
        <div class="gauge"><div class="k">${escapeHtml(name)}</div>
          <div class="v">${fmtRisk(value)}</div>
          <div class="bar"><span style="width:${value * 100}%"></span></div>
          <div class="n">family mean</div></div>`).join('')}
    </div>
    <div class="tablewrap" style="margin-top:12px"><table>
      <thead><tr><th>Metric</th><th class="num">Current</th><th class="num">6M ago</th>
        <th class="num">1Y ago</th><th class="num">4Y ago</th></tr></thead>
      <tbody>${body}</tbody></table></div>
    <p class="howto caveat">${escapeHtml(s.caveat)}</p>`;
}

function cyclesPanel(data: any): string {
  const current = data.current;
  const rows = data.cycles.map((c: any) => `
    <tr><td>${escapeHtml(c.peak_date)}</td><td class="num">${price(c.peak_price, '$')}</td>
      <td>${escapeHtml(c.low_date)}</td><td class="num">${price(c.low_price, '$')}</td>
      <td class="num down">−${c.drawdown_pct}%</td></tr>`).join('');
  return `
    <div class="gauges">
      <div class="gauge"><div class="k">Days since peak</div>
        <div class="v">${current.days_since_peak}</div>
        <div class="n">peak ${escapeHtml(current.peak_date)} at ${price(current.peak_price, '$')}</div></div>
      <div class="gauge"><div class="k">ROI from peak</div>
        <div class="v">${current.roi_from_peak}</div>
        <div class="n">price at ${(current.roi_from_peak * 100).toFixed(1)}% of the peak
          · <span class="down">−${current.drawdown_pct}%</span></div></div>
      <div class="gauge"><div class="k">Prior peak → low</div>
        <div class="v" style="font-size:15px">${current.prior_peak_to_low_days.join(' · ')}</div>
        <div class="n">days, previous cycles</div></div>
    </div>
    <div class="tablewrap" style="margin-top:12px"><table>
      <thead><tr><th>Peak</th><th class="num">Price</th><th>Low</th>
        <th class="num">Price</th><th class="num">Drawdown</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="howto">${escapeHtml(data.roi_convention)}</p>
    <p class="howto caveat">Cycle boundaries are detected from price: a peak is an
    all-time high later retraced by more than 55%, and the low is the lowest close
    before the next peak. They are approximate, and there are only four completed
    cycles, so any statement about "this point in the cycle" rests on that sample.</p>`;
}

function midtermPanel(m: any): string {
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const cell = (value: number | null) => value === null || value === undefined
    ? '<td class="na">—</td>'
    : `<td class="num ${dirClass(value)}">${value.toFixed(1)}</td>`;
  return `<div class="tablewrap"><table>
    <thead><tr><th>Year</th>${months.map((m2) => `<th class="num">${m2}</th>`).join('')}</tr></thead>
    <tbody>${m.rows.map((row: any) => `<tr><td><strong>${row.year}</strong></td>
      ${months.map((_, i) => {
        const key = String(i + 1);
        const isPartial = (row.partial ?? []).includes(key);
        const inner = cell(row.months[key]);
        return isPartial
          ? inner.replace('</td>', ' <span class="badge warn" title="month still running"><i></i>partial</span></td>')
          : inner;
      }).join('')}</tr>`).join('')}
    </tbody></table></div>
    <p class="howto caveat">${escapeHtml(m.how_to_read)}</p>`;
}

async function main(): Promise<void> {
  const index = await load<{ assets: string[] }>('index');
  await mountFrame('btc-cycle', index?.assets ?? []);
  const root = document.getElementById('main') as HTMLElement;
  const data = await load<any>('btc-cycle');

  if (!data?.available) {
    root.innerHTML = panel('BTC cycle', null, panelError(data?.reason ?? 'not built'));
    return;
  }

  root.innerHTML = [
    panel('Where this cycle sits', data.as_of, cyclesPanel(data),
      'Every completed cycle since 2011, and how far this one has run.'),
    panel('Quantile bands', data.as_of, quantilePanel(data.quantile),
      'A quantile regression of log price on log time, with the upper and lower '
      + 'tails allowed to curve differently. It ships only because it reproduces '
      + 'the published parameters — the gate is shown in full.'),
    panel('Risk scorecard', data.as_of, scorecardPanel(data.scorecard),
      'Each metric against its own history as a 0–1 percentile, computed on an '
      + 'expanding window so no reading ever used data from after its own date.'),
    panel('Midterm-year monthly returns', data.as_of, midtermPanel(data.midterm_monthly),
      'Bitcoin’s monthly returns in midterm election years, percent.'),
  ].join('');
}

void main();
