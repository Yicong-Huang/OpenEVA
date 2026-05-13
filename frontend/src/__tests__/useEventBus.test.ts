import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useEventBus, _resetEventBus } from '../hooks/useEventBus'

// Mock EventSource
class MockEventSource {
  static instances: MockEventSource[] = []
  static CLOSED = 2
  url: string
  onmessage: ((evt: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  close() {
    this.readyState = MockEventSource.CLOSED
  }

  simulateMessage(data: string) {
    if (this.onmessage) this.onmessage({ data })
  }

  simulateError() {
    if (this.onerror) this.onerror()
  }
}

beforeEach(() => {
  _resetEventBus()
  MockEventSource.instances = []
  vi.stubGlobal('EventSource', MockEventSource)
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('useEventBus', () => {
  it('connects to /api/events/stream on first subscription', () => {
    renderHook(() => useEventBus('*', vi.fn()))
    expect(MockEventSource.instances).toHaveLength(1)
    expect(MockEventSource.instances[0].url).toBe('/api/events/stream')
  })

  it('shares a single SSE connection across multiple hooks', () => {
    const handler1 = vi.fn()
    const handler2 = vi.fn()
    renderHook(() => {
      useEventBus('agent.*', handler1)
      useEventBus('github.*', handler2)
    })
    // Only one EventSource instance
    expect(MockEventSource.instances).toHaveLength(1)
  })

  it('exact match: calls handler for matching event type', () => {
    const handler = vi.fn()
    renderHook(() => useEventBus('agent.task_done', handler))

    const source = MockEventSource.instances[0]
    act(() => {
      source.simulateMessage(JSON.stringify({ type: 'agent.task_done', session: 's1' }))
    })
    expect(handler).toHaveBeenCalledOnce()
    expect(handler).toHaveBeenCalledWith({ type: 'agent.task_done', session: 's1' })
  })

  it('exact match: does not call handler for non-matching event', () => {
    const handler = vi.fn()
    renderHook(() => useEventBus('agent.task_done', handler))

    const source = MockEventSource.instances[0]
    act(() => {
      source.simulateMessage(JSON.stringify({ type: 'github.comment' }))
    })
    expect(handler).not.toHaveBeenCalled()
  })

  it('wildcard match: "github.*" catches all github events', () => {
    const handler = vi.fn()
    renderHook(() => useEventBus('github.*', handler))

    const source = MockEventSource.instances[0]
    act(() => {
      source.simulateMessage(JSON.stringify({ type: 'github.comment' }))
      source.simulateMessage(JSON.stringify({ type: 'github.ci_activity' }))
      source.simulateMessage(JSON.stringify({ type: 'agent.idle' }))
    })
    expect(handler).toHaveBeenCalledTimes(2)
  })

  it('global "*" catches all events', () => {
    const handler = vi.fn()
    renderHook(() => useEventBus('*', handler))

    const source = MockEventSource.instances[0]
    act(() => {
      source.simulateMessage(JSON.stringify({ type: 'github.comment' }))
      source.simulateMessage(JSON.stringify({ type: 'agent.idle' }))
      source.simulateMessage(JSON.stringify({ type: 'auth.cert_expired' }))
    })
    expect(handler).toHaveBeenCalledTimes(3)
  })

  it('ignores malformed JSON', () => {
    const handler = vi.fn()
    renderHook(() => useEventBus('*', handler))

    const source = MockEventSource.instances[0]
    act(() => {
      source.simulateMessage('not-json{{{')
    })
    expect(handler).not.toHaveBeenCalled()
  })

  it('reconnects after error', () => {
    renderHook(() => useEventBus('*', vi.fn()))
    expect(MockEventSource.instances).toHaveLength(1)

    const source = MockEventSource.instances[0]
    act(() => { source.simulateError() })
    expect(source.readyState).toBe(MockEventSource.CLOSED)

    act(() => { vi.advanceTimersByTime(3000) })
    expect(MockEventSource.instances).toHaveLength(2)
  })

  it('unsubscribes on unmount', () => {
    const handler = vi.fn()
    const { unmount } = renderHook(() => useEventBus('github.*', handler))

    const source = MockEventSource.instances[0]
    act(() => {
      source.simulateMessage(JSON.stringify({ type: 'github.comment' }))
    })
    expect(handler).toHaveBeenCalledOnce()

    unmount()
    act(() => {
      source.simulateMessage(JSON.stringify({ type: 'github.comment' }))
    })
    // Should not receive after unmount
    expect(handler).toHaveBeenCalledOnce()
  })

  it('treats events with non-string `type` as empty type', () => {
    // Defensive: the SSE server is the only source so this should
    // never happen in prod, but the message handler hardens against
    // a malformed payload (type:123 / type:null) by coercing to "".
    // Global "*" handlers still see the event; named handlers don't.
    const star = vi.fn()
    const named = vi.fn()
    renderHook(() => {
      useEventBus('*', star)
      useEventBus('github.comment', named)
    })
    const source = MockEventSource.instances[0]
    act(() => {
      source.simulateMessage(JSON.stringify({ type: 123, payload: 'x' }))
    })
    expect(named).not.toHaveBeenCalled()
    expect(star).toHaveBeenCalledTimes(1)
  })

  it('unsubscribe is safe after the pattern bucket is cleared', () => {
    // Two hooks share a pattern. Unmount the first; the bucket still
    // exists (handler 2 lives there). Then `_resetEventBus` wipes
    // ALL state. Now unmounting the second hook hits the
    // `if (set) ...` else branch -- subscribers.get(pattern) returns
    // undefined and the cleanup must not blow up.
    const h1 = vi.fn()
    const h2 = vi.fn()
    const r1 = renderHook(() => useEventBus('github.*', h1))
    const r2 = renderHook(() => useEventBus('github.*', h2))
    r1.unmount()
    _resetEventBus()
    // Nothing throws -- the unsubscribe arrow finds no bucket.
    r2.unmount()
  })
})
