import { useCallback } from 'react'
import { PRNode } from './PRNode'
import type { PR } from '../types'

/**
 * ReviewNode -- a single row in the All Reviews queue (left pane).
 *
 * Naming: in the reviews UI we use "node / card / detail" by position
 * (left / middle / right) rather than by the underlying React file
 * names. This component IS the PR node, even though it composes
 * `PRNode` for the inner chip strip + title rendering.
 *
 * Responsibilities specific to the reviews queue (not in PRNode):
 *   * Folded merged PRs dim to ~55% opacity (hover lifts to ~90%) so
 *     reference rows fade out without disappearing.
 *   * Selected row gets the accent outline.
 *   * Manual-pinned rows expose an Unpin button that opens a
 *     pop-confirm bubble at the click position (not a centred modal).
 *   * Compact mode (`isCompact`) flips PRNode into its 2-row layout
 *     for the 25% queue width in 3-pane mode, where a single-line
 *     ellipsis-truncated title would hide the actual PR contents.
 */

interface Props {
  pr: PR & { source?: string; repo: string }
  isManual: boolean
  isSelected: boolean
  /** Merged PRs the user hasn't reviewed yet -- fold to dim/no-meta */
  isFolded: boolean
  /** True in 3-pane layout so PRNode switches to 2-row compact form */
  isCompact: boolean
  onSelect: () => void
  /** Anchor carries click position so the unpin pop-confirm bubble
   *  appears at the cursor instead of centring a modal. */
  onUnpin: (anchor: { x: number; y: number }) => void
}

export function ReviewNode({
  pr, isManual, isSelected, isFolded, isCompact, onSelect, onUnpin,
}: Props) {
  const handleHoverIn = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (isFolded) e.currentTarget.style.opacity = '0.9'
  }, [isFolded])
  const handleHoverOut = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (isFolded) e.currentTarget.style.opacity = '0.55'
  }, [isFolded])

  return (
    <div
      data-testid={isManual ? 'review-row-pinned' : 'review-row'}
      data-folded={isFolded ? 'true' : undefined}
      style={{
        display: 'flex',
        alignItems: 'stretch',
        gap: 4,
        outline: isSelected ? '2px solid var(--accent)' : undefined,
        borderRadius: 6,
        marginBottom: 4,
        minWidth: 0,
        opacity: isFolded ? 0.55 : 1,
        transition: 'opacity 0.15s ease',
      }}
      onMouseEnter={handleHoverIn}
      onMouseLeave={handleHoverOut}
    >
      {/* min-width:0 + overflow:hidden lets PRNode's internal ellipsis
          (or 2-row compact wrap) take effect instead of pushing the
          Unpin button off the row. */}
      <div style={{ flex: 1, minWidth: 0, overflow: 'hidden' }}>
        <PRNode
          pr={pr}
          showMeta={!isCompact && !isFolded}
          compact={isCompact}
          onClick={onSelect}
        />
      </div>
      {isManual && (
        <button
          className="btn-action"
          data-testid="review-unpin-btn"
          style={{
            flexShrink: 0,
            alignSelf: 'flex-start',
            marginTop: 6, marginRight: 4,
            fontSize: 9, padding: '1px 6px', color: 'var(--red)',
            background: 'transparent', border: '1px solid var(--border)',
            whiteSpace: 'nowrap',
          }}
          title={
            pr.source === 'both'
              ? 'Remove the manual pin (PR stays in the queue via GitHub)'
              : 'Remove from review list'
          }
          onClick={(e) => {
            e.stopPropagation()
            onUnpin({ x: e.clientX, y: e.clientY })
          }}
        >
          Unpin
        </button>
      )}
    </div>
  )
}
