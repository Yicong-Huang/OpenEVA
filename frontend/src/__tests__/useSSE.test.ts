import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useSSE } from '../hooks/useSSE'

// Mock EventSource
class MockEventSource {
  onmessage: ((evt: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  url: string
  closed = false

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  close() {
    this.closed = true
  }

  // Simulate a message
  simulateMessage(data: string) {
    this.onmessage?.({ data })
  }

  simulateError() {
    this.onerror?.()
  }

  static instances: MockEventSource[] = []
  static reset() {
    MockEventSource.instances = []
  }
}

beforeEach(() => {
  MockEventSource.reset()
  vi.stubGlobal('EventSource', MockEventSource)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useSSE', () => {
  it('creates EventSource when url is provided', () => {
    const onMessage = vi.fn()
    renderHook(() => useSSE('/api/stream', onMessage))
    expect(MockEventSource.instances.length).toBe(1)
    expect(MockEventSource.instances[0].url).toBe('/api/stream')
  })

  it('does not create EventSource when url is null', () => {
    const onMessage = vi.fn()
    renderHook(() => useSSE(null, onMessage))
    expect(MockEventSource.instances.length).toBe(0)
  })

  it('calls onMessage when data arrives', () => {
    const onMessage = vi.fn()
    renderHook(() => useSSE('/api/stream', onMessage))
    MockEventSource.instances[0].simulateMessage('{"phase":"done"}')
    expect(onMessage).toHaveBeenCalledWith('{"phase":"done"}')
  })

  it('closes EventSource on error', () => {
    const onMessage = vi.fn()
    renderHook(() => useSSE('/api/stream', onMessage))
    const source = MockEventSource.instances[0]
    source.simulateError()
    expect(source.closed).toBe(true)
  })

  it('closes EventSource on unmount', () => {
    const onMessage = vi.fn()
    const { unmount } = renderHook(() => useSSE('/api/stream', onMessage))
    const source = MockEventSource.instances[0]
    unmount()
    expect(source.closed).toBe(true)
  })

  it('closes previous EventSource when url changes', () => {
    const onMessage = vi.fn()
    const { rerender } = renderHook(
      ({ url }) => useSSE(url, onMessage),
      { initialProps: { url: '/api/stream1' as string | null } }
    )
    const first = MockEventSource.instances[0]
    rerender({ url: '/api/stream2' })
    expect(first.closed).toBe(true)
    expect(MockEventSource.instances.length).toBe(2)
  })

  it('close() method closes the EventSource', () => {
    const onMessage = vi.fn()
    const { result } = renderHook(() => useSSE('/api/stream', onMessage))
    const source = MockEventSource.instances[0]
    act(() => { result.current.close() })
    expect(source.closed).toBe(true)
  })
})
