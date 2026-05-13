import { useCallback, useState } from 'react'
import { useSessionState } from './SessionStatusProvider'

/**
 * Per-session state reader. Used by SessionCard / ProjectSessionCard
 * to drive the dot color + status label.
 *
 * Internally this is a thin adapter on top of the global session
 * status service: the actual cache + SSE wiring lives in
 * `SessionStatusProvider`. Components no longer subscribe to SSE
 * directly -- the service does, and re-renders consumers when its
 * snapshot map mutates.
 *
 * Why preserve the old shape (sseStatus / setSseStatus / lastEvent)?
 * Several callers expect to *override* the displayed status
 * imperatively (e.g. SessionCard sets it to null when the parent
 * tells it the session is dead). A local override layered on top of
 * the service's value lets us keep that ergonomics without making
 * every caller adopt the new useSessionState() shape today.
 */

export interface UseAgentSessionStatus {
  /** Latest status string -- service value, optionally overridden
   * by an imperative setSseStatus call. Null until the service
   * sees the session for the first time. */
  sseStatus: string | null
  /** Imperative override (e.g. "I just killed it, show stopped
   * immediately"). Setting to null clears the override -- the
   * service's value re-takes precedence on the next render. */
  setSseStatus: (s: string | null) => void
  /** Same as before for backwards compat -- not used by anything
   * post-refactor, kept null. Callers who relied on raw event
   * suffixes should migrate to a SessionStatusProvider event listener
   * (or compose their own state diff inside the consumer). */
  lastEvent: string | null
}

export function useAgentSessionStatus(tmuxName: string | null | undefined): UseAgentSessionStatus {
  const row = useSessionState(tmuxName)
  const [override, setOverride] = useState<string | null>(null)

  // The override wins when set; service value is the fallback.
  const sseStatus = override ?? row?.state ?? null

  const setSseStatus = useCallback((s: string | null) => {
    setOverride(s)
  }, [])

  return {
    sseStatus,
    setSseStatus,
    lastEvent: null,
  }
}
