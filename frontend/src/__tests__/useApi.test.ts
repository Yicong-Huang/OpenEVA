import { renderHook, waitFor, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useApi } from '../hooks/useApi'

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

beforeEach(() => mockFetch.mockReset())

describe('useApi', () => {
  it('fetches data on mount', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve('{"name":"test"}'),
    })
    const { result } = renderHook(() => useApi<{ name: string }>('/api/test'))
    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toEqual({ name: 'test' })
    expect(result.current.error).toBeNull()
  })

  it('sets error on HTTP failure', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: () => Promise.resolve('Internal Server Error'),
    })
    const { result } = renderHook(() => useApi<unknown>('/api/fail'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('500: Internal Server Error')
    expect(result.current.data).toBeNull()
  })

  it('skips fetch when url is null', () => {
    const { result } = renderHook(() => useApi<unknown>(null))
    expect(result.current.loading).toBe(false)
    expect(result.current.data).toBeNull()
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('does NOT re-set state when response JSON is identical', async () => {
    const jsonText = '{"name":"stable"}'
    mockFetch.mockResolvedValue({
      ok: true,
      text: () => Promise.resolve(jsonText),
    })

    // We cannot spy on setData directly, so instead we verify by checking
    // that fetch is called twice but the data reference stays the same.
    const { result } = renderHook(() => useApi<{ name: string }>('/api/stable'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    const firstData = result.current.data

    // Trigger a refetch - fetch returns identical JSON
    await act(async () => {
      result.current.refetch()
    })
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2))

    // Data reference should be the same object since JSON was identical
    expect(result.current.data).toBe(firstData)
    expect(result.current.data).toEqual({ name: 'stable' })
  })

  it('mutate updates data locally and invalidates cache', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve('{"count":1}'),
    })
    const { result } = renderHook(() => useApi<{ count: number }>('/api/counter'))
    await waitFor(() => expect(result.current.data).toEqual({ count: 1 }))

    // Mutate locally
    act(() => {
      result.current.mutate((prev) => prev ? { count: prev.count + 1 } : prev)
    })
    expect(result.current.data).toEqual({ count: 2 })

    // After mutate, refetch should pick up new data (cache invalidated)
    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve('{"count":10}'),
    })
    await act(async () => {
      result.current.refetch()
    })
    await waitFor(() => expect(result.current.data).toEqual({ count: 10 }))
  })

  it('sets error on network failure', async () => {
    mockFetch.mockRejectedValueOnce(new Error('network down'))
    const { result } = renderHook(() => useApi<unknown>('/api/unreachable'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('network down')
    expect(result.current.data).toBeNull()
  })

  it('sets error on non-Error throw', async () => {
    mockFetch.mockRejectedValueOnce('string error')
    const { result } = renderHook(() => useApi<unknown>('/api/string-error'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('string error')
  })

  it('re-fetches when url changes', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve('{"a":1}'),
    })
    const { result, rerender } = renderHook(
      ({ url }) => useApi<Record<string, number>>(url),
      { initialProps: { url: '/api/first' as string | null } },
    )
    await waitFor(() => expect(result.current.data).toEqual({ a: 1 }))

    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve('{"b":2}'),
    })
    rerender({ url: '/api/second' })
    await waitFor(() => expect(result.current.data).toEqual({ b: 2 }))
  })

  it('does not show loading on subsequent refetches', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve('{"x":1}'),
    })
    const { result } = renderHook(() => useApi<{ x: number }>('/api/no-loading'))
    await waitFor(() => expect(result.current.loading).toBe(false))

    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve('{"x":2}'),
    })
    // On refetch, loading should not flip to true
    act(() => { result.current.refetch() })
    expect(result.current.loading).toBe(false)
    await waitFor(() => expect(result.current.data).toEqual({ x: 2 }))
  })
})
