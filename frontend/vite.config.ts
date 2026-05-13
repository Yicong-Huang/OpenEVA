import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import fs from 'node:fs'

// Resolve to absolute paths so the `fs.allow` whitelist + `resolve.alias`
// agree with one another regardless of where Vite is launched from.
const repoRoot = path.resolve(__dirname, '..')
const corePlugins = path.join(repoRoot, 'core', 'src', 'plugins')
const frontendSrc = path.join(__dirname, 'src')

// `@app/*` resolves into `frontend/src/*`. Plugin .tsx files (which
// live OUTSIDE `frontend/src/`) use this to reach shared frontend
// utilities like `@app/api`, `@app/hooks/usePluginCollapse` instead
// of brittle `../../api` paths.

// Mirror `core.extensions.discover()` from the backend: any
// sibling-of-core folder with an `extension.conf` marker is an
// extension. Each gets an `@<id>` alias pointing at its `src/`.
// `core/` itself is excluded by convention. Dynamic discovery
// means adding a new extension (e.g. `acme/`) is a matter of
// dropping a folder + marker -- no Vite config edit.
type Ext = { id: string; src: string }
function discoverExtensions(): Ext[] {
  const out: Ext[] = []
  for (const name of fs.readdirSync(repoRoot)) {
    if (name === 'core' || name.startsWith('.') || name.startsWith('_')) continue
    const dir = path.join(repoRoot, name)
    if (!fs.statSync(dir).isDirectory()) continue
    const marker = path.join(dir, 'extension.conf')
    if (!fs.existsSync(marker)) continue
    // Minimal yaml parse for `id:` line; avoids pulling a yaml dep
    // into the Vite config. Falls back to the folder name when the
    // marker is empty or malformed.
    let id = name
    try {
      const txt = fs.readFileSync(marker, 'utf8')
      const m = txt.match(/^id:\s*([A-Za-z0-9_\-]+)/m)
      if (m) id = m[1].trim()
    } catch { /* keep folder-name fallback */ }
    out.push({ id, src: path.join(dir, 'src') })
  }
  return out
}
const extensions = discoverExtensions()

export default defineConfig({
  plugins: [react()],
  base: '/',
  // Plugin frontends now live alongside their backend Python in the
  // matching per-plugin folder (`core/src/plugins/<id>/PluginName.tsx`
  // for OSS, `<extension>/src/<id>/PluginName.tsx` for extensions).
  // Vite by default refuses to serve files outside the project root
  // and doesn't know about non-relative imports -- two adjustments
  // make the cross-tree imports work:
  //   `resolve.alias`   tells the dev server + bundler what
  //                     `@core/plugins/...` and `@<extension>/...`
  //                     mean on disk.
  //   `server.fs.allow` widens the dev-server filesystem allowlist
  //                     (production `vite build` doesn't need this
  //                     but keeping both branches symmetric avoids
  //                     surprises when devs flip between `npm run dev`
  //                     and `npm run build`).
  resolve: {
    // Force every cross-tree consumer (plugin .tsx files under
    // `core/src/plugins/`, extension .tsx files under `<ext>/src/`)
    // to resolve `react` / `react-dom` to the SAME copy installed in
    // `frontend/node_modules`. Vite 8 / Rolldown otherwise walks the
    // importer's parent chain looking for `node_modules/react` and
    // fails because the importer lives outside `frontend/`. Mapping
    // the bare specifier here also eliminates the "duplicate React"
    // class of bugs (separate copies break hooks).
    alias: {
      react: path.join(__dirname, 'node_modules', 'react'),
      'react-dom': path.join(__dirname, 'node_modules', 'react-dom'),
      'react/jsx-runtime': path.join(
        __dirname, 'node_modules', 'react', 'jsx-runtime',
      ),
      'react/jsx-dev-runtime': path.join(
        __dirname, 'node_modules', 'react', 'jsx-dev-runtime',
      ),
      '@core/plugins': corePlugins,
      '@app': frontendSrc,
      ...Object.fromEntries(extensions.map(e => [`@${e.id}`, e.src])),
    },
    // Deduplicate explicitly so any transitive dep that pulls in
    // its own React (rare but happens) is collapsed onto our copy.
    dedupe: ['react', 'react-dom'],
  },
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:8021',
      '/static': 'http://localhost:8021',
    },
    fs: {
      allow: [__dirname, corePlugins, ...extensions.map(e => e.src)],
    },
  },
  build: {
    outDir: 'dist',
  },
})
