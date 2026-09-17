/**
 * Pure helpers for SessionCard, split out so they're testable without
 * mounting the component (and so Fast Refresh keeps working).
 */

export interface ResumeResult {
  action: 'resumed' | 'relaunched' | 'noop'
  running: boolean
}

export interface ResumeNotice {
  title: string
  message: string
  kind: 'error' | 'warning'
}

/**
 * Decide what (if anything) to tell the user after a resume attempt.
 *
 * Three outcomes worth surfacing:
 *
 *  - `running === false`: the launch command succeeded but the agent exited
 *    at startup, so tmux reaped the pane. This is the case that used to be
 *    reported as success -- the card looked resumed while the terminal never
 *    connected. Checked FIRST because it can accompany either action.
 *  - `action === 'relaunched'`: tmux is back but the previous conversation
 *    was not restored (nothing on record, or the transcript was deleted).
 *  - otherwise: a clean resume; stay quiet.
 *
 * Returns null when there's nothing to say.
 */
export function resumeNotice(res: ResumeResult,
                             sessionName: string): ResumeNotice | null {
  if (res.running === false) {
    return {
      title: 'Session could not be resumed',
      message: `The agent for "${sessionName}" exited immediately after ` +
               'launch, so the tmux pane is gone. Its saved conversation ' +
               'may have been deleted. Check the server log for details, ' +
               'then try again or kill the session.',
      kind: 'error',
    }
  }
  if (res.action === 'relaunched') {
    return {
      title: 'Session relaunched (history not resumed)',
      message: `No resumable conversation was found for "${sessionName}" ` +
               '(nothing on record, or its saved transcript was deleted), ' +
               'so a fresh agent was started. The previous conversation is ' +
               'lost.',
      kind: 'warning',
    }
  }
  return null
}
