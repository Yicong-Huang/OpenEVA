import { sessionDotColor, sessionDotAnim, sessionDotHalo } from '../utils/sessionState'

/**
 * SessionDot -- the colored dot that represents a live session's
 * state. Rendered identically wherever Eva shows session status:
 * GraphView task nodes, TaskNodeChip footer, SessionCard / ReviewCard
 * / ProjectSessionCard headers, LiveSessionChip pills, CronJobsPage
 * rows.
 *
 * Color / animation / halo all come from one source of truth in
 * `utils/sessionState.ts`. Don't hand-roll a colored span elsewhere
 * for session state -- use this component (or, for an aggregated
 * count, the corresponding CSS variable) so the 3-tier urgency model
 * stays consistent.
 *
 * Sized via the `size` prop (default 8px) since the chip / card / row
 * contexts each prefer slightly different visual weight. The dot
 * shape is always a perfect circle.
 */
export interface SessionDotProps {
  /** Canonical session state from the backend hook rules. */
  state: string | null | undefined
  /** Pixel diameter (default 8). */
  size?: number
  style?: React.CSSProperties
  /** Optional testid override. Defaults to `session-status-dot`. */
  testid?: string
}

export function SessionDot({ state, size = 8, style, testid = 'session-status-dot' }: SessionDotProps) {
  const color = sessionDotColor(state)
  const halo = sessionDotHalo(state)
  return (
    <span
      data-testid={testid}
      data-status={state || ''}
      className={sessionDotAnim(state)}
      style={{
        width: size, height: size, borderRadius: '50%',
        background: color,
        boxShadow: halo ? `0 0 4px ${color}` : undefined,
        display: 'inline-block', flexShrink: 0,
        ...style,
      }}
    />
  )
}
