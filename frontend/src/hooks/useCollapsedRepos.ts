import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'

/**
 * Read-write persistence for which repo groups are collapsed on the
 * Reviews page. Backed by the generic settings store under
 * `ui.reviews.collapsed_repos` (a JSON array of repo keys).
 *
 * Unlike `useLayoutRatios` (read-only in-app), collapse state is
 * toggled by the user, so this hook exposes a `toggle(repo)` that
 * updates local state immediately (optimistic) and persists in the
 * background. A persist failure is swallowed -- the in-session state
 * still reflects the user's intent; only cross-reload memory is lost.
 *
 * `SETTING_KEY` GET returns 404 when unset (fresh install): the hook
 * then seeds from `initialDefault` so a first-time user gets the
 * intended defaults (e.g. apache/texera collapsed) without a settings
 * row existing yet.
 */
const SETTING_KEY = 'ui.reviews.collapsed_repos'

export function useCollapsedRepos(initialDefault: string[] = []) {
  const [collapsed, setCollapsed] = useState<Set<string>>(
    () => new Set(initialDefault),
  )
  // Guard so the background persist in `toggle` doesn't fire before
  // the initial fetch resolves (which would race and clobber the
  // stored value with the default).
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetch(`/api/settings/${encodeURIComponent(SETTING_KEY)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled) return
        if (data && Array.isArray(data.value)) {
          setCollapsed(new Set(data.value.filter((x: unknown) => typeof x === 'string')))
        }
        // 404 / malformed -> keep the seeded `initialDefault`.
      })
      .catch(() => { /* keep default */ })
      .finally(() => { if (!cancelled) setLoaded(true) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const toggle = useCallback((repo: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(repo)) next.delete(repo)
      else next.add(repo)
      // Persist in the background once the initial load settled.
      if (loaded) {
        api.setSetting(SETTING_KEY, [...next]).catch(() => { /* best-effort */ })
      }
      return next
    })
  }, [loaded])

  return { collapsed, toggle }
}
