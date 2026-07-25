import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { Ticket, PR } from '../api'

// Mock session-status hook (no live session) so the card renders
// without the SSE provider. Return null = no session.
vi.mock('../hooks/SessionStatusProvider', () => ({
  useSessionState: () => null,
}))

const { TicketTaskCard } = await import('../components/TicketTaskCard')

const TICKET = (overrides: Partial<Ticket> = {}): Ticket => ({
  key: 'EX-1', summary: 'a flaky test', description: '',
  status: 'Open', priority: 'Major', issue_type: 'Test Failure',
  project_key: 'EX', assignee_email: '', reporter_email: '',
  url: 'https://j/browse/EX-1', created_at: '', updated_at: '',
  synced_at: '',
  ...overrides,
})

const PR_STUB = (n: number): PR => ({
  number: n, url: `https://gh/pr/${n}`, status: 'open', title: `pr ${n}`,
  ci_status: 'success', review_status: 'approved',
} as PR)

describe('TicketTaskCard history + PRs (shared task model)', () => {
  it('renders the History field when the ticket has history', () => {
    render(<TicketTaskCard ticket={TICKET({
      history: [{ ts: '2026-07-01T10:00:00', text: 'root cause found' }],
    })} />)
    expect(screen.getByText('History')).toBeInTheDocument()
    expect(screen.getByText('root cause found')).toBeInTheDocument()
  })

  it('renders Related PRs when the ticket has PRs', () => {
    render(<TicketTaskCard ticket={TICKET({ prs: [PR_STUB(4321)] })} />)
    expect(screen.getByTestId('ticket-task-prs')).toBeInTheDocument()
    expect(screen.getByText('#4321')).toBeInTheDocument()
  })

  it('omits both when there is no history or PRs', () => {
    render(<TicketTaskCard ticket={TICKET()} />)
    expect(screen.queryByText('History')).not.toBeInTheDocument()
    expect(screen.queryByTestId('ticket-task-prs')).not.toBeInTheDocument()
  })
})
