import { useRef, useEffect, useCallback } from 'react'
import type { Terminal as TerminalT } from 'xterm'
import type { FitAddon as FitAddonT } from 'xterm-addon-fit'
import { terminalMux } from './terminalMux'
import {
  stripTerminalTracking,
  TERMINAL_WHEEL_OPTIONS,
  wheelScrollRequest,
} from './useTerminalHelpers'

interface UseTerminalOptions {
  sessionName: string
  containerRef: React.RefObject<HTMLDivElement | null>
  active: boolean
  onStatusChange?: (status: string) => void
}

/** Check if data is a mouse escape sequence (CSI M... or CSI <...) */
function isMouseSequence(data: string): boolean {
  if (data.length >= 3 && data.charCodeAt(0) === 0x1b && data[1] === '[' && data[2] === 'M') return true
  if (data.length >= 3 && data.charCodeAt(0) === 0x1b && data[1] === '[' && data[2] === '<') return true
  return false
}

/**
 * Remove mouse and focus-tracking toggles so browser drag selection remains
 * available. Preserve alternate-screen switches because xterm must mirror
 * the tmux pane's active buffer. Wheel input is forwarded through /scroll.
 */
function filterTerminalBytes(bytes: Uint8Array): Uint8Array {
  // Decoding to latin-1 preserves byte values 0..255 roundtrip.
  let s = ''
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i])
  const filtered = stripTerminalTracking(s)
  const out = new Uint8Array(filtered.length)
  for (let i = 0; i < filtered.length; i++) out[i] = filtered.charCodeAt(i)
  return out
}

export function useTerminal({ sessionName, containerRef, active, onStatusChange }: UseTerminalOptions) {
  const termRef = useRef<TerminalT | null>(null)
  const fitRef = useRef<FitAddonT | null>(null)

  useEffect(() => {
    if (!active || !containerRef.current) return

    const container = containerRef.current
    let cancelled = false
    let cleanup: (() => void) | null = null

    // Dynamic import isolates xterm from the module-load graph so jsdom-based
    // tests (which omit some globals the UMD wrapper probes) can render
    // components that use this hook without crashing.
    ;(async () => {
      const [{ Terminal }, { FitAddon }, { WebLinksAddon }] = await Promise.all([
        import('xterm'),
        import('xterm-addon-fit'),
        import('xterm-addon-web-links'),
        import('xterm/css/xterm.css'),
      ])
      if (cancelled) return

      const term = new Terminal({
        theme: {
          background: '#0a0a12',
          foreground: '#e0e0e0',
          cursor: '#6366f1',
          selectionBackground: 'rgba(99,102,241,0.3)',
        },
        // Information-dense default: small font + tight line-height so a
        // taller session card surfaces as many rows as possible without
        // forcing the user to scroll. xterm renders cell-pixel-perfect
        // at lineHeight 1.0 -- this is the floor.
        fontSize: 10,
        lineHeight: 1.0,
        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
        cursorBlink: true,
        scrollback: 10000,
        scrollOnUserInput: true,
        altClickMovesCursor: false,
        overviewRulerWidth: 0,
      })
      const fit = new FitAddon()
      term.loadAddon(fit)
      term.loadAddon(new WebLinksAddon((_event, uri) => {
        window.open(uri, '_blank', 'noopener,noreferrer')
      }))
      // Markdown-style links `[text](http://...)`: WebLinksAddon only
      // catches the bare URL, so users clicking the `[text]` part get
      // nothing. Register an extra link provider that highlights
      // exactly the `text` span (the URL itself is still clickable via
      // WebLinksAddon).
      const MD_LINK_RE = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g
      term.registerLinkProvider({
        provideLinks(bufferLineNumber, callback) {
          const line = term.buffer.active.getLine(bufferLineNumber - 1)
          if (!line) { callback(undefined); return }
          const text = line.translateToString(true)
          const links: { range: { start: { x: number, y: number }, end: { x: number, y: number } }, text: string, activate: () => void }[] = []
          MD_LINK_RE.lastIndex = 0
          let m: RegExpExecArray | null
          while ((m = MD_LINK_RE.exec(text)) !== null) {
            const linkText = m[1]
            const url = m[2]
            // Range is over the visible `[text]` (cols 1-based incl). xterm
            // counts inclusive end, so end = textStart + linkText.length.
            const textStart = m.index + 1 // skip '['
            const start = textStart + 1   // 1-based
            const end = textStart + linkText.length
            links.push({
              range: {
                start: { x: start, y: bufferLineNumber },
                end:   { x: end,   y: bufferLineNumber },
              },
              text: linkText,
              activate: () => {
                window.open(url, '_blank', 'noopener,noreferrer')
              },
            })
          }
          callback(links.length ? links : undefined)
        },
      })
      term.open(container)
      fit.fit()
      termRef.current = term
      fitRef.current = fit
      const apiBase = `/api/terminal/${encodeURIComponent(sessionName)}`
      const postInput = (data: string) => {
        fetch(`${apiBase}/input`, { method: 'POST', body: data })
      }

      // xterm uses a hidden textarea to receive soft-keyboard input. iOS
      // Safari sometimes reports Return only as a beforeinput line-break and
      // never emits xterm's normal onData("\r"). Translate that event here.
      // The timestamp guard below prevents a double Enter on browsers that do
      // emit both events.
      const helperTextarea = container.querySelector<HTMLTextAreaElement>(
        '.xterm-helper-textarea',
      )
      let lastSoftInput = { data: '', at: 0 }
      const softInputHandler = (event: Event) => {
        const inputEvent = event as InputEvent
        const isEnter = inputEvent.inputType === 'insertLineBreak'
          || inputEvent.inputType === 'insertParagraph'
        const isSpace = inputEvent.inputType === 'insertText'
          && (inputEvent.data === ' ' || inputEvent.data === '\u00a0')
        if (!isEnter && !isSpace) return
        event.preventDefault()
        const data = isEnter ? '\r' : ' '
        lastSoftInput = { data, at: Date.now() }
        postInput(data)
      }
      if (helperTextarea) {
        helperTextarea.setAttribute('enterkeyhint', 'send')
        helperTextarea.setAttribute('autocapitalize', 'none')
        helperTextarea.setAttribute('autocomplete', 'off')
        helperTextarea.addEventListener('beforeinput', softInputHandler)
      }

      // Bubble-phase only: capture-phase stopPropagation kills the
      // event before it reaches xterm's own viewport listener
      // (descendant of `container`), and the terminal stops scrolling.
      // Bubble-phase stopPropagation runs *after* xterm has already
      // handled the wheel, so the in-terminal scroll works AND the
      // event doesn't bubble out to a parent.
      //
      // Boundary pass-through: if the user is wheeling in a direction
      // xterm has NOTHING to scroll (already at the top or bottom of
      // scrollback), let the event bubble up so the parent pane can
      // take it. Without this, a fully-expanded SessionCard fills the
      // pane + locks the parent's `overflow-y: auto` -- the user
      // perceives "can't scroll the page when my cursor is over the
      // terminal".
      // Wheel-to-history: when xterm's own buffer is exhausted in the
      // wheel direction, drive the tmux pane's scrollback via copy-mode
      // instead of letting the wheel escape to the parent pane. The
      // agent's interactive TUI redraws in place (its history lives in
      // tmux, not xterm's buffer), so without this the terminal "won't
      // scroll" and the parent card list jumps instead. Requests are
      // coalesced to one in-flight call so a fast wheel can't flood the
      // backend with tmux subprocesses.
      let scrollInFlight = false
      let pendingLines = 0
      let pendingDir: 'up' | 'down' = 'up'
      const flushScroll = () => {
        if (scrollInFlight || pendingLines <= 0) return
        scrollInFlight = true
        const dir = pendingDir
        const lines = pendingLines
        pendingLines = 0
        fetch(`/api/terminal/${encodeURIComponent(sessionName)}/scroll`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dir, lines }),
        }).catch(() => { /* best-effort */ })
          .finally(() => { scrollInFlight = false; flushScroll() })
      }

      const wheelHandler = (e: WheelEvent) => {
        // Capture the wheel before xterm converts it to cursor keys on an
        // alternate screen, and prevent the parent card list from scrolling.
        e.preventDefault()
        e.stopPropagation()
        const { dir, lines } = wheelScrollRequest(e.deltaY)
        if (dir !== pendingDir) { pendingDir = dir; pendingLines = 0 }
        pendingLines += lines
        flushScroll()
      }
      // Capture runs before xterm's inner listener; non-passive permits
      // preventDefault().
      container.addEventListener('wheel', wheelHandler, TERMINAL_WHEEL_OPTIONS)

      // Touch-to-history for phones/tablets. xterm's native touch handling can
      // only traverse browser-side scrollback; interactive agent TUIs keep
      // most history in their own viewport/tmux. Consume vertical swipes and
      // use xterm scrollback first, then fall through to the same backend
      // scroll endpoint as a desktop wheel at either boundary.
      let lastTouchY: number | null = null
      let touchRemainder = 0
      const touchStartHandler = (event: TouchEvent) => {
        if (event.touches.length !== 1) return
        lastTouchY = event.touches[0].clientY
        touchRemainder = 0
      }
      const touchMoveHandler = (event: TouchEvent) => {
        if (event.touches.length !== 1 || lastTouchY === null) return
        const currentY = event.touches[0].clientY
        const delta = currentY - lastTouchY
        lastTouchY = currentY
        if (Math.abs(delta) < 2) return
        event.preventDefault()
        event.stopPropagation()
        touchRemainder += Math.abs(delta)
        const lines = Math.min(30, Math.floor(touchRemainder / 5))
        if (lines < 1) return
        touchRemainder -= lines * 5

        // Match the expected mobile gesture: swipe upward to reveal older
        // history; swipe downward to return toward the live tail.
        const dir: 'up' | 'down' = delta < 0 ? 'up' : 'down'
        try {
          const buf = term.buffer.active
          const canScrollXterm = dir === 'up'
            ? buf.viewportY > 0
            : buf.viewportY < buf.baseY
          if (canScrollXterm) {
            term.scrollLines(dir === 'up' ? -lines : lines)
            return
          }
        } catch { /* backend fallback below */ }
        if (dir !== pendingDir) { pendingDir = dir; pendingLines = 0 }
        pendingLines += lines
        flushScroll()
      }
      const touchEndHandler = () => {
        lastTouchY = null
        touchRemainder = 0
      }
      // Capture phase is intentional: xterm installs handlers on descendant
      // nodes and may stop the event before it bubbles back to the container.
      container.addEventListener('touchstart', touchStartHandler, { passive: true, capture: true })
      container.addEventListener('touchmove', touchMoveHandler, { passive: false, capture: true })
      container.addEventListener('touchend', touchEndHandler, { passive: true, capture: true })
      container.addEventListener('touchcancel', touchEndHandler, { passive: true, capture: true })

      onStatusChange?.('')
      // Send the current viewport size to the server so the tmux session
      // renders at the right dimensions before any input arrives.
      const pushResize = () => {
        const d = fit.proposeDimensions()
        if (d && d.rows > 0 && d.cols > 0) {
          fetch(`${apiBase}/resize?rows=${d.rows}&cols=${d.cols}`, { method: 'POST' })
        }
      }
      pushResize()

      // Subscribe to the multiplex stream. Two distinct frame kinds
      // arrive on (re)subscribe:
      //   1. `replay: true` (full tmux snapshot). Cold-start case OR
      //      ring-buffer overflow case. We reset xterm so the snapshot
      //      doesn't stack on top of stale bytes.
      //   2. `incremental: true` (ring delta) -- handed to us as a
      //      normal "not replay" frame. The mux already advanced its
      //      lastSeq so we just write the bytes; preserves scrollback
      //      and avoids the visible flash that `term.reset()` causes.
      // Live frames also arrive without `replay`, same path.
      const unsubscribe = terminalMux.subscribe(sessionName, (bytes, replay) => {
        if (replay) {
          // Wipe scrollback + viewport + state. The server still prepends
          // \x1b[H\x1b[2J\x1b[3J for defence-in-depth, but term.reset()
          // is what guarantees no leftover bytes survive a reconnect.
          try { term.reset() } catch { /* xterm not yet open in tests */ }
        }
        term.write(filterTerminalBytes(bytes))
      })
      term.focus()

      term.onData((data) => {
        // Drop any stray mouse report. We strip mouse-tracking-enable
        // from the output stream (see filterTerminalBytes) so xterm
        // never grabs the mouse -- that keeps plain-drag text selection
        // working in the browser. Wheel scrolling is handled out-of-band
        // by `wheelHandler` -> POST /scroll instead.
        if (isMouseSequence(data)) return
        if (data === lastSoftInput.data && Date.now() - lastSoftInput.at < 100) return
        postInput(data)
      })

      const observer = new ResizeObserver(() => {
        fit.fit()
        pushResize()
      })
      observer.observe(container)

      const refitTimer = setTimeout(() => {
        fit.fit()
        pushResize()
      }, 500)

      cleanup = () => {
        clearTimeout(refitTimer)
        observer.disconnect()
        unsubscribe()
        // Capture must match the addEventListener call.
        container.removeEventListener('wheel', wheelHandler,
                                      TERMINAL_WHEEL_OPTIONS)
        container.removeEventListener('touchstart', touchStartHandler, true)
        container.removeEventListener('touchmove', touchMoveHandler, true)
        container.removeEventListener('touchend', touchEndHandler, true)
        container.removeEventListener('touchcancel', touchEndHandler, true)
        helperTextarea?.removeEventListener('beforeinput', softInputHandler)
        term.dispose()
        termRef.current = null
        fitRef.current = null
      }
    })()

    return () => {
      cancelled = true
      if (cleanup) cleanup()
    }
  }, [active, sessionName]) // eslint-disable-line react-hooks/exhaustive-deps

  const sendInput = useCallback((text: string) => {
    fetch(`/api/terminal/${encodeURIComponent(sessionName)}/input`, { method: 'POST', body: text })
  }, [sessionName])

  const scrollHistory = useCallback((direction: 'up' | 'down', lines = 30) => {
    fetch(`/api/terminal/${encodeURIComponent(sessionName)}/scroll`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dir: direction, lines }),
    })
  }, [sessionName])

  return { sendInput, scrollHistory, termRef, fitRef }
}
