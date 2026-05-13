import { useState, useCallback } from 'react'
import type { PR } from '../types'
import { timeAgo, repoFromPrUrl } from '../utils'
import { useLiveClock } from '../hooks/useLiveClock'

function RefreshBtn({ onRefresh }: { onRefresh: () => void }) {
  const [state, setState] = useState<'idle' | 'loading' | 'done'>('idle')
  const handleClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    if (state === 'loading') return
    setState('loading')
    onRefresh()
    // Show done briefly, then reset
    setTimeout(() => setState('done'), 800)
    setTimeout(() => setState('idle'), 2000)
  }, [onRefresh, state])

  return (
    <button
      className="btn-action"
      title="Refresh PR status"
      style={{
        padding: '0 4px', fontSize: 10, lineHeight: 1, flexShrink: 0, marginLeft: 'auto',
        opacity: state === 'idle' ? 0.4 : 1,
        color: state === 'done' ? 'var(--green)' : state === 'loading' ? 'var(--accent)' : undefined,
      }}
      onClick={handleClick}
    >
      {state === 'loading' ? '...' : state === 'done' ? '\u2713' : '\u21BB'}
    </button>
  )
}

interface Props {
  pr: PR
  showMeta?: boolean
  showTask?: boolean
  // Compact mode -- when the card lives in a narrow column (e.g. the
  // 25%-wide reviews queue in 3-pane mode) the row is too tight to
  // fit pr-num + pr-label + CI ring + ReviewIcon + MyReviewPill +
  // pr-title. Compact drops the lower-priority indicators (review
  // icon + my-review pill) and reorders so pr-title sits right after
  // the number, guaranteeing the title is visible even at ~250px.
  compact?: boolean
  onClick?: () => void
  onClickNumber?: () => void
  onRefresh?: () => void
}

interface CIRingProps {
  status: string
}

export function CIRing({ status }: CIRingProps) {
  const size = 14
  const r = (size - 2) / 2
  const cx = size / 2
  const cy = size / 2
  const circumference = 2 * Math.PI * r

  let color: string
  if (status === 'success') color = 'var(--green)'
  else if (status === 'failure') color = 'var(--red)'
  else color = 'var(--yellow)'

  const bgColor = 'var(--ci-ring-bg)'

  return (
    <svg
      data-testid="ci-ring"
      width={size}
      height={size}
      style={{ verticalAlign: 'middle', flexShrink: 0 }}
    >
      <circle cx={cx} cy={cy} r={r} fill="none" stroke={bgColor} strokeWidth={2} />
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeDasharray={`${circumference.toFixed(1)} 0`}
        transform={`rotate(-90 ${cx} ${cy})`}
      />
    </svg>
  )
}

interface ReviewIconProps {
  status: string
}

export function ReviewIcon({ status }: ReviewIconProps) {
  if (status === 'approved') {
    return (
      <span data-testid="review-icon" style={{ color: 'var(--green)' }} title="Approved">
        {'\u2713'}
      </span>
    )
  }
  if (status === 'changes_requested') {
    return (
      <span data-testid="review-icon" style={{ color: 'var(--red)' }} title="Changes requested">
        {'\u2717'}
      </span>
    )
  }
  if (status === 'review_required') {
    return (
      <span data-testid="review-icon" style={{ color: 'var(--yellow)' }} title="Review required">
        {'\u2022'}
      </span>
    )
  }
  return null
}


// Small pill showing *my* review stance for this PR, distinct from the
// PR-wide reviewDecision above. Rendered only for review-queue PRs
// where the backend computed a state (empty value -> nothing shown).
const MY_REVIEW_PILL: Record<string, { label: string; bg: string; fg: string; title: string }> = {
  pending_review: {
    label: 'Pending',
    bg: 'var(--yellow)', fg: '#1c1300',
    title: 'GitHub has me in the requested-reviewers list (or re-requested after I already reviewed)',
  },
  approved: {
    label: 'Approved',
    bg: 'var(--green)', fg: '#001b0a',
    title: 'My latest review on this PR was APPROVE',
  },
  changes_requested: {
    label: 'Changes',
    bg: 'var(--red)', fg: '#2a0000',
    title: 'My latest review on this PR was REQUEST_CHANGES',
  },
  commented: {
    label: 'Commented',
    bg: 'var(--text-dim)', fg: 'var(--card-bg)',
    title: 'My latest review on this PR was COMMENT (no approval stance)',
  },
}

export function MyReviewPill({ state }: { state?: string }) {
  const spec = state ? MY_REVIEW_PILL[state] : undefined
  if (!spec) return null
  return (
    <span
      data-testid="my-review-pill"
      data-state={state}
      title={spec.title}
      style={{
        fontSize: 9, fontWeight: 700, letterSpacing: 0.2,
        padding: '0 5px', lineHeight: '15px',
        borderRadius: 8,
        background: spec.bg, color: spec.fg,
        flexShrink: 0,
      }}
    >
      {spec.label}
    </span>
  )
}

// "N new" comment badge for the PR node. Shows up only when the
// backend's `unread_comment_count` field is positive -- i.e. there
// are GitHub comments the user hasn't acknowledged by opening the
// review yet. Bright accent so it's the eye's first stop on the row.
function NewCommentsBadge({ count }: { count: number }) {
  if (count <= 0) return null
  return (
    <span
      data-testid="pr-unread-comments-badge"
      title={`${count} new comment${count > 1 ? 's' : ''} since you last opened this PR`}
      style={{
        fontSize: 9, fontWeight: 700, letterSpacing: 0.2,
        padding: '0 6px', lineHeight: '15px',
        borderRadius: 8,
        background: 'var(--accent)',
        color: '#fff',
        flexShrink: 0,
      }}
    >
      {count > 9 ? '9+' : count} new
    </span>
  )
}

export function PRNode({ pr, showMeta, showTask, compact, onClick, onClickNumber, onRefresh }: Props) {
  useLiveClock()
  const prRepo = repoFromPrUrl(pr.url)
  const repoName = prRepo.split('/').pop() || ''
  const unread = pr.unread_comment_count || 0

  const numberLink = onClickNumber ? (
    <a
      href="#"
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        onClickNumber()
      }}
    >
      #{pr.number}
    </a>
  ) : (
    <a href={pr.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
      #{pr.number}
    </a>
  )

  const branchDisplay = pr.head_branch && pr.head_branch.length > 25
    ? pr.head_branch.substring(0, 25) + '..'
    : pr.head_branch

  return (
    <div data-testid="pr-overview">
      {compact ? (
        // Narrow column (e.g. 3-pane reviews queue at ~25%). A single
        // ellipsis line cuts the title off; instead lay the row out
        // VERTICALLY -- chip strip on row 1 (number, status, CI,
        // refresh), title on its own row 2 with `white-space:normal`
        // so it wraps to as many lines as it needs. The "node" is
        // taller, but every word of the title actually visible.
        <div
          className="task-card-pr"
          data-compact="true"
          style={{
            flexDirection: 'column',
            alignItems: 'stretch',
            gap: 4,
            padding: '6px 10px',
            cursor: onClick ? 'pointer' : undefined,
          }}
          onClick={onClick}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%' }}>
            <span className="pr-num">{numberLink}</span>
            <span className={`pr-label ${pr.status}`}>{pr.status}</span>
            {pr.ci_status && <CIRing status={pr.ci_status} />}
            <MyReviewPill state={pr.my_review_state} />
            <NewCommentsBadge count={unread} />
            {onRefresh && <span style={{ marginLeft: 'auto' }}><RefreshBtn onRefresh={onRefresh} /></span>}
          </div>
          <div
            data-testid="pr-title"
            style={{
              fontSize: 12, fontWeight: 500, lineHeight: 1.35,
              color: 'var(--text)', overflowWrap: 'anywhere',
            }}
          >
            {pr.title}
          </div>
        </div>
      ) : (
        <div
          className="task-card-pr"
          style={onClick ? { cursor: 'pointer' } : undefined}
          onClick={onClick}
        >
          <span className="pr-num">{numberLink}</span>
          <span className={`pr-label ${pr.status}`}>{pr.status}</span>
          {pr.ci_status && <CIRing status={pr.ci_status} />}
          {pr.review_status && <ReviewIcon status={pr.review_status} />}
          <MyReviewPill state={pr.my_review_state} />
          <NewCommentsBadge count={unread} />
          <span className="pr-title">{pr.title}</span>
          {onRefresh && (
            <RefreshBtn onRefresh={onRefresh} />
          )}
        </div>
      )}
      {showMeta !== false && (
        <div
          data-testid="pr-meta"
          className="pr-meta"
          style={{
            display: 'flex',
            gap: 8,
            fontSize: 10,
            color: 'var(--text-dim)',
            alignItems: 'center',
            padding: '0 10px 4px',
            flexWrap: 'wrap',
            minWidth: 0,
          }}
        >
          {showTask && pr.task_id && (
            <span style={{ color: 'var(--accent)' }}>{pr.task_id}</span>
          )}
          {repoName && (
            <span style={{ fontFamily: 'monospace' }}>{repoName}</span>
          )}
          {pr.comment_count > 0 && <span data-testid="comment-count">{pr.comment_count} comments</span>}
          {(pr.additions > 0 || pr.deletions > 0) && (
            <span data-testid="diff-stats" style={{ fontFamily: 'monospace' }}>
              <span style={{ color: 'var(--green)' }}>+{pr.additions}</span>{' '}
              <span style={{ color: 'var(--red)' }}>-{pr.deletions}</span>
            </span>
          )}
          {pr.head_branch && (
            <span style={{ fontFamily: 'monospace', color: 'var(--text-subtle)' }}>{branchDisplay}</span>
          )}
          {pr.last_updated && <span>{timeAgo(pr.last_updated)}</span>}
        </div>
      )}
    </div>
  )
}
