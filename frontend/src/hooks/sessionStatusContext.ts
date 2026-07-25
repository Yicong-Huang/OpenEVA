import { createContext } from 'react'
import type { CronJob, ProjectManagerSession, Ticket } from '../api'
import type { PR, Task } from '../types'

/**
 * Session status service -- centralized cache of every "live session"
 * surface in Eva.
 *
 * Architecture (3 invariants):
 *   1. ONE global snapshot: the service holds a `Map<tmuxName, SessionState>`
 *      that is the single source of truth for "what state is each session
 *      in right now". Every consumer reads from this map.
 *   2. Resilient to disconnect: on initial mount AND on every SSE
 *      reconnect, the service refetches `/api/sessions/snapshot` to
 *      catch up on anything that happened while we were offline (or
 *      events that were emitted with `persist=False` and aren't
 *      replayable via `Last-Event-ID`).
 *   3. Single SSE consumer: the service is the ONLY component
 *      subscribing to `agent.*` / `session.*` events. Per-component
 *      subscriptions are forbidden -- they miss events fired while
 *      the component was unmounted. UI components read state from the
 *      snapshot map; React re-renders them when the map mutates.
 *
 * The 4 business endpoints (cron-jobs / review-requests / tickets /
 * all-sessions) are still cached for non-state fields (job name, PR
 * title, ticket summary, project metadata). State on those rows is
 * authoritative from the snapshot map, not from the per-row
 * `session_alive` / `session_status` fields any more.
 *
 * Lives in a separate file from the Provider component so React
 * fast-refresh works correctly on the .tsx file.
 */

// One row in the snapshot returned by GET /api/sessions/snapshot.
export interface SessionState {
  tmux_name: string
  kind: 'task' | 'review' | 'cron' | 'ticket'
  state: 'starting' | 'thinking' | 'idle' | 'needs_permission' | 'stopped'
       | 'unknown' | 'crashed'
  detail: string
  ts: string
  agent_session_id: string
  // Multi-polymorphic target columns -- only the ones relevant to
  // `kind` are non-empty. e.g. kind='task' uses (project_id, target_id);
  // kind='ticket' uses (target_id, target_instance).
  project_id: string
  target_id: string
  target_instance: string
}

export type SessionMap = Record<string, SessionState>

// Raw shape of the /api/all-sessions response, mirroring the backend
// `routes/prs.list_all_project_sessions` -- a dict keyed by project id.
export interface ProjectSessionsGroup {
  id: string
  name: string
  has_tickets: boolean
  sessions: Array<{
    task_id: string
    project: string
    tmux_name: string
    running: boolean
    status: string
  }>
  tasks: Record<string, Task>
}

export type AllProjectSessions = Record<string, ProjectSessionsGroup>

export type LiveReview = PR & { repo: string; source?: 'github' | 'manual' | 'both' }

/** Aggregated counts driving the SessionIndicator badge. */
export interface SessionCounts {
  total: number
  needs: number
  flight: number
  idle: number
  other: number
  byKind: { task: number; cron: number; review: number; ticket: number }
}

export interface SessionStatusValue {
  // Global snapshot -- the source of truth for session state.
  // Components query this directly via `getSession(name)` or read
  // their own derived view via the helpers below.
  sessions: SessionMap

  // Business-data caches (still per-endpoint because the rows carry
  // fields beyond just session state). Each row's *state* should be
  // looked up from `sessions[row.tmux_name]`, not from the row's
  // own `session_alive` / `session_status` (those are stale on
  // anything but the most recent fetch).
  projectSessions: AllProjectSessions | null
  cronJobs: CronJob[]
  reviews: LiveReview[]
  tickets: Ticket[]
  projectManagers: ProjectManagerSession[]

  // Filtered to entries with a live session (state != 'stopped' &&
  // state != 'unknown'), pre-sorted for stable chip ordering.
  liveCronJobs: CronJob[]
  liveReviews: LiveReview[]
  liveTickets: Ticket[]
  liveProjectManagers: ProjectManagerSession[]

  // Aggregated counts, computed from the snapshot map only.
  counts: SessionCounts

  // Helpers
  getSession: (tmuxName: string) => SessionState | undefined

  // Imperative refetches for the BUSINESS endpoints only. The
  // session-state map is event-driven (SSE re-emits the snapshot on
  // every reconnect); there's no manual refetch for state itself.
  refetchSessions: () => void
  refetchCron: () => void
  refetchReviews: () => void
  refetchTickets: () => void
  refetchProjectManagers: () => void
  refetchAll: () => void
}


export const DEFAULT_SESSION_STATUS: SessionStatusValue = {
  sessions: {},
  projectSessions: null,
  cronJobs: [],
  reviews: [],
  tickets: [],
  projectManagers: [],
  liveCronJobs: [],
  liveReviews: [],
  liveTickets: [],
  liveProjectManagers: [],
  counts: {
    total: 0, needs: 0, flight: 0, idle: 0, other: 0,
    byKind: { task: 0, cron: 0, review: 0, ticket: 0 },
  },
  getSession: () => undefined,
  refetchSessions: () => {},
  refetchCron: () => {},
  refetchReviews: () => {},
  refetchTickets: () => {},
  refetchProjectManagers: () => {},
  refetchAll: () => {},
}


export const SessionStatusContext = createContext<SessionStatusValue>(DEFAULT_SESSION_STATUS)
