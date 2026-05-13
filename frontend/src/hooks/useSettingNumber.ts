import { useEffect, useState } from 'react'

/**
 * Read a single numeric `service.*` / `ui.*` setting with bounds
 * validation, falling back to the supplied default on any failure.
 *
 * Why a dedicated hook: several pages need scalar knobs
 * (`ui.worklog.day_mode_days`, `ui.worklog.standup_mode_weeks`, ...)
 * that follow the same pattern -- fetch one setting, validate the
 * shape (number + within [min, max]), and use the default on miss.
 * `useLayoutRatios` covers list-of-numbers; this covers the scalar
 * case so callers don't reimplement the fetch + clamp logic each
 * time.
 *
 * Validation contract:
 *   - Value must be a number (or numeric string the backend coerces).
 *   - If `min` / `max` are provided, the value must satisfy them
 *     INCLUSIVELY -- a value outside the band falls back to
 *     `defaultValue`. This matches the server-side
 *     `core.settings.get_int_in_range` accessor pattern.
 */
export function useSettingNumber(
  settingKey: string,
  defaultValue: number,
  bounds: { min?: number; max?: number } = {},
): number {
  const { min, max } = bounds
  const [value, setValue] = useState<number>(defaultValue)
  useEffect(() => {
    let cancelled = false
    fetch(`/api/settings/${encodeURIComponent(settingKey)}`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (cancelled || !data) return
        const v = data.value
        const n = typeof v === 'number' ? v
                : typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v)
                ? Number(v)
                : NaN
        if (!Number.isFinite(n)) return
        if (typeof min === 'number' && n < min) return
        if (typeof max === 'number' && n > max) return
        setValue(n)
      })
      .catch(() => { /* fall through to default */ })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settingKey])
  return value
}
