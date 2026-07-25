import { SessionDot } from './SessionDot'

/**
 * LiveSessionChip -- compact, kind-tagged chip for the All Live Tasks
 * page when the underlying entity is *not* a project task (which uses
 * `TaskNodeChip`). Today that's cron jobs and review PRs.
 *
 * Both share the same visual: a tiny [KIND] tag on the left, the
 * label, and a status dot driven by the live eva-hook state. We
 * keep everything fixed-size so a row of mixed chips looks aligned.
 *
 * Click delegates to the parent so the page can route to the
 * appropriate detail view (CronJobsPage with the job selected, or
 * ReviewsPage with the review URL selected).
 */
export interface LiveSessionChipProps {
  kind: 'cron' | 'review' | 'manager'
  label: string
  /** Optional secondary line, e.g. PR title for reviews. Truncated. */
  sublabel?: string
  /** Live agent status: 'idle' | 'thinking' | 'needs_input' |
   *  'needs_permission' | 'starting' | 'stopped' | ''. */
  status: string
  selected?: boolean
  dimmed?: boolean
  onClick?: () => void
}

const KIND_BADGE = {
  cron: { label: 'CRON', bg: 'rgba(20,184,166,0.18)', fg: 'var(--teal, #14b8a6)' },
  review: { label: 'REVIEW', bg: 'rgba(168,85,247,0.18)', fg: 'var(--purple)' },
  manager: { label: 'PM', bg: 'rgba(59,130,246,0.18)', fg: 'var(--blue)' },
} as const

export function LiveSessionChip({
  kind, label, sublabel, status, selected, dimmed, onClick,
}: LiveSessionChipProps) {
  const badge = KIND_BADGE[kind]
  return (
    <div
      data-testid={`live-chip-${kind}-${label}`}
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 6,
        border: `1px solid ${selected ? 'var(--accent)' : 'var(--border)'}`,
        outline: selected ? '1px solid var(--accent)' : undefined,
        borderRadius: 6, padding: '4px 8px',
        cursor: 'pointer', background: 'var(--card-bg)',
        minWidth: 220, maxWidth: 240,
        opacity: dimmed ? 0.4 : 1,
        transition: 'opacity 0.15s',
      }}
    >
      <span style={{
        fontSize: 9, padding: '0 5px', borderRadius: 3,
        background: badge.bg, color: badge.fg,
        fontWeight: 700, letterSpacing: 0.3,
        whiteSpace: 'nowrap',
      }}>{badge.label}</span>
      <div style={{
        flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column',
        gap: 1,
      }}>
        <span style={{
          fontSize: 11, fontWeight: 600, color: 'var(--text)',
          overflow: 'hidden', textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}>{label}</span>
        {sublabel && (
          <span style={{
            fontSize: 10, color: 'var(--text-dim)',
            overflow: 'hidden', textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>{sublabel}</span>
        )}
      </div>
      <SessionDot state={status} />
      <span style={{
        fontSize: 9, color: 'var(--text-dim)', whiteSpace: 'nowrap',
      }}>{status || 'unknown'}</span>
    </div>
  )
}
