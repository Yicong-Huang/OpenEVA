import { useState, useCallback, useEffect } from 'react'
import type { PR, ActionDef } from '../types'
import { useApi } from '../hooks/useApi'
import { useSSE } from '../hooks/useSSE'
import { useLayoutRatios } from '../hooks/useLayoutRatios'
import { PRNode } from '../components/PRNode'
import { PRCard } from '../components/PRCard'

import { TaskCard } from '../components/TaskCard'
import { useProject } from '../hooks/useProject'
import { api } from '../api'
import { repoFromPrUrl } from '../utils'

// In 3-pane mode (list + task panel + detail) the page reads its
// column ratios from `ui.layout.prs_col_ratios`. Other modes use
// degenerate-case widths since they have a different shape.
const DEFAULT_PRS_RATIOS = [25, 40, 35] as const

interface PRsPageProps {
  selectedPR?: { repo: string; number: number; taskId?: string; projectId?: string } | null
  onSelectPR?: (pr: { repo: string; number: number; taskId?: string; projectId?: string } | null) => void
}

type FilterStatus = 'open' | 'merged' | 'closed'

const SYNC_DONE_DISPLAY_MS = 3000
const SYNC_ERROR_DISPLAY_MS = 2000

const COLLAPSED_GROUPS_KEY = 'eva-prs-collapsed-groups'

function loadCollapsedGroups(): Record<string, boolean> {
  try {
    const stored = localStorage.getItem(COLLAPSED_GROUPS_KEY)
    return stored ? JSON.parse(stored) : {}
  } catch { return {} }
}

function saveCollapsedGroups(state: Record<string, boolean>) {
  try { localStorage.setItem(COLLAPSED_GROUPS_KEY, JSON.stringify(state)) } catch { /* ignore */ }
}

export function PRsPage({ selectedPR: selectedPRProp, onSelectPR }: PRsPageProps) {
  const [filterStatus, setFilterStatus] = useState<FilterStatus>('open')
  const [search, setSearch] = useState('')
  const [localSelectedPR, setLocalSelectedPR] = useState<{ repo: string; number: number; taskId?: string; projectId?: string } | null>(null)
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>(loadCollapsedGroups)
  const [prsListRatio, prsTaskRatio, prsDetailRatio] = useLayoutRatios(
    'ui.layout.prs_col_ratios',
    [...DEFAULT_PRS_RATIOS],
  )

  const selectedPR = selectedPRProp !== undefined ? selectedPRProp : localSelectedPR
  const setSelectedPR = onSelectPR || setLocalSelectedPR

  const [syncStatus, setSyncStatus] = useState<string | null>(null)
  const [syncPhase, setSyncPhase] = useState<'idle' | 'syncing' | 'done' | 'error'>('idle')
  const [syncUrl, setSyncUrl] = useState<string | null>('/api/all-prs/sync-stream')

  // Task panel: derived from selectedPR's task association (no separate state).
  const taskPanel = (selectedPR?.taskId && selectedPR?.projectId)
    ? { projectId: selectedPR.projectId, taskId: selectedPR.taskId }
    : null
  // Shared hook: fetches the panel's project and auto-refreshes on agent/github events.
  const { project: taskProject } = useProject(taskPanel?.projectId)
  const [taskActions, setTaskActions] = useState<ActionDef[]>([])
  const [externalAction, setExternalAction] = useState<{ actionId: string; taskId?: string; prNumber?: number; prRepo?: string; customPrompt?: string; ts: number } | null>(null)

  const { data: prData, loading, refetch } = useApi<{ groups: Record<string, { name: string; prs: PR[] }> }>(
    `/api/all-prs?status=${encodeURIComponent(filterStatus)}&search=${encodeURIComponent(search)}`,
  )

  // SSE sync
  useSSE(syncUrl, useCallback((rawData: string) => {
    try {
      const d = JSON.parse(rawData)
      if (d.phase === 'dirty') {
        setSyncPhase('syncing')
        setSyncStatus(`Syncing ${d.count} dirty PRs...`)
      } else if (d.phase === 'dirty_update') {
        setSyncPhase('syncing')
        setSyncStatus(`Dirty ${d.current}/${d.total}`)
      } else if (d.phase === 'discover') {
        setSyncPhase('syncing')
        setSyncStatus(`Found ${d.discovered} new PRs`)
      } else if (d.phase === 'update') {
        setSyncPhase('syncing')
        setSyncStatus(`${d.current}/${d.total} (#${d.pr})`)
      } else if (d.phase === 'done') {
        setSyncPhase('done')
        setSyncStatus(`${d.discovered} new, ${d.updated} updated`)
        refetch()
        setTimeout(() => { setSyncStatus(null); setSyncPhase('idle') }, SYNC_DONE_DISPLAY_MS)
      } else if (d.phase === 'start') {
        setSyncPhase('syncing')
        setSyncStatus('Starting sync...')
      }
    } catch {
      setSyncPhase('error')
      setSyncStatus('Sync failed')
      setTimeout(() => { setSyncStatus(null); setSyncPhase('idle') }, SYNC_ERROR_DISPLAY_MS)
    }
  }, [refetch]))

  const handleManualSync = useCallback(() => {
    setSyncUrl(null)
    setSyncPhase('syncing')
    setSyncStatus('Starting sync...')
    setTimeout(() => {
      setSyncUrl('/api/all-prs/sync-stream?t=' + Date.now())
    }, 50)
  }, [])

  const handleClickPR = useCallback((pr: PR) => {
    const repo = repoFromPrUrl(pr.url)
    setSelectedPR({ repo, number: pr.number, taskId: pr.task_id, projectId: pr.project })
  }, [setSelectedPR])

  // When PRDetail action button is clicked: re-emit externalAction (task panel is derived from selectedPR).
  // Stamp with the target taskId so TaskCard ignores this event after the
  // user switches to a different PR/task (state is shared across mounts).
  const handleOpenAction = useCallback((actionId: string, customPrompt?: string) => {
    if (!selectedPR?.taskId || !selectedPR?.projectId) return
    setExternalAction({ actionId, taskId: selectedPR.taskId, prNumber: selectedPR.number, prRepo: selectedPR.repo, customPrompt, ts: Date.now() })
  }, [selectedPR])

  // Fetch task-context actions for the TaskCard
  useEffect(() => {
    api.getActions('task')
      .then((data) => setTaskActions(data.actions || []))
      .catch(() => setTaskActions([]))
  }, [])

  // Mark a task PR as "seen" whenever the user opens it -- snapshots
  // the current comment_count into last_seen_comment_count so the
  // "N new" badge clears. Re-fetches the list so the cleared badge
  // shows up in the queue without waiting for the next sync tick.
  useEffect(() => {
    if (!selectedPR) return
    const num = selectedPR.number
    let cancelled = false
    api.markPrSeen(num).then(() => {
      if (!cancelled) refetch()
    }).catch(() => { /* best-effort -- badge resets on next sync */ })
    return () => { cancelled = true }
  }, [selectedPR, refetch])

  const groups = prData?.groups || {}
  const allPrs = Object.values(groups).flatMap((g) => g.prs)
  const hasTaskPanel = !!(taskPanel && taskProject)

  const toggleGroup = useCallback((key: string) => {
    setCollapsedGroups((prev) => {
      const next = { ...prev, [key]: !prev[key] }
      saveCollapsedGroups(next)
      return next
    })
  }, [])

  return (
    <div data-testid="all-prs-page" style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Left panel: PR list */}
      <div
        style={{
          width: hasTaskPanel ? `${prsListRatio}%` : selectedPR ? '40%' : '50%',
          borderRight: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          transition: 'width 0.2s',
        }}
      >
        {/* Header */}
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <h2 style={{ fontSize: 16, margin: 0, flex: 1 }}>Pull Requests</h2>
            {syncStatus && syncPhase !== 'done' && syncPhase !== 'error' && (
              <span style={{ fontSize: 10, color: 'var(--accent)' }}>{syncStatus}</span>
            )}
            <button
              className="btn-action green"
              data-testid="sync-prs-btn"
              style={{
                fontSize: 11,
                ...(syncPhase === 'done'
                  ? { background: 'var(--green)', color: '#000', borderColor: 'var(--green)' }
                  : syncPhase === 'error'
                    ? { background: 'var(--red)', color: '#fff', borderColor: 'var(--red)' }
                    : {}),
              }}
              disabled={syncPhase === 'syncing'}
              onClick={handleManualSync}
            >
              {syncPhase === 'done' ? syncStatus : syncPhase === 'error' ? syncStatus : 'Sync from GitHub'}
            </button>
          </div>
          {/* Filter tabs */}
          <div className="view-tabs" style={{ marginBottom: 8 }}>
            {(['open', 'merged', 'closed'] as const).map((s) => (
              <div
                key={s}
                className={`view-tab${filterStatus === s ? ' active' : ''}`}
                onClick={() => setFilterStatus(s)}
              >
                {s.charAt(0).toUpperCase() + s.slice(1)}
              </div>
            ))}
          </div>
          {/* Search */}
          <input
            data-testid="pr-search"
            type="text"
            placeholder="Search PRs..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              width: '100%',
              padding: '6px 10px',
              background: 'var(--panel-bg)',
              border: '1px solid var(--border)',
              borderRadius: 6,
              color: 'var(--text)',
              fontSize: 12,
            }}
          />
        </div>

        {/* PR list */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
          {loading ? (
            <div style={{ padding: 16, color: 'var(--text-dim)', fontSize: 12 }}>Loading...</div>
          ) : allPrs.length === 0 ? (
            <div style={{ padding: 16, color: 'var(--text-dim)', fontSize: 12 }}>No PRs found.</div>
          ) : (
            Object.entries(groups).map(([repoKey, group]) => {
              const isCollapsed = !!collapsedGroups[repoKey]
              return (
                <div key={repoKey}>
                  <div
                    onClick={() => toggleGroup(repoKey)}
                    style={{
                      padding: '6px 16px',
                      fontSize: 11,
                      fontWeight: 600,
                      color: 'var(--text-dim)',
                      textTransform: 'uppercase',
                      letterSpacing: 0.5,
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                      userSelect: 'none',
                    }}
                    data-testid={`pr-group-header-${repoKey}`}
                  >
                    <span style={{
                      fontSize: 9,
                      transition: 'transform 0.15s',
                      transform: isCollapsed ? 'rotate(0deg)' : 'rotate(90deg)',
                      display: 'inline-block',
                    }}>{'\u25B6'}</span>
                    {group.name} ({group.prs.length})
                  </div>
                  {!isCollapsed && group.prs.map((pr) => (
                    <div
                      key={`${repoKey}-${pr.number}`}
                      style={{
                        padding: '2px 12px',
                        background:
                          selectedPR?.number === pr.number ? 'rgba(99,102,241,0.1)' : 'transparent',
                        cursor: 'pointer',
                      }}
                    >
                      <PRNode
                        pr={pr}
                        showMeta
                        showTask
                        onClick={() => handleClickPR(pr)}
                        onRefresh={async () => {
                          await api.refreshPR(pr.number)
                          refetch()
                        }}
                      />
                    </div>
                  ))}
                </div>
              )
            })
          )}
        </div>
      </div>

      {/* Middle panel: TaskCard when a task is associated, else PR detail.
          Convention shared across all 3-col pages: TaskCard (action buttons
          + terminal) gets the widest slice (40%); PRDetail goes to the
          right (35%) for reading + inline comments. */}
      {hasTaskPanel && taskProject ? (
        <div
          data-testid="task-side-panel"
          style={{
            width: `${prsTaskRatio}%`, flexShrink: 0,
            borderRight: '1px solid var(--border)',
            overflowY: 'auto',
            padding: '0 12px',
            transition: 'width 0.2s',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 4px' }}>
            <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>
              {taskPanel.projectId} / {taskPanel.taskId}
            </span>
            <button
              className="btn-action"
              style={{ padding: '2px 8px', fontSize: 10 }}
              onClick={() => {
                // Close the task panel by detaching the task association on the
                // selected PR (taskPanel is derived from selectedPR now).
                if (selectedPR) {
                  setSelectedPR({ repo: selectedPR.repo, number: selectedPR.number })
                }
              }}
            >
              Close
            </button>
          </div>
          <TaskCard
            project={taskProject}
            taskId={taskPanel.taskId}
            actions={taskActions}
            forceFullRender

            externalAction={externalAction}
            onOpenAction={() => {}}
          />
        </div>
      ) : null}

      {/* Right panel: PR detail. Width is 35% in 3-col, fills in 2-col. */}
      <div
        style={{
          width: hasTaskPanel ? `${prsDetailRatio}%` : undefined,
          flex: hasTaskPanel ? undefined : 1,
          flexShrink: 0,
          overflowY: 'auto',
          padding: 16,
          display: 'flex',
          flexDirection: 'column',
          transition: 'width 0.2s',
        }}
      >
        {!selectedPR && (
          <div style={{ color: 'var(--text-dim)', fontSize: 13, margin: 'auto', textAlign: 'center' }}>
            Select a PR to view details
          </div>
        )}
        {selectedPR && (
          <PRCard
            repo={selectedPR.repo}
            number={selectedPR.number}
            projectId={selectedPR.projectId}
            taskId={selectedPR.taskId}
            onOpenAction={handleOpenAction}
          />
        )}
      </div>
    </div>
  )
}
