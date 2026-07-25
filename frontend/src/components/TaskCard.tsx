import { useState, useCallback, useEffect, useRef } from 'react'
import type { Project, Task, PR, ActionDef, TaskStatus } from '../types'
import { StatusDot } from './StatusDot'
import { ActionButton } from './ActionButton'
import { SessionCard } from './SessionCard'
import { PRNode } from './PRNode'
import { TicketLink } from './TicketLink'
import { api } from '../api'
import { isTaskBlocked, isTerminalTaskStatus, evalActionCondition, classifyHistoryEntry, historyKindColor } from '../utils/taskHelpers'
import { formatLocalShort } from '../utils'
import { useAlert } from './Alert'
import { useSessionLauncher } from '../hooks/useSessionLauncher'

export { evalActionCondition } from '../utils/taskHelpers'

interface TaskCardProps {
  project: Project
  taskId: string
  actions: ActionDef[]
  expandable?: boolean
  forceFullRender?: boolean
  sessionExpanded?: boolean
  externalAction?: { actionId: string; taskId?: string; prNumber?: number; prRepo?: string; customPrompt?: string; ts: number } | null
  onOpenAction?: (actionId: string, prNumber?: number, prRepo?: string, customPrompt?: string) => void
  onClickPRNumber?: (pr: PR) => void
}

export function TaskCard({
  project,
  taskId,
  actions,
  expandable,
  forceFullRender,
  sessionExpanded,
  externalAction,
  onOpenAction,
  onClickPRNumber,
}: TaskCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [syncText, setSyncText] = useState('\u21BB')
  const [syncColor, setSyncColor] = useState<string | undefined>(undefined)
  const [pendingAction, setPendingAction] = useState<'opening' | 'killing' | null>(null)
  const [shouldAutoExpand, setShouldAutoExpand] = useState(false)
  const { alert, prompt, promptWithCheckbox, confirmAt } = useAlert()

  const tasks = project.tasks || {}
  const task = tasks[taskId] || ({} as Task)
  const blocked = isTaskBlocked(taskId, tasks)
  let status: TaskStatus | string = task.status || 'not_started'
  // `blocked` is computed from dep graph -- override the displayed
  // status for any non-terminal task with unclosed deps. Terminal
  // states (done/closed) stay as-is; the work is over. Mirrors the
  // backend's `effective_status` computation in core.tasks.get_task.
  if (blocked && status !== 'done' && status !== 'closed') status = 'blocked'

  const prs = task.prs || []
  const deps = task.dependencies || []
  const followUps = task.follow_ups || []
  const session = task.session
  const hasTickets = project.has_tickets !== false
  const ticketId = task.ticket_id
  const ticketUrl = task.ticket_url

  // Auto-expand when session appears (from any source: own action, PRDetail action, or external)
  const prevSessionRef = useRef(session)
  useEffect(() => {
    if (!prevSessionRef.current && session) {
      // Session just appeared -- auto expand
      setShouldAutoExpand(true)
    }
    prevSessionRef.current = session
  }, [session])

  // Clear pendingAction when server data matches expected state
  useEffect(() => {
    if (pendingAction === 'opening' && session) {
      setPendingAction(null)
    }
    if (pendingAction === 'killing' && !session) {
      setPendingAction(null)
      setShouldAutoExpand(false)
    }
  }, [session, pendingAction])

  const handleSync = useCallback(async () => {
    setSyncText('...')
    setSyncColor(undefined)
    try {
      const data = await api.checkStatus(project.id, taskId)
      if (data.changed) {
        setSyncText(`${data.old_status} -> ${data.new_status}`)
        setSyncColor('var(--green)')
      } else if (!ticketId && hasTickets) {
        // No change + no ticket: launch agent sync
        setSyncText('syncing...')
        setSyncColor('var(--accent)')
        try {
          await api.openSession({
            task_id: taskId,
            project_id: project.id,
            action_id: 'open',
            custom_prompt: 'Sync the status of this task.',
          })
          setSyncText('sync sent')
          setSyncColor('var(--green)')
        } catch {
          setSyncText('sync failed')
          setSyncColor('var(--red)')
        }
      } else {
        setSyncText('up to date')
        setSyncColor('var(--green)')
      }
    } catch {
      setSyncText('error')
      setSyncColor('var(--red)')
    }
    setTimeout(() => {
      setSyncText('\u21BB')
      setSyncColor(undefined)
    }, 3000)
  }, [project.id, taskId, ticketId, hasTickets])

  const handleKillSession = useCallback(async () => {
    const name = session?.name || taskId
    try {
      setPendingAction('killing')
      await api.killSession(name)
      // Don't set any other state -- wait for event bus to trigger refetch
    } catch {
      setPendingAction(null)
    }
  }, [session, taskId])

  // Shared launch + deliver-prompt logic. Endpoint is task-context;
  // ReviewsPage uses the same hook with endpoint.type='review'.
  const { launch } = useSessionLauncher({
    type: 'task', taskId, projectId: project.id,
  })

  // Handle action: call API, open session, deliver prompt via terminal
  const handleAction = useCallback(async (actionId: string, prNumber?: number, prRepo?: string, customPrompt?: string) => {
    setPendingAction('opening')
    const result = await launch({ actionId, prNumber, prRepo, customPrompt })
    if (!result) {
      // launch() already surfaced the error dialog; just clear state.
      setPendingAction(null)
      return
    }
    // Notify parent (used for side effects like navigation, not state)
    onOpenAction?.(actionId, prNumber, prRepo, customPrompt)
    // Don't clear pendingAction here -- the event bus refetch will
    // refresh the session row and pendingAction unsets naturally.
  }, [launch, onOpenAction])

  // Click handler for the "Do This Task" button. When the project
  // uses tickets and this task doesn't have one yet, bubble-prompt the
  // user: should the agent create the JIRA ticket first, then dive
  // into implementation? Picking "yes" ships both prompts (create
  // ticket -> do task) as a single combined customPrompt so the
  // agent runs them in one session without having to re-prompt.
  const handleDoTask = useCallback(async (e: React.MouseEvent) => {
    const needsTicket = hasTickets && !ticketId
    if (!needsTicket) {
      handleAction('do-task')
      return
    }
    const yes = await confirmAt(
      {
        title: 'Create JIRA ticket first?',
        message: 'This task has no ticket yet. Creating one first lets the work be tracked + linked to the right epic before the agent starts.',
        confirmLabel: 'Yes, create ticket first',
        cancelLabel: 'Just do the task',
      },
      { x: e.clientX, y: e.clientY },
    )
    if (!yes) {
      handleAction('do-task')
      return
    }
    const createTicket = actions.find((a) => a.id === 'create-ticket')
    const doTask = actions.find((a) => a.id === 'do-task')
    const combined = (
      `STEP 1: ${(createTicket?.prompt_template || '').trim()}\n\n` +
      `STEP 2 (only after the ticket is created and linked to this task): ` +
      `${(doTask?.prompt_template || '').trim()}`
    )
    handleAction('do-task', undefined, undefined, combined)
  }, [hasTickets, ticketId, actions, handleAction, confirmAt])

  // React to external action trigger (e.g. Ask Agent from PRDetail).
  // Guard with `taskId` when present: the parent's externalAction state
  // survives TaskCard remounts (switching between PRs keeps the same state
  // object). Without the taskId check, remounting for a different task
  // would see the old ts as "fresh" (lastExternalTs resets per mount) and
  // fire the previous prompt against the wrong session. Older callers that
  // don't set taskId keep working.
  const lastExternalTs = useRef(0)
  useEffect(() => {
    if (!externalAction) return
    if (externalAction.taskId && externalAction.taskId !== taskId) return
    if (externalAction.ts <= lastExternalTs.current) return
    lastExternalTs.current = externalAction.ts
    handleAction(externalAction.actionId, externalAction.prNumber, externalAction.prRepo, externalAction.customPrompt)
  }, [externalAction, handleAction, taskId])

  // When this card comes into "active" view, kick a background PR refresh
  // so the user sees fresh CI / review / merge state without clicking the
  // per-PR refresh button. "Active" means user-selected (sessionExpanded),
  // manually expanded from mini, or forced-full-render by the parent page
  // (graph/side panel). Fire-and-forget -- events from the server update
  // the UI when fetches land.
  //
  // The DB is a cache: the background pr_sync job keeps open PRs fresh, so
  // most opens read current data with no gh call. We only refresh PRs whose
  // cache is invalid (dirty=1, set by the notification poller and not yet
  // swept) -- that covers the "just marked dirty, background tick hasn't run
  // yet" window without re-fetching already-fresh PRs.
  //
  // 300ms debounce: if the user scrolls through cards quickly we don't
  // want N HTTP calls per second. `prs` is intentionally left out of the
  // dep array so changes to PR data don't trigger extra refreshes.
  const isActive = sessionExpanded !== undefined
    ? sessionExpanded
    : (forceFullRender || expanded)
  useEffect(() => {
    if (!isActive || prs.length === 0) return
    const timer = setTimeout(() => {
      for (const pr of prs) {
        if (pr.dirty) {
          api.refreshPR(pr.number).catch(() => {})
        }
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [isActive, taskId])  // eslint-disable-line react-hooks/exhaustive-deps

  const handleClose = useCallback(async () => {
    const hasTicket = !!(hasTickets && ticketId)
    // No ticket -> plain reason prompt; nothing to ask about ticket.
    // Has ticket -> reason + checkbox so the user can opt out of
    // closing JIRA in the rare case they want the task closed but
    // the ticket left open. Default checked so the common case
    // (close both) is one Enter-press away.
    let reason: string | null
    let closeTicket = false
    if (hasTicket) {
      const result = await promptWithCheckbox({
        title: 'Close task?',
        message: 'Optional reason; checkbox controls whether the linked ticket is also closed.',
        placeholder: 'Reason (optional)',
        confirmLabel: 'Close task',
        checkbox: {
          label: `Also close linked ticket (${ticketId})`,
          defaultChecked: true,
        },
      })
      if (result === null) return
      reason = result.value
      closeTicket = result.checked
    } else {
      reason = await prompt({
        title: 'Close task?',
        message: 'Provide an optional reason for the close.',
        placeholder: 'Reason (optional)',
        confirmLabel: 'Close task',
      })
      if (reason === null) return
    }
    try {
      await api.closeTask(project.id, taskId, reason)
      if (hasTicket && closeTicket) {
        let closePrompt = 'Close the JIRA ticket for this task.'
        if (reason) closePrompt += ` Reason: ${reason}`
        closePrompt += ' Set the ticket resolution to "Won\'t Fix" or "Closed" as appropriate.'
        handleAction('open', undefined, undefined, closePrompt)
      }
    } catch (e) {
      await alert({
        title: 'Failed to close task',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
  }, [project.id, taskId, hasTickets, ticketId, handleAction, prompt, promptWithCheckbox, alert])


  // Filter action buttons
  const actionData = { prs, has_ticket: !!ticketId, ci_status: undefined as string | undefined }
  // Determine ci_status from PRs for condition evaluation
  const failedPr = prs.find((p) => p.ci_status === 'failure')
  if (failedPr) actionData.ci_status = 'failure'

  const headerClickHandler = forceFullRender ? () => setExpanded(false) : undefined

  // Done/closed collapsed mini card. This conditional branch must be the ONLY
  // place that short-circuits the render -- early returns BEFORE hook declarations
  // would violate React's rules-of-hooks.
  if (isTerminalTaskStatus(status) && expandable && !forceFullRender && !expanded) {
    const miniPrs = prs.map((p) => `#${p.number}`).join(' ')
    return (
      <div
        data-testid="task-card-mini"
        className="task-card st-done"
        style={{ padding: '6px 12px', marginBottom: 4, cursor: 'pointer', opacity: 0.6, flexDirection: 'row', alignItems: 'center' }}
        onClick={() => setExpanded(true)}
      >
        <span className="dot dot-done" style={{ width: 6, height: 6, marginRight: 6 }} />
        <span style={{ fontSize: 11, color: 'var(--text-dim)', flex: 1 }}>{taskId}</span>
        <span className="task-card-type" style={{ fontSize: 9, padding: '1px 5px' }}>{task.type || ''}</span>
        {miniPrs && (
          <span style={{ fontSize: 10, color: 'var(--text-dim)', marginLeft: 8, fontFamily: 'monospace' }}>{miniPrs}</span>
        )}
        {task.updated_at && (
          <span style={{ fontSize: 9, color: 'var(--text-faint)', marginLeft: 8 }}>{task.updated_at.substring(0, 10)}</span>
        )}
      </div>
    )
  }

  return (
    <div data-testid="task-card" className={`task-card st-${status}`}>
      {/* Header */}
      <div
        className="task-card-header"
        style={forceFullRender ? { cursor: 'pointer' } : undefined}
        onClick={headerClickHandler}
      >
        <div className="task-card-title">
          <StatusDot status={status} style={{ verticalAlign: 'middle', marginRight: 6 }} />
          {taskId}
          {ticketId && ticketUrl ? (
            <>
              {' '}
              {/* testId omitted -> renders the legacy bare
                  data-testid="ticket-link" that existing tests
                  assert on. */}
              <TicketLink
                ticketKey={ticketId}
                fallbackUrl={ticketUrl}
                style={{ fontSize: 11, fontWeight: 400 }}
              >
                [{ticketId}]
              </TicketLink>
            </>
          ) : hasTickets ? (
            <>
              {' '}
              <button
                className="btn-action"
                data-testid="create-ticket-btn"
                style={{ padding: '1px 6px', fontSize: 9 }}
                onClick={(e) => {
                  e.stopPropagation()
                  onOpenAction?.('create-ticket')
                }}
              >
                + ticket
              </button>
            </>
          ) : null}
          {' '}
          <button
            className="btn-action"
            data-testid="sync-btn"
            style={{ padding: '1px 6px', fontSize: 9, color: syncColor }}
            onClick={(e) => {
              e.stopPropagation()
              handleSync()
            }}
            title="Check and sync status"
          >
            {syncText}
          </button>
        </div>
        <span className="task-card-type">{task.type || ''}</span>
      </div>

      {/* Group + updated_at */}
      <div className="task-card-group">
        {task.group_name || ''}
        {task.updated_at && (
          <span style={{ marginLeft: 'auto', fontSize: 9, color: 'var(--text-faint)' }}>
            {task.updated_at.substring(0, 10)}
          </span>
        )}
      </div>

      {/* Description */}
      {task.description && (
        <div style={{ fontSize: 12, color: 'var(--text)', marginBottom: 8, lineHeight: 1.4 }}>
          {task.description}
        </div>
      )}

      {/* Body fields */}
      <div className="task-card-body">
        <div className="task-card-field">
          <span className="label">Status</span>
          <span className="value">{status}</span>
        </div>
        {deps.length > 0 && (
          <div className="task-card-field">
            <span className="label">Depends on</span>
            <span className="value">
              {deps.map((d, i) => {
                const depTask = tasks[d]
                const depSt = depTask?.status || 'not_started'
                return (
                  <span key={d}>
                    <StatusDot status={depSt} style={{ width: 6, height: 6, verticalAlign: 'middle' }} />
                    {' '}{d}
                    {i < deps.length - 1 ? ', ' : ''}
                  </span>
                )
              })}
            </span>
          </div>
        )}
        {followUps.length > 0 && (
          <div className="task-card-field">
            <span className="label">Follow-ups</span>
            <span className="value">{followUps.join(', ')}</span>
          </div>
        )}
        {task.notes && (
          <div className="task-card-field">
            <span className="label">Notes</span>
            <span className="value" style={{ color: 'var(--yellow)' }}>{task.notes}</span>
          </div>
        )}
        {task.history && task.history.length > 0 && (
          <div className="task-card-field" style={{ alignItems: 'flex-start' }}>
            <span className="label">History</span>
            <span className="value" style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 10 }}>
              {task.history.slice(0, 5).map((e, i) => {
                const kind = classifyHistoryEntry(e.text)
                const dotColor = historyKindColor(kind)
                return (
                  <span key={`${e.ts}-${i}`}
                        data-history-kind={kind}
                        style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    {dotColor && (
                      <span
                        aria-hidden="true"
                        title={kind.replace('_', ' ')}
                        style={{
                          width: 6, height: 6, borderRadius: '50%',
                          background: dotColor, flexShrink: 0,
                        }}
                      />
                    )}
                    <span
                      style={{ color: 'var(--text-faint)', whiteSpace: 'nowrap', fontFamily: 'monospace' }}
                      title={e.ts}  /* full UTC timestamp for hover */
                    >
                      {formatLocalShort(e.ts)}
                    </span>
                    <span style={{ color: kind === 'manual' ? 'var(--text-dim)' : 'var(--text)' }}>
                      {e.text}
                    </span>
                  </span>
                )
              })}
              {task.history.length > 5 && (
                <span style={{ color: 'var(--text-faint)', fontSize: 9 }}>
                  + {task.history.length - 5} older
                </span>
              )}
            </span>
          </div>
        )}
      </div>

      {/* PRs */}
      {prs.length > 0 && (
        <div className="task-card-prs">
          {prs.map((pr) => (
            <PRNode
              key={pr.number}
              pr={pr}
              showMeta
              onClickNumber={onClickPRNumber ? () => onClickPRNumber(pr) : undefined}
            />
          ))}
        </div>
      )}

      {/* Open Agent button -- shown when no active session and not pending */}
      {!session && pendingAction !== 'opening' && (
        <div data-testid="open-agent">
          <button
            className="btn-action accent"
            style={{ width: '100%', margin: '4px 0', padding: 6 }}
            onClick={() => handleAction('open')}
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

      {/* Loading indicator for pending actions */}
      {pendingAction === 'opening' && !session && (
        <div style={{ fontSize: 11, color: 'var(--accent)', padding: '6px 0', textAlign: 'center' }}>
          Opening session...
        </div>
      )}

      {/* Session component -- from server data */}
      {session && pendingAction !== 'killing' && (
        <SessionCard
          sessionName={session.name || taskId}
          initialStatus={session.running ? (session.status || 'idle') : 'stopped'}
          compact
          autoExpand={sessionExpanded !== undefined ? sessionExpanded : shouldAutoExpand}
          onKill={handleKillSession}
        />
      )}

      {/* Loading indicator for kill in progress */}
      {pendingAction === 'killing' && session && (
        <div style={{ fontSize: 11, color: 'var(--text-dim)', padding: '6px 0', textAlign: 'center' }}>
          Stopping session...
        </div>
      )}

      {/* Action buttons bar */}
      <div className="action-bar">
        {expandable && (
          <ActionButton label="Expand" onClick={() => {}} />
        )}
        {actions
          .filter((a) => {
            if (a.id === 'open') return false
            if (a.id === 'create-ticket' && (!hasTickets || ticketId)) return false
            if (!evalActionCondition(a.condition, actionData)) return false
            return true
          })
          .map((a) => (
            <ActionButton
              key={a.id}
              label={a.label}
              // "Do This Task" gets the create-ticket-first bubble
              // prompt when the project tracks tickets and this task
              // doesn't have one yet. All other actions go through
              // the bare handleAction path.
              onClick={a.id === 'do-task'
                ? (e: React.MouseEvent) => handleDoTask(e)
                : () => handleAction(a.id)}
            />
          ))}
        {status !== 'closed' && (
          <ActionButton
            label="Close"
            onClick={handleClose}
            style={{ marginLeft: 'auto', color: 'var(--text-dim)' }}
          />
        )}
      </div>
    </div>
  )
}
