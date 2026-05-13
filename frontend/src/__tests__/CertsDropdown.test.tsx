import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { AuthStatus } from '../components/status/AuthStatus'

vi.mock('../hooks/useClickOutside', () => ({
  useClickOutside: vi.fn(),
}))

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

vi.mock('../hooks/useEventBus', () => ({
  useEventBus: vi.fn(),
}))

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

describe('AuthStatus', () => {
  it('renders cert names after opening', async () => {
    mockFetchJson({
      certs: [
        { name: 'mycert', ok: true },
        { name: 'sshkey', ok: false, status: 'expired' },
      ],
    })
    render(<AuthStatus />)
    // Wait for data to load, then click the inner span to open dropdown
    await waitFor(() => {
      expect(screen.getByTestId('certs-topbar').querySelector('span')).toBeTruthy()
    })
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('mycert')).toBeInTheDocument()
      expect(screen.getByText('sshkey')).toBeInTheDocument()
    })
  })

  it('shows renew button for expired certs', async () => {
    mockFetchJson({
      certs: [
        { name: 'expired-cert', ok: false, status: 'expired' },
      ],
    })
    render(<AuthStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('certs-topbar').querySelector('span')).toBeTruthy()
    })
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByTestId('renew-expired-cert')).toBeInTheDocument()
    })
  })

  it('shows dimmed refresh button for OK certs', async () => {
    mockFetchJson({
      certs: [
        { name: 'good-cert', ok: true, status: 'ok' },
      ],
    })
    render(<AuthStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('certs-topbar').querySelector('span')).toBeTruthy()
    })
    fireEvent.click(screen.getByTestId('certs-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('good-cert')).toBeInTheDocument()
    })
    const btn = screen.getByTestId('renew-good-cert')
    expect(btn).toBeInTheDocument()
    expect(btn.style.opacity).toBe('0.4')
  })
})
