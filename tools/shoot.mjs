// Screenshot every route at 1440px and 390px (SPEC §0.6).
// Fails loudly on a console error or a failed request: a page that renders but
// threw is not a page that works, and the whole point of this step is to catch
// exactly that before claiming a phase is done.
import { chromium } from 'playwright';
import { mkdirSync, existsSync } from 'fs';

const BASE = process.env.PREVIEW ?? 'http://127.0.0.1:4173/Crypto';
const OUT = 'screenshots';
mkdirSync(OUT, { recursive: true });

const ROUTES = [
  ['home', '/'],
  ['btc-cycle', '/pages/btc-cycle.html'],
  ['assets', '/pages/assets.html'],
  ['asset-BTC', '/pages/asset.html?s=BTC'],
  ['asset-HYPE', '/pages/asset.html?s=HYPE'],
  ['asset-SOL', '/pages/asset.html?s=SOL'],
  ['source-health', '/pages/source-health.html'],
  ['valuation', '/pages/valuation.html'],
  ['macro-stub', '/pages/macro.html'],
];
const SIZES = [['desktop', 1440, 1200], ['mobile', 390, 900]];

// This container ships Chromium at a fixed path; the npm package's pinned
// revision may not match, and downloading one is blocked. Use what is here.
const EXECUTABLE = process.env.CHROMIUM_PATH ?? '/opt/pw-browsers/chromium/chrome-linux/chrome';
const browser = await chromium.launch(
  existsSync(EXECUTABLE) ? { executablePath: EXECUTABLE } : {});
let problems = 0;

for (const [label, width, height] of SIZES) {
  const context = await browser.newContext({
    viewport: { width, height }, deviceScaleFactor: label === 'mobile' ? 2 : 1,
    // Only when shooting a real https:// origin: this container routes outbound
    // TLS through a proxy whose CA Chromium does not carry, so every external
    // navigation fails with ERR_CERT_AUTHORITY_INVALID. That is an artefact of
    // the sandbox, not of the site. Local preview over http is unaffected and
    // keeps strict checking.
    ignoreHTTPSErrors: BASE.startsWith('https://'),
  });
  for (const [name, path] of ROUTES) {
    const page = await context.newPage();
    const errors = [];
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
    page.on('requestfailed', (r) => errors.push(`requestfailed: ${r.url()}`));

    await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle', timeout: 45000 });
    await page.waitForTimeout(900);

    // A horizontal scrollbar at 390px is the mobile failure that screenshots
    // hide, because the capture is as wide as the content.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth);

    await page.screenshot({ path: `${OUT}/${label}-${name}.png`, fullPage: true });

    // Google Fonts is blocked by this container's TLS proxy, not by anything on
    // the page, and the stylesheet has a system-font fallback. Filtered so a
    // real error is not lost in eighteen copies of the same environment quirk.
    const realErrors = errors.filter(
      (e) => !/favicon|fonts\.gstatic|fonts\.googleapis|ERR_CERT_AUTHORITY_INVALID/.test(e));
    const flag = realErrors.length || (label === 'mobile' && overflow > 2);
    if (flag) problems++;
    console.log(`${flag ? 'PROBLEM' : 'ok     '} ${label}/${name}`
      + (overflow > 2 ? `  overflow=${overflow}px` : '')
      + (realErrors.length ? `  errors: ${realErrors.slice(0, 2).join(' | ')}` : ''));
    await page.close();
  }
  await context.close();
}
await browser.close();
console.log(problems ? `\n${problems} page(s) need attention` : '\nall pages clean');
