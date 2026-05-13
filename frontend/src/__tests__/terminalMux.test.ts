/**
 * Tests for terminalMux: single shared EventSource across many terminals.
 *
 * We stub EventSource + fetch so no HTTP is made. Each test imports the
 * module fresh via `vi.resetModules()` so the internal singleton starts
 * clean.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// --- Test doubles ----------------------------------------------------------

type MsgHandler = (evt: { data: string }) => void

class FakeEventSource {
  static instances: FakeEventSource[] = []
  onmessage: MsgHandler | null = null
  onerror: (() => void) | null = null
  url: string
  readyState = 0
  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  emit(data: string) { this.onmessage?.({ data }) }
  error() { this.onerror?.() }
  close() { this.readyState = 2 }
}

function b64(s: string): string {
  return btoa(s)
}

async function freshMux() {
  vi.resetModules()
  return (await import('../hooks/terminalMux')).terminalMux
}

// --- Tests -----------------------------------------------------------------

describe('terminalMux', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }))
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('subscribing before hello frame does not POST; hello triggers flush', async () => {
    const mux = await freshMux()
    const handler = vi.fn()

    mux.subscribe('sess-1', handler)

    // Before the hello frame, fetch must NOT have been called.
    expect(fetch).not.toHaveBeenCalled()

    // Server sends hello.
    const es = FakeEventSource.instances[0]
    es.emit(JSON.stringify({ type: 'hello', client_id: 'abc123' }))

    // Now the pending subscribe is flushed via POST.
    await vi.waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/terminals/subscribe',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            client_id: 'abc123', name: 'sess-1', since_seq: 0,
          }),
        }),
      )
    })
  })

  it('delivers frames to the correct handler by session name', async () => {
    const mux = await freshMux()
    const handlerA = vi.fn()
    const handlerB = vi.fn()

    mux.subscribe('sess-a', handlerA)
    mux.subscribe('sess-b', handlerB)

    const es = FakeEventSource.instances[0]
    es.emit(JSON.stringify({ type: 'hello', client_id: 'c' }))

    es.emit(JSON.stringify({ name: 'sess-a', data: b64('hello') }))
    es.emit(JSON.stringify({ name: 'sess-b', data: b64('world') }))

    expect(handlerA).toHaveBeenCalledTimes(1)
    expect(handlerB).toHaveBeenCalledTimes(1)
    // handlerA payload == "hello" bytes
    const bytesA = handlerA.mock.calls[0][0] as Uint8Array
    expect(Array.from(bytesA)).toEqual([...new TextEncoder().encode('hello')])
  })

  it('marks replay frames distinctly', async () => {
    const mux = await freshMux()
    const handler = vi.fn()
    mux.subscribe('sess-x', handler)

    const es = FakeEventSource.instances[0]
    es.emit(JSON.stringify({ type: 'hello', client_id: 'c' }))
    es.emit(JSON.stringify({ name: 'sess-x', data: b64('prior'), replay: true }))
    es.emit(JSON.stringify({ name: 'sess-x', data: b64('live') }))

    expect(handler).toHaveBeenCalledTimes(2)
    expect(handler.mock.calls[0][1]).toBe(true)   // replay flag on first
    expect(handler.mock.calls[1][1]).toBe(false)  // no replay flag on live
  })

  it('unsubscribe removes handler and POSTs unsubscribe', async () => {
    const mux = await freshMux()
    const handler = vi.fn()

    const unsub = mux.subscribe('sess-u', handler)
    const es = FakeEventSource.instances[0]
    es.emit(JSON.stringify({ type: 'hello', client_id: 'c' }))

    // Let the pending subscribe flush.
    await Promise.resolve()
    unsub()

    // Subsequent frames for that session must NOT reach the handler.
    es.emit(JSON.stringify({ name: 'sess-u', data: b64('ignored') }))
    expect(handler).not.toHaveBeenCalled()

    await vi.waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/terminals/unsubscribe',
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })

  it('opens only one EventSource no matter how many terminals subscribe', async () => {
    const mux = await freshMux()
    mux.subscribe('a', vi.fn())
    mux.subscribe('b', vi.fn())
    mux.subscribe('c', vi.fn())
    mux.subscribe('d', vi.fn())
    expect(FakeEventSource.instances.length).toBe(1)
  })

  it('ignores malformed messages without crashing', async () => {
    const mux = await freshMux()
    const handler = vi.fn()
    mux.subscribe('sess', handler)

    const es = FakeEventSource.instances[0]
    // Valid hello first so internal state is live.
    es.emit(JSON.stringify({ type: 'hello', client_id: 'c' }))
    // Now send garbage + unknown shape.
    es.emit('not json')
    es.emit(JSON.stringify({ unknown: 'shape' }))

    // Valid frame after garbage still works.
    es.emit(JSON.stringify({ name: 'sess', data: b64('ok') }))
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('on SSE error, active subscriptions are queued to re-subscribe', async () => {
    const mux = await freshMux()
    const handler = vi.fn()
    mux.subscribe('sess', handler)

    const es = FakeEventSource.instances[0]
    es.emit(JSON.stringify({ type: 'hello', client_id: 'c1' }))
    await Promise.resolve()
    ;(fetch as unknown as { mockClear: () => void }).mockClear()

    // Simulate SSE reconnect.
    es.error()

    // When the new hello arrives, the session must be re-subscribed.
    es.emit(JSON.stringify({ type: 'hello', client_id: 'c2' }))

    await vi.waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/terminals/subscribe',
        expect.objectContaining({
          body: JSON.stringify({
            client_id: 'c2', name: 'sess', since_seq: 0,
          }),
        }),
      )
    })
  })

  it('tracks last-seen seq per session and resubscribes with delta', async () => {
    // Lossless reconnect contract: the mux records the highest seq
    // it's seen for each session and sends it as `since_seq` on
    // re-subscribe so the server can replay only the delta.
    const mux = await freshMux()
    mux.subscribe('sess-seq', vi.fn())
    const es = FakeEventSource.instances[0]
    es.emit(JSON.stringify({ type: 'hello', client_id: 'c1' }))
    await Promise.resolve()
    // Seed a few frames; each carries its server-assigned seq.
    es.emit(JSON.stringify({ name: 'sess-seq', data: b64('a'), seq: 5 }))
    es.emit(JSON.stringify({ name: 'sess-seq', data: b64('b'), seq: 6 }))
    es.emit(JSON.stringify({ name: 'sess-seq', data: b64('c'), seq: 7 }))
    // Drop the connection -> mux re-queues the subscription as pending.
    ;(fetch as unknown as { mockClear: () => void }).mockClear()
    es.error()
    es.emit(JSON.stringify({ type: 'hello', client_id: 'c2' }))
    await vi.waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/terminals/subscribe',
        expect.objectContaining({
          body: JSON.stringify({
            client_id: 'c2', name: 'sess-seq', since_seq: 7,
          }),
        }),
      )
    })
  })

  it('does not regress lastSeq when a frame arrives out of order', async () => {
    // If the server's incremental replay queues a delta out of order
    // (seq 6 arriving after seq 9 because of the queue's drop-oldest),
    // we keep the highest -- never going backwards. A future
    // resubscribe still asks for seq=9 onwards.
    const mux = await freshMux()
    mux.subscribe('sess-mono', vi.fn())
    const es = FakeEventSource.instances[0]
    es.emit(JSON.stringify({ type: 'hello', client_id: 'cid' }))
    await Promise.resolve()
    es.emit(JSON.stringify({ name: 'sess-mono', data: b64('x'), seq: 9 }))
    // Out of order arrival; smaller seq must NOT clobber the high water.
    es.emit(JSON.stringify({ name: 'sess-mono', data: b64('y'), seq: 6 }))
    ;(fetch as unknown as { mockClear: () => void }).mockClear()
    es.error()
    es.emit(JSON.stringify({ type: 'hello', client_id: 'cid2' }))
    await vi.waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/terminals/subscribe',
        expect.objectContaining({
          body: JSON.stringify({
            client_id: 'cid2', name: 'sess-mono', since_seq: 9,
          }),
        }),
      )
    })
  })

  it('subscribe AFTER hello frame POSTs immediately (no pending queue)', async () => {
    // The subscribe code path differs depending on whether the
    // hello-frame already arrived: pre-hello goes into
    // `pendingSubscribes` (flushed later); post-hello goes straight
    // through `sendSubscribe`. The existing tests cover the
    // pre-hello path; this one covers the post-hello path.
    const mux = await freshMux()
    // Drive a connection + hello first.
    mux.subscribe('warmup', vi.fn())
    const es = FakeEventSource.instances[0]
    es.emit(JSON.stringify({ type: 'hello', client_id: 'cid-pre' }))
    await vi.waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/terminals/subscribe',
        expect.objectContaining({
          body: JSON.stringify({
            client_id: 'cid-pre', name: 'warmup', since_seq: 0,
          }),
        }),
      )
    })
    ;(fetch as unknown as { mockClear: () => void }).mockClear()
    // Now subscribe AFTER hello -- hits sendSubscribe directly.
    mux.subscribe('post-hello', vi.fn())
    await vi.waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/terminals/subscribe',
        expect.objectContaining({
          body: JSON.stringify({
            client_id: 'cid-pre', name: 'post-hello', since_seq: 0,
          }),
        }),
      )
    })
    // No new EventSource was opened -- shared connection reused.
    expect(FakeEventSource.instances.length).toBe(1)
  })
})
