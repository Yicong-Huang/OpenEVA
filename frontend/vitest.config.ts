import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import fs from 'node:fs'

// Plugin frontend code lives in per-plugin folders outside `src/`:
//   `core/src/plugins/<id>/PluginName.tsx`        OSS plugins
//   `<extension>/src/<id>/PluginName.tsx`         extension plugins
// Vitest needs the same `resolve.alias` Vite uses at runtime so test
// imports (`import { MyPlugin } from '@<extension>/<plugin>/MyPlugin'`)
// resolve to those files. Vitest does NOT pick aliases up from
// vite.config.ts automatically when both files exist -- the explicit
// vitest.config.ts wins -- so we duplicate the small bit here.
const repoRoot = path.resolve(__dirname, '..')
const corePlugins = path.join(repoRoot, 'core', 'src', 'plugins')
const frontendSrc = path.join(__dirname, 'src')

// Mirror vite.config.ts's extension discovery so test runs see the
// same `@<id>` aliases as `vite build` / `vite dev`.
type Ext = { id: string; src: string }
function discoverExtensions(): Ext[] {
  const out: Ext[] = []
  for (const name of fs.readdirSync(repoRoot)) {
    if (name === 'core' || name.startsWith('.') || name.startsWith('_')) continue
    const dir = path.join(repoRoot, name)
    if (!fs.statSync(dir).isDirectory()) continue
    const marker = path.join(dir, 'extension.conf')
    if (!fs.existsSync(marker)) continue
    let id = name
    try {
      const m = fs.readFileSync(marker, 'utf8').match(/^id:\s*([A-Za-z0-9_\-]+)/m)
      if (m) id = m[1].trim()
    } catch { /* keep folder-name fallback */ }
    out.push({ id, src: path.join(dir, 'src') })
  }
  return out
}
const extensions = discoverExtensions()

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@core/plugins': corePlugins,
      '@app': frontendSrc,
      ...Object.fromEntries(extensions.map(e => [`@${e.id}`, e.src])),
    },
  },
  // Vitest's underlying Vite dev server enforces `fs.allow`. The
  // default whitelist is just `frontend/`, so tests living under
  // `<extension>/test/` fail to import. Widen the list so each
  // extension's source + test trees both resolve.
  server: {
    fs: {
      allow: [
        __dirname,
        corePlugins,
        ...extensions.flatMap(e => [
          e.src,
          path.join(e.src, '..', 'test'),
        ]),
      ],
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test-setup.ts',
    css: true,
    // Pick up tests from `frontend/src/` (core/OSS tests) AND from
    // every extension's `test/**` so per-plugin frontend tests can
    // live alongside their implementation under
    // `<extension>/test/<plugin>/`. Vitest's default `include` only
    // looks under `frontend/`, so the explicit list is necessary
    // for extension trees to be picked up.
    include: [
      'src/**/*.{test,spec}.{ts,tsx}',
      ...extensions.map(e => path.join(e.src, '..', 'test', '**/*.{test,spec}.{ts,tsx}')),
    ],
    exclude: ['e2e/**', 'node_modules/**', 'frontend/e2e/**', '**/__pycache__/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'text-summary', 'lcov'],
      reportsDirectory: './coverage',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/test-setup.ts', 'src/**/*.test.{ts,tsx}', 'src/__tests__/**'],
    },
  },
})
