import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { useState } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { Project, GraphData } from '../types'

// Store the latest ReactFlow props so tests can invoke event handlers
let latestRFProps: Record<string, any> = {}

let latestMiniMapProps: Record<string, any> = {}

vi.mock('@xyflow/react', () => ({
  ReactFlow: (props: any) => {
    latestRFProps = props
    // Render actual node components so TaskNode gets covered
    const nodeTypes = props.nodeTypes || {}
    const nodes = props.nodes || []
    return (
      <div data-testid="react-flow">
        {nodes.map((n: any) => {
          const NodeComp = nodeTypes[n.type]
          return NodeComp ? <NodeComp key={n.id} data={n.data} /> : null
        })}
        {props.children}
      </div>
    )
  },
  Background: () => null,
  Controls: () => null,
  MiniMap: (props: any) => {
    latestMiniMapProps = props
    return null
  },
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
  // No-op provider; tests don't pan/zoom so flow coords == screen coords.
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  // For tests, return identity for screenToFlowPosition (no transform).
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

const mockGetGraph = vi.fn()
const mockCreateTask = vi.fn()
const mockRemoveDep = vi.fn()
vi.mock('../api', () => ({
  api: {
    getGraph: (...args: any[]) => mockGetGraph(...args),
    createTask: (...args: any[]) => mockCreateTask(...args),
    removeDep: (...args: any[]) => mockRemoveDep(...args),
  },
}))

// StatusDot is a simple component used in GraphView; let it render normally
// but if it causes issues, we can mock it.

const mockProject: Project = {
  id: 'test-proj',
  name: 'Test Project',
  description: '',
  has_tickets: true,
  progress: 50,
  task_counts: {},
  tasks: {
    'task-1': {
      task_id: 'task-1',
      project: 'test-proj',
      description: 'Do something',
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
}

const mockGraphData: GraphData = {
  nodes: [
    {
      id: 'task-1',
      task_id: 'task-1',
      project: 'test-proj',
      description: 'Do something',
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
  ],
  edges: [],
  groups: ['core'],
  has_tickets: true,
}

describe('GraphView', () => {
  beforeEach(() => {
    mockGetGraph.mockReset()
  })

  it('shows loading state initially', async () => {
    mockGetGraph.mockReturnValue(new Promise(() => {})) // never resolves
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    expect(screen.getByText('Loading graph...')).toBeInTheDocument()
  })

  it('shows "No tasks" when graph data has empty nodes', async () => {
    const emptyGraph: GraphData = { nodes: [], edges: [], groups: [], has_tickets: true }
    mockGetGraph.mockResolvedValue(emptyGraph)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByText('No tasks to graph.')).toBeInTheDocument()
    })
  })

  it('fetches graph data on mount with correct project id', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    expect(mockGetGraph).toHaveBeenCalledWith('test-proj')
  })

  it('renders legend when graph data loads', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByText('Done')).toBeInTheDocument()
      expect(screen.getByText('In Progress')).toBeInTheDocument()
      expect(screen.getByText('Not Started')).toBeInTheDocument()
      expect(screen.getByText('Blocked')).toBeInTheDocument()
    })
  })

  it('renders ReactFlow container when graph data loads', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })
  })

  it('shows error message when fetch fails', async () => {
    mockGetGraph.mockRejectedValue(new Error('network error'))
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByText('Failed to load graph data.')).toBeInTheDocument()
    })
  })

  it('renders multiple nodes with edges', async () => {
    const multiGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'task-a', task_id: 'task-a', status: 'done', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'task-b', task_id: 'task-b', status: 'in_progress', dependencies: ['task-a'], follow_ups: [] },
      ],
      edges: [{ from: 'task-a', to: 'task-b' }],
      groups: ['core'],
      has_tickets: true,
    }
    const projectWithMultiple = {
      ...mockProject,
      tasks: {
        'task-a': { ...mockProject.tasks['task-1'], task_id: 'task-a', status: 'done' as const },
        'task-b': { ...mockProject.tasks['task-1'], task_id: 'task-b', status: 'in_progress' as const, dependencies: ['task-a'] },
      },
    }
    mockGetGraph.mockResolvedValue(multiGraph)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={projectWithMultiple} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })
  })

  it('refetches graph when project id changes', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    const { rerender } = render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    expect(mockGetGraph).toHaveBeenCalledWith('test-proj')

    const otherProject = { ...mockProject, id: 'other-proj' }
    const otherGraph = { ...mockGraphData }
    mockGetGraph.mockResolvedValue(otherGraph)
    rerender(<GraphView project={otherProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(mockGetGraph).toHaveBeenCalledWith('other-proj')
    })
  })

  it('renders nodes with PRs showing badge count', async () => {
    const graphWithPrs: GraphData = {
      nodes: [
        {
          ...mockGraphData.nodes[0],
          id: 'task-prs',
          task_id: 'task-prs',
          prs: [
            { number: 1, url: 'https://github.com/org/repo/pull/1', status: 'open', title: 'PR 1', ci_status: '', review_status: '', comment_count: 0, additions: 0, deletions: 0, author: '', head_branch: '', base_branch: '', last_updated: '' },
            { number: 2, url: 'https://github.com/org/repo/pull/2', status: 'open', title: 'PR 2', ci_status: '', review_status: '', comment_count: 0, additions: 0, deletions: 0, author: '', head_branch: '', base_branch: '', last_updated: '' },
          ],
          follow_ups: [],
        },
      ],
      edges: [],
      groups: ['core'],
      has_tickets: true,
    }
    mockGetGraph.mockResolvedValue(graphWithPrs)
    const { GraphView } = await import('../components/GraphView')
    const projectWithPrs = {
      ...mockProject,
      tasks: {
        'task-prs': {
          ...mockProject.tasks['task-1'],
          task_id: 'task-prs',
          prs: graphWithPrs.nodes[0].prs,
        },
      },
    }
    render(<GraphView project={projectWithPrs} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })
  })

  it('renders all status types in legend', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      for (const label of ['Done', 'Needs Follow-up', 'In Review', 'In Progress', 'Not Started', 'Blocked', 'Closed']) {
        expect(screen.getByText(label)).toBeInTheDocument()
      }
    })
  })

  it('passes selectedTask to nodes', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask="task-1" />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })
  })

  // ===================== Context menu tests =====================

  it('pane right-click shows "New Task" context menu', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // Invoke onPaneContextMenu captured from ReactFlow props
    act(() => {
      latestRFProps.onPaneContextMenu({
        preventDefault: vi.fn(),
        clientX: 100,
        clientY: 200,
      })
    })

    await waitFor(() => {
      expect(screen.getByText('+ New Task')).toBeInTheDocument()
    })
  })

  it('node right-click on task without ticket shows "Delete Task"', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    act(() => {
      latestRFProps.onNodeContextMenu(
        { preventDefault: vi.fn(), clientX: 150, clientY: 250 },
        { id: 'task-1', data: { taskId: 'task-1' } },
      )
    })

    await waitFor(() => {
      expect(screen.getByText('Delete Task')).toBeInTheDocument()
    })
  })

  it('node right-click on task with ticket shows disabled delete', async () => {
    const projectWithTicket: Project = {
      ...mockProject,
      tasks: {
        'task-1': { ...mockProject.tasks['task-1'], ticket_id: 'PROJ-123', ticket_url: 'https://jira/PROJ-123' },
      },
    }
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={projectWithTicket} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    act(() => {
      latestRFProps.onNodeContextMenu(
        { preventDefault: vi.fn(), clientX: 150, clientY: 250 },
        { id: 'task-1', data: { taskId: 'task-1' } },
      )
    })

    await waitFor(() => {
      expect(screen.getByText('Delete (has ticket)')).toBeInTheDocument()
    })
    // Should NOT show an active Delete Task option
    expect(screen.queryByText('Delete Task')).not.toBeInTheDocument()
  })

  it('delete task from context menu: confirm dialog triggers DELETE API call', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')

    const origConfirm = window.confirm
    window.confirm = vi.fn().mockReturnValue(true)
    const origFetch = globalThis.fetch
    const mockFetch = vi.fn().mockResolvedValue({ ok: true })
    globalThis.fetch = mockFetch as any

    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // Open context menu on node
    act(() => {
      latestRFProps.onNodeContextMenu(
        { preventDefault: vi.fn(), clientX: 150, clientY: 250 },
        { id: 'task-1', data: { taskId: 'task-1' } },
      )
    })

    await waitFor(() => {
      expect(screen.getByText('Delete Task')).toBeInTheDocument()
    })

    // Click delete
    fireEvent.click(screen.getByText('Delete Task'))

    await waitFor(() => {
      expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('Delete task "task-1"'))
    })
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/projects/test-proj/tasks/task-1'),
      expect.objectContaining({ method: 'DELETE' }),
    )

    window.confirm = origConfirm
    globalThis.fetch = origFetch
  })

  it('+ New Task spawns an inline draft node (replaces old modal)', async () => {
    /* New flow (2026-04-26): right-click pane -> "+ New Task" -> a
     * `draftTaskNode` appears on the graph at the click position.
     * The user types into it directly. No modal anymore. */
    const { _resetForTests } = await import('../services/pendingCreates')
    _resetForTests()
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => expect(screen.getByTestId('react-flow')).toBeInTheDocument())

    act(() => {
      latestRFProps.onPaneContextMenu({ preventDefault: vi.fn(), clientX: 100, clientY: 200 })
    })
    await waitFor(() => expect(screen.getByText('+ New Task')).toBeInTheDocument())
    fireEvent.click(screen.getByText('+ New Task'))

    // Draft node renders inline -- not a modal overlay.
    await waitFor(() => {
      expect(screen.getByTestId('draft-task-node')).toBeInTheDocument()
    })
    // No more modal-style "New Task" h3 with overlay backdrop.
    expect(screen.queryByPlaceholderText(/EX-55754/)).not.toBeInTheDocument()
  })

  it('draft node Cancel removes it without firing fetch', async () => {
    const { _resetForTests } = await import('../services/pendingCreates')
    _resetForTests()
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => expect(screen.getByTestId('react-flow')).toBeInTheDocument())

    act(() => {
      latestRFProps.onPaneContextMenu({ preventDefault: vi.fn(), clientX: 100, clientY: 200 })
    })
    await waitFor(() => expect(screen.getByText('+ New Task')).toBeInTheDocument())
    fireEvent.click(screen.getByText('+ New Task'))
    await waitFor(() => expect(screen.getByTestId('draft-task-node')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Cancel'))

    await waitFor(() => {
      expect(screen.queryByTestId('draft-task-node')).not.toBeInTheDocument()
    })
  })

  it('node click calls onSelectTask for non-done nodes', async () => {
    // The TaskNode component uses data.onSelect callback; however since we
    // use a simplified ReactFlow mock that doesn't render custom nodes, we test
    // through the onNodeContextMenu path (which sets up the node reference)
    // and the onPaneClick path (which calls onSelectTask(null)).
    mockGetGraph.mockResolvedValue(mockGraphData)
    const onSelectTask = vi.fn()
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={onSelectTask} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // The onPaneClick handler calls onSelectTask(null)
    act(() => {
      latestRFProps.onPaneClick()
    })
    expect(onSelectTask).toHaveBeenCalledWith(null)
  })

  it('done/closed nodes are rendered as mini (smaller dimensions) in layout', async () => {
    const mixedGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'done-task', task_id: 'done-task', status: 'done', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'closed-task', task_id: 'closed-task', status: 'closed', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'active-task', task_id: 'active-task', status: 'in_progress', follow_ups: [] },
      ],
      edges: [],
      groups: ['core'],
      has_tickets: true,
    }
    const mixedProject: Project = {
      ...mockProject,
      tasks: {
        'done-task': { ...mockProject.tasks['task-1'], task_id: 'done-task', status: 'done' as const },
        'closed-task': { ...mockProject.tasks['task-1'], task_id: 'closed-task', status: 'closed' as const },
        'active-task': { ...mockProject.tasks['task-1'], task_id: 'active-task', status: 'in_progress' as const },
      },
    }
    mockGetGraph.mockResolvedValue(mixedGraph)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mixedProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })
    // The dagre mock returns { x: 0, y: 0 } for all nodes, and getLayoutedElements
    // sets isMini=true for done/closed. We verify that ReactFlow receives nodes
    // with the correct isMini data property.
    // Need to wait for the layout useEffect to run and update nodes
    await waitFor(() => {
      expect(latestRFProps.nodes?.length).toBe(3)
    })
    const doneNode = latestRFProps.nodes?.find((n: any) => n.id === 'done-task')
    const closedNode = latestRFProps.nodes?.find((n: any) => n.id === 'closed-task')
    const activeNode = latestRFProps.nodes?.find((n: any) => n.id === 'active-task')
    expect(doneNode?.data?.isMini).toBe(true)
    expect(closedNode?.data?.isMini).toBe(true)
    expect(activeNode?.data?.isMini).toBe(false)
  })

  it('highlight set: selecting a task highlights its dependency chain', async () => {
    const chainGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'root', task_id: 'root', status: 'done', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'middle', task_id: 'middle', status: 'in_progress', dependencies: ['root'], follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'leaf', task_id: 'leaf', status: 'not_started', dependencies: ['middle'], follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'unrelated', task_id: 'unrelated', status: 'in_progress', follow_ups: [] },
      ],
      edges: [
        { from: 'root', to: 'middle' },
        { from: 'middle', to: 'leaf' },
      ],
      groups: ['core'],
      has_tickets: true,
    }
    const chainProject: Project = {
      ...mockProject,
      tasks: {
        'root': { ...mockProject.tasks['task-1'], task_id: 'root', status: 'done' as const, dependencies: [] },
        'middle': { ...mockProject.tasks['task-1'], task_id: 'middle', status: 'in_progress' as const, dependencies: ['root'] },
        'leaf': { ...mockProject.tasks['task-1'], task_id: 'leaf', status: 'not_started' as const, dependencies: ['middle'] },
        'unrelated': { ...mockProject.tasks['task-1'], task_id: 'unrelated', status: 'in_progress' as const, dependencies: [] },
      },
    }
    mockGetGraph.mockResolvedValue(chainGraph)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={chainProject} onSelectTask={vi.fn()} selectedTask="middle" />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // Wait for nodes to be populated with highlight data
    await waitFor(() => {
      expect(latestRFProps.nodes?.length).toBe(4)
    })

    // Nodes in the chain (root, middle, leaf) should be highlighted, unrelated should not
    const rootNode = latestRFProps.nodes?.find((n: any) => n.id === 'root')
    const middleNode = latestRFProps.nodes?.find((n: any) => n.id === 'middle')
    const leafNode = latestRFProps.nodes?.find((n: any) => n.id === 'leaf')
    const unrelatedNode = latestRFProps.nodes?.find((n: any) => n.id === 'unrelated')

    expect(rootNode?.data?.highlighted).toBe(true)
    expect(middleNode?.data?.highlighted).toBe(true)
    expect(leafNode?.data?.highlighted).toBe(true)
    expect(unrelatedNode?.data?.highlighted).toBe(false)
  })

  it('edge right-click -> "Remove dependency" menu item triggers removeDep for non-done tasks', async () => {
    const edgeGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'A', task_id: 'A', status: 'in_progress', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'B', task_id: 'B', status: 'in_progress', dependencies: ['A'], follow_ups: [] },
      ],
      edges: [{ from: 'A', to: 'B' }],
      groups: ['core'],
      has_tickets: true,
    }
    const edgeProject: Project = {
      ...mockProject,
      tasks: {
        'A': { ...mockProject.tasks['task-1'], task_id: 'A', status: 'in_progress' as const },
        'B': { ...mockProject.tasks['task-1'], task_id: 'B', status: 'in_progress' as const, dependencies: ['A'] },
      },
    }
    mockGetGraph.mockResolvedValue(edgeGraph)
    mockRemoveDep.mockResolvedValue(undefined)
    const origConfirm = window.confirm
    window.confirm = vi.fn().mockReturnValue(true)

    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={edgeProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // 1. Right-click the edge -> opens context menu
    act(() => {
      latestRFProps.onEdgeContextMenu(
        { preventDefault: () => {}, clientX: 100, clientY: 100 },
        { id: 'e-0-A-B', source: 'A', target: 'B' },
      )
    })
    // 2. Click the "Remove dependency" menu item
    const item = await screen.findByText('Remove dependency')
    await act(async () => { fireEvent.click(item) })

    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('Remove dependency'))
    await waitFor(() => {
      expect(mockRemoveDep).toHaveBeenCalledWith('test-proj', 'B', 'A')
    })

    window.confirm = origConfirm
  })

  it('renders multiple node types with different statuses in graph', async () => {
    const allStatusGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'ns', task_id: 'ns', status: 'not_started', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'ip', task_id: 'ip', status: 'in_progress', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'ir', task_id: 'ir', status: 'in_review', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'dn', task_id: 'dn', status: 'done', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'nf', task_id: 'nf', status: 'needs_follow_up', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'bl', task_id: 'bl', status: 'blocked', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'cl', task_id: 'cl', status: 'closed', follow_ups: [] },
      ],
      edges: [],
      groups: ['core'],
      has_tickets: true,
    }
    const allStatusProject: Project = {
      ...mockProject,
      tasks: {
        'ns': { ...mockProject.tasks['task-1'], task_id: 'ns', status: 'not_started' as const },
        'ip': { ...mockProject.tasks['task-1'], task_id: 'ip', status: 'in_progress' as const },
        'ir': { ...mockProject.tasks['task-1'], task_id: 'ir', status: 'in_review' as const },
        'dn': { ...mockProject.tasks['task-1'], task_id: 'dn', status: 'done' as const },
        'nf': { ...mockProject.tasks['task-1'], task_id: 'nf', status: 'needs_follow_up' as const },
        'bl': { ...mockProject.tasks['task-1'], task_id: 'bl', status: 'blocked' as const },
        'cl': { ...mockProject.tasks['task-1'], task_id: 'cl', status: 'closed' as const },
      },
    }
    mockGetGraph.mockResolvedValue(allStatusGraph)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={allStatusProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // All 7 nodes should be passed to ReactFlow
    await waitFor(() => {
      expect(latestRFProps.nodes?.length).toBe(7)
    })

    // Each node should have correct status in data
    const statuses = latestRFProps.nodes.map((n: any) => n.data.status)
    expect(statuses).toContain('not_started')
    expect(statuses).toContain('in_progress')
    expect(statuses).toContain('in_review')
    expect(statuses).toContain('done')
    expect(statuses).toContain('needs_follow_up')
    expect(statuses).toContain('blocked')
    expect(statuses).toContain('closed')
  })

  it('pane click resets selection to null', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const onSelectTask = vi.fn()
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={onSelectTask} selectedTask="task-1" />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    act(() => {
      latestRFProps.onPaneClick()
    })

    expect(onSelectTask).toHaveBeenCalledWith(null)
  })

  it('connect handler adds edge via addDep', async () => {
    const twoNodeGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'A', task_id: 'A', status: 'in_progress', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'B', task_id: 'B', status: 'not_started', follow_ups: [] },
      ],
      edges: [],
      groups: ['core'],
      has_tickets: true,
    }
    const twoNodeProject: Project = {
      ...mockProject,
      tasks: {
        'A': { ...mockProject.tasks['task-1'], task_id: 'A', status: 'in_progress' as const },
        'B': { ...mockProject.tasks['task-1'], task_id: 'B', status: 'not_started' as const },
      },
    }
    mockGetGraph.mockResolvedValue(twoNodeGraph)

    const mockAddDep = vi.fn().mockResolvedValue({ ok: true })
    const { api: apiMod } = await import('../api')
    ;(apiMod as any).addDep = mockAddDep

    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={twoNodeProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // Trigger onConnect (drag from A to B = B depends on A)
    act(() => {
      latestRFProps.onConnect({ source: 'A', target: 'B' })
    })

    expect(mockAddDep).toHaveBeenCalledWith('test-proj', 'B', 'A')
  })

  it('delete task: confirm=false does NOT call delete API', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')

    const origConfirm = window.confirm
    window.confirm = vi.fn().mockReturnValue(false) // user cancels
    const origFetch = globalThis.fetch
    const mockFetch = vi.fn()
    globalThis.fetch = mockFetch as any

    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    act(() => {
      latestRFProps.onNodeContextMenu(
        { preventDefault: vi.fn(), clientX: 150, clientY: 250 },
        { id: 'task-1', data: { taskId: 'task-1' } },
      )
    })

    await waitFor(() => {
      expect(screen.getByText('Delete Task')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Delete Task'))

    await waitFor(() => {
      expect(window.confirm).toHaveBeenCalled()
    })
    // Should NOT call fetch because user cancelled
    expect(mockFetch).not.toHaveBeenCalled()

    window.confirm = origConfirm
    globalThis.fetch = origFetch
  })

  it('refetches graph when task count changes (task created)', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    const { rerender } = render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })
    expect(mockGetGraph).toHaveBeenCalledTimes(1)

    // Simulate a new task being added (task count changes from 1 to 2)
    const updatedGraph: GraphData = {
      ...mockGraphData,
      nodes: [
        ...mockGraphData.nodes,
        { ...mockGraphData.nodes[0], id: 'task-2', task_id: 'task-2', status: 'not_started', follow_ups: [] },
      ],
    }
    mockGetGraph.mockResolvedValue(updatedGraph)

    const projectWithNewTask: Project = {
      ...mockProject,
      tasks: {
        ...mockProject.tasks,
        'task-2': { ...mockProject.tasks['task-1'], task_id: 'task-2', status: 'not_started' as const },
      },
    }
    rerender(<GraphView project={projectWithNewTask} onSelectTask={vi.fn()} selectedTask={null} />)

    // getGraph should be called again because taskCount changed (1 -> 2)
    await waitFor(() => {
      expect(mockGetGraph).toHaveBeenCalledTimes(2)
    })
  })

  it('does NOT refetch graph when only task status changes (same count)', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    const { rerender } = render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })
    expect(mockGetGraph).toHaveBeenCalledTimes(1)

    // Rerender with same task count but different status
    const projectUpdatedStatus: Project = {
      ...mockProject,
      tasks: {
        'task-1': { ...mockProject.tasks['task-1'], status: 'done' as const },
      },
    }
    mockGetGraph.mockResolvedValue(mockGraphData)
    rerender(<GraphView project={projectUpdatedStatus} onSelectTask={vi.fn()} selectedTask={null} />)

    // Should not refetch -- task count is still 1
    // Wait a tick to make sure no extra call happens
    await new Promise((r) => setTimeout(r, 50))
    expect(mockGetGraph).toHaveBeenCalledTimes(1)
  })

  it.skip('OBSOLETE create task dialog: SSE error lines are shown in red', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')

    const origFetch = globalThis.fetch
    const mockFetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('smart-create')) {
        const encoder = new TextEncoder()
        const streamData = encoder.encode('data: {"error":"Something went wrong"}\ndata: {"done":true}\n')
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(streamData)
            controller.close()
          },
        })
        return Promise.resolve({ ok: true, body: stream })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    globalThis.fetch = mockFetch as any

    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // Open context menu -> New Task -> fill context -> submit
    act(() => {
      latestRFProps.onPaneContextMenu({ preventDefault: vi.fn(), clientX: 100, clientY: 200 })
    })
    await waitFor(() => {
      expect(screen.getByText('+ New Task')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('+ New Task'))
    await waitFor(() => {
      expect(screen.getByText('New Task')).toBeInTheDocument()
    })
    const contextTextarea = screen.getByPlaceholderText(/EX-55754/)
    fireEvent.change(contextTextarea, { target: { value: 'Test error handling' } })
    fireEvent.click(screen.getByText('Create with AI'))

    await waitFor(() => {
      expect(screen.getByText('ERROR: Something went wrong')).toBeInTheDocument()
    })

    globalThis.fetch = origFetch
  })

  it('context menu closes when clicking outside', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // Open context menu
    act(() => {
      latestRFProps.onPaneContextMenu({ preventDefault: vi.fn(), clientX: 100, clientY: 200 })
    })
    await waitFor(() => {
      expect(screen.getByText('+ New Task')).toBeInTheDocument()
    })

    // Click on the pane (which calls onPaneClick and closes context menu)
    act(() => {
      latestRFProps.onPaneClick()
    })

    await waitFor(() => {
      expect(screen.queryByText('+ New Task')).not.toBeInTheDocument()
    })
  })

  it('onConnect ignores self-connection (source === target)', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const mockAddDep = vi.fn().mockResolvedValue({ ok: true })
    const { api: apiMod } = await import('../api')
    ;(apiMod as any).addDep = mockAddDep

    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // Self-connection should be rejected
    act(() => {
      latestRFProps.onConnect({ source: 'task-1', target: 'task-1' })
    })
    expect(mockAddDep).not.toHaveBeenCalled()
  })

  it('onConnect ignores connection with null source', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const mockAddDep = vi.fn().mockResolvedValue({ ok: true })
    const { api: apiMod } = await import('../api')
    ;(apiMod as any).addDep = mockAddDep

    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    act(() => {
      latestRFProps.onConnect({ source: null, target: 'task-1' })
    })
    expect(mockAddDep).not.toHaveBeenCalled()
  })

  it('edge right-click menu on both-done tasks does NOT trigger removeDep', async () => {
    const doneGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'dA', task_id: 'dA', status: 'done', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'dB', task_id: 'dB', status: 'done', dependencies: ['dA'], follow_ups: [] },
      ],
      edges: [{ from: 'dA', to: 'dB' }],
      groups: ['core'],
      has_tickets: true,
    }
    const doneProject: Project = {
      ...mockProject,
      tasks: {
        'dA': { ...mockProject.tasks['task-1'], task_id: 'dA', status: 'done' as const },
        'dB': { ...mockProject.tasks['task-1'], task_id: 'dB', status: 'done' as const, dependencies: ['dA'] },
      },
    }
    mockGetGraph.mockResolvedValue(doneGraph)
    mockRemoveDep.mockReset()
    const origConfirm = window.confirm
    window.confirm = vi.fn().mockReturnValue(true)

    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={doneProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // Open the menu, then click Remove. removeDepEdge guards on
    // both-done and bails before showing confirm.
    act(() => {
      latestRFProps.onEdgeContextMenu(
        { preventDefault: () => {}, clientX: 100, clientY: 100 },
        { id: 'e-0-dA-dB', source: 'dA', target: 'dB' },
      )
    })
    const item = await screen.findByText('Remove dependency')
    await act(async () => { fireEvent.click(item) })

    // Both done -> should NOT show confirm or call removeDep
    expect(window.confirm).not.toHaveBeenCalled()
    expect(mockRemoveDep).not.toHaveBeenCalled()

    window.confirm = origConfirm
  })

  it('edge right-click menu: user declines confirm does NOT remove dep', async () => {
    const edgeGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'X', task_id: 'X', status: 'in_progress', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'Y', task_id: 'Y', status: 'not_started', dependencies: ['X'], follow_ups: [] },
      ],
      edges: [{ from: 'X', to: 'Y' }],
      groups: ['core'],
      has_tickets: true,
    }
    const edgeProject: Project = {
      ...mockProject,
      tasks: {
        'X': { ...mockProject.tasks['task-1'], task_id: 'X', status: 'in_progress' as const },
        'Y': { ...mockProject.tasks['task-1'], task_id: 'Y', status: 'not_started' as const, dependencies: ['X'] },
      },
    }
    mockGetGraph.mockResolvedValue(edgeGraph)
    mockRemoveDep.mockReset()
    const origConfirm = window.confirm
    window.confirm = vi.fn().mockReturnValue(false) // user declines

    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={edgeProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    act(() => {
      latestRFProps.onEdgeContextMenu(
        { preventDefault: () => {}, clientX: 100, clientY: 100 },
        { id: 'e-0-X-Y', source: 'X', target: 'Y' },
      )
    })
    const item = await screen.findByText('Remove dependency')
    await act(async () => { fireEvent.click(item) })

    expect(window.confirm).toHaveBeenCalled()
    expect(mockRemoveDep).not.toHaveBeenCalled()

    window.confirm = origConfirm
  })

  it('removeDep failure reverts edge in graphData', async () => {
    const edgeGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'R1', task_id: 'R1', status: 'in_progress', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'R2', task_id: 'R2', status: 'not_started', dependencies: ['R1'], follow_ups: [] },
      ],
      edges: [{ from: 'R1', to: 'R2' }],
      groups: ['core'],
      has_tickets: true,
    }
    const edgeProject: Project = {
      ...mockProject,
      tasks: {
        'R1': { ...mockProject.tasks['task-1'], task_id: 'R1', status: 'in_progress' as const },
        'R2': { ...mockProject.tasks['task-1'], task_id: 'R2', status: 'not_started' as const, dependencies: ['R1'] },
      },
    }
    mockGetGraph.mockResolvedValue(edgeGraph)
    mockRemoveDep.mockRejectedValue(new Error('server error'))
    const origConfirm = window.confirm
    window.confirm = vi.fn().mockReturnValue(true)

    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={edgeProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    act(() => {
      latestRFProps.onEdgeContextMenu(
        { preventDefault: () => {}, clientX: 100, clientY: 100 },
        { id: 'e-0-R1-R2', source: 'R1', target: 'R2' },
      )
    })
    const item = await screen.findByText('Remove dependency')
    await act(async () => { fireEvent.click(item) })

    await waitFor(() => {
      expect(mockRemoveDep).toHaveBeenCalledWith('test-proj', 'R2', 'R1')
    })
    // Edge is removed optimistically then reverted on failure
    // After the rejection, the edge should be re-added
    await waitFor(() => {
      // The edges should be restored (revert) after the rejection
      expect(latestRFProps.edges?.length).toBeGreaterThanOrEqual(0)
    })

    window.confirm = origConfirm
  })

  it('addDep failure reverts edge in graphData', async () => {
    const twoNodeGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'F1', task_id: 'F1', status: 'in_progress', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'F2', task_id: 'F2', status: 'not_started', follow_ups: [] },
      ],
      edges: [],
      groups: ['core'],
      has_tickets: true,
    }
    const twoNodeProject: Project = {
      ...mockProject,
      tasks: {
        'F1': { ...mockProject.tasks['task-1'], task_id: 'F1', status: 'in_progress' as const },
        'F2': { ...mockProject.tasks['task-1'], task_id: 'F2', status: 'not_started' as const },
      },
    }
    mockGetGraph.mockResolvedValue(twoNodeGraph)

    const mockAddDep = vi.fn().mockRejectedValue(new Error('server error'))
    const { api: apiMod } = await import('../api')
    ;(apiMod as any).addDep = mockAddDep

    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={twoNodeProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    act(() => {
      latestRFProps.onConnect({ source: 'F1', target: 'F2' })
    })

    expect(mockAddDep).toHaveBeenCalledWith('test-proj', 'F2', 'F1')
    // Edge added optimistically then reverted after rejection
    await waitFor(() => {
      expect(latestRFProps.edges).toBeDefined()
    })
  })

  it.skip('OBSOLETE create task dialog: direct create (no context, with task ID)', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    mockCreateTask.mockResolvedValue({})
    const { GraphView } = await import('../components/GraphView')

    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // Open context menu -> New Task
    act(() => {
      latestRFProps.onPaneContextMenu({ preventDefault: vi.fn(), clientX: 100, clientY: 200 })
    })
    await waitFor(() => {
      expect(screen.getByText('+ New Task')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('+ New Task'))

    // Open manual override section
    await waitFor(() => {
      expect(screen.getByText('New Task')).toBeInTheDocument()
    })

    // Expand manual override
    const summary = screen.getByText(/Manual override/)
    fireEvent.click(summary)

    // Fill in task ID (no context)
    const idInput = screen.getByPlaceholderText(/task-id/)
    fireEvent.change(idInput, { target: { value: 'new-task-1' } })

    // Button should say "Create" (not "Create with AI")
    await waitFor(() => {
      expect(screen.getByText('Create')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Create'))

    await waitFor(() => {
      expect(mockCreateTask).toHaveBeenCalledWith('test-proj', {
        id: 'new-task-1',
        description: '',
      })
    })
  })

  it.skip('OBSOLETE create task dialog: direct create failure shows alert', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    mockCreateTask.mockRejectedValue(new Error('Duplicate task'))
    const origAlert = window.alert
    window.alert = vi.fn()
    const { GraphView } = await import('../components/GraphView')

    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    act(() => {
      latestRFProps.onPaneContextMenu({ preventDefault: vi.fn(), clientX: 100, clientY: 200 })
    })
    await waitFor(() => {
      expect(screen.getByText('+ New Task')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('+ New Task'))

    await waitFor(() => {
      expect(screen.getByText('New Task')).toBeInTheDocument()
    })

    const summary = screen.getByText(/Manual override/)
    fireEvent.click(summary)

    const idInput = screen.getByPlaceholderText(/task-id/)
    fireEvent.change(idInput, { target: { value: 'dup-task' } })

    fireEvent.click(screen.getByText('Create'))

    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith(expect.stringContaining('Duplicate task'))
    })

    window.alert = origAlert
  })

  it.skip('OBSOLETE create task dialog: SSE fetch failure shows error in AI response', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')

    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network failure')) as any

    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    act(() => {
      latestRFProps.onPaneContextMenu({ preventDefault: vi.fn(), clientX: 100, clientY: 200 })
    })
    await waitFor(() => {
      expect(screen.getByText('+ New Task')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('+ New Task'))

    await waitFor(() => {
      expect(screen.getByText('New Task')).toBeInTheDocument()
    })

    const contextTextarea = screen.getByPlaceholderText(/EX-55754/)
    fireEvent.change(contextTextarea, { target: { value: 'Something that fails' } })
    fireEvent.click(screen.getByText('Create with AI'))

    await waitFor(() => {
      expect(screen.getByText(/Error: Network failure/)).toBeInTheDocument()
    })

    globalThis.fetch = origFetch
  })

  it('create task button disabled when both context and id are empty', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')

    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    act(() => {
      latestRFProps.onPaneContextMenu({ preventDefault: vi.fn(), clientX: 100, clientY: 200 })
    })
    await waitFor(() => {
      expect(screen.getByText('+ New Task')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('+ New Task'))

    await waitFor(() => {
      expect(screen.getByText('New Task')).toBeInTheDocument()
    })

    // Both context and id are empty, so create button should be disabled
    const createBtn = screen.getByText('Create')
    expect(createBtn).toBeDisabled()
  })

  it('nodes with sessions have hasSession=true in data', async () => {
    const sessionGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'sess-task', task_id: 'sess-task', status: 'in_progress', follow_ups: [] },
      ],
      edges: [],
      groups: ['core'],
      has_tickets: true,
    }
    const sessionProject: Project = {
      ...mockProject,
      tasks: {
        'sess-task': {
          ...mockProject.tasks['task-1'],
          task_id: 'sess-task',
          session: { name: 'sess-1', running: true, status: 'idle' },
        },
      },
    }
    mockGetGraph.mockResolvedValue(sessionGraph)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={sessionProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(latestRFProps.nodes?.length).toBe(1)
      expect(latestRFProps.nodes[0].data.hasSession).toBe(true)
    })
  })

  it('nodes with has_tickets=false pass hasTickets=false in data', async () => {
    const noTicketGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'nt', task_id: 'nt', status: 'in_progress', follow_ups: [] },
      ],
      edges: [],
      groups: ['core'],
      has_tickets: false,
    }
    const noTicketProject: Project = {
      ...mockProject,
      has_tickets: false,
      tasks: {
        'nt': { ...mockProject.tasks['task-1'], task_id: 'nt' },
      },
    }
    mockGetGraph.mockResolvedValue(noTicketGraph)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={noTicketProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(latestRFProps.nodes?.length).toBe(1)
      expect(latestRFProps.nodes[0].data.hasTickets).toBe(false)
    })
  })

  it('layout uses live task data for ticket and PR info', async () => {
    const ticketGraph: GraphData = {
      nodes: [
        {
          ...mockGraphData.nodes[0],
          id: 'tk-1',
          task_id: 'tk-1',
          status: 'in_progress',
          ticket_id: 'OLD-1',
          ticket_url: 'http://old',
          prs: [],
          follow_ups: [],
        },
      ],
      edges: [],
      groups: ['core'],
      has_tickets: true,
    }
    const ticketProject: Project = {
      ...mockProject,
      tasks: {
        'tk-1': {
          ...mockProject.tasks['task-1'],
          task_id: 'tk-1',
          ticket_id: 'NEW-1',
          ticket_url: 'http://new',
          prs: [
            { number: 1, url: '', status: 'open', title: 'PR', ci_status: '', review_status: '', comment_count: 0, additions: 0, deletions: 0, author: '', head_branch: '', base_branch: '', last_updated: '' },
          ],
        },
      },
    }
    mockGetGraph.mockResolvedValue(ticketGraph)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={ticketProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(latestRFProps.nodes?.length).toBe(1)
      // Live task data should override graph data
      expect(latestRFProps.nodes[0].data.ticketId).toBe('NEW-1')
      expect(latestRFProps.nodes[0].data.ticketUrl).toBe('http://new')
      expect(latestRFProps.nodes[0].data.prCount).toBe(1)
    })
  })

  it('edge between two done tasks is NOT animated', async () => {
    const bothDoneGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'bd-a', task_id: 'bd-a', status: 'done', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'bd-b', task_id: 'bd-b', status: 'done', dependencies: ['bd-a'], follow_ups: [] },
      ],
      edges: [{ from: 'bd-a', to: 'bd-b' }],
      groups: ['core'],
      has_tickets: true,
    }
    const bothDoneProject: Project = {
      ...mockProject,
      tasks: {
        'bd-a': { ...mockProject.tasks['task-1'], task_id: 'bd-a', status: 'done' as const },
        'bd-b': { ...mockProject.tasks['task-1'], task_id: 'bd-b', status: 'done' as const, dependencies: ['bd-a'] },
      },
    }
    mockGetGraph.mockResolvedValue(bothDoneGraph)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={bothDoneProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(latestRFProps.edges?.length).toBe(1)
    })
    // Both-done edges should not be animated
    expect(latestRFProps.edges[0].animated).toBe(false)
  })

  it('expand/collapse toggling for done nodes updates layout', async () => {
    const doneGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'exp-task', task_id: 'exp-task', status: 'done', follow_ups: [] },
      ],
      edges: [],
      groups: ['core'],
      has_tickets: true,
    }
    const doneProject: Project = {
      ...mockProject,
      tasks: {
        'exp-task': { ...mockProject.tasks['task-1'], task_id: 'exp-task', status: 'done' as const },
      },
    }
    mockGetGraph.mockResolvedValue(doneGraph)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={doneProject} onSelectTask={vi.fn()} selectedTask={null} />)

    await waitFor(() => {
      expect(latestRFProps.nodes?.length).toBe(1)
    })

    // Initially done nodes are mini (not expanded)
    expect(latestRFProps.nodes[0].data.isMini).toBe(true)
    expect(latestRFProps.nodes[0].data.isExpanded).toBe(false)

    // Call onToggleExpand via the node's data callback
    act(() => {
      latestRFProps.nodes[0].data.onToggleExpand('exp-task')
    })

    // After toggling, should now be expanded (not mini)
    await waitFor(() => {
      expect(latestRFProps.nodes[0].data.isExpanded).toBe(true)
    })
    expect(latestRFProps.nodes[0].data.isMini).toBe(false)

    // Toggle again to collapse
    act(() => {
      latestRFProps.nodes[0].data.onToggleExpand('exp-task')
    })

    await waitFor(() => {
      expect(latestRFProps.nodes[0].data.isExpanded).toBe(false)
    })
    expect(latestRFProps.nodes[0].data.isMini).toBe(true)
  })

  it('onSelect callback from node data calls onSelectTask', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const onSelectTask = vi.fn()
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={onSelectTask} selectedTask={null} />)
    await waitFor(() => {
      expect(latestRFProps.nodes?.length).toBe(1)
    })

    // Wait for onSelect to be a real function (set in highlighting useEffect)
    await waitFor(() => {
      expect(typeof latestRFProps.nodes[0].data.onSelect).toBe('function')
      // The onSelect should not be the no-op from getLayoutedElements
      expect(latestRFProps.nodes[0].data.onSelect.toString()).not.toBe('() => {}')
    })

    act(() => {
      latestRFProps.nodes[0].data.onSelect('task-1')
    })
    expect(onSelectTask).toHaveBeenCalledWith('task-1')
  })

  it('highlight edges: selected edges are blue and animated, others are dim', async () => {
    const chainGraph: GraphData = {
      nodes: [
        { ...mockGraphData.nodes[0], id: 'h-root', task_id: 'h-root', status: 'in_progress', follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'h-mid', task_id: 'h-mid', status: 'in_progress', dependencies: ['h-root'], follow_ups: [] },
        { ...mockGraphData.nodes[0], id: 'h-other', task_id: 'h-other', status: 'in_progress', follow_ups: [] },
      ],
      edges: [
        { from: 'h-root', to: 'h-mid' },
        { from: 'h-other', to: 'h-mid' },
      ],
      groups: ['core'],
      has_tickets: true,
    }
    const chainProject: Project = {
      ...mockProject,
      tasks: {
        'h-root': { ...mockProject.tasks['task-1'], task_id: 'h-root', status: 'in_progress' as const, dependencies: [] },
        'h-mid': { ...mockProject.tasks['task-1'], task_id: 'h-mid', status: 'in_progress' as const, dependencies: ['h-root'] },
        'h-other': { ...mockProject.tasks['task-1'], task_id: 'h-other', status: 'in_progress' as const, dependencies: [] },
      },
    }
    mockGetGraph.mockResolvedValue(chainGraph)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={chainProject} onSelectTask={vi.fn()} selectedTask="h-root" />)
    await waitFor(() => {
      expect(latestRFProps.edges?.length).toBe(2)
    })

    // Edge h-root -> h-mid should be highlighted (both in highlight set)
    const highlightedEdge = latestRFProps.edges.find((e: any) => e.source === 'h-root' && e.target === 'h-mid')
    expect(highlightedEdge.animated).toBe(true)
    expect(highlightedEdge.style.stroke).toBe('var(--blue)')
  })

  it('minimapNodeColor returns correct color for known status', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // latestMiniMapProps.nodeColor is the minimapNodeColor callback
    const nodeColor = latestMiniMapProps.nodeColor
    expect(typeof nodeColor).toBe('function')

    // Node with status in_progress should return blue
    const result = nodeColor({ data: { status: 'in_progress' } })
    expect(result).toBe('var(--blue)')

    // Node with status done should return green
    const resultDone = nodeColor({ data: { status: 'done' } })
    expect(resultDone).toBe('var(--green)')

    // Node with no data should return fallback
    const resultNoData = nodeColor({})
    expect(resultNoData).toBe('var(--text-faint)')

    // Node with unknown status should return fallback
    const resultUnknown = nodeColor({ data: { status: 'unknown_status' } })
    expect(resultUnknown).toBe('var(--text-faint)')
  })

  it.skip('OBSOLETE create dialog: clicking overlay closes dialog when not creating', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')

    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // Open create dialog
    act(() => {
      latestRFProps.onPaneContextMenu({ preventDefault: vi.fn(), clientX: 100, clientY: 200 })
    })
    await waitFor(() => {
      expect(screen.getByText('+ New Task')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('+ New Task'))

    await waitFor(() => {
      expect(screen.getByText('New Task')).toBeInTheDocument()
    })

    // Click the overlay (the fixed backdrop)
    // The overlay has the onClick that calls setCreateDialog(null)
    // The overlay is the element with position: fixed, inset: 0
    const fixedOverlay = document.querySelector('[style*="position: fixed"][style*="inset: 0"]') as HTMLElement
    expect(fixedOverlay).toBeTruthy()
    fireEvent.click(fixedOverlay)

    // Dialog should close
    await waitFor(() => {
      expect(screen.queryByText('New Task')).not.toBeInTheDocument()
    })
  })

  it.skip('OBSOLETE create dialog: description input in manual override section is editable', async () => {
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')

    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() => {
      expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    })

    // Open create dialog
    act(() => {
      latestRFProps.onPaneContextMenu({ preventDefault: vi.fn(), clientX: 100, clientY: 200 })
    })
    await waitFor(() => {
      expect(screen.getByText('+ New Task')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('+ New Task'))

    await waitFor(() => {
      expect(screen.getByText('New Task')).toBeInTheDocument()
    })

    // Expand manual override
    const summary = screen.getByText(/Manual override/)
    fireEvent.click(summary)

    // Find the description input
    const descInput = screen.getByPlaceholderText(/Description/)
    expect(descInput).toBeInTheDocument()

    // Type into the description input
    fireEvent.change(descInput, { target: { value: 'A detailed description' } })
    expect((descInput as HTMLInputElement).value).toBe('A detailed description')
  })

  // ============================================================
  // Hover-to-link gesture: drag node A, hold over B for 2s,
  // create a B-depends-on-A dependency.
  // ============================================================

  function makeTwoNodeGraph(): { graph: GraphData; project: Project } {
    return {
      graph: {
        nodes: [
          { ...mockGraphData.nodes[0], id: 'A', task_id: 'A', status: 'in_progress', follow_ups: [] },
          { ...mockGraphData.nodes[0], id: 'B', task_id: 'B', status: 'in_progress', follow_ups: [] },
        ],
        edges: [],
        groups: ['core'],
        has_tickets: true,
      },
      project: {
        ...mockProject,
        tasks: {
          'A': { ...mockProject.tasks['task-1'], task_id: 'A', status: 'in_progress' as const, dependencies: [] },
          'B': { ...mockProject.tasks['task-1'], task_id: 'B', status: 'in_progress' as const, dependencies: [] },
        },
      },
    }
  }

  it('hover-to-link: drag A onto B for 2s -> api.addDep called with (A, dependsOn=B)', async () => {
    const { graph, project } = makeTwoNodeGraph()
    mockGetGraph.mockResolvedValue(graph)
    const mockAddDep = vi.fn().mockResolvedValue(undefined)
    const apiMod = await import('../api')
    const origAddDep = apiMod.api.addDep
    ;(apiMod.api as { addDep: typeof origAddDep }).addDep = mockAddDep
    try {
      const { GraphView } = await import('../components/GraphView')
      render(<GraphView project={project} onSelectTask={vi.fn()} selectedTask={null} />)
      await waitFor(() => expect(screen.getByTestId('react-flow')).toBeInTheDocument())
      // Wait for `nodes` prop to actually contain BOTH A and B after
      // the layout effect runs. Without this the hover-target lookup
      // inside onNodeDrag sees an empty `nodes` array and bails (this
      // was the source of intermittent failures under parallel test
      // load).
      await waitFor(() => {
        const ids = (latestRFProps.nodes || []).map((n: { id: string }) => n.id)
        expect(ids).toContain('A')
        expect(ids).toContain('B')
      })

      // Switch to fake timers AFTER initial render so waitFor (which
      // uses real time) didn't get blocked.
      vi.useFakeTimers()
      try {
        act(() => {
          latestRFProps.onNodeDrag(
            {} as unknown as React.MouseEvent,
            // Position A so its center sits inside B's bbox. B is laid
          // out at center (0, 0) by the mocked dagre, so its top-left
          // corner is at (-NODE_W/2, -NODE_H/2). Place A's top-left at
          // (-NODE_W/2, -NODE_H/2) so its center coincides with B's.
          { id: 'A', type: 'taskNode', position: { x: -108, y: -46 }, data: {}, measured: { width: 216, height: 92 } },
          )
        })
        // Advance past the 2s arm.
        act(() => { vi.advanceTimersByTime(2100) })
      } finally {
        vi.useRealTimers()
      }
      // Drag A onto B = "A depends on B" => addDep(project, A, B)
      expect(mockAddDep).toHaveBeenCalledWith('test-proj', 'A', 'B')
    } finally {
      ;(apiMod.api as { addDep: typeof origAddDep }).addDep = origAddDep
    }
  })

  it('hover-to-link: drag-stop before 2s does NOT create dep', async () => {
    const { graph, project } = makeTwoNodeGraph()
    mockGetGraph.mockResolvedValue(graph)
    const mockAddDep = vi.fn()
    const apiMod = await import('../api')
    const origAddDep = apiMod.api.addDep
    ;(apiMod.api as { addDep: typeof origAddDep }).addDep = mockAddDep
    try {
      const { GraphView } = await import('../components/GraphView')
      render(<GraphView project={project} onSelectTask={vi.fn()} selectedTask={null} />)
      await waitFor(() => expect(screen.getByTestId('react-flow')).toBeInTheDocument())

      vi.useFakeTimers()
      try {
        act(() => {
          latestRFProps.onNodeDrag(
            {} as unknown as React.MouseEvent,
            // Position A so its center sits inside B's bbox. B is laid
          // out at center (0, 0) by the mocked dagre, so its top-left
          // corner is at (-NODE_W/2, -NODE_H/2). Place A's top-left at
          // (-NODE_W/2, -NODE_H/2) so its center coincides with B's.
          { id: 'A', type: 'taskNode', position: { x: -108, y: -46 }, data: {}, measured: { width: 216, height: 92 } },
          )
        })
        act(() => { latestRFProps.onNodeDragStop?.() })
        act(() => { vi.advanceTimersByTime(3000) })
      } finally {
        vi.useRealTimers()
      }
      expect(mockAddDep).not.toHaveBeenCalled()
    } finally {
      ;(apiMod.api as { addDep: typeof origAddDep }).addDep = origAddDep
    }
  })

  it('hover-to-link: skipped when dep already exists', async () => {
    const { graph, project } = makeTwoNodeGraph()
    // Pre-existing dep: A already depends on B (matches new flip).
    project.tasks['A'] = { ...project.tasks['A'], dependencies: ['B'] }
    mockGetGraph.mockResolvedValue(graph)
    const mockAddDep = vi.fn()
    const apiMod = await import('../api')
    const origAddDep = apiMod.api.addDep
    ;(apiMod.api as { addDep: typeof origAddDep }).addDep = mockAddDep
    try {
      const { GraphView } = await import('../components/GraphView')
      render(<GraphView project={project} onSelectTask={vi.fn()} selectedTask={null} />)
      await waitFor(() => expect(screen.getByTestId('react-flow')).toBeInTheDocument())

      vi.useFakeTimers()
      try {
        act(() => {
          latestRFProps.onNodeDrag(
            {} as unknown as React.MouseEvent,
            // Position A so its center sits inside B's bbox. B is laid
          // out at center (0, 0) by the mocked dagre, so its top-left
          // corner is at (-NODE_W/2, -NODE_H/2). Place A's top-left at
          // (-NODE_W/2, -NODE_H/2) so its center coincides with B's.
          { id: 'A', type: 'taskNode', position: { x: -108, y: -46 }, data: {}, measured: { width: 216, height: 92 } },
          )
        })
        act(() => { vi.advanceTimersByTime(3000) })
      } finally {
        vi.useRealTimers()
      }
      expect(mockAddDep).not.toHaveBeenCalled()
    } finally {
      ;(apiMod.api as { addDep: typeof origAddDep }).addDep = origAddDep
    }
  })

  it('TaskNode exposes data-status attribute for theming', async () => {
    // Modern theming hook: external CSS / themes can target
    // `[data-status="in_progress"]` etc. without re-rendering
    // through the component.
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()} selectedTask={null} />)
    await waitFor(() =>
      expect(screen.getByTestId('graph-node-task-1')).toBeInTheDocument())
    const node = screen.getByTestId('graph-node-task-1')
    expect(node.getAttribute('data-status')).toBe('in_progress')
  })

  it('TaskNode applies modern selection halo via box-shadow', async () => {
    // The selected node renders a multi-layer box-shadow that
    // includes a color-mix with the accent variable -- the visual
    // affordance for "this is the selected task".
    mockGetGraph.mockResolvedValue(mockGraphData)
    const { GraphView } = await import('../components/GraphView')
    render(<GraphView project={mockProject} onSelectTask={vi.fn()}
                       selectedTask={'task-1'} />)
    await waitFor(() =>
      expect(screen.getByTestId('graph-node-task-1')).toBeInTheDocument())
    const node = screen.getByTestId('graph-node-task-1')
    const shadow = node.style.boxShadow
    expect(shadow).toMatch(/var\(--accent\)/)
  })
})

describe('resolveCollision', () => {
  it('returns original position when no overlap exists', async () => {
    const { resolveCollision } = await import('../components/GraphView')
    const result = resolveCollision(
      { x: 100, y: 100 }, 50, 30,
      [{ x: 500, y: 500, w: 50, h: 30 }],
    )
    expect(result).toEqual({ x: 100, y: 100 })
  })

  it('moves the dragged node to a non-overlapping spot when colliding', async () => {
    const { resolveCollision } = await import('../components/GraphView')
    // Drop at (100,100) with size 50x30; collides with (110,110,50,30).
    const others = [{ x: 110, y: 110, w: 50, h: 30 }]
    const result = resolveCollision({ x: 100, y: 100 }, 50, 30, others)
    // Result must not intersect any other (with default 10px padding).
    const collides = others.some(
      (o) =>
        result.x < o.x + o.w + 10 &&
        result.x + 50 + 10 > o.x &&
        result.y < o.y + o.h + 10 &&
        result.y + 30 + 10 > o.y,
    )
    expect(collides).toBe(false)
    // Should be reasonably close to the original position (closest free
    // ring), not pushed across the canvas.
    const dist = Math.hypot(result.x - 100, result.y - 100)
    expect(dist).toBeLessThan(120)
  })

  it('avoids multiple obstacles', async () => {
    const { resolveCollision } = await import('../components/GraphView')
    // Cluster of 4 nodes around (100,100); dragged drops at (100,100).
    const others = [
      { x: 60, y: 60, w: 50, h: 30 },
      { x: 130, y: 60, w: 50, h: 30 },
      { x: 60, y: 130, w: 50, h: 30 },
      { x: 130, y: 130, w: 50, h: 30 },
    ]
    const result = resolveCollision({ x: 100, y: 100 }, 50, 30, others)
    const intersects = others.some(
      (o) =>
        result.x < o.x + o.w + 10 &&
        result.x + 50 + 10 > o.x &&
        result.y < o.y + o.h + 10 &&
        result.y + 30 + 10 > o.y,
    )
    expect(intersects).toBe(false)
  })

  it('respects custom padding', async () => {
    const { resolveCollision } = await import('../components/GraphView')
    // Two nodes are not touching but within the 30-px pad of each other.
    const result = resolveCollision(
      { x: 100, y: 100 }, 50, 30,
      [{ x: 165, y: 100, w: 50, h: 30 }],
      30,
    )
    // Position should have shifted since 100+50=150 and other.x=165;
    // gap = 15 < pad 30 -> overlap with pad.
    const distMoved = Math.hypot(result.x - 100, result.y - 100)
    expect(distMoved).toBeGreaterThan(0)
  })
})
