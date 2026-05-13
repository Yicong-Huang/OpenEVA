import type { PRDetail } from '../../types'
import { ghAvatar } from '../../utils'

interface ReviewSectionProps {
  reviews: PRDetail['reviews']
  reviewDecision: string
}

export function ReviewSection({ reviews, reviewDecision }: ReviewSectionProps) {
  // Deduplicate by login (last review wins)
  const reviewMap: Record<string, PRDetail['reviews'][number]> = {}
  for (const rv of reviews || []) {
    const rvLogin = rv.author?.login || 'unknown'
    reviewMap[rvLogin] = rv
  }
  const reviewLogins = Object.keys(reviewMap)

  if (reviewLogins.length === 0 && !reviewDecision) return null

  const rdColor = reviewDecision === 'APPROVED'
    ? 'var(--green)'
    : reviewDecision === 'CHANGES_REQUESTED'
      ? 'var(--red)'
      : 'var(--yellow)'

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      {reviewLogins.map((rLogin) => {
        const rState = reviewMap[rLogin].state
        const rBorder = rState === 'APPROVED'
          ? 'var(--green)'
          : rState === 'CHANGES_REQUESTED'
            ? 'var(--red)'
            : 'var(--border)'
        return (
          <img
            key={rLogin}
            src={ghAvatar(rLogin)}
            title={`${rLogin}: ${rState}`}
            alt={rLogin}
            style={{ width: 20, height: 20, borderRadius: '50%', border: `2px solid ${rBorder}` }}
          />
        )
      })}
      {reviewDecision && (
        <span style={{ fontWeight: 600, color: rdColor }}>{reviewDecision}</span>
      )}
    </div>
  )
}
