import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { api, type Ticket } from '../api'
import {
  TICKET_TABS, type TicketTab, myEmails, tabForTicket, groupForTicket,
} from '../utils/ticketBuckets'
import { TicketNode } from '../components/TicketNode'
import { TicketTaskCard } from '../components/TicketTaskCard'
import { TicketCard } from '../components/TicketCard'
import { useEventBus } from '../hooks/useEventBus'
import { useLayoutRatios } from '../hooks/useLayoutRatios'
import { useSettingNumber } from '../hooks/useSettingNumber'

// Width-percent triple for the 3-pane layout (queue / session / detail)
// when a ticket is selected. Backed by the `ui.layout.tickets_col_ratios`
// setting; the shared `useLayoutRatios` hook handles fetch + validation.
const DEFAULT_TICKETS_RATIOS = [30, 35, 35] as const

// Queue-pane fetch horizon. Default mirrors the API client's default;
// bounds match the route clamp.
const DEFAULT_TICKETS_LIST_LIMIT = 100

/**
 * Tickets page (JIRA cache, Phase 1).
 *
 * Layout: 3 columns when a ticket is selected, mirroring ReviewsPage:
 *   left  -- queue (enriched ticket rows: key + category + priority +
 *            status chip + updated-ago).
 *   middle -- per-ticket agent session card (mirrors how ReviewsPage
 *            embeds review sessions and CronJobsPage embeds cron-job
 *            sessions). Auto-opens on selection so the terminal is
 *            always visible.
 *   right  -- detail panel: full field table, labels / components /
 *            fix versions / parent crumb, action buttons, linked
 *            tasks (reverse-link via `linked_tasks` so the user can
 *            jump from a ticket back to its tracking task).
 *
 * The page reads `/api/tickets` (cached + enriched) for the queue,
 * and `/api/tickets/{key}` for the selected detail so freshly-synced
 * fields land in the panel without a full page refetch.
 */
interface TicketsPageProps {
  /** Optional callback for clicking a "linked task" inside the
   * detail panel. The host app routes the navigation; we just hand
   * back the (project, task_id) tuple so the page stays standalone-
   * testable. */
  onSelectLinkedTask?: (project: string, taskId: string) => void
  /** When supplied (e.g. via deep-link or a <TicketLink> dispatch),
   * the page auto-selects this ticket on mount or when the prop
   * changes. We refetch the enriched detail in case the cache has
   * fresher fields than the queue snapshot. */
  requestedKey?: string
  requestedInstance?: string
}


export function TicketsPage({
  onSelectLinkedTask, requestedKey, requestedInstance,
}: TicketsPageProps = {}) {
  const [queueRatio, sessionRatio, detailRatio] = useLayoutRatios(
    'ui.layout.tickets_col_ratios',
    [...DEFAULT_TICKETS_RATIOS],
  )
  const listLimit = useSettingNumber(
    'ui.tickets.list_limit', DEFAULT_TICKETS_LIST_LIMIT,
    { min: 10, max: 1000 },
  )
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [configured, setConfigured] = useState<boolean>(true)
  const [instances, setInstances] = useState<Array<{
    name: string; base_url: string; email: string
  }>>([])
  const [error, setError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [selected, setSelected] = useState<Ticket | null>(null)
  // Tabs by assignment + JIRA status_category: Open / In Progress /
  // Resolved are my tickets; Triaged (last) is everything that left
  // my plate (reassigned or unassigned). See utils/ticketBuckets.
  const [tab, setTab] = useState<TicketTab>('open')

  const refresh = useCallback(async () => {
    try {
      const r = await api.listTickets(listLimit)
      setTickets(r.tickets)
      setConfigured(r.configured)
      // Cache the configured-instances list so `+ Add ticket` can
      // do a host-match check up front (saves a backend round trip
      // when the pasted URL belongs to a different JIRA).
      setInstances(r.instances || [])
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    }
  }, [listLimit])

  useEffect(() => { refresh() }, [refresh])

  // Auto-select the requested ticket (deep-link / TicketLink dispatch).
  // We re-fetch through getTicket so the detail pane sees the freshest
  // enrichment even if the queue's row is stale.
  useEffect(() => {
    if (!requestedKey) return
    let cancelled = false
    api.getTicket(requestedKey, requestedInstance).then((fresh) => {
      if (cancelled) return
      setSelected(fresh)
    }).catch(() => {
      // Track may have failed at the link layer; fall through silently.
    })
    return () => { cancelled = true }
  }, [requestedKey, requestedInstance])

  // Subscribe to `ticket.*` events emitted by the JIRA poller. A
  // created/updated event fires once per affected ticket -- we coalesce
  // by just re-fetching the list (cheap; cache is local SQLite) and,
  // when the changed ticket is currently selected, refreshing the
  // detail view too so the right pane never lags the queue.
  useEventBus('ticket.*', useCallback(async (event: Record<string, unknown>) => {
    refresh()
    const key = String(event.ticket_key || event.source_id || '')
    const instance = String(event.instance_name || '')
    if (selected && selected.key === key &&
        (selected.instance_name || '') === instance) {
      try {
        const fresh = await api.getTicket(key, instance || undefined)
        setSelected(fresh)
      } catch { /* row may have just been pruned -- keep stale view */ }
    }
  }, [refresh, selected]))

  const onSync = useCallback(async () => {
    setSyncing(true)
    setError(null)
    try {
      await api.syncTickets()
      await refresh()
      // After a sync, refresh the selected ticket too so its detail
      // panel sees the new fields.
      if (selected) {
        try {
          const fresh = await api.getTicket(
            selected.key, selected.instance_name)
          setSelected(fresh)
        } catch { /* ignore: a deleted ticket just keeps the stale view */ }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sync failed')
    } finally {
      setSyncing(false)
    }
  }, [refresh, selected])

  const onAdd = useCallback(async () => {
    // Prompt the user for a JIRA key (or full URL). Symmetric with
    // the Reviews page "+ Add PR" flow. The backend's
    // `POST /api/tickets/{key}/track` endpoint resolves the right
    // instance + auto-syncs the row, so we just need to extract a
    // bare key and call it.
    const raw = window.prompt(
      'Add a JIRA ticket. Paste a key (EX-1002) or a full JIRA URL.',
    )
    if (!raw) return
    // Extract `<PROJECT>-<NUMBER>` from either a bare key or a URL.
    const m = raw.match(/[A-Z][A-Z0-9_]*-\d+/)
    if (!m) {
      setError('Invalid ticket reference -- expected e.g. EX-1002')
      return
    }
    const key = m[0]
    // Early host-match check: if the user pasted a full URL, see
    // whether ANY configured instance's `base_url` covers that host.
    // Saves a backend round-trip and gives a more actionable error
    // than "not found in any configured JIRA instance".
    const hostInUrl = (() => {
      try { return new URL(raw).host } catch { return '' }
    })()
    if (hostInUrl && instances.length > 0) {
      const matchesConfigured = instances.some((i) => {
        try { return new URL(i.base_url).host === hostInUrl }
        catch { return false }
      })
      if (!matchesConfigured) {
        const configured = instances
          .map((i) => { try { return new URL(i.base_url).host }
                       catch { return i.name } })
          .join(', ')
        setError(
          `${key} lives on ${hostInUrl}, but no configured JIRA `
          + `instance covers that host. Configured: ${configured}. `
          + `Open Settings -> JIRA to add ${hostInUrl}.`,
        )
        return
      }
    }
    setError(null)
    try {
      const ticket = await api.trackTicket(key)
      await refresh()
      // Auto-select the just-added ticket so the user sees the detail
      // panel populate.
      setSelected(ticket)
    } catch (e) {
      setError(e instanceof Error ? e.message : `Add ${key} failed`)
    }
  }, [refresh, instances])

  // Partition every cached ticket into its tab. Counts come from the
  // full list (independent of the active tab) so each tab label can
  // show how many tickets it holds.
  const mine = useMemo(() => myEmails(instances), [instances])
  const byTab = useMemo(() => {
    const out: Record<TicketTab, Ticket[]> = {
      open: [], in_progress: [], resolved: [], triaged: [],
    }
    for (const t of tickets) out[tabForTicket(t, mine)].push(t)
    return out
  }, [tickets, mine])
  const visibleTickets = byTab[tab]

  // Sync-on-open (mirrors the PRs page): one JIRA sync per mount,
  // fired as soon as the instances list confirms JIRA is configured
  // (waiting avoids a spurious 422 when nothing is configured).
  const autoSynced = useRef(false)
  useEffect(() => {
    if (autoSynced.current || instances.length === 0) return
    autoSynced.current = true
    onSync()
  }, [instances, onSync])

  const onSelect = useCallback(async (t: Ticket) => {
    if (selected?.key === t.key && selected?.instance_name === t.instance_name) {
      setSelected(null)
      return
    }
    // Fetch the enriched single-ticket view so the detail panel always
    // sees the freshest linked_tasks / labels / components.
    try {
      const fresh = await api.getTicket(t.key, t.instance_name)
      setSelected(fresh)
    } catch {
      // Server returned 404 (e.g. row was just deleted). Fall back to
      // the row we already have; the user gets stale data instead of a
      // broken page.
      setSelected(t)
    }
  }, [selected])

  return (
    <div data-testid="tickets-page" style={{
      display: 'flex', height: '100%', overflow: 'hidden',
    }}>
      {/* Left: queue. ratio-driven 3-pane split when a ticket is
          open; full width otherwise. The default 30/35/35 mirrors
          ReviewsPage; users can override via the
          `ui.layout.tickets_col_ratios` setting. */}
      <div style={{
        width: selected ? `${queueRatio}%` : '100%',
        overflowY: 'auto', padding: 12, flexShrink: 0,
        transition: 'width 0.2s',
        borderRight: selected ? '1px solid var(--border)' : undefined,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <span style={{ fontSize: 14, fontWeight: 700 }}>Tickets</span>
          <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>
            {`${tickets.length} cached`}
          </span>
          <button
            className="btn-action"
            data-testid="tickets-add"
            onClick={onAdd}
            disabled={!configured}
            title="Manually pin a JIRA ticket by key or URL (e.g. EX-1002)"
            style={{ fontSize: 11, padding: '2px 8px', marginLeft: 'auto' }}
          >+ Add ticket</button>
          <button
            className="btn-action accent"
            data-testid="tickets-sync"
            onClick={onSync}
            disabled={syncing || !configured}
            style={{ fontSize: 11, padding: '2px 8px' }}
          >{syncing ? 'Syncing...' : 'Sync from JIRA'}</button>
        </div>

        {!configured && (
          <div data-testid="tickets-setup-hint" style={{
            border: '1px dashed var(--border)', borderRadius: 6,
            padding: 12, fontSize: 12, color: 'var(--text-dim)',
            marginBottom: 12,
          }}>
            JIRA isn't configured. Open <strong>Settings</strong> and
            add at least one JIRA instance under the <em>JIRA</em> tab
            to start syncing tickets.
          </div>
        )}

        {error && (
          <div style={{ color: 'var(--red)', fontSize: 12, marginBottom: 8 }}>
            {error}
          </div>
        )}

        {configured && tickets.length === 0 && !error && (
          <div style={{ color: 'var(--text-dim)', fontSize: 12 }}>
            No tickets cached yet -- click <em>Sync from JIRA</em>.
          </div>
        )}

        {/* Tabs: Open / In Progress / Resolved are my tickets split
            by JIRA status; Triaged (last) holds reassigned +
            unassigned tickets that left my plate. */}
        {tickets.length > 0 && (
          <div data-testid="tickets-tabs" style={{
            display: 'flex', gap: 4, marginBottom: 12,
            borderBottom: '1px solid var(--border)',
          }}>
            {TICKET_TABS.map(({ key, label }) => (
              <button
                key={key}
                data-testid={`tickets-tab-${key}`}
                onClick={() => setTab(key)}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  padding: '6px 10px', fontSize: 12,
                  fontWeight: tab === key ? 700 : 500,
                  color: tab === key ? 'var(--text)' : 'var(--text-dim)',
                  borderBottom: tab === key
                    ? '2px solid var(--accent)' : '2px solid transparent',
                  marginBottom: -1,
                }}
              >
                {label}{' '}
                <span style={{ color: 'var(--text-faint)', fontWeight: 500 }}>
                  ({byTab[key].length})
                </span>
              </button>
            ))}
          </div>
        )}

        {configured && tickets.length > 0 && visibleTickets.length === 0 && (
          <div data-testid="tickets-empty-tab"
               style={{ color: 'var(--text-dim)', fontSize: 12 }}>
            {`No ${TICKET_TABS.find((t) => t.key === tab)
              ?.label.toLowerCase()} tickets.`}
          </div>
        )}

        <TicketsGroupedList
          tickets={visibleTickets}
          tab={tab}
          selected={selected}
          onSelect={onSelect}
        />
      </div>

      {/* Middle: per-ticket session card. Auto-opens with the
          ticket-INSTANCE-KEY tmux name so the user sees their agent
          session for this ticket the moment they click in. */}
      {selected && (
        <div data-testid="tickets-session-pane" style={{
          width: `${sessionRatio}%`, overflowY: 'auto', padding: 12, flexShrink: 0,
          borderRight: '1px solid var(--border)',
        }}>
          <TicketTaskCard ticket={selected} />
        </div>
      )}

      {/* Right: enriched detail panel. */}
      {selected && (
        <div data-testid="tickets-detail-pane" style={{
          width: `${detailRatio}%`, overflowY: 'auto', padding: 12, flexShrink: 0,
        }}>
          <TicketCard
            ticket={selected}
            onSelectLinkedTask={onSelectLinkedTask}
          />
        </div>
      )}
    </div>
  )
}


function TicketsGroupedList({
  tickets, tab, selected, onSelect,
}: {
  tickets: Ticket[]
  tab: TicketTab
  selected: Ticket | null
  onSelect: (t: Ticket) => void
}) {
  // Group + sort: each bucket carries its sort priority + alphabetic
  // name as a stable secondary key. Within a bucket, tickets sort by
  // updated_at desc (newest first). Kind classification lives in
  // utils/ticketBuckets; the Triaged tab adds an Unassigned group.
  const groups = new Map<string, { tickets: Ticket[]; priority: number }>()
  for (const t of tickets) {
    const { name, priority } = groupForTicket(t, tab)
    let g = groups.get(name)
    if (!g) {
      g = { tickets: [], priority }
      groups.set(name, g)
    }
    g.tickets.push(t)
  }
  for (const [, g] of groups) {
    g.tickets.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
  }
  const ordered = Array.from(groups.entries())
    .sort(([na, ga], [nb, gb]) => {
      if (ga.priority !== gb.priority) return ga.priority - gb.priority
      return na.localeCompare(nb)
    })
  return (
    <div data-testid="tickets-grouped-list"
         style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {ordered.map(([name, g]) => (
        <div key={name} data-testid={`tickets-group-${name.replace(/\s+/g, '-')}`}>
          <div style={{
            fontSize: 11, fontWeight: 700,
            color: 'var(--text-dim)', marginBottom: 4,
            letterSpacing: 0.3,
          }}>
            {name}{' '}
            <span style={{ color: 'var(--text-faint)', fontWeight: 500 }}>
              ({g.tickets.length})
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {g.tickets.map((t) => (
              <TicketNode
                key={`${t.instance_name || ''}:${t.key}`}
                ticket={t}
                active={
                  selected?.key === t.key &&
                  (selected?.instance_name || '') === (t.instance_name || '')
                }
                onClick={() => onSelect(t)}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

