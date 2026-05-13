import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../../api'
import type { EvaEvent } from '../../types'
import { useClickOutside } from '../../hooks/useClickOutside'
import { useEventBus } from '../../hooks/useEventBus'
import { timeAgo } from '../../utils'

interface EventStatusProps {
  sseUnread?: number
  onOpened?: () => void
  onNavigate?: (event: EvaEvent) => void
}

// Icon + color per event type prefix
function eventMeta(type: string): { icon: string; color: string; label: string } {
  if (type === 'github.review_requested') return { icon: '\u{1F440}', color: 'var(--yellow)', label: 'Review' }
  if (type === 'github.ci_activity') return { icon: '\u26A0', color: 'var(--red)', label: 'CI' }
  if (type === 'github.comment') return { icon: '\u{1F4AC}', color: 'var(--accent)', label: 'Comment' }
  if (type === 'github.mention') return { icon: '@', color: 'var(--yellow)', label: 'Mention' }
  if (type === 'github.assign') return { icon: '\u{1F464}', color: 'var(--text-dim)', label: 'Assigned' }
  if (type === 'github.state_change') return { icon: '\u2194', color: 'var(--green)', label: 'State' }
  if (type === 'github.author') return { icon: '\u2191', color: 'var(--accent)', label: 'PR' }
  if (type === 'agent.task_done') return { icon: '\u2713', color: 'var(--green)', label: 'Done' }
  if (type === 'agent.needs_permission') return { icon: '\u26A0', color: 'var(--yellow)', label: 'Permission' }
  if (type.startsWith('auth.cert_expired')) return { icon: '\u2717', color: 'var(--red)', label: 'Cert' }
  if (type.startsWith('auth.cert_expiring')) return { icon: '\u23F1', color: 'var(--yellow)', label: 'Cert' }
  if (type.startsWith('auth.cert_renewed')) return { icon: '\u2713', color: 'var(--green)', label: 'Cert' }
  if (type === 'slack.boba') return { icon: '\u{1F9CB}', color: 'var(--green)', label: 'Boba' }
  if (type === 'slack.message') return { icon: '\u{1F4E8}', color: 'var(--accent)', label: 'Slack' }
  return { icon: '\u2022', color: 'var(--text-dim)', label: '' }
}

interface GroupedEvent {
  event: EvaEvent
  count: number
}

const GROUP_WINDOW_MS = 30 * 60 * 1000 // 30 minutes

/** Group events with the same title + type within a 30-min window. */
function groupEvents(events: EvaEvent[]): GroupedEvent[] {
  const groups: GroupedEvent[] = []
  for (const ev of events) {
    const evTime = new Date(ev.ts).getTime()
    // Find an existing group with same title+type within the time window
    const match = groups.find(g =>
      g.event.title === ev.title &&
      g.event.type === ev.type &&
      Math.abs(new Date(g.event.ts).getTime() - evTime) < GROUP_WINDOW_MS
    )
    if (match) {
      match.count++
    } else {
      groups.push({ event: ev, count: 1 })
    }
  }
  return groups
}

// Event types shown in the dropdown (actionable notifications only)
const DISPLAY_TYPES = new Set([
  'github.review_requested', 'github.ci_activity', 'github.comment',
  'github.mention', 'github.assign', 'github.author', 'github.state_change',
  'agent.task_done', 'agent.needs_permission',
  'auth.cert_expired', 'auth.cert_expiring', 'auth.cert_renewed',
  'slack.message', 'slack.boba',
])

function isDisplayable(ev: EvaEvent): boolean {
  return DISPLAY_TYPES.has(ev.type)
}

function canNavigate(ev: EvaEvent): boolean {
  if (ev.url && ev.type.startsWith('github.')) return true
  if (ev.url && ev.type === 'slack.message') return true
  if (ev.type === 'agent.needs_permission' || ev.type === 'agent.task_done') return true
  return false
}

const MIN_ROWS = 10
const MORE_ROWS = 20
const FETCH_BATCH = 80 // fetch enough raw events to fill grouped rows

export function EventStatus({ sseUnread, onOpened, onNavigate }: EventStatusProps) {
  const [open, setOpen] = useState(false)
  const [events, setEvents] = useState<EvaEvent[]>([])
  const [apiUnread, setApiUnread] = useState(0)
  const [visibleRows, setVisibleRows] = useState(MIN_ROWS)
  const [fetchLimit, setFetchLimit] = useState(FETCH_BATCH)
  const [hasMore, setHasMore] = useState(true)
  const ref = useRef<HTMLDivElement>(null)
  useClickOutside(ref, () => setOpen(false))

  const unread = (sseUnread != null && sseUnread > 0) ? sseUnread : apiUnread

  const load = useCallback(async (limit?: number) => {
    try {
      const data = await api.getEvents(limit || fetchLimit)
      setEvents(data.events)
      setApiUnread(data.unread)
      setHasMore(data.events.length >= (limit || fetchLimit))
    } catch {
      setEvents([])
      setApiUnread(0)
    }
  }, [fetchLimit])

  useEffect(() => { load() }, [load])

  // Auto-refresh when displayable events arrive via SSE
  const refreshIfOpen = useCallback(() => { if (open) load() }, [open, load])
  useEventBus('github.*', refreshIfOpen)
  useEventBus('agent.task_done', refreshIfOpen)
  useEventBus('agent.needs_permission', refreshIfOpen)
  useEventBus('auth.*', refreshIfOpen)
  useEventBus('slack.*', refreshIfOpen)

  const handleLoadMore = useCallback(() => {
    const newVisible = visibleRows + MORE_ROWS
    setVisibleRows(newVisible)
    // Fetch more raw events if we might need them
    const newFetchLimit = fetchLimit + FETCH_BATCH
    setFetchLimit(newFetchLimit)
    load(newFetchLimit)
  }, [visibleRows, fetchLimit, load])

  const handleClick = async (ev: EvaEvent) => {
    if (!canNavigate(ev)) return

    // Mark this event + related events as read
    try {
      if (ev.type.startsWith('github.') && ev.url) {
        await api.markEventsRead({ url: ev.url })
      } else if (ev.type.startsWith('agent.')) {
        // Extract session name from title like "Agent done: session-name".
        const match = ev.title.match(/: (.+)$/)
        if (match) await api.markEventsRead({ session: match[1] })
      }
      load()
    } catch { /* ignore */ }

    if (onNavigate) {
      onNavigate(ev)
      setOpen(false)
    }
  }

  return (
    <div ref={ref} style={{ position: 'relative', cursor: 'pointer' }} data-testid="events-topbar">
      <span
        style={{ display: 'flex', alignItems: 'center', gap: 4 }}
        onClick={() => { setOpen(!open); if (!open) { setVisibleRows(MIN_ROWS); onOpened?.(); load() } }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          style={{ color: 'var(--text-dim)' }}>
          <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 01-3.46 0" />
        </svg>
        {unread > 0 && (
          <span data-testid="events-badge" style={{
            background: 'var(--red)', color: 'var(--bg)', fontSize: 8, fontWeight: 700,
            borderRadius: 8, padding: '0 4px', lineHeight: '14px', minWidth: 14, textAlign: 'center',
          }}>
            {unread}
          </span>
        )}
      </span>
      {open && (
        <div style={{
          position: 'absolute', right: 0, top: 32, background: 'var(--card-bg)',
          border: '1px solid var(--border)', borderRadius: 8, padding: 12,
          width: 400, maxHeight: 460, zIndex: 300, boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontWeight: 600, color: 'var(--text)', fontSize: 12 }}>
              Events{' '}
              <span style={{ fontWeight: 400, fontSize: 10, color: 'var(--text-dim)' }}>({events.length})</span>
            </span>
            <span style={{ display: 'flex', gap: 4 }}>
              {unread > 0 && (
                <button className="btn-action" style={{ padding: '1px 6px', fontSize: 9 }}
                  data-testid="events-read-all"
                  onClick={async (e) => {
                    e.stopPropagation()
                    try { await api.markEventsRead(); await load() } catch { /* ignore */ }
                  }}>Read All</button>
              )}
              <button className="btn-action" style={{ padding: '1px 6px', fontSize: 9 }}
                onClick={(e) => { e.stopPropagation(); load() }}>&#8635;</button>
            </span>
          </div>
          <div style={{ overflowY: 'auto', maxHeight: 400 }}>
            {(() => {
              const allGrouped = groupEvents(events.filter(isDisplayable))
              const visible = allGrouped.slice(0, visibleRows)
              const moreAvailable = allGrouped.length > visibleRows || hasMore

              if (allGrouped.length === 0) {
                return <div style={{ color: 'var(--text-dim)', fontSize: 11, textAlign: 'center', padding: 16 }}>No events</div>
              }

              return (
                <>
                  {visible.map(({ event: ev, count }) => {
                    const meta = eventMeta(ev.type)
                    const navigable = canNavigate(ev)
                    return (
                      <div
                        key={ev.id}
                        data-testid="event-row"
                        style={{
                          padding: '6px 8px',
                          borderBottom: '1px solid var(--border)',
                          fontSize: 11,
                          opacity: ev.read === 1 ? 0.5 : 1,
                          cursor: navigable ? 'pointer' : 'default',
                          borderRadius: 4,
                        }}
                        onMouseEnter={(e) => { if (navigable) (e.currentTarget.style.background = 'rgba(99,102,241,0.08)') }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                        onClick={() => handleClick(ev)}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span style={{ color: meta.color, fontSize: 12, width: 16, textAlign: 'center', flexShrink: 0 }}>
                            {meta.icon}
                          </span>
                          <span style={{ fontWeight: 600, color: 'var(--text)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {ev.title}
                          </span>
                          {count > 1 && (
                            <span style={{
                              fontSize: 8, fontWeight: 700, color: 'var(--text-dim)', background: 'var(--border)',
                              borderRadius: 8, padding: '0 5px', lineHeight: '15px', minWidth: 15, textAlign: 'center', flexShrink: 0,
                            }}>
                              {count}
                            </span>
                          )}
                          <span style={{ fontSize: 9, color: 'var(--text-faint)', flexShrink: 0 }}>{timeAgo(ev.ts)}</span>
                        </div>
                        {ev.message && (
                          <div style={{ color: 'var(--text-dim)', fontSize: 10, marginTop: 2, marginLeft: 22, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {ev.message}
                          </div>
                        )}
                      </div>
                    )
                  })}
                  {moreAvailable && (
                    <button
                      className="btn-action"
                      data-testid="events-load-more"
                      style={{ width: '100%', padding: '6px 0', fontSize: 10, marginTop: 4, color: 'var(--text-dim)' }}
                      onClick={(e) => { e.stopPropagation(); handleLoadMore() }}
                    >
                      Historical...
                    </button>
                  )}
                </>
              )
            })()}
          </div>
        </div>
      )}
    </div>
  )
}
