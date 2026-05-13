/**
 * Shared constants, types, and pure helpers used by the task-graph visualizations.
 *
 * Extracted out of GraphView.tsx so React Fast Refresh can keep component hot-
 * reload intact (fast-refresh requires a component file to export ONLY
 * components -- constants and helpers must live in a plain TS module).
 */

// Red is reserved for "needs me right now" (session needs_input /
// needs_permission). not_started uses purple so a wall of un-started
// tasks doesn't drown out the actual urgent signal.
export const STATUS_COLORS: Record<string, string> = {
  done: 'var(--green)',
  needs_follow_up: 'var(--orange)',
  in_review: 'var(--yellow)',
  in_progress: 'var(--blue)',
  not_started: 'var(--purple)',
  blocked: 'var(--text-faint)',
  closed: 'var(--text-dim)',
}

// Card width grows with task id length (see GraphView::computeWidth)
// so the id always fits on a single line. NODE_W is just the floor
// for short ids so the visual rhythm doesn't shrink to nothing.
//
// Both mini and full share row 1 (task id + PR count) verbatim --
// expand / collapse feels like the rest of the rows sliding in or
// out, not a different card. The colored border-left already
// conveys status, so row 1 doesn't need a status dot.
//
// Full layout:
//   row 1 (header):  task id ............. [N PRs]   -- identical to mini
//   row 2 (history): latest task_history entry, wraps up to 2 lines
//   row 3 (meta):    time ago ........... [type pill]
//   row 4 (footer):  o status ............ ticket pill o claude
// Width: deliberately fixed (not dynamic-per-id). Uniform width
// trumps "always fits the longest id" -- a ragged grid was visually
// noisy. Longer ids ellipsis-truncate, with the full string still
// available via the title attribute.
export const NODE_W = 240
export const NODE_H = 88
// Mini width inherits the same dynamic computation as full so the
// same task occupies the same horizontal slot in both modes.
export const MINI_H = 22

export interface TaskNodeData {
  taskId: string
  status: string
  /** Type tag (feature / fix / bug / ...). Shown as a pill in the
   * header so the user can spot category at a glance. */
  type?: string | null
  /** ISO timestamp of last update -- rendered as "5m ago" / "2d ago"
   * in the footer so the user can spot stale tasks. */
  updatedAt?: string | null
  /** Most recent `task_history` entry, if any. The graph card surfaces
   * this instead of `notes` / `description` so users see recent
   * activity directly. */
  latestHistory?: { ts: string; text: string } | null
  /** Live agent session status (idle / thinking / starting /
   * needs_input / needs_permission / stopped / ''). Drives a small
   * coloured dot on the card matching the TaskNodeChip palette. */
  sessionStatus?: string
  isMini: boolean
  isExpanded: boolean
  isSelected: boolean
  ticketId: string | null
  ticketUrl: string | null
  prCount: number
  /** CI status of the most-recently-updated PR on the task. Drives
   * the PR pill background colour: 'success' -> green, 'failure' ->
   * red, anything else (pending / running / unknown) -> yellow.
   * Empty / undefined falls back to the neutral badge palette. */
  latestPrCiStatus?: string
  hasTickets: boolean
  hasSession: boolean
  highlighted: boolean | null  // null = no highlighting active
  /** True when this task was created in the last NEW_BADGE_TTL_MS
   * window. Drives the "NEW" badge in TaskNode. */
  isNewlyCreated?: boolean
  /** True when another node is being dragged over this one and the
   * 2-second hover-to-link arm timer is running. Drives a pulsing
   * accent border so the user knows "drop here to link". */
  isDropTarget?: boolean
  /** Hide React Flow target/source handles. The same TaskNode
   * component renders on the All Live Tasks page (flat list, no
   * graph), where edge ports are noise. */
  hideHandles?: boolean
  onSelect: (taskId: string) => void
  onToggleExpand: (taskId: string) => void
  [key: string]: unknown
}

/** Pick the most-recently-updated PR's CI status. Returns '' when
 *  the task has no PRs. The "latest" definition uses `last_updated`
 *  ISO string lexically, which is correct for ISO-8601 timestamps. */
export function latestPrCiStatus(
  prs: ReadonlyArray<{ ci_status?: string; last_updated?: string }>,
): string {
  if (!prs || prs.length === 0) return ''
  let best: { ci_status?: string; last_updated?: string } | null = null
  for (const p of prs) {
    if (!best || (p.last_updated || '') > (best.last_updated || '')) {
      best = p
    }
  }
  return best?.ci_status || ''
}


/** Tailwind-ish background + foreground colour pair for a CI status
 *  pill, with a subtle border so the pill reads as a token. Returns
 *  null for unknown / empty statuses (caller uses neutral palette). */
export function ciPillStyle(
  ci: string,
): { bg: string; fg: string; border: string } | null {
  if (!ci) return null
  if (ci === 'success') {
    return {
      bg: 'color-mix(in srgb, var(--green) 18%, transparent)',
      fg: 'var(--green)',
      border: 'color-mix(in srgb, var(--green) 45%, transparent)',
    }
  }
  if (ci === 'failure' || ci === 'failed') {
    return {
      bg: 'color-mix(in srgb, var(--red) 18%, transparent)',
      fg: 'var(--red)',
      border: 'color-mix(in srgb, var(--red) 45%, transparent)',
    }
  }
  // Anything else (pending / running / queued / unknown) reads as
  // "in flight" -- yellow.
  return {
    bg: 'color-mix(in srgb, var(--yellow) 18%, transparent)',
    fg: 'var(--yellow)',
    border: 'color-mix(in srgb, var(--yellow) 45%, transparent)',
  }
}


/** Read a numeric CSS variable with a fallback. */
export function getThemeOpacity(varName: string, fallback: number): number {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim()
    return v ? parseFloat(v) : fallback
  } catch {
    return fallback
  }
}
