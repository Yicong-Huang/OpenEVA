/**
 * Session state -- single source of truth for the 6 canonical agent
 * session lifecycle values + the helpers that map them to UI buckets,
 * dot colors, and "is this session live?" predicates.
 *
 * Before this module the same enum + the same status->bucket switch
 * lived (in 5 different shapes) in:
 *   - SessionStatusProvider.bucketize        -> needs/flight/idle/other
 *   - LiveSessionChip.dotStatus              -> done/in_progress/not_started/blocked
 *   - SessionCard's inline ternary           -> done/in_progress/not_started
 *   - ProjectSessionCard's inline ternary    -> in_progress/needs_follow_up/...
 *   - SessionsPage.AttentionView's filters   -> needs/inFlight/other (again)
 *
 * Adding a new state used to mean editing 5 places. Now it's one
 * `STATE` literal + one `bucketize` switch.
 */

// The 6 canonical session states. Mirrors the backend's
// `_HOOK_RULES` table + the two derived states (stopped / unknown).
export const STATE = {
  STARTING: 'starting',
  THINKING: 'thinking',
  IDLE: 'idle',
  NEEDS_PERMISSION: 'needs_permission',
  STOPPED: 'stopped',
  UNKNOWN: 'unknown',
} as const

export type SessionStateValue =
  | 'starting'
  | 'thinking'
  | 'idle'
  | 'needs_permission'
  | 'stopped'
  | 'unknown'
  | 'crashed'  // tmux died unexpectedly; we expected it to stay alive


/**
 * Attention buckets used by SessionIndicator + SessionsPage's
 * by-attention view. Lower bucket = higher urgency.
 *   - needs:  user has to do something (needs_permission, crashed, etc.)
 *   - flight: the agent is actively working
 *   - idle:   waiting for the user's next prompt
 *   - other:  stopped, unknown, anything we can't classify
 */
export type Bucket = 'needs' | 'flight' | 'idle' | 'other'

// `crashed` lives in `needs` because the user likely wants to either
// resume or explicitly kill -- a tmux pane that died unexpectedly is
// a "deal with this" signal, not background noise.
//
// Note: `needs_input` is intentionally NOT in this set. The backend
// collapses idle_prompt notifications into `idle` (see
// `_HOOK_RULES["Notification:idle_prompt"]`) because from the user's
// POV "the agent just finished" and "the agent waiting at prompt for a
// while" are the same situation. Any `needs_input` events still in
// flight from before that change are treated as `idle` here.
const NEEDS_BUCKET = new Set<string>(['needs_permission', 'crashed'])
const FLIGHT_BUCKET = new Set<string>(['thinking', 'starting', 'connecting...'])

export function bucketize(state: string | null | undefined): Bucket {
  if (!state) return 'other'
  if (state === 'stopped' || state === 'unknown') return 'other'
  if (NEEDS_BUCKET.has(state)) return 'needs'
  if (FLIGHT_BUCKET.has(state)) return 'flight'
  if (state === 'idle' || state === 'needs_input') return 'idle'
  return 'other'
}


/** "Is this session something the user could meaningfully interact
 * with right now?" -- i.e. a tmux pane is up or expected to be. */
export function isLive(state: string | null | undefined): boolean {
  if (!state) return false
  return state !== 'stopped' && state !== 'unknown' && state !== 'stream lost'
}


/** True for states where the agent is in the middle of a turn (thinking
 * about a response, starting up, etc.). Used by some progress
 * indicators that want a "spinner-ish" visual. */
export function isInFlight(state: string | null | undefined): boolean {
  return bucketize(state) === 'flight'
}


/** True for states that demand attention from the user. */
export function needsAttention(state: string | null | undefined): boolean {
  return bucketize(state) === 'needs'
}


/**
 * The 3-tier urgency palette. THE single source of truth for every
 * place that paints a session status (graph nodes, dots on cards /
 * chips, top-bar indicator, cron list). Adding a new state means
 * editing this one switch.
 *
 *   red    needs_permission, crashed     -> "block now"
 *   yellow idle, needs_input             -> "ready for next task"
 *   blue   thinking, starting, connect.. -> "working"
 *   faint  stopped, unknown, ''          -> "no live session"
 */
export function sessionDotColor(state: string | null | undefined): string {
  if (!state || state === 'stopped' || state === 'unknown'
      || state === 'stream lost') {
    return 'var(--text-faint)'
  }
  if (state === 'needs_permission' || state === 'crashed') return 'var(--red)'
  if (state === 'idle' || state === 'needs_input') return 'var(--yellow)'
  if (state === 'thinking' || state === 'starting' || state === 'connecting...') {
    return 'var(--blue)'
  }
  return 'var(--text-faint)'
}

/** Animation class for the urgent tier (red); empty otherwise. */
export function sessionDotAnim(state: string | null | undefined): string {
  if (state === 'needs_permission' || state === 'crashed') return 'session-dot-blink'
  return ''
}

const KNOWN_STATES = new Set<string>([
  'starting', 'thinking', 'idle', 'needs_input', 'needs_permission',
  'crashed', 'stopped', 'unknown', 'connecting...', 'stream lost',
])
/** True for any state we have an explicit color for. Used by
 * renderers that prefer to render NOTHING for stale/typo'd states
 * rather than a misleading grey dot. */
export function isKnownSessionState(state: string | null | undefined): boolean {
  return !!state && KNOWN_STATES.has(state)
}

/** True for states that get a soft glow on the dot itself. Reserved
 * for the urgent tier so a fleet of idle sessions doesn't all glow. */
export function sessionDotHalo(state: string | null | undefined): boolean {
  return state === 'needs_permission' || state === 'crashed'
}
