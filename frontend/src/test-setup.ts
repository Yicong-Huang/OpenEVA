import '@testing-library/jest-dom/vitest'

// xterm (used transitively via SessionCard/TaskCard) references the `self`
// global that browsers expose but jsdom does not. Alias it to window to keep
// module top-level imports from crashing.
if (typeof globalThis.self === 'undefined') {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).self = globalThis
}

// jsdom doesn't implement Element.prototype.scrollTo; the endless task list
// calls it when recentering a selected card.
if (typeof Element !== 'undefined' && typeof Element.prototype.scrollTo !== 'function') {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (Element.prototype as any).scrollTo = function () { /* no-op in tests */ }
}

// jsdom lacks ResizeObserver; SessionsPage observes selected card size changes.
if (typeof globalThis.ResizeObserver === 'undefined') {
  class MockResizeObserver {
    observe = () => {}
    unobserve = () => {}
    disconnect = () => {}
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).ResizeObserver = MockResizeObserver
}

// jsdom lacks EventSource; hooks that open SSE streams crash at subscribe time.
// Provide a no-op stub so components can mount and be asserted against.
if (typeof globalThis.EventSource === 'undefined') {
  class MockEventSource {
    static CONNECTING = 0
    static OPEN = 1
    static CLOSED = 2
    readyState = 0
    url: string
    onmessage: ((e: MessageEvent) => void) | null = null
    onerror: ((e: Event) => void) | null = null
    onopen: ((e: Event) => void) | null = null
    constructor(url: string) { this.url = url }
    addEventListener = () => {}
    removeEventListener = () => {}
    close = () => { this.readyState = 2 }
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).EventSource = MockEventSource
}
