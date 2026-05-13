import { useCallback } from 'react'
import { api } from '../api'
import { useAlert } from '../components/Alert'

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
}

export interface LaunchResult {
  session: string
  prompt?: string
  new: boolean
}

export function useSessionLauncher(endpoint: SessionEndpoint) {
  const { alert } = useAlert()

  const launch = useCallback(
    async (opts: LaunchOptions): Promise<LaunchResult | null> => {
      try {
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
              }
            : {
                kind: 'review' as const,
                review_url: endpoint.reviewUrl,
                action_id: opts.actionId,
                custom_prompt: opts.customPrompt,
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
    [endpoint, alert],
  )

  return { launch }
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
