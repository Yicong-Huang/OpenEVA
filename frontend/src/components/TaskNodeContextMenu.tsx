import { useAlert } from './Alert'
import { api } from '../api'
import { STATUS_COLORS, SETTABLE_STATUSES } from './graphShared'

interface TaskNodeContextMenuProps {
  projectId: string
  taskId: string
  /** Tasks with a ticket are protected from deletion (backend enforces
   *  this too); the Delete item renders disabled when set. */
  hasTicket?: boolean
  /** Stored status of the task, so the matching "Set status" row can be
   *  marked as current (non-clickable). */
  currentStatus?: string
  /** Screen coordinates of the right-click, used to position the menu. */
  x: number
  y: number
  onClose: () => void
}

/**
 * Right-click menu for a task node: "Set status" (all settable statuses)
 * + "Delete". Shared by the GraphView canvas and the All Live Tasks page
 * so the two never drift apart -- same items, same behaviour, one source.
 *
 * `blocked` is deliberately not offered under "Set status": it is a
 * derived/effective status computed from unclosed dependencies, never a
 * stored value (see `eva_db.VALID_STATUSES` / `SETTABLE_STATUSES`).
 *
 * Mutations are fire-and-forget: the event bus / graph refetch reflects
 * the change, mirroring the existing addDep / removeDep flow. No
 * optimistic local mutation.
 */
export function TaskNodeContextMenu({
  projectId, taskId, hasTicket, currentStatus, x, y, onClose,
}: TaskNodeContextMenuProps) {
  const { confirm, alert } = useAlert()
  return (
    <div
      data-testid="task-node-context-menu"
      style={{
        position: 'fixed', left: x, top: y,
        background: 'var(--card-bg)', border: '1px solid var(--border)',
        borderRadius: 6, padding: 4, zIndex: 1000,
        boxShadow: '0 4px 12px var(--shadow-color)',
      }}
      onClick={onClose}
    >
      <div data-testid="set-status-menu">
        <div style={{ padding: '4px 16px 2px', fontSize: 10, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: 0.5 }}>
          Set status
        </div>
        {SETTABLE_STATUSES.map(({ value, label }) => {
          const isCurrent = currentStatus === value
          return (
            <div
              key={value}
              className={isCurrent ? undefined : 'menu-item'}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '6px 16px', fontSize: 12, borderRadius: 4,
                cursor: isCurrent ? 'default' : 'pointer',
                color: isCurrent ? 'var(--text-faint)' : 'var(--text)',
              }}
              onClick={() => {
                if (isCurrent) return
                onClose()
                api.updateTaskStatus(projectId, taskId, value).catch(() => {
                  alert({ title: 'Failed to set status', message: `Could not set "${taskId}" to ${label}.` })
                })
              }}
            >
              <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: STATUS_COLORS[value] || 'var(--text-faint)' }} />
              <span style={{ flex: 1 }}>{label}</span>
              {isCurrent && <span style={{ fontSize: 11 }}>{'[current]'}</span>}
            </div>
          )
        })}
        <div style={{ height: 1, background: 'var(--border)', margin: '4px 0' }} />
      </div>
      {!hasTicket && (
        <div
          className="menu-item"
          style={{ padding: '6px 16px', fontSize: 12, cursor: 'pointer', borderRadius: 4, color: 'var(--red)' }}
          onClick={async () => {
            onClose()
            const ok = await confirm({
              title: `Delete task "${taskId}"?`,
              message: 'This cannot be undone.',
              confirmLabel: 'Delete',
              danger: true,
            })
            if (!ok) return
            try {
              await fetch(`/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
            } catch { /* ignore */ }
          }}
        >
          Delete Task
        </div>
      )}
      {hasTicket && (
        <div
          style={{ padding: '6px 16px', fontSize: 11, color: 'var(--text-faint)', cursor: 'not-allowed' }}
          title="Tasks with tickets cannot be deleted"
        >
          Delete (has ticket)
        </div>
      )}
    </div>
  )
}
