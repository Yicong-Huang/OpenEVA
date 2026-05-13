import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { PRsPage } from '../pages/PRsPage'

let capturedSSECallback: ((data: string) => void) | null = null
vi.mock('../hooks/useSSE', () => ({
  useSSE: vi.fn((_url: string | null, onMessage: (data: string) => void) => {
    capturedSSECallback = onMessage
    return { close: vi.fn() }
  }),
}))

// Capture useEventBus callbacks by pattern
const eventBusCallbacks: Record<string, (event: Record<string, unknown>) => void> = {}
vi.mock('../hooks/useEventBus', () => ({
  useEventBus: vi.fn((pattern: string, handler: (event: Record<string, unknown>) => void) => {
    eventBusCallbacks[pattern] = handler
  }),
}))

vi.mock('../hooks/useLiveClock', () => ({
  useLiveClock: vi.fn(),
}))

// PRDetail mock: renders a minimal placeholder so tests that click a PR
// card can assert the detail panel opened. Lifted to module level so
// vitest's hoisting doesn't warn (and fires before imports).
vi.mock('../components/PRCard', async () => ({
  PRCard: ({ number }: { number: number }) => (
    <div data-testid="pr-detail-mock">Detail #{number}</div>
  ),
}))

// useEventBus mock is above (captures callbacks by pattern)

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

const samplePRData = {
  groups: {
    'example/repo': {
      name: 'repo',
      prs: [
        {
          number: 101,
          url: 'https://github.com/example/repo/pull/101',
          title: 'Add new feature',
          status: 'open',
          ci_status: 'success',
          review_status: 'approved',
          comment_count: 2,
          additions: 30,
          deletions: 5,
          author: 'alice',
          head_branch: 'feature-branch',
          base_branch: 'master',
          last_updated: '2026-04-13T06:00:00Z',
          task_id: 'task-1',
          project: 'proj-1',
        },
        {
          number: 102,
          url: 'https://github.com/example/repo/pull/102',
          title: 'Fix bug',
          status: 'open',
          ci_status: 'failure',
          review_status: '',
          comment_count: 0,
          additions: 10,
          deletions: 3,
          author: 'bob',
          head_branch: 'fix-bug',
          base_branch: 'master',
          last_updated: '2026-04-12T12:00:00Z',
        },
      ],
    },
  },
}

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
  mockFetchJson(samplePRData)
})

describe('PRsPage', () => {
  it('renders the Pull Requests heading', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Pull Requests' })).toBeInTheDocument()
    })
  })

  it('renders filter tabs (Open, Merged, Closed)', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('Open')).toBeInTheDocument()
      expect(screen.getByText('Merged')).toBeInTheDocument()
      expect(screen.getByText('Closed')).toBeInTheDocument()
    })
  })

  it('renders search input', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByTestId('pr-search')).toBeInTheDocument()
    })
  })

  it('renders PR list with group header', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('Add new feature')).toBeInTheDocument()
      expect(screen.getByText('Fix bug')).toBeInTheDocument()
    })
    // Group header should show count
    expect(screen.getByText(/repo \(2\)/i)).toBeInTheDocument()
  })

  it('renders sync button', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByTestId('sync-prs-btn')).toBeInTheDocument()
      expect(screen.getByText('Sync from GitHub')).toBeInTheDocument()
    })
  })

  it('shows placeholder when no PR selected', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('Select a PR to view details')).toBeInTheDocument()
    })
  })

  it('switches filter when tab is clicked', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('Merged')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Merged'))
    // Should refetch with merged filter
    await waitFor(() => {
      const calls = mockFetch.mock.calls.map((c: unknown[]) => String(c[0]))
      expect(calls.some((url: string) => url.includes('status=merged'))).toBe(true)
    })
  })

  it('shows No PRs found when API returns empty groups', async () => {
    mockFetchJson({ groups: {} })
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('No PRs found.')).toBeInTheDocument()
    })
  })

  it('updates search input value', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByTestId('pr-search')).toBeInTheDocument()
    })
    const input = screen.getByTestId('pr-search') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'feature' } })
    expect(input.value).toBe('feature')
  })

  it('shows loading state', async () => {
    mockFetch.mockImplementation(() => new Promise(() => {}))
    render(<PRsPage />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('clicking a PR shows the detail panel', async () => {
    // PRDetail is mocked at module scope (see top of file).
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('Add new feature')).toBeInTheDocument()
    })
    // Click on the PR card
    fireEvent.click(screen.getByText('Add new feature'))
    await waitFor(() => {
      // placeholder should disappear
      expect(screen.queryByText('Select a PR to view details')).not.toBeInTheDocument()
    })
  })

  it('renders PR count in group header', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      // Group header shows "repo (2)"
      expect(screen.getByText(/repo \(2\)/i)).toBeInTheDocument()
    })
  })

  it('shows task ID when PR has one', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('task-1')).toBeInTheDocument()
    })
  })

  it('disables sync button while syncing', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByTestId('sync-prs-btn')).toBeInTheDocument()
    })
    // Click sync
    fireEvent.click(screen.getByTestId('sync-prs-btn'))
    // Button should show syncing state
    await waitFor(() => {
      expect(screen.getByTestId('sync-prs-btn')).toBeDisabled()
    })
  })

  it('SSE callback handles "start" phase', async () => {
    render(<PRsPage />)
    await waitFor(() => { expect(screen.getByTestId('sync-prs-btn')).toBeInTheDocument() })
    // Simulate SSE message
    capturedSSECallback?.(JSON.stringify({ phase: 'start' }))
    await waitFor(() => {
      expect(screen.getByTestId('sync-prs-btn')).toBeDisabled()
    })
  })

  it('SSE callback handles "dirty" phase', async () => {
    render(<PRsPage />)
    await waitFor(() => { expect(screen.getByTestId('sync-prs-btn')).toBeInTheDocument() })
    capturedSSECallback?.(JSON.stringify({ phase: 'dirty', count: 5 }))
    await waitFor(() => {
      expect(screen.getByText(/Syncing 5 dirty PRs/)).toBeInTheDocument()
    })
  })

  it('SSE callback handles "update" phase', async () => {
    render(<PRsPage />)
    await waitFor(() => { expect(screen.getByTestId('sync-prs-btn')).toBeInTheDocument() })
    capturedSSECallback?.(JSON.stringify({ phase: 'update', current: 3, total: 10, pr: 42 }))
    await waitFor(() => {
      expect(screen.getByText(/3\/10.*#42/)).toBeInTheDocument()
    })
  })

  it('SSE callback handles "done" phase', async () => {
    render(<PRsPage />)
    await waitFor(() => { expect(screen.getByTestId('sync-prs-btn')).toBeInTheDocument() })
    capturedSSECallback?.(JSON.stringify({ phase: 'done', discovered: 2, updated: 5 }))
    await waitFor(() => {
      expect(screen.getByText(/2 new, 5 updated/)).toBeInTheDocument()
    })
  })

  it('SSE callback handles "discover" phase', async () => {
    render(<PRsPage />)
    await waitFor(() => { expect(screen.getByTestId('sync-prs-btn')).toBeInTheDocument() })
    capturedSSECallback?.(JSON.stringify({ phase: 'discover', discovered: 3 }))
    await waitFor(() => {
      expect(screen.getByText(/Found 3 new PRs/)).toBeInTheDocument()
    })
  })

  it('SSE callback handles parse error', async () => {
    render(<PRsPage />)
    await waitFor(() => { expect(screen.getByTestId('sync-prs-btn')).toBeInTheDocument() })
    capturedSSECallback?.('not valid json{{')
    await waitFor(() => {
      expect(screen.getByText('Sync failed')).toBeInTheDocument()
    })
  })

  it('collapsing a group header hides its PRs', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('Add new feature')).toBeInTheDocument()
    })
    // Click the group header to collapse
    const header = screen.getByTestId('pr-group-header-example/repo')
    fireEvent.click(header)
    // PRs should be hidden after collapse
    await waitFor(() => {
      expect(screen.queryByText('Add new feature')).not.toBeInTheDocument()
      expect(screen.queryByText('Fix bug')).not.toBeInTheDocument()
    })
    // Click again to expand
    fireEvent.click(header)
    await waitFor(() => {
      expect(screen.getByText('Add new feature')).toBeInTheDocument()
      expect(screen.getByText('Fix bug')).toBeInTheDocument()
    })
  })

  it('SSE callback handles "dirty_update" phase', async () => {
    render(<PRsPage />)
    await waitFor(() => { expect(screen.getByTestId('sync-prs-btn')).toBeInTheDocument() })
    capturedSSECallback?.(JSON.stringify({ phase: 'dirty_update', current: 2, total: 5 }))
    await waitFor(() => {
      expect(screen.getByText(/Dirty 2\/5/)).toBeInTheDocument()
    })
  })

  it('search refetches with search param', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByTestId('pr-search')).toBeInTheDocument()
    })
    const input = screen.getByTestId('pr-search') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'new feature' } })
    await waitFor(() => {
      const calls = mockFetch.mock.calls.map((c: unknown[]) => String(c[0]))
      expect(calls.some((url: string) => url.includes('search=new'))).toBe(true)
    })
  })

  it('renders multiple groups when data has them', async () => {
    const multiGroupData = {
      groups: {
        'example/repo': {
          name: 'repo',
          prs: [{ number: 101, url: 'https://github.com/example/repo/pull/101', title: 'PR A', status: 'open', ci_status: 'success', review_status: '', comment_count: 0, additions: 5, deletions: 2, author: 'alice', head_branch: 'a', base_branch: 'main', last_updated: '2026-04-13T00:00:00Z' }],
        },
        'myorg/svc': {
          name: 'runtime',
          prs: [{ number: 201, url: 'https://github.com/myorg/svc/pull/201', title: 'PR B', status: 'open', ci_status: 'failure', review_status: '', comment_count: 1, additions: 10, deletions: 0, author: 'bob', head_branch: 'b', base_branch: 'main', last_updated: '2026-04-12T00:00:00Z' }],
        },
      },
    }
    mockFetchJson(multiGroupData)
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText(/repo \(1\)/i)).toBeInTheDocument()
      expect(screen.getByText(/runtime \(1\)/i)).toBeInTheDocument()
      expect(screen.getByText('PR A')).toBeInTheDocument()
      expect(screen.getByText('PR B')).toBeInTheDocument()
    })
  })

  it('sync button shows done state after completion', async () => {
    render(<PRsPage />)
    await waitFor(() => { expect(screen.getByTestId('sync-prs-btn')).toBeInTheDocument() })
    capturedSSECallback?.(JSON.stringify({ phase: 'done', discovered: 1, updated: 3 }))
    await waitFor(() => {
      const btn = screen.getByTestId('sync-prs-btn')
      expect(btn).toHaveTextContent('1 new, 3 updated')
      expect(btn).not.toBeDisabled()
    })
  })

  it('task panel renders when taskPanel and project are provided', async () => {
    // Mock getProject and getActions fetch calls
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/projects/proj-1') && !url.includes('tasks')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            id: 'proj-1',
            name: 'Test Project',
            description: '',
            has_tickets: true,
            progress: 50,
            task_counts: {},
            tasks: {
              'task-1': {
                task_id: 'task-1',
                project: 'proj-1',
                description: 'Do stuff',
                type: 'feature',
                status: 'in_progress',
                group_name: 'core',
                notes: '',
                priority: 1,
                ticket_id: null,
                ticket_url: null,
                dependencies: [],
                follow_ups: [],
                prs: [],
                created_at: '',
                updated_at: '',
              },
            },
          }),
          text: () => Promise.resolve(''),
        })
      }
      if (typeof url === 'string' && url.includes('/api/actions')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ actions: [] }),
          text: () => Promise.resolve(''),
        })
      }
      // Default: return PR data
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(samplePRData),
        text: () => Promise.resolve(JSON.stringify(samplePRData)),
      })
    })

    render(
      <PRsPage
        selectedPR={{ repo: 'example/repo', number: 100, projectId: 'proj-1', taskId: 'task-1' }}
        onSelectPR={vi.fn()}
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('task-side-panel')).toBeInTheDocument()
    })

    // Should show project/task breadcrumb text
    expect(screen.getByText(/proj-1 \/ task-1/)).toBeInTheDocument()

    // Should have a Close button in the task-side-panel header
    const panel = screen.getByTestId('task-side-panel')
    const panelCloseBtn = panel.querySelector('button.btn-action') as HTMLButtonElement
    expect(panelCloseBtn).toBeTruthy()
    expect(panelCloseBtn.textContent).toContain('Close')
  })

  it('task panel Close button detaches task info from selectedPR', async () => {
    const onSelectPR = vi.fn()
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/projects/proj-1') && !url.includes('tasks')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            id: 'proj-1', name: 'Test', description: '', has_tickets: false,
            progress: 0, task_counts: {}, tasks: { 'task-1': {
              task_id: 'task-1', project: 'proj-1', description: '', type: 'feature',
              status: 'in_progress', group_name: '', notes: '', priority: 1,
              ticket_id: null, ticket_url: null, dependencies: [], follow_ups: [],
              prs: [], created_at: '', updated_at: '',
            }},
          }),
          text: () => Promise.resolve(''),
        })
      }
      if (typeof url === 'string' && url.includes('/api/actions')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ actions: [] }),
          text: () => Promise.resolve(''),
        })
      }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(samplePRData),
        text: () => Promise.resolve(JSON.stringify(samplePRData)),
      })
    })

    render(
      <PRsPage
        selectedPR={{ repo: 'example/repo', number: 100, projectId: 'proj-1', taskId: 'task-1' }}
        onSelectPR={onSelectPR}
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('task-side-panel')).toBeInTheDocument()
    })

    // Click the Close button inside the task-side-panel header (not the TaskCard's Close)
    const panel = screen.getByTestId('task-side-panel')
    const closeBtn = panel.querySelector('button.btn-action') as HTMLButtonElement
    expect(closeBtn).toBeTruthy()
    fireEvent.click(closeBtn)
    // Close now re-emits selectedPR without task association
    expect(onSelectPR).toHaveBeenCalledWith({ repo: 'example/repo', number: 100 })
  })

  it('clicking PR card refresh button calls refreshPR API', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('Add new feature')).toBeInTheDocument()
    })

    // Find all refresh buttons (title="Refresh PR status")
    const refreshBtns = screen.getAllByTitle('Refresh PR status')
    expect(refreshBtns.length).toBeGreaterThanOrEqual(1)

    // Click the first one
    fireEvent.click(refreshBtns[0])

    // Should call refreshPR API
    await waitFor(() => {
      const calls = mockFetch.mock.calls.map((c: unknown[]) => String(c[0]))
      expect(calls.some((url: string) => url.includes('pr-refresh'))).toBe(true)
    })
  })

  it('clicking PR without task sets task panel to null', async () => {
    render(<PRsPage />)
    await waitFor(() => {
      // PR 102 (Fix bug) has no task_id or project
      expect(screen.getByText('Fix bug')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Fix bug'))
    // task-side-panel should NOT appear since PR has no task
    await waitFor(() => {
      expect(screen.queryByTestId('task-side-panel')).not.toBeInTheDocument()
    })
  })

  it('agent.* event bus callback refetches project data when task panel is open', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/projects/proj-1') && !url.includes('tasks')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({
            id: 'proj-1', name: 'Test', description: '', has_tickets: false,
            progress: 0, task_counts: {}, tasks: { 'task-1': {
              task_id: 'task-1', project: 'proj-1', description: '', type: 'feature',
              status: 'in_progress', group_name: '', notes: '', priority: 1,
              ticket_id: null, ticket_url: null, dependencies: [], follow_ups: [],
              prs: [], created_at: '', updated_at: '',
            }},
          }),
          text: () => Promise.resolve(''),
        })
      }
      if (typeof url === 'string' && url.includes('/api/actions')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ actions: [] }),
          text: () => Promise.resolve(''),
        })
      }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(samplePRData),
        text: () => Promise.resolve(JSON.stringify(samplePRData)),
      })
    })

    render(
      <PRsPage
        selectedPR={{ repo: 'example/repo', number: 100, projectId: 'proj-1', taskId: 'task-1' }}
        onSelectPR={vi.fn()}
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('task-side-panel')).toBeInTheDocument()
    })

    const callsBefore = mockFetch.mock.calls.length

    // Trigger the agent.* event bus callback
    if (eventBusCallbacks['agent.*']) {
      eventBusCallbacks['agent.*']({ type: 'agent.done' })
    }

    await waitFor(() => {
      expect(mockFetch.mock.calls.length).toBeGreaterThan(callsBefore)
    })
  })

  it('task panel handles getProject failure gracefully', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/projects/proj-fail') && !url.includes('tasks')) {
        return Promise.reject(new Error('project not found'))
      }
      if (typeof url === 'string' && url.includes('/api/actions')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ actions: [] }),
          text: () => Promise.resolve(''),
        })
      }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(samplePRData),
        text: () => Promise.resolve(JSON.stringify(samplePRData)),
      })
    })

    render(
      <PRsPage
        selectedPR={{ repo: 'example/repo', number: 100, projectId: 'proj-fail', taskId: 'task-1' }}
        onSelectPR={vi.fn()}
      />,
    )

    // Should not crash; task panel should not appear since getProject failed
    await waitFor(() => {
      expect(screen.queryByTestId('task-side-panel')).not.toBeInTheDocument()
    })
  })

  it('github.* event bus callback refetches project data when task panel is open', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/projects/proj-1') && !url.includes('tasks')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({
            id: 'proj-1', name: 'Test', description: '', has_tickets: false,
            progress: 0, task_counts: {}, tasks: { 'task-1': {
              task_id: 'task-1', project: 'proj-1', description: '', type: 'feature',
              status: 'in_progress', group_name: '', notes: '', priority: 1,
              ticket_id: null, ticket_url: null, dependencies: [], follow_ups: [],
              prs: [], created_at: '', updated_at: '',
            }},
          }),
          text: () => Promise.resolve(''),
        })
      }
      if (typeof url === 'string' && url.includes('/api/actions')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ actions: [] }),
          text: () => Promise.resolve(''),
        })
      }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(samplePRData),
        text: () => Promise.resolve(JSON.stringify(samplePRData)),
      })
    })

    render(
      <PRsPage
        selectedPR={{ repo: 'example/repo', number: 100, projectId: 'proj-1', taskId: 'task-1' }}
        onSelectPR={vi.fn()}
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('task-side-panel')).toBeInTheDocument()
    })

    // Record fetch calls before invoking the event bus
    const callsBefore = mockFetch.mock.calls.length

    // Trigger the github.* event bus callback
    if (eventBusCallbacks['github.*']) {
      eventBusCallbacks['github.*']({ type: 'github.push' })
    }

    // Should have made an additional fetch call for getProject
    await waitFor(() => {
      expect(mockFetch.mock.calls.length).toBeGreaterThan(callsBefore)
    })
  })
})
