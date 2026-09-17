export const TERMINAL_WHEEL_OPTIONS = {
  passive: false,
  capture: true,
} as const

const TERMINAL_STRIP_RE =
  /\x1b\[\?(?:1000|1001|1002|1003|1004|1005|1006|1015)[hl]/g

export function stripTerminalTracking(text: string): string {
  return text.replace(TERMINAL_STRIP_RE, '')
}

export function wheelScrollRequest(deltaY: number): {
  dir: 'up' | 'down'
  lines: number
} {
  return {
    dir: deltaY < 0 ? 'up' : 'down',
    lines: Math.max(1, Math.min(10, Math.round(Math.abs(deltaY) / 24))),
  }
}
