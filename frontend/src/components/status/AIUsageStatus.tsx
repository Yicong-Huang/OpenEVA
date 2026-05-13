import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../../api'
import { useClickOutside } from '../../hooks/useClickOutside'
import { timeAgo } from '../../utils'
import { useLiveClock } from '../../hooks/useLiveClock'
import { useEventBus } from '../../hooks/useEventBus'

interface UsageData {
  daily: string
  weekly: string
  monthly: string
  tier?: string
  updated?: string
}

/** Animated number that rolls up/down when value changes. */
function AnimatedValue({ value, style }: { value: string; style?: React.CSSProperties }) {
  const [display, setDisplay] = useState(value)
  const rafRef = useRef(0)

  useEffect(() => {
    const prev = parseFloat(display.replace(/,/g, ''))
    const next = parseFloat(value.replace(/,/g, ''))
    if (isNaN(prev) || isNaN(next) || prev === next) {
      setDisplay(value)
      return
    }
    const start = performance.now()
    const duration = 600
    const fmt = (n: number) => n.toLocaleString('en-US', {
      minimumFractionDigits: value.includes('.') ? (value.split('.')[1]?.length || 2) : 0,
      maximumFractionDigits: value.includes('.') ? (value.split('.')[1]?.length || 2) : 0,
    })
    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1)
      // ease-out cubic
      const ease = 1 - Math.pow(1 - t, 3)
      const cur = prev + (next - prev) * ease
      setDisplay(fmt(cur))
      if (t < 1) rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  return <span style={style}>{display}</span>
}

export function AIUsageStatus() {
  const [open, setOpen] = useState(false)
  const [usage, setUsage] = useState<UsageData | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  useClickOutside(ref, () => setOpen(false))
  // Keep timeAgo display fresh
  useLiveClock(30_000)

  const load = useCallback(async () => {
    try {
      const data = (await api.getUsage()) as Record<string, unknown>
      setUsage({
        daily: String(data.daily || '--'),
        weekly: String(data.weekly || '--'),
        monthly: String(data.monthly || '--'),
        tier: data.tier as string | undefined,
        updated: (data.updated_at || data.updated) as string | undefined,
      })
    } catch {
      setUsage(null)
    }
  }, [])

  // Initial load (so the UI has a value to show before the first
  // scheduler tick has pushed an update).
  useEffect(() => {
    load()
  }, [load])

  // Push-based refresh: backend scheduler fetches fresh usage every
  // USAGE_REFRESH_INTERVAL_SECONDS (~120s) and emits `usage.updated`.
  // We just re-read /api/usage (now a pure cache read) whenever we
  // see that event.
  useEventBus('usage.updated', load)

  const dailyLabel = usage?.daily || '--'

  return (
    <div ref={ref} style={{ position: 'relative', cursor: 'pointer' }} data-testid="usage-topbar">
      <span
        style={{ fontSize: 11, color: 'var(--text-dim)', fontFamily: 'monospace', display: 'flex', alignItems: 'center', gap: 5 }}
        onClick={() => { if (!open) load(); setOpen(!open) }}
      >
        <AnimatedValue value={dailyLabel} style={{ fontWeight: 600, color: 'var(--text)' }} />
        {usage?.updated && (
          <span style={{ fontSize: 9, color: 'var(--text-faint)' }}>{timeAgo(usage.updated)}</span>
        )}
      </span>
      {open && (
        <div style={{
          position: 'absolute', right: 0, top: 32, background: 'var(--card-bg)',
          border: '1px solid var(--border)', borderRadius: 8, padding: '12px 16px',
          minWidth: 180, zIndex: 300, boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontWeight: 600, color: 'var(--text)', fontSize: 12 }}>AI Usage</span>
            <button className="btn-action" style={{ padding: '1px 6px', fontSize: 9 }}
              onClick={(e) => { e.stopPropagation(); load() }}>&#8635;</button>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--text-dim)' }}>Daily</span>
              <AnimatedValue value={usage?.daily || '--'} style={{ fontWeight: 600, fontFamily: 'monospace', color: 'var(--text)' }} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--text-dim)' }}>Weekly</span>
              <AnimatedValue value={usage?.weekly || '--'} style={{ fontWeight: 600, fontFamily: 'monospace', color: 'var(--text)' }} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--text-dim)' }}>Monthly</span>
              <AnimatedValue value={usage?.monthly || '--'} style={{ fontWeight: 600, fontFamily: 'monospace', color: 'var(--text)' }} />
            </div>
          </div>
          {usage?.tier && (
            <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', fontSize: 10 }}>
              <span style={{ color: 'var(--text-dim)' }}>Tier</span>
              <span style={{ fontWeight: 600, color: 'var(--accent)' }}>{usage.tier}</span>
            </div>
          )}
          {usage?.updated && (
            <div style={{ marginTop: 4, fontSize: 9, color: 'var(--text-faint)', textAlign: 'right' }}>
              Updated: {timeAgo(usage.updated)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
