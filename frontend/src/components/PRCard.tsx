import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import type { PRDetail as PRDetailType, ActionDef, PR } from '../types'
import { api } from '../api'
import { ghAvatar, renderMarkdown } from '../utils'
import { useLiveClock } from '../hooks/useLiveClock'
import { useEventBus } from '../hooks/useEventBus'
import { CISection } from './pr/CISection'
import { ciResult, isFailed, isNonBlocking } from './pr/ciHelpers'
import { ReviewSection } from './pr/ReviewSection'
import { FileList } from './pr/FileList'
import { InlineComments, GeneralComments } from './pr/CommentThread'
import { evalActionCondition } from './TaskCard'
import { useAlert } from './Alert'
import { useSessionLauncher } from '../hooks/useSessionLauncher'
import { useClickOutside } from '../hooks/useClickOutside'

interface PRCardProps {
  repo: string
  number: number
  projectId?: string
  taskId?: string
  /**
   * When set, this PR is being shown inside All Reviews (not All PRs).
   * Flips the action-button set from context='pr' (task-based PR
   * actions) to context='review' (Review PR / Draft Reply / Sync
   * Status) and routes `onOpenAction` through the review-session
   * endpoint rather than the task-session one.
   */
  reviewUrl?: string
  onOpenAction?: (actionId: string, customPrompt?: string) => void
}

// Description renders collapsed by default whenever the body is long
// enough to need scrolling. Most GitHub PR templates blow past this
// threshold (release notes / test plan / build links), so the default
// view is just the first ~5 lines + a "Show more" toggle.
const DESCRIPTION_COLLAPSED_HEIGHT = 96

function DescriptionEditor({ body, repo, number, onSaved }: {
  body: string
  repo: string
  number: number
  onSaved: (newBody: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(body)
  const [saving, setSaving] = useState(false)
  const [expanded, setExpanded] = useState(false)
  // True only when the rendered body actually exceeds the collapsed
  // height -- short descriptions should NOT show a Show-more button.
  // Measured after each render via the ref below.
  const [overflowing, setOverflowing] = useState(false)
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const { alert } = useAlert()

  // Sync draft when body changes externally (e.g. refresh)
  useEffect(() => {
    if (!editing) setDraft(body)
  }, [body, editing])

  // Re-measure overflow after each body change. `scrollHeight` reads
  // the real rendered height ignoring the maxHeight cap, so comparing
  // to the threshold tells us whether collapsing actually hides
  // content. Re-collapse on body change so a fresh PR isn't stuck
  // expanded from the previous one.
  useEffect(() => {
    if (editing) return
    setExpanded(false)
    const el = bodyRef.current
    if (!el) { setOverflowing(false); return }
    // Defer one frame so dangerouslySetInnerHTML has actually painted.
    const id = requestAnimationFrame(() => {
      setOverflowing(el.scrollHeight > DESCRIPTION_COLLAPSED_HEIGHT + 4)
    })
    return () => cancelAnimationFrame(id)
  }, [body, editing])

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.updatePRBody(repo, number, draft)
      onSaved(draft)
      setEditing(false)
    } catch (e) {
      await alert({
        title: 'Failed to update',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 4, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}>
          Description
          <span style={{ fontSize: 9, color: 'var(--accent)', fontWeight: 400 }}>editing</span>
        </div>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={saving}
          style={{
            width: '100%', minHeight: 200, background: 'var(--panel-bg)',
            border: '1px solid var(--accent)', borderRadius: 6,
            color: 'var(--text)', padding: 12, fontSize: 12,
            fontFamily: 'Menlo, Monaco, "Courier New", monospace',
            resize: 'vertical',
          }}
        />
        <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', marginTop: 6 }}>
          <button className="btn-action" onClick={() => { setDraft(body); setEditing(false) }} disabled={saving}>
            Cancel
          </button>
          <button className="btn-action accent" onClick={handleSave} disabled={saving || draft === body}>
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    )
  }

  if (!body) return null

  const showToggle = overflowing
  const collapsed = !expanded && overflowing

  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 4, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}>
        Description
        <button
          className="btn-action"
          style={{ padding: '1px 5px', fontSize: 9 }}
          title="Edit description"
          onClick={() => setEditing(true)}
        >
          Edit
        </button>
        {showToggle && (
          <button
            data-testid="pr-description-toggle"
            className="btn-action"
            style={{ padding: '1px 5px', fontSize: 9, marginLeft: 'auto' }}
            onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v) }}
          >
            {expanded ? 'Show less' : 'Show more'}
          </button>
        )}
      </div>
      <div style={{ position: 'relative' }}>
        <div
          ref={bodyRef}
          className="md-body"
          // .md-body sets font-size from CSS; inline overrides removed
          // so the per-element rules (h1=14, code=10, pre=10) take
          // effect and the column actually wraps inside the narrow
          // PR Card pane.
          style={{
            maxHeight: collapsed ? DESCRIPTION_COLLAPSED_HEIGHT : 360,
            overflowY: collapsed ? 'hidden' : 'auto',
            background: 'var(--panel-bg)',
            padding: 10, borderRadius: 6,
            cursor: 'pointer',
            transition: 'max-height 0.18s ease',
          }}
          title="Click to edit"
          onClick={() => setEditing(true)}
          dangerouslySetInnerHTML={{ __html: renderMarkdown(body) }}
        />
        {/* Fade overlay on the collapsed bottom edge so the user can
            tell the description is truncated, not just short. */}
        {collapsed && (
          <div
            aria-hidden="true"
            style={{
              position: 'absolute', left: 0, right: 0, bottom: 0,
              height: 28,
              background: 'linear-gradient(to bottom, transparent, var(--panel-bg))',
              borderRadius: '0 0 6px 6px',
              pointerEvents: 'none',
            }}
          />
        )}
      </div>
    </div>
  )
}

export function PRCard({ repo, number, projectId, taskId, reviewUrl, onOpenAction }: PRCardProps) {
  useLiveClock()
  const [pr, setPr] = useState<PRDetailType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [myLogins, setMyLogins] = useState<string[]>([])
  const [repoAccountMap, setRepoAccountMap] = useState<Record<string, string>>({})
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const [savingTitle, setSavingTitle] = useState(false)
  const { alert } = useAlert()

  // Review-mode action buttons launch the session right here instead
  // of bouncing the actionId up to ReviewsPage as a callback. The
  // launcher is created unconditionally (rules of hooks) but no-ops
  // for non-review surfaces (`reviewUrl=''`); task-mode action
  // clicks still go through the `onOpenAction` callback to TaskCard.
  const { launch: launchReview } = useSessionLauncher({
    type: 'review',
    reviewUrl: reviewUrl || '',
  })
  const handleActionClick = useCallback(
    (actionId: string, customPrompt?: string) => {
      if (reviewUrl) {
        launchReview({ actionId, customPrompt })
      } else if (customPrompt !== undefined) {
        onOpenAction?.(actionId, customPrompt)
      } else {
        // Match original arity so existing `toBeCalledWith('open')`
        // assertions in test fixtures keep passing.
        onOpenAction?.(actionId)
      }
    },
    [reviewUrl, launchReview, onOpenAction],
  )

  // Fetch current user's GitHub logins and repo-account mapping (once)
  useEffect(() => {
    fetch('/api/me').then(r => r.json()).then(d => {
      setMyLogins(d.logins || [])
      setRepoAccountMap(d.repoAccount || {})
    }).catch(() => {})
  }, [])

  // Resolve the correct GitHub login for the current PR's repo. Memoized so
  // useCallback consumers (e.g. handlePostComment) don't invalidate on every
  // render.
  const myLoginForRepo = useMemo(() => {
    for (const [key, login] of Object.entries(repoAccountMap)) {
      if (key !== 'default' && repo.includes(key)) return login
    }
    return repoAccountMap['default'] || myLogins[0] || ''
  }, [repoAccountMap, repo, myLogins])
  const [ciExpanded, setCiExpanded] = useState(false)
  const [commentText, setCommentText] = useState('')
  const [commenting, setCommenting] = useState(false)
  const [prActions, setPrActions] = useState<ActionDef[]>([])
  const [hasUpdate, setHasUpdate] = useState(false)
  // Review-submit popover state. Modeled after GitHub's "Review
  // changes" dropdown button: compact affordance in the header,
  // expands into an anchored popover with textarea + radios.
  const [reviewOpen, setReviewOpen] = useState(false)
  const [reviewEvent, setReviewEvent] = useState<'APPROVE' | 'REQUEST_CHANGES' | 'COMMENT'>('COMMENT')
  const [reviewBody, setReviewBody] = useState('')
  const [submittingReview, setSubmittingReview] = useState(false)
  const reviewPopoverRef = useRef<HTMLDivElement>(null)
  useClickOutside(reviewPopoverRef, () => setReviewOpen(false))

  // Show "has update" badge when github events arrive
  useEventBus('github.*', useCallback(() => { setHasUpdate(true) }, []))

  // Load the action-button set for the current context. Review mode
  // shows a slim review-only set (context='review'); task-linked PRs
  // show the full PR action set (fix-ci, address-comments, ...).
  useEffect(() => {
    // Review-mode action buttons live on the Review Card now -- skip
    // the fetch entirely when in review mode so we don't spend a
    // request on data the UI will never render.
    if (reviewUrl) {
      setPrActions([])
      return
    }
    if (projectId && taskId) {
      api.getActions('pr')
        .then((data) => setPrActions(data.actions || []))
        .catch(() => setPrActions([]))
    } else {
      setPrActions([])
    }
  }, [projectId, taskId, reviewUrl])

  const fetchDetail = useCallback(() => {
    setLoading(true)
    setError(null)
    setHasUpdate(false)
    api.getPRDetail(repo, number)
      .then((detail) => {
        setPr(detail)
        setLoading(false)
      })
      .catch((e: Error) => {
        setError(e.message || 'Failed to load PR detail')
        setLoading(false)
      })
  }, [repo, number])

  useEffect(() => {
    fetchDetail()
  }, [fetchDetail])

  const handlePostComment = useCallback(async () => {
    if (!commentText.trim() || !pr) return
    const body = commentText.trim()
    setCommenting(true)
    try {
      await fetch('/api/pr-comment', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo, number, body }),
      })
      // Optimistic insert instead of full reload
      const myUser = myLoginForRepo || 'me'
      setPr({
        ...pr,
        comments: [...(pr.comments || []), {
          id: Date.now(),
          author: { login: myUser },
          body,
          createdAt: new Date().toISOString(),
        }],
      })
      setCommentText('')
    } catch {
      // silently fail
    }
    setCommenting(false)
  }, [commentText, pr, repo, number, myLoginForRepo])

  const handleSubmitReview = useCallback(async () => {
    if (!pr) return
    const body = reviewBody.trim()
    // GitHub rejects empty bodies for REQUEST_CHANGES / COMMENT events.
    // Guard client-side so the user gets an inline hint instead of a
    // generic 422 from the backend.
    if (reviewEvent !== 'APPROVE' && !body) {
      await alert({
        title: `Review body required`,
        message: `A ${reviewEvent === 'REQUEST_CHANGES' ? 'request-changes' : 'comment'} review must include a message.`,
        kind: 'warning',
      })
      return
    }
    setSubmittingReview(true)
    try {
      await api.submitPRReview(repo, number, reviewEvent, body)
      setReviewBody('')
      setReviewOpen(false)
      // Local optimistic reflection: bump reviewDecision so the header
      // pill updates without waiting for the next poll.
      const optimisticDecision =
        reviewEvent === 'APPROVE' ? 'APPROVED'
        : reviewEvent === 'REQUEST_CHANGES' ? 'CHANGES_REQUESTED'
        : pr.reviewDecision
      setPr({
        ...pr,
        reviewDecision: optimisticDecision || pr.reviewDecision,
        reviews: [
          ...(pr.reviews || []),
          { author: { login: myLoginForRepo || 'me' }, state: reviewEvent },
        ],
      })
    } catch (e) {
      await alert({
        title: 'Failed to submit review',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setSubmittingReview(false)
    }
  }, [pr, reviewEvent, reviewBody, repo, number, myLoginForRepo, alert])

  if (loading) {
    return (
      <div data-testid="pr-detail-loading" style={{ padding: 16, color: 'var(--text-dim)', fontSize: 12 }}>
        Loading PR #{number}...
      </div>
    )
  }

  if (error || !pr) {
    return (
      <div data-testid="pr-detail-error" style={{ padding: 16, color: 'var(--red)', fontSize: 12 }}>
        {error || 'Failed to load PR detail'}
      </div>
    )
  }

  const authorLogin = pr.author?.login || ''
  const checks = pr.statusCheckRollup || []
  const ciBlockingFailed = checks.filter((c) => isFailed(ciResult(c)) && !isNonBlocking(c)).length
  const stClass = pr.state === 'MERGED' ? 'delivered' : pr.state === 'CLOSED' ? 'cancelled' : 'ordered'
  const hasTask = !!(projectId && taskId)

  return (
    <div data-testid="pr-detail">
      {/* Header */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', marginBottom: 10 }}>
        <img src={ghAvatar(authorLogin)} alt={authorLogin}
          style={{ width: 32, height: 32, borderRadius: '50%', marginTop: 2, flexShrink: 0 }} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div data-testid="pr-title" style={{ fontSize: 15, fontWeight: 700, lineHeight: 1.3, display: 'flex', alignItems: 'center', gap: 8 }}>
            {reviewUrl && (
              // Amber "REVIEW" pill so the user can tell they're in the
              // All Reviews flow at a glance -- visually distinct from
              // the purple accent used for my-work actions.
              <span
                data-testid="review-badge"
                style={{
                  fontSize: 9, fontWeight: 800, letterSpacing: 0.5,
                  padding: '2px 6px', borderRadius: 4,
                  background: 'rgba(251, 191, 36, 0.18)',
                  color: '#f59e0b',
                  border: '1px solid rgba(251, 191, 36, 0.45)',
                  flexShrink: 0,
                }}
              >REVIEW</span>
            )}
            {editingTitle ? (
              <div style={{ flex: 1, display: 'flex', gap: 6, alignItems: 'center' }}>
                <input
                  autoFocus
                  value={titleDraft}
                  onChange={(e) => setTitleDraft(e.target.value)}
                  disabled={savingTitle}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && titleDraft.trim()) {
                      setSavingTitle(true)
                      api.updatePRTitle(repo, number, titleDraft.trim())
                        .then(() => {
                          setPr((prev) => prev ? { ...prev, title: titleDraft.trim() } : prev)
                          setEditingTitle(false)
                        })
                        .catch((err) => alert({ title: 'Failed to update title', message: err instanceof Error ? err.message : String(err), kind: 'error' }))
                        .finally(() => setSavingTitle(false))
                    }
                    if (e.key === 'Escape') setEditingTitle(false)
                  }}
                  style={{
                    flex: 1, padding: '4px 8px', fontSize: 14, fontWeight: 700,
                    background: 'var(--panel-bg)', border: '1px solid var(--accent)',
                    borderRadius: 4, color: 'var(--text)', fontFamily: 'inherit',
                  }}
                />
                <button className="btn-action accent" style={{ fontSize: 10, padding: '3px 8px' }}
                  disabled={!titleDraft.trim() || savingTitle}
                  onClick={() => {
                    setSavingTitle(true)
                    api.updatePRTitle(repo, number, titleDraft.trim())
                      .then(() => {
                        setPr((prev) => prev ? { ...prev, title: titleDraft.trim() } : prev)
                        setEditingTitle(false)
                      })
                      .catch((err) => alert({ title: 'Failed to update title', message: err instanceof Error ? err.message : String(err), kind: 'error' }))
                      .finally(() => setSavingTitle(false))
                  }}
                >{savingTitle ? '...' : 'Save'}</button>
                <button className="btn-action" style={{ fontSize: 10, padding: '3px 8px' }}
                  onClick={() => setEditingTitle(false)} disabled={savingTitle}
                >Cancel</button>
              </div>
            ) : (
              <span
                style={{ flex: 1, cursor: 'pointer' }}
                title="Click to edit title"
                onClick={() => { setTitleDraft(pr.title); setEditingTitle(true) }}
              >{pr.title}</span>
            )}
            <button
              data-testid="pr-refresh-btn"
              className="btn-action"
              style={{ flexShrink: 0, padding: '2px 6px', fontSize: 11, position: 'relative' }}
              title="Refresh PR details"
              onClick={(e) => { e.stopPropagation(); fetchDetail() }}
            >
              &#8635;
              {hasUpdate && (
                <span style={{
                  position: 'absolute', top: -3, right: -3,
                  width: 8, height: 8, borderRadius: '50%',
                  background: 'var(--accent)', border: '1.5px solid var(--card-bg)',
                }} />
              )}
            </button>

            {/* "Review changes" button -- GitHub-style dropdown anchored
                to the header. Only shown on open PRs I didn't author. */}
            {pr.state === 'OPEN' && authorLogin && !myLogins.includes(authorLogin) && (
              <div ref={reviewPopoverRef} style={{ position: 'relative', flexShrink: 0 }}
                   data-testid="review-submit-panel">
                <button
                  className="btn-action accent"
                  style={{
                    padding: '3px 10px', fontSize: 11, fontWeight: 600,
                    display: 'flex', alignItems: 'center', gap: 5,
                  }}
                  title="Submit Approve / Request changes / Comment review"
                  onClick={(e) => { e.stopPropagation(); setReviewOpen((v) => !v) }}
                >
                  Review changes
                  <span style={{ fontSize: 8, opacity: 0.7 }}>{reviewOpen ? '▲' : '▼'}</span>
                </button>
                {reviewOpen && (
                  <div
                    style={{
                      position: 'absolute', top: 'calc(100% + 6px)', right: 0,
                      width: 380, zIndex: 20,
                      background: 'var(--card-bg)', border: '1px solid var(--border)',
                      borderRadius: 8, padding: 12,
                      boxShadow: '0 6px 20px rgba(0,0,0,0.35)',
                    }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div style={{
                      fontSize: 12, fontWeight: 700, marginBottom: 8,
                      color: 'var(--text)',
                    }}>
                      Finish your review
                    </div>
                    <textarea
                      data-testid="review-body-input"
                      placeholder="Leave a review comment... (optional for Approve, required for Request changes / Comment)"
                      value={reviewBody}
                      onChange={(e) => setReviewBody(e.target.value)}
                      disabled={submittingReview}
                      style={{
                        width: '100%', minHeight: 90, background: 'var(--input-bg)',
                        border: '1px solid var(--border)', borderRadius: 6,
                        color: 'var(--text)', padding: 10, fontSize: 12, resize: 'vertical',
                        fontFamily: 'inherit',
                      }}
                    />
                    <div style={{
                      display: 'flex', flexDirection: 'column', gap: 6,
                      marginTop: 8,
                    }}>
                      {([
                        ['COMMENT', 'Comment',
                         'Submit general feedback without explicit approval.'],
                        ['APPROVE', 'Approve',
                         'Signal that these changes look good to merge.'],
                        ['REQUEST_CHANGES', 'Request changes',
                         'Block merge until feedback is addressed.'],
                      ] as const).map(([value, label, desc]) => (
                        <label
                          key={value}
                          style={{
                            display: 'flex', alignItems: 'flex-start', gap: 8,
                            fontSize: 11, cursor: 'pointer',
                            padding: '6px 8px',
                            border: '1px solid',
                            borderColor: reviewEvent === value
                              ? 'var(--accent)' : 'var(--border)',
                            borderRadius: 6,
                            background: reviewEvent === value
                              ? 'var(--panel-bg)' : 'transparent',
                          }}
                        >
                          <input
                            type="radio"
                            name="review-event"
                            value={value}
                            checked={reviewEvent === value}
                            onChange={() => setReviewEvent(value)}
                            disabled={submittingReview}
                            style={{ marginTop: 2 }}
                          />
                          <div style={{ display: 'flex', flexDirection: 'column' }}>
                            <span style={{ fontWeight: 600 }}>{label}</span>
                            <span style={{ color: 'var(--text-dim)', fontSize: 10 }}>{desc}</span>
                          </div>
                        </label>
                      ))}
                    </div>
                    <div style={{
                      display: 'flex', justifyContent: 'flex-end',
                      gap: 6, marginTop: 10,
                    }}>
                      <button
                        className="btn-action"
                        style={{ fontSize: 11, padding: '4px 10px' }}
                        onClick={() => setReviewOpen(false)}
                        disabled={submittingReview}
                      >
                        Cancel
                      </button>
                      <button
                        className="btn-action accent"
                        onClick={handleSubmitReview}
                        disabled={submittingReview}
                        data-testid="review-submit-btn"
                        style={{ fontSize: 11, padding: '4px 14px', fontWeight: 600 }}
                      >
                        {submittingReview ? 'Submitting...' : 'Submit review'}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 11, marginTop: 4, flexWrap: 'wrap' }}>
            <span className={`fk-status ${stClass}`}>{pr.state}</span>
            <span data-testid="pr-author" style={{ color: 'var(--text-dim)', fontWeight: 600 }}>{authorLogin}</span>
            <span style={{ fontFamily: 'monospace', color: 'var(--text-dim)', fontSize: 10 }}>{pr.headRefName || ''}</span>
            <a href={pr.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 10, color: 'var(--accent)' }}>
              #{pr.number || ''}
            </a>
          </div>
        </div>
      </div>

      {/* Status bar */}
      <div style={{ display: 'flex', alignItems: 'center', padding: '8px 12px', background: 'var(--panel-bg)', borderRadius: 6, marginBottom: 12, gap: 8, fontSize: 11, position: 'relative' }}>
        <CISection checks={checks} ciExpanded={ciExpanded} onToggleExpand={() => setCiExpanded(!ciExpanded)} />
        <div style={{ flex: 1 }} />
        <ReviewSection reviews={pr.reviews} reviewDecision={pr.reviewDecision} />
        <div style={{ flex: 1 }} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'monospace', fontSize: 10 }}>
          <span style={{ color: 'var(--green)' }}>+{pr.additions || 0}</span>
          <span style={{ color: 'var(--red)' }}>-{pr.deletions || 0}</span>
          <span style={{ color: 'var(--text-dim)' }}>{(pr.files || []).length} files</span>
          {pr.mergeable && <span style={{ color: 'var(--text-dim)' }}>{pr.mergeable}</span>}
        </div>
      </div>

      {/* Action buttons -- task-mode only. In review mode the action
          buttons live on the Review Card (middle pane); duplicating
          them here just confused the user about which entry to click. */}
      {!reviewUrl && (
        <div data-testid="pr-actions" style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
          {hasTask && (
            <button className="btn-action accent" onClick={() => handleActionClick('open')}>
              <img src="/static/claude-favicon.ico" width={12} height={12} style={{ verticalAlign: 'middle', marginRight: 4 }} alt="" />
              Open Agent
            </button>
          )}
          {hasTask && prActions
            .filter((a) => {
              if (a.id === 'open') return false
              const actionData = {
                ci_status: ciBlockingFailed > 0 ? 'failure' : 'success',
                prs: [{ number, status: 'open' }] as unknown as PR[],
                pr_number: number,
              }
              return evalActionCondition(a.condition, actionData)
            })
            .map((a) => (
              <button
                key={a.id}
                className="btn-action"
                onClick={() => handleActionClick(a.id)}
              >
                {a.label}
              </button>
            ))}
        </div>
      )}

      {/* Labels */}
      {pr.labels && pr.labels.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 12 }}>
          {pr.labels.map((l) => (
            <span key={l.name} style={{ fontSize: 10, padding: '2px 6px', borderRadius: 10, background: 'rgba(99,102,241,0.15)', color: 'var(--accent)' }}>
              {l.name}
            </span>
          ))}
        </div>
      )}

      {/* Description (click to edit) */}
      <DescriptionEditor
        body={pr.body || ''}
        repo={repo}
        number={number}
        onSaved={(newBody) => setPr({ ...pr, body: newBody })}
      />

      {/* Files with expandable diffs */}
      <FileList
        files={pr.files}
        repo={repo}
        prNumber={number}
        onComment={async (path, line, body) => {
          try {
            await api.replyToComment(repo, number, 0, `**${path}:${line}**\n${body}`, false)
            fetchDetail()
          } catch { /* ignore */ }
        }}
        onAskAgent={hasTask ? (context) => {
          // Ask Agent = conversational Q&A about the selected code.
          // `context` already carries the user's question + snippet from
          // FileList; pass it through verbatim as the custom prompt.
          // `open` action has an empty template -- customPrompt overrides,
          // so Agent gets exactly what the user typed plus code context.
          handleActionClick('open', context)
        } : undefined}
      />

      {/* Inline code comments */}
      <InlineComments inlineComments={pr.inlineComments} repo={repo} prNumber={number} onRefresh={fetchDetail} myLogins={myLogins} myLogin={myLoginForRepo}
        onAskAgent={hasTask ? (commentUrl) => handleActionClick('draft-reply',
          `Draft a concise reply to this review comment: ${commentUrl}\nRead the thread, construct the reply message only, do NOT post it.`
        ) : undefined} />

      {/* General comments */}
      <GeneralComments comments={pr.comments} repo={repo} prNumber={number} onRefresh={fetchDetail} myLogins={myLogins} />

      {/* Reply editor */}
      <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
        <img
          src={myLoginForRepo ? ghAvatar(myLoginForRepo) : ''}
          alt=""
          style={{ width: 28, height: 28, borderRadius: '50%', flexShrink: 0, marginTop: 2 }}
        />
        <div style={{ flex: 1 }}>
          <textarea
            data-testid="comment-input"
            placeholder="Write a comment... (Markdown supported)"
            value={commentText}
            onChange={(e) => setCommentText(e.target.value)}
            disabled={commenting}
            style={{
              width: '100%', minHeight: 60, background: 'var(--input-bg)',
              border: '1px solid var(--border)', borderRadius: 6,
              color: 'var(--text)', padding: 10, fontSize: 12, resize: 'vertical',
            }}
          />
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 6, gap: 6 }}>
            {hasTask && (
              <button className="btn-action" style={{ fontSize: 10, padding: '4px 10px' }}
                onClick={() => {
                  onOpenAction?.('draft-reply',
                    `Draft concise reply messages for all unresolved comments on ${pr.url}\nRead each thread, construct reply messages only, do NOT post them.`
                  )
                }}>
                <img src="/static/claude-favicon.ico" width={12} height={12}
                  style={{ verticalAlign: 'middle', marginRight: 3 }} alt="" />
                Draft Reply
              </button>
            )}
            <button className="btn-action accent" onClick={handlePostComment} disabled={commenting || !commentText.trim()}>
              Comment
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
