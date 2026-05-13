// Stub the useTerminal hook so tests don't pull in xterm (UMD wrapper probes
// `self`, which jsdom does not expose).
interface Opts {
  sessionName: string
  containerRef: { current: HTMLElement | null }
  active: boolean
  onStatusChange?: (s: string | null) => void
}
export function useTerminal(_opts: Opts): void {
  // no-op in tests
}
