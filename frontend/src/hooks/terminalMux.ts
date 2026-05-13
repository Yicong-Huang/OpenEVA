/**
 * Shared terminal multiplex client.
 *
 * One EventSource (`/api/terminals/stream`) carries frames for every open
 * terminal in this tab. This dodges the browser's HTTP/1.1 6-connection cap
 * that would otherwise make 6+ simultaneously open terminals unusable (stuck
 * EventSource + queued POST /input -> can't type).
 *
 * Each `useTerminal` hook subscribes with `subscribe(name, handler)` and the
 * server immediately pushes a `tmux capture-pane` replay frame so refresh no
 * longer loses scrollback history.
 *
 * Fairness: the server owns the fan-out; each client has a bounded queue and
 * drops the oldest frame on overflow, so one noisy terminal cannot stall
 * another's stream.
 */

type Handler = (data: Uint8Array, replay: boolean) => void

interface Frame {
  name: string
  data: string         // base64-encoded raw PTY bytes
  replay?: boolean     // true for a full tmux scrollback snapshot
  incremental?: boolean // true for a ring-buffer delta on resubscribe
  seq?: number         // monotonic per-session sequence number
}

interface Hello {
  type: 'hello'
  client_id: string
}

/** Decode a base64 chunk into a Uint8Array suitable for xterm.write(). */
function b64ToBytes(b64: string): Uint8Array {
  const raw = atob(b64)
  const out = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

class TerminalMux {
  private clientId: string | null = null
  private handlers = new Map<string, Handler>()
  // Sessions the caller asked to subscribe to before the hello frame arrived.
  private pendingSubscribes = new Set<string>()
  // Sessions currently subscribed on the server.
  private activeSubscribes = new Set<string>()
  // Last seq seen per session. On (re)subscribe we send this so the
  // server can replay only the delta (lossless reconnect) instead of
  // a full tmux snapshot (which forces a terminal reset). Survives
  // EventSource auto-reconnect because we never clear it on disconnect.
  private lastSeq = new Map<string, number>()
  private openPromise: Promise<void> | null = null
  private resolveOpen: (() => void) | null = null

  /** Lazily open the shared SSE. Safe to call repeatedly. */
  private connect(): Promise<void> {
    if (this.openPromise) return this.openPromise
    this.openPromise = new Promise<void>((resolve) => {
      this.resolveOpen = resolve
      const source = new EventSource('/api/terminals/stream')
      source.onmessage = (evt) => this.handleMessage(evt.data)
      source.onerror = () => {
        // EventSource auto-retries; clear client_id so next hello re-subs.
        this.clientId = null
        // Mark all previously-active subscriptions as pending so they
        // re-subscribe after reconnect (and receive a fresh replay).
        for (const name of this.activeSubscribes) this.pendingSubscribes.add(name)
        this.activeSubscribes.clear()
      }
    })
    return this.openPromise
  }

  private handleMessage(raw: string) {
    let payload: Hello | Frame
    try {
      payload = JSON.parse(raw)
    } catch {
      return
    }
    if ('type' in payload && payload.type === 'hello') {
      this.clientId = payload.client_id
      if (this.resolveOpen) {
        this.resolveOpen()
        this.resolveOpen = null
      }
      // Flush any subscribes the caller queued before we had a client_id.
      for (const name of this.pendingSubscribes) this.sendSubscribe(name)
      return
    }
    if ('name' in payload && 'data' in payload) {
      const handler = this.handlers.get(payload.name)
      if (!handler) return
      // Track the highest seq we've seen so a future resubscribe can
      // ask the server for a delta. Snapshot replay frames also carry
      // seq (= ring's current next_seq - 1) so the seq monotonically
      // advances past everything in the snapshot.
      if (typeof payload.seq === 'number') {
        const prev = this.lastSeq.get(payload.name) ?? 0
        if (payload.seq > prev) this.lastSeq.set(payload.name, payload.seq)
      }
      // The handler distinguishes "replay" (full snapshot, reset
      // xterm) from incremental / live (just write). Incremental
      // delta frames look like normal live output to the handler.
      handler(b64ToBytes(payload.data), Boolean(payload.replay))
    }
  }

  private async sendSubscribe(name: string) {
    if (!this.clientId) return
    try {
      const sinceSeq = this.lastSeq.get(name) ?? 0
      const resp = await fetch('/api/terminals/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          client_id: this.clientId, name, since_seq: sinceSeq,
        }),
      })
      if (resp.ok) {
        this.activeSubscribes.add(name)
        this.pendingSubscribes.delete(name)
      }
    } catch {
      // Leave in pendingSubscribes; reconnect handler will retry.
    }
  }

  private async sendUnsubscribe(name: string) {
    if (!this.clientId) return
    try {
      await fetch('/api/terminals/unsubscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: this.clientId, name }),
      })
    } catch {
      // Server forgets on stream disconnect anyway.
    }
    this.activeSubscribes.delete(name)
    this.pendingSubscribes.delete(name)
  }

  /** Subscribe to a session's frames. Returns an unsubscribe function. */
  subscribe(name: string, handler: Handler): () => void {
    this.handlers.set(name, handler)
    this.connect()
    if (this.clientId) {
      this.sendSubscribe(name)
    } else {
      this.pendingSubscribes.add(name)
    }
    return () => this.unsubscribe(name)
  }

  /** Drop a subscription. No-op if never subscribed. */
  unsubscribe(name: string) {
    this.handlers.delete(name)
    this.sendUnsubscribe(name)
  }
}

// Singleton -- one SSE per browser tab, reused across every useTerminal hook.
export const terminalMux = new TerminalMux()
