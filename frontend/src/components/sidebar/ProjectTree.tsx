import { useState, useEffect } from 'react'
import type { Project } from '../../types'
import { api } from '../../api'
import { getNavPages } from '../../pages/registry'

interface ProjectTreeProps {
  activeProject: string | null
  activeView: string
  onNavigate: (projectId: string | null, view: string) => void
}

/**
 * Sidebar project list. Each row is one project; clicking it lands
 * on the unified Project Page (`view='graph'`). The previous
 * expand/collapse + child sub-views (Task Tracker / Task Cards /
 * Sessions) were removed: the per-project Task Cards list and
 * Sessions list are now consolidated on All Live Tasks.
 */
export function ProjectTree({ activeProject, activeView, onNavigate }: ProjectTreeProps) {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [showHidden, setShowHidden] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [newId, setNewId] = useState('')
  const [newName, setNewName] = useState('')
  const [createError, setCreateError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const refresh = () => {
    api.getProjects().then((data) => {
      setProjects(data.projects)
      setLoading(false)
    }).catch(() => setLoading(false))
  }

  async function submitCreate(e?: React.FormEvent) {
    if (e) e.preventDefault()
    if (!newId.trim() || creating) return
    setCreating(true)
    setCreateError(null)
    try {
      await api.createProject({ id: newId.trim(), name: newName.trim() })
      setNewId(''); setNewName('')
      setShowCreate(false)
      refresh()
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : String(err))
    } finally {
      setCreating(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    api
      .getProjects()
      .then((data) => {
        if (cancelled) return
        setProjects(data.projects)
        setLoading(false)
      })
      .catch(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (loading) {
    return (
      <div style={{ padding: '8px 16px', color: 'var(--text-dim)', fontSize: 12 }}>
        Loading...
      </div>
    )
  }

  async function toggleHidden(pid: string, nextHidden: boolean) {
    try {
      await api.setProjectVisibility(pid, nextHidden)
      refresh()
      // Tell other panels (Live Tasks, All PRs, Tickets) to refetch
      // -- they filter by visibility too, but cache their list response.
      window.dispatchEvent(new CustomEvent('eva:project-visibility-changed', {
        detail: { project_id: pid, hidden: nextHidden },
      }))
    } catch {
      /* best-effort: UI stays unchanged on failure */
    }
  }

  const visibleProjects = showHidden ? projects : projects.filter((p) => !p.hidden)
  const hiddenCount = projects.filter((p) => p.hidden).length

  return (
    <>
      <div id="sidebar-tree" data-testid="sidebar-tree">
        {/* Create-project form: collapsed by default, expands on `+`. */}
        {showCreate && (
          <form onSubmit={submitCreate} data-testid="new-project-form"
                style={{ padding: '6px 12px', borderBottom: '1px solid var(--border)' }}>
            <input
              autoFocus
              value={newId}
              onChange={(e) => setNewId(e.target.value)}
              placeholder="project-id (lowercase-kebab)"
              data-testid="new-project-id"
              style={{
                width: '100%', padding: '4px 6px', fontSize: 11, marginBottom: 4,
                background: 'var(--card-bg)', color: 'var(--text)',
                border: '1px solid var(--border)', borderRadius: 3,
              }}
            />
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Display name (optional)"
              data-testid="new-project-name"
              style={{
                width: '100%', padding: '4px 6px', fontSize: 11, marginBottom: 4,
                background: 'var(--card-bg)', color: 'var(--text)',
                border: '1px solid var(--border)', borderRadius: 3,
              }}
            />
            {createError && (
              <div style={{ fontSize: 10, color: 'var(--danger,#e06c75)', marginBottom: 4 }}>
                {createError}
              </div>
            )}
            <div style={{ display: 'flex', gap: 4 }}>
              <button type="submit" disabled={!newId.trim() || creating}
                      data-testid="new-project-submit"
                      style={{
                        flex: 1, padding: '3px 6px', fontSize: 10, cursor: 'pointer',
                        background: 'var(--accent)', color: 'var(--bg)',
                        border: 'none', borderRadius: 3,
                      }}>
                {creating ? 'Creating...' : 'Create'}
              </button>
              <button type="button"
                      onClick={() => { setShowCreate(false); setCreateError(null) }}
                      style={{
                        padding: '3px 6px', fontSize: 10, cursor: 'pointer',
                        background: 'transparent', color: 'var(--text-dim)',
                        border: '1px solid var(--border)', borderRadius: 3,
                      }}>
                Cancel
              </button>
            </div>
          </form>
        )}
        {!showCreate && (
          <div
            onClick={() => setShowCreate(true)}
            data-testid="new-project-toggle"
            style={{
              padding: '4px 16px', fontSize: 10, color: 'var(--text-dim)',
              cursor: 'pointer', textTransform: 'uppercase', letterSpacing: 0.5,
              borderBottom: '1px solid var(--border)',
            }}
          >
            {'+ New project'}
          </div>
        )}
        {visibleProjects.map((p) => {
          const isActive = activeProject === p.id && activeView === 'graph'
          return (
            <div key={p.id} className="project-node" style={{ display: 'flex', alignItems: 'center' }}>
              <div
                className={`project-name${isActive ? ' active' : ''}`}
                onClick={() => onNavigate(p.id, 'graph')}
                data-testid={`project-${p.id}`}
                data-pid={p.id}
                style={{ flex: 1, opacity: p.hidden ? 0.5 : 1, cursor: 'pointer' }}
              >
                {p.name}
                <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-dim)' }}>
                  {p.progress}%
                </span>
              </div>
              <button
                type="button"
                title={p.hidden ? 'Unhide project' : 'Hide project'}
                onClick={(e) => { e.stopPropagation(); toggleHidden(p.id, !p.hidden) }}
                data-testid={`project-hide-${p.id}`}
                style={{
                  background: 'transparent', border: 'none', cursor: 'pointer',
                  color: 'var(--text-dim)', fontSize: 14, lineHeight: 1,
                  padding: '0 8px', fontFamily: 'inherit',
                }}
              >
                {p.hidden ? '+' : '×'}
              </button>
            </div>
          )
        })}
        {hiddenCount > 0 && (
          <div
            onClick={() => setShowHidden(!showHidden)}
            data-testid="toggle-hidden-projects"
            style={{
              padding: '4px 16px', fontSize: 10, color: 'var(--text-dim)',
              cursor: 'pointer', textTransform: 'uppercase', letterSpacing: 0.5,
            }}
          >
            {showHidden ? 'Hide hidden' : `Show ${hiddenCount} hidden`}
          </div>
        )}
      </div>

      {/* Top-level nav from page registry (built-in + extension-contributed). */}
      {getNavPages().map((p, i) => (
        <div
          key={p.id}
          className={`child-node${!activeProject && activeView === p.id ? ' active' : ''}`}
          style={{
            padding: '8px 16px',
            fontWeight: 600,
            ...(i === 0 ? { marginTop: 4, borderTop: '1px solid var(--border)' } : {}),
          }}
          onClick={() => onNavigate(null, p.id)}
          data-testid={`${p.id}-btn`}
        >
          {p.nav!.label}
        </div>
      ))}
    </>
  )
}
