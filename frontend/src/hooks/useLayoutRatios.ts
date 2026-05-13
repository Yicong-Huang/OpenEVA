import { useEffect, useState } from 'react'

/**
 * Shared reader for `ui.layout.*` width-ratio settings (Reviews
 * page's 3-pane split, PRsPage's task+detail panes, etc.). Each
 * page passes its own settings key + canonical default; the hook
 * fetches the value once on mount, validates the shape, and falls
 * back to the default on any malformed response.
 *
 * Why a shared hook: the user's "布局也可以用setting来做" directive
 * implies many per-page layout knobs. Centralising the fetch +
 * validation here keeps the per-page wiring trivial (one
 * `useLayoutRatios(KEY, [a, b, c])` call) and avoids drift between
 * implementations.
 *
 * Validation contract (matches `core.settings.get_reviews_col_ratios`):
 *   - Value must be a list of N positive numbers (where N is the
 *     defaults' length).
 *   - Wrong length, negative, non-numeric -> default.
 *
 * Tests live in `__tests__/useLayoutRatios.test.ts`. The previous
 * inline `_useReviewsRatios` inside ReviewsPage was untestable in
 * isolation; this lift makes the hook a directly verifiable unit.
 */
export function useLayoutRatios<N extends number>(
  settingKey: string,
  defaults: number[] & { length: N },
): number[] & { length: N } {
  const [ratios, setRatios] = useState<number[]>(defaults)
  useEffect(() => {
    let cancelled = false
    fetch(`/api/settings/${encodeURIComponent(settingKey)}`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (cancelled || !data) return
        const v = data.value
        if (Array.isArray(v) && v.length === defaults.length
            && v.every((x: unknown) => typeof x === 'number' && x > 0)) {
          setRatios(v as number[])
        }
      })
      .catch(() => { /* fall through to default */ })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settingKey])
  return ratios as number[] & { length: N }
}
