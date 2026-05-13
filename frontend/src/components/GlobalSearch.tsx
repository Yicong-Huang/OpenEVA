import { useState, useRef, useEffect, useCallback } from 'react'
import { api } from '../api'
import { useClickOutside } from '../hooks/useClickOutside'

type Result = Awaited<ReturnType<typeof api.search>>['results'][number]

interface Props {
  /** Navigate to a project + view (task/graph/list/all-prs/all-tasks/etc). */
  onNavigate: (projectId: string | null, view: string) => void
  /** Select a specific task on the target page. */
  onSelectTask?: (taskId: string | null) => void
  /** Select a PR for the PRs page. */
  onSelectPR?: (pr: { repo: string; number: number; taskId?: string; projectId?: string } | null) => void
}

/** Icon shown on each result row. Kept monochrome so it inherits
 *  --text-dim; easy to theme, no extra assets. */
function ResultIcon({ type }: { type: Result['type'] }) {
  const glyph = type === 'task' ? '\u2611'            // ballot box with check
              : type === 'pr'  ? '\u229C'             // circled plus (pr-ish)
              : '\u25B6'                              // play (session)
  const color = type === 'task' ? 'var(--blue)'
              : type === 'pr'   ? 'var(--green)'
              : 'var(--purple)'
  return (
    <span style={{
      fontSize: 13, width: 16, textAlign: 'center',
      color, flexShrink: 0,
    }}>{glyph}</span>
  )
}

function StatusBadge({ text }: { text: string }) {
  if (!text) return null
  return (
    <span style={{
      fontSize: 9, padding: '1px 6px', borderRadius: 8,
      background: 'var(--node-badge-bg)', color: 'var(--node-badge-text)',
      whiteSpace: 'nowrap', flexShrink: 0,
    }}>{text}</span>
  )
}

export function GlobalSearch({ onNavigate, onSelectTask, onSelectPR }: Props) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Result[]>([])
  const [open, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useClickOutside(rootRef, () => setOpen(false))

  // Debounce: don't hit /api/search on every keystroke. 120ms feels
  // instant but dodges the 3-4 network calls per word typed.
  useEffect(() => {
    if (!query.trim()) {
      setResults([])
      return
    }
    let cancelled = false
    const handle = setTimeout(async () => {
      try {
        const data = await api.search(query, 12)
        if (!cancelled) {
          setResults(data.results || [])
          setActiveIdx(0)
        }
      } catch {
        if (!cancelled) setResults([])
      }
    }, 120)
    return () => { cancelled = true; clearTimeout(handle) }
  }, [query])

  // Cmd/Ctrl+K opens the search (standard shortcut).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        inputRef.current?.focus()
        inputRef.current?.select()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const selectResult = useCallback((r: Result) => {
    setOpen(false)
    setQuery('')
    setResults([])
    if (r.type === 'task') {
      onNavigate(r.project_id || null, 'graph')
      onSelectTask?.(r.task_id || null)
    } else if (r.type === 'session') {
      // Live Tasks (formerly the per-project "sessions" view, which
      // was consolidated into a single global page). The page reads
      // selectedProjectId + selectedTaskId from props and auto-
      // focuses the matching card -- complete with the inline tmux
      // terminal, which is what the user is searching for. The old
      // value 'sessions' rendered NOTHING (no `view === 'sessions'`
      // branch in App.tsx anymore), so picking a session result
      // landed on a blank screen.
      onNavigate(r.project_id || null, 'all-tasks')
      onSelectTask?.(r.task_id || null)
    } else if (r.type === 'pr') {
      onNavigate(null, 'all-prs')
      if (r.pr_number && r.pr_repo) {
        onSelectPR?.({
          repo: r.pr_repo,
          number: r.pr_number,
          taskId: r.task_id || undefined,
          projectId: r.project_id || undefined,
        })
      }
    }
  }, [onNavigate, onSelectTask, onSelectPR])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (!open) return
    if (e.key === 'Escape') { setOpen(false); return }
    if (!results.length) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIdx((i) => Math.min(results.length - 1, i + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx((i) => Math.max(0, i - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      selectResult(results[activeIdx])
    }
  }, [open, results, activeIdx, selectResult])

  return (
    <div
      ref={rootRef}
      data-testid="global-search"
      style={{ position: 'relative', flex: '0 1 420px', maxWidth: 520 }}
    >
      <input
        ref={inputRef}
        data-testid="global-search-input"
        value={query}
        onChange={(e) => { setQuery(e.target.value); setOpen(true) }}
        onFocus={() => { if (query) setOpen(true) }}
        onKeyDown={handleKeyDown}
        placeholder="Search tasks/sessions/PRs (type:pr, status:open, ticket:EX-123, in:task) -- Cmd+K"
        style={{
          width: '100%', padding: '5px 10px', fontSize: 11,
          background: 'var(--input-bg)', color: 'var(--text)',
          border: '1px solid var(--border)', borderRadius: 6,
          fontFamily: 'inherit', outline: 'none',
        }}
      />
      {open && query.trim() && (
        <div
          data-testid="global-search-dropdown"
          style={{
            position: 'absolute', top: 30, left: 0, right: 0,
            background: 'var(--card-bg)',
            border: '1px solid var(--border)', borderRadius: 6,
            boxShadow: '0 6px 20px rgba(0,0,0,0.35)',
            zIndex: 500, maxHeight: 520, overflowY: 'auto',
          }}
        >
          {results.length === 0 ? (
            <div style={{ padding: '10px 12px', fontSize: 11, color: 'var(--text-faint)' }}>
              No results.
            </div>
          ) : results.map((r, i) => (
            <div
              key={`${r.type}:${r.project_id}:${r.task_id || r.pr_number || r.title}:${i}`}
              data-testid={`global-search-result-${i}`}
              onClick={() => selectResult(r)}
              onMouseEnter={() => setActiveIdx(i)}
              style={{
                display: 'flex', alignItems: 'flex-start', gap: 10,
                padding: '10px 12px', cursor: 'pointer',
                background: i === activeIdx ? 'var(--hover-bg)' : 'transparent',
                borderBottom: '1px solid var(--border)',
              }}
            >
              <div style={{ paddingTop: 2 }}>
                <ResultIcon type={r.type} />
              </div>
              <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                  <span style={{
                    fontWeight: 600, color: 'var(--text)', fontSize: 12,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    minWidth: 0,
                  }}>
                    {r.title}
                  </span>
                  <StatusBadge text={r.badge} />
                </div>
                <span style={{
                  color: 'var(--text-dim)', fontSize: 10,
                  lineHeight: 1.4,
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                  wordBreak: 'break-word',
                }}>
                  {r.subtitle}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
