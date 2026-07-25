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
  // Real period spend / token totals from newer usage output.
  claudeCost?: string
  claudeTokens?: string
  codexCost?: string
  codexTokens?: string
  // Account-wide monthly total (all tools) + budget cap from the
  // AI Gateway Budgets block, and the Claude-Code-only monthly slice.
  monthlyTotal?: string
  monthlyBudget?: string
  claudeMonthly?: string
}

/** Compact a comma-separated integer token count as e.g. "1.4B" / "647M". */
function fmtTokens(raw?: string): string | undefined {
  if (!raw) return undefined
  const n = parseInt(raw.replace(/,/g, ''), 10)
  if (isNaN(n)) return raw
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return String(n)
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
        claudeCost: data.claude_cost as string | undefined,
        claudeTokens: data.claude_tokens as string | undefined,
        codexCost: data.codex_cost as string | undefined,
        codexTokens: data.codex_tokens as string | undefined,
        monthlyTotal: data.monthly_total as string | undefined,
        monthlyBudget: data.monthly_budget as string | undefined,
        claudeMonthly: data.claude_monthly as string | undefined,
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
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ color: 'var(--text-dim)' }}>Monthly</span>
              <span style={{ fontFamily: 'monospace' }}>
                <AnimatedValue value={usage?.monthly || '--'} style={{ fontWeight: 600, color: 'var(--text)' }} />
                {usage?.monthlyBudget && (
                  <span style={{ color: 'var(--text-faint)' }}> / {usage.monthlyBudget}</span>
                )}
              </span>
            </div>
          </div>
          {usage?.tier && (
            <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', fontSize: 10 }}>
              <span style={{ color: 'var(--text-dim)' }}>Tier</span>
              <span style={{ fontWeight: 600, color: 'var(--accent)' }}>{usage.tier}</span>
            </div>
          )}
          {usage?.claudeMonthly && usage?.monthlyTotal && (
            <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11 }}>
              <div style={{ color: 'var(--text-faint)', fontSize: 9, textTransform: 'uppercase', letterSpacing: 0.4 }}>Monthly breakdown</div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-dim)' }}>Claude Code</span>
                <span style={{ fontFamily: 'monospace', fontWeight: 600, color: 'var(--text)' }}>${usage.claudeMonthly}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-dim)' }}>Other (gateway)</span>
                <span style={{ fontFamily: 'monospace', color: 'var(--text-faint)' }}>
                  ${(() => {
                    const total = parseFloat((usage.monthlyTotal || '0').replace(/,/g, ''))
                    const claude = parseFloat((usage.claudeMonthly || '0').replace(/,/g, ''))
                    const diff = total - claude
                    return isNaN(diff) ? '--' : diff.toLocaleString('en-US', { maximumFractionDigits: 2 })
                  })()}
                </span>
              </div>
            </div>
          )}
          {(usage?.claudeCost || usage?.codexCost) && (
            <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11 }}>
              <div style={{ color: 'var(--text-faint)', fontSize: 9, textTransform: 'uppercase', letterSpacing: 0.4 }}>Spend (recent)</div>
              {usage?.claudeCost && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-dim)' }}>Claude Code</span>
                  <span style={{ fontFamily: 'monospace', color: 'var(--text)' }}>
                    <span style={{ fontWeight: 600 }}>${usage.claudeCost}</span>
                    {fmtTokens(usage.claudeTokens) && (
                      <span style={{ color: 'var(--text-faint)', marginLeft: 6 }}>{fmtTokens(usage.claudeTokens)} tok</span>
                    )}
                  </span>
                </div>
              )}
              {usage?.codexCost && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-dim)' }}>Codex</span>
                  <span style={{ fontFamily: 'monospace', color: 'var(--text)' }}>
                    <span style={{ fontWeight: 600 }}>${usage.codexCost}</span>
                    {fmtTokens(usage.codexTokens) && (
                      <span style={{ color: 'var(--text-faint)', marginLeft: 6 }}>{fmtTokens(usage.codexTokens)} tok</span>
                    )}
                  </span>
                </div>
              )}
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
