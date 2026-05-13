import type { ComponentType } from 'react'

// Vite plugin discovery: glob every `<Name>Plugin.tsx` file under the
// known plugin trees so OSS installs (with no extension namespaces)
// still build, and any future extension namespace gets picked up just
// by adding files. No hardcoded vendor imports here.
//
// Two glob patterns are needed because the core/OSS plugins sit one
// level deeper (`core/src/plugins/<id>/`) than extension plugins
// (`<extension>/src/<id>/`):
//
//   core OSS:     core/src/plugins/pr/PRPlugin.tsx
//   extension:    <extension>/src/<id>/<Name>Plugin.tsx
//
// Each plugin file is expected to export:
//
//   * `<Name>Plugin`      -- the full widget shown in the open sidebar
//   * `Mini<Name>Plugin`  -- the compact 36px icon in the collapsed
//                            sidebar (optional; sidebar renders an
//                            empty slot when missing)
//
// The naming convention is what we hook on: the discovery code reads
// the file path's basename (`LunchPlugin.tsx` -> `LunchPlugin`) and
// pulls both names from the module record.

// Relative paths from this file (`frontend/src/utils/...`) up to the
// repo root. Vite's `import.meta.glob` requires a literal pattern.
const coreModules = import.meta.glob('../../../core/src/plugins/*/*Plugin.tsx', {
  eager: true,
}) as Record<string, Record<string, ComponentType>>

// `*/src/*/*Plugin.tsx` matches `<extension>/src/<plugin>/<Name>Plugin.tsx`.
// `frontend/` and `core/` have a deeper layout so they don't match this
// pattern -- only true extension plugins do.
const extensionModules = import.meta.glob('../../../*/src/*/*Plugin.tsx', {
  eager: true,
}) as Record<string, Record<string, ComponentType>>

export interface DiscoveredPlugin {
  /** Component name -- e.g. "LunchPlugin", "PRPlugin". Used as React key. */
  name: string
  /** Full widget. Always present. */
  Full: ComponentType
  /** Mini variant for the collapsed sidebar. Optional. */
  Mini?: ComponentType
}

function collect(
  modules: Record<string, Record<string, ComponentType>>,
  out: DiscoveredPlugin[],
  seen: Set<string>,
): void {
  for (const path of Object.keys(modules).sort()) {
    // Path looks like `/<extension>/src/<id>/<Name>Plugin.tsx`.
    // Pull the basename (`LunchPlugin`).
    const m = path.match(/\/([A-Z][A-Za-z0-9]*Plugin)\.tsx$/)
    if (!m) continue
    const name = m[1]
    if (seen.has(name)) continue
    const mod = modules[path]
    const Full = mod[name] as ComponentType | undefined
    if (!Full) continue
    seen.add(name)
    out.push({
      name,
      Full,
      Mini: mod[`Mini${name}`] as ComponentType | undefined,
    })
  }
}

// Build once at module load. Stable order = sorted by path, which
// puts core/ before extension namespaces alphabetically, and within
// each, alphabetical by folder name. That's good enough for the
// sidebar without a per-plugin priority knob.
const plugins: DiscoveredPlugin[] = []
const seen = new Set<string>()
collect(coreModules, plugins, seen)
collect(extensionModules, plugins, seen)

export const discoveredPlugins: ReadonlyArray<DiscoveredPlugin> = plugins
