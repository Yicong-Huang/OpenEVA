import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import { useTheme } from '../hooks/useTheme'

describe('useTheme', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
  })

  it('defaults to dark theme', () => {
    const { result } = renderHook(() => useTheme())
    expect(result.current.theme).toBe('dark')
  })

  it('applies data-theme attribute to documentElement', () => {
    renderHook(() => useTheme())
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('toggle switches dark to light', () => {
    const { result } = renderHook(() => useTheme())
    act(() => { result.current.toggle() })
    expect(result.current.theme).toBe('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })

  it('toggle switches light back to dark', () => {
    const { result } = renderHook(() => useTheme())
    act(() => { result.current.toggle() })
    act(() => { result.current.toggle() })
    expect(result.current.theme).toBe('dark')
  })

  it('setTheme sets specific theme', () => {
    const { result } = renderHook(() => useTheme())
    act(() => { result.current.setTheme('light') })
    expect(result.current.theme).toBe('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })

  it('persists theme to localStorage', () => {
    const { result } = renderHook(() => useTheme())
    act(() => { result.current.setTheme('light') })
    expect(localStorage.getItem('eva-theme')).toBe('light')
  })

  it('reads theme from localStorage on mount', () => {
    localStorage.setItem('eva-theme', 'light')
    const { result } = renderHook(() => useTheme())
    expect(result.current.theme).toBe('light')
  })

  it('handles corrupted localStorage gracefully', () => {
    localStorage.setItem('eva-theme', 'invalid-value')
    const { result } = renderHook(() => useTheme())
    expect(result.current.theme).toBe('dark')
  })

  it('accepts the new alternate palettes', () => {
    const { result } = renderHook(() => useTheme())
    act(() => { result.current.setTheme('solarized-dark') })
    expect(result.current.theme).toBe('solarized-dark')
    expect(document.documentElement.getAttribute('data-theme'))
      .toBe('solarized-dark')
    act(() => { result.current.setTheme('nord') })
    expect(result.current.theme).toBe('nord')
    act(() => { result.current.setTheme('high-contrast') })
    expect(result.current.theme).toBe('high-contrast')
  })

  it('exposes the brightness dimension with 5 stops', () => {
    const { result } = renderHook(() => useTheme())
    // Default is 1 (no change).
    expect(result.current.brightness).toBe(1)
    // Setting writes the CSS variable + persists to localStorage.
    act(() => { result.current.setBrightness(0.85) })
    expect(result.current.brightness).toBe(0.85)
    expect(document.documentElement.style.getPropertyValue('--brightness'))
      .toBe('0.85')
    expect(localStorage.getItem('eva-brightness')).toBe('0.85')
    // Each shipped stop applies cleanly.
    for (const stop of [0.85, 0.95, 1, 1.05, 1.15] as const) {
      act(() => { result.current.setBrightness(stop) })
      expect(document.documentElement.style.getPropertyValue('--brightness'))
        .toBe(String(stop))
    }
  })

  it('reads brightness from localStorage on mount', () => {
    localStorage.setItem('eva-brightness', '1.05')
    const { result } = renderHook(() => useTheme())
    expect(result.current.brightness).toBe(1.05)
  })

  it('rejects invalid brightness values from storage', () => {
    localStorage.setItem('eva-brightness', '99')
    const { result } = renderHook(() => useTheme())
    expect(result.current.brightness).toBe(1)  // back to default
  })

  it('exposes the crimson-dark + slate-pro palettes added for OSS polish', () => {
    // These two themes ship as the "modern engineering dashboard" pair:
    // crimson-dark (dark, red accent) and slate-pro (light, teal
    // accent). Lock them into THEMES so the Settings UI can show them
    // and so any future rename trips a test.
    const { result } = renderHook(() => useTheme())
    act(() => { result.current.setTheme('crimson-dark') })
    expect(result.current.theme).toBe('crimson-dark')
    expect(document.documentElement.getAttribute('data-theme'))
      .toBe('crimson-dark')
    act(() => { result.current.setTheme('slate-pro') })
    expect(result.current.theme).toBe('slate-pro')
    expect(document.documentElement.getAttribute('data-theme'))
      .toBe('slate-pro')
  })

  it('toggle from a non-dark/light palette resets to dark', () => {
    // Not a regression: toggle is the dark<->light shortcut. Other
    // palettes are picked explicitly. From "nord" toggle should land
    // on dark so the shortcut is never a no-op.
    const { result } = renderHook(() => useTheme())
    act(() => { result.current.setTheme('nord') })
    act(() => { result.current.toggle() })
    expect(result.current.theme).toBe('dark')
  })

  // ---- font scale ----

  it('defaults fontScale to 1', () => {
    const { result } = renderHook(() => useTheme())
    expect(result.current.fontScale).toBe(1)
  })

  it('setFontScale persists and writes the CSS variable', () => {
    const { result } = renderHook(() => useTheme())
    act(() => { result.current.setFontScale(1.15) })
    expect(result.current.fontScale).toBe(1.15)
    expect(localStorage.getItem('eva-font-scale')).toBe('1.15')
    expect(document.documentElement.style.getPropertyValue('--font-scale'))
      .toBe('1.15')
  })

  it('reads fontScale from localStorage on mount', () => {
    localStorage.setItem('eva-font-scale', '0.85')
    const { result } = renderHook(() => useTheme())
    expect(result.current.fontScale).toBe(0.85)
  })

  it('rejects invalid fontScale values from storage', () => {
    localStorage.setItem('eva-font-scale', '5.0')  // not in the allowed set
    const { result } = renderHook(() => useTheme())
    expect(result.current.fontScale).toBe(1)  // fallback default
  })

  // ---- density ----

  it('defaults density to normal', () => {
    const { result } = renderHook(() => useTheme())
    expect(result.current.density).toBe('normal')
  })

  it('setDensity writes both CSS var and data attribute', () => {
    const { result } = renderHook(() => useTheme())
    act(() => { result.current.setDensity('compact') })
    expect(document.documentElement.style.getPropertyValue('--gap'))
      .toBe('0.7')
    expect(document.documentElement.getAttribute('data-density'))
      .toBe('compact')
    act(() => { result.current.setDensity('spacious') })
    expect(document.documentElement.style.getPropertyValue('--gap'))
      .toBe('1.4')
  })

  it('density gracefully ignores corrupted localStorage', () => {
    localStorage.setItem('eva-density', 'extreme')
    const { result } = renderHook(() => useTheme())
    expect(result.current.density).toBe('normal')
  })
})
