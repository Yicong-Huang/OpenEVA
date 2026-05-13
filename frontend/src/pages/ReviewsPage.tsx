import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { PR } from '../types'

import { ReviewCard } from '../components/ReviewCard'
import { ReviewNode } from '../components/ReviewNode'
import { PRCard } from '../components/PRCard'
import { useAlert } from '../components/Alert'
import { useEventBus } from '../hooks/useEventBus'
import { useLayoutRatios } from '../hooks/useLayoutRatios'

type ReviewPR = PR & { repo: string; source?: 'github' | 'manual' | 'both' }

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
    try {
      await api.addReviewWatch(url.trim())
      await load()
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

  // Merged-PR housekeeping. Two rules:
  //   1. If a PR is merged AND I already approved or commented on it,
  //      drop it from the UI entirely. The DB row stays so analytics
  //      and "did I review this?" lookups still work; we just don't
  //      pull it into the visual queue every time.
  //   2. Other merged PRs (no review from me, or pending re-request)
  //      stay in the list but fold to a dim, meta-less row -- they're
  //      reference material at this point, not actionable, so they
  //      shouldn't compete with open reviews for attention.
  const isMerged = (pr: ReviewPR) => pr.status === 'merged'
  const iAmDoneWith = (pr: ReviewPR) =>
    pr.my_review_state === 'approved' || pr.my_review_state === 'commented'
  const visiblePrs = ((prs || []) as ReviewPR[]).filter(
    (p) => !(isMerged(p) && iAmDoneWith(p))
  )
  const hiddenMergedCount = ((prs || []) as ReviewPR[]).length - visiblePrs.length

  // Group by org/repo so PRs from the same codebase stay together.
  const grouped = visiblePrs.reduce<Record<string, ReviewPR[]>>((acc, p) => {
    const k = p.repo || 'other'
    if (!acc[k]) acc[k] = []
    acc[k].push(p)
    return acc
  }, {})
  const repoKeys = Object.keys(grouped).sort()

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
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <span style={{ fontSize: 14, fontWeight: 700 }}>Reviews</span>
          <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>
            {prs ? `${visiblePrs.length} shown` : ''}
            {hiddenMergedCount > 0 && (
              <span
                title={`${hiddenMergedCount} merged PR${hiddenMergedCount > 1 ? 's' : ''} I already approved or commented on are hidden`}
                style={{ marginLeft: 6, color: 'var(--text-subtle)' }}
              >
                ({hiddenMergedCount} hidden)
              </span>
            )}
          </span>
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
        {!error && prs && prs.length === 0 && (
          <div style={{ color: 'var(--text-dim)', fontSize: 12 }}>Nothing to review right now.</div>
        )}
        {repoKeys.map((repo) => (
          <div key={repo} style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: 'var(--accent)', fontWeight: 600, marginBottom: 6 }}>
              {repo}
              <span style={{ color: 'var(--text-dim)', fontWeight: 400, marginLeft: 6 }}>
                ({grouped[repo].length})
              </span>
            </div>
            {grouped[repo].map((pr) => {
              const isManual = pr.source === 'manual' || pr.source === 'both'
              const isSelected = !!selectedPR
                && selectedPR.repo === pr.repo
                && selectedPR.number === pr.number
              // Merged PRs that survived the filter (i.e. I haven't
              // approved/commented yet) fold to a dim, meta-less row
              // -- they're reference, not action, and shouldn't pull
              // attention away from open reviews.
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
        ))}
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
