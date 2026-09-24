/** Source health: what worked, what did not, and what needs a key (SPEC §6.14). */

import './styles.css';
import { mountFrame } from './lib/frame';
import { load, panelError } from './lib/data';
import { escapeHtml, staleness } from './lib/format';
import { sortableTable } from './lib/table';

const STATUS_CLASS: Record<string, string> = {
  ok: 'ok', empty: 'warn', needs_key: 'warn', skipped: 'unknown',
  http_error: 'bad', network_error: 'bad',
};

async function main(): Promise<void> {
  const index = await load<{ assets: string[] }>('index');
  await mountFrame('source-health', index?.assets ?? []);
  const root = document.getElementById('main') as HTMLElement;
  const data = await load<any>('source-health');
  if (!data) { root.innerHTML = panelError('no health data'); return; }

  const counts = Object.entries(data.counts ?? {}).map(([status, n]: [string, any]) => `
    <div class="gauge"><div class="k">${escapeHtml(status.replace('_', ' '))}</div>
      <div class="v">${n}</div>
      <div class="n"><span class="badge ${STATUS_CLASS[status] ?? 'unknown'}"><i></i>${escapeHtml(status)}</span></div>
    </div>`).join('');

  const rows = data.rows.map((r: any) => {
    const stale = staleness(r.as_of ?? r.fetched_at, 'daily',
      r.expected_lag_days ?? 1, r.archival ?? false);
    return `<tr>
      <td>${escapeHtml(r.source)}</td>
      <td>${escapeHtml(r.dataset)}</td>
      <td data-v="${escapeHtml(r.status)}">
        <span class="badge ${STATUS_CLASS[r.status] ?? 'unknown'}"><i></i>${escapeHtml(r.status)}</span></td>
      <td class="num" data-v="${r.rows ?? 0}">${r.rows ?? '—'}</td>
      <td class="num">${escapeHtml(stale.text)}</td>
      <td style="white-space:normal;text-align:left;color:var(--muted)">${escapeHtml(r.error || '')}</td>
    </tr>`;
  }).join('');

  root.innerHTML = `
    <section class="panel"><h2>Source health
      <span class="asof">${escapeHtml(data.as_of.slice(0, 16).replace('T', ' '))}Z</span></h2>
      <div class="body">
        <p class="howto">${escapeHtml(data.how_to_read)}</p>
        <div class="gauges">${counts}</div>
        <div class="tablewrap" style="margin-top:12px"><table id="health">
          <thead><tr><th data-sort="str">Source</th><th data-sort="str">Dataset</th>
            <th data-sort="str">Status</th><th data-sort="num" class="num">Rows</th>
            <th class="num">As of</th><th style="text-align:left">Detail</th></tr></thead>
          <tbody>${rows}</tbody></table></div>
      </div></section>`;

  sortableTable(document.getElementById('health') as HTMLTableElement);
}

void main();
