/** Geopolitics, policy and events (SPEC §6.11). */

import './styles.css';
import { mountFrame } from './lib/frame';
import { load, panelError } from './lib/data';
import { compact, escapeHtml, dirClass } from './lib/format';
import { sortableTable } from './lib/table';

const pct1 = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(1)}`;

function panel(title: string, asOf: string | null, body: string, howto?: string): string {
  return `<section class="panel"><h2>${escapeHtml(title)}
    ${asOf ? `<span class="asof">${escapeHtml(asOf)}</span>` : ''}</h2>
    <div class="body">${howto ? `<p class="howto">${escapeHtml(howto)}</p>` : ''}${body}</div></section>`;
}

/**
 * The oil chain, as a row of linked boxes.
 *
 * Drawn even when most links are empty, because the shape of the argument is
 * itself the point: a reader should be able to see that five of the seven
 * values are missing and exactly which source would supply them. A version
 * that hid the empty links would quietly change the claim being made.
 */
function oilChain(chain: any): string {
  if (!chain?.links?.length) return panelError('no chain definition');
  const boxes = chain.links.map((link: any, index: number) => {
    const value = link.available
      ? `<div class="v">${escapeHtml(String(link.value))}${
        link.unit ? `<span class="u"> ${escapeHtml(link.unit)}</span>` : ''}</div>
         ${link.change_30d_pct !== null && link.change_30d_pct !== undefined
    ? `<div class="n ${dirClass(link.change_30d_pct)}">${pct1(link.change_30d_pct)}% 30d</div>`
    : `<div class="n">${escapeHtml(link.as_of ?? '')}</div>`}`
      : `<div class="v na">—</div><div class="n missing">${escapeHtml(link.reason ?? 'no source')}</div>`;
    return `${index ? '<div class="chain-arrow" aria-hidden="true">→</div>' : ''}
      <div class="chain-link ${link.available ? '' : 'empty'}">
        <div class="k">${escapeHtml(link.label)}</div>
        ${value}
        <div class="src">${escapeHtml(link.detail)}</div>
      </div>`;
  }).join('');

  return `<div class="chain">${boxes}</div>
    <p class="howto caveat"><strong>${chain.filled} of ${chain.total} links have a
    value.</strong> Each empty one names the series it needs. Nothing is
    interpolated across a gap.</p>`;
}

function themesPanel(themes: any): string {
  if (!themes?.available) return panelError(themes?.reason ?? 'no GDELT data');
  const rows = themes.rows.map((row: any) => {
    if (!row.available) {
      return `<tr><td>${escapeHtml(row.theme)}</td>
        <td colspan="5" class="na">${escapeHtml(row.reason)}</td></tr>`;
    }
    // A spike is only interesting above about 2; colour never carries the
    // meaning alone, so the number is always there to read.
    const hot = (row.spike_z ?? 0) >= 2;
    return `<tr>
      <td>${escapeHtml(row.theme)}${hot ? ' <span class="badge"><i></i>spike</span>' : ''}</td>
      <td class="num ${hot ? 'up' : ''}" data-v="${row.spike_z ?? ''}">${
  row.spike_z === null || row.spike_z === undefined ? '—' : row.spike_z.toFixed(2)}</td>
      <td class="num" data-v="${row.recent_volume_pct}">${row.recent_volume_pct.toFixed(3)}%</td>
      <td class="num dim" data-v="${row.baseline_volume_pct}">${row.baseline_volume_pct.toFixed(3)}%</td>
      <td class="num ${dirClass(row.tone_recent)}" data-v="${row.tone_recent ?? ''}">${
  row.tone_recent === null || row.tone_recent === undefined ? '—' : row.tone_recent.toFixed(2)}</td>
      <td class="num ${dirClass(row.tone_shift)}" data-v="${row.tone_shift ?? ''}">${
  row.tone_shift === null || row.tone_shift === undefined ? '—' : pct1(row.tone_shift)}</td>
      <td class="num dim">${escapeHtml(row.last_date ?? '')}</td>
    </tr>`;
  }).join('');

  return `<div class="tablewrap"><table id="themes"><thead><tr>
    <th data-sort="str">Theme</th>
    <th data-sort="num" class="num" title="Recent coverage against the prior baseline">Spike z</th>
    <th data-sort="num" class="num">Recent</th>
    <th data-sort="num" class="num">Baseline</th>
    <th data-sort="num" class="num" title="GDELT average tone; negative is more negative coverage">Tone</th>
    <th data-sort="num" class="num">Tone shift</th>
    <th class="num">Last day</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${themes.missing?.length
    ? `<p class="howto caveat"><strong>${themes.rows.length} of
        ${themes.configured} configured themes have data.</strong> Not yet
        fetched: ${escapeHtml(themes.missing.join(', '))}. GDELT rate-limits by
        source address and the sweep runs to a time budget, taking the stalest
        theme first, so the set fills in over successive builds. An absent theme
        here means it was not asked, not that it is quiet.</p>`
    : ''}
    <p class="howto caveat">${escapeHtml(themes.method_note)}</p>`;
}

function riskPanel(gpr: any, epu: any): string {
  const cards: string[] = [];

  if (gpr?.available) {
    const head = gpr.headline;
    cards.push(gauge('Geopolitical risk (GPR)', head.latest,
      `${head.percentile}th percentile of ${head.n.toLocaleString()} days since ${head.start.slice(0, 4)}`));
    cards.push(gauge('Threats', gpr.threat.latest,
      `vs acts ${gpr.act.latest} · gap ${pct1(gpr.gap)}`));
  }
  if (epu?.available) {
    cards.push(gauge('Policy uncertainty (EPU)', epu.latest,
      `${epu.percentile}th percentile of ${epu.n.toLocaleString()} days since ${epu.start.slice(0, 4)}`));
  }
  if (!cards.length) return panelError('neither risk index has been fetched');

  const reading = gpr?.available
    ? `<p class="howto caveat"><strong>${escapeHtml(gpr.reading)}.</strong> The
       index separates coverage of THREATS from coverage of ACTS. They diverge
       exactly when the headline number is least informative.</p>`
    : '';
  return `<div class="gauges">${cards.join('')}</div>${reading}`;
}

function gauge(label: string, value: number | null, note: string): string {
  return `<div class="gauge"><div class="k">${escapeHtml(label)}</div>
    <div class="v">${value === null || value === undefined ? '—' : value.toFixed(1)}</div>
    <div class="n">${escapeHtml(note)}</div></div>`;
}

function eventsPanel(events: any): string {
  if (!events?.available) return panelError(events?.reason ?? 'no odds snapshot');
  const blocks = Object.entries(events.topics).map(([topic, markets]: [string, any]) => {
    const rows = markets.slice(0, 6).map((m: any) => `<tr>
      <td>${escapeHtml(m.question)}</td>
      <td class="num">${m.probability.toFixed(1)}%</td>
      <td class="num ${dirClass(m.change_pts)}">${
  m.change_pts === null || m.change_pts === undefined
    ? '<span class="na">—</span>' : `${pct1(m.change_pts)} pts`}</td>
      <td class="num dim">${m.volume ? `$${compact(m.volume)}` : '—'}</td>
      <td class="dim">${escapeHtml(m.venue)}</td>
    </tr>`).join('');
    return `<div class="topic"><h3>${escapeHtml(topic)}</h3>
      <div class="tablewrap"><table><thead><tr>
      <th>Market</th><th class="num">Probability</th><th class="num">1d</th>
      <th class="num">Volume</th><th>Venue</th></tr></thead>
      <tbody>${rows}</tbody></table></div></div>`;
  }).join('');

  const trend = events.snapshot_days > 1
    ? `${events.snapshot_days} days of odds history so far.`
    : 'Odds history starts today: neither venue serves a free price series, so '
      + 'the 1d column is blank until there are two snapshots to compare.';
  return `${blocks}<p class="howto caveat">${escapeHtml(trend)}</p>`;
}

function calendarPanel(calendar: any): string {
  if (!calendar?.available) return panelError(calendar?.reason ?? 'no calendar');
  const rows = calendar.rows.map((row: any) => {
    const when = row.days_away < 0
      ? `<span class="dim">${row.days_away}d</span>`
      : `<strong>${row.days_away === 0 ? 'today' : `${row.days_away}d`}</strong>`;
    return `<tr>
      <td class="num">${escapeHtml(row.date)}</td>
      <td class="num">${when}</td>
      <td>${escapeHtml(row.title)}</td>
      <td class="dim">${escapeHtml(row.category)}</td>
      <td class="dim"><span class="badge"><i></i>${escapeHtml(row.provenance)}</span>
        ${escapeHtml(row.detail ?? '')}</td>
    </tr>`;
  }).join('');

  const base = (import.meta.env.BASE_URL ?? '/').replace(/\/$/, '');
  return `<p class="howto"><a href="${base}/data/calendar.ics" download>Download
    <strong>calendar.ics</strong></a> — subscribe to it on a phone and these
    dates appear alongside everything else.</p>
    <div class="tablewrap"><table id="calendar"><thead><tr>
    <th data-sort="str">Date</th><th class="num">In</th>
    <th data-sort="str">Event</th><th data-sort="str">Category</th>
    <th>Where it came from</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="howto caveat">${escapeHtml(calendar.how_to_read)}</p>`;
}

function narrativePanel(narrative: any): string {
  if (!narrative?.available) return panelError(narrative?.reason ?? 'no narrative file');
  if (!narrative.entries.length) return '<p class="na">No entries yet.</p>';
  const entries = narrative.entries.map((entry: any) => `<article class="note">
    <h3>${escapeHtml(entry.date)}</h3>
    <p>${escapeHtml(entry.body).replace(/\n\n+/g, '</p><p>')}</p></article>`).join('');
  return `${entries}<p class="howto caveat">${escapeHtml(narrative.note)}</p>`;
}

async function main(): Promise<void> {
  await mountFrame('geopolitics');
  const root = document.getElementById('main') as HTMLElement;
  const data = await load<any>('geopolitics');
  if (!data) {
    root.innerHTML = `<section class="panel"><h2>Geopolitics</h2>
      <div class="body">${panelError('the geopolitics artefact has not been built')}</div>
      </section>`;
    return;
  }
  const stamp = data.as_of?.slice(0, 10) ?? null;

  root.innerHTML = [
    panel('Oil → inflation → Fed → liquidity → crypto', stamp,
      oilChain(data.oil_chain), data.oil_chain?.how_to_read),
    panel('Risk indices', stamp, riskPanel(data.gpr, data.epu),
      'Two published daily indices: how much of the world’s news is about '
      + 'geopolitical risk, and how much is about policy uncertainty. Both are '
      + 'indexed so that 100 is their own long-run average.'),
    panel('Themes — coverage and tone', stamp, themesPanel(data.themes),
      'Which of the conditions around the book the world is suddenly talking '
      + 'about, and whether that coverage is getting more negative.'),
    panel('Event markets', data.events?.as_of ?? stamp, eventsPanel(data.events),
      data.events?.how_to_read),
    panel('Calendar', stamp, calendarPanel(data.calendar)),
    panel('Narrative log', stamp, narrativePanel(data.narrative)),
  ].join('');

  ['themes', 'calendar'].forEach((id) => {
    const table = document.getElementById(id);
    if (table) sortableTable(table as HTMLTableElement);
  });
}

void main();
