import { useCallback, useEffect, useMemo, useState } from 'react'
import { useEventBus } from '../hooks/useEventBus'
import { useSessionStatus } from '../hooks/SessionStatusProvider'
import { SessionCard } from './SessionCard'
import { CIRing, MyReviewPill } from './PRNode'
import { api } from '../api'
import { useAlert } from './Alert'
import { timeAgo } from '../utils'
import { useSessionLauncher } from '../hooks/useSessionLauncher'
import type { ActionDef } from '../types'

/**
 * ReviewCard -- the middle-pane view shown on the All Reviews page when
 * a PR is selected. Parallels TaskCard but deliberately slimmer:
 *   * no dependency graph (review doesn't own work)
 *   * no ticket / create-ticket / do-task (not a task)
 *   * no dependency PRs list (the review IS the PR)
 *   * shows the append-only history timeline (`review_history`) and a
 *     workflow-state toggle (queued/active/done/dismissed)
 *
 * Visual language:
 *   * amber accent (`#f59e0b`) instead of the purple task-accent so the
 *     user can tell at a glance this is the "reviewer" side of Eva.
 *   * "REVIEW" pill in the header echoes the one PRDetail draws.
 */

interface ReviewCardProps {
  reviewUrl: string
  repo: string
  number: number
}

interface ReviewRow {
  url: string
  repo: string
  number: number
  title: string
  // /api/review-requests returns the same enriched fields the PRCard
  // already renders -- exposing them here lets ReviewCard show the PR
  // at a glance instead of forcing the user back to the right detail
  // pane just to read the title.
  author?: string
  status?: string                // open / merged / closed
  ci_status?: string             // success / failure / pending / ''
  review_status?: string         // approved / changes_requested / review_required / ''
  my_review_state?: string       // pending_review / approved / changes_requested / commented / ''
  comment_count?: number
  additions?: number
  deletions?: number
  head_branch?: string
  base_branch?: string
  last_updated?: string
  session_name?: string
  agent_session_id?: string
  my_workflow_state?: string
  started_at?: string
  source?: string
}

interface HistoryEntry {
  ts: string
  text: string
  source: string
}

const WORKFLOW_STATES: Array<{ value: string; label: string; desc: string }> = [
  { value: 'queued',    label: 'Queued',    desc: 'Haven’t started yet.' },
  { value: 'active',    label: 'Active',    desc: 'Currently reviewing.' },
  { value: 'done',      label: 'Done',      desc: 'Approved / commented / merged on my end.' },
  { value: 'dismissed', label: 'Dismissed', desc: 'Skipping this one (stays in the queue).' },
]

const ACCENT = '#f59e0b'  // amber, matches the REVIEW badge in PRDetail

export function ReviewCard({ reviewUrl, repo, number }: ReviewCardProps) {
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [savingState, setSavingState] = useState(false)
  const [actions, setActions] = useState<ActionDef[]>([])
  const [launching, setLaunching] = useState<string | null>(null)
  const { confirm, alert } = useAlert()

  // Same launch + deliver-prompt path TaskCard / ReviewsPage use, so
  // ReviewCard can host its own action buttons -- no need to bounce
  // through PRDetail's onOpenAction callback when the user is already
  // looking at the middle pane and just wants to start the review.
  const { launch } = useSessionLauncher({ type: 'review', reviewUrl })

  // Pull the row from the global session-status service rather than
  // refetching `/api/review-requests` per-card. Multiple ReviewCard
  // instances mounted at once (e.g. one per live review on All Live
  // Tasks) used to mean N parallel GETs whenever a github.* event
  // fired. Now there's one cached list, all cards just read from it.
  const { reviews } = useSessionStatus()
  const review = useMemo(
    () => (reviews as unknown as ReviewRow[]).find(r => r.url === reviewUrl) || null,
    [reviews, reviewUrl],
  )

  const loadHistory = useCallback(async () => {
    try {
      const r = await fetch(
        `/api/reviews/history?url=${encodeURIComponent(reviewUrl)}`,
      )
      const data = await r.json()
      setHistory(data.entries || [])
    } catch {
      setHistory([])
    }
  }, [reviewUrl])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  // Pull review-context actions on mount so the card can render its
  // own Review PR / Draft Reply / Sync Status buttons. Same source
  // PRDetail uses; both surfaces stay in sync without a prop.
  useEffect(() => {
    let cancelled = false
    api.getActions('review')
      .then((data) => { if (!cancelled) setActions(data.actions || []) })
      .catch(() => { if (!cancelled) setActions([]) })
    return () => { cancelled = true }
  }, [])

  const handleLaunch = useCallback(async (actionId: string) => {
    setLaunching(actionId)
    try {
      const result = await launch({ actionId })
      if (result) {
        // Refresh the per-card history; the row itself updates via
        // the service's SSE-driven refetch.
        await loadHistory()
      }
    } finally {
      setLaunching(null)
    }
  }, [launch, loadHistory])

  // History is per-card and not in the global service, so we still
  // listen for review.* events here. The row itself updates via the
  // service -- we don't subscribe to github.review.updated any more.
  useEventBus('review.*', useCallback(() => {
    loadHistory()
  }, [loadHistory]))

  const handleKillSession = useCallback(async () => {
    if (!review?.session_name) return
    const ok = await confirm({
      title: 'Kill review session?',
      message: `Terminate tmux session ${review.session_name}?`,
      confirmLabel: 'Kill',
    })
    if (!ok) return
    try {
      await api.killSession(review.session_name)
      // Row update propagates via the service's SSE-driven refetch;
      // we just refresh the per-card history here.
      await loadHistory()
    } catch (e) {
      await alert({
        title: 'Kill failed',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
  }, [review, confirm, alert, loadHistory])

  const { refetchReviews } = useSessionStatus()
  const setWorkflowState = useCallback(async (newState: string) => {
    setSavingState(true)
    try {
      const r = await fetch(
        `/api/reviews?url=${encodeURIComponent(reviewUrl)}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ my_workflow_state: newState }),
        },
      )
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      // Trigger one (shared) refetch so the row reflects the new
      // workflow state before the next github.* event.
      refetchReviews()
    } finally {
      setSavingState(false)
    }
  }, [reviewUrl, refetchReviews])

  if (!review) {
    return (
      <div style={{ padding: 12, color: 'var(--text-dim)', fontSize: 12 }}>
        Loading review...
      </div>
    )
  }

  const curState = review.my_workflow_state || 'queued'
  const sessionLive = !!(review.session_name && review.session_name !== '')
  const branchDisplay = review.head_branch && review.head_branch.length > 28
    ? review.head_branch.substring(0, 28) + '..'
    : (review.head_branch || '')
  // The session terminal is heavy (540px). Auto-expand ONLY when the
  // user is actively reviewing -- otherwise the PR meta + workflow +
  // history sections get pushed off-screen in narrow panes.
  const autoExpandSession = curState === 'active'

  return (
    <div
      data-testid="review-card"
      style={{
        padding: 12,
        borderRadius: 8,
        // Subtle amber tint so the card visually reads as "review" vs
        // the default panel background used for tasks.
        background: 'rgba(251, 191, 36, 0.04)',
        border: `1px solid rgba(251, 191, 36, 0.25)`,
      }}
    >
      {/* Header row: REVIEW pill + repo#number + PR status pill +
          CI ring. Wraps gracefully on narrow widths. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
        <span
          style={{
            fontSize: 9, fontWeight: 800, letterSpacing: 0.5,
            padding: '2px 6px', borderRadius: 4,
            background: 'rgba(251, 191, 36, 0.18)',
            color: ACCENT,
            border: `1px solid rgba(251, 191, 36, 0.45)`,
          }}
        >REVIEW</span>
        <a
          href={review.url}
          target="_blank"
          rel="noopener noreferrer"
          style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)', textDecoration: 'none' }}
          title="Open on GitHub"
        >
          {repo}#{number}
        </a>
        {review.status && (
          <span className={`pr-label ${review.status}`}>{review.status}</span>
        )}
        {review.ci_status && <CIRing status={review.ci_status} />}
        <MyReviewPill state={review.my_review_state} />
      </div>

      {/* PR title -- the most important "what is this" signal, given a
          full row so it never gets squeezed by the chip stack above. */}
      {review.title && (
        <div
          data-testid="review-pr-title"
          style={{
            fontSize: 13, fontWeight: 600, lineHeight: 1.35,
            marginBottom: 6, color: 'var(--text)',
            overflowWrap: 'anywhere',
          }}
        >
          {review.title}
        </div>
      )}

      {/* Compact meta strip: author / branch / diff / updated. Each is
          only rendered when present so a partial row doesn't show
          empty separators. */}
      {(review.author || review.head_branch || review.additions || review.deletions || review.last_updated) && (
        <div
          style={{
            display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center',
            fontSize: 10, color: 'var(--text-dim)', marginBottom: 10,
          }}
        >
          {review.author && (
            <span>
              by <span style={{ color: 'var(--accent)' }}>{review.author}</span>
            </span>
          )}
          {review.head_branch && (
            <span style={{ fontFamily: 'monospace' }}>
              {branchDisplay}
              {review.base_branch ? ` -> ${review.base_branch}` : ''}
            </span>
          )}
          {(review.additions || review.deletions) && (
            <span style={{ fontFamily: 'monospace' }}>
              <span style={{ color: 'var(--green)' }}>+{review.additions || 0}</span>
              {' '}
              <span style={{ color: 'var(--red)' }}>-{review.deletions || 0}</span>
            </span>
          )}
          {typeof review.comment_count === 'number' && review.comment_count > 0 && (
            <span>{review.comment_count} comments</span>
          )}
          {review.last_updated && <span>{timeAgo(review.last_updated)}</span>}
        </div>
      )}

      {/* Session: collapsed-by-default in non-active reviews so the
          rest of the card stays visible. SessionCard's own header
          still lets the user expand the terminal when they need it. */}
      {sessionLive && review.session_name ? (
        <div style={{ marginBottom: 10 }}>
          <SessionCard
            sessionName={review.session_name}
            initialStatus={curState === 'active' ? 'idle' : 'stopped'}
            compact
            autoExpand={autoExpandSession}
            onKill={handleKillSession}
          />
          {review.started_at && (
            <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 4 }}>
              started {review.started_at.slice(0, 16).replace('T', ' ')}
            </div>
          )}
        </div>
      ) : (
        // No session yet -- give the user the action buttons right
        // here instead of asking them to look elsewhere. The primary
        // `review-pr` action gets the accent treatment.
        <div data-testid="review-actions" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
          {actions.length === 0 ? (
            <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>Loading actions...</span>
          ) : actions.map((a) => (
            <button
              key={a.id}
              data-testid={`review-action-${a.id}`}
              className={a.id === 'review-pr' ? 'btn-action accent' : 'btn-action'}
              disabled={!!launching}
              onClick={() => handleLaunch(a.id)}
              style={{ fontSize: 11, padding: '4px 10px' }}
              title={a.prompt_template?.slice(0, 200) || a.label}
            >
              {launching === a.id ? '...' : a.label}
            </button>
          ))}
        </div>
      )}

      {/* Workflow state picker */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-dim)', marginBottom: 4 }}>
          Workflow state
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {WORKFLOW_STATES.map((s) => {
            const selected = s.value === curState
            return (
              <button
                key={s.value}
                className="btn-action"
                onClick={() => !selected && setWorkflowState(s.value)}
                disabled={savingState || selected}
                title={s.desc}
                style={{
                  fontSize: 10, padding: '3px 8px',
                  background: selected ? 'rgba(251, 191, 36, 0.18)' : undefined,
                  borderColor: selected ? ACCENT : undefined,
                  color: selected ? ACCENT : undefined,
                  fontWeight: selected ? 700 : 400,
                }}
              >
                {s.label}
              </button>
            )
          })}
        </div>
      </div>

      {/* History timeline */}
      <div>
        <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-dim)', marginBottom: 4 }}>
          History ({history.length})
        </div>
        {history.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--text-dim)', fontStyle: 'italic' }}>
            No history yet. Entries appear as you run review actions.
          </div>
        ) : (
          <div
            data-testid="review-history"
            style={{ display: 'flex', flexDirection: 'column', gap: 2, maxHeight: 240, overflowY: 'auto' }}
          >
            {history.map((e, i) => (
              <div key={i} style={{ fontSize: 11, display: 'flex', gap: 6 }}>
                <span style={{ color: 'var(--text-dim)', flexShrink: 0, fontFamily: 'monospace' }}>
                  {e.ts.slice(5, 16).replace('T', ' ')}
                </span>
                <span style={{ flex: 1 }}>{e.text}</span>
                {e.source !== 'manual' && (
                  <span
                    style={{
                      fontSize: 9, color: 'var(--text-dim)',
                      padding: '0 4px', borderRadius: 2,
                      background: 'var(--panel-bg)', flexShrink: 0,
                    }}
                  >{e.source}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
