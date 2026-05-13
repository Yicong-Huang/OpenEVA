import React, { useState, useCallback } from 'react'
import { ProjectTree } from './sidebar/ProjectTree'
import { discoveredPlugins } from '@app/utils/discoveredPlugins'
import { getNavPages } from '@app/pages/registry'

const COLLAPSED_KEY = 'eva-sidebar-collapsed'

function loadCollapsed(): boolean {
  try { return localStorage.getItem(COLLAPSED_KEY) === '1' } catch { return false }
}

// Plugins section: memoized so it never re-renders when sidebar
// navigation changes. The set of plugins is whatever `import.meta.glob`
// found at build time -- no hardcoded vendor imports here, so an OSS
// install with only `core/src/plugins/pr/` ships just the PR widget,
// and adding a new extension plugin is a matter of dropping a folder.
const PluginsPanel = React.memo(function PluginsPanel() {
  return (
    <div style={{ flexShrink: 0, borderTop: '1px solid var(--border)', padding: '0 12px 8px' }}>
      <div style={{ padding: '8px 4px', fontSize: 10, fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: 0.5 }}>Plugins</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 9 }}>
        {discoveredPlugins.map(({ name, Full }) => <Full key={name} />)}
      </div>
    </div>
  )
})

// Navigation button for collapsed sidebar
function NavButton({ icon, label, active, onClick }: { icon: string; label: string; active: boolean; onClick: () => void }) {
  return (
    <div
      title={label}
      onClick={onClick}
      style={{
        width: 36, height: 36, borderRadius: 6,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: active ? 'rgba(99,102,241,0.15)' : 'transparent',
        color: active ? 'var(--accent)' : 'var(--text-dim)',
        cursor: 'pointer', fontSize: 14, fontWeight: 600,
        transition: 'background 0.1s',
      }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'rgba(99,102,241,0.08)' }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent' }}
    >
      {icon}
    </div>
  )
}

// Project icon button for collapsed sidebar. Renders a circular
// progress ring around the 2-letter initials -- conveys "how done
// is this project" at a glance without the previous 7px % text
// (which was barely legible). Hover/active states match the rest
// of the collapsed sidebar.
function ProjectButton({ name, progress, active, onClick }: { name: string; progress: number; active: boolean; onClick: () => void }) {
  const initials = name.split(/[\s-]+/).map(w => w[0]).join('').substring(0, 2).toUpperCase()
  // Ring math: 36px button, 32px diameter ring, 2.5px stroke leaves
  // room for hover backgrounds without clipping. Stroke goes
  // counter-clockwise so 100% looks like a closed circle.
  const size = 36
  const r = 14.5
  const cx = size / 2
  const cy = size / 2
  const circumference = 2 * Math.PI * r
  const pct = Math.max(0, Math.min(100, progress))
  const offset = circumference * (1 - pct / 100)
  return (
    <div
      data-testid={`project-button-${name}`}
      title={`${name} (${progress}%)`}
      onClick={onClick}
      style={{
        width: size, height: size, borderRadius: 8,
        position: 'relative',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: active ? 'rgba(99,102,241,0.15)' : 'transparent',
        color: active ? 'var(--accent)' : 'var(--text)',
        cursor: 'pointer', fontSize: 11, fontWeight: 700,
        transition: 'background 0.18s ease, color 0.18s ease',
      }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'rgba(99,102,241,0.08)' }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = active ? 'rgba(99,102,241,0.15)' : 'transparent' }}
    >
      <svg
        width={size} height={size}
        style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
        aria-hidden
      >
        {/* Track: faint outer ring so 0% projects still have a visual outline. */}
        <circle
          cx={cx} cy={cy} r={r}
          fill="none"
          stroke="var(--border)"
          strokeWidth={2}
          opacity={0.4}
        />
        {/* Progress: stroke-dashoffset trick draws a partial arc. */}
        {pct > 0 && (
          <circle
            cx={cx} cy={cy} r={r}
            fill="none"
            stroke={active ? 'var(--accent)' : 'var(--green)'}
            strokeWidth={2.5}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            transform={`rotate(-90 ${cx} ${cy})`}
            style={{ transition: 'stroke-dashoffset 0.4s ease' }}
          />
        )}
      </svg>
      <span style={{ position: 'relative', zIndex: 1 }}>{initials}</span>
    </div>
  )
}

interface SideBarProps {
  activeProject: string | null
  activeView: string
  onNavigate: (projectId: string | null, view: string) => void
  projects?: Array<{ id: string; name: string; progress: number }>
}

export function SideBar({ activeProject, activeView, onNavigate }: SideBarProps) {
  const [collapsed, setCollapsed] = useState(loadCollapsed)

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev
      try { localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0') } catch { /* ignore */ }
      return next
    })
  }, [])

  if (collapsed) {
    return (
      <div
        className="sidebar"
        data-testid="sidebar"
        style={{
          width: 52, minWidth: 52,
          display: 'flex', flexDirection: 'column', height: '100%',
          alignItems: 'center', padding: '8px 0',
          transition: 'width 0.2s',
        }}
      >
        {/* Expand button */}
        <div
          title="Expand sidebar"
          onClick={toggleCollapsed}
          style={{
            width: 36, height: 28, borderRadius: 6, marginBottom: 8,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            cursor: 'pointer', color: 'var(--accent)', fontSize: 14,
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(99,102,241,0.1)' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
        >
          {'\u25B6'}
        </div>

        {/* Project icons -- rendered by ProjectTree in collapsed mode */}
        <CollapsedProjectList activeProject={activeProject} onNavigate={onNavigate} />

        <div style={{ borderTop: '1px solid var(--border)', width: 28, margin: '6px 0' }} />

        {/* Nav buttons from page registry (built-in + extension-contributed). */}
        {getNavPages().map((p) => (
          <NavButton
            key={p.id}
            icon={p.nav!.icon}
            label={p.nav!.label}
            active={activeView === p.id}
            onClick={() => onNavigate(null, p.id)}
          />
        ))}

        {/* Spacer */}
        <div style={{ flex: 1 }} />

        {/* Plugin mini icons with live data -- same discovery as the
            expanded PluginsPanel. Plugins that don't ship a Mini
            variant simply contribute nothing here. */}
        <div style={{ borderTop: '1px solid var(--border)', width: 28, margin: '6px 0' }} />
        {discoveredPlugins.map(({ name, Mini }) => Mini ? <Mini key={name} /> : null)}
      </div>
    )
  }

  return (
    <div className="sidebar" data-testid="sidebar" style={{ display: 'flex', flexDirection: 'column', height: '100%', transition: 'width 0.2s' }}>
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {/* Section header: matches the Plugins panel style (small, dim,
            uppercase) so both panels read as peers. Background falls
            through to `--sidebar-bg`; the collapse caret floats right. */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '8px 12px 4px',
        }}>
          <div style={{
            fontSize: 10, fontWeight: 600, color: 'var(--text-dim)',
            textTransform: 'uppercase', letterSpacing: 0.5,
          }}>Projects</div>
          <span
            title="Collapse sidebar"
            onClick={toggleCollapsed}
            style={{ cursor: 'pointer', fontSize: 11, color: 'var(--text-dim)', padding: '2px 4px' }}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--accent)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
          >
            {'\u25C0'}
          </span>
        </div>
        <ProjectTree activeProject={activeProject} activeView={activeView} onNavigate={onNavigate} />
      </div>
      <PluginsPanel />
    </div>
  )
}

// Collapsed project list -- fetches projects independently (lightweight)
function CollapsedProjectList({ activeProject, onNavigate }: { activeProject: string | null; onNavigate: (pid: string | null, view: string) => void }) {
  const [projects, setProjects] = React.useState<Array<{ id: string; name: string; progress: number; hidden: boolean }>>([])

  React.useEffect(() => {
    api_getProjects().then(setProjects).catch(() => {})
  }, [])

  // Mirror the expanded ProjectTree: filter hidden projects out of
  // the collapsed icon strip too. No "show hidden" toggle here --
  // the user has to expand the sidebar to un-hide.
  const visible = projects.filter((p) => !p.hidden)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, alignItems: 'center' }}>
      {visible.map((p) => (
        <ProjectButton
          key={p.id}
          name={p.name}
          progress={p.progress}
          active={activeProject === p.id}
          onClick={() => onNavigate(p.id, 'graph')}
        />
      ))}
    </div>
  )
}

// Lightweight project fetch for collapsed mode
async function api_getProjects(): Promise<Array<{ id: string; name: string; progress: number; hidden: boolean }>> {
  const resp = await fetch('/api/projects')
  if (!resp.ok) return []
  const data = await resp.json()
  return (data.projects || []).map((p: { id: string; name: string; progress: number; hidden?: boolean }) => ({
    id: p.id, name: p.name, progress: Math.round(p.progress), hidden: !!p.hidden,
  }))
}
