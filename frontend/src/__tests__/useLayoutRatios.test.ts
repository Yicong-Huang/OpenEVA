/**
 * Direct tests for the shared `useLayoutRatios` hook. The hook is
 * the per-page layout-ratio reader used by ReviewsPage today and any
 * future page that wants `ui.layout.*` knobs (PRsPage, SessionsPage,
 * etc.). Centralizing the fetch + validation here means each page's
 * own test can rely on the contract being kept honest.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useLayoutRatios } from '../hooks/useLayoutRatios'

const DEFAULTS = [25, 35, 40]

function mockSettingsResponse(value: unknown) {
  return vi.fn(async (url: RequestInfo | URL) => {
    const u = String(url)
    if (u.includes('/api/settings/')) {
      return new Response(JSON.stringify({ key: 'k', value }), { status: 200 })
    }
    return new Response('', { status: 404 })
  }) as typeof fetch
}

describe('useLayoutRatios', () => {
  let origFetch: typeof fetch
  beforeEach(() => {
    origFetch = globalThis.fetch
  })
  afterEach(() => {
    globalThis.fetch = origFetch
  })

  it('returns defaults synchronously on first render (before fetch resolves)', () => {
    globalThis.fetch = vi.fn(() => new Promise(() => {})) as typeof fetch
    const { result } = renderHook(() => useLayoutRatios('any.key', DEFAULTS))
    expect(result.current).toEqual(DEFAULTS)
  })

  it('flips to settings value when fetch returns a valid triple', async () => {
    globalThis.fetch = mockSettingsResponse([50, 30, 20])
    const { result } = renderHook(() =>
      useLayoutRatios('ui.layout.reviews_col_ratios', DEFAULTS))
    await waitFor(() => expect(result.current).toEqual([50, 30, 20]))
  })

  it('keeps defaults when settings value is wrong length', async () => {
    globalThis.fetch = mockSettingsResponse([50, 50])
    const { result } = renderHook(() => useLayoutRatios('k', DEFAULTS))
    // Tick of microtasks; should still be defaults.
    await new Promise((r) => setTimeout(r, 10))
    expect(result.current).toEqual(DEFAULTS)
  })

  it('keeps defaults when settings value contains a non-positive number', async () => {
    globalThis.fetch = mockSettingsResponse([25, -1, 40])
    const { result } = renderHook(() => useLayoutRatios('k', DEFAULTS))
    await new Promise((r) => setTimeout(r, 10))
    expect(result.current).toEqual(DEFAULTS)
  })

  it('keeps defaults when settings value is non-numeric', async () => {
    globalThis.fetch = mockSettingsResponse([25, '35', 40])
    const { result } = renderHook(() => useLayoutRatios('k', DEFAULTS))
    await new Promise((r) => setTimeout(r, 10))
    expect(result.current).toEqual(DEFAULTS)
  })

  it('keeps defaults when settings endpoint returns non-200', async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response('', { status: 500 })) as typeof fetch
    const { result } = renderHook(() => useLayoutRatios('k', DEFAULTS))
    await new Promise((r) => setTimeout(r, 10))
    expect(result.current).toEqual(DEFAULTS)
  })

  it('keeps defaults when fetch throws', async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error('network down')
    }) as typeof fetch
    const { result } = renderHook(() => useLayoutRatios('k', DEFAULTS))
    await new Promise((r) => setTimeout(r, 10))
    expect(result.current).toEqual(DEFAULTS)
  })

  it('encodes the setting key in the URL (handles slashes / dots)', async () => {
    const fetchSpy = mockSettingsResponse([1, 2, 3]) as ReturnType<typeof vi.fn>
    globalThis.fetch = fetchSpy as unknown as typeof fetch
    renderHook(() => useLayoutRatios('ui.layout.with/slash', DEFAULTS))
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled())
    const calledUrl = String(fetchSpy.mock.calls[0][0])
    // `.` is allowed in URL paths so it stays; `/` becomes %2F.
    expect(calledUrl).toContain('ui.layout.with%2Fslash')
  })

  it('works for 2-pane defaults (not just 3)', async () => {
    globalThis.fetch = mockSettingsResponse([60, 40])
    const { result } = renderHook(() => useLayoutRatios('k', [50, 50]))
    await waitFor(() => expect(result.current).toEqual([60, 40]))
  })

  it('keeps defaults when 2-pane setting has 3 elements (length mismatch)', async () => {
    globalThis.fetch = mockSettingsResponse([60, 20, 20])
    const { result } = renderHook(() => useLayoutRatios('k', [50, 50]))
    await new Promise((r) => setTimeout(r, 10))
    expect(result.current).toEqual([50, 50])
  })

  it('cancels the in-flight fetch when component unmounts mid-request', async () => {
    let resolveFetch: (r: Response) => void = () => {}
    globalThis.fetch = vi.fn(() => new Promise<Response>((r) => {
      resolveFetch = r
    })) as typeof fetch
    const { result, unmount } = renderHook(() =>
      useLayoutRatios('k', DEFAULTS))
    unmount()
    // Resolve the fetch AFTER unmount -- the cancelled flag must
    // prevent the setState. No "update on unmounted" warning fires;
    // the value snapshot still equals defaults.
    resolveFetch(new Response(JSON.stringify({ value: [50, 30, 20] }),
                              { status: 200 }))
    await new Promise((r) => setTimeout(r, 10))
    expect(result.current).toEqual(DEFAULTS)
  })
})
