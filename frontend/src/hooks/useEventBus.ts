import { useEffect, useRef, useCallback } from 'react'

type EventHandler = (event: Record<string, unknown>) => void
type ConnectListener = (initial: boolean) => void

// Global SSE connection + subscriber registry
const subscribers = new Map<string, Set<EventHandler>>()
const connectListeners = new Set<ConnectListener>()
let source: EventSource | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
// Tracks whether we've ever opened the SSE before. Distinguishes the
// first `onopen` (initial connect) from subsequent ones (reconnect),
// so the SessionStatusProvider can decide whether to refetch the
// snapshot to recover from any events missed while disconnected.
let hasConnected = false

function dispatch(eventType: string, event: Record<string, unknown>) {
  // Exact match
  const exact = subscribers.get(eventType)
  if (exact) exact.forEach((fn) => fn(event))

  // Wildcard match: "github.*" matches "github.comment"
  const prefix = eventType.split('.')[0] + '.*'
  const wild = subscribers.get(prefix)
  if (wild) wild.forEach((fn) => fn(event))

  // Global catch-all
  const all = subscribers.get('*')
  if (all) all.forEach((fn) => fn(event))
}

function notifyConnect(initial: boolean) {
  connectListeners.forEach((fn) => {
    try { fn(initial) } catch { /* ignore listener errors */ }
  })
}

function ensureConnection() {
  if (source && source.readyState !== EventSource.CLOSED) return

  source = new EventSource('/api/events/stream')

  source.onopen = () => {
    const initial = !hasConnected
    hasConnected = true
    notifyConnect(initial)
  }

  source.onmessage = (evt) => {
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(evt.data)
    } catch {
      return
    }
    const eventType = typeof parsed.type === 'string' ? parsed.type : ''
    dispatch(eventType, parsed)
  }

  source.onerror = () => {
    if (source) {
      source.close()
      source = null
    }
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(ensureConnection, 3000)
  }
}

function subscribe(pattern: string, handler: EventHandler) {
  if (!subscribers.has(pattern)) subscribers.set(pattern, new Set())
  subscribers.get(pattern)!.add(handler)
  ensureConnection()
  return () => {
    const set = subscribers.get(pattern)
    if (set) {
      set.delete(handler)
      if (set.size === 0) subscribers.delete(pattern)
    }
  }
}

function subscribeConnect(listener: ConnectListener) {
  connectListeners.add(listener)
  // Make sure the SSE connection is open even if no event subscribers
  // exist yet. The SessionStatusProvider mounts before any consumer
  // and wants the connection up immediately.
  ensureConnection()
  // If we're already connected when this listener registers, fire
  // synchronously so the listener doesn't need a separate "is
  // already connected?" check on its first paint.
  if (source && source.readyState === EventSource.OPEN && hasConnected) {
    try { listener(false) } catch { /* ignore */ }
  }
  return () => {
    connectListeners.delete(listener)
  }
}

/**
 * Subscribe to SSE events by type pattern.
 * - "auth.cert_expired" -> exact match
 * - "github.*" -> all github events
 * - "*" -> all events
 */
/** Reset global state. For testing only. */
export function _resetEventBus() {
  if (source) { source.close(); source = null }
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  subscribers.clear()
  connectListeners.clear()
  hasConnected = false
}

export function useEventBus(pattern: string, handler: (event: Record<string, unknown>) => void) {
  const handlerRef = useRef(handler)
  handlerRef.current = handler

  const stableHandler = useCallback((event: Record<string, unknown>) => {
    handlerRef.current(event)
  }, [])

  useEffect(() => {
    return subscribe(pattern, stableHandler)
  }, [pattern, stableHandler])
}

/**
 * Subscribe to SSE (re)connect signals. Listener fires once on
 * initial connect and again on every reconnect.
 *
 * Originally used by `SessionStatusProvider` to refetch its snapshot
 * on reconnect, but the backend now replays the snapshot via
 * `session.snapshot.begin / .end` events on every connect, so the
 * provider doesn't need this. Kept as exported infra for any future
 * consumer that wants to do "on reconnect" work.
 */
export function useSseConnect(listener: (initial: boolean) => void) {
  const listenerRef = useRef(listener)
  listenerRef.current = listener

  const stable = useCallback((initial: boolean) => {
    listenerRef.current(initial)
  }, [])

  useEffect(() => {
    return subscribeConnect(stable)
  }, [stable])
}
