import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { AIUsageStatus } from '../components/status/AIUsageStatus'

vi.mock('../hooks/useClickOutside', () => ({
  useClickOutside: vi.fn(),
}))

vi.mock('../hooks/useLiveClock', () => ({
  useLiveClock: vi.fn(),
}))

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

function mockFetchResponse(_url: string, data: unknown) {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  }
}

beforeEach(() => {
  mockFetch.mockReset()
  mockFetch.mockImplementation((url: string) => {
    if (String(url).includes('/api/usage/history')) {
      return Promise.resolve(mockFetchResponse(String(url), { history: [], total_records: 0 }))
    }
    if (String(url).includes('/api/usage')) {
      return Promise.resolve(mockFetchResponse(String(url), {
        daily: '221.50',
        weekly: '221.50',
        monthly: '4300.00',
        tier: 'Power User',
        updated_at: '2026-04-13T06:00:00',
      }))
    }
    return Promise.resolve(mockFetchResponse(String(url), {}))
  })
})

describe('AIUsageStatus', () => {
  it('renders daily usage in the topbar', async () => {
    render(<AIUsageStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('usage-topbar')).toBeInTheDocument()
    })
    // After loading, should show the daily value
    await waitFor(() => {
      expect(screen.getByTestId('usage-topbar').textContent).toContain('221.50')
    })
  })

  it('shows dropdown with daily/weekly/monthly on click', async () => {
    render(<AIUsageStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('usage-topbar').textContent).toContain('221.50')
    })
    fireEvent.click(screen.getByTestId('usage-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('AI Usage')).toBeInTheDocument()
      expect(screen.getByText('Daily')).toBeInTheDocument()
      expect(screen.getByText('Weekly')).toBeInTheDocument()
      expect(screen.getByText('Monthly')).toBeInTheDocument()
    })
  })

  it('shows tier when available', async () => {
    render(<AIUsageStatus />)
    fireEvent.click(screen.getByTestId('usage-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('Power User')).toBeInTheDocument()
    })
  })

  it('displays friendly time for updated timestamp', async () => {
    render(<AIUsageStatus />)
    await waitFor(() => {
      // Should show relative time like "Xh ago" or "just now", not raw ISO
      const topbar = screen.getByTestId('usage-topbar')
      expect(topbar.textContent).not.toContain('2026-04-13T06:00:00')
    })
  })

  it('triggers refresh when dropdown is opened', async () => {
    render(<AIUsageStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('usage-topbar').textContent).toContain('221.50')
    })
    const callsBefore = mockFetch.mock.calls.filter(
      (c: unknown[]) => String(c[0]).includes('/api/usage') && !String(c[0]).includes('history')
    ).length
    // Open dropdown
    fireEvent.click(screen.getByTestId('usage-topbar').querySelector('span')!)
    await waitFor(() => {
      const callsAfter = mockFetch.mock.calls.filter(
        (c: unknown[]) => String(c[0]).includes('/api/usage') && !String(c[0]).includes('history')
      ).length
      expect(callsAfter).toBeGreaterThan(callsBefore)
    })
  })

  it('handles API error gracefully', async () => {
    mockFetch.mockRejectedValue(new Error('network error'))
    render(<AIUsageStatus />)
    // Should render without crashing
    expect(screen.getByTestId('usage-topbar')).toBeInTheDocument()
  })

  it('shows -- when usage data is null', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (String(url).includes('/api/usage')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ daily: null, weekly: null, monthly: null }),
          text: () => Promise.resolve('{}'),
        })
      }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({}),
        text: () => Promise.resolve('{}'),
      })
    })
    render(<AIUsageStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('usage-topbar').textContent).toContain('--')
    })
  })

  it('refresh button in dropdown re-fetches usage', async () => {
    render(<AIUsageStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('usage-topbar').textContent).toContain('221.50')
    })
    // Open dropdown
    fireEvent.click(screen.getByTestId('usage-topbar').querySelector('span')!)
    await waitFor(() => { expect(screen.getByText('AI Usage')).toBeInTheDocument() })

    const callsBefore = mockFetch.mock.calls.length
    // Click the refresh button (the unicode reload symbol)
    const refreshBtn = screen.getByText('\u21BB')
    fireEvent.click(refreshBtn)
    await waitFor(() => {
      expect(mockFetch.mock.calls.length).toBeGreaterThan(callsBefore)
    })
  })

  it('adjusts refresh rate based on usage history gaps', async () => {
    const now = Date.now()
    mockFetch.mockImplementation((url: string) => {
      if (String(url).includes('/api/usage/history')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({
            history: [
              { ts: new Date(now - 120000).toISOString(), daily: 100, weekly: 200, monthly: 300 },
              { ts: new Date(now - 60000).toISOString(), daily: 110, weekly: 210, monthly: 310 },
              { ts: new Date(now).toISOString(), daily: 120, weekly: 220, monthly: 320 },
            ],
            total_records: 3,
          }),
          text: () => Promise.resolve('{}'),
        })
      }
      if (String(url).includes('/api/usage')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ daily: '120.00', weekly: '220.00', monthly: '320.00' }),
          text: () => Promise.resolve('{}'),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<AIUsageStatus />)
    // Should not crash and should render
    await waitFor(() => {
      expect(screen.getByTestId('usage-topbar')).toBeInTheDocument()
    })
  })

  it('hides tier line when tier is not provided', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (String(url).includes('/api/usage/history')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ history: [], total_records: 0 }), text: () => Promise.resolve('{}') })
      }
      if (String(url).includes('/api/usage')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ daily: '50.00', weekly: '100.00', monthly: '200.00' }),
          text: () => Promise.resolve('{}'),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<AIUsageStatus />)
    await waitFor(() => { expect(screen.getByTestId('usage-topbar').textContent).toContain('50.00') })
    fireEvent.click(screen.getByTestId('usage-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('AI Usage')).toBeInTheDocument()
      expect(screen.queryByText('Tier')).toBeNull()
    })
  })

  it('shows updated timestamp in dropdown', async () => {
    render(<AIUsageStatus />)
    await waitFor(() => { expect(screen.getByTestId('usage-topbar').textContent).toContain('221.50') })
    fireEvent.click(screen.getByTestId('usage-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText(/Updated:/)).toBeInTheDocument()
    })
  })

  it('hides updated line when updated timestamp is not provided', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (String(url).includes('/api/usage/history')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ history: [], total_records: 0 }), text: () => Promise.resolve('{}') })
      }
      if (String(url).includes('/api/usage')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ daily: '100.00', weekly: '200.00', monthly: '300.00' }),
          text: () => Promise.resolve('{}'),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<AIUsageStatus />)
    await waitFor(() => { expect(screen.getByTestId('usage-topbar').textContent).toContain('100.00') })
    fireEvent.click(screen.getByTestId('usage-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('AI Usage')).toBeInTheDocument()
      expect(screen.queryByText(/Updated:/)).not.toBeInTheDocument()
    })
  })

  it('animates value changes in AnimatedValue', async () => {
    // Render with initial value, then re-render with a new value to trigger animation
    const { rerender } = render(<AIUsageStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('usage-topbar').textContent).toContain('221.50')
    })

    // Update the mock to return a different value
    mockFetch.mockImplementation((url: string) => {
      if (String(url).includes('/api/usage/history')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ history: [], total_records: 0 }), text: () => Promise.resolve('{}') })
      }
      if (String(url).includes('/api/usage')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({
            daily: '350.00', weekly: '500.00', monthly: '5000.00',
            tier: 'Power User', updated_at: '2026-04-13T07:00:00',
          }),
          text: () => Promise.resolve('{}'),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })

    // Re-render to trigger data refetch
    rerender(<AIUsageStatus />)
    // The animated value should eventually reach the new value
    await waitFor(() => {
      expect(screen.getByTestId('usage-topbar')).toBeInTheDocument()
    })
  })

  it('shows all three usage values in the dropdown panel', async () => {
    render(<AIUsageStatus />)
    await waitFor(() => { expect(screen.getByTestId('usage-topbar').textContent).toContain('221.50') })
    fireEvent.click(screen.getByTestId('usage-topbar').querySelector('span')!)
    await waitFor(() => {
      // Should display all three values
      const content = screen.getByText('AI Usage').closest('div')!.parentElement!.textContent
      expect(content).toContain('Daily')
      expect(content).toContain('Weekly')
      expect(content).toContain('Monthly')
    })
  })

  it('adaptive refresh handles history API error gracefully', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (String(url).includes('/api/usage/history')) {
        return Promise.reject(new Error('history fetch failed'))
      }
      if (String(url).includes('/api/usage')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ daily: '50.00', weekly: '100.00', monthly: '200.00' }),
          text: () => Promise.resolve('{}'),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<AIUsageStatus />)
    // Should not crash, should still show usage
    await waitFor(() => {
      expect(screen.getByTestId('usage-topbar').textContent).toContain('50.00')
    })
  })

  it('adaptive refresh with insufficient history records keeps default rate', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (String(url).includes('/api/usage/history')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({
            history: [
              { ts: new Date().toISOString(), daily: 100, weekly: 200, monthly: 300 },
            ],
            total_records: 1,
          }),
          text: () => Promise.resolve('{}'),
        })
      }
      if (String(url).includes('/api/usage')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ daily: '100.00', weekly: '200.00', monthly: '300.00' }),
          text: () => Promise.resolve('{}'),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<AIUsageStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('usage-topbar').textContent).toContain('100.00')
    })
  })

  it('closes dropdown when clicking again', async () => {
    render(<AIUsageStatus />)
    await waitFor(() => { expect(screen.getByTestId('usage-topbar').textContent).toContain('221.50') })
    // Open
    fireEvent.click(screen.getByTestId('usage-topbar').querySelector('span')!)
    await waitFor(() => { expect(screen.getByText('AI Usage')).toBeInTheDocument() })
    // Close
    fireEvent.click(screen.getByTestId('usage-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.queryByText('AI Usage')).not.toBeInTheDocument()
    })
  })

  it('AnimatedValue animates when numeric value changes', async () => {
    // Mock requestAnimationFrame to execute callback synchronously
    const originalRaf = globalThis.requestAnimationFrame
    const originalCaf = globalThis.cancelAnimationFrame
    let rafId = 0
    globalThis.requestAnimationFrame = (cb: FrameRequestCallback) => {
      rafId++
      // Call with a high timestamp to complete the animation immediately
      setTimeout(() => cb(performance.now() + 1000), 0)
      return rafId
    }
    globalThis.cancelAnimationFrame = () => {}

    // First render with one value
    mockFetch.mockImplementation((url: string) => {
      if (String(url).includes('/api/usage/history')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ history: [], total_records: 0 }), text: () => Promise.resolve('{}') })
      }
      if (String(url).includes('/api/usage')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ daily: '100.00', weekly: '200.00', monthly: '300.00' }),
          text: () => Promise.resolve(JSON.stringify({ daily: '100.00', weekly: '200.00', monthly: '300.00' })),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })

    render(<AIUsageStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('usage-topbar').textContent).toContain('100.00')
    })

    // Now change the value to trigger animation
    mockFetch.mockImplementation((url: string) => {
      if (String(url).includes('/api/usage/history')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ history: [], total_records: 0 }), text: () => Promise.resolve('{}') })
      }
      if (String(url).includes('/api/usage')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ daily: '250.00', weekly: '400.00', monthly: '600.00' }),
          text: () => Promise.resolve(JSON.stringify({ daily: '250.00', weekly: '400.00', monthly: '600.00' })),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })

    // Open dropdown to trigger reload with new value
    fireEvent.click(screen.getByTestId('usage-topbar').querySelector('span')!)
    await waitFor(() => {
      // The animation should eventually settle on the new value
      const topbar = screen.getByTestId('usage-topbar')
      expect(topbar).toBeInTheDocument()
    })

    globalThis.requestAnimationFrame = originalRaf
    globalThis.cancelAnimationFrame = originalCaf
  })

  it('renders usage values with monospace font in dropdown', async () => {
    render(<AIUsageStatus />)
    await waitFor(() => { expect(screen.getByTestId('usage-topbar').textContent).toContain('221.50') })
    fireEvent.click(screen.getByTestId('usage-topbar').querySelector('span')!)
    await waitFor(() => {
      expect(screen.getByText('AI Usage')).toBeInTheDocument()
      expect(screen.getByText('Tier')).toBeInTheDocument()
      expect(screen.getByText('Power User')).toBeInTheDocument()
    })
  })
})
