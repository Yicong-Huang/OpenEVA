import { renderHook, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useApi } from '../hooks/useApi'

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

beforeEach(() => mockFetch.mockReset())

describe('useApi', () => {
  it('fetches data and returns it', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve(JSON.stringify({ name: 'test' })),
    })
    const { result } = renderHook(() => useApi<{ name: string }>('/api/test'))
    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data?.name).toBe('test')
    expect(result.current.error).toBeNull()
  })

  it('sets error on failure', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false, status: 500,
      text: () => Promise.resolve('Server error'),
    })
    const { result } = renderHook(() => useApi<unknown>('/api/fail'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBeTruthy()
    expect(result.current.data).toBeNull()
  })

  it('does not fetch when url is null', () => {
    const { result } = renderHook(() => useApi<unknown>(null))
    expect(result.current.loading).toBe(false)
    expect(result.current.data).toBeNull()
    expect(mockFetch).not.toHaveBeenCalled()
  })
})
