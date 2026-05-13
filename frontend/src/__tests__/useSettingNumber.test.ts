import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useSettingNumber } from '../hooks/useSettingNumber'

const fetchSpy = vi.fn()
beforeEach(() => {
  vi.stubGlobal('fetch', fetchSpy)
  fetchSpy.mockReset()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

function ok(value: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve({ value }) })
}
function notFound() {
  return Promise.resolve({ ok: false, json: () => Promise.resolve(null) })
}


describe('useSettingNumber', () => {
  it('returns the default while the fetch is in flight', async () => {
    fetchSpy.mockReturnValue(new Promise(() => {}))  // never resolves
    const { result } = renderHook(() => useSettingNumber('k', 42))
    expect(result.current).toBe(42)
  })

  it('falls back to default when the setting is unset (404)', async () => {
    fetchSpy.mockReturnValue(notFound())
    const { result } = renderHook(() => useSettingNumber('k', 42))
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled())
    expect(result.current).toBe(42)
  })

  it('uses the configured value when valid', async () => {
    fetchSpy.mockReturnValue(ok(123))
    const { result } = renderHook(() => useSettingNumber('k', 42))
    await waitFor(() => expect(result.current).toBe(123))
  })

  it('coerces a numeric string', async () => {
    fetchSpy.mockReturnValue(ok('17'))
    const { result } = renderHook(() => useSettingNumber('k', 42))
    await waitFor(() => expect(result.current).toBe(17))
  })

  it('falls back when value is below `min`', async () => {
    fetchSpy.mockReturnValue(ok(3))
    const { result } = renderHook(() =>
      useSettingNumber('k', 42, { min: 7 }),
    )
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled())
    expect(result.current).toBe(42)
  })

  it('falls back when value is above `max`', async () => {
    fetchSpy.mockReturnValue(ok(9999))
    const { result } = renderHook(() =>
      useSettingNumber('k', 42, { max: 365 }),
    )
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled())
    expect(result.current).toBe(42)
  })

  it('accepts a value at the inclusive boundary', async () => {
    fetchSpy.mockReturnValue(ok(7))
    const { result } = renderHook(() =>
      useSettingNumber('k', 42, { min: 7, max: 365 }),
    )
    await waitFor(() => expect(result.current).toBe(7))
  })

  it('falls back on non-numeric / non-finite values', async () => {
    fetchSpy.mockReturnValue(ok('not a number'))
    const { result } = renderHook(() => useSettingNumber('k', 42))
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled())
    expect(result.current).toBe(42)
  })

  it('falls back on a network failure', async () => {
    fetchSpy.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useSettingNumber('k', 42))
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled())
    expect(result.current).toBe(42)
  })

  it('encodes the setting key (handles dots / slashes)', async () => {
    fetchSpy.mockReturnValue(ok(5))
    renderHook(() =>
      useSettingNumber('ui.worklog.day_mode_days', 60, { min: 7 }),
    )
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled())
    const url = fetchSpy.mock.calls[0][0] as string
    expect(url).toContain('ui.worklog.day_mode_days')
  })
})
