/**
 * The global frame every page shares (SPEC §6.1): nav, clocks, ticker,
 * command palette, footer.
 *
 * Built in TypeScript rather than duplicated into every HTML file, so adding a
 * route or changing the disclaimer happens once.
 */

import { load } from './data';
import { price, pct, dirClass, escapeHtml, el } from './format';

export const ROUTES: { path: string; label: string; built: boolean }[] = [
  { path: '', label: 'Home', built: true },
  { path: 'btc-cycle', label: 'BTC cycle', built: true },
  { path: 'assets', label: 'Assets', built: true },
  { path: 'valuation', label: 'Valuation', built: true },
  { path: 'breadth', label: 'Breadth', built: true },
  { path: 'sectors', label: 'Sectors', built: true },
  { path: 'derivatives', label: 'Derivatives', built: true },
  { path: 'macro', label: 'Macro', built: false },
  { path: 'geopolitics', label: 'Geopolitics', built: false },
  { path: 'portfolio', label: 'Portfolio', built: false },
  { path: 'radar', label: 'Radar', built: false },
  { path: 'source-health', label: 'Source health', built: true },
];

const BASE = (import.meta.env.BASE_URL ?? '/').replace(/\/$/, '');

export function href(path: string): string {
  if (path === '') return `${BASE}/`;
  return `${BASE}/pages/${path}.html`;
}

/** UTC plus the two zones the owner works between (§6.1). */
const CLOCK_ZONES: [string, string][] = [
  ['UTC', 'UTC'],
  ['MEL', 'Australia/Melbourne'],
  ['CMB', 'Asia/Colombo'],
];

function renderClocks(node: HTMLElement): void {
  const tick = () => {
    node.innerHTML = CLOCK_ZONES.map(([label, zone]) => {
      const time = new Intl.DateTimeFormat('en-GB', {
        hour: '2-digit', minute: '2-digit', timeZone: zone, hour12: false,
      }).format(new Date());
      return `<span><b>${label}</b> ${time}</span>`;
    }).join('');
  };
  tick();
  setInterval(tick, 30_000);
}

interface HomeArtefact {
  as_of: string;
  grid: { symbol: string; price: number | null; r1d: number | null }[];
  btc_levels?: { price?: number };
}

async function renderTicker(node: HTMLElement): Promise<void> {
  const home = await load<HomeArtefact>('home');
  if (!home) {
    node.innerHTML = '<div class="tick"><span class="k">ticker</span>'
      + '<span class="v na">no build data</span></div>';
    return;
  }
  // Only names we actually hold prices for. §0.2: no placeholder numbers.
  const wanted = ['BTC', 'ETH', 'SOL', 'HYPE', 'ZEC', 'LINK'];
  const rows = wanted
    .map((symbol) => home.grid.find((g) => g.symbol === symbol))
    .filter((row): row is NonNullable<typeof row> => !!row && row.price !== null);

  node.innerHTML = rows.map((row) => `
    <div class="tick">
      <span class="k">${escapeHtml(row.symbol)}</span>
      <span class="v">${price(row.price, '$')}
        <span class="${dirClass(row.r1d)}">${pct(row.r1d)}</span></span>
    </div>`).join('')
    + `<div class="tick"><span class="k">snapshot</span>
         <span class="v">${escapeHtml(home.as_of.slice(0, 16).replace('T', ' '))}Z</span></div>`;
}

/** Command palette: `/` to open, type a ticker or a page name (§6.1). */
function wirePalette(symbols: string[]): void {
  let open = false;
  let backdrop: HTMLElement | null = null;

  const close = () => { backdrop?.remove(); backdrop = null; open = false; };

  const show = () => {
    if (open) return;
    open = true;
    const items = [
      ...ROUTES.map((r) => ({
        label: r.label,
        hint: r.built ? 'page' : 'not built yet',
        url: href(r.path),
      })),
      ...symbols.map((s) => ({ label: s, hint: 'asset', url: `${href('asset')}?s=${s}` })),
    ];
    backdrop = el(`<div class="palette-backdrop" role="dialog" aria-modal="true"
        aria-label="Command palette"><div class="palette">
        <input type="text" placeholder="Type a ticker or a page…" aria-label="Search" />
        <ul role="listbox"></ul></div></div>`);
    document.body.appendChild(backdrop);
    const input = backdrop.querySelector('input') as HTMLInputElement;
    const list = backdrop.querySelector('ul') as HTMLUListElement;
    let selected = 0;
    let shown = items;

    const draw = () => {
      const query = input.value.trim().toLowerCase();
      shown = query
        ? items.filter((i) => i.label.toLowerCase().includes(query)).slice(0, 40)
        : items.slice(0, 40);
      selected = Math.min(selected, Math.max(shown.length - 1, 0));
      list.innerHTML = shown.map((item, index) => `
        <li role="option" aria-selected="${index === selected}" data-url="${item.url}">
          <span>${escapeHtml(item.label)}</span>
          <span class="hint">${escapeHtml(item.hint)}</span></li>`).join('')
        || '<li class="hint">no match</li>';
    };

    const go = () => { if (shown[selected]) window.location.href = shown[selected].url; };

    input.addEventListener('input', draw);
    input.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown') { selected = Math.min(selected + 1, shown.length - 1); draw(); event.preventDefault(); }
      else if (event.key === 'ArrowUp') { selected = Math.max(selected - 1, 0); draw(); event.preventDefault(); }
      else if (event.key === 'Enter') { go(); }
      else if (event.key === 'Escape') { close(); }
    });
    list.addEventListener('click', (event) => {
      const li = (event.target as HTMLElement).closest('li');
      if (li?.dataset.url) window.location.href = li.dataset.url;
    });
    backdrop.addEventListener('click', (event) => { if (event.target === backdrop) close(); });
    draw();
    input.focus();
  };

  document.addEventListener('keydown', (event) => {
    const target = event.target as HTMLElement;
    const typing = ['INPUT', 'TEXTAREA'].includes(target.tagName);
    if (event.key === '/' && !typing) { event.preventDefault(); show(); }
    if (event.key === 'Escape' && open) close();
  });
}

export async function mountFrame(current: string, symbols: string[] = []): Promise<void> {
  const header = document.querySelector('header.top');
  if (header) {
    header.innerHTML = `
      <div class="wrap">
        <div class="topbar">
          <a class="brand" href="${href('')}">Mini<span>Lizard</span> terminal</a>
          <div class="clocks" id="clocks"></div>
          <nav class="routes">
            ${ROUTES.map((r) => `<a href="${href(r.path)}"
                ${r.path === current ? 'aria-current="page"' : ''}
                ${r.built ? '' : 'style="opacity:.5"'}>${r.label}</a>`).join('')}
          </nav>
        </div>
        <div class="ticker" id="ticker"></div>
      </div>`;
    renderClocks(document.getElementById('clocks') as HTMLElement);
    void renderTicker(document.getElementById('ticker') as HTMLElement);
  }

  const footer = document.querySelector('footer.site');
  if (footer) {
    footer.innerHTML = `
      <div class="wrap">
        <p class="disclaimer"><strong>This is a research tool, not investment
        advice.</strong> It is read-only: it places no orders, holds no exchange
        keys with trading rights and connects to no wallet. Every number is
        derived from public data and may be wrong, stale or misunderstood.
        Nothing here is a recommendation to buy or sell anything.</p>
        <p>Data: <a href="https://www.coingecko.com" rel="noopener">CoinGecko</a> ·
        <a href="https://defillama.com" rel="noopener">DefiLlama</a> ·
        <a href="https://coinmetrics.io/community-network-data/" rel="noopener">CoinMetrics
        Community</a> · <a href="https://fred.stlouisfed.org" rel="noopener">FRED</a> ·
        Binance · Coinbase · Hyperliquid · alternative.me · Wikimedia.
        Charts by <a href="https://www.tradingview.com/lightweight-charts/"
        rel="noopener">TradingView Lightweight Charts</a> (Apache-2.0).</p>
        <p>Press <kbd>/</kbd> for the command palette.
        <span id="buildstamp"></span></p>
      </div>`;
    void load<{ built_at: string }>('index').then((index) => {
      const stamp = document.getElementById('buildstamp');
      if (stamp && index) {
        stamp.textContent = ` · built ${index.built_at.replace('T', ' ')}Z`;
      }
    });
  }

  wirePalette(symbols);
}
