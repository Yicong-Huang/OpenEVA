import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api', () => ({
  api: {
    trackTicket: vi.fn(),
  },
}))

import { api } from '../api'
import { TicketLink, NAVIGATE_EVENT } from '../components/TicketLink'


beforeEach(() => {
  vi.clearAllMocks()
  // Reset URL between tests so pushState assertions are independent.
  window.history.replaceState({}, '', '/')
})


describe('TicketLink', () => {
  it('renders [KEY] by default and uses bare ticket-link testid', () => {
    vi.mocked(api.trackTicket).mockResolvedValue(
      {} as unknown as Awaited<ReturnType<typeof api.trackTicket>>,
    )
    render(<TicketLink ticketKey="EX-1" />)
    const link = screen.getByTestId('ticket-link')
    expect(link.textContent).toBe('[EX-1]')
  })

  it('uses suffixed testid when caller supplies testId prop', () => {
    render(<TicketLink ticketKey="EX-9" testId="custom" />)
    expect(screen.getByTestId('ticket-link-custom')).toBeInTheDocument()
  })

  it('clicking calls trackTicket and dispatches eva:navigate-ticket', async () => {
    vi.mocked(api.trackTicket).mockResolvedValue(
      {} as unknown as Awaited<ReturnType<typeof api.trackTicket>>,
    )
    const navHandler = vi.fn()
    window.addEventListener(NAVIGATE_EVENT, navHandler as EventListener)
    try {
      render(<TicketLink ticketKey="EX-42" />)
      fireEvent.click(screen.getByTestId('ticket-link'))
      await waitFor(() =>
        expect(api.trackTicket).toHaveBeenCalledWith('EX-42', undefined),
      )
      await waitFor(() => expect(navHandler).toHaveBeenCalledTimes(1))
      const ev = navHandler.mock.calls[0][0] as CustomEvent
      expect(ev.detail).toEqual({ key: 'EX-42', instance: undefined })
    } finally {
      window.removeEventListener(NAVIGATE_EVENT, navHandler as EventListener)
    }
  })

  it('writes ?view=tickets&ticket=KEY to URL on click', async () => {
    vi.mocked(api.trackTicket).mockResolvedValue(
      {} as unknown as Awaited<ReturnType<typeof api.trackTicket>>,
    )
    render(<TicketLink ticketKey="EX-1234" instanceName="example" />)
    fireEvent.click(screen.getByTestId('ticket-link'))
    await waitFor(() => {
      const url = new URL(window.location.href)
      expect(url.searchParams.get('view')).toBe('tickets')
      expect(url.searchParams.get('ticket')).toBe('EX-1234')
      expect(url.searchParams.get('ticket_instance')).toBe('example')
    })
  })

  it('falls back to fallbackUrl in a new tab when track fails', async () => {
    vi.mocked(api.trackTicket).mockRejectedValue(new Error('404'))
    const open = vi.fn()
    const origOpen = window.open
    window.open = open as unknown as typeof window.open
    try {
      render(
        <TicketLink ticketKey="GONE-1"
                    fallbackUrl="https://jira.example/browse/GONE-1" />,
      )
      fireEvent.click(screen.getByTestId('ticket-link'))
      await waitFor(() =>
        expect(open).toHaveBeenCalledWith(
          'https://jira.example/browse/GONE-1',
          '_blank', 'noopener,noreferrer',
        ),
      )
    } finally {
      window.open = origOpen
    }
  })

  it('shows red failure styling when track fails and no fallbackUrl', async () => {
    vi.mocked(api.trackTicket).mockRejectedValue(new Error('boom'))
    render(<TicketLink ticketKey="MISS-1" />)
    fireEvent.click(screen.getByTestId('ticket-link'))
    // The link's title attribute and color flip to the failure
    // state for a couple of seconds. Title is the easiest assertion.
    await waitFor(() => {
      expect(screen.getByTestId('ticket-link'))
        .toHaveAttribute('title', expect.stringMatching(/Couldn't track/))
    })
  })

  it('href fallback preserves middle-click open-in-new-tab behaviour', () => {
    render(
      <TicketLink ticketKey="OK-1"
                  fallbackUrl="https://jira.example/browse/OK-1" />,
    )
    expect(screen.getByTestId('ticket-link'))
      .toHaveAttribute('href', 'https://jira.example/browse/OK-1')
  })
})
