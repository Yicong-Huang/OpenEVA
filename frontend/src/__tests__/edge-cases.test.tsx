import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { Project, PR, GraphData } from '../types'

// ---------------------------------------------------------------------------
// Shared mocks -- must be declared before any component imports
// ---------------------------------------------------------------------------

vi.mock('../hooks/useTerminal', () => ({
  useTerminal: () => ({ sendInput: vi.fn() }),
}))

vi.mock('../hooks/useLiveClock', () => ({
  useLiveClock: () => {},
}))

vi.mock('../hooks/useSSE', () => ({
  useSSE: () => ({ close: vi.fn() }),
}))

vi.mock('../hooks/useEventBus', () => ({
  useEventBus: vi.fn(),
}))

const mockGetGraph = vi.fn()
vi.mock('../api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api')>()
  return {
    ...original,
    api: {
      ...original.api,
      getGraph: (...args: any[]) => mockGetGraph(...args),
    },
  }
})

vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ children }: any) => <div data-testid="react-flow">{children}</div>,
  Background: () => null,
  Controls: () => null,
  MiniMap: () => null,
  Handle: () => null,
  Position: { Left: 'left', Right: 'right' },
  useNodesState: (init: any) => {
    const [state, setState] = useState(init)
    return [state, setState, vi.fn()]
  },
  useEdgesState: (init: any) => {
    const [state, setState] = useState(init)
    return [state, setState, vi.fn()]
  },
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useReactFlow: () => ({
    screenToFlowPosition: (p: { x: number; y: number }) => p,
  }),
}))

vi.mock('dagre', () => {
  class MockGraph {
    setDefaultEdgeLabel = vi.fn()
    setGraph = vi.fn()
    setNode = vi.fn()
    setEdge = vi.fn()
    node = vi.fn(() => ({ x: 0, y: 0 }))
  }
  return {
    default: {
      graphlib: { Graph: MockGraph },
      layout: vi.fn(),
    },
  }
})

// Stub fetch globally for api + PRsPage tests
const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

// Component imports -- must come after vi.mock calls are set up
import { TaskCard } from '../components/TaskCard'
import { PRNode as PRCard, ReviewIcon } from '../components/PRNode'
import { SessionCard } from '../components/SessionCard'
import { GraphView } from '../components/GraphView'
import { api } from '../api'

beforeEach(() => {
  mockFetch.mockReset()
  mockGetGraph.mockReset()
})

// ---------------------------------------------------------------------------
// 1. TaskCard edge cases
// ---------------------------------------------------------------------------

describe('TaskCard edge cases', () => {
  const minimalProject: Project = {
    id: 'proj',
    name: 'P',
    description: '',
    has_tickets: false,
    progress: 0,
    task_counts: {},
    tasks: {
      't1': {
        task_id: 't1',
        project: 'proj',
        description: '',
        type: 'task',
        status: 'not_started',
        group_name: '',
        notes: '',
        priority: 0,
        ticket_id: null,
        ticket_url: null,
        dependencies: [],
        follow_ups: [],
        prs: [],
        created_at: '',
        updated_at: '',
      },
    },
  }

  it('renders minimal card with no PRs, no dependencies, no notes', () => {
    render(<TaskCard project={minimalProject} taskId="t1" actions={[]} />)
    expect(screen.getByText('t1')).toBeInTheDocument()
    expect(screen.queryByTestId('pr-overview')).not.toBeInTheDocument()
    expect(screen.queryByText('Depends on')).not.toBeInTheDocument()
    expect(screen.queryByText('Notes')).not.toBeInTheDocument()
  })

  it('renders task with very long description without crashing', () => {
    const longDesc = 'A'.repeat(5000)
    const proj: Project = {
      ...minimalProject,
      tasks: {
        't1': { ...minimalProject.tasks['t1'], description: longDesc },
      },
    }
    render(<TaskCard project={proj} taskId="t1" actions={[]} />)
    expect(screen.getByText(longDesc)).toBeInTheDocument()
  })

  it('shows ticket link when task has ticket_id even if project has_tickets=false', () => {
    const proj: Project = {
      ...minimalProject,
      has_tickets: false,
      tasks: {
        't1': {
          ...minimalProject.tasks['t1'],
          ticket_id: 'PROJ-99',
          ticket_url: 'https://issues.example.com/PROJ-99',
        },
      },
    }
    render(<TaskCard project={proj} taskId="t1" actions={[]} />)
    const link = screen.getByTestId('ticket-link')
    expect(link).toHaveAttribute('href', 'https://issues.example.com/PROJ-99')
  })

  it('create-ticket button click does not throw when no onOpenAction handler', async () => {
    const proj: Project = {
      ...minimalProject,
      has_tickets: true,
      tasks: {
        't1': {
          ...minimalProject.tasks['t1'],
          ticket_id: null,
          ticket_url: null,
        },
      },
    }
    // No onOpenAction passed -- the optional chain (onOpenAction?.()) should be safe
    render(<TaskCard project={proj} taskId="t1" actions={[]} />)
    const btn = screen.getByTestId('create-ticket-btn')
    await userEvent.click(btn)
    expect(btn).toBeInTheDocument()
  })

  it('sync button rapid double-click shows loading state', async () => {
    // Make checkStatus return a promise that never resolves immediately
    mockFetch.mockImplementation(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => new Promise(() => {}), // hangs
        text: () => new Promise(() => {}),
      }),
    )

    render(<TaskCard project={minimalProject} taskId="t1" actions={[]} />)
    const syncBtn = screen.getByTestId('sync-btn')

    // Click twice rapidly
    await userEvent.click(syncBtn)
    await userEvent.click(syncBtn)

    // The handler sets text to '...' on entry
    expect(syncBtn.textContent).toBe('...')
  })
})

// ---------------------------------------------------------------------------
// 2. PRCard edge cases
// ---------------------------------------------------------------------------

describe('PRCard edge cases', () => {
  const basePR: PR = {
    number: 1,
    url: 'https://github.com/org/repo/pull/1',
    status: 'open',
    title: '',
    ci_status: '',
    review_status: '',
    comment_count: 0,
    additions: 0,
    deletions: 0,
    author: 'dev',
    head_branch: '',
    base_branch: 'main',
    last_updated: '',
  }

  it('renders PR with empty title without crashing', () => {
    render(<PRCard pr={basePR} />)
    expect(screen.getByText('#1')).toBeInTheDocument()
    const titleSpan = document.querySelector('.pr-title')
    expect(titleSpan).toBeInTheDocument()
    expect(titleSpan!.textContent).toBe('')
  })

  it('does not show CI ring when ci_status is empty string', () => {
    render(<PRCard pr={{ ...basePR, ci_status: '' }} />)
    expect(screen.queryByTestId('ci-ring')).not.toBeInTheDocument()
  })

  it('does not show CI ring when ci_status is falsy (undefined)', () => {
    render(<PRCard pr={{ ...basePR, ci_status: undefined as any }} />)
    expect(screen.queryByTestId('ci-ring')).not.toBeInTheDocument()
  })

  it('ReviewIcon returns null for unknown review_status', () => {
    const { container } = render(<ReviewIcon status="some_unknown_status" />)
    expect(container.innerHTML).toBe('')
  })

  it('does not show diff stats when additions=0 and deletions=0', () => {
    render(<PRCard pr={{ ...basePR, additions: 0, deletions: 0 }} showMeta />)
    expect(screen.queryByTestId('diff-stats')).not.toBeInTheDocument()
  })

  it('does not show comment count when count is 0', () => {
    render(<PRCard pr={{ ...basePR, comment_count: 0 }} showMeta />)
    expect(screen.queryByTestId('comment-count')).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 3. SessionCard edge cases
// ---------------------------------------------------------------------------

describe('SessionCard edge cases', () => {
  it('does not call onKill when window.confirm returns false', async () => {
    const onKill = vi.fn()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<SessionCard sessionName="s1" initialStatus="idle" onKill={onKill} />)
    await userEvent.click(screen.getByText('Kill'))
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('Kill session "s1"?'))
    expect(onKill).not.toHaveBeenCalled()
  })

  it('renders session with "stopped" status', () => {
    render(<SessionCard sessionName="s2" initialStatus="stopped" onKill={vi.fn()} />)
    expect(screen.getByText('stopped')).toBeInTheDocument()
    expect(screen.getByText('s2')).toBeInTheDocument()
  })

  it('renders session with very long name without crashing', () => {
    const longName = 'session-' + 'x'.repeat(500)
    render(<SessionCard sessionName={longName} initialStatus="idle" onKill={vi.fn()} />)
    expect(screen.getByText(longName)).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 4. GraphView edge cases
// ---------------------------------------------------------------------------

describe('GraphView edge cases', () => {
  const baseProject: Project = {
    id: 'gp',
    name: 'Graph Project',
    description: '',
    has_tickets: false,
    progress: 0,
    task_counts: {},
    tasks: {},
  }

  it('shows empty message for graph with 0 nodes', async () => {
    const emptyGraph: GraphData = { nodes: [], edges: [], groups: [], has_tickets: false }
    mockGetGraph.mockResolvedValue(emptyGraph)
    render(<GraphView project={baseProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByText('No tasks to graph.')).toBeInTheDocument()
    })
  })

  it('renders single node with no edges', async () => {
    const singleNode: GraphData = {
      nodes: [{
        id: 'only-task',
        task_id: 'only-task',
        project: 'gp',
        description: 'Solo task',
        type: 'feature',
        status: 'in_progress',
        group_name: 'default',
        notes: '',
        priority: 1,
        ticket_id: null,
        ticket_url: null,
        dependencies: [],
        follow_ups: [],
        prs: [],
        created_at: '',
        updated_at: '',
      }],
      edges: [],
      groups: ['default'],
      has_tickets: false,
    }
    mockGetGraph.mockResolvedValue(singleNode)
    render(<GraphView project={baseProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })
    expect(screen.queryByText('No tasks to graph.')).not.toBeInTheDocument()
    expect(screen.queryByText('Failed to load graph data.')).not.toBeInTheDocument()
  })

  it('shows error message when graph fetch fails', async () => {
    mockGetGraph.mockRejectedValue(new Error('server down'))
    render(<GraphView project={baseProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByText('Failed to load graph data.')).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// 5. PRsPage edge cases
// ---------------------------------------------------------------------------

describe('PRsPage edge cases', () => {
  function setupFetchMock(responses: Record<string, unknown>) {
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

  it('shows "No PRs found" when all filters return empty groups', async () => {
    setupFetchMock({ 'all-prs': { groups: {} }, actions: { actions: [] } })
    const { PRsPage } = await import('../pages/PRsPage')
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('No PRs found.')).toBeInTheDocument()
    })
  })

  it('shows "No PRs found" after typing a search that matches nothing', async () => {
    setupFetchMock({ 'all-prs': { groups: {} }, actions: { actions: [] } })
    const { PRsPage } = await import('../pages/PRsPage')
    render(<PRsPage />)
    await waitFor(() => {
      expect(screen.getByText('No PRs found.')).toBeInTheDocument()
    })
    const searchInput = screen.getByTestId('pr-search')
    await userEvent.type(searchInput, 'nonexistent-xyz')
    await waitFor(() => {
      expect(screen.getByText('No PRs found.')).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// 6. API client edge cases
// ---------------------------------------------------------------------------

describe('API client edge cases', () => {
  it('returns null for 204 No Content response', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 204,
      json: () => { throw new Error('should not call json on 204') },
      text: () => Promise.resolve(''),
    })
    const result = await api.killSession('test-session')
    expect(result).toBeNull()
  })

  it('throws on network error (fetch rejects)', async () => {
    mockFetch.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await expect(api.getProjects()).rejects.toThrow('Failed to fetch')
  })

  it('throws on non-OK response with status in message', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: () => Promise.resolve('Internal Server Error'),
    })
    await expect(api.getProjects()).rejects.toThrow('500')
  })

  it('throws when response body is not valid JSON', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.reject(new SyntaxError('Unexpected token')),
      text: () => Promise.resolve('not json'),
    })
    await expect(api.getProject('test')).rejects.toThrow('Unexpected token')
  })

  it('does not call .json() on 204 responses', async () => {
    const jsonSpy = vi.fn(() => { throw new Error('should not call json') })
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 204,
      json: jsonSpy,
      text: () => Promise.resolve(''),
    })
    await api.killSession('sess')
    expect(jsonSpy).not.toHaveBeenCalled()
  })
})
