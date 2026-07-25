import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { PR } from '../types'

import { ReviewCard } from '../components/ReviewCard'
import { ReviewNode } from '../components/ReviewNode'
import { PRCard } from '../components/PRCard'
import { useAlert } from '../components/Alert'
import { useEventBus } from '../hooks/useEventBus'
import { useLayoutRatios } from '../hooks/useLayoutRatios'
import { useCollapsedRepos } from '../hooks/useCollapsedRepos'

type ReviewPR = PR & { repo: string; source?: 'github' | 'manual' | 'both' }

// The repo we always sort to the bottom of the queue (a low-signal
// firehose). Matched exactly so a rename doesn't silently re-order it.
const TEXERA_REPO = 'apache/texera'

// Which review tab is showing. `active` = the open review queue;
// `completed` = PRs I approved that have since merged (these were
// previously hidden from the page entirely).
type ReviewTab = 'active' | 'completed'

/**
 * "All Reviews" -- flat list of open PRs awaiting my review, aggregated
 * across all configured GitHub accounts via the backend's
 * `/api/review-requests` route, merged with the manual watchlist and
 * any PR where someone @mentioned me.
 *
 * Layout mirrors PRsPage: left pane is the scrollable queue, right pane
 * is the full PRDetail view for the currently selected PR (so clicking
 * a PR opens it inline instead of navigating off to GitHub). Users who
 * prefer the GitHub UI can still click the PR's title inside PRDetail.
 */
interface ReviewsPageProps {
  selectedReviewUrl?: string | null
  onSelectReview?: (url: string | null) => void
}

// Default 3-pane width ratios. Source of truth in
// `core.settings.DEFAULT_REVIEWS_RATIOS`; the shared
// `useLayoutRatios` hook handles fetch + validation.
const DEFAULT_REVIEWS_RATIOS = [25, 35, 40] as const

export function ReviewsPage({ selectedReviewUrl, onSelectReview }: ReviewsPageProps = {}) {
  const [prs, setPrs] = useState<ReviewPR[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [tab, setTab] = useState<ReviewTab>('active')
  // Collapse state per repo group, persisted to settings. Seed
  // apache/texera collapsed by default (it's the bottom firehose).
  const { collapsed, toggle: toggleRepo } = useCollapsedRepos([TEXERA_REPO])
  const [queueRatio, cardRatio, detailRatio] = useLayoutRatios(
    'ui.layout.reviews_col_ratios',
    [...DEFAULT_REVIEWS_RATIOS],
  )
  // Fallback to local state when the page is rendered without
  // App-level wiring (standalone tests, embedded previews). Controlled
  // mode (URL param) activates when `onSelectReview` is provided.
  const [localUrl, setLocalUrl] = useState<string | null>(null)
  const activeReviewUrl = onSelectReview ? (selectedReviewUrl ?? null) : localUrl
  const { prompt, alert, confirm, confirmAt } = useAlert()

  // Derive the "selected PR" detail from the url + loaded queue. When
  // the URL param lands before the queue fetches, parse owner/repo/num
  // out of the URL itself so the detail panes render immediately.
  const selectedPR = activeReviewUrl
    ? (() => {
        const found = (prs || []).find((p) => p.url === activeReviewUrl)
        if (found) {
          return { repo: found.repo, number: found.number, url: found.url }
        }
        const m = activeReviewUrl.match(/github\.com\/([^/]+\/[^/]+)\/pull\/(\d+)/)
        if (m) return { repo: m[1], number: parseInt(m[2], 10), url: activeReviewUrl }
        return null
      })()
    : null

  const setSelectedPR = (pr: { repo: string; number: number; url: string } | null) => {
    const next = pr ? pr.url : null
    if (onSelectReview) onSelectReview(next)
    else setLocalUrl(next)
  }

  // When the user clicks a review-context action button inside PRDetail
  // (Review PR, Draft Reply, Sync Status), POST to /api/reviews/open with
  // the selected PR's url -- backend launches the tmux session and
  // returns the prompt to type into the TUI. We mirror TaskCard's
  // delivery flow: wait until the agent's REPL is idle, then type the
  // prompt followed by a carriage return. Without this the session
  // sits at an empty prompt forever because the action template never
  // makes it into the conversation.
  // DB-backed fetch: cheap, no gh call. Safe to run as often as we
  // want -- the source of truth is updated separately by the sync
  // worker + `github.review.updated` events.
  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.getReviewRequests()
      setPrs(res.prs || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Mark a review as "seen" whenever the user opens it -- snapshots
  // the current comment_count into last_seen_comment_count so the
  // "N new" badge clears. Re-fetches so the cleared badge shows up
  // in the queue without waiting for the next sync.
  useEffect(() => {
    if (!selectedPR) return
    const url = selectedPR.url
    let cancelled = false
    api.markReviewSeen(url).then(() => {
      if (!cancelled) load()
    }).catch(() => { /* best-effort -- badge will reset on next sync */ })
    return () => { cancelled = true }
  }, [selectedPR, load])

  // Auto-refetch when the backend finishes a (scheduled or manual)
  // sync pass. Emits are fire-and-forget -- the frontend just knows
  // "something changed, pull the fresh DB rows".
  useEventBus('github.review.updated', useCallback(() => {
    load()
    setSyncing(false)
  }, [load]))

  const handleSync = useCallback(async () => {
    setSyncing(true)
    try {
      await api.syncReviewRequests()
    } catch (e) {
      setSyncing(false)
      await alert({
        title: 'Sync failed',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
    // NOTE: we don't clear `syncing` here. The useEventBus handler
    // above clears it when the server emits `github.review.updated`,
    // so the spinner remains until refreshed data is actually back.
  }, [alert])

  const handleAdd = useCallback(async () => {
    const url = await prompt({
      title: 'Add PR to review list',
      message: 'Paste the GitHub PR URL (e.g. https://github.com/owner/repo/pull/123).',
      placeholder: 'https://github.com/...',
      confirmLabel: 'Add',
    })
    if (!url) return
    const trimmed = url.trim()
    try {
      await api.addReviewWatch(trimmed)
      await load()
      // Jump straight to the newly-added PR's detail view. A fresh add
      // is a to-review item, so make sure we're on the active tab (else
      // the selection would land on a PR the current tab filters out).
      // `setSelectedPR` parses owner/repo/number from the URL, so the
      // detail panes render even before the queue reflects the new row.
      setTab('active')
      setSelectedPR({ repo: '', number: 0, url: trimmed })
    } catch (e) {
      await alert({
        title: 'Could not add PR',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
  }, [prompt, alert, load])

  const handleRemove = useCallback(async (pr: ReviewPR, anchor?: { x: number; y: number }) => {
    const opts = {
      title: 'Remove from review list?',
      message: pr.title || pr.url,
      confirmLabel: 'Remove',
      danger: true,
    }
    // Pop a non-blocking bubble at the click position when we have one;
    // otherwise (programmatic call) fall back to the centred modal.
    const ok = anchor ? await confirmAt(opts, anchor) : await confirm(opts)
    if (!ok) return
    // Drop the right-pane view if we're removing the selected PR.
    if (selectedPR && selectedPR.repo === pr.repo && selectedPR.number === pr.number) {
      setSelectedPR(null)
    }
    try {
      await api.removeReviewWatch(pr.url)
      await load()
    } catch (e) {
      await alert({
        title: 'Remove failed',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
  }, [confirm, confirmAt, alert, load, selectedPR])

  // Tab partitioning. A PR is "completed" once it's merged AND I
  // approved it -- that's the terminal state for my review, so it
  // moves out of the active queue into the Completed tab (previously
  // these were hidden from the page entirely). Everything else stays
  // in the active queue, where merged-but-unreviewed PRs still fold to
  // a dim reference row (see `isFolded` below).
  const isMerged = (pr: ReviewPR) => pr.status === 'merged'
  const isCompleted = (pr: ReviewPR) =>
    isMerged(pr) &&
    (pr.my_review_state === 'approved' || pr.my_review_state === 'commented')
  const allPrs = (prs || []) as ReviewPR[]
  const activePrs = allPrs.filter((p) => !isCompleted(p))
  const completedPrs = allPrs.filter(isCompleted)
  const tabPrs = tab === 'completed' ? completedPrs : activePrs

  // Group by org/repo so PRs from the same codebase stay together.
  const grouped = tabPrs.reduce<Record<string, ReviewPR[]>>((acc, p) => {
    const k = p.repo || 'other'
    if (!acc[k]) acc[k] = []
    acc[k].push(p)
    return acc
  }, {})
  // Alphabetical, except apache/texera always sinks to the bottom.
  const repoKeys = Object.keys(grouped).sort((a, b) => {
    if (a === TEXERA_REPO && b !== TEXERA_REPO) return 1
    if (b === TEXERA_REPO && a !== TEXERA_REPO) return -1
    return a.localeCompare(b)
  })

  return (
    <div
      data-testid="all-reviews-page"
      style={{ display: 'flex', height: '100%', overflow: 'hidden' }}
    >
      {/* Left: review queue. Three-column layout when a PR is selected.
          Ratios (queue / card / detail) come from the
          `ui.layout.reviews_col_ratios` setting, default 25/35/40 --
          users can rebalance via the Settings UI. */}
      <div
        style={{
          width: selectedPR ? `${queueRatio}%` : '100%',
          overflowY: 'auto',
          padding: 12,
          flexShrink: 0,
          transition: 'width 0.2s',
          borderRight: selectedPR ? '1px solid var(--border)' : undefined,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          {/* Reviews / Completed tab switch. Completed = PRs I approved
              that have merged (terminal review state). */}
          {(['active', 'completed'] as ReviewTab[]).map((t) => {
            const isActive = tab === t
            const count = t === 'completed' ? completedPrs.length : activePrs.length
            const label = t === 'completed' ? 'Completed' : 'Reviews'
            return (
              <button
                key={t}
                className={`btn-action${isActive ? ' accent' : ''}`}
                style={{ fontSize: 11, padding: '2px 10px', fontWeight: isActive ? 700 : 400 }}
                onClick={() => setTab(t)}
                aria-pressed={isActive}
                data-testid={`reviews-tab-${t}`}
              >
                {label}{prs ? ` (${count})` : ''}
              </button>
            )
          })}
          <span style={{ flex: 1 }} />
          <button
            className="btn-action accent"
            style={{ fontSize: 11, padding: '2px 8px' }}
            onClick={handleAdd}
            title="Manually pin a PR to the review queue (e.g. a 'please review' Slack ask)"
          >
            + Add PR
          </button>
          <button
            className="btn-action"
            style={{ fontSize: 11, padding: '2px 8px' }}
            onClick={handleSync}
            disabled={syncing || loading}
            title="Re-sync queue from GitHub (scheduled every 10 min; click to do it now)"
          >
            {syncing ? 'Syncing...' : 'Refresh'}
          </button>
        </div>
        {error && (
          <div style={{ color: 'var(--red)', fontSize: 12, marginBottom: 8 }}>
            Failed to load: {error}
          </div>
        )}
        {!error && prs && tabPrs.length === 0 && (
          <div style={{ color: 'var(--text-dim)', fontSize: 12 }}>
            {tab === 'completed'
              ? 'No approved-and-merged PRs yet.'
              : 'Nothing to review right now.'}
          </div>
        )}
        {repoKeys.map((repo) => {
          const isCollapsed = collapsed.has(repo)
          return (
            <div key={repo} style={{ marginBottom: 16 }}>
              <div
                onClick={() => toggleRepo(repo)}
                role="button"
                aria-expanded={!isCollapsed}
                data-testid={`reviews-repo-header-${repo}`}
                style={{
                  fontSize: 12, color: 'var(--accent)', fontWeight: 600,
                  marginBottom: 6, cursor: 'pointer', userSelect: 'none',
                  display: 'flex', alignItems: 'center', gap: 4,
                }}
                title={isCollapsed ? 'Expand' : 'Collapse'}
              >
                <span style={{ fontSize: 9, width: 10, display: 'inline-block' }}>
                  {isCollapsed ? '▶' : '▼'}
                </span>
                {repo}
                <span style={{ color: 'var(--text-dim)', fontWeight: 400, marginLeft: 2 }}>
                  ({grouped[repo].length})
                </span>
              </div>
              {!isCollapsed && grouped[repo].map((pr) => {
                const isManual = pr.source === 'manual' || pr.source === 'both'
                const isSelected = !!selectedPR
                  && selectedPR.repo === pr.repo
                  && selectedPR.number === pr.number
                // Merged PRs that survived the filter (i.e. I haven't
                // approved yet) fold to a dim, meta-less row -- they're
                // reference, not action, and shouldn't pull attention
                // away from open reviews.
                const isFolded = isMerged(pr)
                return (
                  <ReviewNode
                    key={`${repo}:${pr.number}`}
                    pr={pr}
                    isManual={isManual}
                    isSelected={isSelected}
                    isFolded={isFolded}
                    // 3-pane mode (selectedPR set) collapses the queue
                    // to ~25% -- compact flips PRCard into its 2-row
                    // layout so pr-title isn't ellipsis-truncated.
                    isCompact={!!selectedPR}
                    onSelect={() => setSelectedPR({ repo: pr.repo, number: pr.number, url: pr.url })}
                    onUnpin={(anchor) => handleRemove(pr, anchor)}
                  />
                )
              })}
            </div>
          )
        })}
      </div>

      {/* Middle: ReviewCard -- session state, workflow state, history */}
      {selectedPR && (
        <div
          data-testid="reviews-card-pane"
          style={{
            width: `${cardRatio}%`,
            overflowY: 'auto',
            padding: 12,
            flexShrink: 0,
            borderRight: '1px solid var(--border)',
          }}
        >
          <ReviewCard
            reviewUrl={selectedPR.url}
            repo={selectedPR.repo}
            number={selectedPR.number}
          />
        </div>
      )}

      {/* Right: PR detail */}
      {selectedPR && (
        <div
          data-testid="reviews-detail-pane"
          style={{ width: `${detailRatio}%`, overflowY: 'auto', padding: 12, flexShrink: 0 }}
        >
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 4 }}>
            <button
              className="btn-action"
              style={{ fontSize: 10, padding: '2px 8px' }}
              onClick={() => setSelectedPR(null)}
              title="Close detail pane"
            >
              Close
            </button>
          </div>
          {/* Passing reviewUrl flips PRDetail into review mode. In
              review mode PRDetail self-launches via useSessionLauncher;
              we don't pass onOpenAction. (Task-mode PRDetail still gets
              onOpenAction from TaskCard -- that path is unchanged.) */}
          <PRCard
            repo={selectedPR.repo}
            number={selectedPR.number}
            reviewUrl={selectedPR.url}
          />
        </div>
      )}
    </div>
  )
}
