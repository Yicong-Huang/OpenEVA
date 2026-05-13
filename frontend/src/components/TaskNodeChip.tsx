import type { Task } from '../types'
import { TaskNode } from './GraphView'
import { type TaskNodeData, latestPrCiStatus } from './graphShared'
import { isTaskBlocked, TERMINAL_TASK_STATUSES } from '../utils/taskHelpers'

interface TaskNodeChipProps {
  taskId: string
  task: Task
  /** All tasks in the same project, keyed by id. Used to derive
   *  the computed `blocked` status -- a task with deps that haven't
   *  reached an unblocking status. Without it the chip would render
   *  the stored status (red `not_started`) instead of dim `blocked`,
   *  mismatching the GraphView. */
  tasksMap?: Record<string, Task>
  hasTickets?: boolean
  hasSession?: boolean
  /** Live agent session status driving the footer dot. */
  sessionStatus?: string
  selected?: boolean
  dimmed?: boolean
  onClick?: () => void
}


// `TERMINAL_TASK_STATUSES` lives in `utils/taskHelpers.ts` so
// GraphView, TaskNodeChip, TaskCard, and any future renderer all
// share one source of truth for what counts as a finished task.


/**
 * Standalone task node "chip" used on the All Live Tasks page.
 *
 * This is a thin adapter around the GraphView `<TaskNode>`: same
 * component, same DOM structure, same visuals. The only delta is
 * `hideHandles: true` which suppresses the React Flow edge ports
 * (irrelevant in a flat list).
 *
 * Doing it this way means there's exactly ONE place the task card
 * is rendered -- if a future iteration changes the visual or adds a
 * new row, both pages get the change for free, and they never drift
 * apart again.
 */
export function TaskNodeChip({
  taskId,
  task,
  tasksMap,
  hasTickets = true,
  hasSession,
  sessionStatus,
  selected,
  dimmed,
  onClick,
}: TaskNodeChipProps) {
  // Effective status: stored, except `blocked` is computed -- mirrors
  // the GraphView's `getLayoutedElements` logic.
  const stored = task?.status || 'not_started'
  const isTerminal = TERMINAL_TASK_STATUSES.has(stored)
  const blocked = !isTerminal && tasksMap
    ? isTaskBlocked(taskId, tasksMap)
    : false
  const status = blocked ? 'blocked' : stored

  const latestHistory = (task?.history && task.history.length > 0)
    ? task.history[task.history.length - 1]
    : null

  // Build a TaskNodeData shaped exactly like the graph would build
  // it. Callbacks are no-ops because the chip handles its own click;
  // the `onClick` prop here lives on the wrapping div, not the
  // TaskNode itself.
  const data: TaskNodeData = {
    taskId,
    status,
    type: task?.type ?? null,
    updatedAt: task?.updated_at ?? null,
    latestHistory,
    sessionStatus,
    isMini: false,
    isExpanded: true,
    isSelected: !!selected,
    ticketId: task?.ticket_id ?? null,
    ticketUrl: task?.ticket_url ?? null,
    prCount: (task?.prs || []).length,
    latestPrCiStatus: latestPrCiStatus(task?.prs || []),
    hasTickets,
    hasSession: !!hasSession,
    highlighted: dimmed ? false : null,
    isNewlyCreated: false,
    isDropTarget: false,
    hideHandles: true,
    onSelect: () => { /* chip's outer onClick handles selection */ },
    onToggleExpand: () => { /* chip is always expanded */ },
  }

  return (
    <div
      data-testid={`task-node-chip-${taskId}`}
      data-status={status}
      onClick={onClick}
      style={{
        // Wrapper isn't styled -- TaskNode renders its own card.
        // We just intercept the click and exposed testid/data-status
        // for tests that pre-date the refactor.
        cursor: 'pointer',
      }}
    >
      <TaskNode data={data} />
    </div>
  )
}
