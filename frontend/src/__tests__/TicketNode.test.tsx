import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { Ticket } from '../api'

// Mock the session-status hook so we control whether this ticket has a
// live agent session, without standing up the whole SSE provider.
const mockState = vi.fn()
vi.mock('../hooks/SessionStatusProvider', () => ({
  useSessionState: (name: string | null | undefined) => mockState(name),
}))

// Import AFTER the mock is registered.
const { TicketNode } = await import('../components/TicketNode')

const TICKET = (overrides: Partial<Ticket> = {}): Ticket => ({
  key: 'EX-1', summary: 'a flaky test', description: '',
  status: 'Open', priority: 'Major', issue_type: 'Test Failure',
  project_key: 'EX', assignee_email: '', reporter_email: '',
  url: 'https://j/browse/EX-1', created_at: '', updated_at: '',
  synced_at: '', session_name: 'ticket-example-EX-1',
  ...overrides,
})

describe('TicketNode live-session indicator', () => {
  it('renders a live dot when the ticket has a live agent session', () => {
    mockState.mockReturnValue({ state: 'thinking' })
    render(<TicketNode ticket={TICKET()} active={false} onClick={() => {}} />)
    expect(screen.getByTestId('ticket-live-EX-1')).toBeInTheDocument()
  })

  it('renders no dot when there is no session', () => {
    mockState.mockReturnValue(undefined)
    render(<TicketNode ticket={TICKET()} active={false} onClick={() => {}} />)
    expect(screen.queryByTestId('ticket-live-EX-1')).toBeNull()
  })

  it('renders no dot when the session is stopped', () => {
    mockState.mockReturnValue({ state: 'stopped' })
    render(<TicketNode ticket={TICKET()} active={false} onClick={() => {}} />)
    expect(screen.queryByTestId('ticket-live-EX-1')).toBeNull()
  })
})

describe('TicketNode triage chip', () => {
  it('shows a Severity chip and hides priority when severity present', () => {
    mockState.mockReturnValue(undefined)
    render(<TicketNode
      ticket={TICKET({ severity: 'SEV1 High', priority: 'Major' })}
      active={false} onClick={() => {}} />)
    expect(screen.getByTestId('ticket-severity-EX-1')).toHaveTextContent('SEV1')
    expect(screen.queryByTestId('ticket-priority-EX-1')).toBeNull()
  })

  it('suppresses the noisy default "Major" priority chip', () => {
    mockState.mockReturnValue(undefined)
    render(<TicketNode ticket={TICKET({ priority: 'Major' })}
      active={false} onClick={() => {}} />)
    expect(screen.queryByTestId('ticket-priority-EX-1')).toBeNull()
  })

  it('still shows a meaningful priority (Blocker)', () => {
    mockState.mockReturnValue(undefined)
    render(<TicketNode ticket={TICKET({ priority: 'Blocker' })}
      active={false} onClick={() => {}} />)
    expect(screen.getByTestId('ticket-priority-EX-1')).toBeInTheDocument()
  })
})
