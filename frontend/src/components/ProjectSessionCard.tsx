import { useState, useRef, useCallback, useEffect } from 'react'
import { SessionDot } from './SessionDot'
import { useTerminal } from '../hooks/useTerminal'
import { useEventBus } from '../hooks/useEventBus'
import { useAgentSessionStatus } from '../hooks/useAgentSessionStatus'
import { useAlert } from './Alert'
import { api } from '../api'

interface Props {
  projectId: string
  projectName: string
}

// Predefined "manager actions" the user can fire at the project session.
// These are intentionally hardcoded for v1 -- once we know which ones are
// useful they can graduate to the actions DB table with context='project'.
// All of them lean on `eva-cli` for live state and stay in REPORT mode --
// the system prompt enforces that boundary too.
const MANAGER_ACTIONS: { id: string; label: string; prompt: string }[] = [
  {
    id: 'sync',
    label: 'Sync Project',
    prompt:
      'Sync this project. Run `eva-cli list-tasks <PROJECT_ID> --json` ' +
      'and `eva-cli list-prs --project <PROJECT_ID> --json`. Then for ' +
      'each task that has a ticket or PRs, run ' +
      '`eva-cli check-status <PROJECT_ID> <task_id>` to refresh JIRA + ' +
      'GitHub state. Report a one-line summary per task that changed ' +
      'status. Do NOT modify anything beyond the sync calls.',
  },
  {
    id: 'audit',
    label: 'Audit Anomalies',
    prompt:
      'Audit this project for anomalies. Look for: tasks marked ' +
      'in_progress with merged PRs (should be done), tasks with closed ' +
      'tickets but open status, blocked tasks whose deps are actually ' +
      'done, missing tickets where the project requires them, stale PRs ' +
      'with no activity in 7+ days, deps cycles. REPORT a list of ' +
      'anomalies with task_id + what looks wrong + what the user could ' +
      'do. Do NOT fix anything yourself.',
  },
  {
    id: 'suggest',
    label: 'Suggest Next',
    prompt:
      'Recommend the next task I should work on for this project. ' +
      'Consider: status (prefer in_progress > in_review > not_started), ' +
      'whether deps are done, ticket priority, PR review state, and ' +
      'whether other tasks are blocked on this one. Pick ONE primary ' +
      'recommendation with a 1-2 sentence reason, then list 2-3 ' +
      'alternatives. Do NOT open the session, just recommend.',
  },
]

interface SessionInfo {
  project_id: string
  tmux_name: string
  running: boolean
  status?: string
}

export function ProjectSessionCard({ projectId, projectName }: Props) {
  const [info, setInfo] = useState<SessionInfo | null>(null)
  const [opening, setOpening] = useState(false)
  const [actionPending, setActionPending] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const terminalRef = useRef<HTMLDivElement>(null)
  const { confirm, alert } = useAlert()

  // Initial fetch -- session may already exist from a prior visit.
  useEffect(() => {
    let cancelled = false
    api.getProjectManager(projectId)
      .then((d) => { if (!cancelled) setInfo(d) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [projectId])

  // Shared hook handles agent.* status translation for this session's
  // tmux name; setSseStatus lets us clear the pill on explicit kill.
  const tmuxName = info?.tmux_name
  const { sseStatus, setSseStatus } = useAgentSessionStatus(tmuxName)

  // session.opened / session.killed for this tmux name -> refresh info
  useEventBus('session.*', useCallback((event: Record<string, unknown>) => {
    if (!tmuxName || event.session !== tmuxName) return
    api.getProjectManager(projectId)
      .then(setInfo)
      .catch(() => setInfo(null))
  }, [tmuxName, projectId]))

  useTerminal({
    sessionName: tmuxName || '',
    containerRef: terminalRef,
    active: !!tmuxName && expanded && !!info?.running,
  })

  // When info?.running is false, force the dot grey via 'stopped'
  // regardless of any stale snapshot state -- the source-of-truth is
  // the project-manager record itself.
  const status = sseStatus || info?.status || (info?.running ? 'idle' : 'stopped')
  const dotState = !info?.running ? 'stopped' : status

  const handleOpen = useCallback(async () => {
    setOpening(true)
    try {
      const d = await api.openProjectManager(projectId)
      setInfo(d)
      setExpanded(true)
    } catch (e) {
      await alert({
        title: 'Failed to open project session',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setOpening(false)
    }
  }, [projectId, alert])

  const handleKill = useCallback(async () => {
    if (!info) return
    const ok = await confirm({
      title: `Kill project session for "${projectName}"?`,
      message: 'The manager agent will stop. You can reopen anytime; project ' +
               'context will be re-injected fresh.',
      confirmLabel: 'Kill',
      danger: true,
    })
    if (!ok) return
    try {
      await api.killProjectManager(projectId)
      setInfo(null)
      setExpanded(false)
      setSseStatus(null)
    } catch { /* event bus will catch up */ }
  }, [info, projectId, projectName, confirm])

  const handleAction = useCallback(async (id: string, label: string, prompt: string) => {
    setActionPending(id)
    try {
      // Replace the literal placeholder so the manager doesn't have to
      // remember which project it's in (it knows from system prompt, but
      // explicit > implicit for the live command).
      const filled = prompt.replace(/<PROJECT_ID>/g, projectId)
      await api.runProjectManagerAction(projectId, filled)
      // Refresh info so card status flips to "thinking" sooner.
      setExpanded(true)
      const d = await api.getProjectManager(projectId).catch(() => null)
      if (d) setInfo(d)
    } catch (e) {
      await alert({
        title: `Action failed: ${label}`,
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setActionPending(null)
    }
  }, [projectId, alert])

  // No session yet -> show open button only. Kept compact (single row,
  // no border) so the top strip stays tight when the manager isn't running.
  if (!info) {
    return (
      <div
        data-testid="project-session-card"
        style={{
          border: '1px solid var(--border)', borderRadius: 6,
          padding: '6px 10px',
          background: 'var(--card-bg)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}
      >
        <span style={{ fontSize: 10, color: 'var(--text-dim)', flex: 1 }}>
          Project Manager
        </span>
        <button
          className="btn-action accent"
          style={{ fontSize: 10, padding: '3px 8px' }}
          disabled={opening}
          onClick={handleOpen}
        >
          {opening ? '...' : 'Open'}
        </button>
      </div>
    )
  }

  return (
    <div
      data-testid="project-session-card"
      style={{
        // `position: relative` so the floating terminal below can anchor
        // to this card's bounding box. `overflow: visible` (was hidden)
        // so the absolute-positioned terminal isn't clipped.
        position: 'relative',
        border: '1px solid var(--border)',
        borderRadius: 6,
        background: 'var(--card-bg)',
      }}
    >
      <div
        className="terminal-header"
        style={{ cursor: 'pointer' }}
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="tname">
          <img
            src="/static/claude-favicon.ico"
            width={12}
            height={12}
            style={{ verticalAlign: 'middle', marginRight: 4 }}
            alt=""
          />
          {projectName} <span style={{ color: 'var(--text-dim)', fontWeight: 400, fontSize: 10 }}>(manager)</span>
        </span>
        <span className="tactions" onClick={(e) => e.stopPropagation()}>
          <SessionDot state={dotState} />
          <span style={{ fontSize: 10 }}>{info.running ? status : 'stopped'}</span>
          {!info.running && (
            <button
              className="btn-action accent"
              style={{ padding: '2px 6px', fontSize: 9 }}
              onClick={handleOpen}
              disabled={opening}
            >
              {opening ? '...' : 'Restart'}
            </button>
          )}
          {info.running && (
            <button
              className="btn-action"
              style={{ padding: '2px 6px', fontSize: 9, color: 'var(--red)' }}
              onClick={handleKill}
            >
              Kill
            </button>
          )}
        </span>
      </div>

      {/* Action button bar -- always visible when session exists */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: 6,
        padding: '6px 10px', background: 'var(--panel-bg)',
        borderBottom: '1px solid var(--border)',
      }}>
        {MANAGER_ACTIONS.map((a) => (
          <button
            key={a.id}
            className="btn-action"
            style={{ fontSize: 10, padding: '3px 8px' }}
            disabled={actionPending !== null || !info.running}
            onClick={() => handleAction(a.id, a.label, a.prompt)}
            title={a.prompt}
          >
            {actionPending === a.id ? '...' : a.label}
          </button>
        ))}
      </div>

      {/* Floating terminal -- anchored to the bottom of the card header
          but positioned absolute so expanding it doesn't push the view
          tabs / graph down. Graph sits on the left (50%+) so it stays
          fully visible while the manager runs on the right. z-index 50
          is above the graph but below modals (Alert uses 10000). */}
      <div
        ref={terminalRef}
        data-testid="project-terminal-container"
        className="xterm-wrap"
        style={{
          position: 'absolute',
          top: '100%',
          right: 0,
          width: '100%',
          display: expanded ? 'block' : 'none',
          height: expanded ? 432 : 0,
          minHeight: expanded ? 216 : 0,
          zIndex: 50,
          background: 'var(--card-bg)',
          border: '1px solid var(--border)',
          borderTop: 'none',
          borderRadius: '0 0 6px 6px',
          boxShadow: expanded ? '0 8px 20px rgba(0,0,0,0.35)' : undefined,
        }}
      />
    </div>
  )
}
