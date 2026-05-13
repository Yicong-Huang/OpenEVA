import type { Task, PR } from '../types'

/** Build a human-readable status summary for a task. */
export function taskStatusSummary(
  status: string,
  prs: PR[],
  deps: string[],
  tasks: Record<string, Task>,
  ticket: { id?: string | null } | null,
  hasTickets: boolean,
): string {
  if (status === 'closed') return '\u5DF2\u5173\u95ED'
  if (status === 'needs_follow_up') return 'Needs follow-up'
  if (status === 'done') {
    if (prs.length > 0) return `PR #${prs[prs.length - 1].number} \u5DF2\u5408\u5E76`
    return '\u5DF2\u5B8C\u6210'
  }
  if (status === 'blocked') {
    const blockers: string[] = []
    for (const d of deps) {
      const dep = tasks[d]
      if (!dep || dep.status !== 'done') blockers.push(d)
    }
    if (blockers.length > 0) return `\u88AB ${blockers[0]} \u963B\u585E`
    return '\u88AB\u4F9D\u8D56\u963B\u585E'
  }
  if (status === 'in_review') {
    const openPr = prs.find((p) => p.status === 'open')
    if (openPr) return `PR #${openPr.number} \u5BA1\u67E5\u4E2D`
    return '\u5BA1\u67E5\u4E2D'
  }
  if (status === 'in_progress') {
    const openPr = prs.find((p) => p.status === 'open')
    if (openPr) return `PR #${openPr.number} \u5F00\u53D1\u4E2D`
    return '\u5F00\u53D1\u4E2D'
  }
  // not_started
  if (hasTickets && (!ticket || !ticket.id)) return '\u5F85\u5F00\u59CB\uFF0C\u9700\u521B\u5EFA ticket'
  return '\u5F85\u5F00\u59CB'
}

/** A dependency is "unblocking" iff its status is one of these.
 * MUST stay in lockstep with `eva_db.UNBLOCKING_DEP_STATUSES` --
 * `tests/test_eva_cli.py::test_frontend_unblocking_set_matches_backend`
 * cross-checks the two. */
export const UNBLOCKING_DEP_STATUSES: ReadonlySet<string> =
  new Set(['done', 'closed', 'needs_follow_up'])


/** Tasks in any of these states are "finished" -- the work that
 * tracked them is complete (`done`) or abandoned/superseded
 * (`closed`). Multiple call sites used to inline
 * `status === 'done' || status === 'closed'`; centralised here so a
 * future state addition (e.g. `'archived'`) lands in one place. */
export const TERMINAL_TASK_STATUSES: ReadonlySet<string> =
  new Set(['done', 'closed'])


/** Convenience predicate over `TERMINAL_TASK_STATUSES`. Tolerates
 * undefined / empty so callers that pass `task?.status` don't have
 * to nullcheck before invoking. */
export function isTerminalTaskStatus(status: string | undefined | null): boolean {
  return !!status && TERMINAL_TASK_STATUSES.has(status)
}


/** Check if a task is blocked by any unclosed dependency.
 *  A missing-dep edge counts as blocking (data inconsistency). */
export function isTaskBlocked(taskId: string, tasks: Record<string, Task>): boolean {
  const task = tasks[taskId]
  if (!task) return false
  const deps = task.dependencies || []
  for (const d of deps) {
    const dep = tasks[d]
    if (!dep) return true
    if (!UNBLOCKING_DEP_STATUSES.has(dep.status)) return true
  }
  return false
}

/** Classify a task_history / review_history entry by its leading shape.
 *
 * Backend auto-appends three bounded prefixes on every state transition
 * (see core.tasks._record_status_transition and core.prs write sites):
 *   - "status: X -> Y"     -> state-machine flip
 *   - "linked PR #N"        -> task gained a PR link
 *   - "PR #N merged"        -> PR went from open/draft to merged
 * Anything else is user-authored ("manual") -- eva-cli append-history
 * and the Review action-prompt hook both call the same append path but
 * the user's free-form text doesn't start with those tokens.
 *
 * Pure function -- safe for render paths and unit tests. Kept ASCII-only
 * on purpose so code search and diff tooling stay plain.
 */
export type HistoryEntryKind = 'status' | 'pr_linked' | 'pr_merged' | 'manual'

export function classifyHistoryEntry(text: string | undefined | null): HistoryEntryKind {
  if (!text) return 'manual'
  const t = text.trimStart()
  if (t.startsWith('status:')) return 'status'
  if (t.startsWith('linked PR')) return 'pr_linked'
  // Match "PR #<digits> merged" anywhere near the start. The backend
  // emits the exact form "PR #<N> merged" but be tolerant of leading
  // spaces and trailing annotations.
  if (/^PR\s+#\d+\s+merged\b/.test(t)) return 'pr_merged'
  return 'manual'
}

/** Map each kind to a CSS color var so TaskCard / ReviewCard stay
 * consistent. Returning undefined means "inherit" (no marker). */
export function historyKindColor(kind: HistoryEntryKind): string | undefined {
  switch (kind) {
    case 'status':    return 'var(--blue)'
    case 'pr_linked': return 'var(--green)'
    case 'pr_merged': return 'var(--purple)'
    default:          return undefined
  }
}

/** Evaluate whether an action's condition is met. */
export function evalActionCondition(
  condition: string | undefined | null,
  data: { ci_status?: string; prs?: PR[]; pr_number?: number },
): boolean {
  if (!condition) return true
  switch (condition) {
    case 'ci_failed':
      return data.ci_status === 'failure'
    case 'has_pr':
      return !!(data.prs && data.prs.length > 0) || !!data.pr_number
    case 'has_open_pr':
      return !!(data.prs && data.prs.some((p) => p.status === 'open'))
    default:
      return true
  }
}
