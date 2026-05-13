import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { api } from '../api'
import { renderMarkdown } from '../utils'
import { markdownToSlack } from '../utils/slackFormat'
import { useAlert } from '../components/Alert'
import { useSettingNumber } from '../hooks/useSettingNumber'

// WorkLog page horizon knobs (settings-driven). Defaults mirror the
// previous hardcoded values so existing users see no behaviour change.
const DEFAULT_DAY_MODE_DAYS = 60
const DEFAULT_STANDUP_MODE_WEEKS = 8

interface LogEntry {
  id: string
  date: string
  label: string
  content: string
  loading: boolean
  mode: 'day' | 'standup'
  rangeStart?: string
  rangeEnd?: string
  updatedAt?: string
}

const STANDUP_KEY = 'eva-worklog-standup-mode'
const COLLAPSED_Q_KEY = 'eva-worklog-collapsed-quarters'
const COLLAPSED_W_KEY = 'eva-worklog-collapsed-weeks'

function loadBool(k: string): boolean {
  try { return localStorage.getItem(k) === '1' } catch { return false }
}
function saveBool(k: string, v: boolean) {
  try { localStorage.setItem(k, v ? '1' : '0') } catch { /* ignore */ }
}
function loadSet(k: string): Set<string> {
  try {
    const raw = localStorage.getItem(k)
    if (!raw) return new Set()
    return new Set(JSON.parse(raw))
  } catch { return new Set() }
}
function saveSet(k: string, v: Set<string>) {
  try { localStorage.setItem(k, JSON.stringify(Array.from(v))) } catch { /* ignore */ }
}

function pad(n: number): string { return String(n).padStart(2, '0') }

function dateToISO(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// Common label style shared by standup + day headers ("Apr 12"). Kept as
// a helper so every WorkLog entry's display date stays in lockstep -- a
// drift here splits the UI into two date-format dialects.
function shortMonthDay(d: Date): string {
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function isoWeek(d: Date): { year: number; week: number } {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()))
  const dow = t.getUTCDay() || 7
  t.setUTCDate(t.getUTCDate() + 4 - dow)
  const yearStart = new Date(Date.UTC(t.getUTCFullYear(), 0, 1))
  const week = Math.ceil(((t.getTime() - yearStart.getTime()) / 86400000 + 1) / 7)
  return { year: t.getUTCFullYear(), week }
}

function quarterKey(d: Date): string {
  return `${d.getFullYear()}-Q${Math.floor(d.getMonth() / 3) + 1}`
}

function weekKey(d: Date): { key: string; start: Date; end: Date } {
  const { year, week } = isoWeek(d)
  const key = `${year}-W${pad(week)}`
  const jan4 = new Date(year, 0, 4)
  const jan4Dow = jan4.getDay() || 7
  const mon = new Date(year, 0, 4 - (jan4Dow - 1) + (week - 1) * 7)
  const sun = new Date(mon)
  sun.setDate(sun.getDate() + 6)
  return { key, start: mon, end: sun }
}

function recentDays(count: number): string[] {
  const out: string[] = []
  const now = new Date()
  for (let i = 0; i < count; i++) {
    const d = new Date(now)
    d.setDate(d.getDate() - i)
    out.push(dateToISO(d))
  }
  return out
}

function laOffsetHours(d: Date): number {
  const y = d.getFullYear()
  const mar = new Date(y, 2, 1)
  const dstStart = new Date(y, 2, 1 + ((7 - mar.getDay()) % 7) + 7)
  const nov = new Date(y, 10, 1)
  const dstEnd = new Date(y, 10, 1 + ((7 - nov.getDay()) % 7))
  return d >= dstStart && d < dstEnd ? -7 : -8
}

function laEveningIso(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number)
  const laLocal = new Date(y, m - 1, d, 18, 0, 0)
  const off = laOffsetHours(laLocal)
  const utcHours = 18 - off
  const utc = new Date(Date.UTC(y, m - 1, d, utcHours, 0, 0))
  return utc.toISOString().replace(/\.\d+Z$/, 'Z')
}

function standupMeetings(weeks: number): Array<{ meetingDate: string; startIso: string; endIso: string; label: string }> {
  // Standup days: Mon (1), Wed (3), Thu (4).
  const meetingDows = [1, 3, 4]
  const candidates: string[] = []
  const now = new Date()
  for (let i = 0; i < weeks * 7 + 14; i++) {
    const d = new Date(now)
    d.setDate(d.getDate() - i)
    // Include today if today is a standup day, even before the 6pm cutoff --
    // so the user can review their log before the actual standup.
    if (meetingDows.includes(d.getDay())) candidates.push(dateToISO(d))
    if (candidates.length >= weeks * 3 + 1) break
  }
  const out: Array<{ meetingDate: string; startIso: string; endIso: string; label: string }> = []
  for (let i = 0; i < candidates.length - 1; i++) {
    const cur = candidates[i]
    const prev = candidates[i + 1]
    const d = new Date(cur + 'T12:00:00')
    const dowName = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()]
    const monthDay = shortMonthDay(d)
    out.push({
      meetingDate: cur,
      startIso: laEveningIso(prev),
      endIso: laEveningIso(cur),
      label: `${dowName} ${monthDay} standup`,
    })
  }
  return out
}

function groupByQuarterWeek(dates: string[]): Array<{ quarter: string; weeks: Array<{ key: string; label: string; dates: string[] }> }> {
  const quarters = new Map<string, Map<string, { label: string; dates: string[] }>>()
  for (const ds of dates) {
    const d = new Date(ds + 'T12:00:00')
    const q = quarterKey(d)
    const w = weekKey(d)
    if (!quarters.has(q)) quarters.set(q, new Map())
    const weeks = quarters.get(q)!
    if (!weeks.has(w.key)) {
      const label = `${shortMonthDay(w.start)} - ${shortMonthDay(w.end)}`
      weeks.set(w.key, { label, dates: [] })
    }
    weeks.get(w.key)!.dates.push(ds)
  }
  return Array.from(quarters.entries()).map(([q, wm]) => ({
    quarter: q,
    weeks: Array.from(wm.entries()).map(([k, v]) => ({ key: k, label: v.label, dates: v.dates })),
  }))
}

export function WorkLogPage() {
  const dayModeDays = useSettingNumber(
    'ui.worklog.day_mode_days', DEFAULT_DAY_MODE_DAYS,
    { min: 7, max: 365 },
  )
  const standupModeWeeks = useSettingNumber(
    'ui.worklog.standup_mode_weeks', DEFAULT_STANDUP_MODE_WEEKS,
    { min: 1, max: 52 },
  )
  const [standupMode, setStandupMode] = useState(loadBool(STANDUP_KEY))
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [collapsedQuarters, setCollapsedQuarters] = useState<Set<string>>(() => loadSet(COLLAPSED_Q_KEY))
  const [collapsedWeeks, setCollapsedWeeks] = useState<Set<string>>(() => loadSet(COLLAPSED_W_KEY))
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [activeId, setActiveId] = useState<string>('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const entryRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const { confirm, alert } = useAlert()

  const plannedEntries = useMemo(() => {
    if (standupMode) {
      return standupMeetings(standupModeWeeks).map(m => ({
        id: `standup-${m.meetingDate}`,
        date: m.meetingDate,
        label: m.label,
        mode: 'standup' as const,
        rangeStart: m.startIso,
        rangeEnd: m.endIso,
      }))
    }
    return recentDays(dayModeDays).map(d => {
      const dt = new Date(d + 'T12:00:00')
      const dowName = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][dt.getDay()]
      const monthDay = shortMonthDay(dt)
      return { id: `day-${d}`, date: d, label: `${dowName} ${monthDay}`, mode: 'day' as const, rangeStart: undefined, rangeEnd: undefined }
    })
  }, [standupMode, dayModeDays, standupModeWeeks])

  useEffect(() => {
    setEntries(plannedEntries.map(p => ({
      id: p.id, date: p.date, label: p.label, mode: p.mode,
      rangeStart: p.rangeStart, rangeEnd: p.rangeEnd,
      content: '', loading: true,
    })))
    setEditingId(null)
  }, [plannedEntries])

  useEffect(() => {
    let cancelled = false
    async function loadOne(entry: LogEntry) {
      try {
        if (entry.mode === 'day') {
          const data = await api.getWorkLog(entry.date)
          if (cancelled) return
          setEntries(prev => prev.map(e => e.id === entry.id ? { ...e, content: data.content, loading: false, updatedAt: data.updated_at } : e))
        } else {
          const data = await api.getWorkLogRange(entry.rangeStart!, entry.rangeEnd!, entry.label)
          if (cancelled) return
          setEntries(prev => prev.map(e => e.id === entry.id ? { ...e, content: data.content, loading: false } : e))
        }
      } catch {
        if (cancelled) return
        setEntries(prev => prev.map(e => e.id === entry.id ? { ...e, content: '(failed to load)', loading: false } : e))
      }
    }
    for (const e of entries) {
      if (e.loading) loadOne(e)
    }
    return () => { cancelled = true }
  }, [entries.length, standupMode])  // eslint-disable-line react-hooks/exhaustive-deps

  const dateGroups = useMemo(() => {
    return groupByQuarterWeek(entries.map(e => e.date))
  }, [entries])

  const toggleQuarter = (q: string) => {
    setCollapsedQuarters(prev => {
      const next = new Set(prev)
      if (next.has(q)) next.delete(q); else next.add(q)
      saveSet(COLLAPSED_Q_KEY, next)
      return next
    })
  }

  const toggleWeek = (w: string) => {
    setCollapsedWeeks(prev => {
      const next = new Set(prev)
      if (next.has(w)) next.delete(w); else next.add(w)
      saveSet(COLLAPSED_W_KEY, next)
      return next
    })
  }

  const toggleStandup = () => {
    setStandupMode(prev => {
      const next = !prev
      saveBool(STANDUP_KEY, next)
      return next
    })
  }

  const scrollToDate = (date: string) => {
    const id = standupMode ? `standup-${date}` : `day-${date}`
    const el = entryRefs.current[id]
    if (el && scrollRef.current) {
      scrollRef.current.scrollTo({ top: el.offsetTop - 8, behavior: 'smooth' })
    }
  }

  const handleScroll = useCallback(() => {
    if (!scrollRef.current) return
    const scrollTop = scrollRef.current.scrollTop
    let best = ''
    let bestTop = -Infinity
    for (const [id, el] of Object.entries(entryRefs.current)) {
      if (!el) continue
      if (el.offsetTop <= scrollTop + 80 && el.offsetTop > bestTop) {
        bestTop = el.offsetTop
        best = id
      }
    }
    if (best) setActiveId(best)
  }, [])

  const handleEdit = (entry: LogEntry) => {
    setEditingId(entry.id)
    setDraft(entry.content)
  }

  const handleSave = async (entry: LogEntry) => {
    setSaving(true)
    try {
      await api.saveWorkLog(entry.date, draft)
      setEntries(prev => prev.map(e => e.id === entry.id ? { ...e, content: draft } : e))
      setEditingId(null)
    } catch (e) {
      await alert({
        title: 'Failed to save',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setSaving(false)
    }
  }

  const handleRegenerate = async (entry: LogEntry) => {
    // Standup mode is always live-generated (not persisted), so we can skip the
    // confirm + DELETE -- just re-fetch the range. Day mode needs the confirm
    // because it overwrites any manual edits the user saved.
    if (entry.mode === 'day') {
      const ok = await confirm({
        title: 'Regenerate from database?',
        message: 'This overwrites your edits for this day.',
        confirmLabel: 'Regenerate',
        danger: true,
      })
      if (!ok) return
    }
    setEntries(prev => prev.map(e => e.id === entry.id ? { ...e, loading: true } : e))
    try { if (entry.mode === 'day') await fetch(`/api/worklog/${entry.date}`, { method: 'DELETE' }) } catch { /* ignore */ }
    try {
      if (entry.mode === 'day') {
        const data = await api.getWorkLog(entry.date)
        setEntries(prev => prev.map(e => e.id === entry.id ? { ...e, content: data.content, loading: false, updatedAt: data.updated_at } : e))
      } else {
        const data = await api.getWorkLogRange(entry.rangeStart!, entry.rangeEnd!, entry.label)
        setEntries(prev => prev.map(e => e.id === entry.id ? { ...e, content: data.content, loading: false } : e))
      }
    } catch {
      setEntries(prev => prev.map(e => e.id === entry.id ? { ...e, loading: false } : e))
    }
  }

  return (
    <div data-testid="worklog-page" style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Left: date tree */}
      <div style={{
        width: 220, borderRight: '1px solid var(--border)',
        overflowY: 'hidden', flexShrink: 0,
        display: 'flex', flexDirection: 'column',
      }}>
        <div style={{ padding: '12px 16px', fontSize: 14, fontWeight: 700, borderBottom: '1px solid var(--border)' }}>
          Work Log
        </div>
        <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)' }}>
          <label style={{ fontSize: 11, color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input type="checkbox" checked={standupMode} onChange={toggleStandup} style={{ cursor: 'pointer' }} />
            Standup mode
          </label>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', paddingBottom: 16 }}>
          {dateGroups.map(({ quarter, weeks }) => {
            const qCollapsed = collapsedQuarters.has(quarter)
            return (
              <div key={quarter}>
                <div
                  onClick={() => toggleQuarter(quarter)}
                  style={{
                    padding: '8px 12px', fontSize: 11, fontWeight: 700, cursor: 'pointer',
                    color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: 0.5,
                    display: 'flex', alignItems: 'center', gap: 4, background: 'var(--panel-bg)',
                  }}
                >
                  <span style={{ fontSize: 9 }}>{qCollapsed ? '\u25B6' : '\u25BC'}</span>
                  {quarter}
                </div>
                {!qCollapsed && weeks.map(({ key, label, dates }) => {
                  const wCollapsed = collapsedWeeks.has(key)
                  return (
                    <div key={key}>
                      <div
                        onClick={() => toggleWeek(key)}
                        style={{
                          padding: '6px 12px 6px 22px', fontSize: 11, cursor: 'pointer',
                          color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 4,
                        }}
                      >
                        <span style={{ fontSize: 9 }}>{wCollapsed ? '\u25B6' : '\u25BC'}</span>
                        {label}
                      </div>
                      {!wCollapsed && dates.map(d => {
                        const id = standupMode ? `standup-${d}` : `day-${d}`
                        const entry = entries.find(e => e.id === id)
                        const dayLabel = entry?.label || d
                        const isActive = activeId === id
                        return (
                          <div
                            key={d}
                            onClick={() => scrollToDate(d)}
                            style={{
                              padding: '4px 12px 4px 36px', fontSize: 11, cursor: 'pointer',
                              background: isActive ? 'rgba(99,102,241,0.15)' : 'transparent',
                              color: isActive ? 'var(--accent)' : 'var(--text)',
                              fontWeight: isActive ? 600 : 400,
                            }}
                          >
                            {dayLabel}
                          </div>
                        )
                      })}
                    </div>
                  )
                })}
              </div>
            )
          })}
        </div>
      </div>

      {/* Right: endless doc */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        style={{ flex: 1, overflowY: 'auto', padding: 20 }}
      >
        {entries.map(entry => (
          <div
            key={entry.id}
            ref={el => { entryRefs.current[entry.id] = el }}
            id={entry.id}
            style={{ marginBottom: 24, scrollMarginTop: 8 }}
          >
            <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
              <span style={{ fontSize: 14, fontWeight: 700, flex: 1 }}>{entry.label}</span>
              {entry.mode === 'day' && editingId !== entry.id && !entry.loading && (
                <>
                  <button className="btn-action" style={{ fontSize: 11 }} onClick={() => handleRegenerate(entry)}>Regenerate</button>
                  <button className="btn-action accent" style={{ fontSize: 11 }} onClick={() => handleEdit(entry)}>Edit</button>
                </>
              )}
              {entry.mode === 'day' && editingId === entry.id && (
                <>
                  <button className="btn-action" style={{ fontSize: 11 }} onClick={() => { setEditingId(null); setDraft('') }} disabled={saving}>Cancel</button>
                  <button className="btn-action accent" style={{ fontSize: 11 }} onClick={() => handleSave(entry)} disabled={saving}>{saving ? '...' : 'Save'}</button>
                </>
              )}
              {entry.mode === 'standup' && !entry.loading && (
                <>
                  <button className="btn-action" style={{ fontSize: 11 }} onClick={() => handleRegenerate(entry)}>Regenerate</button>
                  <button
                    className="btn-action"
                    style={{ fontSize: 11 }}
                    title="Copy as flat Slack-friendly text (URLs and bullets stripped)"
                    onClick={() => { navigator.clipboard.writeText(markdownToSlack(entry.content)).catch(() => {}) }}
                  >Copy</button>
                </>
              )}
            </div>
            {entry.loading ? (
              <div style={{ color: 'var(--text-dim)', fontSize: 12, padding: 16 }}>Loading...</div>
            ) : editingId === entry.id ? (
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                disabled={saving}
                style={{
                  width: '100%', minHeight: 400, padding: 12,
                  background: 'var(--panel-bg)', border: '1px solid var(--border)',
                  borderRadius: 6, color: 'var(--text)', fontSize: 12,
                  fontFamily: 'Menlo, Monaco, "Courier New", monospace',
                  lineHeight: 1.6, resize: 'vertical',
                }}
              />
            ) : (
              <div
                className="md-body"
                style={{
                  background: 'var(--card-bg)', borderRadius: 8, padding: 16,
                  border: '1px solid var(--border)', lineHeight: 1.7,
                }}
                dangerouslySetInnerHTML={{ __html: renderMarkdown(entry.content) }}
              />
            )}
            {entry.updatedAt && entry.mode === 'day' && editingId !== entry.id && (
              <div style={{ fontSize: 10, color: 'var(--text-faint)', marginTop: 4, textAlign: 'right' }}>
                Updated: {entry.updatedAt}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
