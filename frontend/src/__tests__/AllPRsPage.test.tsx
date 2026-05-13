import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { PRsPage } from '../pages/PRsPage'

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

// Mock useSSE to avoid EventSource issues
vi.mock('../hooks/useSSE', () => ({
  useSSE: () => ({ close: vi.fn() }),
}))

vi.mock('../hooks/useEventBus', () => ({
  useEventBus: vi.fn(),
}))

function mockFetchResponses(responses: Record<string, unknown>) {
  mockFetch.mockImplementation((url: string) => {
    for (const [pattern, data] of Object.entries(responses)) {
      if (url.includes(pattern)) {
        return Promise.resolve({
          ok: true,
          text: () => Promise.resolve(JSON.stringify(data)),
        })
      }
    }
    return Promise.resolve({
      ok: true,
      text: () => Promise.resolve('{}'),
    })
  })
}

const prData = {
  groups: {
    'example/repo': {
      name: 'example/repo',
      prs: [
        {
          number: 100,
          url: 'https://github.com/example/repo/pull/100',
          status: 'open',
          title: 'Fix null pointer',
          ci_status: 'success',
          review_status: 'approved',
          comment_count: 3,
          additions: 20,
          deletions: 5,
          author: 'dev1',
          head_branch: 'fix-npe',
          base_branch: 'master',
          last_updated: '2026-04-12T10:00:00Z',
        },
      ],
    },
  },
}

describe('PRsPage', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  it('renders filter tabs (Open, Merged, Closed)', async () => {
    mockFetchResponses({ 'all-prs': prData, actions: { actions: [] } })
    render(<PRsPage />)
    expect(screen.getByText('Open')).toBeInTheDocument()
    expect(screen.getByText('Merged')).toBeInTheDocument()
    expect(screen.getByText('Closed')).toBeInTheDocument()
  })

  it('shows loading then PR list', async () => {
    mockFetchResponses({ 'all-prs': prData, actions: { actions: [] } })
    render(<PRsPage />)
    // Initially shows loading
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    // After fetch resolves, shows PR title
    await waitFor(() => {
      expect(screen.getByText('Fix null pointer')).toBeInTheDocument()
    })
  })

  it('shows repo group header', async () => {
    mockFetchResponses({ 'all-prs': prData, actions: { actions: [] } })
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('example/repo (1)')).toBeInTheDocument()
    })
  })

  it('shows "Select a PR" prompt when no PR selected', async () => {
    mockFetchResponses({ 'all-prs': prData, actions: { actions: [] } })
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('Select a PR to view details')).toBeInTheDocument()
    })
  })

  it('shows "No PRs found" when list is empty', async () => {
    mockFetchResponses({ 'all-prs': { groups: {} }, actions: { actions: [] } })
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('No PRs found.')).toBeInTheDocument()
    })
  })

  it('shows search input', async () => {
    mockFetchResponses({ 'all-prs': prData, actions: { actions: [] } })
    render(<PRsPage />)
    expect(screen.getByTestId('pr-search')).toBeInTheDocument()
  })
})
