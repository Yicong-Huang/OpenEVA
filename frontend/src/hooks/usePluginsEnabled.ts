/**
 * Per-plugin enabled-state subscription.
 *
 * One module-level cache + listener fan-out so we never make more
 * than one /api/plugins/enabled fetch per app load no matter how many
 * plugin components mount. Components subscribe via the hook; when
 * the user flips a plugin in the SettingsModal we refresh the cache
 * and notify everyone.
 */
import { useEffect, useState } from 'react'

export type PluginId =
  | 'pr' | 'forkable' | 'ubereats' | 'boba'
  | 'slack_monitor' | 'github_poll' | 'cert_tracker'

let _cache: Record<string, boolean> | null = null
let _inflight: Promise<Record<string, boolean>> | null = null
const _listeners = new Set<(map: Record<string, boolean>) => void>()


async function fetchEnabled(): Promise<Record<string, boolean>> {
  if (_cache) return _cache
  if (_inflight) return _inflight
  _inflight = (async () => {
    try {
      const resp = await fetch('/api/plugins/enabled')
      if (!resp.ok) throw new Error('not ok')
      const data = await resp.json() as { plugins: Record<string, boolean> }
      _cache = data.plugins || {}
      return _cache
    } catch {
      // Network/parse failure -- default everything to enabled so
      // a transient backend hiccup doesn't black out the UI.
      _cache = {}
      return _cache
    } finally {
      _inflight = null
    }
  })()
  return _inflight
}

/** Force a refetch and notify subscribers. Call after toggling a
 * plugin in Settings so the panels react immediately. */
export async function refreshPluginsEnabled() {
  _cache = null
  _inflight = null
  const next = await fetchEnabled()
  for (const cb of _listeners) cb(next)
}

/** Subscribe to plugin enabled-state. Returns the current value
 * (defaulting to `true` while the initial fetch is in flight, so we
 * don't briefly hide every plugin on first render). */
export function usePluginEnabled(name: PluginId): boolean {
  const [enabled, setEnabled] = useState<boolean>(() => {
    if (_cache && name in _cache) return _cache[name] !== false
    return true
  })

  useEffect(() => {
    let cancelled = false
    const update = (map: Record<string, boolean>) => {
      if (cancelled) return
      setEnabled(name in map ? map[name] !== false : true)
    }
    _listeners.add(update)
    fetchEnabled().then(update)
    return () => {
      cancelled = true
      _listeners.delete(update)
    }
  }, [name])

  return enabled
}

/** Test-only helper: reset the module cache between tests. */
export function _resetForTests() {
  _cache = null
  _inflight = null
  _listeners.clear()
}
