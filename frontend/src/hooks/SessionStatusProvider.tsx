import { useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useApi } from './useApi'
import { useEventBus } from './useEventBus'
import type { CronJob, Ticket } from '../api'
import { bucketize } from '../utils/sessionState'
import {
  SessionStatusContext,
  type AllProjectSessions,
  type LiveReview,
  type SessionCounts,
  type SessionMap,
  type SessionState,
} from './sessionStatusContext'

/**
 * SessionStatusProvider -- the single global owner of session state.
 *
 * Architecture (per the redesign):
 *   - tmux is the source of truth.
 *   - Backend keeps an in-memory `core.session_state` cache that
 *     mirrors tmux + is patched live by agent hooks.
 *   - On every SSE (re)connect, the backend replays its entire
 *     cache as a series of `session.state` events bracketed by
 *     `session.snapshot.begin / .end`. The provider buffers those
 *     into a temp map and atomically swaps when `.end` arrives so
 *     there's no flicker mid-replay.
 *   - During normal operation, every state change emits a
 *     `session.state` event which patches the map directly.
 *   - There is NO `/api/sessions/snapshot` GET. There is NO DB
 *     persistence of state. This service is the single frontend
 *     mirror of the backend's single in-memory cache of tmux's
 *     ground truth.
 *
 * The four business endpoints (`/api/all-sessions`,
 * `/api/cron-jobs`, `/api/review-requests`, `/api/tickets`) still
 * back the per-kind row caches (cron names, PR titles, ticket
 * summaries, project metadata). Refresh on `task.*`/`github.*`/
 * `ticket.*` events. State on those rows is NOT trusted -- always
 * looked up via `sessions[row.session_name]`.
 */


export function SessionStatusProvider({ children }: { children: ReactNode }) {
  // The authoritative snapshot mirror. Patched per session.state
  // event during normal operation; atomically swapped on every SSE
  // (re)connect via the snapshot.begin/end replay.
  const [sessions, setSessions] = useState<SessionMap>({})
  // While receiving a snapshot replay, accumulate rows here and
  // commit on `session.snapshot.end`. Outside snapshots this stays
  // null and `session.state` events patch `sessions` directly.
  const snapshotBufferRef = useRef<SessionMap | null>(null)

  // Business-data caches. Their state columns are NOT used by the
  // indicator counts -- those come from `sessions` above.
  const { data: sessionsData, refetch: refetchSessions } =
    useApi<AllProjectSessions>('/api/all-sessions')
  const { data: cronData, refetch: refetchCron } =
    useApi<{ jobs: CronJob[] }>('/api/cron-jobs')
  const { data: reviewData, refetch: refetchReviews } =
    useApi<{ prs: LiveReview[] }>('/api/review-requests')
  const { data: ticketData, refetch: refetchTickets } =
    useApi<{ tickets: Ticket[]; configured: boolean }>('/api/tickets')

  // session.snapshot.begin: open a fresh buffer. Don't touch the
  // visible map yet -- we want the previous snapshot to keep
  // rendering until .end commits the new one.
  useEventBus('session.snapshot.begin', useCallback(() => {
    snapshotBufferRef.current = {}
  }, []))

  // session.snapshot.end: atomic commit. Replaces the entire map so
  // sessions that disappeared on the backend (e.g. evicted while we
  // were offline) don't linger.
  useEventBus('session.snapshot.end', useCallback(() => {
    if (snapshotBufferRef.current) {
      setSessions(snapshotBufferRef.current)
      snapshotBufferRef.current = null
    }
  }, []))

  // session.state: the only state-update path. While buffering a
  // snapshot replay, accumulate; otherwise patch the live map.
  useEventBus('session.state', useCallback((event: Record<string, unknown>) => {
    const tmuxName = typeof event.session === 'string' ? event.session : ''
    if (!tmuxName) return
    const row: SessionState = {
      tmux_name: tmuxName,
      kind: (event.kind as SessionState['kind']) || inferKindFromName(tmuxName),
      state: (event.state as SessionState['state']) || 'unknown',
      detail: typeof event.detail === 'string' ? event.detail : '',
      ts: typeof event.ts === 'string' ? event.ts : '',
      agent_session_id: typeof event.agent_session_id === 'string'
        ? event.agent_session_id : '',
      project_id: typeof event.project_id === 'string'
        ? event.project_id : '',
      target_id: typeof event.target_id === 'string'
        ? event.target_id : '',
      target_instance: typeof event.target_instance === 'string'
        ? event.target_instance : '',
    }
    if (snapshotBufferRef.current !== null) {
      snapshotBufferRef.current[tmuxName] = row
    } else {
      setSessions(prev => ({ ...prev, [tmuxName]: row }))
    }
  }, []))

  // session.removed: drop the row entirely (rare -- usually we keep
  // 'stopped' rows visible. This is for "the entity owning this
  // session was deleted upstream" cases).
  useEventBus('session.removed', useCallback((event: Record<string, unknown>) => {
    const tmuxName = typeof event.session === 'string' ? event.session : ''
    if (!tmuxName) return
    if (snapshotBufferRef.current !== null) {
      delete snapshotBufferRef.current[tmuxName]
    } else {
      setSessions(prev => {
        if (!(tmuxName in prev)) return prev
        const next = { ...prev }
        delete next[tmuxName]
        return next
      })
    }
  }, []))

  // Business-data invalidation -- same as before. These don't touch
  // the snapshot map, only their own per-endpoint cache.
  useEventBus('task.*', useCallback(() => refetchSessions(), [refetchSessions]))
  useEventBus('github.*', useCallback(() => refetchReviews(), [refetchReviews]))
  useEventBus('ticket.*', useCallback(() => refetchTickets(), [refetchTickets]))
  // Kill flows delete the session row from the DB. The snapshot already
  // gets the `session.state -> stopped` patch, but `/api/all-sessions`
  // (which carries the per-row business metadata + the `running` flag)
  // is a separate cache -- without an explicit refetch the killed
  // row keeps rendering until the next task/github/ticket event fires.
  useEventBus('session.killed', useCallback(() => refetchSessions(), [refetchSessions]))

  // Project visibility toggles change which rows the backend
  // includes in each per-project bundle. Refetch the lists that
  // group by project so hidden-project content drops out without
  // a manual page reload.
  useEffect(() => {
    const handler = () => {
      refetchSessions()
      refetchReviews()
      refetchTickets()
    }
    window.addEventListener('eva:project-visibility-changed', handler)
    return () => window.removeEventListener('eva:project-visibility-changed', handler)
  }, [refetchSessions, refetchReviews, refetchTickets])

  // Memoized derived views. Each `live*` filter is by snapshot state
  // (NOT by any per-row `session_alive` -- those fields are gone).
  const cronJobs = cronData?.jobs ?? []
  const reviews = reviewData?.prs ?? []
  const tickets = ticketData?.tickets ?? []

  const isLiveByName = useCallback((name: string) => {
    const s = sessions[name]
    return !!s && s.state !== 'stopped' && s.state !== 'unknown'
  }, [sessions])

  const liveCronJobs = useMemo(() => {
    return cronJobs
      .filter(j => isLiveByName(`cron-job-${j.id}`))
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [cronJobs, isLiveByName])

  const liveReviews = useMemo(() => {
    return reviews
      .filter(p => p.session_name && isLiveByName(p.session_name))
      .sort((a, b) => a.title.localeCompare(b.title))
  }, [reviews, isLiveByName])

  const liveTickets = useMemo(() => {
    return tickets
      .filter(t => t.session_name && isLiveByName(t.session_name))
      .sort((a, b) => (a.key || '').localeCompare(b.key || ''))
  }, [tickets, isLiveByName])

  const counts: SessionCounts = useMemo(() => {
    const c: SessionCounts = {
      total: 0, needs: 0, flight: 0, idle: 0, other: 0,
      byKind: { task: 0, cron: 0, review: 0, ticket: 0 },
    }
    for (const s of Object.values(sessions)) {
      // Stopped sessions are history; the indicator counts only
      // what's currently live.
      if (s.state === 'stopped') continue
      c.total += 1
      c.byKind[s.kind] += 1
      c[bucketize(s.state)] += 1
    }
    return c
  }, [sessions])

  const getSession = useCallback(
    (name: string) => sessions[name],
    [sessions],
  )

  const refetchAll = useCallback(() => {
    refetchSessions()
    refetchCron()
    refetchReviews()
    refetchTickets()
  }, [refetchSessions, refetchCron, refetchReviews, refetchTickets])

  const value = useMemo(() => ({
    sessions,
    projectSessions: sessionsData ?? null,
    cronJobs, reviews, tickets,
    liveCronJobs, liveReviews, liveTickets,
    counts,
    getSession,
    refetchSessions, refetchCron, refetchReviews,
    refetchTickets, refetchAll,
  }), [
    sessions, sessionsData, cronJobs, reviews, tickets,
    liveCronJobs, liveReviews, liveTickets, counts, getSession,
    refetchSessions, refetchCron, refetchReviews,
    refetchTickets, refetchAll,
  ])

  return (
    <SessionStatusContext.Provider value={value}>
      {children}
    </SessionStatusContext.Provider>
  )
}


/** Best-effort kind inference from a tmux session name. Used when a
 * session.state event arrives without a `kind` field (shouldn't
 * happen; defensive). The backend's session_state.set_state always
 * provides one. */
function inferKindFromName(tmuxName: string): SessionState['kind'] {
  if (tmuxName.startsWith('cron-job-')) return 'cron'
  if (tmuxName.startsWith('review-')) return 'review'
  if (tmuxName.startsWith('ticket-')) return 'ticket'
  return 'task'
}


/** Consumer hook. Returns the live snapshot + refetch helpers. Safe
 * to call outside the Provider -- you'll get the empty default
 * (no fetches issued) which is what tests need. */
export function useSessionStatus() {
  return useContext(SessionStatusContext)
}


/**
 * Per-session state lookup. Returns the row from the global snapshot,
 * or `undefined` if the service hasn't seen this tmux name yet.
 *
 * This is the canonical way for a leaf component (SessionCard,
 * TicketTaskCard, CronJobsPage, etc.) to learn its session's state.
 */
export function useSessionState(tmuxName: string | null | undefined): SessionState | undefined {
  const { sessions } = useSessionStatus()
  if (!tmuxName) return undefined
  return sessions[tmuxName]
}
