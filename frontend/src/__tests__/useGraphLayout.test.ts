import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useGraphLayout } from '../hooks/useGraphLayout'

const KEY = (pid: string) => `eva-graph-layout-${pid}`


beforeEach(() => {
  try { localStorage.clear() } catch { /* ignore */ }
})

describe('useGraphLayout', () => {
  it('starts with empty positions when no LS data', () => {
    const { result } = renderHook(() => useGraphLayout('p1'))
    expect(result.current.positions).toEqual({})
  })

  it('hydrates from localStorage on project mount', () => {
    localStorage.setItem(KEY('p1'), JSON.stringify({ a: { x: 10, y: 20 } }))
    const { result } = renderHook(() => useGraphLayout('p1'))
    expect(result.current.positions).toEqual({ a: { x: 10, y: 20 } })
  })

  it('setPosition persists to LS and updates state', () => {
    const { result } = renderHook(() => useGraphLayout('p1'))
    act(() => result.current.setPosition('t', { x: 50, y: 60 }))
    expect(result.current.positions).toEqual({ t: { x: 50, y: 60 } })
    const stored = JSON.parse(localStorage.getItem(KEY('p1')) || '{}')
    expect(stored).toEqual({ t: { x: 50, y: 60 } })
  })

  it('seedPositions only fills missing entries (no clobber)', () => {
    const { result } = renderHook(() => useGraphLayout('p1'))
    act(() => result.current.setPosition('a', { x: 1, y: 2 }))
    act(() => result.current.seedPositions({
      a: { x: 999, y: 999 },     // existing -- must NOT overwrite
      b: { x: 10, y: 20 },        // new -- should fill
    }))
    expect(result.current.positions).toEqual({
      a: { x: 1, y: 2 },
      b: { x: 10, y: 20 },
    })
  })

  it('seedPositions does not write LS when nothing changed', () => {
    const { result } = renderHook(() => useGraphLayout('p1'))
    act(() => result.current.setPosition('a', { x: 1, y: 2 }))
    const before = localStorage.getItem(KEY('p1'))
    // Seed with the same key already set -- no-op.
    act(() => result.current.seedPositions({ a: { x: 99, y: 99 } }))
    expect(localStorage.getItem(KEY('p1'))).toBe(before)
  })

  it('clearLayout removes LS and resets state', () => {
    const { result } = renderHook(() => useGraphLayout('p1'))
    act(() => result.current.setPosition('t', { x: 1, y: 2 }))
    act(() => result.current.clearLayout())
    expect(result.current.positions).toEqual({})
    expect(localStorage.getItem(KEY('p1'))).toBeNull()
  })

  it('handles a malformed LS payload by starting empty', () => {
    localStorage.setItem(KEY('p1'), '{not-json{')
    const { result } = renderHook(() => useGraphLayout('p1'))
    expect(result.current.positions).toEqual({})
  })

  it('null projectId yields empty state with no LS access', () => {
    const { result } = renderHook(() => useGraphLayout(null))
    expect(result.current.positions).toEqual({})
    // Calling setPosition / clearLayout on a null project is a no-op
    // (no LS write); just verify no throw.
    act(() => result.current.setPosition('t', { x: 1, y: 2 }))
    act(() => result.current.clearLayout())
  })

  it('isolates positions per project (key includes projectId)', () => {
    const { result: r1 } = renderHook(() => useGraphLayout('p1'))
    act(() => r1.current.setPosition('t', { x: 1, y: 1 }))
    const { result: r2 } = renderHook(() => useGraphLayout('p2'))
    expect(r2.current.positions).toEqual({})
    act(() => r2.current.setPosition('t', { x: 99, y: 99 }))
    // Each project's LS slot is independent
    expect(JSON.parse(localStorage.getItem(KEY('p1')) || '{}'))
      .toEqual({ t: { x: 1, y: 1 } })
    expect(JSON.parse(localStorage.getItem(KEY('p2')) || '{}'))
      .toEqual({ t: { x: 99, y: 99 } })
  })
})
