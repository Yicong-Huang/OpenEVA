import { render, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useLiveClock } from '../hooks/useLiveClock'

let renderCount = 0

function TestComponent({ intervalMs = 1000 }: { intervalMs?: number }) {
  useLiveClock(intervalMs)
  renderCount++
  return <div data-testid="tick">{renderCount}</div>
}

describe('useLiveClock', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    renderCount = 0
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('re-renders at the specified interval', () => {
    render(<TestComponent intervalMs={500} />)
    const initial = renderCount
    act(() => { vi.advanceTimersByTime(500) })
    expect(renderCount).toBeGreaterThan(initial)
  })

  it('stops re-rendering after unmount', () => {
    const { unmount } = render(<TestComponent intervalMs={500} />)
    unmount()
    const countAfterUnmount = renderCount
    act(() => { vi.advanceTimersByTime(1000) })
    expect(renderCount).toBe(countAfterUnmount)
  })
})
