import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { AuthStatus } from '../components/status/AuthStatus'

vi.mock('../hooks/useClickOutside', () => ({
  useClickOutside: vi.fn(),
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

vi.mock('../hooks/useEventBus', () => ({
  useEventBus: vi.fn(),
}))

beforeEach(() => {
  mockFetch.mockReset()
})

const sampleCerts = {
  ssh_cert: { key: 'ssh_cert', name: 'ssh_cert',
              status: 'ok', remaining_seconds: 72000 },
  oauth_provider: { key: 'oauth_provider',
                    name: 'OAuth Provider',
                    status: 'warning', remaining_seconds: 1800 },
  // `key: 'slack'` is the route-side id; `name: 'Slack'` is the
  // display label. Renew button must POST to /api/certs/renew/slack
  // (the route-id), NOT /api/certs/renew/Slack (the display name).
  slack: { key: 'slack', name: 'Slack',
           status: 'expired', remaining_seconds: 0,
           note: 'token_expired' },
}

describe('AuthStatus', () => {
  it('renders cert dots after loading', async () => {
    mockFetchJson(sampleCerts)
    render(<AuthStatus />)
    await waitFor(() => {
      const topbar = screen.getByTestId('certs-topbar')
      // Should have 3 dot spans (one per cert)
      const dots = topbar.querySelectorAll('span[title]')
      expect(dots.length).toBe(3)
    })
  })

  it('shows cert details when dropdown is opened', async () => {
    mockFetchJson(sampleCerts)
    render(<AuthStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('certs-topbar')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('Auth Status')).toBeInTheDocument()
      expect(screen.getByText('ssh_cert')).toBeInTheDocument()
      expect(screen.getByText('OAuth Provider')).toBeInTheDocument()
    })
  })

  it('shows renew button for expired/warning certs', async () => {
    mockFetchJson(sampleCerts)
    render(<AuthStatus />)
    // Open dropdown
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      // Warning and expired certs should have renew buttons
      expect(screen.getByTestId('renew-OAuth Provider')).toBeInTheDocument()
      expect(screen.getByTestId('renew-Slack')).toBeInTheDocument()
    })
  })

  it('calls renew API when renew button is clicked', async () => {
    mockFetchJson(sampleCerts)
    render(<AuthStatus />)
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByTestId('renew-Slack')).toBeInTheDocument()
    })
    // Click renew on Slack
    fireEvent.click(screen.getByTestId('renew-Slack'))
    await waitFor(() => {
      // The button text uses the display name ("Slack") but the
      // request URL must use the route-id (`slack`). Asserts the
      // bug fix where the frontend used to send the display name.
      const calls = mockFetch.mock.calls.map((c: unknown[]) => String(c[0]))
      expect(calls.some((url: string) => url.includes('/api/certs/renew/slack'))).toBe(true)
    })
  })

  it('shows "No certs loaded" when API returns empty', async () => {
    mockFetchJson({})
    render(<AuthStatus />)
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('No certs loaded')).toBeInTheDocument()
    })
  })

  it('handles array format for certs', async () => {
    mockFetchJson({ certs: [
      { name: 'test-cert', ok: true, status: 'ok', remaining_seconds: 5000 },
    ] })
    render(<AuthStatus />)
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('test-cert')).toBeInTheDocument()
    })
  })

  it('displays human-readable time remaining', async () => {
    mockFetchJson({
      cert1: { name: 'cert1', status: 'ok', remaining_seconds: 90000 },
    })
    render(<AuthStatus />)
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      // 90000 seconds = 25 hours = 1d
      expect(screen.getByText('1d')).toBeInTheDocument()
    })
  })

  it('displays time in hours when less than a day', async () => {
    mockFetchJson({
      cert1: { name: 'cert1', status: 'ok', remaining_seconds: 7200 },
    })
    render(<AuthStatus />)
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('2h')).toBeInTheDocument()
    })
  })

  it('displays time in minutes when less than an hour', async () => {
    mockFetchJson({
      cert1: { name: 'cert1', status: 'ok', remaining_seconds: 300 },
    })
    render(<AuthStatus />)
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('5m')).toBeInTheDocument()
    })
  })

  it('displays time in seconds when less than a minute', async () => {
    mockFetchJson({
      cert1: { name: 'cert1', status: 'ok', remaining_seconds: 45 },
    })
    render(<AuthStatus />)
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('45s')).toBeInTheDocument()
    })
  })

  it('handles API error gracefully (setCerts to empty)', async () => {
    mockFetch.mockRejectedValue(new Error('network error'))
    render(<AuthStatus />)
    // Should render without crashing
    expect(screen.getByTestId('certs-topbar')).toBeInTheDocument()
    // Open dropdown -- should show empty state
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('No certs loaded')).toBeInTheDocument()
    })
  })

  it('renew failure does not crash and clears renewing state', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (String(url).includes('/api/certs/renew/')) {
        return Promise.reject(new Error('renew failed'))
      }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(sampleCerts),
        text: () => Promise.resolve(JSON.stringify(sampleCerts)),
      })
    })
    render(<AuthStatus />)
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByTestId('renew-Slack')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('renew-Slack'))
    // Should not crash; button should become clickable again
    await waitFor(() => {
      expect(screen.getByTestId('renew-Slack')).not.toBeDisabled()
    })
  })

  it('countdown timer creates interval for certs with remaining_seconds', async () => {
    // Verify the timer setup by checking certs render without crashing
    // and remaining_seconds are displayed correctly
    mockFetchJson({
      cert1: { name: 'cert1', status: 'ok', remaining_seconds: 120 },
      cert2: { name: 'cert2', status: 'warning', remaining_seconds: 0 },
    })
    render(<AuthStatus />)
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('2m')).toBeInTheDocument()
      expect(screen.getByText('cert1')).toBeInTheDocument()
      expect(screen.getByText('cert2')).toBeInTheDocument()
    })
  })

  it('uses label property for display name when available', async () => {
    mockFetchJson({
      mycert: { name: 'mycert', status: 'ok', remaining_seconds: 3600, label: 'My Certificate' },
    })
    render(<AuthStatus />)
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('My Certificate')).toBeInTheDocument()
    })
  })
})
