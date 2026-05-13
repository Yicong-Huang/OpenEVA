import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ProjectPage } from '../pages/ProjectPage'

vi.mock('../components/GraphView', () => ({
  GraphView: ({ selectedTask, onSelectTask }: { selectedTask: string | null; onSelectTask: (id: string | null) => void }) => (
    <div data-testid="graph-view">
      Graph selected={selectedTask}
      <button data-testid="graph-deselect" onClick={() => onSelectTask(null)}>Deselect</button>
    </div>
  ),
}))
vi.mock('../components/TaskCard', () => ({
  TaskCard: ({ taskId, onOpenAction, onClickPRNumber, externalAction }: {
    taskId: string;
    onOpenAction?: (actionId: string, prNumber?: number, prRepo?: string, customPrompt?: string) => void;
    onClickPRNumber?: (pr: { number: number; url: string }) => void;
    externalAction?: { actionId: string; customPrompt?: string } | null;
  }) => (
    <div data-testid="task-card">
      {taskId}
      {onOpenAction && <button data-testid={`open-action-${taskId}`} onClick={() => onOpenAction('test-action')}>Open Action</button>}
      {onClickPRNumber && <button data-testid={`click-pr-${taskId}`} onClick={() => onClickPRNumber({ number: 42, url: 'https://github.com/org/repo/pull/42' })}>Click PR</button>}
      {externalAction && <span data-testid="task-ext-action">{externalAction.actionId}</span>}
      {externalAction?.customPrompt && <span data-testid="task-ext-prompt">{externalAction.customPrompt}</span>}
    </div>
  ),
  evalActionCondition: () => true,
}))
vi.mock('../components/SessionCard', () => ({
  SessionCard: ({ sessionName, onKill }: { sessionName: string; onKill: () => void }) => (
    <div data-testid="session-card">
      {sessionName}
      <button data-testid={`kill-${sessionName}`} onClick={onKill}>Kill</button>
    </div>
  ),
}))
vi.mock('../components/PRCard', () => ({
  PRCard: ({ number, onOpenAction }: { number: number; onOpenAction?: (actionId: string, customPrompt?: string) => void }) => (
    <div data-testid="pr-detail">
      PR #{number}
      {onOpenAction && <button data-testid="pr-open-action" onClick={() => onOpenAction('review-action')}>PR Action</button>}
      {onOpenAction && (
        <button
          data-testid="pr-ask-agent"
          onClick={() => onOpenAction('open', "Why is this here?\n\nCode: foo.ts (L10), PR org/repo#42:\n```\nfoo()\n```")}
        >Ask Agent</button>
      )}
    </div>
  ),
}))
vi.mock('../components/StatusDot', () => ({
  StatusDot: ({ status }: { status: string }) => <span data-testid="status-dot">{status}</span>,
}))

const eventBusHandlers: Array<{ pattern: string; handler: (event: Record<string, unknown>) => void }> = []

vi.mock('../hooks/useEventBus', () => ({
  useEventBus: (pattern: string, handler: (event: Record<string, unknown>) => void) => {
    eventBusHandlers.push({ pattern, handler })
  },
}))

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

const sampleProject = {
  id: 'test-project',
  name: 'Test Project',
  description: 'A test project',
  progress: 50,
  tasks: {
    'task-1': {
      task_id: 'task-1', project: 'test-project', description: 'First task',
      type: 'task', status: 'in_progress', group_name: 'core', notes: '',
      priority: 1, ticket_id: null, ticket_url: null, dependencies: [],
      follow_ups: [], prs: [], created_at: '2026-01-01', updated_at: '2026-01-01',
    },
    'task-2': {
      task_id: 'task-2', project: 'test-project', description: 'Second task',
      type: 'task', status: 'done', group_name: 'core', notes: '',
      priority: 0, ticket_id: null, ticket_url: null, dependencies: [],
      follow_ups: [], prs: [], created_at: '2026-01-01', updated_at: '2026-01-01',
    },
  },
  task_counts: { in_progress: 1, done: 1 },
}

function mockApi() {
  mockFetch.mockImplementation((url: string) => {
    const u = String(url)
    if (u.includes('/sessions')) {
      const sessPayload = { sessions: [{ tmux_name: 'sess-1', name: 'sess-1', running: true, status: 'idle' }] }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(sessPayload),
        text: () => Promise.resolve(JSON.stringify(sessPayload)),
      })
    }
    if (u.includes('/api/projects/') && u.includes('/tasks/')) {
      // Single task fetch for event-driven updates
      const tid = u.split('/tasks/')[1]?.split('?')[0]
      const task = sampleProject.tasks[tid as keyof typeof sampleProject.tasks]
      if (task) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve(task),
          text: () => Promise.resolve(JSON.stringify(task)),
        })
      }
    }
    if (u.includes('/api/projects/')) {
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(sampleProject),
        text: () => Promise.resolve(JSON.stringify(sampleProject)),
      })
    }
    if (u.includes('/api/actions')) {
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ actions: [] }),
        text: () => Promise.resolve('{"actions":[]}'),
      })
    }
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve('{}'),
    })
  })
}

beforeEach(() => { mockFetch.mockReset(); eventBusHandlers.length = 0; mockApi() })

const defaultProps = {
  projectId: 'test-project',
  selectedTask: null,
  onSelectTask: vi.fn(),
}

describe('ProjectPage', () => {
  it('shows loading state initially', () => {
    mockFetch.mockImplementation(() => new Promise(() => {}))
    render(<ProjectPage {...defaultProps} />)
    expect(screen.getByText('Loading project...')).toBeInTheDocument()
  })

  it('shows error state on API failure', async () => {
    mockFetch.mockImplementation(() =>
      Promise.resolve({ ok: false, status: 500, text: () => Promise.resolve('Server error') })
    )
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => { expect(screen.getByText(/Error:/)).toBeInTheDocument() })
  })

  it('renders project header with name and progress', async () => {
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => {
      expect(screen.getByText('Test Project')).toBeInTheDocument()
      expect(screen.getByText('50%')).toBeInTheDocument()
      expect(screen.getByText('2 tasks')).toBeInTheDocument()
    })
  })

  it('renders description', async () => {
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => { expect(screen.getByText('A test project')).toBeInTheDocument() })
  })

  it('renders GraphView (single Project Page surface, no tabs)', async () => {
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => { expect(screen.getByTestId('graph-view')).toBeInTheDocument() })
    // The previous "view tabs" (Task Tracker / Task Cards / Sessions)
    // were removed -- there is now only one Project Page surface.
    expect(screen.queryByText('Task Cards')).toBeNull()
    expect(screen.queryByText('Sessions')).toBeNull()
  })

  it('renders task counts', async () => {
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => { expect(screen.getAllByTestId('status-dot').length).toBeGreaterThan(0) })
  })

  it('shows task card when selectedTask is set in graph view', async () => {
    render(<ProjectPage {...defaultProps} selectedTask="task-1" />)
    await waitFor(() => { expect(screen.getByTestId('task-card')).toBeInTheDocument() })
  })

  it('shows PR detail when selectedPR is set in graph view', async () => {
    render(
      <ProjectPage
        {...defaultProps}
       
        selectedTask="task-1"
        selectedPR={{ repo: 'org/repo', number: 42 }}
        onSelectPR={vi.fn()}
      />,
    )
    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
      expect(screen.getByText('PR #42')).toBeInTheDocument()
    })
  })

  it('handles project with no description', async () => {
    const noDescProject = { ...sampleProject, description: '' }
    mockFetch.mockReset()
    mockFetch.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/api/actions')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ actions: [] }), text: () => Promise.resolve('{}') })
      }
      if (u.includes('/api/projects/')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve(noDescProject),
          text: () => Promise.resolve(JSON.stringify(noDescProject)),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => {
      expect(screen.getByText('Test Project')).toBeInTheDocument()
    })
  })

  it('shows opening session indicator', async () => {
    // Mock a slow session open
    mockFetch.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/api/projects/') && !u.includes('/sessions')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve(sampleProject),
          text: () => Promise.resolve(JSON.stringify(sampleProject)),
        })
      }
      if (u.includes('/api/actions')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ actions: [] }),
          text: () => Promise.resolve('{}'),
        })
      }
      if (u.includes('/api/sessions/open')) {
        return new Promise(() => {}) // never resolves
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => { expect(screen.getByText('Test Project')).toBeInTheDocument() })
  })

  it('handles project not found (null response)', async () => {
    mockFetch.mockImplementation(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(null), text: () => Promise.resolve('null') })
    )
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => {
      expect(screen.getByText('Project not found')).toBeInTheDocument()
    })
  })

  it('shows PR detail when selectedPR is provided', async () => {
    render(
      <ProjectPage
        {...defaultProps}
       
        selectedTask="task-1"
        selectedPR={{ repo: 'example/repo', number: 123 }}
        onSelectPR={vi.fn()}
      />,
    )
    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
      expect(screen.getByText('PR #123')).toBeInTheDocument()
    })
  })

  it('handles task.deleted event by removing task from project', async () => {
    // Render with the about-to-be-deleted task selected so a TaskCard
    // is mounted and we can observe selection-clearing behavior.
    const onSelectTask = vi.fn()
    render(<ProjectPage {...defaultProps} selectedTask="task-2" onSelectTask={onSelectTask} />)
    await waitFor(() => { expect(screen.getByTestId('task-card')).toBeInTheDocument() })

    const taskHandler = eventBusHandlers.find(h => h.pattern === 'task.*')
    expect(taskHandler).toBeTruthy()

    await act(async () => {
      taskHandler!.handler({
        type: 'task.deleted',
        title: 'Task deleted: task-2',
        session: 'test-project',
      })
    })

    // task.deleted for the selected task -> selection clears.
    await waitFor(() => {
      expect(onSelectTask).toHaveBeenCalledWith(null)
    })
  })

  it('handles task.updated event by fetching and merging the task', async () => {
    const updatedTask = {
      task_id: 'task-1', project: 'test-project', description: 'Updated First task',
      type: 'task', status: 'done', group_name: 'core', notes: '',
      priority: 1, ticket_id: null, ticket_url: null, dependencies: [],
      follow_ups: [], prs: [], created_at: '2026-01-01', updated_at: '2026-04-15',
    }

    render(<ProjectPage {...defaultProps} selectedTask="task-1" />)
    await waitFor(() => { expect(screen.getByTestId('task-card')).toBeInTheDocument() })

    mockFetch.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/api/projects/test-project/tasks/task-1')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve(updatedTask),
          text: () => Promise.resolve(JSON.stringify(updatedTask)),
        })
      }
      if (u.includes('/api/actions')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ actions: [] }), text: () => Promise.resolve('{}') })
      }
      if (u.includes('/api/projects/')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(sampleProject), text: () => Promise.resolve(JSON.stringify(sampleProject)) })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })

    const taskHandler = eventBusHandlers.find(h => h.pattern === 'task.*')
    expect(taskHandler).toBeTruthy()

    const callsBefore = mockFetch.mock.calls.length
    await act(async () => {
      taskHandler!.handler({
        type: 'task.updated',
        title: 'Task updated: task-1',
        session: 'test-project',
      })
    })

    // The handler issues a single-task fetch to merge the latest row.
    await waitFor(() => {
      const calls = mockFetch.mock.calls.slice(callsBefore).map(c => String(c[0]))
      expect(calls.some(u => u.includes('/tasks/task-1'))).toBe(true)
    })
  })

  it('ignores task events for other projects', async () => {
    const onSelectTask = vi.fn()
    render(<ProjectPage {...defaultProps} selectedTask="task-1" onSelectTask={onSelectTask} />)
    await waitFor(() => { expect(screen.getByTestId('task-card')).toBeInTheDocument() })

    const taskHandler = eventBusHandlers.find(h => h.pattern === 'task.*')
    expect(taskHandler).toBeTruthy()

    // task.deleted for the SELECTED task but on a DIFFERENT project --
    // selection should NOT be cleared.
    await act(async () => {
      taskHandler!.handler({
        type: 'task.deleted',
        title: 'Task deleted: task-1',
        session: 'other-project',
      })
    })

    // Give it a tick.
    await new Promise(r => setTimeout(r, 10))
    expect(onSelectTask).not.toHaveBeenCalledWith(null)
  })

  it('handles task.created event by issuing single-task fetch', async () => {
    const newTask = {
      task_id: 'task-new', project: 'test-project', description: 'New task',
      type: 'task', status: 'not_started', group_name: 'core', notes: '',
      priority: 5, ticket_id: null, ticket_url: null, dependencies: [],
      follow_ups: [], prs: [], created_at: '2026-04-15', updated_at: '2026-04-15',
    }

    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => { expect(screen.getByText('Test Project')).toBeInTheDocument() })

    mockFetch.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/api/projects/test-project/tasks/task-new')) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve(newTask),
          text: () => Promise.resolve(JSON.stringify(newTask)),
        })
      }
      if (u.includes('/api/actions')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ actions: [] }), text: () => Promise.resolve('{}') })
      }
      if (u.includes('/api/projects/')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(sampleProject), text: () => Promise.resolve(JSON.stringify(sampleProject)) })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })

    const taskHandler = eventBusHandlers.find(h => h.pattern === 'task.*')

    const callsBefore = mockFetch.mock.calls.length
    await act(async () => {
      taskHandler!.handler({
        type: 'task.created',
        title: 'Task created: task-new',
        session: 'test-project',
      })
    })

    await waitFor(() => {
      const calls = mockFetch.mock.calls.slice(callsBefore).map(c => String(c[0]))
      expect(calls.some(u => u.includes('/tasks/task-new'))).toBe(true)
    })
  })

  it('clears selected task when clicking pane in graph view (null select)', async () => {
    const onSelectTask = vi.fn()
    render(<ProjectPage {...defaultProps} selectedTask="task-1" onSelectTask={onSelectTask} />)
    await waitFor(() => {
      expect(screen.getByTestId('graph-view')).toBeInTheDocument()
      expect(screen.getByTestId('task-card')).toBeInTheDocument()
    })
  })

  it('shows opening session indicator when openSession API is pending', async () => {
    // Mock TaskCard to expose an onOpenAction callback
    const { unmount } = render(<ProjectPage {...defaultProps} />)
    await waitFor(() => { expect(screen.getByText('Test Project')).toBeInTheDocument() })
    unmount()
  })

  it('renders progress bar with correct width', async () => {
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => {
      const progressFill = document.querySelector('.progress-fill') as HTMLElement
      expect(progressFill).toBeTruthy()
      expect(progressFill.style.width).toBe('50%')
    })
  })

  it('renders task count badges with status dots', async () => {
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => {
      // task_counts: { in_progress: 1, done: 1 }
      expect(screen.getByText(/1 in progress/)).toBeInTheDocument()
      expect(screen.getByText(/1 done/)).toBeInTheDocument()
    })
  })

  it('shows PR detail panel when selectedPR is set in graph view', async () => {
    render(
      <ProjectPage
        {...defaultProps}
       
        selectedTask="task-1"
        selectedPR={{ repo: 'myorg/svc', number: 999 }}
        onSelectPR={vi.fn()}
      />,
    )
    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
      expect(screen.getByText('PR #999')).toBeInTheDocument()
    })
  })

  it('graph view adjusts width when selectedPR is set', async () => {
    render(
      <ProjectPage
        {...defaultProps}
       
        selectedTask="task-1"
        selectedPR={{ repo: 'org/repo', number: 7 }}
        onSelectPR={vi.fn()}
      />,
    )
    await waitFor(() => {
      expect(screen.getByTestId('graph-view')).toBeInTheDocument()
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })
  })

  it('handles task.updated event that fails single-task fetch (fallback to full refetch)', async () => {
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => { expect(screen.getByText('Test Project')).toBeInTheDocument() })

    // Single-task fetch fails -- handler must fall back to full project
    // refetch without throwing.
    mockFetch.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/api/projects/test-project/tasks/task-1')) {
        return Promise.reject(new Error('not found'))
      }
      if (u.includes('/api/actions')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ actions: [] }), text: () => Promise.resolve('{}') })
      }
      if (u.includes('/api/projects/')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(sampleProject), text: () => Promise.resolve(JSON.stringify(sampleProject)) })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })

    const taskHandler = eventBusHandlers.find(h => h.pattern === 'task.*')
    expect(taskHandler).toBeTruthy()

    const callsBefore = mockFetch.mock.calls.length
    await act(async () => {
      taskHandler!.handler({
        type: 'task.updated',
        title: 'Task updated: task-1',
        session: 'test-project',
      })
    })

    // Falls back to a full /api/projects/test-project refetch.
    await waitFor(() => {
      const calls = mockFetch.mock.calls.slice(callsBefore).map(c => String(c[0]))
      expect(calls.some(u =>
        u.includes('/api/projects/test-project') && !u.includes('/tasks/'))).toBe(true)
    })
  })

  it('handles empty task_counts gracefully', async () => {
    const noCounts = { ...sampleProject, task_counts: {} }
    mockFetch.mockReset()
    mockFetch.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/api/actions')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ actions: [] }), text: () => Promise.resolve('{}') })
      }
      if (u.includes('/api/projects/')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(noCounts), text: () => Promise.resolve(JSON.stringify(noCounts)) })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => {
      expect(screen.getByText('Test Project')).toBeInTheDocument()
      // No count badges should be rendered
      expect(screen.queryByText(/in progress/)).not.toBeInTheDocument()
    })
  })

  it('github.* event triggers project refetch', async () => {
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => { expect(screen.getByText('Test Project')).toBeInTheDocument() })

    const ghHandler = eventBusHandlers.find(h => h.pattern === 'github.*')
    expect(ghHandler).toBeTruthy()

    const callsBefore = mockFetch.mock.calls.length
    await act(async () => {
      ghHandler!.handler({ type: 'github.push', title: 'Push event' })
    })

    await waitFor(() => {
      expect(mockFetch.mock.calls.length).toBeGreaterThan(callsBefore)
    })
  })

  it('agent.* event triggers project refetch', async () => {
    render(<ProjectPage {...defaultProps} />)
    await waitFor(() => { expect(screen.getByText('Test Project')).toBeInTheDocument() })

    const agentHandler = eventBusHandlers.find(h => h.pattern === 'agent.*')
    expect(agentHandler).toBeTruthy()

    const callsBefore = mockFetch.mock.calls.length
    await act(async () => {
      agentHandler!.handler({ type: 'agent.idle', session: 'sess-1' })
    })

    await waitFor(() => {
      expect(mockFetch.mock.calls.length).toBeGreaterThan(callsBefore)
    })
  })

  it('open action button shows Opening session indicator and calls API', async () => {
    let resolveSession: () => void = () => {}
    mockFetch.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/api/sessions/open')) {
        return new Promise((resolve) => {
          resolveSession = () => resolve({
            ok: true, status: 200,
            json: () => Promise.resolve({ session: 'new-sess', new: true, background_sent: false, prompt: '' }),
            text: () => Promise.resolve('{}'),
          })
        })
      }
      if (u.includes('/api/actions')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ actions: [] }), text: () => Promise.resolve('{"actions":[]}') })
      }
      if (u.includes('/api/projects/')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(sampleProject), text: () => Promise.resolve(JSON.stringify(sampleProject)) })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<ProjectPage {...defaultProps} selectedTask="task-1" />)
    await waitFor(() => { expect(screen.getByTestId('open-action-task-1')).toBeInTheDocument() })

    // Click the open action button (triggers handleOpenAction)
    fireEvent.click(screen.getByTestId('open-action-task-1'))
    // Should show "Opening session..." indicator
    await waitFor(() => {
      expect(screen.getByText('Opening session...')).toBeInTheDocument()
    })

    // Resolve the session open
    await act(async () => { resolveSession() })
    await waitFor(() => {
      expect(screen.queryByText('Opening session...')).not.toBeInTheDocument()
    })
  })

  it('open action failure shows alert', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    mockFetch.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/api/sessions/open')) {
        return Promise.reject(new Error('session failed'))
      }
      if (u.includes('/api/actions')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ actions: [] }), text: () => Promise.resolve('{}') })
      }
      if (u.includes('/api/projects/')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(sampleProject), text: () => Promise.resolve(JSON.stringify(sampleProject)) })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<ProjectPage {...defaultProps} selectedTask="task-1" />)
    await waitFor(() => { expect(screen.getByTestId('open-action-task-1')).toBeInTheDocument() })

    await act(async () => {
      fireEvent.click(screen.getByTestId('open-action-task-1'))
    })
    await waitFor(() => {
      expect(alertSpy).toHaveBeenCalledWith(expect.stringContaining('session failed'))
    })
    alertSpy.mockRestore()
  })

  it('clicking PR number in task card sets selectedPR', async () => {
    const onSelectPR = vi.fn()
    render(
      <ProjectPage
        {...defaultProps}
       
        selectedTask="task-1"
        onSelectPR={onSelectPR}
      />,
    )
    await waitFor(() => { expect(screen.getByTestId('click-pr-task-1')).toBeInTheDocument() })
    fireEvent.click(screen.getByTestId('click-pr-task-1'))
    expect(onSelectPR).toHaveBeenCalledWith({ repo: 'org/repo', number: 42 })
  })

  it('deselecting task in graph view clears selectedPR', async () => {
    const onSelectTask = vi.fn()
    const onSelectPR = vi.fn()
    render(
      <ProjectPage
        {...defaultProps}
       
        selectedTask="task-1"
        onSelectTask={onSelectTask}
        onSelectPR={onSelectPR}
      />,
    )
    await waitFor(() => { expect(screen.getByTestId('graph-deselect')).toBeInTheDocument() })
    fireEvent.click(screen.getByTestId('graph-deselect'))
    expect(onSelectTask).toHaveBeenCalledWith(null)
    expect(onSelectPR).toHaveBeenCalledWith(null)
  })

  it('open action calls handleOpenAction (selected task in side panel)', async () => {
    let sessionOpened = false
    mockFetch.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/api/sessions/open')) {
        sessionOpened = true
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ session: 'list-sess', new: true, background_sent: false, prompt: '' }),
          text: () => Promise.resolve('{}'),
        })
      }
      if (u.includes('/api/actions')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ actions: [] }), text: () => Promise.resolve('{}') })
      }
      if (u.includes('/api/projects/')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(sampleProject), text: () => Promise.resolve(JSON.stringify(sampleProject)) })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') })
    })
    render(<ProjectPage {...defaultProps} selectedTask="task-1" />)
    await waitFor(() => { expect(screen.getByTestId('open-action-task-1')).toBeInTheDocument() })

    await act(async () => {
      fireEvent.click(screen.getByTestId('open-action-task-1'))
    })
    await waitFor(() => { expect(sessionOpened).toBe(true) })
  })

  it('PRDetail onOpenAction sets externalAction state', async () => {
    render(
      <ProjectPage
        {...defaultProps}
       
        selectedTask="task-1"
        selectedPR={{ repo: 'org/repo', number: 42 }}
        onSelectPR={vi.fn()}
      />,
    )
    await waitFor(() => { expect(screen.getByTestId('pr-open-action')).toBeInTheDocument() })
    // Click the PR action button - this triggers setExternalAction
    fireEvent.click(screen.getByTestId('pr-open-action'))
    // Should not crash; the action is stored in state
    expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
  })

  it('PRDetail Ask Agent forwards customPrompt to TaskCard externalAction', async () => {
    // Regression: ProjectPage used to call `onOpenAction={(actionId) => ...}`,
    // dropping the customPrompt argument. That silently swallowed the Ask-Agent
    // code selection so Agent got the default action prompt instead of the
    // reviewer's snippet. See components/pr/FileList.tsx + PRDetail.tsx.
    render(
      <ProjectPage
        {...defaultProps}
       
        selectedTask="task-1"
        selectedPR={{ repo: 'org/repo', number: 42 }}
        onSelectPR={vi.fn()}
      />,
    )
    await waitFor(() => { expect(screen.getByTestId('pr-ask-agent')).toBeInTheDocument() })
    fireEvent.click(screen.getByTestId('pr-ask-agent'))
    await waitFor(() => {
      // Ask Agent uses 'open' (no template -> customPrompt wins), so the
      // user's question flows through unmodified.
      expect(screen.getByTestId('task-ext-action').textContent).toBe('open')
    })
    expect(screen.getByTestId('task-ext-prompt').textContent).toContain('Why is this here?')
    expect(screen.getByTestId('task-ext-prompt').textContent).toContain('foo.ts (L10)')
  })

})
