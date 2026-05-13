import type { Ticket } from '../api'

/**
 * Action-button registry for the Tickets page.
 *
 * Each action declares:
 *  - id           Unique identifier; used as the button's data-testid.
 *  - label        Button text shown to the user.
 *  - description  Tooltip explaining what the action will do.
 *  - match        Predicate against a Ticket; controls whether the
 *                 button shows. Match logic is intentionally permissive
 *                 (substring on labels / issue_type / summary) because
 *                 JIRA conventions vary across projects.
 *  - prompt       Template for the prompt sent to a fresh agent
 *                 session. `{key}` and `{summary}` are substituted
 *                 client-side before POST.
 *
 * The registry is plain data so future iters can move it to settings
 * (let users define their own per-team actions) without restructuring
 * call sites. For now, a small built-in set covers the two cases the
 * user called out -- flaky-test fix, perf-regression bisect -- plus a
 * generic "investigate" fallback.
 */
export interface TicketAction {
  id: string
  label: string
  description: string
  match: (t: Ticket) => boolean
  prompt: string
}


const lower = (s: string | undefined | null) => (s || '').toLowerCase()

const matchesAny = (haystack: string[], needles: string[]) => {
  const all = haystack.map(lower)
  return needles.some((n) => all.some((h) => h.includes(n)))
}


export const TICKET_ACTIONS: TicketAction[] = [
  {
    id: 'fix-flaky-test',
    label: 'Fix flaky test',
    description: 'Open an agent session and ask it to investigate + fix this flaky test.',
    match: (t) => {
      const labels = (t.labels || []).map(lower)
      const summary = lower(t.summary)
      return matchesAny(labels, ['flaky', 'flake', 'flakiness'])
        || summary.includes('flaky')
        || summary.includes('flake')
    },
    prompt:
      'Investigate flaky test for ticket {key}: "{summary}". '
      + 'Reproduce the failure locally if possible, find the root cause, '
      + 'and propose a fix. Cite the runs you looked at.',
  },
  {
    id: 'bisect-perf-regression',
    label: 'Bisect regression',
    description: 'Open an agent session and ask it to bisect a performance regression for this ticket.',
    match: (t) => {
      const labels = (t.labels || []).map(lower)
      const summary = lower(t.summary)
      const it = lower(t.issue_type)
      return matchesAny(labels, ['perf', 'performance', 'regression', 'slow'])
        || summary.includes('regression')
        || summary.includes('slower')
        || it.includes('performance')
    },
    prompt:
      'Bisect a performance regression for ticket {key}: "{summary}". '
      + 'Identify the commit range, run the relevant benchmark before/after, '
      + 'and report the suspected commit with timings.',
  },
  {
    id: 'reproduce-bug',
    label: 'Reproduce bug',
    description: 'Set up a minimal repro for this bug.',
    match: (t) => {
      const it = lower(t.issue_type)
      return it === 'bug' || it === 'defect'
    },
    prompt:
      'Reproduce bug {key}: "{summary}". '
      + 'Build a minimal example that triggers the failure, '
      + 'then save it as a regression test scaffold.',
  },
  {
    id: 'investigate',
    label: 'Investigate',
    description: 'Open an agent session and ask it to dig into this ticket\'s context.',
    // Generic fallback -- always available.
    match: () => true,
    prompt:
      'Investigate ticket {key}: "{summary}". '
      + 'Skim related code, prior PRs, and history. Summarise what you find '
      + 'and propose next steps.',
  },
]


export function applicableActions(ticket: Ticket): TicketAction[] {
  return TICKET_ACTIONS.filter((a) => a.match(ticket))
}


export function renderPrompt(action: TicketAction, ticket: Ticket): string {
  return action.prompt
    .replaceAll('{key}', ticket.key)
    .replaceAll('{summary}', ticket.summary || ticket.key)
}
