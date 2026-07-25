import type { Ticket } from '../api'
import { timeAgo } from '../utils'
import {
  colorForStatus, colorForPriority, colorForIssueType,
  colorForSeverity, shortSeverity,
} from '../utils/ticketColors'
import { useSessionState } from '../hooks/SessionStatusProvider'
import { isLive } from '../utils/sessionState'
import { SessionDot } from './SessionDot'

/**
 * TicketNode -- a single row in the All Tickets queue (left pane).
 *
 * Naming: in the tickets UI we use "node / node / card" by position
 * (left list / middle session / right detail), matching the
 * conventions on All PRs and All Reviews. This component IS the
 * ticket node.
 *
 * Visual model:
 *   - 3px colored left bar keyed off issue_type, so "is this a bug
 *     vs a story vs an epic" reads in a single glance even before
 *     the user scans the chip row.
 *   - Row 1: type chip + key + status chip + priority chip + (right)
 *     time-ago. Each chip is color-coded by its corresponding helper
 *     in `utils/ticketColors`.
 *   - Row 2: summary, two-line clamp (so long titles don't blow up
 *     the queue but the user still sees enough context).
 *   - Row 3 (optional): meta crumbs -- assignee local-part, first
 *     few components, and parent key. Only renders when at least
 *     one of these is present, so simple tickets stay compact.
 *
 * Pure presentation: parent owns selection state and click routing.
 */

interface Props {
  ticket: Ticket
  active: boolean
  onClick: () => void
}

/** Compress JIRA priority strings so the chip stays narrow.
 * "Highest" / "Critical" -> P0; "High" -> P1; "Medium" -> P2;
 * "Low" -> P3; "Lowest" -> P4. P-style strings pass through. */
function shortPriority(priority: string): string {
  const p = priority.toLowerCase()
  if (/^p\d/i.test(priority)) return priority.toUpperCase()
  if (p.includes('highest') || p.includes('critical')) return 'P0'
  if (p.includes('high')) return 'P1'
  if (p.includes('medium')) return 'P2'
  if (p.includes('lowest')) return 'P4'
  if (p.includes('low')) return 'P3'
  return priority
}


/** A ticket that left its instance JQL (resolved or reassigned away)
 * carries a `left_jql_at` stamp. Render a badge so the user SEES the
 * transition instead of the ticket silently vanishing. We key the
 * label off `status_category`: JIRA's `done` bucket -> "Resolved",
 * anything else (still open but no longer mine) -> "Reassigned". */
function leftJqlBadge(t: Ticket): { label: string; color: { bg: string; fg: string } } | null {
  if (!t.left_jql_at) return null
  if ((t.status_category || '').toLowerCase() === 'done') {
    return { label: 'Resolved', color: { bg: 'rgba(34,197,94,0.18)', fg: 'var(--green)' } }
  }
  return { label: 'Reassigned', color: { bg: 'rgba(168,162,158,0.22)', fg: 'var(--text-dim)' } }
}


/** JIRA's default priority ("Major") carries no signal -- it's on
 * nearly every ticket. Treat it as noise and don't render a chip for
 * it, so the rare genuinely-prioritised ticket (Blocker / Critical /
 * P0) actually stands out. */
function isNoisyPriority(priority: string): boolean {
  return (priority || '').trim().toLowerCase() === 'major'
}

export function TicketNode({ ticket, active, onClick }: Props) {
  const statusColor = colorForStatus(ticket.status)
  const typeColor = colorForIssueType(ticket.issue_type)
  const severityColor = colorForSeverity(ticket.severity || '')
  const badge = leftJqlBadge(ticket)

  // Live agent-session state for this ticket, read from the global
  // session-status snapshot keyed by the ticket's tmux session name.
  // When a session is live we mark the row so the queue makes "this
  // ticket is a live task" obvious at a glance (the #recognition gap).
  const liveRow = useSessionState(ticket.session_name)
  const sessionLive = isLive(liveRow?.state)

  // Triage chip: Severity wins when present (the real triage axis on
  // enterprise JIRA). Otherwise fall back to Priority -- but only when
  // it actually means something (suppress the ubiquitous "Major").
  const showSeverity = !!ticket.severity
  const showPriority = !showSeverity && !!ticket.priority
    && !isNoisyPriority(ticket.priority)
  const priorityColor = colorForPriority(ticket.priority)

  // Meta crumbs: only the bits that aren't already in the chip row.
  // Strip the @domain off the assignee email so the row stays tight;
  // a tooltip on the row carries the full address for power users.
  const assigneeShort = ticket.assignee_email
    ? ticket.assignee_email.split('@')[0]
    : ''
  const components = (ticket.components ?? []).slice(0, 2)
  const hasMeta = !!(assigneeShort || components.length || ticket.parent_key)

  return (
    <div
      data-testid={`ticket-row-${ticket.key}`}
      onClick={onClick}
      title={ticket.assignee_email || undefined}
      style={{
        border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
        // A 4px type-coloured rail down the left edge -- the primary
        // at-a-glance "what kind of ticket is this" cue when scanning
        // the queue. Backed by the type-aware colour map.
        borderLeft: `4px solid ${typeColor.fg}`,
        outline: active ? '1px solid var(--accent)' : undefined,
        borderRadius: 6,
        padding: '7px 9px 8px 10px',
        cursor: 'pointer',
        // Active rows get a faint wash in the ticket's own type colour
        // so the selection reads as "this kind of thing", not a generic
        // highlight.
        background: active ? typeColor.bg : 'var(--card-bg)',
        display: 'flex', flexDirection: 'column', gap: 4,
      }}
    >
      {/* Row 1 -- identity (left: what is this) + state (right: where
          is it). type + key + triage chip cluster left; status + age
          right. A leading SessionDot marks tickets with a live agent
          session so they read as "live tasks" in the queue. */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap',
      }}>
        {sessionLive && (
          <SessionDot state={liveRow?.state} size={8}
                      testid={`ticket-live-${ticket.key}`}
                      style={{ marginRight: 1 }} />
        )}
        {ticket.issue_type && (
          <span data-testid={`ticket-type-${ticket.key}`}
                title={ticket.issue_type}
                style={{
                  fontSize: 9.5, padding: '1px 6px', borderRadius: 3,
                  background: typeColor.bg, color: typeColor.fg,
                  fontWeight: 700, letterSpacing: 0.2,
                  whiteSpace: 'nowrap',
                }}>{ticket.issue_type}</span>
        )}
        <span style={{
          fontSize: 11, fontFamily: 'monospace', color: 'var(--accent)',
          fontWeight: 700,
        }}>{ticket.key}</span>
        {showSeverity && (
          <span data-testid={`ticket-severity-${ticket.key}`}
                title={`Severity: ${ticket.severity}`}
                style={{
                  fontSize: 9.5, padding: '1px 6px', borderRadius: 3,
                  background: severityColor.bg, color: severityColor.fg,
                  fontWeight: 800, whiteSpace: 'nowrap',
                  border: `1px solid ${severityColor.fg}`,
                }}>{shortSeverity(ticket.severity || '')}</span>
        )}
        {showPriority && (
          <span data-testid={`ticket-priority-${ticket.key}`}
                title={ticket.priority}
                style={{
                  fontSize: 9, padding: '0 5px', borderRadius: 3,
                  background: priorityColor.bg, color: priorityColor.fg,
                  fontWeight: 700, whiteSpace: 'nowrap',
                }}>{shortPriority(ticket.priority)}</span>
        )}
        <span style={{ marginLeft: 'auto', display: 'flex',
                       alignItems: 'center', gap: 6 }}>
          {badge && (
            <span data-testid={`ticket-leftjql-${ticket.key}`}
                  title={`Left your queue at ${ticket.left_jql_at}`}
                  style={{
                    fontSize: 9, padding: '0 5px', borderRadius: 3,
                    background: badge.color.bg, color: badge.color.fg,
                    fontWeight: 700, whiteSpace: 'nowrap',
                  }}>{badge.label}</span>
          )}
          <span style={{
            fontSize: 9.5, padding: '1px 6px', borderRadius: 3,
            background: statusColor.bg, color: statusColor.fg,
            fontWeight: 600, whiteSpace: 'nowrap',
          }}>{ticket.status || 'unknown'}</span>
          <span style={{ fontSize: 9, color: 'var(--text-faint)',
                         whiteSpace: 'nowrap' }}>
            {timeAgo(ticket.updated_at)}
          </span>
        </span>
      </div>

      {/* Row 2 -- the hero. Summary is what actually distinguishes one
          ticket from the next, so it gets the most ink: full contrast,
          a touch larger, two-line clamp. */}
      <div style={{
        fontSize: 13, fontWeight: 600, color: 'var(--text)',
        lineHeight: 1.35,
        display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
        overflow: 'hidden', textOverflow: 'ellipsis',
        wordBreak: 'break-word',
      }}>{ticket.summary}</div>

      {hasMeta && (
        <div data-testid={`ticket-meta-${ticket.key}`}
             style={{
               display: 'flex', alignItems: 'center', gap: 6,
               fontSize: 9, color: 'var(--text-dim)',
               overflow: 'hidden', textOverflow: 'ellipsis',
               whiteSpace: 'nowrap',
             }}>
          {assigneeShort && (
            <span data-testid={`ticket-assignee-${ticket.key}`}
                  style={{ fontFamily: 'monospace' }}>
              @{assigneeShort}
            </span>
          )}
          {components.length > 0 && (
            <>
              {assigneeShort && <span style={{ color: 'var(--text-faint)' }}>{'·'}</span>}
              <span data-testid={`ticket-components-${ticket.key}`}
                    style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {components.join(', ')}
                {(ticket.components?.length ?? 0) > components.length && '+'}
              </span>
            </>
          )}
          {ticket.parent_key && (
            <>
              {(assigneeShort || components.length > 0) && (
                <span style={{ color: 'var(--text-faint)' }}>{'·'}</span>
              )}
              <span data-testid={`ticket-parent-${ticket.key}`}
                    style={{
                      fontFamily: 'monospace', color: 'var(--accent)',
                    }}>
                ^{ticket.parent_key}
              </span>
            </>
          )}
        </div>
      )}
    </div>
  )
}
