import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { Project, Task } from '../types'
import { TaskCard } from '../components/TaskCard'

vi.mock('../hooks/useTerminal', () => ({
  useTerminal: () => ({ sendInput: vi.fn() }),
}))
vi.mock('../hooks/useEventBus', () => ({
  useEventBus: vi.fn(),
}))

vi.mock('../api', () => ({
  api: {
    checkStatus: vi.fn(),
    closeTask: vi.fn(),
    openSession: vi.fn().mockResolvedValue({ session: 'task-1', new: true, prompt: '' }),
    killSession: vi.fn().mockResolvedValue({}),
    waitReady: vi.fn().mockResolvedValue({ ready: true }),
    sendTerminalInput: vi.fn().mockResolvedValue({}),
    refreshPR: vi.fn().mockResolvedValue({}),
  },
}))

import { api } from '../api'

const baseTask: Task = {
  task_id: 'task-1',
  project: 'test-proj',
  description: 'Fix the bug',
  type: 'bug',
  status: 'in_progress',
  group_name: 'core',
  notes: 'Some notes here',
  priority: 3,
  ticket_id: 'EX-123',
  ticket_url: 'https://issues.example.org/jira/browse/EX-123',
  dependencies: [],
  follow_ups: [],
  prs: [],
  created_at: '2026-01-01',
  updated_at: '2026-01-15',
}

function makeProject(taskOverrides: Partial<Task> = {}, extraTasks: Record<string, Task> = {}): Project {
  return {
    id: 'test-proj',
    name: 'Test',
    description: '',
    has_tickets: true,
    progress: 50,
    task_counts: {},
    tasks: {
      'task-1': { ...baseTask, ...taskOverrides },
      ...extraTasks,
    },
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('TaskCard rendering', () => {
  it('renders task id and status dot', () => {
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    expect(screen.getByTestId('task-card')).toBeInTheDocument()
    expect(screen.getByText('task-1')).toBeInTheDocument()
  })

  it('renders ticket link when ticket_id is set', () => {
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    const link = screen.getByTestId('ticket-link')
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute('href', 'https://issues.example.org/jira/browse/EX-123')
  })

  it('shows description', () => {
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    expect(screen.getByText('Fix the bug')).toBeInTheDocument()
  })

  it('shows notes when present', () => {
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    expect(screen.getByText('Some notes here')).toBeInTheDocument()
  })

  it('renders PRs when present', () => {
    const proj = makeProject({
      prs: [
        { number: 100, url: 'https://github.com/example/repo/pull/100', status: 'open', title: 'Fix PR', ci_status: '', review_status: '', comment_count: 0, additions: 0, deletions: 0, author: '', head_branch: '', base_branch: '', last_updated: '' },
      ],
    })
    render(<TaskCard project={proj} taskId="task-1" actions={[]} />)
    expect(screen.getByText('#100')).toBeInTheDocument()
  })

  it('shows dependencies', () => {
    const dep: Task = { ...baseTask, task_id: 'dep-task', status: 'done', dependencies: [] }
    const proj = makeProject({ dependencies: ['dep-task'] }, { 'dep-task': dep })
    render(<TaskCard project={proj} taskId="task-1" actions={[]} />)
    expect(screen.getByText(/dep-task/)).toBeInTheDocument()
  })

  it('shows blocked status when dependency is not done', () => {
    const dep: Task = { ...baseTask, task_id: 'dep-task', status: 'in_progress', dependencies: [] }
    const proj = makeProject({ status: 'not_started', dependencies: ['dep-task'] }, { 'dep-task': dep })
    render(<TaskCard project={proj} taskId="task-1" actions={[]} />)
    expect(screen.getByTestId('task-card').className).toContain('st-blocked')
  })

  it('shows type badge', () => {
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    expect(screen.getByText('bug')).toBeInTheDocument()
  })

  it('shows follow_ups when present', () => {
    const proj = makeProject({ follow_ups: ['Rebase on latest master'] })
    render(<TaskCard project={proj} taskId="task-1" actions={[]} />)
    expect(screen.getByText(/Rebase on latest master/)).toBeInTheDocument()
  })
})

describe('TaskCard mini card (done + expandable)', () => {
  it('renders collapsed mini card for done tasks when expandable', () => {
    const proj = makeProject({ status: 'done' })
    render(<TaskCard project={proj} taskId="task-1" actions={[]} expandable />)
    expect(screen.getByTestId('task-card-mini')).toBeInTheDocument()
  })

  it('renders full card for done tasks when forceFullRender', () => {
    const proj = makeProject({ status: 'done' })
    render(<TaskCard project={proj} taskId="task-1" actions={[]} expandable forceFullRender />)
    expect(screen.getByTestId('task-card')).toBeInTheDocument()
  })

  it('shows PRs on mini card', () => {
    const proj = makeProject({
      status: 'done',
      prs: [{ number: 42, url: '', status: 'merged', title: '', ci_status: '', review_status: '', comment_count: 0, additions: 0, deletions: 0, author: '', head_branch: '', base_branch: '', last_updated: '' }],
    })
    render(<TaskCard project={proj} taskId="task-1" actions={[]} expandable />)
    expect(screen.getByText('#42')).toBeInTheDocument()
  })
})

describe('TaskCard interactions', () => {
  it('sync button calls checkStatus and shows result', async () => {
    vi.mocked(api.checkStatus).mockResolvedValue({
      task_id: 'task-1', project: 'test-proj', description: '', type: 'bug',
      status: 'done', group_name: '', notes: '', priority: 0,
      ticket_id: null, ticket_url: null, dependencies: [], follow_ups: [],
      prs: [], created_at: '', updated_at: '',
      changed: true, old_status: 'in_progress', new_status: 'done',
    })
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByTestId('sync-btn'))
    await waitFor(() => {
      expect(api.checkStatus).toHaveBeenCalledWith('test-proj', 'task-1')
    })
    await waitFor(() => {
      expect(screen.getByTestId('sync-btn').textContent).toContain('in_progress')
    })
  })

  it('sync button shows "up to date" when no change', async () => {
    vi.mocked(api.checkStatus).mockResolvedValue({
      task_id: 'task-1', project: 'test-proj', description: '', type: 'bug',
      status: 'in_progress', group_name: '', notes: '', priority: 0,
      ticket_id: 'EX-123', ticket_url: '', dependencies: [], follow_ups: [],
      prs: [], created_at: '', updated_at: '',
      changed: false, old_status: 'in_progress', new_status: 'in_progress',
    })
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByTestId('sync-btn'))
    await waitFor(() => {
      expect(screen.getByTestId('sync-btn').textContent).toContain('up to date')
    })
  })

  it('sync button shows error on failure', async () => {
    vi.mocked(api.checkStatus).mockRejectedValue(new Error('fail'))
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByTestId('sync-btn'))
    await waitFor(() => {
      expect(screen.getByTestId('sync-btn').textContent).toContain('error')
    })
  })

  it('close button triggers prompt and calls closeTask', async () => {
    vi.mocked(api.closeTask).mockResolvedValue({} as Awaited<ReturnType<typeof api.closeTask>>)
    vi.spyOn(window, 'prompt').mockReturnValue('no longer needed')
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByText('Close'))
    await waitFor(() => {
      expect(api.closeTask).toHaveBeenCalledWith('test-proj', 'task-1', 'no longer needed')
    })
  })

  it('close is cancelled when prompt returns null', () => {
    vi.spyOn(window, 'prompt').mockReturnValue(null)
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByText('Close'))
    expect(api.closeTask).not.toHaveBeenCalled()
  })

  it('close button is hidden for already-closed tasks', () => {
    const proj = makeProject({ status: 'closed' })
    render(<TaskCard project={proj} taskId="task-1" actions={[]} forceFullRender />)
    expect(screen.queryByText('Close')).toBeNull()
  })

  it('action button renders and triggers openSession', async () => {
    const actions = [
      { id: 'do-task', label: 'Do Task', prompt_template: 'Do it', context: 'task', condition: '', sort_order: 0 },
    ]
    const onOpenAction = vi.fn()
    render(<TaskCard project={makeProject()} taskId="task-1" actions={actions} onOpenAction={onOpenAction} />)
    fireEvent.click(screen.getByText('Do Task'))
    await waitFor(() => {
      expect(api.openSession).toHaveBeenCalled()
    })
  })

  it('action button with condition ci_failed hidden when no failed CI', () => {
    const actions = [
      { id: 'fix-ci', label: 'Fix CI', prompt_template: '', context: 'task', condition: 'ci_failed', sort_order: 0 },
    ]
    render(<TaskCard project={makeProject()} taskId="task-1" actions={actions} />)
    expect(screen.queryByText('Fix CI')).toBeNull()
  })

  it('onClickPRNumber is called when PR number is clicked', () => {
    const handler = vi.fn()
    const proj = makeProject({
      prs: [
        { number: 100, url: 'https://github.com/example/repo/pull/100', status: 'open', title: 'Fix PR', ci_status: '', review_status: '', comment_count: 0, additions: 0, deletions: 0, author: '', head_branch: '', base_branch: '', last_updated: '' },
      ],
    })
    render(<TaskCard project={proj} taskId="task-1" actions={[]} onClickPRNumber={handler} />)
    fireEvent.click(screen.getByText('#100'))
    expect(handler).toHaveBeenCalled()
  })

  it('does not render the removed status summary block', () => {
    // The Chinese status-summary line under the task description was
    // removed when OpenEVA went OSS -- the data was redundant with the
    // status pill + PRs list. The render block is gone but we keep an
    // assertion here so any accidental re-introduction fails fast.
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    expect(screen.queryByTestId('status-summary')).toBeNull()
  })

  it('kill session button calls killSession and shows pending state', async () => {
    const projWithSession = makeProject({
      session: { name: 'sess-1', running: true, status: 'idle' },
    })
    vi.mocked(api.killSession).mockReturnValue(new Promise(() => {})) // never resolves
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<TaskCard project={projWithSession} taskId="task-1" actions={[]} />)
    // SessionCard should be shown
    expect(screen.getByTestId('session-component')).toBeInTheDocument()
    // Click Kill button within SessionCard (text = "Kill")
    const killBtn = screen.getByText('Kill')
    fireEvent.click(killBtn)
    await waitFor(() => {
      expect(api.killSession).toHaveBeenCalledWith('sess-1')
    })
    // Pending killing state should show "Stopping session..."
    await waitFor(() => {
      expect(screen.getByText('Stopping session...')).toBeInTheDocument()
    })
  })

  it('kill session failure resets pending state', async () => {
    const projWithSession = makeProject({
      session: { name: 'sess-1', running: true, status: 'idle' },
    })
    vi.mocked(api.killSession).mockRejectedValue(new Error('fail'))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<TaskCard project={projWithSession} taskId="task-1" actions={[]} />)
    const killBtn = screen.getByText('Kill')
    fireEvent.click(killBtn)
    await waitFor(() => {
      expect(api.killSession).toHaveBeenCalled()
    })
    // After failure, session component should still be visible (pendingAction cleared)
    await waitFor(() => {
      expect(screen.getByTestId('session-component')).toBeInTheDocument()
    })
  })

  it('PR click calls onClickPRNumber with PR object', () => {
    const handler = vi.fn()
    const pr = {
      number: 200, url: 'https://github.com/example/repo/pull/200',
      status: 'open', title: 'My PR', ci_status: 'success',
      review_status: '', comment_count: 3, additions: 50,
      deletions: 10, author: 'user', head_branch: 'fix',
      base_branch: 'main', last_updated: '2026-01-01',
    }
    const proj = makeProject({ prs: [pr] })
    render(<TaskCard project={proj} taskId="task-1" actions={[]} onClickPRNumber={handler} />)
    fireEvent.click(screen.getByText('#200'))
    expect(handler).toHaveBeenCalledWith(pr)
  })

  it('shows "Opening session..." when action is pending', async () => {
    // openSession that never resolves to keep pending state
    vi.mocked(api.openSession).mockReturnValue(new Promise(() => {}))
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    // Click Open Agent
    fireEvent.click(screen.getByText('Open Agent'))
    await waitFor(() => {
      expect(screen.getByText('Opening session...')).toBeInTheDocument()
    })
    // Open Agent button should be hidden
    expect(screen.queryByTestId('open-agent')).not.toBeInTheDocument()
  })

  it('pending opening state clears when session appears', async () => {
    vi.mocked(api.openSession).mockResolvedValue({
      session: 'task-1', new: true, prompt: '',
    })
    const { rerender } = render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByText('Open Agent'))
    await waitFor(() => {
      expect(api.openSession).toHaveBeenCalled()
    })
    // Now rerender with session present (simulates server push)
    const projWithSession = makeProject({
      session: { name: 'task-1', running: true, status: 'idle' },
    })
    rerender(<TaskCard project={projWithSession} taskId="task-1" actions={[]} />)
    await waitFor(() => {
      expect(screen.getByTestId('session-component')).toBeInTheDocument()
    })
    // "Opening session..." should no longer be shown
    expect(screen.queryByText('Opening session...')).not.toBeInTheDocument()
  })

  it('session auto-expand triggers when session appears', async () => {
    // Start without session
    const { rerender } = render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    // Rerender with session (simulates session appearing)
    const projWithSession = makeProject({
      session: { name: 'task-1', running: true, status: 'idle' },
    })
    rerender(<TaskCard project={projWithSession} taskId="task-1" actions={[]} />)
    // SessionCard should appear with autoExpand
    await waitFor(() => {
      expect(screen.getByTestId('session-component')).toBeInTheDocument()
    })
  })

  it('handleAction with prompt: delivers prompt via terminal after waitReady', async () => {
    vi.mocked(api.openSession).mockResolvedValue({
      session: 'task-1', new: true, prompt: 'Do something important',
    })
    vi.mocked(api.waitReady).mockResolvedValue({ ready: true })
    vi.mocked(api.sendTerminalInput).mockResolvedValue({} as unknown as Awaited<ReturnType<typeof api.sendTerminalInput>>)

    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByText('Open Agent'))

    await waitFor(() => {
      expect(api.openSession).toHaveBeenCalled()
    })

    // Wait for the setTimeout + waitReady + sendTerminalInput chain
    await waitFor(() => {
      expect(api.waitReady).toHaveBeenCalledWith('task-1', 60)
    }, { timeout: 3000 })

    await waitFor(() => {
      expect(api.sendTerminalInput).toHaveBeenCalledWith('task-1', 'Do something important')
    })

    // Enter key follows the prompt with a small delay
    await waitFor(() => {
      expect(api.sendTerminalInput).toHaveBeenCalledWith('task-1', '\r')
    })
  })

  it('handleAction with prompt: fallback when waitReady fails', async () => {
    vi.mocked(api.openSession).mockResolvedValue({
      session: 'task-1', new: true, prompt: 'Fallback prompt',
    })
    vi.mocked(api.waitReady).mockRejectedValue(new Error('timeout'))
    vi.mocked(api.sendTerminalInput).mockResolvedValue({} as Awaited<ReturnType<typeof api.sendTerminalInput>>)

    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByText('Open Agent'))

    await waitFor(() => {
      expect(api.waitReady).toHaveBeenCalled()
    }, { timeout: 3000 })

    // Fallback path: after 3s timeout, should still try to send input
    await waitFor(() => {
      expect(api.sendTerminalInput).toHaveBeenCalledWith('task-1', 'Fallback prompt')
    }, { timeout: 10000 })
  })

  it('handleKillSession calls killSession with session name', async () => {
    const projWithSession = makeProject({
      session: { name: 'custom-sess', running: true, status: 'idle' },
    })
    vi.mocked(api.killSession).mockResolvedValue({} as unknown as Awaited<ReturnType<typeof api.killSession>>)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<TaskCard project={projWithSession} taskId="task-1" actions={[]} />)
    const killBtn = screen.getByText('Kill')
    fireEvent.click(killBtn)
    await waitFor(() => {
      expect(api.killSession).toHaveBeenCalledWith('custom-sess')
    })
  })

  it('external action trigger fires handleAction', async () => {
    vi.mocked(api.openSession).mockResolvedValue({
      session: 'task-1', new: true, prompt: '',
    })
    const onOpenAction = vi.fn()
    const externalAction = { actionId: 'review', prNumber: 42, prRepo: 'example/repo', ts: Date.now() }
    render(
      <TaskCard
        project={makeProject()}
        taskId="task-1"
        actions={[]}
        onOpenAction={onOpenAction}
        externalAction={externalAction}
      />,
    )
    await waitFor(() => {
      expect(api.openSession).toHaveBeenCalledWith(expect.objectContaining({
        task_id: 'task-1',
        action_id: 'review',
        pr_number: 42,
        pr_repo: 'example/repo',
      }))
    })
  })

  it('externalAction with non-matching taskId is ignored', async () => {
    // Regression: parent's externalAction state survives remounts when the
    // user switches between PRs/tasks. Without the taskId scope, the new
    // TaskCard would fire the stale Ask-Agent prompt on the wrong session.
    vi.mocked(api.openSession).mockResolvedValue({
      session: 'task-2', new: true, prompt: '',
    })
    const externalAction = {
      actionId: 'open',
      taskId: 'task-1',                        // event is for task-1
      customPrompt: 'stale question from task-1',
      ts: Date.now(),
    }
    render(
      <TaskCard
        project={makeProject()}
        taskId="task-2"                         // we're rendering task-2
        actions={[]}
        externalAction={externalAction}
      />,
    )
    // Give any stray effect a chance to fire, then assert it did NOT.
    await new Promise(r => setTimeout(r, 100))
    expect(api.openSession).not.toHaveBeenCalled()
  })

  it('externalAction with matching taskId fires as expected', async () => {
    vi.mocked(api.openSession).mockResolvedValue({
      session: 'task-1', new: true, prompt: '',
    })
    const externalAction = {
      actionId: 'open',
      taskId: 'task-1',
      customPrompt: 'fresh question',
      ts: Date.now(),
    }
    render(
      <TaskCard
        project={makeProject()}
        taskId="task-1"
        actions={[]}
        externalAction={externalAction}
      />,
    )
    await waitFor(() => {
      expect(api.openSession).toHaveBeenCalledWith(expect.objectContaining({
        task_id: 'task-1',
        custom_prompt: 'fresh question',
      }))
    })
  })

  it('refreshes related PRs when card becomes active (sessionExpanded)', async () => {
    // When the user selects a card in SessionsPage we want fresh CI / review
    // state for its PRs without making the user click each PR's refresh
    // button. Fire-and-forget; debounced 300ms to dodge scroll/click bursts.
    const project = makeProject({
      prs: [
        { number: 100, status: 'open', url: 'https://github.com/o/r/pull/100' },
        { number: 200, status: 'open', url: 'https://github.com/o/r/pull/200' },
      ] as Task['prs'],
    })
    render(
      <TaskCard project={project} taskId="task-1" actions={[]} sessionExpanded={true} />,
    )
    await waitFor(() => {
      expect(api.refreshPR).toHaveBeenCalledWith(100)
      expect(api.refreshPR).toHaveBeenCalledWith(200)
    }, { timeout: 1000 })
    expect(vi.mocked(api.refreshPR).mock.calls.length).toBe(2)
  })

  it('does NOT refresh PRs when card is rendered but not active', async () => {
    const project = makeProject({
      prs: [{ number: 100, status: 'open', url: 'x' }] as Task['prs'],
    })
    render(
      <TaskCard project={project} taskId="task-1" actions={[]} sessionExpanded={false} />,
    )
    // Wait past the debounce; refreshPR must still be 0.
    await new Promise(r => setTimeout(r, 500))
    expect(api.refreshPR).not.toHaveBeenCalled()
  })

  it('refreshes PRs in graph/side panel mode (forceFullRender)', async () => {
    const project = makeProject({
      prs: [{ number: 555, status: 'open', url: 'x' }] as Task['prs'],
    })
    render(
      <TaskCard project={project} taskId="task-1" actions={[]} forceFullRender />,
    )
    await waitFor(() => expect(api.refreshPR).toHaveBeenCalledWith(555), { timeout: 1000 })
  })

  it('does not call refreshPR for tasks with no PRs', async () => {
    const project = makeProject({ prs: [] })
    render(
      <TaskCard project={project} taskId="task-1" actions={[]} sessionExpanded={true} />,
    )
    await new Promise(r => setTimeout(r, 500))
    expect(api.refreshPR).not.toHaveBeenCalled()
  })

  it('close task with ticket triggers handleAction for JIRA close', async () => {
    vi.mocked(api.closeTask).mockResolvedValue({} as Awaited<ReturnType<typeof api.closeTask>>)
    vi.mocked(api.openSession).mockResolvedValue({
      session: 'task-1', new: true, prompt: '',
    })
    vi.spyOn(window, 'prompt').mockReturnValue('obsolete')
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByText('Close'))
    await waitFor(() => {
      expect(api.closeTask).toHaveBeenCalledWith('test-proj', 'task-1', 'obsolete')
    })
    // Since task has ticket, openSession should be called with close prompt
    await waitFor(() => {
      expect(api.openSession).toHaveBeenCalledWith(expect.objectContaining({
        action_id: 'open',
        custom_prompt: expect.stringContaining('Close the JIRA ticket'),
      }))
    })
  })

  it('close task failure shows alert', async () => {
    vi.mocked(api.closeTask).mockRejectedValue(new Error('DB error'))
    vi.spyOn(window, 'prompt').mockReturnValue('')
    vi.spyOn(window, 'alert').mockImplementation(() => {})
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByText('Close'))
    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith(expect.stringContaining('Failed to close task'))
      expect(window.alert).toHaveBeenCalledWith(expect.stringContaining('DB error'))
    })
  })

  it('mini card renders for closed tasks in expandable mode', () => {
    const proj = makeProject({ status: 'closed' })
    render(<TaskCard project={proj} taskId="task-1" actions={[]} expandable />)
    expect(screen.getByTestId('task-card-mini')).toBeInTheDocument()
  })

  it('pending killing state clears when session disappears on rerender', async () => {
    const projWithSession = makeProject({
      session: { name: 'sess-1', running: true, status: 'idle' },
    })
    vi.mocked(api.killSession).mockReturnValue(new Promise(() => {})) // never resolves
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { rerender } = render(<TaskCard project={projWithSession} taskId="task-1" actions={[]} />)
    const killBtn = screen.getByText('Kill')
    fireEvent.click(killBtn)
    await waitFor(() => {
      expect(screen.getByText('Stopping session...')).toBeInTheDocument()
    })

    // Rerender without session (simulates server push after kill)
    rerender(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    await waitFor(() => {
      // pendingAction should clear, Open Agent should be visible
      expect(screen.getByTestId('open-agent')).toBeInTheDocument()
    })
  })

  it('open agent button calls openSession with correct params', async () => {
    vi.mocked(api.openSession).mockResolvedValue({
      session: 'task-1', new: true, prompt: '',
    })
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByText('Open Agent'))
    await waitFor(() => {
      expect(api.openSession).toHaveBeenCalledWith({
        kind: 'task',
        task_id: 'task-1',
        project_id: 'test-proj',
        action_id: 'open',
        pr_number: undefined,
        pr_repo: undefined,
        custom_prompt: undefined,
      })
    })
  })

  it('sync button opens session with sync prompt when no ticket and no change', async () => {
    // Task has no ticket (ticket_id: null) and checkStatus returns no change
    vi.mocked(api.checkStatus).mockResolvedValue({
      task_id: 'task-1', project: 'test-proj', description: '', type: 'bug',
      status: 'in_progress', group_name: '', notes: '', priority: 0,
      ticket_id: null, ticket_url: null, dependencies: [], follow_ups: [],
      prs: [], created_at: '', updated_at: '',
      changed: false, old_status: 'in_progress', new_status: 'in_progress',
    })
    vi.mocked(api.openSession).mockResolvedValue({
      session: 'task-1', new: true, prompt: '',
    })
    const proj = makeProject({ ticket_id: null, ticket_url: null })
    render(<TaskCard project={proj} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByTestId('sync-btn'))
    // Should call openSession with sync prompt (no ticket + no change -> opens session)
    await waitFor(() => {
      expect(api.openSession).toHaveBeenCalledWith(expect.objectContaining({
        task_id: 'task-1',
        action_id: 'open',
        custom_prompt: 'Sync the status of this task.',
      }))
    })
    // After session opens, it shows "sync sent"
    await waitFor(() => {
      expect(screen.getByTestId('sync-btn').textContent).toContain('sync sent')
    })
  })

  it('sync button shows "sync failed" when session open fails (no ticket, no change)', async () => {
    vi.mocked(api.checkStatus).mockResolvedValue({
      task_id: 'task-1', project: 'test-proj', description: '', type: 'bug',
      status: 'in_progress', group_name: '', notes: '', priority: 0,
      ticket_id: null, ticket_url: null, dependencies: [], follow_ups: [],
      prs: [], created_at: '', updated_at: '',
      changed: false, old_status: 'in_progress', new_status: 'in_progress',
    })
    vi.mocked(api.openSession).mockRejectedValue(new Error('session failed'))
    const proj = makeProject({ ticket_id: null, ticket_url: null })
    render(<TaskCard project={proj} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByTestId('sync-btn'))
    await waitFor(() => {
      expect(screen.getByTestId('sync-btn').textContent).toContain('sync failed')
    })
  })

  it('openSession failure shows alert and clears pending', async () => {
    vi.mocked(api.openSession).mockRejectedValue(new Error('Server error'))
    vi.spyOn(window, 'alert').mockImplementation(() => {})
    render(<TaskCard project={makeProject()} taskId="task-1" actions={[]} />)
    fireEvent.click(screen.getByText('Open Agent'))
    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith(expect.stringContaining('Server error'))
    })
    // Open Agent should be visible again (pending cleared)
    await waitFor(() => {
      expect(screen.getByTestId('open-agent')).toBeInTheDocument()
    })
  })
})
