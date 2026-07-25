/**
 * Pure classification helpers for the Tickets page tabs + groups.
 *
 * Tabs split by "is this still my work" (assignee vs the configured
 * JIRA instance emails) and JIRA's normalised status_category.
 * Groups split each tab by ticket kind: the label-driven semantic
 * groups (Flaky tests / Performance) first, then everything else falls
 * back to the ticket's own JIRA project prefix -- no project key is
 * hardcoded, so any org's prefixes group naturally. Kept free of React
 * so vitest covers them as plain functions.
 */
import type { Ticket } from '../api'

export type TicketTab = 'open' | 'in_progress' | 'resolved' | 'triaged'

/** Tab order for rendering. Triaged sits last -- it's the "no longer
 * my work" archive, not the actionable queue. */
export const TICKET_TABS: Array<{ key: TicketTab; label: string }> = [
  { key: 'open', label: 'Open' },
  { key: 'in_progress', label: 'In Progress' },
  { key: 'resolved', label: 'Resolved' },
  { key: 'triaged', label: 'Triaged' },
]

/** Collect "who am I" from the configured JIRA instances. Only the
 * basic-auth instances carry an email; bearer instances contribute
 * nothing (their tickets are excluded by the backend prefix rule
 * anyway). */
export function myEmails(
  instances: Array<{ email: string }>,
): Set<string> {
  const out = new Set<string>()
  for (const i of instances) {
    const e = (i.email || '').trim().toLowerCase()
    if (e) out.add(e)
  }
  return out
}

export function isUnassigned(t: Ticket): boolean {
  return !(t.assignee_email || '').trim()
}

/** Tab classification. Anything not assigned to me (including
 * unassigned) is Triaged -- it was in my queue once (the JQL is
 * assignee = currentUser()) but is no longer my work. My tickets
 * split by JIRA's normalised status_category; an empty category
 * (never-synced row) falls back to Open until the next sync. */
export function tabForTicket(t: Ticket, mine: Set<string>): TicketTab {
  const assignee = (t.assignee_email || '').trim().toLowerCase()
  if (!assignee || !mine.has(assignee)) return 'triaged'
  const cat = (t.status_category || '').toLowerCase()
  if (cat === 'done') return 'resolved'
  if (cat === 'indeterminate') return 'in_progress'
  return 'open'
}

export interface TicketGroup {
  name: string
  /** Lower sorts first when rendering groups inside a tab. */
  priority: number
}

// Kind matchers. Substring/regex-based because labels vary
// ('testman-automation', 'benchmarking-regression',
// 'release-1.2-perf-not-triaged', ...). Precedence: Flaky ->
// Performance -> the ticket's own project prefix.
const FLAKY_LABEL_RE = /flaky|testman|test-?failure/i
const FLAKY_SUMMARY_RE = /flaky|test failure|failing target/i
const PERF_LABEL_RE = /benchmark|regression-detection|perf-not-triaged/i
const PERF_SUMMARY_RE = /\b(regression|benchmark(ing)?|perf)\b/i

/** The JIRA project prefix from a ticket key: `ABC-123` -> `ABC`.
 * Falls back to `project_key`, then a generic 'Other' label so a
 * malformed key never produces an empty group heading. */
function projectPrefix(t: Ticket): string {
  const key = t.key || ''
  const dash = key.indexOf('-')
  if (dash > 0) return key.slice(0, dash)
  return (t.project_key || '').trim() || 'Other'
}

export function kindGroupForTicket(t: Ticket): TicketGroup {
  const labels = t.labels ?? []
  const summary = t.summary || ''
  if (labels.some((l) => FLAKY_LABEL_RE.test(l))
      || FLAKY_SUMMARY_RE.test(summary)) {
    return { name: 'Flaky tests', priority: 1 }
  }
  if (labels.some((l) => PERF_LABEL_RE.test(l))
      || PERF_SUMMARY_RE.test(summary)) {
    return { name: 'Performance', priority: 2 }
  }
  // Everything else groups under its own project prefix, sorted after
  // the semantic groups. No prefix is special-cased.
  return { name: projectPrefix(t), priority: 3 }
}

/** Group used for rendering inside a given tab. The Triaged tab
 * carries an extra pinned-to-top Unassigned group so tickets nobody
 * owns are impossible to miss. */
export function groupForTicket(t: Ticket, tab: TicketTab): TicketGroup {
  if (tab === 'triaged' && isUnassigned(t)) {
    return { name: 'Unassigned', priority: -1 }
  }
  return kindGroupForTicket(t)
}
