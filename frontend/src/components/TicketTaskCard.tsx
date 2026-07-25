import { useCallback, useState } from 'react'
import type { Ticket } from '../api'
import type { TaskStatus } from '../types'
import { StatusDot } from './StatusDot'
import { SessionCard } from './SessionCard'
import { TicketLink } from './TicketLink'
import { PRNode } from './PRNode'
import { useSessionState } from '../hooks/SessionStatusProvider'
import { isLive } from '../utils/sessionState'
import { applicableActions, renderPrompt } from '../utils/ticketActions'
import { classifyHistoryEntry, historyKindColor } from '../utils/taskHelpers'
import { formatLocalShort } from '../utils'
import { api } from '../api'

/**
 * TicketTaskCard -- middle pane of the Tickets page (and the inline
 * card used in All Live Tasks for live tickets).
 *
 * Per the naming convention, the user calls this surface the
 * "Task Card" for tickets -- a visual sibling to the project task
 * `TaskCard`. We deliberately reuse TaskCard's CSS classes
 * (`task-card`, `task-card-header`, `task-card-title`, `task-card-body`,
 * `task-card-field`, `action-bar`, `st-{status}`) and child components
 * (StatusDot, SessionCard, TicketLink) so both cards render identically.
 *
 * Differences from project TaskCard:
 *   - Data shape is `Ticket`, not `Task`. JIRA status strings get
 *     mapped onto our compact TaskStatus palette so the chrome
 *     coloring works.
 *   - Session opens via the ticket-mode endpoint
 *     (`/api/tickets/{key}/session`) directly, not through
 *     `useSessionLauncher`. The launcher's value-add is the
 *     wait-ready + paste flow; for tickets the backend already
 *     handles the paste, and we want errors surfaced INLINE rather
 *     than in a modal alert.
 *   - The action set is the ticket-action registry
 *     (`utils/ticketActions.ts`).
 *
 * Right pane stays as `TicketCard` -- the JIRA detail view (transitions,
 * comments, triage, etc.). Action buttons used to live there but moved
 * here so the "card with the session" owns its action buttons, matching
 * the project Task Card / Review Card pattern.
 */

interface Props {
  ticket: Ticket
}

/** Map a JIRA status string onto the compact TaskStatus palette so
 * task-card-st-* CSS rules paint the right border color. Substring
 * match because workflows vary across projects. */
function mapStatus(jiraStatus: string): TaskStatus {
  const s = (jiraStatus || '').toLowerCase()
  if (s.includes('done') || s.includes('closed') || s.includes('resolved')) {
    return 'done'
  }
  if (s.includes('progress') || s.includes('review')
      || s.includes('coding') || s.includes('testing')) {
    return 'in_progress'
  }
  if (s.includes('block')) return 'blocked'
  return 'not_started'
}


export function TicketTaskCard({ ticket }: Props) {
  const sessionName = ticket.instance_name
    ? `ticket-${ticket.instance_name}-${ticket.key}`
    : `ticket-${ticket.key}`
  // Live state comes from the global session-status snapshot. The
  // service's snapshot is the source of truth -- patched live by
  // SSE, refetched on reconnect. Local state only tracks the
  // "Opening..." spinner + transient action feedback.
  const sessionRow = useSessionState(sessionName)
  const present = sessionRow ? isLive(sessionRow.state) : null
  const [opening, setOpening] = useState(false)
  const [openError, setOpenError] = useState<string | null>(null)
  const [busyActionId, setBusyActionId] = useState<string | null>(null)
  const [doneActionId, setDoneActionId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  // Open / kill / action handlers delegate to the API only. The
  // snapshot service picks up the resulting state via SSE -- we
  // don't manually toggle a local `present` flag any more.
  const onOpen = useCallback(async () => {
    setOpening(true)
    setOpenError(null)
    try {
      await api.openTicketSession(ticket.key, {
        instanceName: ticket.instance_name,
      })
    } catch (e) {
      setOpenError(e instanceof Error ? e.message : 'Open failed')
    } finally {
      setOpening(false)
    }
  }, [ticket.key, ticket.instance_name])

  const onKill = useCallback(async () => {
    try {
      await api.killSession(sessionName)
    } catch { /* SSE corrects on next event */ }
  }, [sessionName])

  const onAction = useCallback(async (actionId: string, customPrompt: string) => {
    setBusyActionId(actionId)
    setActionError(null)
    setDoneActionId(null)
    try {
      await api.openTicketSession(ticket.key, {
        instanceName: ticket.instance_name,
        customPrompt,
      })
      setDoneActionId(actionId)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Action failed')
    } finally {
      setBusyActionId(null)
    }
  }, [ticket.key, ticket.instance_name])

  const status = mapStatus(ticket.status)
  const actions = applicableActions(ticket)
  const updatedShort = (ticket.updated_at || '').substring(0, 10)
  // A ticket IS a task row, so the detail view carries the same
  // task-keyed history + PRs as a project TaskCard. Populated by the
  // single-ticket fetch (getTicket / trackTicket).
  const history = ticket.history ?? []
  const prs = ticket.prs ?? []

  return (
    <div data-testid="ticket-task-card" className={`task-card st-${status}`}>
      <div className="task-card-header">
        <div className="task-card-title">
          <StatusDot status={status} style={{ verticalAlign: 'middle', marginRight: 6 }} />
          {ticket.key}
          {' '}
          <TicketLink
            ticketKey={ticket.key}
            fallbackUrl={ticket.url}
            style={{ fontSize: 11, fontWeight: 400 }}
          >
            [{ticket.key}]
          </TicketLink>
        </div>
        <span className="task-card-type">{ticket.issue_type || ''}</span>
      </div>

      <div className="task-card-group">
        {ticket.project_key || ticket.category || ''}
        {updatedShort && (
          <span style={{ marginLeft: 'auto', fontSize: 9, color: 'var(--text-faint)' }}>
            {updatedShort}
          </span>
        )}
      </div>

      {ticket.summary && (
        <div style={{
          fontSize: 12, color: 'var(--text)', marginBottom: 8, lineHeight: 1.4,
        }}>
          {ticket.summary}
        </div>
      )}

      <div className="task-card-body">
        <div className="task-card-field">
          <span className="label">Status</span>
          <span className="value">{ticket.status || 'unknown'}</span>
        </div>
        {ticket.priority && (
          <div className="task-card-field">
            <span className="label">Priority</span>
            <span className="value">{ticket.priority}</span>
          </div>
        )}
        {ticket.assignee_email && (
          <div className="task-card-field">
            <span className="label">Assignee</span>
            <span className="value" style={{ fontFamily: 'monospace' }}>
              {ticket.assignee_email}
            </span>
          </div>
        )}
        {ticket.parent_key && (
          <div className="task-card-field">
            <span className="label">Parent</span>
            <span className="value" style={{ fontFamily: 'monospace' }}>
              {ticket.parent_key}
            </span>
          </div>
        )}
        {history.length > 0 && (
          <div className="task-card-field" style={{ alignItems: 'flex-start' }}>
            <span className="label">History</span>
            <span className="value" style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 10 }}>
              {history.slice(0, 5).map((e, i) => {
                const kind = classifyHistoryEntry(e.text)
                const dotColor = historyKindColor(kind)
                return (
                  <span key={`${e.ts}-${i}`}
                        data-history-kind={kind}
                        style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    {dotColor && (
                      <span aria-hidden="true" title={kind.replace('_', ' ')}
                            style={{ width: 6, height: 6, borderRadius: '50%',
                                     background: dotColor, flexShrink: 0 }} />
                    )}
                    <span style={{ color: 'var(--text-faint)', whiteSpace: 'nowrap',
                                   fontFamily: 'monospace' }} title={e.ts}>
                      {formatLocalShort(e.ts)}
                    </span>
                    <span style={{ color: kind === 'manual' ? 'var(--text-dim)' : 'var(--text)' }}>
                      {e.text}
                    </span>
                  </span>
                )
              })}
              {history.length > 5 && (
                <span style={{ color: 'var(--text-faint)', fontSize: 9 }}>
                  + {history.length - 5} older
                </span>
              )}
            </span>
          </div>
        )}
      </div>

      {/* Related PRs -- same task-keyed prs table as project tasks. */}
      {prs.length > 0 && (
        <div data-testid="ticket-task-prs" className="task-card-prs">
          {prs.map((pr) => (
            <PRNode key={pr.number} pr={pr} showMeta />
          ))}
        </div>
      )}

      {!present && !opening && (
        <div data-testid="open-agent">
          <button
            className="btn-action accent"
            data-testid="ticket-open-session"
            style={{ width: '100%', margin: '4px 0', padding: 6 }}
            onClick={onOpen}
          >
            <img
              src="/static/claude-favicon.ico"
              width={12}
              height={12}
              style={{ verticalAlign: 'middle', marginRight: 4 }}
              alt=""
            />
            Open Agent
          </button>
        </div>
      )}
      {opening && (
        <div style={{
          fontSize: 11, color: 'var(--accent)', padding: '6px 0', textAlign: 'center',
        }}>
          Opening session...
        </div>
      )}
      {openError && (
        <div data-testid="ticket-session-error" style={{
          fontSize: 11, color: 'var(--red)', marginTop: 4,
        }}>
          {openError}
        </div>
      )}

      {present === true && (
        <SessionCard
          sessionName={sessionName}
          initialStatus={sessionRow?.state || 'idle'}
          compact
          autoExpand
          onKill={onKill}
        />
      )}

      <div data-testid="status-summary" style={{
        fontSize: 11, color: 'var(--text-dim)', marginTop: 6, marginBottom: 4,
      }}>
        {summaryHint(ticket, present, sessionRow?.state)}
      </div>

      {actions.length > 0 && (
        <div data-testid="ticket-actions" className="action-bar">
          {actions.map((a) => (
            <button
              key={a.id}
              className={doneActionId === a.id ? 'btn-action accent' : 'btn-action'}
              data-testid={`ticket-action-${a.id}`}
              onClick={(e) => {
                e.stopPropagation()
                onAction(a.id, renderPrompt(a, ticket))
              }}
              disabled={!!busyActionId}
              title={a.description}
              style={{ fontSize: 11 }}
            >
              {busyActionId === a.id ? `${a.label}...`
                : doneActionId === a.id ? `${a.label} (sent)`
                : a.label}
            </button>
          ))}
        </div>
      )}
      {actionError && (
        <div data-testid="ticket-action-error" style={{
          fontSize: 11, color: 'var(--red)', marginTop: 4,
        }}>
          {actionError}
        </div>
      )}
    </div>
  )
}


/** Single-line "what to do next" hint, mirroring TaskCard's
 * status-summary row. Keeps the visual rhythm consistent. */
function summaryHint(
  ticket: Ticket,
  present: boolean | null,
  sessionState: string | undefined,
): string {
  if (!present) {
    return `${ticket.status || 'open'} - open Agent to start working.`
  }
  if (sessionState === 'needs_input' || sessionState === 'needs_permission') {
    return 'Agent needs your input.'
  }
  if (sessionState === 'thinking') return 'Agent is working...'
  return ticket.status || 'in progress'
}
