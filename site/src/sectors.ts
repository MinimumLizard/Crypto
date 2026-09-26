/** Sector rotation (SPEC §6.8) and the RRG (§7.4). */

import './styles.css';
import { mountFrame } from './lib/frame';
import { load, panelError } from './lib/data';
import { compact, escapeHtml, dirClass } from './lib/format';
import { sortableTable } from './lib/table';

const pct1 = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`;

// Divergence is one percentage return minus another, so it is a difference in
// percentage POINTS. Printing it with a % sign invites reading Privacy's +282.8
// as a return, which it is not.
const pts1 = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(1)} pts`;

const QUADRANT_COLOUR: Record<string, string> = {
  leading: 'var(--up)',
  improving: 'var(--accent)',
  weakening: 'var(--warn)',
  lagging: 'var(--down)',
};

function panel(title: string, asOf: string | null, body: string, howto?: string): string {
  return `<section class="panel"><h2>${escapeHtml(title)}
    ${asOf ? `<span class="asof">${escapeHtml(asOf)}</span>` : ''}</h2>
    <div class="body">${howto ? `<p class="howto">${escapeHtml(howto)}</p>` : ''}${body}</div></section>`;
}

/**
 * The RRG, as inline SVG.
 *
 * Both axes are centred on 100 and the four quadrants are the reading, so the
 * crosshair and the quadrant labels are the chart — not decoration. Each sector
 * is drawn as a tail so rotation is visible as a path rather than a point: a
 * sector moving clockwise from improving into leading is the thing this page
 * exists to show.
 */
function rrgChart(rrg: any): string {
  if (!rrg?.available) return panelError(rrg?.reason ?? 'not computable');

  const all = rrg.points.flatMap((p: any) => p.tail);
  const xs = all.map((t: any) => t.x);
  const ys = all.map((t: any) => t.y);
  // Symmetric bounds around 100, so the crosshair sits in the middle and a
  // point's distance from centre is comparable on both axes.
  const reach = Math.max(
    ...xs.map((v: number) => Math.abs(v - 100)),
    ...ys.map((v: number) => Math.abs(v - 100)), 0.5) * 1.25;
  const lo = 100 - reach;
  const hi = 100 + reach;
  const sx = (v: number) => ((v - lo) / (hi - lo)) * 100;
  const sy = (v: number) => 100 - ((v - lo) / (hi - lo)) * 100;

  // The tail is drawn as separate segments that brighten toward the head, so
  // direction of travel is readable without an arrowhead. Ten overlapping
  // equal-weight polylines were a tangle: which end was "now" was invisible.
  // Heads first, so labels can be de-overlapped before anything is drawn.
  // Sectors cluster: the five leading ones sat inside 1.5 RS-Ratio units of each
  // other and their labels printed on top of one another, which made the most
  // important corner of the chart the least readable one.
  const LABEL_GAP = 3.1;
  interface Head { p: any; hx: number; hy: number; flip: boolean; ly: number }
  const heads: Head[] = rrg.points.map((p: any): Head => {
    const head = p.tail[p.tail.length - 1];
    const hx = sx(head.x);
    const hy = sy(head.y);
    return { p, hx, hy, flip: hx > 70, ly: hy + 0.9 };
  });
  for (const side of [true, false]) {
    const group = heads.filter((h) => h.flip === side).sort((a, b) => a.ly - b.ly);
    // Push down to open a gap, then, if that ran off the bottom, push the whole
    // stack back up. Two passes settle it because the gap is uniform.
    for (let i = 1; i < group.length; i++) {
      group[i].ly = Math.max(group[i].ly, group[i - 1].ly + LABEL_GAP);
    }
    const overflow = group.length ? group[group.length - 1].ly - 98 : 0;
    if (overflow > 0) {
      for (const h of group) h.ly -= overflow;
      for (let i = group.length - 2; i >= 0; i--) {
        group[i].ly = Math.min(group[i].ly, group[i + 1].ly - LABEL_GAP);
      }
    }
  }

  const tails = heads.map(({ p, hx, hy, flip, ly }: Head) => {
    const colour = QUADRANT_COLOUR[p.quadrant] ?? 'var(--ink-2)';
    const segments = p.tail.slice(1).map((t: any, i: number) => {
      const from = p.tail[i];
      const progress = (i + 1) / Math.max(p.tail.length - 1, 1);
      return `<line x1="${sx(from.x).toFixed(2)}" y1="${sy(from.y).toFixed(2)}"
        x2="${sx(t.x).toFixed(2)}" y2="${sy(t.y).toFixed(2)}" stroke="${colour}"
        stroke-width="${(0.4 + progress * 0.9).toFixed(2)}"
        opacity="${(0.15 + progress * 0.6).toFixed(2)}"
        vector-effect="non-scaling-stroke" stroke-linecap="round"/>`;
    }).join('');
    const lx = hx + (flip ? -2.4 : 2.4);
    // A displaced label needs a leader back to its dot or it becomes ambiguous
    // which sector it names — worse than the overlap it was moved to avoid.
    const leader = Math.abs(ly - (hy + 0.9)) > 0.6
      ? `<line x1="${hx.toFixed(2)}" y1="${hy.toFixed(2)}" x2="${lx.toFixed(2)}"
          y2="${(ly - 0.8).toFixed(2)}" stroke="${colour}" stroke-width="0.25"
          opacity="0.45" vector-effect="non-scaling-stroke"/>`
      : '';
    return `${segments}${leader}
      <circle cx="${hx.toFixed(2)}" cy="${hy.toFixed(2)}" r="1.5"
        fill="${colour}" stroke="var(--surface)" stroke-width="0.4">
        <title>${escapeHtml(p.name)} — ${escapeHtml(p.quadrant)} (RS-Ratio ${p.x}, RS-Momentum ${p.y})</title></circle>
      <text x="${lx.toFixed(2)}" y="${ly.toFixed(2)}"
        text-anchor="${flip ? 'end' : 'start'}"
        fill="var(--ink)" font-size="2.5"
        font-family="JetBrains Mono, monospace">${escapeHtml(p.name)}</text>`;
  }).join('');

  const centreX = sx(100);
  const centreY = sy(100);
  return `<svg viewBox="0 0 100 100" class="chart" style="height:440px" role="img"
      aria-label="Relative rotation graph of sectors against ${escapeHtml(rrg.benchmark)}">
      <rect x="${centreX}" y="0" width="${100 - centreX}" height="${centreY}"
        fill="var(--up)" opacity="0.045"/>
      <rect x="0" y="${centreY}" width="${centreX}" height="${100 - centreY}"
        fill="var(--down)" opacity="0.045"/>
      <line x1="${centreX}" y1="0" x2="${centreX}" y2="100"
        stroke="var(--axis)" stroke-width="0.4" vector-effect="non-scaling-stroke"/>
      <line x1="0" y1="${centreY}" x2="100" y2="${centreY}"
        stroke="var(--axis)" stroke-width="0.4" vector-effect="non-scaling-stroke"/>
      <text x="98" y="3.5" text-anchor="end" fill="var(--up)" font-size="3">leading</text>
      <text x="2" y="3.5" fill="var(--accent)" font-size="3">improving</text>
      <text x="2" y="98" fill="var(--down)" font-size="3">lagging</text>
      <text x="98" y="98" text-anchor="end" fill="var(--warn)" font-size="3">weakening</text>
      ${tails}
    </svg>
    <p class="howto">Horizontal is relative strength against
      ${escapeHtml(rrg.benchmark)}; vertical is whether that strength is building.
      Each trail is the last ${rrg.tail_weeks} weeks, so rotation reads as a path:
      a sector moving from improving into leading is the setup this page exists
      to surface.</p>
    <p class="howto caveat">${escapeHtml(rrg.method_note)}</p>`;
}

function indicesTable(indices: Record<string, any>, feeGrowth: any[]): string {
  const fees = new Map(feeGrowth.map((f) => [f.sector, f]));
  const rows = Object.entries(indices).map(([sector, v]: [string, any]) => {
    if (!v.available) {
      return `<tr><td>${escapeHtml(sector)}</td>
        <td colspan="8" class="na">${escapeHtml(v.reason)}</td></tr>`;
    }
    const f = fees.get(sector);
    const divergence = v.cap_return_pct - v.equal_return_pct;
    // The since-start columns are measured over DIFFERENT windows and must never
    // be ranked against each other without the window on the row. 30d and 90d are
    // the comparable pair. days comes from the pipeline: the plotted series is
    // thinned, so its length is a point count and would halve every window.
    const days = v.days;
    return `<tr>
      <td>${escapeHtml(sector)}
        <span class="badge" title="${escapeHtml(v.included.join(', '))}"><i></i>${v.included.length}</span></td>
      <td class="num dim" data-v="${days ?? ''}"
        title="index starts ${escapeHtml(v.start)} &mdash; ${escapeHtml(v.start_reason ?? '')}"
        >${days ? `${days}d` : '—'}${v.start_capped ? '<span class="badge" title="this index is cut off by the window cap, not by its members\u2019 history">cap</span>' : ''}</td>
      <td class="num ${dirClass(v.equal_return_pct)}" data-v="${v.equal_return_pct}">${pct1(v.equal_return_pct)}</td>
      <td class="num ${dirClass(v.cap_return_pct)}" data-v="${v.cap_return_pct}">${pct1(v.cap_return_pct)}</td>
      <td class="num ${dirClass(divergence)}" data-v="${divergence}"
        title="cap-weight minus equal-weight, in percentage points: positive means larger names carried the sector">${pts1(divergence)}</td>
      <td class="num ${dirClass(v.return_30d_pct)}" data-v="${v.return_30d_pct ?? ''}">${pct1(v.return_30d_pct)}</td>
      <td class="num ${dirClass(v.return_90d_pct)}" data-v="${v.return_90d_pct ?? ''}">${pct1(v.return_90d_pct)}</td>
      <td class="num ${f?.available ? dirClass(f.growth_pct) : 'na'}" data-v="${f?.growth_pct ?? ''}">
        ${f?.available ? pct1(f.growth_pct) : '—'}</td>
      <td class="num">${f?.available ? `$${compact(f.fees_90d)}` : '—'}</td>
    </tr>`;
  }).join('');

  return `<div class="tablewrap"><table id="sectors"><thead><tr>
    <th data-sort="str">Sector</th>
    <th data-sort="num" class="num" title="Length of this sector's index; they differ">Window</th>
    <th data-sort="num" class="num" title="Average member, rebased to 100, over this sector's own window">Equal wt, since start</th>
    <th data-sort="num" class="num" title="Weighted by market cap, over this sector's own window">Cap wt, since start</th>
    <th data-sort="num" class="num">Divergence</th>
    <th data-sort="num" class="num">30d</th>
    <th data-sort="num" class="num">90d</th>
    <th data-sort="num" class="num" title="90 days of fees against the prior 90">Fee growth</th>
    <th class="num">Fees 90d</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="howto">Equal-weight says what the average name in the sector did;
    cap-weight says what the sector's money did. A large positive divergence means
    one big name carried it, which is the opposite of the broad participation a
    rotation needs. Both indices start at the first date every included member has
    a price, so an index never jumps because a constituent appeared &mdash; or at
    the window cap, marked <span class="badge">cap</span>, where that history runs
    back further than the chart is drawn.</p>
    <p class="howto caveat">That start date differs by sector, so the two
    since-start columns are NOT comparable across rows and the window is on each
    row for that reason. Rank sectors on 30d or 90d, which are measured over the
    same period for every row, or on a sector against its own history.</p>`;
}

function quadrantSummary(rrg: any): string {
  if (!rrg?.available) return '';
  const groups: Record<string, string[]> = {};
  for (const p of rrg.points) (groups[p.quadrant] ??= []).push(p.name);
  const order = ['leading', 'improving', 'weakening', 'lagging'];
  return `<div class="gauges">${order.map((q) => `
    <div class="gauge"><div class="k" style="color:${QUADRANT_COLOUR[q]}">${q}</div>
      <div class="v">${(groups[q] ?? []).length}</div>
      <div class="n">${escapeHtml((groups[q] ?? []).join(', ') || 'none')}</div></div>`).join('')}
  </div>`;
}

async function main(): Promise<void> {
  const index = await load<{ assets: string[] }>('index');
  await mountFrame('sectors', index?.assets ?? []);
  const root = document.getElementById('main') as HTMLElement;
  const s = await load<any>('sectors');
  if (!s) {
    root.innerHTML = panel('Sectors', null, panelError('no sectors artefact'));
    return;
  }
  const stamp = s.as_of.slice(0, 10);

  root.innerHTML = [
    panel('Where sectors are rotating', stamp,
      quadrantSummary(s.rrg) + rrgChart(s.rrg), s.how_to_read),
    panel('Sector indices and fee growth', stamp, indicesTable(s.indices, s.fee_growth)),
  ].join('');

  sortableTable(document.getElementById('sectors') as HTMLTableElement);
}

void main();
