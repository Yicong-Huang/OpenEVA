import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { Ticket, PR } from '../api'
import { TicketCard } from '../components/TicketCard'

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

describe('TicketCard history + PRs (shared task model)', () => {
  it('renders the History section when the ticket has history', () => {
    render(<TicketCard ticket={TICKET({
      history: [{ ts: '2026-07-01T10:00:00', text: 'started digging' }],
    })} />)
    expect(screen.getByTestId('ticket-history')).toBeInTheDocument()
    expect(screen.getByText('started digging')).toBeInTheDocument()
  })

  it('renders the Related PRs section when the ticket has PRs', () => {
    render(<TicketCard ticket={TICKET({ prs: [PR_STUB(777)] })} />)
    expect(screen.getByTestId('ticket-prs')).toBeInTheDocument()
    expect(screen.getByText('#777')).toBeInTheDocument()
  })

  it('omits both sections when there is no history or PRs', () => {
    render(<TicketCard ticket={TICKET()} />)
    expect(screen.queryByTestId('ticket-history')).not.toBeInTheDocument()
    expect(screen.queryByTestId('ticket-prs')).not.toBeInTheDocument()
  })
})
