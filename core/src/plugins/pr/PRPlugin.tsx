import { useState, useEffect, useCallback } from 'react'
import { api } from '@app/api'
import { usePluginCollapse } from '@app/hooks/usePluginCollapse'
import { useEventBus } from '@app/hooks/useEventBus'
import { usePluginEnabled } from '@app/hooks/usePluginsEnabled'
import { miniPluginStyle } from '@app/utils/pluginMini'

interface Quarter {
  period: string
  total: number
  by_repo: Record<string, number>
}

interface PRPluginData {
  openPrs: Record<string, number>
  total: number
  quarters: Quarter[]
  weekly: number[]
  weeklyPrimary: number[]
  contributorRank: number | null
  contributorContributions: string
  contributorRepo: string
}

// Repo color: stable HSL hash of the name so every repo gets a
// distinct hue. Users can override per-repo via the
// `ui.repo_color_overrides` setting (JSON map of `{repo: cssColor}`)
// when they want a specific palette for the repos they look at most.
const REPO_COLORS: Record<string, string> = {}
function repoColor(name: string): string {
  const fixed = REPO_COLORS[name]
  if (fixed) return fixed
  let h = 0
  for (let i = 0; i < name.length; i++) {
    h = (h * 31 + name.charCodeAt(i)) | 0
  }
  const hue = Math.abs(h) % 360
  return `hsl(${hue}, 70%, 58%)`
}

function buildTrendlineSvg(weekly: number[], weeklyPrimary: number[], primaryColor: string): string | null {
  if (weekly.length === 0) return null
  // Both series share the same Y axis so the overlay line is visually
  // comparable to (and never taller than) the total line -- the max of
  // total is also the max of the whole chart by definition.
  const maxW = Math.max(...weekly) || 1
  const svgH = 28
  const svgW = 200
  const padY = 2
  const stepX = svgW / Math.max(weekly.length - 1, 1)
  const projectY = (v: number) =>
    Math.round(svgH - padY - (v / maxW) * (svgH - padY * 2))
  const toPoints = (series: number[]) => series.map((v, i) => {
    const px = Math.round(i * stepX)
    return `${px},${projectY(v)}`
  }).join(' ')
  const totalPts = toPoints(weekly)
  // Align primary series to the same length -- pad with zeros at the head
  // if the backend returns a shorter array (shouldn't happen, but defend).
  const alignedPrimary = weeklyPrimary.length === weekly.length
    ? weeklyPrimary
    : [...Array(Math.max(0, weekly.length - weeklyPrimary.length)).fill(0), ...weeklyPrimary]
  const hasPrimary = alignedPrimary.some((v) => v > 0)
  const primaryPts = toPoints(alignedPrimary)
  const lastIdx = weekly.length - 1
  const lastX = Math.round(lastIdx * stepX)
  const lastTotalY = projectY(weekly[lastIdx])
  const lastPrimaryY = projectY(alignedPrimary[lastIdx] ?? 0)
  return (
    `<svg width="100%" height="${svgH}" viewBox="0 0 ${svgW} ${svgH}" preserveAspectRatio="none" style="display:block">` +
    `<polygon points="0,${svgH} ${totalPts} ${svgW},${svgH}" fill="rgba(34,197,94,0.1)"/>` +
    `<polyline points="${totalPts}" fill="none" stroke="var(--green)" stroke-width="1.5" stroke-linejoin="round"/>` +
    (hasPrimary
      ? `<polyline points="${primaryPts}" fill="none" stroke="${primaryColor}" stroke-width="1.2" stroke-linejoin="round" stroke-dasharray="0"/>` +
        `<circle cx="${lastX}" cy="${lastPrimaryY}" r="2" fill="${primaryColor}"/>`
      : '') +
    `<circle cx="${lastX}" cy="${lastTotalY}" r="2.5" fill="var(--green)"/>` +
    `</svg>`
  )
}

// localStorage key for per-browser visibility of the "Open" row.
// Backend doesn't know about this -- the toggle is purely a UI knob.
const HIDE_OPEN_KEY = 'pr-plugin.hide-open'

function loadHideOpen(): boolean {
  try { return localStorage.getItem(HIDE_OPEN_KEY) === '1' } catch { return false }
}

export function PRPlugin() {
  const enabled = usePluginEnabled('pr')
  const [data, setData] = useState<PRPluginData | null>(null)
  const [error, setError] = useState(false)
  const { collapsed, toggle } = usePluginCollapse()
  const [hideOpen, setHideOpen] = useState(loadHideOpen)

  const toggleHideOpen = useCallback(() => {
    setHideOpen((prev) => {
      const next = !prev
      try { localStorage.setItem(HIDE_OPEN_KEY, next ? '1' : '0') } catch { /* ignore */ }
      return next
    })
  }, [])

  const load = useCallback(async (refresh = false) => {
    try {
      const [live, ws] = await Promise.all([api.getLiveStats(refresh), api.getWorkstats()])
      const liveAny = live as Record<string, unknown>
      const wsAny = ws as Record<string, unknown>
      const openPrs = (liveAny.open_prs as Record<string, number>) || {}
      const quarters = ((wsAny.quarters as Quarter[]) || []).slice().reverse()
      const weekly = (wsAny.weekly as number[]) || []
      const weeklyPrimary = (wsAny.weekly_primary as number[]) || []
      setData({
        openPrs,
        total: openPrs.total || 0,
        quarters,
        weekly,
        weeklyPrimary,
        contributorRank: (liveAny.contributor_rank as number) || null,
        contributorContributions: (liveAny.contributor_contributions as string) || '',
        contributorRepo: (liveAny.contributor_repo as string) || '',
      })
      setError(false)
    } catch {
      setError(true)
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    load()
  }, [enabled, load])

  useEventBus('github.*', useCallback(() => {
    if (!enabled) return
    load()
  }, [enabled, load]))

  if (!enabled) return null
  if (error) {
    return (
      <div style={{  fontSize: 10 }}>
        <span style={{ color: 'var(--red)' }}>PR stats failed</span>
      </div>
    )
  }
  if (!data) {
    return (
      <div id="pr-bar" style={{  fontSize: 10 }}>
        <span style={{ color: 'var(--text-dim)' }}>Loading...</span>
      </div>
    )
  }

  const {
    openPrs, total, quarters, weekly, weeklyPrimary,
    contributorRank, contributorContributions, contributorRepo,
  } = data
  const repos = Object.keys(openPrs).filter((k) => k !== 'total')
  const maxTotal = Math.max(...quarters.map((q) => q.total), 1)
  const mergedTotal = quarters.reduce((sum, q) => sum + q.total, 0)
  const contributorShort = contributorRepo.split('/').pop() || ''
  const primaryColor = repoColor(contributorShort || 'widgets')
  const trendlineSvg = buildTrendlineSvg(weekly, weeklyPrimary, primaryColor)
  const hasPrimaryOverlay = weeklyPrimary.some((v) => v > 0)
  return (
    <div data-testid="pr-bar" style={{  fontSize: 10, flexShrink: 0 }}>
      <div
        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, cursor: 'pointer' }}
        onClick={toggle}
      >
        <span style={{ fontWeight: 600, color: 'var(--text)' }}>PRs</span>
        <button className="btn-action" style={{ padding: '1px 6px', fontSize: 9 }} onClick={(e) => { e.stopPropagation(); load(true) }}>&#8635;</button>
      </div>
      {!collapsed && <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 }}>
        <span
          className="usage-label"
          onClick={toggleHideOpen}
          title={hideOpen ? 'Show open PR counts' : 'Hide open PR counts (per browser, localStorage)'}
          data-testid="pr-plugin-toggle-open"
          style={{ cursor: 'pointer', userSelect: 'none' }}
        >
          Open
        </span>
        {hideOpen ? (
          <span style={{ fontSize: 9, color: 'var(--text-dim)', fontStyle: 'italic' }}>
            hidden
          </span>
        ) : (
          <span style={{ fontSize: 10, fontFamily: 'monospace', display: 'flex', gap: 6 }}>
            {repos.map((r) =>
              (openPrs[r] || 0) > 0 && (
                <span key={r} style={{ color: repoColor(r) }}>{r}:{openPrs[r]}</span>
              ),
            )}
            <span style={{ color: 'var(--text)' }}><b>{total}</b></span>
          </span>
        )}
      </div>
      {quarters.map((q) => {
        const entries = Object.entries(q.by_repo || {}).filter(([, n]) => n > 0)
        return (
          <div key={q.period}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 1 }}>
              <span style={{ color: 'var(--text-dim)' }}>{q.period}</span>
              <span style={{ fontFamily: 'monospace', color: 'var(--text)' }}>{q.total}</span>
            </div>
            <div style={{ marginBottom: 3, height: 5, width: '100%', fontSize: 0, lineHeight: 0, whiteSpace: 'nowrap' }}>
              {entries.map(([repo, n]) => (
                <span
                  key={repo}
                  style={{
                    display: 'inline-block',
                    width: `${((n / maxTotal) * 100).toFixed(3)}%`,
                    height: '100%',
                    background: repoColor(repo),
                    verticalAlign: 'top',
                  }}
                  title={`${repo}: ${n}`}
                />
              ))}
            </div>
          </div>
        )
      })}
      {quarters.length > 0 && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid var(--border)', paddingTop: 2, marginTop: 2 }}>
          <span style={{ color: 'var(--text-dim)', fontSize: 10 }}>Merged</span>
          <span style={{ fontFamily: 'monospace', color: 'var(--green)', fontWeight: 600 }}>{mergedTotal}</span>
        </div>
      )}
      {trendlineSvg && (
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 4, marginTop: 4 }}>
          <div dangerouslySetInnerHTML={{ __html: trendlineSvg }} />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: 'var(--text-faint)', marginTop: 2 }}>
            <span style={{ color: 'var(--green)' }}>total</span>
            {hasPrimaryOverlay && contributorShort && (
              <span style={{ color: primaryColor }}>{contributorShort}</span>
            )}
          </div>
        </div>
      )}
      {contributorRank && contributorRepo && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 4 }}>
          <a
            href={`https://github.com/${contributorRepo}/graphs/contributors`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: 'var(--text-dim)', fontSize: 10, textDecoration: 'none' }}
          >
            {contributorShort} <span style={{ color: 'var(--accent)', fontWeight: 600 }}>#{contributorRank}</span>
          </a>
          {contributorContributions && (
            <span style={{ fontFamily: 'monospace', fontSize: 10, color: 'var(--text-dim)' }}>{contributorContributions} commits</span>
          )}
        </div>
      )}
      </>}
    </div>
  )
}


// Mini variant rendered in the collapsed sidebar column. The
// SideBar's plugin-discovery glob picks this up automatically by
// name, so adding the export is the only wiring required.
export function MiniPRPlugin() {
  const [count, setCount] = useState<number | null>(null)
  useEffect(() => {
    fetch('/api/live-stats').then(r => r.json())
      .then(d => setCount(d?.open_prs?.total ?? null))
      .catch(() => {})
  }, [])
  return (
    <div title={`PRs: ${count ?? '...'} open`} style={miniPluginStyle}>
      <span style={{ fontSize: 8, color: 'var(--text-dim)' }}>PRs</span>
      <span style={{ fontSize: 12, fontWeight: 700, color: count ? 'var(--accent)' : 'var(--text-dim)' }}>{count ?? '-'}</span>
    </div>
  )
}
