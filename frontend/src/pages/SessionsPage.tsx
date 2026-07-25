import { useState, useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react'
import type { Project, Task, ActionDef, PR } from '../types'
import type { CronJob, ProjectManagerSession, Ticket } from '../api'
import { useApi } from '../hooks/useApi'
import { useLayoutRatios } from '../hooks/useLayoutRatios'
import { useSessionStatus } from '../hooks/SessionStatusProvider'
import { bucketize } from '../utils/sessionState'
import { TaskCard } from '../components/TaskCard'
import { PRCard } from '../components/PRCard'
import { TaskNodeChip } from '../components/TaskNodeChip'
import { LiveSessionChip } from '../components/LiveSessionChip'
import { ReviewCard } from '../components/ReviewCard'
import { TicketNode } from '../components/TicketNode'
import { TicketTaskCard } from '../components/TicketTaskCard'
import { ProjectSessionCard } from '../components/ProjectSessionCard'
import { CronCard } from './CronJobsPage'
import { useAlert } from '../components/Alert'
import { api } from '../api'
import { repoFromPrUrl } from '../utils'

// In 3-pane mode (list + detail + task panel) the page reads its
// column ratios from `ui.layout.sessions_col_ratios`. The 1-pane
// mode (no PR selected) keeps its 50% default.
const DEFAULT_SESSIONS_RATIOS = [25, 40, 35] as const

interface SessionEntry {
  task_id: string
  project: string
  tmux_name: string
  running: boolean
  status: string
}

interface ProjectGroup {
  id: string
  name: string
  has_tickets: boolean
  sessions: SessionEntry[]
  // Tasks referenced by this group's sessions + their direct deps.
  // Embedded here so SessionsPage doesn't need a second round trip to
  // `/api/projects/{pid}` to render TaskCards.
  tasks: Record<string, Task>
}

interface SessionsPageProps {
  onNavigate?: (projectId: string, view: string) => void
  selectedPR?: { repo: string; number: number } | null
  onSelectPR?: (pr: { repo: string; number: number } | null) => void
  selectedProjectId?: string | null
  selectedTaskId?: string | null
  onSelectLiveTask?: (projectId: string | null, taskId: string | null) => void
}

export function SessionsPage({
  onNavigate, selectedPR, onSelectPR,
  selectedProjectId = null, selectedTaskId = null, onSelectLiveTask,
}: SessionsPageProps) {
  const { data: actionsData } = useApi<{ actions: ActionDef[] }>('/api/actions?context=task')

  // Live cron / review / ticket sessions all come from the central
  // SessionStatusProvider so this page shares one cache + one fetch
  // cycle with the top-bar SessionIndicator. Project-task sessions
  // (`/api/all-sessions`) are also served by the service -- both raw
  // (sessionsData) for the project chip / TaskCard wiring on this
  // page, and pre-bucketed for the indicator.
  const {
    sessions: sessionStateMap,
    projectSessions: sessionsData,
    liveCronJobs, liveReviews, liveTickets, liveProjectManagers,
    refetchSessions, refetchCron,
  } = useSessionStatus()
  const [sessListRatio, sessDetailRatio, sessTaskRatio] = useLayoutRatios(
    'ui.layout.sessions_col_ratios',
    [...DEFAULT_SESSIONS_RATIOS],
  )

  // Memoize so downstream useEffect / useCallback dep arrays stay stable between
  // renders where projectId+taskId didn't change.
  const selectedTask = useMemo(
    () => (selectedProjectId && selectedTaskId)
      ? { projectId: selectedProjectId, taskId: selectedTaskId }
      : null,
    [selectedProjectId, selectedTaskId],
  )
  const [actionState, setActionState] = useState<'idle' | 'loading' | 'done'>('idle')
  const [actionResult, setActionResult] = useState<string | null>(null)
  const [openingSession, setOpeningSession] = useState(false)
  const { alert } = useAlert()
  const [externalAction, setExternalAction] = useState<{ actionId: string; taskId?: string; prNumber?: number; prRepo?: string; customPrompt?: string; ts: number } | null>(null)
  const [ticketDetails, setTicketDetails] = useState<Record<string, Ticket>>({})

  const actions = actionsData?.actions || []

  // View mode for the left pane: 'by-project' groups chips by project
  // (default; matches the original layout); 'by-attention' flattens
  // every session into three buckets ordered by urgency so the user
  // can see "what needs me right now" without scanning N projects.
  // Persisted in localStorage so the choice survives reloads.
  const [viewMode, setViewMode] = useState<'by-project' | 'by-attention'>(() => {
    try {
      const v = localStorage.getItem('eva.allLiveTasks.viewMode')
      if (v === 'by-attention' || v === 'by-project') return v
    } catch { /* ignore (private mode / disabled storage) */ }
    return 'by-project'
  })
  useEffect(() => {
    try { localStorage.setItem('eva.allLiveTasks.viewMode', viewMode) } catch { /* ignore */ }
  }, [viewMode])

  // `/api/all-sessions` already embeds the tasks referenced by each group
  // SSE-driven cache invalidation lives in `SessionStatusProvider` --
  // the service refetches each affected endpoint on agent.* /
  // session.* / task.* / github.* / ticket.* events. We don't
  // duplicate those subscriptions here.

  const showResult = useCallback((msg: string) => {
    setActionResult(msg)
    setActionState('done')
    refetchSessions()
    setTimeout(() => { setActionState('idle'); setActionResult(null) }, 3000)
  }, [refetchSessions])

  const handleRebuild = useCallback(async () => {
    setActionState('loading')
    try {
      const result = await api.rebuildSessions()
      showResult(result.rebuilt.length > 0 ? `Rebuilt ${result.rebuilt.length}` : 'All running')
    } catch {
      showResult('Rebuild failed')
    }
  }, [showResult])

  const handleKillByStatus = useCallback(async (statuses: string[]) => {
    setActionState('loading')
    try {
      const result = await api.killSessionsByStatus(statuses)
      showResult(result.killed.length > 0 ? `Killed ${result.killed.length}` : 'None matched')
    } catch {
      showResult('Kill failed')
    }
  }, [showResult])

  const handleOpenAction = useCallback(async (projectId: string, taskId: string, actionId: string, prNumber?: number, prRepo?: string, customPrompt?: string) => {
    setOpeningSession(true)
    try {
      await api.openSession({ task_id: taskId, project_id: projectId, action_id: actionId, pr_number: prNumber, pr_repo: prRepo, custom_prompt: customPrompt })
      refetchSessions()
    } catch (e) {
      await alert({
        title: 'Failed to open session',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setOpeningSession(false)
    }
  }, [refetchSessions, alert])

  const handleSelectNode = useCallback((projectId: string, taskId: string) => {
    if (selectedTask?.projectId === projectId && selectedTask?.taskId === taskId) {
      onSelectLiveTask?.(null, null)
      onSelectPR?.(null)
    } else {
      onSelectLiveTask?.(projectId, taskId)
      // Switching to a different task: clear any open PR (no PR selected yet for the new task).
      onSelectPR?.(null)
    }
  }, [selectedTask, onSelectPR, onSelectLiveTask])

  const handleClickPR = useCallback((pr: PR) => {
    const repo = repoFromPrUrl(pr.url)
    onSelectPR?.({ repo, number: pr.number })
  }, [onSelectPR])

  // liveCronJobs / liveReviews / liveTickets come from the
  // SessionStatusProvider context; the service already filters to
  // session_alive and sorts for stable chip ordering.

  // Cron / review selection lives locally on this page -- App-level
  // selection state already covers project tasks (selectedTask), and
  // these "extras" are page-specific affordances (the cron job card
  // and review card render inline so the user doesn't have to leave
  // All Live Tasks). Mutually exclusive with selectedTask: clicking
  // a cron chip clears the task selection and vice versa.
  const [selectedExtra, setSelectedExtra] = useState<
    | { kind: 'cron'; jobId: number }
    | { kind: 'review'; url: string }
    | { kind: 'ticket'; key: string; instance: string }
    | { kind: 'manager'; projectId: string }
    | null
  >(null)

  const handleClickCron = useCallback((job: CronJob) => {
    if (selectedExtra?.kind === 'cron' && selectedExtra.jobId === job.id) {
      setSelectedExtra(null)
      return
    }
    // Clear other selections so only one card is "active" at a time.
    onSelectLiveTask?.(null, null)
    onSelectPR?.(null)
    setSelectedExtra({ kind: 'cron', jobId: job.id })
  }, [selectedExtra, onSelectLiveTask, onSelectPR])
  // Derive the unified task_id for a review row from its PR URL --
  // mirrors `EvaDB._review_task_id_from_url` (`review-<slug>-<n>`) so
  // clicking a review chip surfaces in the URL as `?task=review-...`,
  // same as any other live task. Returns '' for unparseable URLs.
  const _reviewTaskId = (url: string): string => {
    const m = (url || '').match(/github\.com\/([^/]+)\/([^/]+)\/pull\/(\d+)/)
    if (!m) return ''
    const slug = `${m[1]}/${m[2]}`.replace(/[^a-zA-Z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '').toLowerCase()
    return `review-${slug}-${m[3]}`
  }

  const handleClickReview = useCallback((pr: PR) => {
    if (selectedExtra?.kind === 'review' && selectedExtra.url === pr.url) {
      setSelectedExtra(null)
      onSelectLiveTask?.(null, null)
      return
    }
    onSelectPR?.(null)
    setSelectedExtra({ kind: 'review', url: pr.url })
    // Reviews are now real tasks (type='review'); push the task_id
    // into the URL so refresh / share-link works AND the address bar
    // reflects the selection. project param stays empty (review-type
    // tasks live in the unsorted bucket).
    onSelectLiveTask?.(null, _reviewTaskId(pr.url) || null)
  }, [selectedExtra, onSelectLiveTask, onSelectPR])
  const handleClickTicket = useCallback((ticket: Ticket) => {
    const inst = ticket.instance_name || ''
    if (selectedExtra?.kind === 'ticket'
        && selectedExtra.key === ticket.key
        && selectedExtra.instance === inst) {
      setSelectedExtra(null)
      onSelectLiveTask?.(null, null)
      return
    }
    onSelectPR?.(null)
    setSelectedExtra({ kind: 'ticket', key: ticket.key, instance: inst })
    // Ticket tasks use the JIRA key as task_id (post-merge). Push it
    // into the URL so the address bar tracks the selection.
    onSelectLiveTask?.(null, ticket.key || null)
  }, [selectedExtra, onSelectLiveTask, onSelectPR])

  const handleClickManager = useCallback((manager: ProjectManagerSession) => {
    if (selectedExtra?.kind === 'manager'
        && selectedExtra.projectId === manager.project_id) {
      setSelectedExtra(null)
      return
    }
    onSelectLiveTask?.(null, null)
    onSelectPR?.(null)
    setSelectedExtra({ kind: 'manager', projectId: manager.project_id })
  }, [selectedExtra, onSelectLiveTask, onSelectPR])

  const selectedTicketKey = selectedExtra?.kind === 'ticket' ? selectedExtra.key : ''
  const selectedTicketInstance = selectedExtra?.kind === 'ticket' ? selectedExtra.instance : ''
  const selectedTicketCacheKey = selectedTicketKey
    ? `${selectedTicketInstance}:${selectedTicketKey}`
    : ''
  const selectedLiveTicket = useMemo(() => {
    if (!selectedTicketKey) return null
    return liveTickets.find((t) =>
      t.key === selectedTicketKey
      && (t.instance_name || '') === selectedTicketInstance,
    ) || null
  }, [selectedTicketKey, selectedTicketInstance, liveTickets])
  useEffect(() => {
    if (!selectedTicketKey) return
    let cancelled = false
    api.getTicket(selectedTicketKey, selectedTicketInstance || undefined)
      .then((fresh) => {
        if (cancelled) return
        setTicketDetails((prev) => ({
          ...prev,
          [selectedTicketCacheKey]: fresh,
        }))
      })
      .catch(() => {
        // Keep rendering the live ticket row if the detail cache was
        // pruned or JIRA sync races the selection.
      })
    return () => { cancelled = true }
  }, [
    selectedTicketKey,
    selectedTicketInstance,
    selectedTicketCacheKey,
    selectedLiveTicket?.updated_at,
  ])
  // Selecting a project-task chip should clear any cron / review
  // selection so the middle pane shows the right card.
  useEffect(() => {
    if (selectedTask) setSelectedExtra(null)
  }, [selectedTask])

  // URL -> selectedExtra restore. On page reload, the App pulls
  // `task=<id>` from the URL into `selectedTaskId`. Review/ticket
  // tasks live outside the project flat-session list, so the normal
  // chip-select flow can't restore them; match the task_id against
  // the live review / ticket caches and re-populate selectedExtra.
  useEffect(() => {
    if (!selectedTaskId || selectedProjectId) return
    if (selectedTaskId.startsWith("review-")) {
      const r = liveReviews.find((p) => _reviewTaskId(p.url) === selectedTaskId)
      if (r) setSelectedExtra({ kind: 'review', url: r.url })
      return
    }
    // Ticket-task: task_id == JIRA key (no review- prefix).
    const t = liveTickets.find((tk) => tk.key === selectedTaskId)
    if (t) {
      setSelectedExtra({
        kind: 'ticket', key: t.key, instance: t.instance_name || '',
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTaskId, selectedProjectId, liveReviews, liveTickets])

  // Flattened list of all live (project, task) pairs. Sort by
  // `(projectId, taskId)` so the order is STABLE across refetches.
  //
  // The backend orders `SELECT ... FROM sessions ORDER BY updated_at DESC`,
  // which means every agent/task event bumps one row to the top and the
  // whole list rearranges under the user's cursor -- the card they were
  // reading "disappears" to a new slot. Sorting client-side keeps the
  // user's mental model intact (a task stays where it was) while the
  // status-dot SSE updates inside each card still reflect live state.
  const flatSessions = useMemo(() => {
    if (!sessionsData) return [] as Array<{ projectId: string; projectName: string; taskId: string }>
    const out: Array<{ projectId: string; projectName: string; taskId: string }> = []
    const projectIds = Object.keys(sessionsData).sort()
    for (const projectId of projectIds) {
      const group = sessionsData[projectId]
      const sessions = [...group.sessions].sort((a, b) => a.task_id.localeCompare(b.task_id))
      for (const s of sessions) {
        out.push({ projectId, projectName: group.name, taskId: s.task_id })
      }
    }
    return out
  }, [sessionsData])

  // Refs for scrolling to selected card in the middle pane.
  const cardRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const middlePaneRef = useRef<HTMLDivElement | null>(null)

  // Initial scroll: align the first card's top to the pane top, but ONLY
  // when the list transitions from empty -> non-empty (i.e. the very first
  // render after data loads). Depending on `flatSessions` directly used to
  // make this effect re-fire on every refetch and yank the user back to
  // the top of the page while they were scrolled to a card below.
  const hasScrolledInitialRef = useRef(false)
  useEffect(() => {
    if (hasScrolledInitialRef.current) return
    if (selectedTask || flatSessions.length === 0) return
    const pane = middlePaneRef.current
    if (!pane) return
    hasScrolledInitialRef.current = true
    const raf = requestAnimationFrame(() => {
      const firstKey = `${flatSessions[0].projectId}:${flatSessions[0].taskId}`
      const el = cardRefs.current[firstKey]
      if (el && pane) {
        const elRect = el.getBoundingClientRect()
        const paneRect = pane.getBoundingClientRect()
        const target = pane.scrollTop + (elRect.top - paneRect.top)
        pane.scrollTo({ top: Math.max(0, target), behavior: 'smooth' })
      }
    })
    return () => cancelAnimationFrame(raf)
  }, [selectedTask, flatSessions])

  // Click on empty area in the left pane (outside any task / cron /
  // review chip): deselect everything currently focused.
  const handleLeftPaneBlankClick = useCallback((e: React.MouseEvent) => {
    const target = e.target as Element
    if (target.closest('[data-testid^="task-node-chip"]')) return
    if (target.closest('[data-testid^="live-chip-"]')) return
    if (target.closest('[data-testid^="ticket-row-"]')) return
    if (target.closest('button')) return
    onSelectLiveTask?.(null, null)
    onSelectPR?.(null)
    setSelectedExtra(null)
  }, [onSelectPR, onSelectLiveTask])

  // Resolve the cardRefs key for whichever entry is currently active --
  // project task, cron job, review PR, or ticket. Returns null when
  // nothing is selected so the scroll effect early-exits.
  const selectedCardKey = useMemo(() => {
    if (selectedTask) {
      return `${selectedTask.projectId}:${selectedTask.taskId}`
    }
    if (selectedExtra?.kind === 'cron') return `cron:${selectedExtra.jobId}`
    if (selectedExtra?.kind === 'review') return `review:${selectedExtra.url}`
    if (selectedExtra?.kind === 'ticket') {
      return `ticket:${selectedExtra.instance}:${selectedExtra.key}`
    }
    if (selectedExtra?.kind === 'manager') {
      return `manager:${selectedExtra.projectId}`
    }
    return null
  }, [selectedTask, selectedExtra])

  useEffect(() => {
    if (!selectedCardKey) return
    const pane = middlePaneRef.current
    if (!pane) return

    // The selected card grows (session terminal expands ~350px) after selection.
    // Re-center after each size change until it stabilizes, up to ~800ms.
    let stableCount = 0
    let lastHeight = 0
    let cancelled = false

    const align = () => {
      const el = cardRefs.current[selectedCardKey]
      if (!el || cancelled) return
      const elRect = el.getBoundingClientRect()
      const delta = (elRect.top + elRect.height / 2) - (window.innerHeight / 2)
      pane.scrollTo({ top: Math.max(0, pane.scrollTop + delta), behavior: 'smooth' })
    }

    let ro: ResizeObserver | null = null
    const el = cardRefs.current[selectedCardKey]
    if (el) {
      ro = new ResizeObserver(() => {
        if (cancelled) return
        const h = el.getBoundingClientRect().height
        if (Math.abs(h - lastHeight) < 1) {
          stableCount++
          if (stableCount >= 3) { ro?.disconnect() }
        } else {
          stableCount = 0
          lastHeight = h
          align()
        }
      })
      ro.observe(el)
    }
    // Also center on initial mount/pane resize.
    const raf = requestAnimationFrame(align)
    const stop = setTimeout(() => { cancelled = true; ro?.disconnect() }, 1200)

    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
      clearTimeout(stop)
      ro?.disconnect()
    }
  }, [selectedCardKey, selectedPR])

  if (!sessionsData) {
    return <div style={{ padding: 24, color: 'var(--text-dim)' }}>Loading sessions...</div>
  }

  // Same stable ordering rationale as `flatSessions` above: the backend
  // sorts by updated_at DESC, which shuffles chips on every event. Sort by
  // project id on the outer level and by task_id inside each group so
  // chips stay put while their status dots light up.
  const groupsWithSessions = Object.entries(sessionsData)
    .map(([projectId, group]): [string, ProjectGroup] => [
      projectId,
      {
        ...group,
        sessions: group.sessions.filter((s) => !s.task_id.startsWith('ticket-')),
      },
    ])
    .filter(([, g]) => g.sessions.length > 0)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([projectId, group]): [string, ProjectGroup] => [
      projectId,
      { ...group, sessions: [...group.sessions].sort((a, b) => a.task_id.localeCompare(b.task_id)) },
    ])

  const projectFromGroup = (group: ProjectGroup): Project => ({
    id: group.id,
    name: group.name,
    description: '',
    progress: 0,
    task_counts: {},
    has_tickets: group.has_tickets,
    tasks: group.tasks,
  })

  // One-stop chip renderer shared by both view modes. Pulling it out
  // keeps the by-project and by-attention branches structurally
  // identical -- when we change chip props (new tooltip, new badge),
  // both views update together.
  const renderChip = (s: SessionEntry, projectId: string, project: Project | null) => {
    const task = project?.tasks?.[s.task_id]
    const isSelected = selectedTask?.projectId === projectId && selectedTask?.taskId === s.task_id
    if (!task) {
      // Two reasons we land here: (a) project fetch hasn't returned
      // yet, or (b) the session references a task that was deleted
      // from the project (orphan). Show different labels so the user
      // can tell them apart.
      const label = project ? '(orphan - task missing)' : '(loading project...)'
      const color = project ? 'var(--red)' : 'var(--text-dim)'
      return (
        <div key={`${projectId}/${s.task_id}`} style={{ fontSize: 10, color, padding: 6 }}>
          {s.task_id} {label}
        </div>
      )
    }
    return (
      <TaskNodeChip
        key={`${projectId}/${s.task_id}`}
        taskId={s.task_id}
        task={task}
        // Pass the full project tasks map so the chip can compute
        // `blocked` for tasks whose deps haven't reached an unblocking
        // status. Without this, blocked tasks would render their
        // stored status (purple `not_started`) instead of dim
        // `blocked`, mismatching the GraphView.
        tasksMap={project?.tasks}
        hasTickets={project?.has_tickets !== false}
        hasSession={true}
        // Read state from the global snapshot map -- it's patched
        // live by SSE and is the source of truth. The DB column on
        // the row is the fallback for the brief window before the
        // snapshot has caught up after a fresh load.
        sessionStatus={
          sessionStateMap[s.tmux_name]?.state
            ?? (s.running ? s.status : 'stopped')
        }
        selected={isSelected}
        dimmed={!!selectedTask && !isSelected}
        onClick={() => handleSelectNode(projectId, s.task_id)}
      />
    )
  }

  return (
    <div data-testid="all-live-tasks-page" style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Left: task node chips grouped by project */}
      <div
        onClick={handleLeftPaneBlankClick}
        style={{
          width: selectedPR ? `${sessListRatio}%` : '50%',
          borderRight: '1px solid var(--border)',
          overflowY: 'auto',
          padding: 16,
          transition: 'width 0.2s',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <h2 style={{ fontSize: 16, margin: 0, flex: 1 }}>Live Tasks</h2>
          {actionResult && actionState === 'done' && (
            <span style={{ fontSize: 11, color: 'var(--green)' }}>{actionResult}</span>
          )}
          <button className="btn-action" style={{ fontSize: 11 }} disabled={actionState === 'loading'} onClick={() => handleKillByStatus(['stopped'])}>Kill Stopped</button>
          <button className="btn-action" style={{ fontSize: 11 }} disabled={actionState === 'loading'} onClick={() => handleKillByStatus(['idle'])}>Kill Idle</button>
          <button className="btn-action green" style={{ fontSize: 11 }} disabled={actionState === 'loading'} onClick={handleRebuild}>
            {actionState === 'loading' ? '...' : 'Rebuild'}
          </button>
        </div>

        {/* View-mode segmented control. Compact pill toggle so the
            user can flip between project grouping (browse) and the
            three-bucket attention view (act-on-this). Persisted in
            localStorage. */}
        <div
          role="tablist"
          aria-label="View mode"
          data-testid="all-live-tasks-view-mode"
          style={{
            display: 'inline-flex',
            border: '1px solid var(--border)',
            borderRadius: 999,
            padding: 2,
            marginBottom: 16,
            background: 'var(--card-bg)',
            fontSize: 11,
          }}
        >
          {(['by-project', 'by-attention'] as const).map((mode) => {
            const active = viewMode === mode
            const label = mode === 'by-project' ? 'By Project' : 'By Attention'
            return (
              <button
                key={mode}
                role="tab"
                aria-selected={active}
                data-testid={`view-mode-${mode}`}
                onClick={() => setViewMode(mode)}
                style={{
                  border: 'none',
                  background: active ? 'var(--accent)' : 'transparent',
                  color: active ? 'var(--accent-text, #fff)' : 'var(--text)',
                  padding: '4px 12px',
                  borderRadius: 999,
                  cursor: 'pointer',
                  fontWeight: active ? 600 : 500,
                  transition: 'background 0.15s ease',
                }}
              >
                {label}
              </button>
            )
          })}
        </div>

        {groupsWithSessions.length === 0
            && liveCronJobs.length === 0
            && liveReviews.length === 0
            && liveTickets.length === 0
            && liveProjectManagers.length === 0 ? (
          <div style={{ color: 'var(--text-dim)', fontSize: 13 }}>No active sessions.</div>
        ) : viewMode === 'by-project' ? (
          <div style={{ position: 'relative' }}>
            {liveProjectManagers.length > 0 && (
              <div data-testid="live-section-manager" style={{ marginBottom: 16 }}>
                <div
                  style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--accent)', cursor: 'pointer' }}
                  onClick={() => onNavigate?.('', 'all-tasks')}
                >
                  Project Managers
                  <span style={{ fontSize: 10, color: 'var(--text-dim)', marginLeft: 6 }}>({liveProjectManagers.length})</span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                  {liveProjectManagers.map((manager) => {
                    const isSel = selectedExtra?.kind === 'manager'
                      && selectedExtra.projectId === manager.project_id
                    return (
                      <LiveSessionChip
                        key={`manager-${manager.project_id}`}
                        kind="manager"
                        label={manager.project_name || manager.project_id}
                        sublabel={manager.project_id}
                        status={sessionStateMap[manager.tmux_name]?.state || manager.status || ''}
                        selected={isSel}
                        dimmed={!!selectedExtra && !isSel || !!selectedTask}
                        onClick={() => handleClickManager(manager)}
                      />
                    )
                  })}
                </div>
              </div>
            )}
            {groupsWithSessions.map(([projectId, group]) => {
              // Build a minimal `Project` from the group payload so TaskCard
              // and its helpers see the same shape they do in ProjectPage.
              // Fields the chip / task card don't touch (description /
              // progress / task_counts) default to empty -- TaskCard never
              // reads them on this page.
              const project: Project | null = group ? projectFromGroup(group) : null
              return (
                <div key={projectId} style={{ marginBottom: 16 }}>
                  <div
                    style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--accent)', cursor: 'pointer' }}
                    onClick={() => onNavigate?.(projectId, 'graph')}
                  >
                    {group.name}
                    <span style={{ fontSize: 10, color: 'var(--text-dim)', marginLeft: 6 }}>({group.sessions.length})</span>
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    {group.sessions.map((s) => renderChip(s, projectId, project))}
                  </div>
                </div>
              )
            })}
            {liveCronJobs.length > 0 && (
              <div data-testid="live-section-cron" style={{ marginBottom: 16 }}>
                <div
                  style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--accent)', cursor: 'pointer' }}
                  onClick={() => onNavigate?.('', 'cron-jobs')}
                >
                  Cron Jobs
                  <span style={{ fontSize: 10, color: 'var(--text-dim)', marginLeft: 6 }}>({liveCronJobs.length})</span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                  {liveCronJobs.map((job) => {
                    const isSel = selectedExtra?.kind === 'cron'
                      && selectedExtra.jobId === job.id
                    return (
                      <LiveSessionChip
                        key={`cron-${job.id}`}
                        kind="cron"
                        label={job.name}
                        sublabel={job.schedule}
                        status={sessionStateMap[job.session_name || '']?.state || ''}
                        selected={isSel}
                        dimmed={!!selectedExtra && !isSel
                          || !!selectedTask}
                        onClick={() => handleClickCron(job)}
                      />
                    )
                  })}
                </div>
              </div>
            )}
            {liveReviews.length > 0 && (
              <div data-testid="live-section-review" style={{ marginBottom: 16 }}>
                <div
                  style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--accent)', cursor: 'pointer' }}
                  onClick={() => onNavigate?.('', 'all-reviews')}
                >
                  Reviews
                  <span style={{ fontSize: 10, color: 'var(--text-dim)', marginLeft: 6 }}>({liveReviews.length})</span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                  {liveReviews.map((pr) => {
                    const isSel = selectedExtra?.kind === 'review'
                      && selectedExtra.url === pr.url
                    return (
                      <LiveSessionChip
                        key={`review-${pr.url}`}
                        kind="review"
                        label={`#${pr.number} ${pr.repo || ''}`}
                        sublabel={pr.title}
                        status={sessionStateMap[pr.session_name || '']?.state || ''}
                        selected={isSel}
                        dimmed={!!selectedExtra && !isSel
                          || !!selectedTask}
                        onClick={() => handleClickReview(pr)}
                      />
                    )
                  })}
                </div>
              </div>
            )}
            {liveTickets.length > 0 && (
              <div data-testid="live-section-ticket" style={{ marginBottom: 16 }}>
                <div
                  style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--accent)', cursor: 'pointer' }}
                  onClick={() => onNavigate?.('', 'tickets')}
                >
                  Tickets
                  <span style={{ fontSize: 10, color: 'var(--text-dim)', marginLeft: 6 }}>({liveTickets.length})</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {liveTickets.map((t) => {
                    const inst = t.instance_name || ''
                    const isSel = selectedExtra?.kind === 'ticket'
                      && selectedExtra.key === t.key
                      && selectedExtra.instance === inst
                    const dim = !!selectedExtra && !isSel || !!selectedTask
                    return (
                      <div key={`ticket-${inst}:${t.key}`}
                           style={{ opacity: dim ? 0.4 : 1,
                                    transition: 'opacity 0.15s' }}>
                        <TicketNode
                          ticket={t}
                          active={isSel}
                          onClick={() => handleClickTicket(t)}
                        />
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        ) : (
          // By-attention: flatten every session into three urgency
          // buckets so the user can answer "what needs me right now?"
          // by glancing at the top of the page. Empty buckets render
          // their header anyway -- a deliberate "nothing here" tells
          // the user the queue is clear, which is itself information.
          <AttentionView
            groups={groupsWithSessions}
            renderChip={renderChip}
            liveCronJobs={liveCronJobs}
            liveReviews={liveReviews}
            liveTickets={liveTickets}
            liveProjectManagers={liveProjectManagers}
            sessionStateMap={sessionStateMap}
            onClickCron={handleClickCron}
            onClickReview={handleClickReview}
            onClickTicket={handleClickTicket}
            onClickManager={handleClickManager}
          />
        )}
      </div>

      {/* Middle: endless list of expanded task cards. Single mask overlays the whole list;
          the selected card rises above with z-index. */}
      <div
        ref={middlePaneRef}
        style={{
          // 3-col layout: 25% / 40% / 35% (left / middle / PR detail).
          // Middle gets the largest slice since the TaskCard + terminal
          // is the main interaction surface.
          width: selectedPR ? `${sessDetailRatio}%` : '50%',
          overflowY: 'auto', padding: 12,
          flexShrink: 0,
          position: 'relative',
          transition: 'width 0.2s',
        }}
      >
        {flatSessions.length === 0
            && liveCronJobs.length === 0
            && liveReviews.length === 0
            && liveTickets.length === 0
            && liveProjectManagers.length === 0 && (
          <div style={{ color: 'var(--text-dim)', fontSize: 12 }}>No live tasks.</div>
        )}
        {/* Top spacer so the first card can scroll to vertical center */}
        {(flatSessions.length > 0
          || liveCronJobs.length > 0
          || liveReviews.length > 0
          || liveTickets.length > 0
          || liveProjectManagers.length > 0) && <div style={{ height: '50vh' }} />}
        <div style={{ position: 'relative' }}>
          {/* Project-manager cards. These are live sessions even though
              they do not belong to a task row, so render them alongside
              cron/review/ticket extras. */}
          {liveProjectManagers.map((manager) => {
            const key = `manager:${manager.project_id}`
            const isSelected = selectedExtra?.kind === 'manager'
              && selectedExtra.projectId === manager.project_id
            const dimmed = (!isSelected
              && (!!selectedExtra || !!selectedTask))
            return (
              <div
                key={key}
                ref={el => { cardRefs.current[key] = el }}
                data-testid={`live-card-manager-${manager.project_id}`}
                onClick={() => {
                  if (!isSelected) handleClickManager(manager)
                }}
                style={{
                  position: 'relative',
                  marginBottom: 12, scrollMarginTop: 8,
                  borderRadius: 8, padding: 8,
                  border: '1px solid var(--border)',
                  outline: isSelected ? '2px solid var(--accent)' : undefined,
                  cursor: isSelected ? 'default' : 'pointer',
                  background: isSelected ? 'var(--panel-bg)' : 'var(--card-bg)',
                  opacity: dimmed ? 0.35 : 1,
                  transition: 'opacity 0.15s',
                }}
              >
                <div style={{
                  fontSize: 10, color: 'var(--text-dim)',
                  padding: '0 0 4px 0',
                }}>
                  Project Manager
                </div>
                <ProjectSessionCard
                  projectId={manager.project_id}
                  projectName={manager.project_name || manager.project_id}
                />
              </div>
            )
          })}

          {flatSessions.map(({ projectId, projectName, taskId }) => {
            const group = sessionsData?.[projectId] || null
            const project: Project | null = group
              ? { id: group.id, name: group.name, description: '', progress: 0, task_counts: {}, has_tickets: group.has_tickets, tasks: group.tasks }
              : null
            const task: Task | undefined = project?.tasks?.[taskId]
            const key = `${projectId}:${taskId}`
            const isSelected = selectedTask?.projectId === projectId && selectedTask?.taskId === taskId
            const dimmed = (!!selectedTask && !isSelected) || !!selectedExtra
            return (
              <div
                key={key}
                ref={el => { cardRefs.current[key] = el }}
                onClick={() => { if (!isSelected) handleSelectNode(projectId, taskId) }}
                style={{
                  position: 'relative',
                  marginBottom: 12, scrollMarginTop: 8,
                  borderRadius: 8,
                  outline: isSelected ? '2px solid var(--accent)' : undefined,
                  cursor: isSelected ? 'default' : 'pointer',
                  background: isSelected ? 'var(--panel-bg)' : undefined,
                  opacity: dimmed ? 0.35 : 1,
                  transition: 'opacity 0.15s',
                }}
              >
                {!task || !project ? (
                  <div style={{ fontSize: 11, color: project ? 'var(--red)' : 'var(--text-dim)', padding: 6 }}>
                    {taskId} {project ? '(orphan - task missing)' : '(loading project...)'}
                  </div>
                ) : (
                  <>
                    <div style={{ fontSize: 10, color: 'var(--text-dim)', padding: '2px 4px 4px' }}>
                      {projectName}
                    </div>
                    <TaskCard
                      project={project}
                      taskId={taskId}
                      actions={actions}
                      forceFullRender
                      sessionExpanded={isSelected}
                      externalAction={isSelected ? externalAction : null}
                      onOpenAction={(actionId, prNumber, prRepo, customPrompt) =>
                        handleOpenAction(projectId, taskId, actionId, prNumber, prRepo, customPrompt)
                      }
                      onClickPRNumber={isSelected ? handleClickPR : undefined}
                    />
                  </>
                )}
              </div>
            )
          })}

          {/* Cron job cards. Inline so the user manages the cron
              schedule + session right here without bouncing to the
              Cron Jobs page. */}
          {liveCronJobs.map((job) => {
            const key = `cron:${job.id}`
            const isSelected = selectedExtra?.kind === 'cron'
              && selectedExtra.jobId === job.id
            const dimmed = (!isSelected
              && (!!selectedExtra || !!selectedTask))
            return (
              <div
                key={key}
                ref={el => { cardRefs.current[key] = el }}
                data-testid={`live-card-cron-${job.id}`}
                onClick={() => {
                  if (!isSelected) handleClickCron(job)
                }}
                style={{
                  position: 'relative',
                  marginBottom: 12, scrollMarginTop: 8,
                  borderRadius: 8, padding: 8,
                  border: '1px solid var(--border)',
                  outline: isSelected ? '2px solid var(--accent)' : undefined,
                  cursor: isSelected ? 'default' : 'pointer',
                  background: isSelected ? 'var(--panel-bg)' : 'var(--card-bg)',
                  opacity: dimmed ? 0.35 : 1,
                  transition: 'opacity 0.15s',
                }}
              >
                <div style={{
                  fontSize: 10, color: 'var(--text-dim)',
                  padding: '0 0 4px 0',
                }}>
                  Cron Job
                </div>
                <CronCard
                  job={job}
                  onChanged={refetchCron}
                  onDeleted={() => {
                    setSelectedExtra(null)
                    refetchCron()
                  }}
                />
              </div>
            )
          })}

          {/* Review cards. Same pattern -- inline review session +
              workflow controls with no page-switch. */}
          {liveReviews.map((pr) => {
            const key = `review:${pr.url}`
            const isSelected = selectedExtra?.kind === 'review'
              && selectedExtra.url === pr.url
            const dimmed = (!isSelected
              && (!!selectedExtra || !!selectedTask))
            return (
              <div
                key={key}
                ref={el => { cardRefs.current[key] = el }}
                data-testid={`live-card-review-${pr.number}`}
                onClick={() => {
                  if (!isSelected) handleClickReview(pr)
                }}
                style={{
                  position: 'relative',
                  marginBottom: 12, scrollMarginTop: 8,
                  borderRadius: 8, padding: 8,
                  border: '1px solid var(--border)',
                  outline: isSelected ? '2px solid var(--accent)' : undefined,
                  cursor: isSelected ? 'default' : 'pointer',
                  background: isSelected ? 'var(--panel-bg)' : 'var(--card-bg)',
                  opacity: dimmed ? 0.35 : 1,
                  transition: 'opacity 0.15s',
                }}
              >
                <div style={{
                  fontSize: 10, color: 'var(--text-dim)',
                  padding: '0 0 4px 0',
                }}>
                  Review {pr.repo}
                </div>
                <ReviewCard
                  reviewUrl={pr.url}
                  repo={pr.repo}
                  number={pr.number}
                />
              </div>
            )
          })}

          {/* Ticket cards. Same inline pattern so the user can read
              JIRA detail, run triage, post comments, transition state
              without bouncing to the Tickets page. */}
          {liveTickets.map((t) => {
            const inst = t.instance_name || ''
            const key = `ticket:${inst}:${t.key}`
            const isSelected = selectedExtra?.kind === 'ticket'
              && selectedExtra.key === t.key
              && selectedExtra.instance === inst
            const detailKey = `${inst}:${t.key}`
            const ticketForCard = isSelected
              ? (ticketDetails[detailKey] || t)
              : t
            const dimmed = (!isSelected
              && (!!selectedExtra || !!selectedTask))
            return (
              <div
                key={key}
                ref={el => { cardRefs.current[key] = el }}
                data-testid={`live-card-ticket-${t.key}`}
                onClick={() => {
                  if (!isSelected) handleClickTicket(t)
                }}
                style={{
                  position: 'relative',
                  marginBottom: 12, scrollMarginTop: 8,
                  borderRadius: 8, padding: 8,
                  border: '1px solid var(--border)',
                  outline: isSelected ? '2px solid var(--accent)' : undefined,
                  cursor: isSelected ? 'default' : 'pointer',
                  background: isSelected ? 'var(--panel-bg)' : 'var(--card-bg)',
                  opacity: dimmed ? 0.35 : 1,
                  transition: 'opacity 0.15s',
                }}
              >
                <div style={{
                  fontSize: 10, color: 'var(--text-dim)',
                  padding: '0 0 4px 0',
                }}>
                  Ticket {t.key}
                </div>
                <TicketTaskCard ticket={ticketForCard} />
              </div>
            )
          })}
        </div>
        {/* Bottom spacer so the last card can scroll to vertical center */}
        {(flatSessions.length > 0
          || liveCronJobs.length > 0
          || liveReviews.length > 0
          || liveTickets.length > 0
          || liveProjectManagers.length > 0) && <div style={{ height: '50vh' }} />}
      </div>

      {/* Right: PR detail (35% in the 3-col layout). */}
      {selectedPR && (
        <div style={{
          width: `${sessTaskRatio}%`, flexShrink: 0,
          overflowY: 'auto', borderLeft: '1px solid var(--border)', padding: 12,
        }}>
          <PRCard
            repo={selectedPR.repo}
            number={selectedPR.number}
            projectId={selectedTask?.projectId}
            taskId={selectedTask?.taskId}
            onOpenAction={(actionId, customPrompt) => {
              // customPrompt carries Ask-Agent selection / draft-reply context.
              // Dropping it means Agent gets no code or comment URL.
              // Stamp with selectedTask.taskId so a stale event can't fire
              // on a different TaskCard after the user switches selection.
              if (selectedTask) {
                setExternalAction({ actionId, taskId: selectedTask.taskId, prNumber: selectedPR.number, prRepo: selectedPR.repo, customPrompt, ts: Date.now() })
              }
            }}
          />
        </div>
      )}

      {openingSession && (
        <div style={{
          position: 'fixed', bottom: 16, right: 16,
          background: 'var(--card-bg)', border: '1px solid var(--accent)',
          borderRadius: 8, padding: '8px 16px', fontSize: 12, color: 'var(--accent)',
        }}>
          Opening session...
        </div>
      )}
    </div>
  )
}


// Four-bucket flat view ordered by urgency. Top bucket is the only
// one that should pull the eye; "ready for next" tells the user
// "agent is free, give it work"; in-flight is calm "leave it alone";
// bottom folds the stopped tail.
//
// Bucket assignment:
//   - needs:    needs_permission | crashed                        (red header)
//   - ready:    idle | needs_input -- agent done, awaiting user   (yellow header)
//   - inFlight: thinking | starting                               (blue header)
//   - other:    stopped | unknown                                 (muted)
//
// Empty buckets render their header with a "(0)" so the user can
// confirm "yes, nothing here" rather than wondering if it's missing.
function AttentionView({
  groups, renderChip,
  liveCronJobs, liveReviews, liveTickets, liveProjectManagers,
  sessionStateMap,
  onClickCron, onClickReview, onClickTicket, onClickManager,
}: {
  groups: Array<[string, ProjectGroup]>
  renderChip: (s: SessionEntry, projectId: string, project: Project | null) => ReactNode
  liveCronJobs: CronJob[]
  liveReviews: Array<PR & { repo: string; source?: string }>
  liveTickets: Ticket[]
  liveProjectManagers: ProjectManagerSession[]
  sessionStateMap: Record<string, { state: string }>
  onClickCron: (job: CronJob) => void
  onClickReview: (pr: PR) => void
  onClickTicket: (t: Ticket) => void
  onClickManager: (m: ProjectManagerSession) => void
}) {
  type TaskFlat = { kind: 'task'; projectId: string; project: Project; session: SessionEntry; status: string }
  type CronFlat = { kind: 'cron'; job: CronJob; status: string }
  type ReviewFlat = { kind: 'review'; pr: PR & { repo: string; source?: string }; status: string }
  type TicketFlat = { kind: 'ticket'; ticket: Ticket; status: string }
  type ManagerFlat = { kind: 'manager'; manager: ProjectManagerSession; status: string }
  type FlatEntry = TaskFlat | CronFlat | ReviewFlat | TicketFlat | ManagerFlat
  const flat: FlatEntry[] = []
  for (const [projectId, group] of groups) {
    const project: Project = {
      id: group.id, name: group.name, description: '',
      progress: 0, task_counts: {}, has_tickets: group.has_tickets,
      tasks: group.tasks,
    }
    for (const s of group.sessions) {
      flat.push({
        kind: 'task', projectId, project, session: s,
        status: s.running ? s.status : 'stopped',
      })
    }
  }
  // Live state for cron / review / ticket entries comes from the
  // global session-status snapshot, indexed by tmux name. Falls back
  // to 'idle' if the snapshot doesn't have the row yet (race window
  // between session.opened and the next snapshot refetch).
  const lookupState = (name: string | undefined) =>
    (name && sessionStateMap[name]?.state) || 'idle'
  for (const j of liveCronJobs) {
    flat.push({ kind: 'cron', job: j, status: lookupState(j.session_name) })
  }
  for (const pr of liveReviews) {
    flat.push({ kind: 'review', pr, status: lookupState(pr.session_name) })
  }
  for (const t of liveTickets) {
    flat.push({ kind: 'ticket', ticket: t, status: lookupState(t.session_name) })
  }
  for (const m of liveProjectManagers) {
    flat.push({ kind: 'manager', manager: m, status: lookupState(m.tmux_name) })
  }
  // Each chip renders to one of four flavours -- task / cron / review
  // use the same chip family; ticket gets its richer TicketNode for
  // parity with the Tickets page (priority, type, etc.).
  const renderEntry = (e: FlatEntry): ReactNode => {
    if (e.kind === 'task') {
      return renderChip(e.session, e.projectId, e.project)
    }
    if (e.kind === 'cron') {
      return (
        <LiveSessionChip
          key={`cron-${e.job.id}`} kind="cron"
          label={e.job.name} sublabel={e.job.schedule}
          status={e.status} onClick={() => onClickCron(e.job)}
        />
      )
    }
    if (e.kind === 'review') {
      return (
        <LiveSessionChip
          key={`review-${e.pr.url}`} kind="review"
          label={`#${e.pr.number} ${e.pr.repo || ''}`}
          sublabel={e.pr.title}
          status={e.status} onClick={() => onClickReview(e.pr)}
        />
      )
    }
    if (e.kind === 'manager') {
      return (
        <LiveSessionChip
          key={`manager-${e.manager.project_id}`} kind="manager"
          label={e.manager.project_name || e.manager.project_id}
          sublabel={e.manager.project_id}
          status={e.status} onClick={() => onClickManager(e.manager)}
        />
      )
    }
    return (
      <div key={`ticket-${e.ticket.instance_name || ''}:${e.ticket.key}`}
           style={{ minWidth: 240, maxWidth: 320 }}>
        <TicketNode
          ticket={e.ticket}
          active={false}
          onClick={() => onClickTicket(e.ticket)}
        />
      </div>
    )
  }
  const needs    = flat.filter(e => bucketize(e.status) === 'needs')
  const ready    = flat.filter(e => bucketize(e.status) === 'idle')
  const inFlight = flat.filter(e => bucketize(e.status) === 'flight')
  const other    = flat.filter(e => bucketize(e.status) === 'other')

  const Section = ({
    title, count, accent, entries, testid,
  }: {
    title: string
    count: number
    accent: string
    entries: FlatEntry[]
    testid: string
  }) => (
    <div data-testid={testid} style={{ marginBottom: 18 }}>
      <div
        style={{
          fontSize: 12, fontWeight: 600,
          color: accent,
          marginBottom: 8,
          textTransform: 'uppercase',
          letterSpacing: '0.5px',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}
      >
        <span>{title}</span>
        <span style={{ fontSize: 10, color: 'var(--text-dim)', fontWeight: 500 }}>
          ({count})
        </span>
        <span style={{ flex: 1, height: 1, background: 'var(--border)' }} />
      </div>
      {entries.length === 0 ? (
        <div style={{ fontSize: 11, color: 'var(--text-dim)', fontStyle: 'italic' }}>
          {title === 'Needs Attention'
            ? 'You’re all caught up.'
            : title === 'Ready for next'
              ? 'No idle sessions.'
              : 'None.'}
        </div>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {entries.map(renderEntry)}
        </div>
      )}
    </div>
  )

  return (
    <div style={{ position: 'relative' }}>
      <Section
        testid="attention-section-needs"
        title="Needs Attention"
        accent="var(--red)"
        count={needs.length}
        entries={needs}
      />
      <Section
        testid="attention-section-ready"
        title="Ready for next"
        accent="var(--yellow)"
        count={ready.length}
        entries={ready}
      />
      <Section
        testid="attention-section-in-flight"
        title="In Flight"
        accent="var(--blue)"
        count={inFlight.length}
        entries={inFlight}
      />
      <Section
        testid="attention-section-other"
        title="Other Live"
        accent="var(--text-muted)"
        count={other.length}
        entries={other}
      />
    </div>
  )
}
