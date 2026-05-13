import { useState, useRef } from 'react'
import { useClickOutside } from '../../hooks/useClickOutside'
import { useSessionStatus } from '../../hooks/SessionStatusProvider'

/**
 * SessionIndicator -- top-bar widget showing the live agent session
 * fleet at a glance, similar in style to AuthStatus (the cert dots).
 *
 * All data + cache invalidation is owned by the SessionStatusProvider
 * mounted at the App root. This widget is purely presentational: it
 * reads counts from the service and renders the colored pills /
 * dropdown. Adding a new session source (eg ad-hoc terminals) is
 * one new field on the service, no churn here.
 *
 * Buckets (3-tier urgency). The single source of truth for the
 * state -> color mapping lives in `utils/sessionState.ts`
 * (sessionDotColor / sessionDotAnim / sessionDotHalo). The pills
 * here mirror the same palette by bucket so the indicator and
 * every per-session dot agree on what each color means:
 *   red    = needs attention   (needs_permission / crashed)
 *   yellow = ready for next    (idle / needs_input)
 *   blue   = in flight         (thinking / starting)
 *   grey   = stopped / other   (stopped / unknown)
 */
export function SessionIndicator() {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useClickOutside(ref, () => setOpen(false))

  const { counts } = useSessionStatus()

  // Auto-collapse to a single "0" pill when nothing is live -- avoids
  // rendering "0 0 0 0" eyesore in the header.
  const collapsed = counts.total === 0

  return (
    <div ref={ref}
         data-testid="session-indicator"
         style={{ position: 'relative', cursor: 'pointer' }}>
      <span
        onClick={() => setOpen(!open)}
        title={`${counts.total} live session${counts.total === 1 ? '' : 's'}`
          + ` (${counts.needs} need attention, ${counts.flight} in flight,`
          + ` ${counts.idle} ready for next)`}
        style={{
          fontSize: 10, display: 'flex', alignItems: 'center', gap: 6,
          fontFamily: 'monospace',
        }}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="2" strokeLinecap="round"
             strokeLinejoin="round"
             style={{ color: 'var(--text-dim)', flexShrink: 0 }}>
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d="M7 9l3 3-3 3" />
          <path d="M13 15h4" />
        </svg>
        {collapsed ? (
          <span data-testid="session-indicator-count"
                style={{ color: 'var(--text-dim)' }}>0</span>
        ) : (
          <>
            <CountPill testid="session-indicator-needs"
                       count={counts.needs} color="var(--red)" hidden={counts.needs === 0} />
            <CountPill testid="session-indicator-flight"
                       count={counts.flight} color="var(--blue)" hidden={counts.flight === 0} />
            <CountPill testid="session-indicator-idle"
                       count={counts.idle} color="var(--yellow)" hidden={counts.idle === 0} />
            <CountPill testid="session-indicator-other"
                       count={counts.other} color="var(--text-dim)" hidden={counts.other === 0} />
          </>
        )}
      </span>

      {open && (
        <div style={{
          position: 'absolute', right: 0, top: 32, background: 'var(--card-bg)',
          border: '1px solid var(--border)', borderRadius: 8, padding: '12px 16px',
          minWidth: 240, zIndex: 300, boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
        }}>
          <div style={{
            display: 'flex', justifyContent: 'space-between',
            alignItems: 'center', marginBottom: 8,
          }}>
            <span style={{ fontWeight: 600, color: 'var(--text)', fontSize: 12 }}>
              Live Sessions
            </span>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>
              {counts.total} total
            </span>
          </div>

          <div style={{
            display: 'flex', flexDirection: 'column', gap: 4,
            fontSize: 11, marginBottom: 8,
          }}>
            <Row dot="var(--red)"    label="Needs attention"  count={counts.needs}  />
            <Row dot="var(--yellow)" label="Ready for next"   count={counts.idle}   />
            <Row dot="var(--blue)"   label="In flight"        count={counts.flight} />
            <Row dot="var(--text-dim)" label="Stopped / other" count={counts.other} />
          </div>

          <div style={{
            borderTop: '1px solid var(--border)', paddingTop: 8,
            display: 'flex', flexDirection: 'column', gap: 3,
            fontSize: 10, color: 'var(--text-dim)',
          }}>
            <KindRow label="Tasks"    n={counts.byKind.task}   />
            <KindRow label="Cron"     n={counts.byKind.cron}   />
            <KindRow label="Reviews"  n={counts.byKind.review} />
            <KindRow label="Tickets"  n={counts.byKind.ticket} />
          </div>
        </div>
      )}
    </div>
  )
}


function CountPill({
  count, color, testid, hidden,
}: { count: number; color: string; testid: string; hidden: boolean }) {
  if (hidden) return null
  return (
    <span data-testid={testid} style={{
      display: 'inline-flex', alignItems: 'center', gap: 3,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: color, display: 'inline-block',
      }} />
      <span style={{ color, fontWeight: 600 }}>{count}</span>
    </span>
  )
}


function Row({ dot, label, count }: { dot: string; label: string; count: number }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{
          width: 6, height: 6, borderRadius: '50%',
          background: dot, display: 'inline-block', flexShrink: 0,
        }} />
        <span style={{ color: 'var(--text-dim)' }}>{label}</span>
      </span>
      <span style={{ color: dot, fontWeight: 600 }}>{count}</span>
    </div>
  )
}


function KindRow({ label, n }: { label: string; n: number }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
      <span>{label}</span>
      <span>{n}</span>
    </div>
  )
}
