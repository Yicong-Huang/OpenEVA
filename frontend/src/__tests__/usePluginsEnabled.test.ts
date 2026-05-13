import { renderHook, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  usePluginEnabled,
  refreshPluginsEnabled,
  _resetForTests,
} from '../hooks/usePluginsEnabled'

let fetchResponse: Record<string, boolean> = {}
let fetchCalls = 0
let origFetch: typeof fetch

function mockFetch(response: Record<string, boolean>) {
  fetchResponse = response
  fetchCalls = 0
  globalThis.fetch = vi.fn(async () => {
    fetchCalls += 1
    return new Response(
      JSON.stringify({ plugins: fetchResponse }),
      { status: 200 },
    )
  }) as typeof fetch
}

describe('usePluginsEnabled', () => {
  beforeEach(() => {
    origFetch = globalThis.fetch
    _resetForTests()
  })
  afterEach(() => {
    globalThis.fetch = origFetch
    _resetForTests()
  })

  it('starts with true while initial fetch is in flight', async () => {
    mockFetch({ boba: true })
    const { result } = renderHook(() => usePluginEnabled('boba'))
    // Synchronous initial value: defaulted to true so the UI doesn't
    // briefly black out every plugin on first render.
    expect(result.current).toBe(true)
  })

  it('reflects the fetched value once the request resolves', async () => {
    mockFetch({ boba: false })
    const { result } = renderHook(() => usePluginEnabled('boba'))
    await waitFor(() => expect(result.current).toBe(false))
  })

  it('shares the fetch across multiple subscribers', async () => {
    mockFetch({ boba: true, ubereats: false })
    const a = renderHook(() => usePluginEnabled('boba'))
    const b = renderHook(() => usePluginEnabled('ubereats'))
    await waitFor(() => expect(b.result.current).toBe(false))
    expect(a.result.current).toBe(true)
    // Only one /api/plugins/enabled call regardless of number of
    // subscribers -- the fan-out is intentional to keep the network
    // chatter low.
    expect(fetchCalls).toBe(1)
  })

  it('refreshPluginsEnabled() refetches and notifies subscribers', async () => {
    mockFetch({ boba: true })
    const { result } = renderHook(() => usePluginEnabled('boba'))
    await waitFor(() => expect(result.current).toBe(true))
    // Flip the server response and refresh.
    fetchResponse = { boba: false }
    await act(async () => { await refreshPluginsEnabled() })
    await waitFor(() => expect(result.current).toBe(false))
    expect(fetchCalls).toBeGreaterThanOrEqual(2)
  })

  it('defaults missing plugin keys to enabled', async () => {
    // Server returned `{}` -> any plugin name should resolve to true.
    mockFetch({})
    const { result } = renderHook(() => usePluginEnabled('boba'))
    await waitFor(() => expect(fetchCalls).toBeGreaterThan(0))
    expect(result.current).toBe(true)
  })

  it('falls back to enabled on fetch error', async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response('boom', { status: 500 })
    ) as typeof fetch
    const { result } = renderHook(() => usePluginEnabled('boba'))
    // Eventually the listener fires with the empty cache.
    await waitFor(() => expect(result.current).toBe(true))
  })
})
