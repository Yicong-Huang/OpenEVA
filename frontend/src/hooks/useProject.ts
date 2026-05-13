import { useState, useEffect, useCallback } from 'react'
import type { Project } from '../types'
import { api } from '../api'
import { useEventBus } from './useEventBus'

/**
 * Fetch a single project and keep it fresh on agent/github events.
 *
 * Passing `null` / `undefined` clears the state (no fetch). Pages can use
 * this instead of hand-rolling a useEffect + two useEventBus handlers for
 * the same refetch behavior.
 */
export function useProject(projectId: string | null | undefined): {
  project: Project | null
  refetch: () => void
} {
  const [project, setProject] = useState<Project | null>(null)

  const fetchProject = useCallback(() => {
    if (!projectId) {
      setProject(null)
      return
    }
    api.getProject(projectId).then(setProject).catch(() => setProject(null))
  }, [projectId])

  useEffect(() => {
    fetchProject()
  }, [fetchProject])

  // Event-driven refresh: any agent / github / task event may change
  // task or session state. Critical: `task.*` subscription was
  // missing originally, which is why graph node colours and
  // effective_status (blocked) didn't update on manual status edits
  // or dependency edge changes -- only agent/github SSE pushes
  // triggered refetch. Adding it makes the fan-out events emitted
  // by `core.tasks._fanout_dependents_status_changed` actually
  // reach the UI.
  useEventBus('agent.*', useCallback(() => {
    if (projectId) fetchProject()
  }, [projectId, fetchProject]))

  useEventBus('github.*', useCallback(() => {
    if (projectId) fetchProject()
  }, [projectId, fetchProject]))

  useEventBus('task.*', useCallback(() => {
    if (projectId) fetchProject()
  }, [projectId, fetchProject]))

  return { project, refetch: fetchProject }
}
