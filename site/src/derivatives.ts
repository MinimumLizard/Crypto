/** Derivatives and volatility (SPEC §6.9). */

import './styles.css';
import { mountFrame } from './lib/frame';
import { load, panelError } from './lib/data';
import { compact, escapeHtml, dirClass } from './lib/format';
import { sortableTable } from './lib/table';

const pct1 = (n: number) => `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`;

function panel(title: string, asOf: string | null, body: string, howto?: string): string {
  return `<section class="panel"><h2>${escapeHtml(title)}
    ${asOf ? `<span class="asof">${escapeHtml(asOf)}</span>` : ''}</h2>
    <div class="body">${howto ? `<p class="howto">${escapeHtml(howto)}</p>` : ''}${body}</div></section>`;
}

function fundingTable(rows: any[]): string {
  if (!rows.length) return panelError('no funding snapshots yet');
  const venueCell = (v: any) => {
    if (!v || v.annualised_pct === null || v.annualised_pct === undefined) {
      return '<td class="na">—</td>';
    }
    return `<td class="num ${dirClass(v.annualised_pct)}"
      title="raw ${v.rate} every ${v.interval_hours}h">${pct1(v.annualised_pct)}</td>`;
  };
  const body = rows.map((r) => {
    const z = r.zscore?.available ? r.zscore.zscore : null;
    const extreme = z !== null && Math.abs(z) > 2;
    return `<tr>
      <td><a href="asset.html?s=${r.symbol}">${escapeHtml(r.symbol)}</a></td>
      ${venueCell(r.venues.HlPerp)}
      ${venueCell(r.venues.BinPerp)}
      ${venueCell(r.venues.BybitPerp)}
      <td class="num ${dirClass(r.mean_annualised_pct)}" data-v="${r.mean_annualised_pct ?? ''}">
        <strong>${r.mean_annualised_pct !== null ? pct1(r.mean_annualised_pct) : '—'}</strong></td>
      <td class="num" data-v="${r.spread_pct ?? ''}">${r.spread_pct !== null && r.spread_pct !== undefined ? `${r.spread_pct.toFixed(1)}pp` : '—'}</td>
      <td class="num" data-v="${z ?? ''}">${z !== null
        ? `${extreme ? '<span class="badge warn"><i></i>' : ''}${z >= 0 ? '+' : ''}${z.toFixed(2)}${extreme ? '</span>' : ''}`
        : `<span class="na" title="${escapeHtml(r.zscore?.reason ?? '')}">—</span>`}</td>
    </tr>`;
  }).join('');

  return `<div class="tablewrap"><table id="funding"><thead><tr>
    <th data-sort="str">Asset</th>
    <th class="num" title="Hyperliquid funds hourly">Hyperliquid</th>
    <th class="num" title="Binance funds every 4 or 8 hours">Binance</th>
    <th class="num" title="Bybit funds every 4 or 8 hours">Bybit</th>
    <th data-sort="num" class="num">Mean</th>
    <th data-sort="num" class="num" title="Widest gap between venues, percentage points">Spread</th>
    <th data-sort="num" class="num" title="Against this coin's own hourly funding distribution">z</th>
    </tr></thead><tbody>${body}</tbody></table></div>
    <p class="howto">All figures are ANNUALISED using each venue's own funding
    interval. The same raw rate is 8.76%/yr where funding is hourly and 1.10%/yr
    where it is every eight hours — a constant multiplier can invert the sign of
    the comparison. Hover any cell for the raw rate and interval.</p>`;
}

function oiTable(rows: any[], caveat: string): string {
  if (!rows.length) return panelError('no perp context snapshots yet');
  const snaps = rows[0]?.snapshots_held ?? 0;
  const body = rows.map((r) => `<tr>
    <td><a href="asset.html?s=${r.symbol}">${escapeHtml(r.symbol)}</a></td>
    <td class="num" data-v="${r.open_interest_usd ?? ''}">$${compact(r.open_interest_usd)}</td>
    <td class="num" data-v="${r.day_volume_usd ?? ''}">$${compact(r.day_volume_usd)}</td>
    <td class="num" data-v="${r.oi_to_market_cap ?? ''}">${r.oi_to_market_cap !== null && r.oi_to_market_cap !== undefined ? `${(r.oi_to_market_cap * 100).toFixed(1)}%` : '—'}</td>
    <td class="num" data-v="${r.oi_to_volume ?? ''}">${r.oi_to_volume ? `${r.oi_to_volume.toFixed(2)}×` : '—'}</td>
    <td class="num ${dirClass(r.oi_change_pct)}" data-v="${r.oi_change_pct ?? ''}">${r.oi_change_pct !== null && r.oi_change_pct !== undefined ? pct1(r.oi_change_pct) : '—'}</td>
    <td class="num ${dirClass(r.price_change_pct)}" data-v="${r.price_change_pct ?? ''}">${r.price_change_pct !== null && r.price_change_pct !== undefined ? pct1(r.price_change_pct) : '—'}</td>
    <td>${r.quadrant === 'unknown' ? '<span class="na">needs history</span>' : escapeHtml(r.quadrant)}</td>
  </tr>`).join('');

  return `<div class="tablewrap"><table id="oi"><thead><tr>
    <th data-sort="str">Asset</th>
    <th data-sort="num" class="num">Open interest</th>
    <th data-sort="num" class="num">24h volume</th>
    <th data-sort="num" class="num" title="Notional riding on the name relative to its size">OI / mkt cap</th>
    <th data-sort="num" class="num">OI / volume</th>
    <th data-sort="num" class="num">OI change</th>
    <th data-sort="num" class="num">Price change</th>
    <th>Reading</th></tr></thead><tbody>${body}</tbody></table></div>
    <p class="howto caveat">${escapeHtml(caveat)} Currently holding
    <strong>${snaps}</strong> snapshot${snaps === 1 ? '' : 's'}.</p>`;
}

function volPanel(vol: any[], implied: Record<string, any>): string {
  const cards = Object.entries(implied).map(([currency, v]: [string, any]) => `
    <div class="gauge"><div class="k">${escapeHtml(currency)} implied (DVOL)</div>
      <div class="v">${v.latest.toFixed(1)}</div>
      <div class="n">realised 30d ${v.realised_30d ? v.realised_30d.toFixed(1) : '—'}
        · premium <span class="${dirClass(v.premium)}">${v.premium !== null && v.premium !== undefined ? (v.premium >= 0 ? '+' : '') + v.premium.toFixed(1) : '—'}</span> vol pts</div>
    </div>`).join('');

  const rows = vol.filter((r) => r.realised_30d !== null).map((r) => `<tr>
    <td><a href="asset.html?s=${r.symbol}">${escapeHtml(r.symbol)}</a></td>
    <td class="num" data-v="${r.realised_30d ?? ''}">${r.realised_30d ? r.realised_30d.toFixed(0) : '—'}</td>
    <td class="num" data-v="${r.realised_90d ?? ''}">${r.realised_90d ? r.realised_90d.toFixed(0) : '—'}</td>
    <td class="num" data-v="${r.atr?.atr_pct ?? ''}">${r.atr?.available ? `${r.atr.atr_pct}%` : '—'}</td>
    <td class="num" data-v="${r.atr?.percentile ?? ''}">${r.atr?.available
      ? `${r.atr.percentile.toFixed(0)}<span style="color:var(--muted)">th</span>` : '—'}</td>
  </tr>`).join('');

  return `<div class="gauges">${cards}</div>
    <div class="tablewrap" style="margin-top:12px"><table id="vol"><thead><tr>
      <th data-sort="str">Asset</th>
      <th data-sort="num" class="num" title="Annualised stdev of daily log returns">Realised 30d</th>
      <th data-sort="num" class="num">Realised 90d</th>
      <th data-sort="num" class="num">ATR %</th>
      <th data-sort="num" class="num" title="Where ATR% sits in its own trailing year">ATR pctile</th>
    </tr></thead><tbody>${rows}</tbody></table></div>
    <p class="howto">A vol premium above zero means options are priced above what
    the asset has actually been doing. ATR percentile ranks each name against its
    OWN year: the level of ATR says little across assets, its percentile says
    whether this name is unusually quiet or unusually busy.</p>`;
}

function basisPanel(basis: Record<string, any[]>): string {
  const blocks = Object.entries(basis).map(([currency, rows]) => {
    if (!rows.length) return `<p class="missing">${escapeHtml(currency)}: no dated futures</p>`;
    return `<div><h3 style="font-size:12px;color:var(--ink-2);margin:0 0 6px">${escapeHtml(currency)}</h3>
      <div class="tablewrap"><table><thead><tr>
        <th>Expiry</th><th class="num">Days</th><th class="num">Basis</th>
        <th class="num">Annualised</th></tr></thead>
      <tbody>${rows.slice(0, 8).map((b) => `<tr>
        <td>${escapeHtml(b.expiry)}</td>
        <td class="num">${b.days}</td>
        <td class="num ${dirClass(b.basis_pct)}">${b.basis_pct.toFixed(2)}%</td>
        <td class="num ${dirClass(b.annualised_pct)}">${pct1(b.annualised_pct)}</td>
      </tr>`).join('')}</tbody></table></div></div>`;
  }).join('');
  return `<div class="grid2">${blocks}</div>
    <p class="howto">Basis is the dated future against spot, annualised over the
    days remaining. A positive curve is the cost of carrying a long position
    through the future rather than the spot market.</p>`;
}

function optionsPanel(options: Record<string, any>): string {
  const blocks = Object.entries(options).map(([currency, o]: [string, any]) => {
    if (!o || !o.term_structure?.length) {
      return `<p class="missing">${escapeHtml(currency)}: no option data</p>`;
    }
    return `<div><h3 style="font-size:12px;color:var(--ink-2);margin:0 0 6px">
      ${escapeHtml(currency)} — put/call OI ${o.put_call_ratio ? o.put_call_ratio.toFixed(2) : '—'}</h3>
      <div class="tablewrap"><table><thead><tr>
        <th>Expiry</th><th class="num">Days</th><th class="num">IV</th>
        <th class="num">Open interest</th></tr></thead>
      <tbody>${o.term_structure.slice(0, 8).map((t: any) => `<tr>
        <td>${escapeHtml(t.expiry)}</td><td class="num">${t.days}</td>
        <td class="num">${t.iv.toFixed(1)}</td>
        <td class="num">${compact(t.open_interest)}</td></tr>`).join('')}
      </tbody></table></div></div>`;
  }).join('');
  const note = (Object.values(options)[0] as any)?.skew_note ?? '';
  return `<div class="grid2">${blocks}</div>
    <p class="howto">IV is the median across strikes at each expiry — a mean is
    dragged by deep wings whose quotes are wide and barely traded.</p>
    ${note ? `<p class="howto caveat">${escapeHtml(note)}</p>` : ''}`;
}

function availabilityPanel(rows: any[]): string {
  const mark = (ok: boolean) => ok
    ? '<span class="badge ok"><i></i>yes</span>'
    : '<span class="na">—</span>';
  return `<div class="tablewrap"><table><thead><tr>
    <th>Asset</th><th>Hyperliquid perp</th><th>Binance spot</th></tr></thead>
    <tbody>${rows.map((r) => `<tr><td>${escapeHtml(r.symbol)}</td>
      <td>${mark(r.hyperliquid)}</td><td>${mark(r.binance)}</td></tr>`).join('')}
    </tbody></table></div>`;
}

async function main(): Promise<void> {
  const index = await load<{ assets: string[] }>('index');
  await mountFrame('derivatives', index?.assets ?? []);
  const root = document.getElementById('main') as HTMLElement;
  const d = await load<any>('derivatives');
  if (!d) {
    root.innerHTML = panel('Derivatives', null, panelError('no derivatives artefact'));
    return;
  }
  const stamp = d.as_of.slice(0, 10);

  root.innerHTML = [
    panel('Funding, annualised per venue', stamp, fundingTable(d.funding), d.how_to_read),
    panel('Open interest', stamp, oiTable(d.open_interest, d.oi_caveat),
      'OI against market cap is the leverage gauge: how much notional is riding '
      + 'on a name relative to its size.'),
    panel('Volatility — implied against realised', stamp, volPanel(d.volatility, d.implied)),
    panel('Futures basis', stamp, basisPanel(d.basis)),
    panel('Options — term structure and put/call', stamp, optionsPanel(d.options)),
    panel('Perp availability', stamp, availabilityPanel(d.availability),
      'Which venues carry each name. Taken from the registry rather than a venue '
      + 'sweep, because Binance and Bybit refuse US addresses and every GitHub '
      + 'runner is one.'),
  ].join('');

  ['funding', 'oi', 'vol'].forEach((id) =>
    sortableTable(document.getElementById(id) as HTMLTableElement));
}

void main();
