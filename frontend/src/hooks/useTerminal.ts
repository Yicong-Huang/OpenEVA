import { useRef, useEffect, useCallback } from 'react'
import type { Terminal as TerminalT } from 'xterm'
import type { FitAddon as FitAddonT } from 'xterm-addon-fit'
import { terminalMux } from './terminalMux'

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
 * Strip escape sequences that would change how the BROWSER xterm
 * handles the buffer / the mouse, so the embedded terminal behaves like
 * a normal scrollable, selectable web terminal:
 *
 *   - Alternate screen (`?1049/1047/47`): without stripping, TUI apps
 *     (tmux, the agent) switch to the alt-screen, which has no
 *     scrollback.
 *   - Mouse tracking (`?1000/1001/1002/1003/1004/1005/1006/1015`):
 *     without stripping, the agent's mouse-enable puts xterm into
 *     mouse-report mode, where a plain drag becomes a mouse report
 *     instead of a TEXT SELECTION -- so the user can't highlight/copy.
 *     We keep xterm out of mouse mode (selection works) and instead
 *     drive the agent's own scroll out-of-band via POST /scroll, which
 *     feeds it synthesized wheel reports server-side.
 *
 * The real tmux pane still tracks the agent's mouse-enable, so the
 * server-side wheel forwarding still reaches the agent -- we only
 * suppress mouse mode in the browser xterm.
 */
const TERMINAL_STRIP_RE =
  /\x1b\[\?(?:1049|1047|47|1000|1001|1002|1003|1004|1005|1006|1015)[hl]/g

function filterTerminalBytes(bytes: Uint8Array): Uint8Array {
  // Decoding to latin-1 preserves byte values 0..255 roundtrip.
  let s = ''
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i])
  const filtered = s.replace(TERMINAL_STRIP_RE, '')
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
        // The terminal OWNS the wheel: never let the browser's default
        // scroll bubble out to the parent card list. `stopPropagation`
        // alone isn't enough -- the parent's `overflow-y: auto` scroll
        // is the browser's DEFAULT action, which only `preventDefault`
        // suppresses (hence the listener is `{ passive: false }`).
        // Without this the agent's alt-screen terminal has no xterm
        // scrollback to consume, so the wheel fell through and scrolled
        // the task-node list instead.
        e.preventDefault()
        e.stopPropagation()
        try {
          const buf = term.buffer.active
          // viewportY is the top-of-screen line within the scrollback
          // buffer; baseY is the bottom-most viewport position (i.e.
          // "live tail"). Equality at either end means xterm has no
          // slack in that direction -- drive the tmux scrollback / the
          // agent's own viewport via POST /scroll instead.
          const atTop = buf.viewportY <= 0
          const atBottom = buf.viewportY >= buf.baseY
          const goingUp = e.deltaY < 0
          const goingDown = e.deltaY > 0
          if ((goingUp && atTop) || (goingDown && atBottom)) {
            const dir: 'up' | 'down' = goingUp ? 'up' : 'down'
            // ~1 line per 24px of wheel delta, capped so a trackpad
            // fling doesn't request a huge jump in one call.
            const lines = Math.max(1, Math.min(10,
              Math.round(Math.abs(e.deltaY) / 24)))
            if (dir !== pendingDir) { pendingDir = dir; pendingLines = 0 }
            pendingLines += lines
            flushScroll()
          }
        } catch {
          // term not yet open (race during teardown / SSR-ish init):
          // the preventDefault above already kept the parent from
          // scrolling, which is the important part.
        }
      }
      container.addEventListener('wheel', wheelHandler, { passive: false })

      onStatusChange?.('')
      const apiBase = `/api/terminal/${encodeURIComponent(sessionName)}`

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
        fetch(`${apiBase}/input`, { method: 'POST', body: data })
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
        container.removeEventListener('wheel', wheelHandler)
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

  return { sendInput, termRef, fitRef }
}
