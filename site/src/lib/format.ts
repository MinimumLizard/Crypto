/**
 * Number formatting (SPEC §7.5).
 *
 * The rule that matters: prices are formatted to SIGNIFICANT DIGITS by
 * magnitude, never to a fixed two decimal places. A book holding tokens priced
 * at $0.0004 and $84,000 cannot use one decimal rule for both -- fixed 2dp
 * renders the first as "0.00", which is not a rounding error, it is a wrong
 * number that looks like a real one.
 *
 * Everything returns a string. `null` and `undefined` become an em-dash, never
 * "0" and never "-": a missing value must not be readable as a zero.
 */

export const MISSING = '—';

export function price(value: number | null | undefined, currency = ''): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return MISSING;
  const magnitude = Math.abs(value);
  let decimals: number;
  if (magnitude === 0) decimals = 2;
  else if (magnitude >= 1000) decimals = 0;
  else if (magnitude >= 100) decimals = 1;
  else if (magnitude >= 1) decimals = 2;
  else if (magnitude >= 0.01) decimals = 4;
  else if (magnitude >= 0.0001) decimals = 6;
  else decimals = 8;
  const text = value.toLocaleString('en-US', {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  });
  return currency ? `${currency}${text}` : text;
}

export function pct(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return MISSING;
  return `${value >= 0 ? '+' : ''}${value.toFixed(decimals)}%`;
}

/** 0-1 risk to two decimals. Distinct from pct: risk is not a percentage change. */
export function risk(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return MISSING;
  return value.toFixed(3);
}

export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return MISSING;
  const magnitude = Math.abs(value);
  if (magnitude >= 1e12) return `${(value / 1e12).toFixed(2)}T`;
  if (magnitude >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (magnitude >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (magnitude >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return value.toFixed(0);
}

/** Colour class for a signed number. Always paired with a sign by `pct`. */
export function dirClass(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'na';
  if (value > 0) return 'up';
  if (value < 0) return 'down';
  return 'flat';
}

/**
 * How stale is this, in plain words. Mirrors Nelson-Siegel's treatment.
 *
 * `cadence` matters and is easy to get wrong. A DAILY series whose newest bar
 * is yesterday's is not stale -- it is correct, because today's bar has not
 * closed and the engine scores confirmed bars only. Flagging that as "1d stale"
 * would train the eye to ignore the badge, which is the one thing a staleness
 * badge must not do. So a daily series is fresh up to two days old, and only
 * past that does it become a lag and then a fault.
 */
export function staleness(
  asOf: string | null | undefined,
  cadence: 'daily' | 'intraday' = 'daily',
): { text: string; cls: string } {
  if (!asOf) return { text: 'no date', cls: 'bad' };
  const then = new Date(asOf).getTime();
  if (Number.isNaN(then)) return { text: asOf, cls: 'unknown' };
  const days = (Date.now() - then) / 864e5;
  const fresh = cadence === 'daily' ? 2 : 0.25;
  if (days < fresh) return { text: asOf.slice(0, 10), cls: 'ok' };
  const whole = Math.floor(days);
  // Past a few days this stops being a publishing lag and starts being a fault,
  // so it is worded as one rather than shown as though it were current.
  return {
    text: `${asOf.slice(0, 10)} · ${whole}d stale`,
    cls: whole > 5 ? 'bad' : 'warn',
  };
}

export function el(html: string): HTMLElement {
  const template = document.createElement('template');
  template.innerHTML = html.trim();
  return template.content.firstElementChild as HTMLElement;
}

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string));
}
