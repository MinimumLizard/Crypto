/**
 * Loading the per-page JSON the pipeline built.
 *
 * Each page fetches only its own artefact, so the home page does not download
 * 26 asset files to show a grid. A failed load renders a stated reason rather
 * than an empty page -- a blank panel and a broken one look identical, and only
 * one of them is honest.
 */

const BASE = (import.meta.env.BASE_URL ?? '/').replace(/\/$/, '');

export async function load<T>(name: string): Promise<T | null> {
  try {
    const response = await fetch(`${BASE}/data/${name}.json`, { cache: 'no-cache' });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export function panelError(message: string): string {
  return `<span class="missing">source unavailable: ${message}</span>`;
}
