import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { PRPlugin } from '@core/plugins/pr/PRPlugin'

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

const liveStatsData = {
  open_prs: { repo: 3, runtime: 2, universe: 1, total: 6 },
  contributor_rank: 42,
  contributor_contributions: '128',
  contributor_repo: 'example/repo',
}

const workstatsData = {
  quarters: [
    {
      period: 'Q1 2026', total: 15,
      by_repo: { repo: 8, runtime: 5, universe: 2 },
    },
    {
      period: 'Q4 2025', total: 12,
      by_repo: { repo: 6, runtime: 4, universe: 2 },
    },
  ],
  weekly: [3, 5, 2, 7, 4],
  weekly_primary: [2, 3, 1, 4, 2],
}

function mockApis() {
  mockFetch.mockImplementation((url: string) => {
    if (url.includes('/api/live-stats')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(liveStatsData),
        text: () => Promise.resolve(JSON.stringify(liveStatsData)),
      })
    }
    if (url.includes('/api/workstats')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(workstatsData),
        text: () => Promise.resolve(JSON.stringify(workstatsData)),
      })
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve('{}'),
    })
  })
}

beforeEach(() => {
  mockFetch.mockReset()
})

describe('PRPlugin', () => {
  it('renders open PR counts', async () => {
    mockApis()
    render(<PRPlugin />)
    await waitFor(() => {
      expect(screen.getByText('repo:3')).toBeInTheDocument()
      expect(screen.getByText('runtime:2')).toBeInTheDocument()
      expect(screen.getByText('universe:1')).toBeInTheDocument()
    })
  })

  it('renders quarterly bars', async () => {
    mockApis()
    render(<PRPlugin />)
    // Quarters are reversed in the component, so Q4 2025 renders first
    await waitFor(() => {
      expect(screen.getByText('Q4 2025')).toBeInTheDocument()
      expect(screen.getByText('Q1 2026')).toBeInTheDocument()
    })
  })

  it('renders contributor rank with repo-derived label', async () => {
    mockApis()
    render(<PRPlugin />)
    await waitFor(() => {
      expect(screen.getByText('#42')).toBeInTheDocument()
      expect(screen.getByText('128 commits')).toBeInTheDocument()
    })
    // The link text + href derive from `contributor_repo` -- no
    // hardcoded "Repo" / `example/repo` in the component source.
    const link = screen.getByRole('link', { name: /repo/i })
    expect(link).toHaveAttribute(
      'href', 'https://github.com/example/repo/graphs/contributors',
    )
  })

  it('shows loading state initially', () => {
    mockFetch.mockReturnValue(new Promise(() => {})) // never resolves
    render(<PRPlugin />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('shows error state when fetch fails', async () => {
    mockFetch.mockRejectedValue(new Error('API down'))
    render(<PRPlugin />)
    await waitFor(() => {
      expect(screen.getByText('PR stats failed')).toBeInTheDocument()
    })
  })

  it('renders with no contributor rank', async () => {
    const noRankData = { ...liveStatsData, contributor_rank: null, contributor_contributions: '' }
    mockFetch.mockImplementation((url: string) => {
      if (url.includes('/api/live-stats')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve(noRankData),
          text: () => Promise.resolve(JSON.stringify(noRankData)),
        })
      }
      if (url.includes('/api/workstats')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve(workstatsData),
          text: () => Promise.resolve(JSON.stringify(workstatsData)),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<PRPlugin />)
    await waitFor(() => {
      expect(screen.getByText('repo:3')).toBeInTheDocument()
    })
    // No contributor rank link should be rendered
    expect(screen.queryByText('#42')).not.toBeInTheDocument()
  })

  it('renders with empty quarters', async () => {
    const noQuarters = { quarters: [], weekly: [], weekly_primary: [] }
    mockFetch.mockImplementation((url: string) => {
      if (url.includes('/api/live-stats')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve(liveStatsData),
          text: () => Promise.resolve(JSON.stringify(liveStatsData)),
        })
      }
      if (url.includes('/api/workstats')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve(noQuarters),
          text: () => Promise.resolve(JSON.stringify(noQuarters)),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<PRPlugin />)
    await waitFor(() => {
      expect(screen.getByTestId('pr-bar')).toBeInTheDocument()
    })
    // No quarterly periods or merged total should appear
    expect(screen.queryByText('Q1 2026')).not.toBeInTheDocument()
    expect(screen.queryByText('Merged')).not.toBeInTheDocument()
  })

  it('renders merged total from quarters', async () => {
    mockApis()
    render(<PRPlugin />)
    await waitFor(() => {
      // Total merged = 15 + 12 = 27
      expect(screen.getByText('27')).toBeInTheDocument()
      expect(screen.getByText('Merged')).toBeInTheDocument()
    })
  })

  it('refresh button triggers load with refresh=true', async () => {
    mockApis()
    render(<PRPlugin />)
    await waitFor(() => {
      expect(screen.getByTestId('pr-bar')).toBeInTheDocument()
    })

    // Find the refresh button (&#8635; character)
    const refreshBtn = screen.getByText('\u21BB')
    fireEvent.click(refreshBtn)

    // Verify fetch was called again with ?refresh=1
    await waitFor(() => {
      const refreshCall = mockFetch.mock.calls.find(
        (call: unknown[]) => typeof call[0] === 'string' && (call[0] as string).includes('refresh=1'),
      )
      expect(refreshCall).toBeTruthy()
    })
  })

  it('renders repos with zero count hidden', async () => {
    const zeroRuntime = {
      ...liveStatsData,
      open_prs: { repo: 3, runtime: 0, universe: 1, total: 4 },
    }
    mockFetch.mockImplementation((url: string) => {
      if (url.includes('/api/live-stats')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve(zeroRuntime),
          text: () => Promise.resolve(JSON.stringify(zeroRuntime)),
        })
      }
      if (url.includes('/api/workstats')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve(workstatsData),
          text: () => Promise.resolve(JSON.stringify(workstatsData)),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<PRPlugin />)
    await waitFor(() => {
      expect(screen.getByText('repo:3')).toBeInTheDocument()
    })
    // runtime:0 should not be displayed
    expect(screen.queryByText('runtime:0')).not.toBeInTheDocument()
  })
})
