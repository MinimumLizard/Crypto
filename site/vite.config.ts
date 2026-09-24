import { defineConfig } from 'vite';
import { resolve } from 'path';
import { readdirSync } from 'fs';

// One HTML entry per route: a multi-page app, not a single-page one. The site
// is a set of documents that each load their own JSON, so there is no router,
// no hydration and no shared bundle to download before the first number appears.
const pagesDir = resolve(__dirname, 'pages');
const input: Record<string, string> = { main: resolve(__dirname, 'index.html') };
for (const file of readdirSync(pagesDir)) {
  if (file.endsWith('.html')) input[file.replace('.html', '')] = resolve(pagesDir, file);
}

export default defineConfig({
  // Served from https://<user>.github.io/Crypto/, so asset URLs need the prefix.
  base: process.env.SITE_BASE ?? '/Crypto/',
  build: { outDir: 'dist', emptyOutDir: true, rollupOptions: { input } },
});
