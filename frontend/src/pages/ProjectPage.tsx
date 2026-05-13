import { useState, useCallback, useMemo } from 'react'
import type { Project, ActionDef } from '../types'
import { useApi } from '../hooks/useApi'
import { useEventBus } from '../hooks/useEventBus'
import { TaskCard } from '../components/TaskCard'
import { GraphView } from '../components/GraphView'
import { StatusDot } from '../components/StatusDot'
import { PRCard } from '../components/PRCard'
import { ProjectSessionCard } from '../components/ProjectSessionCard'
import { useAlert } from '../components/Alert'
import { api } from '../api'
import { repoFromPrUrl } from '../utils'

/**
 * ProjectPage -- the single per-project surface (formerly "Task Tracker").
 *
 * History: this page used to host three sub-views (graph / list /
 * sessions) selected via tab strip. The list and sessions tabs were
 * dropped because the same affordances already live elsewhere:
 *   - "Task Cards" list -> All Live Tasks (when sessions are running)
 *     and the per-task TaskCard side panel here.
 *   - "Sessions" list -> All Live Tasks (project sessions group) and
 *     SessionCard inside each TaskCard.
 *
 * Now: clicking a project in the sidebar lands directly on this page,
 * which is the graph + the side TaskCard + the side PRCard. No tabs.
 */

interface ProjectPageProps {
  projectId: string
  selectedTask: string | null
  onSelectTask: (taskId: string | null) => void
  selectedPR?: { repo: string; number: number } | null
  onSelectPR?: (pr: { repo: string; number: number } | null) => void
}


export function ProjectPage({
  projectId, selectedTask, onSelectTask,
  selectedPR: selectedPRProp, onSelectPR,
}: ProjectPageProps) {
  const { data: project, loading: projectLoading, error: projectError, refetch: refetchProject, mutate: mutateProject } = useApi<Project>(
    `/api/projects/${encodeURIComponent(projectId)}`,
  )
  const { data: actionsData } = useApi<{ actions: ActionDef[] }>('/api/actions?context=task')

  const actions = actionsData?.actions || []

  // Event-driven refetch: SSE events trigger project data refresh
  useEventBus('agent.*', useCallback(() => {
    refetchProject()
  }, [refetchProject]))

  // session.opened / session.killed events fire immediately from the
  // server (don't wait 2-5s for agent's hook). This removes the "Opening
  // session..." / "Killing..." stalls in TaskCard without a manual refresh.
  useEventBus('session.*', useCallback(() => {
    refetchProject()
  }, [refetchProject]))

  useEventBus('github.*', useCallback(() => {
    refetchProject()
  }, [refetchProject]))

  // Task events: surgical update -- only refetch the changed task
  useEventBus('task.*', useCallback((event: Record<string, unknown>) => {
    const eventType = typeof event.type === 'string' ? event.type : ''
    const taskProject = typeof event.session === 'string' ? event.session : ''
    if (taskProject !== projectId) return  // different project
    const title = typeof event.title === 'string' ? event.title : ''
    const taskId = title.replace(/^Task \w+: /, '')  // "Task updated: my-task" -> "my-task"
    if (eventType === 'task.deleted') {
      mutateProject((prev) => {
        if (!prev) return prev
        const next = { ...prev.tasks }
        delete next[taskId]
        return { ...prev, tasks: next }
      })
      // Clear selection if deleted task was selected
      if (taskId === selectedTask) onSelectTask(null)
    } else if (taskId) {
      // created or updated: fetch single task and merge
      api.getTask(projectId, taskId).then((task) => {
        mutateProject((prev) => {
          if (!prev) return prev
          return { ...prev, tasks: { ...prev.tasks, [taskId]: task } }
        })
      }).catch(() => refetchProject())  // fallback to full refetch on error
    }
  }, [projectId, mutateProject, refetchProject, selectedTask, onSelectTask]))

  const [openingSession, setOpeningSession] = useState(false)
  const { alert } = useAlert()
  const [externalAction, setExternalAction] = useState<{ actionId: string; taskId?: string; prNumber?: number; prRepo?: string; customPrompt?: string; ts: number } | null>(null)
  const selectedPR = selectedPRProp ?? null
  // Stable identity so useCallback consumers don't invalidate every render.
  const setSelectedPR = useMemo(() => onSelectPR ?? (() => {}), [onSelectPR])

  // Wrap onSelectTask to also clear PR when deselecting task (pane click)
  const handleSelectTask = useCallback((taskId: string | null) => {
    onSelectTask(taskId)
    if (taskId === null) setSelectedPR(null)
  }, [onSelectTask, setSelectedPR])

  const handleOpenAction = useCallback(
    async (taskId: string, actionId: string, prNumber?: number, prRepo?: string, customPrompt?: string) => {
      setOpeningSession(true)
      try {
        await api.openSession({
          task_id: taskId,
          project_id: projectId,
          action_id: actionId,
          pr_number: prNumber,
          pr_repo: prRepo,
          custom_prompt: customPrompt,
        })
        refetchProject()
      } catch (e) {
        await alert({
          title: 'Failed to open session',
          message: e instanceof Error ? e.message : String(e),
          kind: 'error',
        })
      } finally {
        setOpeningSession(false)
      }
    },
    [projectId, refetchProject, alert],
  )

  // Memoize to keep stable identity between renders where the tasks dict
  // didn't change.
  const tasks = useMemo(() => project?.tasks || {}, [project?.tasks])
  const taskIds = useMemo(() => Object.keys(tasks), [tasks])

  if (projectLoading) {
    return <div style={{ padding: 24, color: 'var(--text-dim)' }}>Loading project...</div>
  }

  if (projectError) {
    return <div style={{ padding: 24, color: 'var(--red)' }}>Error: {projectError}</div>
  }

  if (!project) {
    return <div style={{ padding: 24, color: 'var(--text-dim)' }}>Project not found</div>
  }

  const taskCounts = project.task_counts || {}

  return (
    <div data-testid="project-page">
      {/* Top strip: compact project summary (left) + project-manager
          session card (right) share a row so the graph gets more
          vertical space. */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', marginBottom: 10 }}>
        <div className="project-header compact" style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <h2 style={{ margin: 0, fontSize: 16 }}>{project.name}</h2>
            <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--accent)' }}>
              {project.progress}%
            </span>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>
              {taskIds.length} tasks
            </span>
            <div className="progress-bar" style={{ flex: '1 1 120px', minWidth: 80, height: 4, marginTop: 0 }}>
              <div className="progress-fill" style={{ width: `${project.progress}%` }} />
            </div>
          </div>
          {project.description && (
            <div style={{ color: 'var(--text-dim)', fontSize: 11, marginTop: 4 }}>
              {project.description}
            </div>
          )}
          <div style={{ display: 'flex', gap: 10, marginTop: 6, flexWrap: 'wrap' }}>
            {Object.entries(taskCounts).map(([status, count]) => (
              <span key={status} style={{ fontSize: 10, display: 'flex', alignItems: 'center', gap: 4 }}>
                <StatusDot status={status} style={{ width: 7, height: 7 }} />
                {count} {status.replace('_', ' ')}
              </span>
            ))}
          </div>
        </div>

        {/* Project-manager session card sits top-right -- the long-lived
            agent coordinator for this project. */}
        <div style={{ width: '45%', flexShrink: 0 }}>
          <ProjectSessionCard projectId={projectId} projectName={project.name || projectId} />
        </div>
      </div>

      {/* Graph + side panels: graph (left) / TaskCard (middle) / PRCard (right). */}
      <div style={{ display: 'flex', gap: 0, height: 'calc(100vh - 180px)' }}>
        <div style={{
          width: selectedPR ? '25%' : selectedTask ? '45%' : '100%',
          minWidth: 0,
          transition: 'width 0.2s',
        }}>
          <GraphView
            project={project}
            onSelectTask={handleSelectTask}
            selectedTask={selectedTask}
          />
        </div>
        {/* Side panel for selected task. In 3-col layout (graph 25% /
            task 40% / PR 35%) the TaskCard gets the widest slice since
            that's where the action buttons + terminal live. */}
        {selectedTask && (
          <div style={{
            width: selectedPR ? '40%' : undefined,
            flex: selectedPR ? undefined : 1,
            flexShrink: 0,
            overflowY: 'auto',
            borderLeft: '1px solid var(--border)',
            paddingLeft: 12,
            transition: 'width 0.2s',
          }}>
            <TaskCard
              project={project}
              taskId={selectedTask}
              actions={actions}
              forceFullRender
              externalAction={externalAction}
              onOpenAction={(actionId, prNumber, prRepo, customPrompt) =>
                handleOpenAction(selectedTask, actionId, prNumber, prRepo, customPrompt)
              }
              onClickPRNumber={(pr) => {
                const repo = repoFromPrUrl(pr.url)
                setSelectedPR({ repo, number: pr.number })
              }}
            />
          </div>
        )}
        {selectedPR && (
          <div style={{ width: '35%', flexShrink: 0, overflowY: 'auto', borderLeft: '1px solid var(--border)', paddingLeft: 12 }}>
            <PRCard
              repo={selectedPR.repo}
              number={selectedPR.number}
              projectId={projectId}
              taskId={selectedTask || undefined}
              onOpenAction={(actionId, customPrompt) => {
                // customPrompt is populated by Ask Agent / Draft reply
                // callbacks inside PRDetail -- must be forwarded, otherwise
                // the selected code / comment URL is silently dropped.
                if (selectedTask) {
                  setExternalAction({ actionId, taskId: selectedTask, prNumber: selectedPR?.number, prRepo: selectedPR?.repo, customPrompt, ts: Date.now() })
                }
              }}
            />
          </div>
        )}
      </div>

      {openingSession && (
        <div
          style={{
            position: 'fixed',
            bottom: 16,
            right: 16,
            background: 'var(--card-bg)',
            border: '1px solid var(--accent)',
            borderRadius: 8,
            padding: '8px 16px',
            fontSize: 12,
            color: 'var(--accent)',
          }}
        >
          Opening session...
        </div>
      )}
    </div>
  )
}
