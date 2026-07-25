import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listTickets: vi.fn(),
    getTicket: vi.fn(),
    syncTickets: vi.fn(),
    trackTicket: vi.fn(),
    openTicketSession: vi.fn(),
    postTicketComment: vi.fn(),
    listTicketTransitions: vi.fn(),
    applyTicketTransition: vi.fn(),
    killSession: vi.fn(),
  },
}))

// Mutable session-state map -- TicketTaskCard reads its session
// state from `useSessionState`, so tests inject the desired state
// per session_name here instead of stubbing api.getSessionStatus.
const sessionStateMap: Record<string, { state: string }> = {}
vi.mock('../hooks/SessionStatusProvider', () => ({
  useSessionState: (name: string | null | undefined) => {
    if (!name) return undefined
    return sessionStateMap[name]
  },
  useSessionStatus: () => ({}),
  SessionStatusProvider: ({ children }: { children: React.ReactNode }) => children,
}))

// SessionCard pulls in xterm + the SSE hook -- both are too heavy
// to exercise in jsdom and not what these tests are about. The stub
// keeps the rendered output predictable: just a sentinel div + a
// Kill button that exposes the parent's onKill callback.
vi.mock('../components/SessionCard', () => ({
  SessionCard: ({ sessionName, onKill }: {
    sessionName: string; onKill: () => void
  }) => (
    <div data-testid="session-header">
      {sessionName}
      <button data-testid="ticket-session-kill" onClick={onKill}>Kill</button>
    </div>
  ),
}))

// Capture every useEventBus subscription so individual tests can
// fire `ticket.*` events through the registered handler and assert
// the page reacts (re-fetches list / detail).
const eventBusHandlers: Array<{
  pattern: string
  handler: (e: Record<string, unknown>) => void
}> = []
vi.mock('../hooks/useEventBus', () => ({
  useEventBus: (pattern: string,
                handler: (e: Record<string, unknown>) => void) => {
    eventBusHandlers.push({ pattern, handler })
  },
}))

import { api } from '../api'
import { TicketsPage } from '../pages/TicketsPage'

const TICKET = (overrides = {}) => ({
  key: 'EX-1', summary: 'Fix bug', description: 'details',
  status: 'Open', priority: 'Medium', issue_type: 'Bug',
  project_key: 'EX', assignee_email: 'me@example.com',
  reporter_email: 'pm@example.com',
  url: 'https://j.example/browse/EX-1',
  created_at: '2026-04-25T08:00:00Z',
  updated_at: '2026-04-25T09:00:00Z',
  synced_at: '2026-04-25T10:00:00Z',
  status_category: 'new',
  labels: [],
  ...overrides,
})

// Configured JIRA instances returned by listTickets. The email is
// what tabForTicket compares assignees against ("my" tickets).
const INSTANCES = [{
  name: 'example', base_url: 'https://example.atlassian.net',
  auth_type: 'basic' as const, email: 'me@example.com',
  jql: 'assignee = currentUser()', has_token: true,
}]

beforeEach(() => {
  vi.clearAllMocks()
  eventBusHandlers.length = 0
  vi.mocked(api.listTickets).mockResolvedValue({
    tickets: [], configured: true, instances: INSTANCES,
  })
  // Default `getTicket` echoes whatever the test seeded into listTickets;
  // individual tests override when they need richer enrichment.
  vi.mocked(api.getTicket).mockImplementation(async (key: string) => {
    return TICKET({ key })
  })
  vi.mocked(api.syncTickets).mockResolvedValue({
    count: 0, jql: 'assignee = currentUser()',
  })
  vi.mocked(api.killSession).mockResolvedValue(undefined as unknown as void)
  for (const k of Object.keys(sessionStateMap)) delete sessionStateMap[k]
})


describe('TicketsPage', () => {
  it('renders header and Sync button', async () => {
    render(<TicketsPage />)
    await waitFor(() => expect(api.listTickets).toHaveBeenCalled())
    expect(screen.getByText('Tickets')).toBeInTheDocument()
    expect(screen.getByTestId('tickets-sync')).toBeInTheDocument()
  })

  it('shows the JIRA setup hint when not configured', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [], configured: false, instances: [],
    })
    render(<TicketsPage />)
    expect(await screen.findByTestId('tickets-setup-hint')).toBeInTheDocument()
    // Sync button is disabled until JIRA is configured.
    expect(screen.getByTestId('tickets-sync')).toBeDisabled()
  })

  it('shows empty-state hint when configured but cache is empty', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [], configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    expect(await screen.findByText(/No tickets cached yet/i))
      .toBeInTheDocument()
    // Sync button enabled.
    expect(screen.getByTestId('tickets-sync')).not.toBeDisabled()
  })

  it('lists tickets with key + summary + status pill', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [
        TICKET({ key: 'A-1', summary: 'first', status: 'In Progress' }),
        TICKET({ key: 'A-2', summary: 'second', status: 'Done' }),
      ],
      configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    const row1 = await screen.findByTestId('ticket-row-A-1')
    expect(screen.getByText('A-1')).toBeInTheDocument()
    expect(screen.getByText('first')).toBeInTheDocument()
    // Scope status-pill checks to the rows -- the tab bar itself
    // also contains the text "In Progress".
    expect(within(row1).getByText('In Progress')).toBeInTheDocument()
    expect(within(screen.getByTestId('ticket-row-A-2'))
      .getByText('Done')).toBeInTheDocument()
  })

  it('switching to the Resolved tab shows only my done tickets', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [
        TICKET({ key: 'A-1', summary: 'open one', status: 'In Progress',
                 status_category: 'indeterminate' }),
        TICKET({ key: 'A-2', summary: 'done one', status: 'Done',
                 status_category: 'done' }),
      ],
      configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('tickets-tab-resolved'))
    expect(screen.getByTestId('ticket-row-A-2')).toBeInTheDocument()
    expect(screen.queryByTestId('ticket-row-A-1')).toBeNull()
  })

  it('shows an empty-tab hint when a tab has no tickets', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [
        TICKET({ key: 'A-1', summary: 'open one', status: 'In Progress',
                 status_category: 'indeterminate' }),
      ],
      configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('tickets-tab-resolved'))
    expect(screen.getByTestId('tickets-empty-tab'))
      .toHaveTextContent(/No resolved tickets/i)
  })

  it('opens detail panel when a ticket is clicked', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'X-7' })], configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-X-7'))
    await screen.findByTestId('tickets-detail-pane')
    expect(screen.getByTestId('ticket-open-jira')).toBeInTheDocument()
    expect(screen.getByTestId('ticket-copy-key')).toBeInTheDocument()
  })

  it('clicking the same row again collapses the detail panel', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'X-7' })], configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-X-7'))
    await screen.findByTestId('tickets-detail-pane')
    fireEvent.click(screen.getByTestId('ticket-row-X-7'))
    expect(screen.queryByTestId('tickets-detail-pane')).toBeNull()
  })

  it('uses configurable layout ratios (default 30/35/35) when ticket selected', async () => {
    // The 3-pane layout reads `ui.layout.tickets_col_ratios` via
    // `useLayoutRatios`. We don't mock the fetch -- the hook falls
    // back to the supplied default on any failure / non-OK response,
    // so the assertion verifies the default branch is wired.
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'X-7' })], configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-X-7'))
    const sessionPane = await screen.findByTestId('tickets-session-pane')
    const detailPane = await screen.findByTestId('tickets-detail-pane')
    // Default ratios resolve to 35% for both side panes.
    expect((sessionPane as HTMLElement).style.width).toBe('35%')
    expect((detailPane as HTMLElement).style.width).toBe('35%')
  })

  it('Sync button calls api.syncTickets and refreshes the list', async () => {
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('tickets-sync'))
    await waitFor(() => expect(api.syncTickets).toHaveBeenCalled())
    // listTickets called twice: initial + post-sync refresh.
    await waitFor(() =>
      expect(vi.mocked(api.listTickets).mock.calls.length).toBeGreaterThanOrEqual(2))
  })

  it('Sync error surfaces in the UI', async () => {
    vi.mocked(api.syncTickets).mockRejectedValue(new Error('502: bad jira'))
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('tickets-sync'))
    expect(await screen.findByText(/502: bad jira/i)).toBeInTheDocument()
  })

  it('Open session button calls api.openTicketSession (the SessionCard then takes over)', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'SES-1' })], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.openTicketSession).mockResolvedValue({
      session: 'ticket-SES-1', new: true, ticket_key: 'SES-1',
    })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-SES-1'))
    fireEvent.click(await screen.findByTestId('ticket-open-session'))
    await waitFor(() =>
      expect(api.openTicketSession).toHaveBeenCalledWith(
        'SES-1', { instanceName: undefined }))
    // Result UI moved to the embedded SessionCard (SSE-driven, mocked
    // away in jsdom). What we still own here is the API call shape.
  })

  it('hides SessionCard when no tmux session exists; only the Open button is shown', async () => {
    // No entry in the snapshot map -> useSessionState returns
    // undefined -> TicketTaskCard treats present as null/false and
    // shows the Open Agent button only.
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'NEW-1' })], configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-NEW-1'))
    expect(await screen.findByTestId('ticket-open-session')).toBeInTheDocument()
    // SessionCard's header (data-testid='session-header') must NOT be
    // present until the snapshot reports a live session.
    expect(screen.queryByTestId('session-header')).toBeNull()
  })

  it('renders SessionCard immediately when a tmux session already exists for the ticket', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'EXIST-1' })], configured: true, instances: INSTANCES,
    })
    // Snapshot says this ticket's session is alive and idle.
    sessionStateMap['ticket-EXIST-1'] = { state: 'idle' }
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-EXIST-1'))
    // SessionCard renders -> 'session-header' shows up. The Open
    // button is suppressed because we're already attached.
    expect(await screen.findByTestId('session-header')).toBeInTheDocument()
    expect(screen.queryByTestId('ticket-open-session')).toBeNull()
  })

  it('Open session error surfaces to the user', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'SES-2' })], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.openTicketSession).mockRejectedValue(
      new Error('404: ticket not found in cache'))
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-SES-2'))
    fireEvent.click(await screen.findByTestId('ticket-open-session'))
    expect(await screen.findByTestId('ticket-session-error'))
      .toHaveTextContent(/not found/i)
  })

  it('reverse-links to tasks tracking the ticket; clicking calls onSelectLinkedTask', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'LNK-1' })], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.getTicket).mockResolvedValue(TICKET({
      key: 'LNK-1',
      linked_tasks: [
        { project: 'oss-repo', task_id: 'repo-99' },
      ],
    }) as unknown as Awaited<ReturnType<typeof api.getTicket>>)
    const onSelectLinkedTask = vi.fn()
    render(<TicketsPage onSelectLinkedTask={onSelectLinkedTask} />)
    fireEvent.click(await screen.findByTestId('ticket-row-LNK-1'))
    const linkBtn = await screen.findByTestId('ticket-linked-task-repo-99')
    fireEvent.click(linkBtn)
    expect(onSelectLinkedTask).toHaveBeenCalledWith('oss-repo', 'repo-99')
  })

  it('renders enrichment chips: labels / components / fix versions', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'CHIP-1' })], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.getTicket).mockResolvedValue(TICKET({
      key: 'CHIP-1',
      labels: ['flaky-test', 'ci'],
      components: ['Example'],
      fix_versions: ['4.2.0'],
    }) as unknown as Awaited<ReturnType<typeof api.getTicket>>)
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-CHIP-1'))
    await screen.findByTestId('ticket-chips')
    expect(screen.getByTestId('ticket-chip-label-flaky-test')).toBeInTheDocument()
    expect(screen.getByTestId('ticket-chip-label-ci')).toBeInTheDocument()
    expect(screen.getByTestId('ticket-chip-component-Example')).toBeInTheDocument()
    expect(screen.getByTestId('ticket-chip-version-4.2.0')).toBeInTheDocument()
  })

  it('row surfaces issue type, priority, and meta crumbs (assignee/components/parent)', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [
        TICKET({
          key: 'EX-1234',
          issue_type: 'Bug',
          priority: 'High',
          assignee_email: 'me@example.com',
          components: ['streaming', 'connect', 'rdd'],
          parent_key: 'EX-1000',
        }),
        TICKET({
          key: 'INTKEY-5',
          issue_type: 'Story',
          priority: 'P2',
          assignee_email: '',
          components: [],
          parent_key: '',
        }),
      ],
      configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    // Type chip is the new at-a-glance signal -- replaces the old
    // duplicate "category prefix" pill (the project prefix is
    // already in the key).
    expect(await screen.findByTestId('ticket-type-EX-1234'))
      .toHaveTextContent('Bug')
    // Priority compresses to P0/P1/P2... so a long "High" -> "P1".
    expect(screen.getByTestId('ticket-priority-EX-1234'))
      .toHaveTextContent('P1')
    // Meta row crumbs only render when populated.
    expect(screen.getByTestId('ticket-assignee-EX-1234'))
      .toHaveTextContent('@me')
    expect(screen.getByTestId('ticket-components-EX-1234'))
      .toHaveTextContent(/streaming, connect\+/)  // "+" indicates overflow
    expect(screen.getByTestId('ticket-parent-EX-1234'))
      .toHaveTextContent('EX-1000')
    // The unassigned Story row lives on the Triaged tab now.
    fireEvent.click(screen.getByTestId('tickets-tab-triaged'))
    expect(await screen.findByTestId('ticket-type-INTKEY-5'))
      .toHaveTextContent('Story')
    // P-style strings pass through unchanged.
    expect(screen.getByTestId('ticket-priority-INTKEY-5'))
      .toHaveTextContent('P2')
    // The Story row has no assignee / no components / no parent ->
    // the meta row is skipped entirely.
    expect(screen.queryByTestId('ticket-meta-INTKEY-5')).toBeNull()
  })

  it('comment box posts to api.postTicketComment', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'CMT-1' })], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.postTicketComment).mockResolvedValue({ id: '99' })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-CMT-1'))
    const input = await screen.findByTestId('ticket-comment-input')
    fireEvent.change(input, { target: { value: 'looks good' } })
    fireEvent.click(screen.getByTestId('ticket-comment-submit'))
    await waitFor(() =>
      expect(api.postTicketComment).toHaveBeenCalledWith(
        'CMT-1', 'looks good', undefined,
      ),
    )
    expect(await screen.findByTestId('ticket-comment-posted'))
      .toHaveTextContent(/Posted/i)
  })

  it('comment submit blocks empty body', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'CMT-2' })], configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-CMT-2'))
    const submit = await screen.findByTestId('ticket-comment-submit')
    expect(submit).toBeDisabled()
  })

  it('comment error surfaces to user', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'CMT-3' })], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.postTicketComment).mockRejectedValue(
      new Error('502: jira down'))
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-CMT-3'))
    const input = await screen.findByTestId('ticket-comment-input')
    fireEvent.change(input, { target: { value: 'hi' } })
    fireEvent.click(screen.getByTestId('ticket-comment-submit'))
    expect(await screen.findByTestId('ticket-comment-error'))
      .toHaveTextContent(/jira down/i)
  })

  it('Resolve dropdown loads transitions on click and applies one', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'TRN-1' })], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.listTicketTransitions).mockResolvedValue({
      transitions: [
        { id: '21', name: 'Resolved' },
        { id: '31', name: 'Closed' },
      ],
    })
    vi.mocked(api.applyTicketTransition).mockResolvedValue(undefined)
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-TRN-1'))
    fireEvent.click(await screen.findByTestId('ticket-transitions-load'))
    await waitFor(() =>
      expect(api.listTicketTransitions).toHaveBeenCalledWith(
        'TRN-1', undefined),
    )
    fireEvent.click(await screen.findByTestId('ticket-transition-21'))
    await waitFor(() =>
      expect(api.applyTicketTransition).toHaveBeenCalledWith(
        'TRN-1', '21', expect.objectContaining({})),
    )
    expect(await screen.findByTestId('ticket-transition-done'))
      .toHaveTextContent(/Resolved/i)
  })

  it('subscribes to ticket.* events and re-fetches the list', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'EVT-1' })], configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    await waitFor(() => expect(api.listTickets).toHaveBeenCalledTimes(1))
    expect(eventBusHandlers.find((h) => h.pattern === 'ticket.*'))
      .toBeTruthy()
    // Simulate a sync emitting ticket.updated.
    const handler = eventBusHandlers.find(
      (h) => h.pattern === 'ticket.*')!.handler
    handler({ ticket_key: 'EVT-1', changes: ['status'] })
    await waitFor(() =>
      expect(api.listTickets).toHaveBeenCalledTimes(2))
  })

  // Helper: each render re-registers the handler with the current
  // closed-over state. Tests want the latest one.
  const latestTicketHandler = () => {
    const matches = eventBusHandlers.filter((h) => h.pattern === 'ticket.*')
    return matches[matches.length - 1].handler
  }

  it('event for selected ticket also refreshes the detail panel', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'SEL-1', instance_name: 'primary' })],
      configured: true, instances: INSTANCES,
    })
    // getTicket must echo the instance_name so the page's
    // "is this the selected ticket?" check passes when the event
    // names it explicitly.
    vi.mocked(api.getTicket).mockImplementation(
      async (key: string, instance?: string) =>
        TICKET({ key, instance_name: instance ?? 'primary' }) as
        unknown as Awaited<ReturnType<typeof api.getTicket>>,
    )
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-SEL-1'))
    await waitFor(() => expect(api.getTicket).toHaveBeenCalledTimes(1))
    latestTicketHandler()({
      ticket_key: 'SEL-1', instance_name: 'primary',
      changes: ['status'],
    })
    // 2 = the click + the event-driven refresh.
    await waitFor(() => expect(api.getTicket).toHaveBeenCalledTimes(2))
  })

  it('event for a different ticket does not re-fetch the open detail', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'KEEP-1' })], configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-KEEP-1'))
    await waitFor(() => expect(api.getTicket).toHaveBeenCalledTimes(1))
    // Event for some OTHER ticket -- list refreshes but detail stays.
    latestTicketHandler()({ ticket_key: 'OTHER-99', changes: ['status'] })
    // Wait long enough for any spurious detail-fetch to fire. Three
    // listTickets calls: initial + auto-sync refresh + event refresh.
    await waitFor(() => expect(api.listTickets).toHaveBeenCalledTimes(3))
    expect(api.getTicket).toHaveBeenCalledTimes(1)
  })

  it('renders type-aware action buttons matching the ticket', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'FLK-1', labels: ['flaky-test'] })],
      configured: true, instances: INSTANCES,
    })
    vi.mocked(api.getTicket).mockResolvedValue(TICKET({
      key: 'FLK-1', labels: ['flaky-test'],
    }) as unknown as Awaited<ReturnType<typeof api.getTicket>>)
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-FLK-1'))
    // Flaky-test action shows up; perf-regression does not.
    expect(await screen.findByTestId('ticket-action-fix-flaky-test'))
      .toBeInTheDocument()
    expect(screen.queryByTestId('ticket-action-bisect-perf-regression'))
      .not.toBeInTheDocument()
    // Generic Investigate fallback always shows.
    expect(screen.getByTestId('ticket-action-investigate'))
      .toBeInTheDocument()
  })

  it('clicking an action POSTs to /session with custom_prompt', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'FLK-2', labels: ['flaky-test'],
                          summary: 'test_arrow flaky' })],
      configured: true, instances: INSTANCES,
    })
    vi.mocked(api.getTicket).mockResolvedValue(TICKET({
      key: 'FLK-2', labels: ['flaky-test'],
      summary: 'test_arrow flaky',
    }) as unknown as Awaited<ReturnType<typeof api.getTicket>>)
    vi.mocked(api.openTicketSession).mockResolvedValue({
      session: 'ticket-FLK-2', new: true,
      ticket_key: 'FLK-2', prompt_sent: true,
    })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-FLK-2'))
    fireEvent.click(await screen.findByTestId('ticket-action-fix-flaky-test'))
    await waitFor(() => {
      expect(api.openTicketSession).toHaveBeenCalledWith(
        'FLK-2',
        expect.objectContaining({
          customPrompt: expect.stringContaining('FLK-2'),
        }),
      )
    })
  })

  it('Copy key writes to clipboard', async () => {
    const writeText = vi.fn()
    Object.assign(navigator, { clipboard: { writeText } })
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET({ key: 'CPY-1' })], configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-CPY-1'))
    fireEvent.click(await screen.findByTestId('ticket-copy-key'))
    expect(writeText).toHaveBeenCalledWith('CPY-1')
  })

  it('list-fetch error surfaces via the inline error banner', async () => {
    vi.mocked(api.listTickets).mockRejectedValueOnce(
      new Error('jira down: 503'),
    )
    render(<TicketsPage />)
    expect(await screen.findByText(/jira down: 503/)).toBeInTheDocument()
  })

  it('list-fetch error has a non-Error fallback message', async () => {
    // Real production paths sometimes throw non-Error values (e.g. a
    // string or POJO). The page must still render *something*.
    vi.mocked(api.listTickets).mockRejectedValueOnce('out of sync')
    render(<TicketsPage />)
    expect(await screen.findByText(/Failed to load/)).toBeInTheDocument()
  })

  it('requestedKey deep-link auto-selects via getTicket', async () => {
    // TicketLink dispatch path: App passes `requestedKey` (and optional
    // `requestedInstance`); the page fetches that single ticket and
    // pre-selects its detail panel without waiting for a click.
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [], configured: true, instances: INSTANCES,
    })
    // Persistent (not Once): the mount auto-sync re-fetches the
    // selected ticket after syncing, so getTicket fires twice.
    vi.mocked(api.getTicket).mockResolvedValue(
      TICKET({ key: 'DEEP-99', summary: 'deep-linked' }),
    )
    render(<TicketsPage requestedKey="DEEP-99" requestedInstance="prod" />)
    // Detail pane appears with the requested ticket loaded.
    expect(await screen.findByTestId('tickets-detail-pane'))
      .toBeInTheDocument()
    // Summary renders in BOTH the middle Task Card (TicketTaskCard) and
    // the right Ticket Card detail title -- one ticket, two surfaces.
    expect(screen.getAllByText('deep-linked').length).toBeGreaterThan(0)
    // Make sure the request was made for the right (key, instance).
    expect(api.getTicket).toHaveBeenCalledWith('DEEP-99', 'prod')
  })

  it('requestedKey path swallows getTicket failure silently', async () => {
    // If the track at the link layer failed (or the row's been
    // pruned), getTicket throws -- the page must NOT crash; just
    // fall through with no auto-selection.
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.getTicket).mockRejectedValueOnce(new Error('404'))
    render(<TicketsPage requestedKey="GHOST-1" />)
    // List loads but no detail pane.
    await waitFor(() => expect(api.listTickets).toHaveBeenCalled())
    // Give the catch a tick.
    await new Promise(r => setTimeout(r, 10))
    expect(screen.queryByTestId('tickets-detail-pane')).toBeNull()
  })

  it('Sync refreshes the currently-selected ticket detail', async () => {
    // The first listTickets returns the row; we click to select; then
    // hitting Sync must call getTicket(selected.key) again so the
    // detail pane reflects fresh enrichment.
    const t = TICKET({ key: 'SYNC-1' })
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [t], configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-SYNC-1'))
    await screen.findByTestId('tickets-detail-pane')
    vi.mocked(api.getTicket).mockClear()
    fireEvent.click(screen.getByTestId('tickets-sync'))
    // The TICKET fixture has no `instance_name` so the call passes
    // undefined for the second arg. The point of the test is the
    // KEY round-trip (the detail panel re-fetches the same row) --
    // not what instance string flows through.
    await waitFor(() =>
      expect(api.getTicket).toHaveBeenCalledWith('SYNC-1', undefined))
  })

  it('+ Add ticket prompts for a key, calls trackTicket, and selects it', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [], configured: true, instances: INSTANCES,
    })
    const newTicket = TICKET({ key: 'EX-1002', summary: 'flaky test' })
    vi.mocked(api.trackTicket).mockResolvedValue(newTicket)
    // Stub window.prompt so the test doesn't open a real prompt.
    const origPrompt = window.prompt
    window.prompt = vi.fn(() => 'EX-1002')
    try {
      render(<TicketsPage />)
      const btn = await screen.findByTestId('tickets-add')
      fireEvent.click(btn)
      await waitFor(() => expect(api.trackTicket).toHaveBeenCalledWith('EX-1002'))
      // After add, refresh + select -> the detail pane shows the new row.
      expect(await screen.findByTestId('tickets-detail-pane'))
        .toBeInTheDocument()
    } finally {
      window.prompt = origPrompt
    }
  })

  it('+ Add ticket extracts a JIRA key from a full URL', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.trackTicket).mockResolvedValue(
      TICKET({ key: 'MYPROJ-1886463' }),
    )
    const origPrompt = window.prompt
    window.prompt = vi.fn(() =>
      'https://example.atlassian.net/browse/MYPROJ-1886463')
    try {
      render(<TicketsPage />)
      fireEvent.click(await screen.findByTestId('tickets-add'))
      await waitFor(() => expect(api.trackTicket).toHaveBeenCalledWith('MYPROJ-1886463'))
    } finally {
      window.prompt = origPrompt
    }
  })

  it('+ Add ticket surfaces an inline error when the input has no key', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [], configured: true, instances: INSTANCES,
    })
    const origPrompt = window.prompt
    window.prompt = vi.fn(() => 'not a ticket reference')
    try {
      render(<TicketsPage />)
      // Let the mount auto-sync settle first -- its setError(null)
      // would otherwise race the add-error banner below.
      await waitFor(() => expect(api.syncTickets).toHaveBeenCalled())
      fireEvent.click(await screen.findByTestId('tickets-add'))
      // The page surfaces the error banner; the trackTicket API
      // should NOT be called.
      await waitFor(() => {
        expect(screen.getByText(/Invalid ticket reference/i))
          .toBeInTheDocument()
      })
      expect(api.trackTicket).not.toHaveBeenCalled()
    } finally {
      window.prompt = origPrompt
    }
  })

  it('Triage button fetches /api/tickets/<key>/triage and renders the report', async () => {
    // Wire the api.getTicket to return a ticket that has the test
    // key. The TriagePanel hits the bare `/api/tickets/.../triage`
    // endpoint via fetch (not via api.ts), so we stub global fetch
    // for that one call.
    const t = TICKET({ key: 'EX-1', summary: 'flaky test',
                       instance_name: 'primary' })
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [t], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.getTicket).mockResolvedValue(t)

    const triageBody = {
      ticket: { key: 'EX-1', summary: 'flaky test', status: 'Open',
                priority: 'Major', issue_type: 'Bug', url: '',
                instance_name: 'primary' },
      problem: 'Stack trace here',
      owner: { assignee: 'alice@example.com', reporter: 'bob@example.com',
               components: ['SQL Core'], labels: ['flaky'],
               project_key: 'EX' },
      files_referenced: ['src/Foo.py'],
      blame: [],
      similar_tickets: [],
      most_likely_owner_team: 'SQL Core',
      most_likely_test_owner: 'alice@example.com',
    }
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url)
      if (u.includes('/api/tickets/EX-1/triage')) {
        return new Response(JSON.stringify(triageBody), { status: 200 })
      }
      return new Response('', { status: 404 })
    }) as typeof fetch

    try {
      render(<TicketsPage />)
      fireEvent.click(await screen.findByTestId('ticket-row-EX-1'))
      // Triage button is in the detail pane, defaulting to "Run triage".
      const btn = await screen.findByTestId('triage-run-btn')
      expect(btn.textContent).toMatch(/Run triage/i)
      fireEvent.click(btn)
      // Report renders with the assignee, components, files.
      const report = await screen.findByTestId('triage-report')
      expect(report).toBeInTheDocument()
      expect(screen.getByTestId('triage-assignee').textContent)
        .toContain('alice@example.com')
      expect(screen.getByTestId('triage-components').textContent)
        .toContain('SQL Core')
      expect(screen.getByTestId('triage-files').textContent)
        .toContain('src/Foo.py')
    } finally {
      globalThis.fetch = origFetch
    }
  })

  it('Triage renders Phase-3 fields: most-likely team, owner, similar tickets', async () => {
    const t = TICKET({ key: 'EX-9', instance_name: 'primary' })
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [t], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.getTicket).mockResolvedValue(t)
    const triageBody = {
      ticket: { key: 'EX-9', summary: 'flaky', status: 'Open',
                priority: 'Major', issue_type: 'Bug', url: '',
                instance_name: 'primary' },
      problem: 'stack trace',
      owner: { assignee: '', reporter: 'auto@x',
               components: [], labels: [], project_key: 'EX' },
      files_referenced: ['widget/foo'],
      blame: [],
      similar_tickets: [
        { key: 'EX-100', summary: 'same flaky test, last week',
          status: 'Done', assignee_email: 'bob@x',
          components: ['Widget Backend'], created_at: '2026-04-22T00:00:00Z',
          url: 'https://j/browse/EX-100' },
        { key: 'EX-99', summary: 'same flaky test, longer ago',
          status: 'Done', assignee_email: 'bob@x',
          components: ['Widget Backend'], created_at: '2026-04-15T00:00:00Z',
          url: 'https://j/browse/EX-99' },
      ],
      most_likely_owner_team: 'Widget Backend',
      most_likely_test_owner: 'bob@x',
    }
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify(triageBody), { status: 200 }),
    ) as typeof fetch
    try {
      render(<TicketsPage />)
      fireEvent.click(await screen.findByTestId('ticket-row-EX-9'))
      fireEvent.click(await screen.findByTestId('triage-run-btn'))
      // The two synthesised top picks render in their dedicated cells.
      expect((await screen.findByTestId('triage-likely-team')).textContent)
        .toContain('Widget Backend')
      expect((await screen.findByTestId('triage-likely-owner')).textContent)
        .toContain('bob@x')
      // Similar-tickets list shows both rows.
      const similar = await screen.findByTestId('triage-similar')
      expect(similar.textContent).toContain('EX-100')
      expect(similar.textContent).toContain('EX-99')
      expect(similar.textContent).toContain('bob@x')
    } finally {
      globalThis.fetch = origFetch
    }
  })

  it('Triage report renders git-blame rows when backend returns non-empty blame', async () => {
    const t = TICKET({ key: 'EX-3', instance_name: 'primary' })
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [t], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.getTicket).mockResolvedValue(t)

    const triageBody = {
      ticket: { key: 'EX-3', summary: 'flaky', status: 'Open',
                priority: 'Major', issue_type: 'Bug', url: '',
                instance_name: 'primary' },
      problem: 'Stack trace',
      owner: { assignee: 'a@x', reporter: 'b@x',
               components: [], labels: [], project_key: 'EX' },
      files_referenced: ['sql/Foo.py'],
      blame: [{
        file: 'sql/Foo.py',
        repo: 'acme/widget',
        local_path: '/home/alice/widget',
        author_name: 'Alice Doe',
        author_email: 'alice@example.com',
        commit: 'deadbee',
        committed_at: '2026-04-25T08:00:00Z',
        subject: 'fix: tighten foo retry',
      }],
      similar_tickets: [],
      most_likely_owner_team: '',
      most_likely_test_owner: '',
    }
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url)
      if (u.includes('/api/tickets/EX-3/triage')) {
        return new Response(JSON.stringify(triageBody), { status: 200 })
      }
      return new Response('', { status: 404 })
    }) as typeof fetch
    try {
      render(<TicketsPage />)
      fireEvent.click(await screen.findByTestId('ticket-row-EX-3'))
      fireEvent.click(await screen.findByTestId('triage-run-btn'))
      const blame = await screen.findByTestId('triage-blame')
      expect(blame.textContent).toContain('deadbee')
      expect(blame.textContent).toContain('Alice Doe')
      expect(blame.textContent).toContain('2026-04-25')
      expect(blame.textContent).toContain('sql/Foo.py')
      expect(blame.textContent).toContain('fix: tighten foo retry')
    } finally {
      globalThis.fetch = origFetch
    }
  })

  it('Triage button surfaces a backend error inline (no crash)', async () => {
    const t = TICKET({ key: 'EX-2', instance_name: 'primary' })
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [t], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.getTicket).mockResolvedValue(t)
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'simulated outage' }),
                   { status: 500 }),
    ) as typeof fetch
    try {
      render(<TicketsPage />)
      fireEvent.click(await screen.findByTestId('ticket-row-EX-2'))
      fireEvent.click(await screen.findByTestId('triage-run-btn'))
      const err = await screen.findByTestId('triage-error')
      expect(err.textContent).toContain('simulated outage')
    } finally {
      globalThis.fetch = origFetch
    }
  })

  it('clicking row swallows getTicket failure (falls back to row data)', async () => {
    // Row open should never break the page. If the enrichment GET
    // returns 404 (e.g. the row was just deleted server-side), we
    // fall back to the row we already have in `tickets`.
    const t = TICKET({ key: 'STALE-1', summary: 'stale row' })
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [t], configured: true, instances: INSTANCES,
    })
    vi.mocked(api.getTicket).mockRejectedValueOnce(new Error('404'))
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('ticket-row-STALE-1'))
    // Detail pane STILL renders -- with the row's existing data.
    expect(await screen.findByTestId('tickets-detail-pane'))
      .toBeInTheDocument()
    // Multiple matches expected (queue list cell + detail header);
    // both reflect the same source row, which is the contract.
    expect(screen.getAllByText(/stale row/).length).toBeGreaterThan(0)
  })
})

describe('TicketsPage tabs + auto-sync', () => {
  it('auto-syncs once on mount when instances are configured', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [TICKET()], configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    await waitFor(() => expect(api.syncTickets).toHaveBeenCalledTimes(1))
  })

  it('does not auto-sync when no instances are configured', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [], configured: false, instances: [],
    })
    render(<TicketsPage />)
    await waitFor(() => expect(api.listTickets).toHaveBeenCalled())
    expect(api.syncTickets).not.toHaveBeenCalled()
  })

  it('renders four tabs in order with counts; Open is default', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [
        TICKET({ key: 'EX-1' }),                                   // open
        TICKET({ key: 'EX-2', status_category: 'indeterminate' }), // in progress
        TICKET({ key: 'EX-3', status_category: 'done' }),          // resolved
        TICKET({ key: 'EX-4', assignee_email: 'other@x.com' }),    // triaged
        TICKET({ key: 'EX-5', assignee_email: '' }),               // triaged (unassigned)
      ],
      configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    const tabs = await screen.findByTestId('tickets-tabs')
    const buttons = tabs.querySelectorAll('button')
    expect(Array.from(buttons).map((b) => b.textContent))
      .toEqual(['Open (1)', 'In Progress (1)', 'Resolved (1)', 'Triaged (2)'])
    // Default tab is Open -> only EX-1 visible.
    expect(screen.getByText('EX-1')).toBeInTheDocument()
    expect(screen.queryByText('EX-4')).not.toBeInTheDocument()
  })

  it('triaged tab shows Unassigned group above kind groups', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [
        TICKET({ key: 'EX-4', assignee_email: 'other@x.com' }),
        TICKET({ key: 'EX-5', assignee_email: '' }),
      ],
      configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    fireEvent.click(await screen.findByTestId('tickets-tab-triaged'))
    const list = await screen.findByTestId('tickets-grouped-list')
    expect(screen.getByTestId('tickets-group-Unassigned'))
      .toBeInTheDocument()
    // Unassigned renders before the kind groups.
    const groupIds = Array.from(
      list.querySelectorAll('[data-testid^="tickets-group-"]'),
    ).map((el) => el.getAttribute('data-testid'))
    expect(groupIds[0]).toBe('tickets-group-Unassigned')
    expect(screen.getByText('EX-5')).toBeInTheDocument()
  })

  it('groups a tab: semantic groups first, then by project prefix', async () => {
    vi.mocked(api.listTickets).mockResolvedValue({
      tickets: [
        TICKET({ key: 'INTKEY-1' }),
        TICKET({ key: 'EX-10', labels: ['testman-automation'] }),
        TICKET({ key: 'EX-11', labels: ['benchmarking-regression'] }),
        TICKET({ key: 'EX-12' }),
      ],
      configured: true, instances: INSTANCES,
    })
    render(<TicketsPage />)
    const list = await screen.findByTestId('tickets-grouped-list')
    const groupIds = Array.from(
      list.querySelectorAll('[data-testid^="tickets-group-"]'),
    ).map((el) => el.getAttribute('data-testid'))
    // Flaky (prio 1) -> Performance (prio 2) -> prefix groups (prio 3,
    // alphabetic): EX before INTKEY. No prefix is special-cased.
    expect(groupIds).toEqual([
      'tickets-group-Flaky-tests', 'tickets-group-Performance',
      'tickets-group-EX', 'tickets-group-INTKEY',
    ])
  })
})
