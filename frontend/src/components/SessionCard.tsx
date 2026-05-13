import { useState, useRef, useCallback, useEffect } from 'react'
import { SessionDot } from './SessionDot'
import { useTerminal } from '../hooks/useTerminal'
import { useAgentSessionStatus } from '../hooks/useAgentSessionStatus'
import { useAlert } from './Alert'
import { api } from '../api'

interface Props {
  sessionName: string
  initialStatus?: string
  compact?: boolean
  autoExpand?: boolean
  onKill: () => void
}

export function SessionCard({ sessionName, initialStatus, compact, autoExpand, onKill }: Props) {
  const [expanded, setExpanded] = useState(autoExpand ?? false)
  const [termStatus, setTermStatus] = useState<string | null>(null)
  const [resuming, setResuming] = useState(false)
  const { confirm, alert } = useAlert()

  // Status comes from the global session status service (single
  // SSE consumer + map snapshot). `setSseStatus` is an imperative
  // override -- e.g. when the parent tells us the session was killed,
  // we want to flip to 'stopped' instantly rather than wait for the
  // service's next event.
  const { sseStatus, setSseStatus } = useAgentSessionStatus(sessionName)
  // Auto-expand when this card's session enters the 'starting' state.
  // Replaces the old `lastEvent === 'session_start'` check; the service
  // exposes the resolved state, not the raw event suffix.
  useEffect(() => {
    if (sseStatus === 'starting') setExpanded(true)
  }, [sseStatus])

  // Sync expanded to autoExpand when it changes (both true and false)
  useEffect(() => {
    if (autoExpand !== undefined) setExpanded(autoExpand)
  }, [autoExpand])

  // When the parent TRANSITIONS `initialStatus` to 'stopped' (e.g.
  // /api/all-sessions discovered running=false because tmux died
  // externally and no agent hook fired), force the local override to
  // 'stopped' so the global snapshot's stale 'thinking'/'idle' can't
  // shadow the truth. The override stays until the parent flips
  // `initialStatus` away from 'stopped' (which clears it).
  //
  // Critically, we DON'T override on the initial render with
  // `initialStatus='stopped'` -- that's the case where the parent
  // just hasn't seen the snapshot yet, and the snapshot may already
  // know the session is starting/thinking. Tracking transitions via
  // a ref keeps both cases right.
  const prevInitialRef = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (prevInitialRef.current !== undefined
        && prevInitialRef.current !== 'stopped'
        && initialStatus === 'stopped') {
      setSseStatus('stopped')
    } else if (initialStatus !== 'stopped') {
      // Parent says alive again -- drop the override so the snapshot
      // can drive the displayed status.
      setSseStatus(null)
    }
    prevInitialRef.current = initialStatus
  }, [initialStatus, setSseStatus])

  const status = sseStatus || initialStatus || 'stopped'
  const terminalRef = useRef<HTMLDivElement>(null)

  useTerminal({
    sessionName,
    containerRef: terminalRef,
    active: expanded,
    onStatusChange: setTermStatus,
  })

  // Session status is the default. Only show terminal status when stream is broken.
  const termError = termStatus === 'stream lost' ? termStatus : null
  const displayStatus = termError || status

  const handleHeaderClick = useCallback(() => {
    setExpanded((prev) => !prev)
  }, [])

  const handleKill = useCallback(async () => {
    const ok = await confirm({
      title: `Kill session "${sessionName}"?`,
      message: 'The tmux session will be stopped AND the agent session will be ' +
               'forgotten. Use Resume instead if you only lost tmux (host recovery reboot).',
      confirmLabel: 'Kill',
      danger: true,
    })
    if (ok) onKill()
  }, [sessionName, onKill, confirm])

  const handleResume = useCallback(async () => {
    setResuming(true)
    try {
      const res = await api.resumeSession(sessionName)
      // Parent will pick up the `session.opened` SSE event and refetch;
      // auto-expand so the user sees agent come back online.
      setExpanded(true)
      if (res.action === 'relaunched') {
        // Fallback path: we didn't have the UUID on record. Tell the user
        // the tmux is back but the prior conversation was NOT restored.
        await alert({
          title: 'Session relaunched (history not resumed)',
          message: 'No agent session id on record for "' + sessionName + '", so a ' +
                   'fresh agent was started. Previous conversation is lost.',
          kind: 'warning',
        })
      }
    } catch (e) {
      await alert({
        title: 'Failed to resume session',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setResuming(false)
    }
  }, [sessionName, alert])

  return (
    <div
      data-testid="session-component"
      style={{
        overflow: 'hidden',
        border: '1px solid var(--border)',
        borderRadius: 6,
        margin: compact ? '4px 0' : '6px 0',
      }}
    >
      <div
        className="terminal-header"
        data-testid="session-header"
        style={{ cursor: 'pointer' }}
        onClick={handleHeaderClick}
      >
        <span className="tname">
          <img
            src="/static/claude-favicon.ico"
            width={12}
            height={12}
            style={{ verticalAlign: 'middle', marginRight: 4 }}
            alt=""
          />
          {sessionName}
        </span>
        <span className="tactions">
          <SessionDot state={displayStatus} />
          <span style={{ fontSize: 10 }}>{displayStatus}</span>
          {/* Show Resume when tmux is gone (stopped / stream lost) or
              when the backend marked this session `crashed` (tmux
              died unexpectedly while we still hold the claude UUID).
              Keeps Kill as a destructive fallback. */}
          {(displayStatus === 'stopped' || displayStatus === 'stream lost'
              || displayStatus === 'crashed') && (
            <button
              className="btn-action accent"
              style={{ padding: '2px 6px', fontSize: 9 }}
              disabled={resuming}
              onClick={(e) => { e.stopPropagation(); handleResume() }}
              title="Restart tmux and resume the existing agent session by UUID"
            >
              {resuming ? '...' : 'Resume'}
            </button>
          )}
          <button
            className="btn-action"
            style={{ padding: '2px 6px', fontSize: 9, color: 'var(--red)' }}
            onClick={(e) => { e.stopPropagation(); handleKill() }}
          >
            Kill
          </button>
        </span>
      </div>

      {/* Terminal container: fixed pixel height keeps xterm renderer stable.
          540px paired with fontSize 10 + lineHeight 1.0 in useTerminal:
          ~51 visible rows, enough to skim a Claude response without
          dominating the page. */}
      <div
        ref={terminalRef}
        data-testid="terminal-container"
        className="xterm-wrap"
        style={{
          display: expanded ? 'block' : 'none',
          height: expanded ? 540 : 0,
          minHeight: expanded ? 270 : 0,
        }}
      />
    </div>
  )
}
