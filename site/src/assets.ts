/** Index of every tracked asset. */

import './styles.css';
import { mountFrame } from './lib/frame';
import { load, panelError } from './lib/data';
import { price, pct, dirClass, escapeHtml } from './lib/format';
import { sortableTable } from './lib/table';

async function main(): Promise<void> {
  const home = await load<any>('home');
  await mountFrame('assets', home?.grid?.map((g: any) => g.symbol) ?? []);
  const root = document.getElementById('main') as HTMLElement;
  if (!home) { root.innerHTML = panelError('no build data'); return; }

  const groups: [string, string][] = [
    ['benchmark', 'Benchmarks'], ['book', 'The book'], ['watchlist', 'Watchlist'],
  ];
  root.innerHTML = groups.map(([kind, label]) => {
    const rows = home.grid.filter((g: any) => g.kind === kind);
    if (!rows.length) return '';
    return `<section class="panel"><h2>${label}
      <span class="asof">${rows.length} names</span></h2><div class="body">
      <div class="tablewrap"><table class="sortable">
      <thead><tr><th data-sort="str">Asset</th><th data-sort="num" class="num">Price</th>
        <th data-sort="num" class="num">7D</th><th data-sort="num" class="num">30D</th>
        <th data-sort="num" class="num">90D</th><th data-sort="str">Regime</th>
        <th data-sort="str">Sector</th><th data-sort="num" class="num">Tier</th></tr></thead>
      <tbody>${rows.map((r: any) => `<tr>
        <td><a href="asset.html?s=${r.symbol}">${escapeHtml(r.symbol)}</a></td>
        <td class="num" data-v="${r.price ?? ''}">${price(r.price, '$')}</td>
        <td class="num ${dirClass(r.r7d)}" data-v="${r.r7d ?? ''}">${pct(r.r7d)}</td>
        <td class="num ${dirClass(r.r30d)}" data-v="${r.r30d ?? ''}">${pct(r.r30d)}</td>
        <td class="num ${dirClass(r.r90d)}" data-v="${r.r90d ?? ''}">${pct(r.r90d)}</td>
        <td data-v="${r.regime ?? ''}">${r.regime
          ? `<span class="regime ${r.regime}">${r.regime}</span>`
          : `<span class="na" title="${escapeHtml(r.regime_reason ?? '')}">no score</span>`}</td>
        <td>${escapeHtml(r.sector || '—')}</td>
        <td class="num" data-v="${r.tier ?? ''}">${r.tier ?? '—'}</td></tr>`).join('')}
      </tbody></table></div></div></section>`;
  }).join('');

  document.querySelectorAll('table.sortable').forEach((t) => sortableTable(t as HTMLTableElement));
}

void main();
