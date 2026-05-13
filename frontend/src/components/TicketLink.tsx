import { useState, useCallback } from 'react'
import { api } from '../api'

/**
 * Inline link to the Tickets page for one ticket key.
 *
 * Used wherever a ticket id is rendered in another page (TaskCard,
 * PRDetail, etc.) so the user can click `[EX-123]` and land
 * inside the Tickets page with that ticket pre-selected -- not on
 * JIRA itself, where they'd lose Eva context. The user can still
 * jump to the JIRA browse URL from the Ticket detail panel.
 *
 * Behaviour:
 *  - On click: POST /api/tickets/{key}/track to make sure the cache
 *    contains the key. Endpoint cheap when already cached.
 *  - Then dispatch a `CustomEvent('eva:navigate-ticket', {detail:
 *    {key, instance}})` that App listens for. Navigation lives in
 *    App so this component stays drop-in (no callback prop chain).
 *  - Updates the URL via `history.pushState` so refreshing the page
 *    re-lands on the same ticket.
 *
 * If track returns 404 (no JIRA instance recognises the key) we fall
 * back to opening the literal JIRA browse URL when one was provided.
 * That preserves the legacy "click ticket -> open JIRA" UX for keys
 * Eva can't auto-track yet.
 */
interface Props {
  ticketKey: string
  /** Optional fallback browse URL (used when track fails / no JIRA
   * is configured). When omitted and track fails, the click no-ops
   * with an inline error chip. */
  fallbackUrl?: string
  /** Optional explicit instance name. Disambiguates the same key
   * across two configured JIRA instances; usually omitted. */
  instanceName?: string
  /** Inline label override. Defaults to `[KEY]`. */
  children?: React.ReactNode
  /** Test/data-testid suffix; defaults to the ticket key. */
  testId?: string
  style?: React.CSSProperties
  className?: string
}

export const NAVIGATE_EVENT = 'eva:navigate-ticket'


export function TicketLink({
  ticketKey, fallbackUrl, instanceName, children, testId,
  style, className,
}: Props) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)

  const onClick = useCallback(async (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (busy) return
    setBusy(true)
    setFailed(false)
    try {
      // Track may be a no-op if the row is already cached -- the
      // server handles dedupe. We don't need the response body
      // (App + TicketsPage refetch through the navigation flow);
      // we await it so a track failure can fall back to JIRA.
      await api.trackTicket(ticketKey, instanceName)
      // Push state so reload re-lands here AND back-button takes the
      // user to the view they came from (App.tsx uses replaceState
      // everywhere, so the only place that creates a history entry
      // is this click). Build the URL from scratch (NOT merged with
      // window.location.search) so stale params from another view
      // don't leak: e.g. clicking a ticket while on `?view=cron-jobs
      // &cron_job=1` must NOT carry `cron_job` into the new
      // `?view=tickets&ticket=K` URL.
      const params = new URLSearchParams()
      params.set('view', 'tickets')
      params.set('ticket', ticketKey)
      if (instanceName) params.set('ticket_instance', instanceName)
      window.history.pushState(
        {}, '', `${window.location.pathname}?${params.toString()}`,
      )
      window.dispatchEvent(new CustomEvent(NAVIGATE_EVENT, {
        detail: { key: ticketKey, instance: instanceName },
      }))
    } catch {
      // Track failed -- usually JIRA isn't configured or returned
      // 404. Fall back to the legacy JIRA browse URL when caller
      // supplied one; otherwise show an inline failure chip.
      if (fallbackUrl) {
        window.open(fallbackUrl, '_blank', 'noopener,noreferrer')
      } else {
        setFailed(true)
        setTimeout(() => setFailed(false), 2000)
      }
    } finally {
      setBusy(false)
    }
  }, [ticketKey, instanceName, fallbackUrl, busy])

  // The href attribute keeps middle-click "open in new tab" working
  // for users who prefer the JIRA flow; preventDefault on left-click
  // routes the click through the in-app navigation handler.
  const href = fallbackUrl
    || `?view=tickets&ticket=${encodeURIComponent(ticketKey)}`
  // testId='' (the default) preserves the legacy bare `ticket-link`
  // data-testid that pre-Phase-4 tests already assert on. Callers
  // that need a unique selector pass a `testId` like 'task-card'
  // and get `ticket-link-task-card`.
  const dataTestId = testId ? `ticket-link-${testId}` : 'ticket-link'
  return (
    <a
      href={href}
      onClick={onClick}
      data-testid={dataTestId}
      data-ticket-key={ticketKey}
      className={className}
      style={{
        color: failed ? 'var(--red)' : 'var(--accent)',
        textDecoration: 'none',
        cursor: 'pointer',
        ...style,
      }}
      title={failed ? `Couldn't track ${ticketKey}` : `Open ${ticketKey} in Tickets page`}
    >
      {children ?? `[${ticketKey}]`}
    </a>
  )
}
