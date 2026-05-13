import { useState, useEffect } from 'react'
import { listPending, subscribe, type PendingCreate } from '../services/pendingCreates'


/** Subscribe to the pending-creates service and re-render on changes.
 *  Pass a projectId to get only that project's pending entries. */
export function usePendingCreates(projectId?: string | null): PendingCreate[] {
  const [list, setList] = useState<PendingCreate[]>(() =>
    listPending(projectId ?? undefined),
  )
  useEffect(() => {
    setList(listPending(projectId ?? undefined))
    return subscribe(() => setList(listPending(projectId ?? undefined)))
  }, [projectId])
  return list
}
