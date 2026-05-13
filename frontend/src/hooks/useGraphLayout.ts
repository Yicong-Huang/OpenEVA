import { useState, useCallback, useEffect } from 'react'

export type NodePosition = { x: number; y: number }
export type NodePositions = Record<string, NodePosition>


/**
 * Per-project graph node positions persisted in localStorage. Lets the
 * user freely arrange task nodes on the dependency graph and have the
 * arrangement survive reloads, status updates, edge add/delete events,
 * and event-driven refetches.
 *
 * The contract:
 *  - `positions[taskId]` is set when the user drags a node OR when the
 *    initial dagre auto-layout runs (so subsequent edge changes don't
 *    shake un-touched nodes around).
 *  - `setPosition` is called on every drag-end through React Flow's
 *    `onNodesChange` (we filter for the position-with-!dragging case).
 *  - `seedPositions` is called once after dagre's initial run; it only
 *    fills entries that aren't already user-set (so seeding doesn't
 *    blow away a stored position from a previous session).
 *  - `clearLayout` removes the LS entry so the next render falls back
 *    to a fresh dagre auto-layout. Powers the "Reset Layout" button.
 */
const KEY = (pid: string) => `eva-graph-layout-${pid}`


export function useGraphLayout(projectId: string | null | undefined): {
  positions: NodePositions
  setPosition: (taskId: string, pos: NodePosition) => void
  /** Atomic bulk update -- one state set + one LS write. Used by
   *  Auto Position to commit the whole nudged layout in one shot. */
  setPositionsBulk: (next: NodePositions) => void
  seedPositions: (seed: NodePositions) => void
  clearLayout: () => void
} {
  const [positions, setPositions] = useState<NodePositions>({})

  // Reload from LS whenever the project changes.
  useEffect(() => {
    if (!projectId) {
      setPositions({})
      return
    }
    try {
      const raw = localStorage.getItem(KEY(projectId))
      setPositions(raw ? JSON.parse(raw) : {})
    } catch {
      setPositions({})
    }
  }, [projectId])

  const writeLS = useCallback((next: NodePositions) => {
    if (!projectId) return
    try {
      localStorage.setItem(KEY(projectId), JSON.stringify(next))
    } catch {
      // Ignore quota / disabled-LS errors -- persistence is a nice-to-have.
    }
  }, [projectId])

  const setPosition = useCallback((taskId: string, pos: NodePosition) => {
    setPositions(prev => {
      const next = { ...prev, [taskId]: pos }
      writeLS(next)
      return next
    })
  }, [writeLS])

  const setPositionsBulk = useCallback((next: NodePositions) => {
    setPositions(next)
    writeLS(next)
  }, [writeLS])

  const seedPositions = useCallback((seed: NodePositions) => {
    setPositions(prev => {
      let changed = false
      const next = { ...prev }
      for (const [id, pos] of Object.entries(seed)) {
        if (next[id]) continue   // already user-set or seeded earlier
        next[id] = pos
        changed = true
      }
      if (changed) writeLS(next)
      return changed ? next : prev
    })
  }, [writeLS])

  const clearLayout = useCallback(() => {
    if (!projectId) return
    try {
      localStorage.removeItem(KEY(projectId))
    } catch { /* ignore */ }
    setPositions({})
  }, [projectId])

  return { positions, setPosition, setPositionsBulk, seedPositions, clearLayout }
}
