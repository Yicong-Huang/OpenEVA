import { useEffect, useState } from 'react'
import { api } from '../api'

type Check = {
  id: string
  label: string
  ok: boolean
  detail: string
  hint: string
}

export function SetupBanner() {
  const [data, setData] = useState<{ all_ok: boolean; checks: Check[] } | null>(null)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    let cancelled = false
    api.getSetupStatus().then((r) => {
      if (!cancelled && r && Array.isArray(r.checks)) setData(r)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  if (!data || data.all_ok || dismissed) return null

  const failing = data.checks.filter((c) => !c.ok)
  if (failing.length === 0) return null
  const open = () => {
    window.dispatchEvent(new CustomEvent('eva-open-settings', {
      detail: { tab: 'setup' },
    }))
  }

  return (
    <div
      data-testid="setup-banner"
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '6px 14px',
        background: 'var(--orange, #d6a300)',
        color: '#000',
        fontSize: 12,
        borderBottom: '1px solid var(--border)',
      }}
    >
      <span style={{ fontWeight: 700 }}>Setup incomplete:</span>
      <span style={{ flex: 1 }}>
        {failing.length} check(s) need attention --{' '}
        {failing.map((c) => c.label).join('; ')}
      </span>
      <button
        className="btn-action"
        onClick={open}
        data-testid="setup-banner-open"
        style={{ fontSize: 11 }}
      >Open Setup</button>
      <button
        onClick={() => setDismissed(true)}
        title="Dismiss for this session"
        style={{
          background: 'transparent', border: 'none', cursor: 'pointer',
          color: '#000', fontSize: 14, padding: '0 4px',
        }}
      >x</button>
    </div>
  )
}
