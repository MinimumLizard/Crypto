/** Valuation: the equity lens (SPEC §6.4). */

import './styles.css';
import { mountFrame } from './lib/frame';
import { load, panelError } from './lib/data';
import { compact, dirClass, escapeHtml } from './lib/format';
import { sortableTable } from './lib/table';

type Cell = { v: number | null; reason: string | null };

interface Row {
  symbol: string; name: string; sector: string; tier: number | null;
  kind: string; is_chain: boolean; grade: string; grade_note: string;
  market_cap: number | null; fdv: number | null; price: number | null;
  circulating: number | null; total_supply: number | null;
  rank: number | null; volume_24h: number | null;
  metrics: Record<string, any>; flows: Record<string, any>; notes: string[];
  own_history: any;
}

interface Valuation {
  as_of: string; basis: string; bases: string[]; rows: Row[];
  sector_medians: Record<string, Record<string, number>>;
  grade_counts: Record<string, number>;
  grade_notes: Record<string, string>;
  how_to_read: string; caveat: string;
}

/** A metric cell: the number, or the reason there isn't one — never a zero. */
function cell(c: Cell | undefined, fmt: (n: number) => string): string {
  if (!c || c.v === null || c.v === undefined) {
    const why = c?.reason ?? 'not available';
    return `<td class="na" title="${escapeHtml(why)}">—</td>`;
  }
  return `<td class="num">${fmt(c.v)}</td>`;
}

function signedCell(c: Cell | undefined, fmt: (n: number) => string): string {
  if (!c || c.v === null || c.v === undefined) {
    return `<td class="na" title="${escapeHtml(c?.reason ?? 'not available')}">—</td>`;
  }
  return `<td class="num ${dirClass(c.v)}">${fmt(c.v)}</td>`;
}

const mult = (n: number) => `${n.toFixed(1)}×`;
const percent = (n: number) => `${n >= 0 ? '+' : ''}${(n * 100).toFixed(1)}%`;

const GRADE_CLASS: Record<string, string> = {
  A: 'ok', B: 'ok', C: 'warn', D: 'warn', F: 'bad',
};

function gradeBadge(grade: string, note: string): string {
  return `<span class="badge ${GRADE_CLASS[grade] ?? 'unknown'}"
    title="${escapeHtml(note)}"><i></i>${grade}</span>`;
}

function screen(data: Valuation): string {
  const rows = data.rows
    .filter((r) => r.market_cap)
    .sort((a, b) => (b.market_cap ?? 0) - (a.market_cap ?? 0));

  const body = rows.map((r) => {
    const m = r.metrics;
    const v = (k: string) => m[k] as Cell | undefined;
    return `<tr>
      <td><a href="asset.html?s=${r.symbol}">${escapeHtml(r.symbol)}</a>
        ${gradeBadge(r.grade, r.grade_note)}</td>
      <td class="num" data-v="${r.market_cap ?? ''}">$${compact(r.market_cap)}</td>
      ${cell(v('p_fees'), mult)}
      ${cell(v('p_revenue'), mult)}
      ${cell(v('p_holders_revenue'), mult)}
      ${signedCell(v('holder_yield'), percent)}
      ${signedCell(v('net_dilution_90d'), percent)}
      ${signedCell(v('net_holder_yield'), percent)}
      ${cell(v('float_pct'), (n) => `${n.toFixed(0)}%`)}
      ${cell(v('fdv_mc'), mult)}
      ${cell(v('mc_tvl'), mult)}
      ${signedCell(v('revenue_growth_90d'), percent)}
      <td>${escapeHtml(r.sector || '—')}</td>
    </tr>`;
  }).join('');

  // data-v on the sortable numeric columns so sorting reads the number, not
  // the formatted string (and unknowns sink rather than sorting as zero).
  const head = `<tr>
    <th data-sort="str">Asset</th>
    <th data-sort="num" class="num">Mkt cap</th>
    <th data-sort="num" class="num" title="Market cap ÷ annualised fees">P/Fees</th>
    <th data-sort="num" class="num" title="Market cap ÷ annualised revenue">P/Rev</th>
    <th data-sort="num" class="num" title="Market cap ÷ annualised holders' revenue">P/E</th>
    <th data-sort="num" class="num" title="Annualised holders' revenue ÷ market cap">Yield</th>
    <th data-sort="num" class="num" title="Annualised change in circulating supply, 90d">Dilution</th>
    <th data-sort="num" class="num" title="Holder yield minus dilution — the headline">Net yield</th>
    <th data-sort="num" class="num" title="Circulating ÷ total supply">Float</th>
    <th data-sort="num" class="num" title="Fully diluted valuation ÷ market cap">FDV/MC</th>
    <th data-sort="num" class="num" title="Market cap ÷ TVL — the P/B analogue">MC/TVL</th>
    <th data-sort="num" class="num" title="90 days against the prior 90">Rev growth</th>
    <th data-sort="str">Sector</th></tr>`;

  return `<div class="tablewrap"><table id="screen">
    <thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

/** Net holder yield, ranked. The one number the brief calls the headline. */
function headline(data: Valuation): string {
  const ranked = data.rows
    .map((r) => ({ r, v: r.metrics.net_holder_yield?.v as number | null }))
    .filter((x) => x.v !== null && x.v !== undefined)
    .sort((a, b) => (b.v as number) - (a.v as number));

  if (!ranked.length) return panelError('no name has both a yield and a dilution reading');

  const widest = Math.max(...ranked.map((x) => Math.abs(x.v as number)));
  const bar = (value: number) => {
    const share = (Math.abs(value) / widest) * 50;
    const colour = value >= 0 ? 'var(--up)' : 'var(--down)';
    const offset = value >= 0 ? 50 : 50 - share;
    return `<span style="position:relative;display:block;height:8px;background:var(--grid);border-radius:2px">
      <span style="position:absolute;left:${offset}%;width:${share}%;height:100%;background:${colour};border-radius:2px"></span>
      <span style="position:absolute;left:50%;top:-2px;width:1px;height:12px;background:var(--muted)"></span>
    </span>`;
  };

  return `<div class="tablewrap"><table>
    <thead><tr><th>Asset</th><th class="num">Holder yield</th>
      <th class="num">Dilution</th><th class="num">Net</th>
      <th style="width:34%">Bought back vs diluted</th></tr></thead>
    <tbody>${ranked.map(({ r, v }) => `<tr>
      <td><a href="asset.html?s=${r.symbol}">${escapeHtml(r.symbol)}</a>
        ${gradeBadge(r.grade, r.grade_note)}</td>
      ${signedCell(r.metrics.holder_yield, percent)}
      ${signedCell(r.metrics.net_dilution_90d, percent)}
      <td class="num ${dirClass(v)}"><strong>${percent(v as number)}</strong></td>
      <td>${bar(v as number)}</td></tr>`).join('')}
    </tbody></table></div>`;
}

/** Supply events inferred from circulating supply — the only unlock data free. */
function supplyEvents(data: Valuation): string {
  const events: { symbol: string; date: string; change: number; kind: string }[] = [];
  for (const r of data.rows) {
    for (const e of r.metrics.supply_quality?.events ?? []) {
      events.push({ symbol: r.symbol, date: e.date, change: e.change_pct, kind: e.kind });
    }
  }
  if (!events.length) return '<span class="missing">no discrete supply events detected</span>';
  events.sort((a, b) => (a.date < b.date ? 1 : -1));

  return `<div class="tablewrap"><table>
    <thead><tr><th>Date</th><th>Asset</th><th class="num">Supply change</th><th>Reading</th></tr></thead>
    <tbody>${events.slice(0, 18).map((e) => `<tr>
      <td>${escapeHtml(e.date)}</td>
      <td><a href="asset.html?s=${e.symbol}">${escapeHtml(e.symbol)}</a></td>
      <td class="num ${dirClass(-e.change)}">${e.change >= 0 ? '+' : ''}${e.change.toFixed(1)}%</td>
      <td>${escapeHtml(e.kind)}</td></tr>`).join('')}
    </tbody></table></div>`;
}

/** Reverse DCF: what growth the price already assumes. */
function reverseDcf(data: Valuation): string {
  const withGrid = data.rows.filter(
    (r) => Object.values(r.metrics.reverse_dcf ?? {}).some((c: any) => c?.v !== null));
  if (!withGrid.length) {
    return panelError('no name has positive holders’ revenue to solve against');
  }
  const cols = ['r15_x10', 'r15_x20', 'r25_x20', 'r35_x20', 'r35_x30'];
  const labels = ['15% / 10×', '15% / 20×', '25% / 20×', '35% / 20×', '35% / 30×'];

  return `<div class="tablewrap"><table>
    <thead><tr><th>Asset</th>${labels.map((l) => `<th class="num">${l}</th>`).join('')}
      <th class="num">Payback</th></tr></thead>
    <tbody>${withGrid.map((r) => `<tr>
      <td><a href="asset.html?s=${r.symbol}">${escapeHtml(r.symbol)}</a></td>
      ${cols.map((c) => cell(r.metrics.reverse_dcf?.[c], percent)).join('')}
      ${cell(r.metrics.payback_years, (n) => `${n.toFixed(0)}y`)}
    </tr>`).join('')}</tbody></table></div>
    <p class="howto">Each cell is the 5-year holders'-revenue CAGR that would justify
    today's market cap, at that discount rate and exit multiple. It says what the
    price already assumes, which is a more answerable question than what the asset
    is worth. Payback is years until cumulative holders' revenue equals market cap,
    with growth decaying 20% a year — extrapolating current growth for decades is an
    arithmetic accident, not a forecast.</p>`;
}

function unmeasurable(data: Valuation): string {
  const f = data.rows.filter((r) => r.grade === 'F');
  const d = data.rows.filter((r) => r.grade === 'D');
  return `
    <p class="howto"><strong>Grade F — unmeasurable (${f.length}):</strong>
      ${f.map((r) => escapeHtml(r.symbol)).join(', ') || 'none'}.
      No revenue data of any kind exists for these, so no multiple is shown.
      The brief holds them deliberately as a venture tail; a multiple here would
      be invented, not computed.</p>
    <p class="howto"><strong>Grade D — measured, capture is zero (${d.length}):</strong>
      ${d.map((r) => escapeHtml(r.symbol)).join(', ') || 'none'}.
      These have years of fee and revenue history and holders' revenue of exactly
      zero. That is a measurement, not a gap. Their P/E is undefined and their net
      holder yield is negative, because dilution is real.</p>`;
}

function panel(title: string, asOf: string | null, body: string, howto?: string): string {
  return `<section class="panel"><h2>${escapeHtml(title)}
    ${asOf ? `<span class="asof">${escapeHtml(asOf)}</span>` : ''}</h2>
    <div class="body">${howto ? `<p class="howto">${escapeHtml(howto)}</p>` : ''}${body}</div></section>`;
}

async function main(): Promise<void> {
  const index = await load<{ assets: string[] }>('index');
  await mountFrame('valuation', index?.assets ?? []);
  const root = document.getElementById('main') as HTMLElement;
  const data = await load<Valuation>('valuation');

  if (!data) {
    root.innerHTML = panel('Valuation', null, panelError('no valuation artefact — run `terminal build`'));
    return;
  }

  const stamp = data.as_of.slice(0, 10);
  const counts = Object.entries(data.grade_counts)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([g, n]) => `<div class="gauge"><div class="k">Grade ${escapeHtml(g)}</div>
      <div class="v">${n}</div>
      <div class="n">${escapeHtml(data.grade_notes[g] ?? '')}</div></div>`).join('');

  root.innerHTML = [
    panel(`Net holder yield — the headline · ${data.basis} annualised`, stamp,
      headline(data),
      'What reaches holders, minus the rate supply is growing. Positive means the '
      + 'token is bought back faster than it is diluted. It is the one number that '
      + 'cannot be improved by showing only the flattering half of the ledger.'),
    panel('The screen', stamp, screen(data), data.how_to_read),
    panel('Data quality', stamp,
      `<div class="gauges">${counts}</div>${unmeasurable(data)}`,
      'Every multiple carries its grade. Two of these grades mean very different '
      + 'things and are easy to confuse.'),
    panel('Observed supply events', stamp, supplyEvents(data),
      'Discrete one-day moves in circulating supply, inferred from the supply '
      + 'series. These are the only unlock data available: DefiLlama’s '
      + 'emissions endpoint is behind its paid tier.'),
    panel('Reverse DCF — what the price assumes', stamp, reverseDcf(data)),
    panel('Caveats', stamp, `<p class="howto caveat">${escapeHtml(data.caveat)}</p>`),
  ].join('');

  sortableTable(document.getElementById('screen') as HTMLTableElement);
}

void main();
