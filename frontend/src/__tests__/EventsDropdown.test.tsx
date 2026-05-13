import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { EventStatus } from '../components/status/EventStatus'

vi.mock('../hooks/useClickOutside', () => ({
  useClickOutside: vi.fn(),
}))
vi.mock('../hooks/useEventBus', () => ({
  useEventBus: vi.fn(),
}))

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

function mockFetchJson(data: unknown) {
  mockFetch.mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  })
}

beforeEach(() => {
  mockFetch.mockReset()
})

const sampleEvents = [
  { id: 'e1', ts: '2026-04-12', source: 'ci', type: 'github.comment', title: 'Build passed', message: 'PR #42 green', severity: 'info', url: null, read: 0 },
  { id: 'e2', ts: '2026-04-11', source: 'gh', type: 'agent.task_done', title: 'Review requested', message: 'From Alice', severity: 'info', url: null, read: 1 },
]

describe('EventStatus', () => {
  it('renders events list when opened', async () => {
    mockFetchJson({ events: sampleEvents, unread: 1, total: 2 })
    render(<EventStatus />)
    // Open the dropdown
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('Build passed')).toBeInTheDocument()
      expect(screen.getByText('Review requested')).toBeInTheDocument()
    })
  })

  it('shows "Read All" button when unread > 0', async () => {
    mockFetchJson({ events: sampleEvents, unread: 1, total: 2 })
    render(<EventStatus sseUnread={1} />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByTestId('events-read-all')).toBeInTheDocument()
      expect(screen.getByText('Read All')).toBeInTheDocument()
    })
  })

  it('shows badge with unread count from sseUnread prop', () => {
    mockFetchJson({ events: sampleEvents, unread: 0, total: 2 })
    render(<EventStatus sseUnread={3} />)
    expect(screen.getByTestId('events-badge')).toBeInTheDocument()
    expect(screen.getByTestId('events-badge').textContent).toBe('3')
  })

  it('hides badge when no unread events', async () => {
    mockFetchJson({ events: sampleEvents, unread: 0, total: 2 })
    render(<EventStatus sseUnread={0} />)
    await waitFor(() => {
      expect(screen.queryByTestId('events-badge')).not.toBeInTheDocument()
    })
  })

  it('shows No events when list is empty', async () => {
    mockFetchJson({ events: [], unread: 0, total: 0 })
    render(<EventStatus />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('No events')).toBeInTheDocument()
    })
  })

  it('calls markEventsRead when Read All is clicked', async () => {
    mockFetchJson({ events: sampleEvents, unread: 1, total: 2 })
    render(<EventStatus sseUnread={1} />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByTestId('events-read-all')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('events-read-all'))
    await waitFor(() => {
      const calls = mockFetch.mock.calls.map((c: unknown[]) => String(c[0]))
      expect(calls.some((url: string) => url.includes('/api/events/read'))).toBe(true)
    })
  })

  it('calls onOpened callback when dropdown opens', async () => {
    mockFetchJson({ events: sampleEvents, unread: 1, total: 2 })
    const onOpened = vi.fn()
    render(<EventStatus onOpened={onOpened} />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    expect(onOpened).toHaveBeenCalledOnce()
  })

  it('does not show Read All when no unread events', async () => {
    mockFetchJson({ events: sampleEvents, unread: 0, total: 2 })
    render(<EventStatus sseUnread={0} />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('Build passed')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('events-read-all')).not.toBeInTheDocument()
  })

  it('shows event count in header', async () => {
    mockFetchJson({ events: sampleEvents, unread: 0, total: 2 })
    render(<EventStatus />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('(2)')).toBeInTheDocument()
    })
  })

  it('handles fetch error gracefully', async () => {
    mockFetch.mockRejectedValue(new Error('network'))
    render(<EventStatus />)
    // Should render without crashing
    expect(screen.getByTestId('events-topbar')).toBeInTheDocument()
  })

  it('read events have lower opacity', async () => {
    mockFetchJson({ events: sampleEvents, unread: 1, total: 2 })
    render(<EventStatus />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('Build passed')).toBeInTheDocument()
    })
    // The read event (read: 1) is inside an event-row div with opacity 0.5
    const readTitle = screen.getByText('Review requested')
    const readRow = readTitle.closest('[data-testid="event-row"]') as HTMLElement
    expect(readRow.style.opacity).toBe('0.5')
    // The unread event (read: 0) is inside an event-row div with opacity 1
    const unreadTitle = screen.getByText('Build passed')
    const unreadRow = unreadTitle.closest('[data-testid="event-row"]') as HTMLElement
    expect(unreadRow.style.opacity).toBe('1')
  })

  it('shows Historical... button when there are more than 10 displayable events', async () => {
    // Create 12 unique displayable events to exceed MIN_ROWS (10)
    const manyEvents = Array.from({ length: 12 }, (_, i) => ({
      id: `ev-${i}`,
      ts: `2026-04-${String(12 - i).padStart(2, '0')}T00:00:00Z`,
      source: 'gh',
      type: 'github.comment',
      title: `Comment event ${i}`,
      message: `Message ${i}`,
      severity: 'info',
      url: null,
      read: 0,
    }))
    mockFetchJson({ events: manyEvents, unread: 12, total: 12 })
    render(<EventStatus />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByTestId('events-load-more')).toBeInTheDocument()
      expect(screen.getByText('Historical...')).toBeInTheDocument()
    })
  })

  it('clicking Historical... loads more events', async () => {
    const manyEvents = Array.from({ length: 12 }, (_, i) => ({
      id: `ev-${i}`,
      ts: `2026-04-${String(12 - i).padStart(2, '0')}T00:00:00Z`,
      source: 'gh',
      type: 'github.comment',
      title: `Comment event ${i}`,
      message: `Message ${i}`,
      severity: 'info',
      url: null,
      read: 0,
    }))
    mockFetchJson({ events: manyEvents, unread: 12, total: 12 })
    render(<EventStatus />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByTestId('events-load-more')).toBeInTheDocument()
    })
    // Only first 10 event rows visible initially
    expect(screen.getAllByTestId('event-row').length).toBe(10)
    // Click Historical... to load more - mock the subsequent fetch with more events
    mockFetchJson({ events: manyEvents, unread: 12, total: 12 })
    fireEvent.click(screen.getByTestId('events-load-more'))
    await waitFor(() => {
      // After loading more, all 12 events should be visible
      expect(screen.getAllByTestId('event-row').length).toBe(12)
    })
  })

  it('event rows with url field show pointer cursor (navigable)', async () => {
    const eventsWithUrl = [
      { id: 'nav-1', ts: '2026-04-12', source: 'gh', type: 'github.comment', title: 'Navigable event', message: 'Has URL', severity: 'info', url: 'https://github.com/org/repo/pull/1', read: 0 },
      { id: 'nav-2', ts: '2026-04-11', source: 'gh', type: 'github.comment', title: 'Non-navigable event', message: 'No URL', severity: 'info', url: null, read: 0 },
    ]
    mockFetchJson({ events: eventsWithUrl, unread: 2, total: 2 })
    render(<EventStatus />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('Navigable event')).toBeInTheDocument()
    })
    // Event with url should have cursor: pointer
    const navRow = screen.getByText('Navigable event').closest('[data-testid="event-row"]') as HTMLElement
    expect(navRow.style.cursor).toBe('pointer')
    // Event without url should have cursor: default
    const nonNavRow = screen.getByText('Non-navigable event').closest('[data-testid="event-row"]') as HTMLElement
    expect(nonNavRow.style.cursor).toBe('default')
  })

  it('clicking a navigable github event calls markEventsRead and onNavigate', async () => {
    const navigableEvents = [
      { id: 'gh-1', ts: '2026-04-12', source: 'gh', type: 'github.comment', title: 'PR comment', message: 'New comment', severity: 'info', url: 'https://github.com/org/repo/pull/1', read: 0 },
    ]
    mockFetchJson({ events: navigableEvents, unread: 1, total: 1 })
    const onNavigate = vi.fn()
    render(<EventStatus onNavigate={onNavigate} />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('PR comment')).toBeInTheDocument()
    })
    // Click the event row
    fireEvent.click(screen.getByTestId('event-row'))
    await waitFor(() => {
      // Should call markEventsRead with url
      const calls = mockFetch.mock.calls.map((c: unknown[]) => String(c[0]))
      expect(calls.some((u: string) => u.includes('/api/events/read'))).toBe(true)
      // Should call onNavigate with the event
      expect(onNavigate).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'gh-1', type: 'github.comment' })
      )
    })
  })

  it('clicking an agent.needs_permission event calls markEventsRead with session', async () => {
    const agentEvents = [
      { id: 'is-1', ts: '2026-04-12', source: 'agent', type: 'agent.needs_permission', title: 'Agent done: my-session', message: 'Needs input', severity: 'warning', url: null, read: 0 },
    ]
    mockFetchJson({ events: agentEvents, unread: 1, total: 1 })
    const onNavigate = vi.fn()
    render(<EventStatus onNavigate={onNavigate} />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('Agent done: my-session')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('event-row'))
    await waitFor(() => {
      const calls = mockFetch.mock.calls.map((c: unknown[]) => String(c[0]))
      expect(calls.some((u: string) => u.includes('/api/events/read'))).toBe(true)
      expect(onNavigate).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'agent.needs_permission' })
      )
    })
  })

  it('clicking a non-navigable event does not call onNavigate', async () => {
    const nonNavEvents = [
      { id: 'nn-1', ts: '2026-04-12', source: 'gh', type: 'github.assign', title: 'Assigned to you', message: '', severity: 'info', url: null, read: 0 },
    ]
    mockFetchJson({ events: nonNavEvents, unread: 1, total: 1 })
    const onNavigate = vi.fn()
    render(<EventStatus onNavigate={onNavigate} />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('Assigned to you')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('event-row'))
    // Non-navigable: onNavigate should NOT be called
    expect(onNavigate).not.toHaveBeenCalled()
  })

  it('dropdown closes after navigating to an event', async () => {
    const navEvents = [
      { id: 'cl-1', ts: '2026-04-12', source: 'gh', type: 'github.comment', title: 'Close test', message: '', severity: 'info', url: 'https://github.com/x/y/pull/5', read: 0 },
    ]
    mockFetchJson({ events: navEvents, unread: 1, total: 1 })
    const onNavigate = vi.fn()
    render(<EventStatus onNavigate={onNavigate} />)
    // Open dropdown
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => { expect(screen.getByText('Close test')).toBeInTheDocument() })
    // Click the event - dropdown should close
    fireEvent.click(screen.getByTestId('event-row'))
    await waitFor(() => {
      // Dropdown content should no longer be visible
      expect(screen.queryByText('Events')).not.toBeInTheDocument()
    })
  })

  it('shows event severity icons for various event types', async () => {
    const typedEvents = [
      { id: 'sev-1', ts: '2026-04-12', source: 'gh', type: 'github.review_requested', title: 'Review requested', message: '', severity: 'info', url: null, read: 0 },
      { id: 'sev-2', ts: '2026-04-11', source: 'gh', type: 'github.ci_activity', title: 'CI failed', message: '', severity: 'error', url: null, read: 0 },
      { id: 'sev-3', ts: '2026-04-10', source: 'gh', type: 'github.mention', title: 'Mentioned', message: '', severity: 'info', url: null, read: 0 },
      { id: 'sev-4', ts: '2026-04-09', source: 'gh', type: 'slack.boba', title: 'Boba time', message: '', severity: 'info', url: null, read: 0 },
      { id: 'sev-5', ts: '2026-04-08', source: 'gh', type: 'auth.cert_expired', title: 'Cert expired', message: '', severity: 'error', url: null, read: 0 },
      { id: 'sev-6', ts: '2026-04-07', source: 'gh', type: 'auth.cert_expiring', title: 'Cert expiring', message: '', severity: 'warning', url: null, read: 0 },
      { id: 'sev-7', ts: '2026-04-06', source: 'gh', type: 'auth.cert_renewed', title: 'Cert renewed', message: '', severity: 'info', url: null, read: 0 },
      { id: 'sev-8', ts: '2026-04-05', source: 'gh', type: 'slack.message', title: 'Slack msg', message: '', severity: 'info', url: null, read: 0 },
      { id: 'sev-9', ts: '2026-04-04', source: 'gh', type: 'github.state_change', title: 'State change', message: '', severity: 'info', url: null, read: 0 },
      { id: 'sev-10', ts: '2026-04-03', source: 'gh', type: 'unknown.type', title: 'Unknown event', message: '', severity: 'info', url: null, read: 0 },
    ]
    mockFetchJson({ events: typedEvents, unread: 10, total: 10 })
    render(<EventStatus />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('Review requested')).toBeInTheDocument()
      expect(screen.getByText('CI failed')).toBeInTheDocument()
      expect(screen.getByText('Mentioned')).toBeInTheDocument()
      expect(screen.getByText('Boba time')).toBeInTheDocument()
      expect(screen.getByText('Cert expired')).toBeInTheDocument()
      expect(screen.getByText('Cert expiring')).toBeInTheDocument()
      expect(screen.getByText('Cert renewed')).toBeInTheDocument()
      expect(screen.getByText('Slack msg')).toBeInTheDocument()
      expect(screen.getByText('State change')).toBeInTheDocument()
    })
    // All 10 event rows should render (some are not in DISPLAY_TYPES, so unknown.type is filtered out)
    const rows = screen.getAllByTestId('event-row')
    expect(rows.length).toBe(9) // unknown.type is not displayable
  })

  it('groups events with same title+type within 30-min window', async () => {
    const groupedEvents = [
      { id: 'g-1', ts: '2026-04-12T10:00:00Z', source: 'gh', type: 'github.comment', title: 'Same title', message: 'First', severity: 'info', url: null, read: 0 },
      { id: 'g-2', ts: '2026-04-12T10:10:00Z', source: 'gh', type: 'github.comment', title: 'Same title', message: 'Second', severity: 'info', url: null, read: 0 },
      { id: 'g-3', ts: '2026-04-12T10:20:00Z', source: 'gh', type: 'github.comment', title: 'Same title', message: 'Third', severity: 'info', url: null, read: 0 },
    ]
    mockFetchJson({ events: groupedEvents, unread: 3, total: 3 })
    render(<EventStatus />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      // Should be grouped into 1 row with a count badge
      const rows = screen.getAllByTestId('event-row')
      expect(rows.length).toBe(1)
      // The group count badge is inside the event row (not the unread badge)
      const row = rows[0]
      // Count "3" appears in a span inside the row
      expect(row.textContent).toContain('3')
    })
  })

  it('filters out non-displayable event types', async () => {
    const mixedEvents = [
      { id: 'disp-1', ts: '2026-04-12', source: 'gh', type: 'github.comment', title: 'Displayable', message: '', severity: 'info', url: null, read: 0 },
      { id: 'disp-2', ts: '2026-04-11', source: 'sys', type: 'system.heartbeat', title: 'Non-displayable', message: '', severity: 'info', url: null, read: 0 },
    ]
    mockFetchJson({ events: mixedEvents, unread: 1, total: 2 })
    render(<EventStatus />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('Displayable')).toBeInTheDocument()
      // Non-displayable event should not appear in the rows
      expect(screen.queryByText('Non-displayable')).not.toBeInTheDocument()
    })
  })

  it('shows event message below title when present', async () => {
    const msgEvents = [
      { id: 'msg-1', ts: '2026-04-12', source: 'gh', type: 'github.comment', title: 'With message', message: 'Details here', severity: 'info', url: null, read: 0 },
    ]
    mockFetchJson({ events: msgEvents, unread: 1, total: 1 })
    render(<EventStatus />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('Details here')).toBeInTheDocument()
    })
  })

  it('uses apiUnread when sseUnread is not provided', async () => {
    mockFetchJson({ events: sampleEvents, unread: 5, total: 2 })
    render(<EventStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('events-badge')).toBeInTheDocument()
      expect(screen.getByTestId('events-badge').textContent).toBe('5')
    })
  })

  it('refresh button in dropdown refetches events', async () => {
    mockFetchJson({ events: sampleEvents, unread: 1, total: 2 })
    render(<EventStatus />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => { expect(screen.getByText('Build passed')).toBeInTheDocument() })
    const callsBefore = mockFetch.mock.calls.length
    // Click the refresh button (unicode reload)
    const refreshBtn = screen.getByText('\u21BB')
    fireEvent.click(refreshBtn)
    await waitFor(() => {
      expect(mockFetch.mock.calls.length).toBeGreaterThan(callsBefore)
    })
  })

  it('clicking an agent.task_done event navigates', async () => {
    const taskDoneEvents = [
      { id: 'td-1', ts: '2026-04-12', source: 'agent', type: 'agent.task_done', title: 'Agent done: session-x', message: 'Task complete', severity: 'info', url: null, read: 0 },
    ]
    mockFetchJson({ events: taskDoneEvents, unread: 1, total: 1 })
    const onNavigate = vi.fn()
    render(<EventStatus onNavigate={onNavigate} />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => { expect(screen.getByText('Agent done: session-x')).toBeInTheDocument() })
    fireEvent.click(screen.getByTestId('event-row'))
    await waitFor(() => {
      expect(onNavigate).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'agent.task_done' })
      )
    })
  })

  it('clicking a slack.message event with url navigates', async () => {
    const slackEvents = [
      { id: 'sl-1', ts: '2026-04-12', source: 'slack', type: 'slack.message', title: 'Slack msg', message: 'Hey', severity: 'info', url: 'https://slack.com/archives/C1/p2', read: 0 },
    ]
    mockFetchJson({ events: slackEvents, unread: 1, total: 1 })
    const onNavigate = vi.fn()
    render(<EventStatus onNavigate={onNavigate} />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => { expect(screen.getByText('Slack msg')).toBeInTheDocument() })
    fireEvent.click(screen.getByTestId('event-row'))
    await waitFor(() => {
      expect(onNavigate).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'slack.message' })
      )
    })
  })

  it('handles markEventsRead failure gracefully on event click', async () => {
    const navEvents = [
      { id: 'fail-1', ts: '2026-04-12', source: 'gh', type: 'github.comment', title: 'Fail click', message: '', severity: 'info', url: 'https://github.com/x/y/pull/1', read: 0 },
    ]
    // First call returns events, subsequent calls reject
    let callCount = 0
    mockFetch.mockImplementation(() => {
      callCount++
      if (callCount <= 2) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ events: navEvents, unread: 1, total: 1 }),
          text: () => Promise.resolve(JSON.stringify({ events: navEvents, unread: 1, total: 1 })),
        })
      }
      return Promise.reject(new Error('network error'))
    })
    const onNavigate = vi.fn()
    render(<EventStatus onNavigate={onNavigate} />)
    fireEvent.click(screen.getByTestId('events-topbar').querySelector('span')!)
    await waitFor(() => { expect(screen.getByText('Fail click')).toBeInTheDocument() })
    // Click should not crash even if markEventsRead fails
    fireEvent.click(screen.getByTestId('event-row'))
    await waitFor(() => {
      expect(onNavigate).toHaveBeenCalled()
    })
  })
})
