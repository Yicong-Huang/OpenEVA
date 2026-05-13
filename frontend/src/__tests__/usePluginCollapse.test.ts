import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { usePluginCollapse } from '../hooks/usePluginCollapse'

vi.mock('../hooks/useLiveClock', () => ({
  useLiveClock: vi.fn(),
}))

describe('usePluginCollapse', () => {
  it('no activeWindow: defaults to expanded', () => {
    const { result } = renderHook(() => usePluginCollapse())
    expect(result.current.collapsed).toBe(false)
  })

  it('with activeWindow outside range: collapsed', () => {
    // Window is 0-1 (midnight to 1am), current time is definitely outside
    const { result } = renderHook(() =>
      usePluginCollapse({ activeWindow: { start: 0, end: 1 } }),
    )
    // Unless test runs at midnight, this should be collapsed
    const now = new Date()
    const mins = now.getHours() * 60 + now.getMinutes()
    const expected = mins < 0 || mins >= 1 // almost certainly true
    expect(result.current.collapsed).toBe(expected)
  })

  it('with activeWindow covering now: expanded', () => {
    // Window that covers all day
    const { result } = renderHook(() =>
      usePluginCollapse({ activeWindow: { start: 0, end: 1440 } }),
    )
    expect(result.current.collapsed).toBe(false)
  })

  it('forceExpanded overrides time window', () => {
    const { result } = renderHook(() =>
      usePluginCollapse({ activeWindow: { start: 0, end: 1 }, forceExpanded: true }),
    )
    expect(result.current.collapsed).toBe(false)
  })

  it('toggle flips the collapsed state', () => {
    const { result } = renderHook(() => usePluginCollapse())
    expect(result.current.collapsed).toBe(false)

    act(() => { result.current.toggle() })
    expect(result.current.collapsed).toBe(true)

    act(() => { result.current.toggle() })
    expect(result.current.collapsed).toBe(false)
  })

  it('user toggle overrides auto logic', () => {
    // Narrow window so it auto-collapses
    const { result } = renderHook(() =>
      usePluginCollapse({ activeWindow: { start: 0, end: 1 } }),
    )
    // Should be collapsed (outside window)
    expect(result.current.collapsed).toBe(true)

    // User expands
    act(() => { result.current.toggle() })
    expect(result.current.collapsed).toBe(false)
  })
})
