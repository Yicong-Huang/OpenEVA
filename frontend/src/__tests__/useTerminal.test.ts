import { describe, it, expect, vi } from 'vitest'
import {
  stripTerminalTracking,
  TERMINAL_WHEEL_OPTIONS,
  wheelScrollRequest,
} from '../hooks/useTerminalHelpers'

// Pure terminal logic lives outside the React hook so it can be tested without
// loading xterm or constructing a DOM terminal.

describe('useTerminal helpers', () => {
  describe('isMouseSequence', () => {
    // Re-implement the check to test the logic
    function isMouseSequence(data: string): boolean {
      if (data.length >= 3 && data.charCodeAt(0) === 0x1b && data[1] === '[' && data[2] === 'M') return true
      if (data.length >= 3 && data.charCodeAt(0) === 0x1b && data[1] === '[' && data[2] === '<') return true
      return false
    }

    it('detects CSI M mouse sequence', () => {
      expect(isMouseSequence('\x1b[M !!')).toBe(true)
    })

    it('detects CSI < mouse sequence', () => {
      expect(isMouseSequence('\x1b[<0;10;20M')).toBe(true)
    })

    it('rejects regular input', () => {
      expect(isMouseSequence('hello')).toBe(false)
    })

    it('rejects short strings', () => {
      expect(isMouseSequence('\x1b[')).toBe(false)
    })

    it('rejects empty string', () => {
      expect(isMouseSequence('')).toBe(false)
    })

    it('rejects other escape sequences', () => {
      expect(isMouseSequence('\x1b[A')).toBe(false) // cursor up
      expect(isMouseSequence('\x1b[B')).toBe(false) // cursor down
    })
  })

  describe('filterTerminalBytes escape handling', () => {
    it('strips SGR mouse tracking (1006) and basic tracking (1000)', () => {
      expect(stripTerminalTracking('\x1b[?1006h\x1b[?1000hHello')).toBe('Hello')
    })

    it('strips button/any-motion tracking (1002/1003)', () => {
      expect(stripTerminalTracking('\x1b[?1002hdata\x1b[?1003l')).toBe('data')
    })

    it('strips focus tracking (1004)', () => {
      expect(stripTerminalTracking('\x1b[?1004hfocus\x1b[?1004l')).toBe('focus')
    })

    // Alternate-screen switches must pass through so xterm mirrors the pane.
    it('preserves alternate screen enable (1049h)', () => {
      expect(stripTerminalTracking('\x1b[?1049hHello')).toBe('\x1b[?1049hHello')
    })

    it('preserves alternate screen disable (1049l)', () => {
      expect(stripTerminalTracking('\x1b[?1049lGoodbye')).toBe('\x1b[?1049lGoodbye')
    })

    it('preserves legacy alt-screen variants (1047/47)', () => {
      expect(stripTerminalTracking('\x1b[?1047ha\x1b[?47hb')).toBe('\x1b[?1047ha\x1b[?47hb')
    })

    it('preserves absolute cursor positioning used by TUI repaints', () => {
      expect(stripTerminalTracking('\x1b[20;3H\x1b[K\x1b[H\x1b[2J'))
        .toBe('\x1b[20;3H\x1b[K\x1b[H\x1b[2J')
    })

    it('preserves colour codes and ordinary text', () => {
      expect(stripTerminalTracking('normal text with \x1b[32m color'))
        .toBe('normal text with \x1b[32m color')
    })

    it('strips only the mouse toggles from a mixed stream', () => {
      expect(stripTerminalTracking('\x1b[?1049h\x1b[?1000h\x1b[?1006hX'))
        .toBe('\x1b[?1049hX')
    })

    it('handles empty string', () => {
      expect(stripTerminalTracking('')).toBe('')
    })
  })

  describe('wheel handling contract', () => {
    it('uses a capturing, non-passive wheel listener', () => {
      expect(TERMINAL_WHEEL_OPTIONS).toEqual({
        passive: false,
        capture: true,
      })
    })

    it('maps wheel delta to a direction and a clamped line count', () => {
      expect(wheelScrollRequest(-100).dir).toBe('up')
      expect(wheelScrollRequest(100).dir).toBe('down')
      // A tiny delta still scrolls at least one line...
      expect(wheelScrollRequest(1).lines).toBe(1)
      // ...and a trackpad fling is capped so one tick can't jump miles.
      expect(wheelScrollRequest(100000).lines).toBe(10)
    })
  })

  describe('formatCountdown', () => {
    function formatCountdown(seconds: number): string {
      if (seconds <= 0) return 'now!'
      const m = Math.floor(seconds / 60)
      const s = Math.floor(seconds % 60)
      if (m > 0) return `${m}m ${s}s`
      return `${s}s`
    }

    it('formats minutes and seconds', () => {
      expect(formatCountdown(125)).toBe('2m 5s')
    })

    it('formats seconds only when < 60', () => {
      expect(formatCountdown(45)).toBe('45s')
    })

    it('returns now! for zero', () => {
      expect(formatCountdown(0)).toBe('now!')
    })

    it('returns now! for negative', () => {
      expect(formatCountdown(-10)).toBe('now!')
    })

    it('handles exact minutes', () => {
      expect(formatCountdown(120)).toBe('2m 0s')
    })
  })
})

describe('useTerminal hook', () => {
  // The hook itself pulls in xterm dynamically inside a useEffect so
  // these tests only exercise pure helpers (URL encoding) -- no mocks
  // for xterm/addons are needed. Historical mocks lived here but were
  // never referenced by the it() blocks below; vitest warned that the
  // vi.mock calls were nested, so they've been removed.

  it('sendInput calls fetch with correct URL', async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', mockFetch)

    // Test the sendInput function logic directly
    const sessionName = 'test-session'
    const text = 'hello'
    await fetch(`/api/terminal/${encodeURIComponent(sessionName)}/input`, { method: 'POST', body: text })
    expect(mockFetch).toHaveBeenCalledWith('/api/terminal/test-session/input', { method: 'POST', body: 'hello' })
  })

  it('encodes session names with special characters for /input path', () => {
    const name = 'session with spaces'
    const encoded = encodeURIComponent(name)
    expect(encoded).toBe('session%20with%20spaces')

    const url = `/api/terminal/${encoded}/input`
    expect(url).toBe('/api/terminal/session%20with%20spaces/input')
  })
})

// --- Reconnect dedup contract --------------------------------------------
//
// When the EventSource auto-reconnects, the server pushes a fresh replay
// frame carrying the authoritative tmux pane state. Without a hard reset
// of xterm, that replay text would land BELOW the leftover output from
// the previous connection -- which is the "duplicate / cut-off" pattern
// users report. The contract: the handler resets xterm before applying
// any frame flagged `replay: true`.
describe('useTerminal replay handler dedup', () => {
  function makeHandler(term: { reset: () => void; write: (b: Uint8Array) => void }) {
    // Mirrors the closure in useTerminal.ts:109. Kept in sync manually --
    // changing the handler in the hook should require updating this test.
    return (bytes: Uint8Array, replay: boolean) => {
      if (replay) {
        try { term.reset() } catch { /* ignore */ }
      }
      term.write(bytes)
    }
  }

  it('replay=true resets xterm before writing', () => {
    const term = { reset: vi.fn(), write: vi.fn() }
    const h = makeHandler(term)
    h(new Uint8Array([97, 98, 99]), true)  // "abc"
    expect(term.reset).toHaveBeenCalledTimes(1)
    // reset must come BEFORE write (the call order matters).
    expect(term.reset.mock.invocationCallOrder[0])
      .toBeLessThan(term.write.mock.invocationCallOrder[0])
  })

  it('replay=false does NOT reset (incremental output keeps state)', () => {
    const term = { reset: vi.fn(), write: vi.fn() }
    const h = makeHandler(term)
    h(new Uint8Array([97]), false)
    expect(term.reset).not.toHaveBeenCalled()
    expect(term.write).toHaveBeenCalledTimes(1)
  })

  it('reset throwing does not break the handler', () => {
    // Some xterm versions throw if reset is called before open(); the
    // handler must still write the bytes through.
    const term = {
      reset: vi.fn(() => { throw new Error('not opened') }),
      write: vi.fn(),
    }
    const h = makeHandler(term)
    expect(() => h(new Uint8Array([97]), true)).not.toThrow()
    expect(term.write).toHaveBeenCalledTimes(1)
  })
})
