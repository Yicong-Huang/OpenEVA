import type { Ticket } from '../api'
import { timeAgo } from '../utils'
import {
  colorForStatus, colorForPriority, colorForIssueType,
} from '../utils/ticketColors'

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


export function TicketNode({ ticket, active, onClick }: Props) {
  const statusColor = colorForStatus(ticket.status)
  const priorityColor = colorForPriority(ticket.priority)
  const typeColor = colorForIssueType(ticket.issue_type)

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
        borderLeft: `3px solid ${typeColor.fg}`,
        outline: active ? '1px solid var(--accent)' : undefined,
        borderRadius: 6,
        padding: '6px 8px 7px 9px',
        cursor: 'pointer',
        background: 'var(--card-bg)',
        display: 'flex', flexDirection: 'column', gap: 3,
      }}
    >
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap',
      }}>
        {ticket.issue_type && (
          <span data-testid={`ticket-type-${ticket.key}`}
                title={ticket.issue_type}
                style={{
                  fontSize: 9, padding: '0 5px', borderRadius: 3,
                  background: typeColor.bg, color: typeColor.fg,
                  fontWeight: 700, letterSpacing: 0.3,
                  textTransform: 'uppercase', whiteSpace: 'nowrap',
                }}>{ticket.issue_type}</span>
        )}
        <span style={{
          fontSize: 11, fontFamily: 'monospace', color: 'var(--accent)',
          fontWeight: 700,
        }}>{ticket.key}</span>
        <span style={{
          fontSize: 9, padding: '0 6px', borderRadius: 3,
          background: statusColor.bg, color: statusColor.fg,
          fontWeight: 600, whiteSpace: 'nowrap',
        }}>{ticket.status || 'unknown'}</span>
        {ticket.priority && (
          <span data-testid={`ticket-priority-${ticket.key}`}
                title={ticket.priority}
                style={{
                  fontSize: 9, padding: '0 5px', borderRadius: 3,
                  background: priorityColor.bg, color: priorityColor.fg,
                  fontWeight: 700,
                }}>{shortPriority(ticket.priority)}</span>
        )}
        <span style={{
          fontSize: 9, color: 'var(--text-faint)', marginLeft: 'auto',
        }}>{timeAgo(ticket.updated_at)}</span>
      </div>

      <div style={{
        fontSize: 12.5, fontWeight: 500, color: 'var(--text)',
        lineHeight: 1.3,
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
