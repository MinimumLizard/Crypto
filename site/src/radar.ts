/** Catalyst radar, screener and feeds (SPEC §6.13). */

import './styles.css';
import { mountFrame } from './lib/frame';
import { load, panelError } from './lib/data';
import { compact, escapeHtml, dirClass } from './lib/format';
import { sortableTable } from './lib/table';

const NEAR_LEVEL_PCT = 3.0;

const pct1 = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`;

function panel(title: string, asOf: string | null, body: string, howto?: string): string {
  return `<section class="panel"><h2>${escapeHtml(title)}
    ${asOf ? `<span class="asof">${escapeHtml(asOf)}</span>` : ''}</h2>
    <div class="body">${howto ? `<p class="howto">${escapeHtml(howto)}</p>` : ''}${body}</div></section>`;
}

/** A 0-1 component as a bar, with the number beside it. */
function bar(value: number | undefined): string {
  if (value === undefined) return '<span class="na">—</span>';
  return `<span class="minibar" title="${value.toFixed(2)}">
    <i style="width:${Math.round(value * 100)}%"></i></span>`;
}

function radarTable(radar: any): string {
  if (!radar?.available) return panelError(radar?.reason ?? 'nothing passed the gate');

  const keys = Object.keys(radar.weights);
  const head = keys.map((k) => `<th class="num" title="weight ${radar.weights[k]}">${
    escapeHtml(k.replace(/_/g, ' '))}</th>`).join('');

  const rows = radar.rows.map((row: any) => {
    const cells = keys.map((k) => `<td class="num">${bar(row.components[k])}</td>`).join('');
    // Only call a level "near" when it actually is. The component already
    // scores 0 beyond the threshold, but printing "200-week 36%" under a
    // heading that says near would be misleading in a way the score is not.
    const near = row.distance_to_level_pct !== null
      && row.distance_to_level_pct !== undefined
      && row.distance_to_level_pct <= NEAR_LEVEL_PCT
      ? `${escapeHtml(row.nearest_level)} ${row.distance_to_level_pct.toFixed(1)}%`
      : '<span class="na">—</span>';
    return `<tr>
      <td><a href="asset.html?s=${encodeURIComponent(row.symbol)}">${escapeHtml(row.symbol)}</a></td>
      <td class="num"><strong>${row.score.toFixed(2)}</strong></td>
      <td class="num dim">${row.score_pct === null || row.score_pct === undefined
    ? '—' : `${row.score_pct.toFixed(1)}%`}</td>
      ${cells}
      <td class="num ${dirClass(row.funding_z)}">${
  row.funding_z === null || row.funding_z === undefined ? '—' : row.funding_z.toFixed(2)}</td>
      <td class="num">${row.volume_z === null || row.volume_z === undefined
    ? '—' : row.volume_z.toFixed(2)}</td>
      <td>${near}</td>
      <td class="num dim">$${compact(row.day_volume_usd)}</td>
    </tr>`;
  }).join('');

  const gated = radar.gated.length
    ? `<details class="gated"><summary><strong>${radar.gated.length} names did not
        pass the liquidity gate</strong> — listed, not dropped</summary>
        <ul>${radar.gated.map((g: any) =>
    `<li><strong>${escapeHtml(g.symbol)}</strong> — ${escapeHtml(g.reason)}</li>`).join('')}</ul>
      </details>`
    : '';

  return `<div class="tablewrap"><table id="radar"><thead><tr>
    <th data-sort="str">Asset</th>
    <th data-sort="num" class="num">Score</th>
    <th data-sort="num" class="num" title="Score against what could be measured for this name">of measurable</th>
    ${head}
    <th data-sort="num" class="num">Funding z</th>
    <th data-sort="num" class="num">Volume z</th>
    <th>Nearest level</th>
    <th data-sort="num" class="num">24h volume</th>
    </tr></thead><tbody>${rows}</tbody></table></div>
    ${gated}
    <p class="howto caveat">${escapeHtml(radar.score_note)}</p>
    <p class="howto caveat">Gate: 24h volume above
    $${compact(radar.gate.min_day_volume_usd)} and market cap above
    $${compact(radar.gate.min_market_cap_usd)}, applied BEFORE ranking.</p>`;
}

function screenerTable(screener: any): string {
  if (!screener?.available) return panelError(screener?.reason ?? 'no screener data');
  const rows = screener.rows.map((row: any) => `<tr>
    <td>${escapeHtml(row.symbol)}${row.tracked ? ' <span class="badge"><i></i>book</span>' : ''}</td>
    <td>${escapeHtml(row.name ?? '')}</td>
    <td class="num">$${compact(row.market_cap)}</td>
    <td class="num">$${compact(row.volume_24h)}</td>
    <td class="num" data-v="${row.volume_to_cap ?? ''}">${
  row.volume_to_cap === null || row.volume_to_cap === undefined
    ? '—' : `${(row.volume_to_cap * 100).toFixed(1)}%`}</td>
    <td class="num" data-v="${row.fdv_to_cap ?? ''}">${
  row.fdv_to_cap === null || row.fdv_to_cap === undefined
    ? '—' : `${row.fdv_to_cap.toFixed(2)}×`}</td>
    <td class="num ${dirClass(row.change_24h)}">${pct1(row.change_24h)}</td>
    <td>${row.has_perp ? 'yes' : '<span class="na">—</span>'}</td>
  </tr>`).join('');

  return `<div class="tablewrap"><table id="screener"><thead><tr>
    <th data-sort="str">Symbol</th><th data-sort="str">Name</th>
    <th data-sort="num" class="num">Market cap</th>
    <th data-sort="num" class="num">24h volume</th>
    <th data-sort="num" class="num" title="Volume as a share of market cap">Vol / MC</th>
    <th data-sort="num" class="num" title="Fully diluted over circulating: how much supply is still to come">FDV / MC</th>
    <th data-sort="num" class="num">24h</th>
    <th>HL perp</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="howto caveat">${escapeHtml(screener.universe_note)}</p>`;
}

function governancePanel(governance: any): string {
  if (!governance?.available) return panelError(governance?.reason ?? 'no proposals');

  // A countdown only means something while a vote is open. Rendering a closed
  // proposal's "closes in" produced -307.7d, which is not a countdown.
  const openRows = (governance.open_rows ?? []).map((row: any) => `<tr>
    <td>${escapeHtml(row.symbol)}</td>
    <td>${escapeHtml(row.title)}</td>
    <td><span class="badge"><i></i>${escapeHtml(row.state)}</span></td>
    <td class="num">${row.closes_in_days === null || row.closes_in_days === undefined
    ? '—' : `${row.closes_in_days.toFixed(1)}d`}</td>
    <td class="num dim">${escapeHtml(row.end ?? '')}</td>
  </tr>`).join('');

  const open = openRows
    ? `<div class="tablewrap"><table><thead><tr>
        <th>Asset</th><th>Proposal</th><th>State</th><th class="num">Closes in</th>
        <th class="num">Ends</th></tr></thead><tbody>${openRows}</tbody></table></div>`
    : `<p class="na">No proposal is open right now in any of the
       ${(governance.spaces_configured ?? []).length} configured spaces. That is
       a measurement, not a gap.</p>`;

  const closedRows = (governance.closed_rows ?? []).map((row: any) => `<tr>
    <td>${escapeHtml(row.symbol)}</td>
    <td>${escapeHtml(row.title)}</td>
    <td class="num dim">${escapeHtml(row.end ?? '')}</td>
  </tr>`).join('');

  const closed = closedRows
    ? `<details class="gated"><summary><strong>Recently closed</strong> —
        ${(governance.closed_rows ?? []).length} proposals, newest first</summary>
        <div class="tablewrap"><table><thead><tr>
        <th>Asset</th><th>Proposal</th><th class="num">Ended</th></tr></thead>
        <tbody>${closedRows}</tbody></table></div></details>`
    : '';

  const elsewhere = Object.entries(governance.governs_elsewhere ?? {});
  const elsewhereNote = elsewhere.length
    ? `<p class="howto caveat">Not on Snapshot at all, so not absent by
       accident: ${elsewhere.map(([sym, where]) =>
    `<strong>${escapeHtml(sym)}</strong> (${escapeHtml(String(where))})`).join(', ')}.</p>`
    : '';

  const silent = (governance.spaces_silent ?? []).length
    ? `<p class="howto caveat">${(governance.spaces_answering ?? []).length} of
       ${(governance.spaces_configured ?? []).length} configured spaces returned
       proposals. Silent: ${escapeHtml((governance.spaces_silent ?? []).join(', '))}
       — either the space has no recent proposals or the slug is wrong, and this
       page cannot tell those apart.</p>`
    : '';

  return `${open}${closed}${silent}${elsewhereNote}
    <p class="howto caveat">${escapeHtml(governance.how_to_read)}</p>`;
}

function incidentRows(rows: any[]): string {
  return rows.map((row: any) => `<tr>
    <td class="num dim">${escapeHtml(row.date)}</td>
    <td>${row.link ? `<a href="${escapeHtml(row.link)}" rel="noopener noreferrer" target="_blank">${escapeHtml(row.name)}</a>` : escapeHtml(row.name)}</td>
    <td class="num">$${compact(row.amount_usd)}</td>
    <td class="dim">${escapeHtml(row.technique ?? '')}</td>
    <td>${row.assets ? `<span class="badge"><i></i>${escapeHtml(row.assets)}</span>` : ''}</td>
  </tr>`).join('');
}

function securityPanel(security: any): string {
  if (!security?.available) return panelError(security?.reason ?? 'no incident feed');

  const table = (rows: string) => `<div class="tablewrap"><table><thead><tr>
    <th>Date</th><th>Incident</th><th class="num">Lost</th><th>Technique</th>
    <th>Tagged</th></tr></thead><tbody>${rows}</tbody></table></div>`;

  // Incidents naming a held protocol come FIRST. Sorting the whole feed by date
  // put every one of them below twenty newer incidents that have nothing to do
  // with the book, so the panel announced four relevant incidents and then
  // showed none of them.
  const touching = security.touching_book ?? [];
  const relevant = touching.length
    ? `<p class="howto caveat"><strong>${touching.length} incident(s) name a
        tracked project</strong> — listed first, ahead of the general feed.</p>
       ${table(incidentRows(touching))}
       <h3 class="subhead">Everything else, newest first</h3>`
    : '<p class="howto caveat">No incident in the feed names a tracked project.</p>';

  const others = security.rows.filter((r: any) => !r.assets).slice(0, 20);
  return `${relevant}${table(incidentRows(others))}
    <p class="howto caveat">${escapeHtml(security.how_to_read)}</p>`;
}

function newsPanel(news: any): string {
  if (!news?.available) return panelError(news?.reason ?? 'no feeds fetched');
  const items = news.rows.slice(0, 30).map((row: any) => `<li>
    <span class="dim">${escapeHtml(row.published || '—')}</span>
    ${row.link ? `<a href="${escapeHtml(row.link)}" rel="noopener noreferrer" target="_blank">${escapeHtml(row.title)}</a>`
    : escapeHtml(row.title)}
    <span class="dim">· ${escapeHtml(row.source)}</span>
    ${row.assets ? `<span class="badge"><i></i>${escapeHtml(row.assets)}</span>` : ''}
  </li>`).join('');
  return `<ul class="feed">${items}</ul>
    <p class="howto caveat">${escapeHtml(news.how_to_read)}</p>`;
}

async function main(): Promise<void> {
  await mountFrame('radar');
  const root = document.getElementById('main') as HTMLElement;
  const data = await load<any>('radar');
  if (!data) {
    root.innerHTML = `<section class="panel"><h2>Radar</h2>
      <div class="body">${panelError('the radar artefact has not been built')}</div>
      </section>`;
    return;
  }
  const stamp = data.as_of?.slice(0, 10) ?? null;

  root.innerHTML = [
    panel('Catalyst radar', stamp, radarTable(data.radar), data.radar?.how_to_read),
    panel('Governance', stamp, governancePanel(data.governance)),
    panel('Security incidents', stamp, securityPanel(data.security)),
    panel('Screener', data.screener?.as_of ?? stamp, screenerTable(data.screener),
      data.screener?.how_to_read),
    panel('Feeds', stamp, newsPanel(data.news)),
  ].join('');

  ['radar', 'screener'].forEach((id) => {
    const table = document.getElementById(id);
    if (table) sortableTable(table as HTMLTableElement);
  });
}

void main();
