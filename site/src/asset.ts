/** Asset page: price, regime, levels and relative strength (SPEC §6.3). */

import './styles.css';
import { createChart, ColorType, LineStyle } from 'lightweight-charts';
import { mountFrame } from './lib/frame';
import { load, panelError } from './lib/data';
import { price, pct, dirClass, escapeHtml, staleness } from './lib/format';

const CHART_THEME = {
  layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#b5b2a6',
            fontFamily: "'JetBrains Mono', monospace", fontSize: 11 },
  grid: { vertLines: { color: '#2c2c27' }, horzLines: { color: '#2c2c27' } },
  rightPriceScale: { borderColor: '#35352e' },
  timeScale: { borderColor: '#35352e' },
  crosshair: { vertLine: { color: '#7d7a70' }, horzLine: { color: '#7d7a70' } },
};

function panel(title: string, asOf: string | null, body: string, howto?: string): string {
  const stale = asOf ? staleness(asOf) : null;
  return `<section class="panel"><h2>${escapeHtml(title)}
    ${stale ? `<span class="asof"><span class="badge ${stale.cls}"><i></i>${escapeHtml(stale.text)}</span></span>` : ''}
    </h2><div class="body">${howto ? `<p class="howto">${escapeHtml(howto)}</p>` : ''}${body}</div></section>`;
}

function crosscheck(check: any): string {
  if (!check?.available) return '';
  const rows = Object.entries(check.closes)
    .map(([venue, value]: [string, any]) => `${escapeHtml(venue)} ${price(value, '$')}`)
    .join(' · ');
  const cls = check.disagrees ? 'bad' : 'ok';
  return `<p class="howto ${check.disagrees ? 'caveat' : ''}">
    <span class="badge ${cls}"><i></i>venues agree to ${check.spread_pct}%</span>
    ${rows} <span style="color:var(--muted)">on ${escapeHtml(check.on_date)}</span>${
      check.excluded?.length ? ` <span style="color:var(--muted)">(excluded: ${
        escapeHtml(check.excluded.join(', '))} — history ends earlier)</span>` : ''}${check.disagrees
      ? ' — <strong>these disagree by more than 10%; one of them is wrong.</strong>'
      : ''}</p>`;
}

function regimePanel(regime: any): string {
  if (!regime?.available) {
    return `${panelError(regime?.reason ?? 'no score')}
      <p class="howto">The engine is defined on Binance daily bars because that is the
      series the TradingView charts it must match are drawn from. Scoring a different
      venue and presenting the result as comparable would be the dishonest option.</p>`;
  }
  const latest = regime.latest;
  const blocks = latest.blocks;
  const substitute = regime.parity === 'substitute';
  return `
    <div class="gauges">
      <div class="gauge"><div class="k">Regime</div>
        <div class="v"><span class="regime ${latest.regime}">${latest.regime}</span></div>
        <div class="n">${escapeHtml(latest.label)} · ${escapeHtml(regime.variant)}</div></div>
      <div class="gauge"><div class="k">Score</div>
        <div class="v">${latest.score ?? '—'}</div>
        <div class="bar"><span style="width:${Math.min(Math.abs(latest.score ?? 0), 100)}%"></span></div>
        <div class="n">clamped to ±100</div></div>
    </div>
    <div class="tablewrap" style="margin-top:10px"><table>
      <thead><tr><th>Block</th><th class="num">Value</th><th class="num">Clamp</th></tr></thead>
      <tbody>
        <tr><td>Price structure</td><td class="num">${blocks.structure ?? '—'}</td><td class="num">±35</td></tr>
        <tr><td>Trend</td><td class="num">${blocks.trend ?? '—'}</td><td class="num">±30</td></tr>
        <tr><td>Momentum</td><td class="num">${blocks.momentum ?? '—'}</td><td class="num">±20</td></tr>
        <tr><td>Volume</td><td class="num">${blocks.volume ?? '—'}</td><td class="num">±15</td></tr>
        <tr><td>Extension penalty</td><td class="num">${blocks.penalty ?? '—'}</td><td class="num">±50</td></tr>
      </tbody></table></div>
    <p class="howto caveat"><strong>${substitute ? 'Substitute venue.' : 'Parity unverified.'}</strong>
      ${escapeHtml(regime.parity_note ?? '')}</p>
    ${regime.cvd_note ? `<p class="howto">${escapeHtml(regime.cvd_note)}</p>` : ''}
    ${regime.no_measured_edge ? `<p class="howto caveat"><strong>No measured edge.</strong>
       The backtest found no edge for this name.</p>` : ''}
    ${regime.signals?.length ? `<div class="tablewrap" style="margin-top:10px"><table>
      <thead><tr><th>Recent signals</th><th class="num">Score</th></tr></thead>
      <tbody>${regime.signals.slice().reverse().map((s: any) => `
        <tr><td>${escapeHtml(s.date)} · <strong>${escapeHtml(s.signal)}</strong></td>
        <td class="num">${s.score}</td></tr>`).join('')}</tbody></table></div>` : ''}`;
}

function drawPrice(node: HTMLElement, ohlc: any[], levels: any): void {
  const chart = createChart(node, { ...CHART_THEME, height: node.clientHeight || 340 });
  const series = chart.addCandlestickSeries({
    upColor: '#4bb36b', downColor: '#d9584f', borderVisible: false,
    wickUpColor: '#4bb36b', wickDownColor: '#d9584f',
  });
  series.setData(ohlc.map((b) => ({ time: b.d, open: b.o, high: b.h, low: b.l, close: b.c })));

  // Key levels as horizontal references, each labelled, because a naked line
  // on a chart is not a number anyone can act on.
  const marks: [string, number | null, string][] = [
    ['200D', levels?.sma200d, '#f0a020'],
    ['50W', levels?.sma50w, '#4bb36b'],
    ['200W', levels?.sma200w, '#d9584f'],
  ];
  for (const [label, value, color] of marks) {
    if (typeof value === 'number') {
      series.createPriceLine({ price: value, color, lineWidth: 1,
        lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: label });
    }
  }
  chart.timeScale().fitContent();
  new ResizeObserver(() => chart.applyOptions({ width: node.clientWidth })).observe(node);
}

function drawScore(node: HTMLElement, history: any[]): void {
  const chart = createChart(node, { ...CHART_THEME, height: node.clientHeight || 200 });
  const line = chart.addLineSeries({ color: '#f0a020', lineWidth: 2 });
  line.setData(history.filter((h) => h.s !== null).map((h) => ({ time: h.d, value: h.s })));
  for (const level of [20, 10, -20]) {
    line.createPriceLine({ price: level, color: '#55554d', lineWidth: 1,
      lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: String(level) });
  }
  chart.timeScale().fitContent();
  new ResizeObserver(() => chart.applyOptions({ width: node.clientWidth })).observe(node);
}

async function main(): Promise<void> {
  const symbol = (new URLSearchParams(window.location.search).get('s') ?? 'BTC').toUpperCase();
  const index = await load<{ assets: string[] }>('index');
  await mountFrame('assets', index?.assets ?? []);
  const root = document.getElementById('main') as HTMLElement;

  const data = await load<any>(`assets/${symbol}`);
  if (!data) {
    root.innerHTML = panel(symbol, null, panelError(`no artefact for ${escapeHtml(symbol)}`));
    return;
  }

  const levels = data.levels ?? {};
  const returns = data.returns ?? {};
  const scoredOn = data.regime?.scored_on;
  const venueNote = (() => {
    const charted = `Charted on <strong>${escapeHtml(data.display_venue ?? '—')}</strong> bars`;
    if (!scoredOn) return `${charted}. No regime score on any venue.`;
    if (scoredOn === data.display_venue) {
      return `${charted}, which is also what the regime score uses.`;
    }
    return `${charted}, which have the longest history. The regime score uses
      <strong>${escapeHtml(scoredOn)}</strong> bars.`;
  })();

  root.innerHTML = [
    panel(`${escapeHtml(data.symbol)} · ${escapeHtml(data.name)}`, levels.as_of, `
      <div class="gauges">
        <div class="gauge"><div class="k">Price</div><div class="v">${price(levels.price, '$')}</div>
          <div class="n"><span class="${dirClass(returns.r1d)}">${pct(returns.r1d)}</span> today</div></div>
        <div class="gauge"><div class="k">7 / 30 / 90 day</div>
          <div class="v" style="font-size:16px">
            <span class="${dirClass(returns.r7d)}">${pct(returns.r7d)}</span> ·
            <span class="${dirClass(returns.r30d)}">${pct(returns.r30d)}</span> ·
            <span class="${dirClass(returns.r90d)}">${pct(returns.r90d)}</span></div>
          <div class="n">trailing returns</div></div>
        <div class="gauge"><div class="k">vs 200-day</div>
          <div class="v ${dirClass(levels.sma200d_distance_pct)}">${pct(levels.sma200d_distance_pct)}</div>
          <div class="n">200D at ${price(levels.sma200d, '$')}</div></div>
        <div class="gauge"><div class="k">Data</div>
          <div class="v" style="font-size:15px">${escapeHtml((data.venues ?? []).join(', ') || '—')}</div>
          <div class="n">${data.data_quality_grade === 'F'
            ? 'grade F — unmeasurable, venture tail' : 'venues with bars'}</div></div>
      </div>
      <p class="howto">${venueNote}</p>
      ${data.open_item ? `<p class="howto caveat"><strong>Open item:</strong> ${escapeHtml(data.open_item)}</p>` : ''}
      ${data.thesis ? `<p class="howto">${escapeHtml(data.thesis)}</p>` : ''}
      ${crosscheck(data.venue_crosscheck)}`),
    panel('Price', levels.as_of, '<div class="chart" id="pricechart"></div>',
      'Daily candles with the 200-day, 50-week and 200-week averages marked.'),
    `<div class="grid2">
      ${panel('Regime', data.regime?.latest?.date ?? null, regimePanel(data.regime),
        'A composite of price structure, trend, momentum and volume. Measured as a '
        + 'drawdown filter, not a source of alpha.')}
      ${panel('Key levels', levels.as_of, `<div class="tablewrap"><table>
        <thead><tr><th>Level</th><th class="num">Value</th></tr></thead><tbody>
        ${[['20-week SMA', levels.sma20w], ['21-week EMA', levels.ema21w],
           ['50-week SMA', levels.sma50w], ['200-week SMA', levels.sma200w],
           ['200-day SMA', levels.sma200d], ['50-day SMA', levels.sma50d],
           ['Prev week high', levels.prev_week_high], ['Prev week low', levels.prev_week_low],
          ].map(([l, v]) => `<tr><td>${l}</td><td class="num">${price(v as number, '$')}</td></tr>`).join('')}
        </tbody></table></div>`)}
    </div>`,
    data.regime?.available
      ? panel('Regime score history', data.regime.latest.date,
          '<div class="chart short" id="scorechart"></div>',
          'The composite score over time. Dotted lines mark the +20 / +10 / −20 '
          + 'hysteresis thresholds that move the regime.')
      : '',
  ].join('');

  if (data.ohlc?.length) drawPrice(document.getElementById('pricechart') as HTMLElement, data.ohlc, levels);
  if (data.regime?.available) {
    drawScore(document.getElementById('scorechart') as HTMLElement, data.regime.history);
  }
}

void main();
