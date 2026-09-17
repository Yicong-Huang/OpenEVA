import { useCallback } from 'react'
import { api } from '../api'
import { useAlert } from '../components/Alert'
import { useSessionStatus } from './SessionStatusProvider'

/**
 * Single source of truth for "open a session and deliver its system
 * prompt into the terminal". Used wherever a UI surface (Task Card,
 * Review Card, ...) exposes an Open / Action button that should
 * launch an agent session with a context-specific prompt.
 *
 * Why a hook (and not just a function)?
 *   * Pulls in the AlertProvider so failures surface as a real dialog
 *     instead of swallowed errors / window.alert.
 *   * Gives every caller the same wait-ready + retry-after-3s fallback
 *     -- previously TaskCard and ReviewsPage each kept their own copy
 *     and they drifted (one logged on empty prompt, the other didn't).
 *
 * The "system command" here = the action's prompt template. The
 * backend assembles it (review-pr / do-task / fix-ci / ...) so the
 * frontend just routes the resulting string into the right tmux
 * session by name.
 */

// Where we're opening a session. The endpoint determines which
// backend route gets called and what payload to send. Adding a new
// surface (e.g. a pure PR review on a task PR, a cron job) is a
// single new branch in `launch()`.
export type SessionEndpoint =
  | { type: 'task'; taskId: string; projectId: string }
  | { type: 'review'; reviewUrl: string }

export interface LaunchOptions {
  actionId: string
  // For "Ask Agent" / "Draft Reply" type flows that override the
  // action's default prompt with a one-off message.
  customPrompt?: string
  // PR-context actions on a task (Address Comments, Fix CI, ...) need
  // these so the backend knows which PR the action targets. Ignored
  // for endpoint.type='review'.
  prNumber?: number
  prRepo?: string
  // Pre-chosen agent id. When set, the launcher skips the "which agent?"
  // prompt and launches this agent directly. Leave unset to let the
  // launcher prompt (multiple enabled) or auto-resolve (one enabled).
  agentId?: string
}

export interface LaunchResult {
  session: string
  prompt?: string
  new: boolean
}

// Sentinel distinguishing "user dismissed the agent picker" (abort the
// launch) from "no pick needed / resolve on the backend" (null id).
const CANCELLED = Symbol('agent-pick-cancelled')

/**
 * Decide which agent launches the session.
 *   - 0/1 enabled agents -> return null (backend resolves; no prompt).
 *   - 2+ enabled -> open a `choose` bubble; return the picked id, or
 *     CANCELLED if the user dismissed it (Escape / backdrop / Cancel).
 * A network failure fetching the list degrades to null (no prompt) so a
 * transient error never blocks launching with the default agent.
 */
async function pickAgentIfNeeded(
  choose: (opts: {
    title: string
    message?: string
    choices: Array<{ key: string; label: string; variant?: 'default' | 'primary' | 'danger' }>
  }) => Promise<string | null>,
): Promise<string | null | typeof CANCELLED> {
  let agents: Array<{ id: string; name: string }>
  let enabled: string[]
  try {
    const res = await api.listAgents()
    agents = res.agents
    enabled = res.enabled
  } catch {
    return null
  }
  if (enabled.length <= 1) return null
  const byId = new Map(agents.map((a) => [a.id, a.name]))
  const picked = await choose({
    title: 'Launch with which agent?',
    message: 'You have multiple agents enabled. Pick which one runs this session.',
    choices: enabled.map((id, i) => ({
      key: id,
      label: byId.get(id) ?? id,
      variant: i === 0 ? 'primary' : 'default',
    })),
  })
  return picked === null ? CANCELLED : picked
}

export function useSessionLauncher(endpoint: SessionEndpoint) {
  const { alert, choose } = useAlert()
  const { sessions, reviews } = useSessionStatus()

  const launch = useCallback(
    async (opts: LaunchOptions): Promise<LaunchResult | null> => {
      try {
        // Agent selection: when the user has enabled more than one
        // agent, prompt them to pick which one launches this session.
        // Exactly one enabled -> launch it silently (agentId stays
        // undefined and the backend resolves it). A caller-supplied
        // `opts.agentId` skips the prompt. Dismissing the picker aborts.
        //
        // BUT: if a session is already open for this surface, the agent
        // was chosen when it launched -- re-clicking an action button
        // just delivers a new prompt into the SAME running session (the
        // backend skips relaunch, so any agent_id is ignored). Prompting
        // "which agent?" there is pointless and annoying, so we reuse the
        // existing session and skip the picker entirely.
        let agentId = opts.agentId
        if (!agentId && !isSessionLive(endpoint, sessions, reviews)) {
          const picked = await pickAgentIfNeeded(choose)
          if (picked === CANCELLED) return null
          agentId = picked ?? undefined
        }
        // Unified endpoint: /api/sessions/open routes by `kind` so
        // task / review sessions share the same network path. Adding
        // a new context (e.g. PR-only sessions) is one new branch on
        // the backend + one new endpoint variant here.
        const body =
          endpoint.type === 'task'
            ? {
                kind: 'task' as const,
                task_id: endpoint.taskId,
                project_id: endpoint.projectId,
                action_id: opts.actionId,
                pr_number: opts.prNumber,
                pr_repo: opts.prRepo,
                custom_prompt: opts.customPrompt,
                agent_id: agentId,
              }
            : {
                kind: 'review' as const,
                review_url: endpoint.reviewUrl,
                action_id: opts.actionId,
                custom_prompt: opts.customPrompt,
                agent_id: agentId,
              }
        const result = (await api.openSession(body)) as LaunchResult
        if (result.prompt) {
          deliverPromptToSession(result.session, result.prompt)
        }
        return result
      } catch (e) {
        await alert({
          title: 'Could not open session',
          message: e instanceof Error ? e.message : String(e),
          kind: 'error',
        })
        return null
      }
    },
    [endpoint, alert, choose, sessions, reviews],
  )

  return { launch }
}

// Minimal shapes we read off the session-status service. Kept local so
// the launcher doesn't couple to the full SessionState / review-row
// types -- all we need is a tmux name -> state lookup and, for reviews,
// the url -> session_name mapping.
type SessionRowLike = { state?: string }
type ReviewRowLike = { url?: string; session_name?: string | null }

/**
 * True iff a live agent session already backs this launch surface.
 * "Live" == present in the snapshot and not stopped/unknown (same rule
 * the SessionStatusProvider uses for its `isLiveByName`). When live, the
 * caller reuses that session instead of re-prompting for an agent.
 *
 *   - task:   the tmux session name IS the task id.
 *   - review: the tmux name is derived server-side, so we resolve it
 *             from the cached review row (url -> session_name). No row
 *             yet (never launched) -> not live -> picker runs as normal.
 */
function isSessionLive(
  endpoint: SessionEndpoint,
  sessions: Record<string, SessionRowLike>,
  reviews: ReviewRowLike[],
): boolean {
  let name: string | null | undefined
  if (endpoint.type === 'task') {
    name = endpoint.taskId
  } else {
    name = reviews.find((r) => r.url === endpoint.reviewUrl)?.session_name
  }
  if (!name) return false
  const state = sessions[name]?.state
  return !!state && state !== 'stopped' && state !== 'unknown'
}

/**
 * Wait for the agent to be at its prompt cursor, then type the system
 * command + Enter. If wait-ready times out we fall back to a blind
 * 3s send -- handles the case where the agent was already running and
 * silently consumed the readiness marker before we subscribed.
 *
 * Exported so non-hook call sites (e.g. ad-hoc scripts in tests) can
 * reuse the same delivery semantics.
 */
export function deliverPromptToSession(sessionName: string, prompt: string): void {
  setTimeout(async () => {
    try {
      const ready = await api.waitReady(sessionName, 60)
      if (ready.ready) {
        await api.sendTerminalInput(sessionName, prompt)
        await new Promise((r) => setTimeout(r, 100))
        await api.sendTerminalInput(sessionName, '\r')
      }
    } catch {
      setTimeout(() => {
        api
          .sendTerminalInput(sessionName, prompt)
          .then(() =>
            setTimeout(
              () => api.sendTerminalInput(sessionName, '\n').catch(() => {}),
              100,
            ),
          )
          .catch(() => {})
      }, 3000)
    }
  }, 500)
}
