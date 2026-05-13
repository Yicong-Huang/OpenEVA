import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../../api'
import { useClickOutside } from '../../hooks/useClickOutside'
import { useEventBus } from '../../hooks/useEventBus'
import { useAlert } from '../Alert'

interface CertEntry {
  // Route-side identifier (e.g. `oauth_provider`). MUST be used
  // when calling `/api/certs/renew/<id>` -- the route does exact
  // equality on `provider.key`, NOT on the display name.
  key: string
  name: string
  ok: boolean
  status?: string
  remaining_seconds?: number
  expires?: string
  note?: string
  label?: string
}

export function AuthStatus() {
  const [open, setOpen] = useState(false)
  const [certs, setCerts] = useState<CertEntry[]>([])
  const [renewing, setRenewing] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  useClickOutside(ref, () => setOpen(false))
  const { alert } = useAlert()

  const load = useCallback(async () => {
    try {
      const data = (await api.getCerts()) as Record<string, unknown>
      // API may return {certs: [...]} or an object keyed by cert name
      let entries: CertEntry[] = []
      if (Array.isArray(data.certs)) {
        entries = data.certs as CertEntry[]
      } else {
        // Object format: each outer dict-key is the route-side
        // provider id (e.g. `ssh_cert`, `oauth_provider`). The inner
        // value carries `name` (display string), `key` (route id --
        // also matches the outer dict-key), `status`, `remaining_seconds`,
        // optional `note`. We prefer `v.key` but fall back to the outer
        // dict-key for back-compat with older API responses that didn't
        // include `key` in the payload.
        for (const [outerKey, val] of Object.entries(data)) {
          if (val && typeof val === 'object') {
            const v = val as Record<string, unknown>
            const status = String(v.status || 'unknown')
            const remaining = typeof v.remaining_seconds === 'number' ? v.remaining_seconds : undefined
            const isOk = status === 'ok' || status === 'valid'
            const isWarning = status === 'warning'
            entries.push({
              key: String(v.key || outerKey),
              name: String(v.name || outerKey),
              ok: isOk,
              status: isWarning ? 'warning' : status,
              remaining_seconds: remaining,
              note: v.note ? String(v.note) : undefined,
              label: v.label ? String(v.label) : undefined,
            })
          }
        }
      }
      setCerts(entries)
    } catch {
      setCerts([])
    }
  }, [])

  // Fetch once on mount
  useEffect(() => {
    load()
  }, [load])

  // Client-side countdown: decrement remaining_seconds every 60s
  useEffect(() => {
    if (certs.length === 0) return
    const timer = setInterval(() => {
      setCerts(prev => prev.map(c =>
        typeof c.remaining_seconds === 'number' && c.remaining_seconds > 0
          ? { ...c, remaining_seconds: c.remaining_seconds - 60 }
          : c
      ))
    }, 60000)
    return () => clearInterval(timer)
  }, [certs.length])

  // Re-fetch when auth cert events arrive via SSE
  useEventBus('auth.*', useCallback(() => { load() }, [load]))

  const handleRenew = useCallback(async (certId: string) => {
    setRenewing(certId)
    try {
      // The backend returns {ok, output} with HTTP 200 even on failure
      // -- the provider can include actionable hints in `output`.
      // Without surfacing it, users see the click do "nothing" and
      // assume Eva is broken. Show the hint as an alert.
      const result = await api.renewCert(certId)
      if (result && result.ok === false) {
        await alert({
          title: `Could not renew "${certId}"`,
          message: result.output || 'Renew failed (no detail).',
          kind: 'warning',
        })
      }
      await load()
    } catch (e) {
      await alert({
        title: `Renew failed for "${certId}"`,
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setRenewing(null)
    }
  }, [load, alert])

  const inlineDots = certs.map((c) => {
    const color = c.status === 'warning' ? 'var(--yellow)' : c.ok ? 'var(--green)' : 'var(--red)'
    const label = c.ok ? 'OK' : c.status === 'warning' ? 'warning' : 'expired'
    return (
      <span
        key={c.name}
        style={{ width: 6, height: 6, borderRadius: '50%', background: color, display: 'inline-block' }}
        title={`${c.name}: ${label}`}
      />
    )
  })

  return (
    <div ref={ref} style={{ position: 'relative', cursor: 'pointer' }} data-testid="certs-topbar">
      <span
        style={{ fontSize: 10, display: 'flex', alignItems: 'center', gap: 4, fontFamily: 'monospace' }}
        onClick={() => setOpen(!open)}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          style={{ color: 'var(--text-dim)', flexShrink: 0 }}>
          <circle cx="8" cy="15" r="5" />
          <path d="M11.5 11.5L17 6" />
          <path d="M15 8l2-2 2 2" />
        </svg>
        {inlineDots}
      </span>
      {open && (
        <div style={{
          position: 'absolute', right: 0, top: 32, background: 'var(--card-bg)',
          border: '1px solid var(--border)', borderRadius: 8, padding: '12px 16px',
          minWidth: 200, zIndex: 300, boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontWeight: 600, color: 'var(--text)', fontSize: 12 }}>Auth Status</span>
            <button className="btn-action" style={{ padding: '1px 6px', fontSize: 9 }}
              onClick={(e) => { e.stopPropagation(); load() }}>&#8635;</button>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11 }}>
            {certs.map((c) => {
              const status = c.status || (c.ok ? 'ok' : 'expired')
              const canRenew = status === 'expired' || status === 'warning'
              const dotColor = status === 'warning' ? 'var(--yellow)' : c.ok ? 'var(--green)' : 'var(--red)'
              const statusLabel = c.ok ? 'OK' : status === 'warning' ? 'Warning' : 'Expired'
              // Human-readable time remaining
              let timeStr = ''
              if (typeof c.remaining_seconds === 'number' && c.remaining_seconds > 0) {
                const s = c.remaining_seconds
                if (s >= 86400) timeStr = Math.floor(s / 86400) + 'd'
                else if (s >= 3600) timeStr = Math.floor(s / 3600) + 'h'
                else if (s >= 60) timeStr = Math.floor(s / 60) + 'm'
                else timeStr = s + 's'
              }
              return (
                <div key={c.name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: dotColor, display: 'inline-block', flexShrink: 0 }} />
                    <span style={{ color: 'var(--text-dim)' }}>{c.label || c.name}</span>
                  </span>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    {timeStr && <span style={{ fontSize: 9, color: 'var(--text-dim)', fontFamily: 'monospace' }}>{timeStr}</span>}
                    <span style={{ color: dotColor, fontWeight: 600 }}>
                      {statusLabel}
                    </span>
                    <button
                      className="btn-action"
                      data-testid={`renew-${c.name}`}
                      style={{ padding: '0 4px', fontSize: 8, marginLeft: 4, opacity: canRenew ? 1 : 0.4 }}
                      disabled={renewing === c.name}
                      title={canRenew ? 'Renew' : 'Force refresh'}
                      onClick={(e) => { e.stopPropagation(); handleRenew(c.key) }}
                    >
                      {renewing === c.name ? '...' : <span>&#8635;</span>}
                    </button>
                  </span>
                </div>
              )
            })}
            {certs.length === 0 && (
              <span style={{ color: 'var(--text-dim)' }}>No certs loaded</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
