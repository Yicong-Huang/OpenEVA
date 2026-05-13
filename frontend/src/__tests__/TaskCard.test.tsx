import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import type { Project } from '../types'
import { TaskCard } from '../components/TaskCard'

vi.mock('../hooks/useTerminal', () => ({
  useTerminal: () => ({ sendInput: vi.fn() }),
}))
vi.mock('../hooks/useEventBus', () => ({
  useEventBus: vi.fn(),
}))

const mockProject: Project = {
  id: 'test-proj',
  name: 'Test',
  description: '',
  has_tickets: true,
  progress: 50,
  task_counts: {},
  tasks: {
    'task-1': {
      task_id: 'task-1',
      project: 'test-proj',
      description: 'Fix the bug',
      type: 'bug',
      status: 'in_progress',
      group_name: 'core',
      notes: '',
      priority: 3,
      ticket_id: 'EX-123',
      ticket_url: 'https://issues.example.org/jira/browse/EX-123',
      dependencies: [],
      follow_ups: [],
      prs: [
        {
          number: 456,
          url: 'https://github.com/example/repo/pull/456',
          status: 'open',
          title: 'Fix PR',
          ci_status: 'success',
          review_status: 'approved',
          comment_count: 2,
          additions: 10,
          deletions: 5,
          author: 'user',
          head_branch: 'fix',
          base_branch: 'main',
          last_updated: '',
        },
      ],
      created_at: '',
      updated_at: '',
    },
  },
}

describe('TaskCard', () => {
  it('renders task ID', () => {
    render(<TaskCard project={mockProject} taskId="task-1" actions={[]} />)
    expect(screen.getByText('task-1')).toBeInTheDocument()
  })

  it('renders ticket link when project has_tickets and task has ticket_id', () => {
    render(<TaskCard project={mockProject} taskId="task-1" actions={[]} />)
    const link = screen.getByTestId('ticket-link')
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute('href', 'https://issues.example.org/jira/browse/EX-123')
    expect(link.textContent).toBe('[EX-123]')
  })

  it('does NOT show ticket button when project has_tickets=false', () => {
    const projNoTickets: Project = {
      ...mockProject,
      has_tickets: false,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          ticket_id: null,
          ticket_url: null,
        },
      },
    }
    render(<TaskCard project={projNoTickets} taskId="task-1" actions={[]} />)
    expect(screen.queryByTestId('create-ticket-btn')).not.toBeInTheDocument()
    expect(screen.queryByTestId('ticket-link')).not.toBeInTheDocument()
  })

  it('renders PR list using PROverview', () => {
    render(<TaskCard project={mockProject} taskId="task-1" actions={[]} />)
    const prOverviews = screen.getAllByTestId('pr-overview')
    expect(prOverviews).toHaveLength(1)
    expect(screen.getByText('Fix PR')).toBeInTheDocument()
  })

  it('shows Open Agent button when no session', () => {
    render(<TaskCard project={mockProject} taskId="task-1" actions={[]} />)
    expect(screen.getByTestId('open-agent')).toBeInTheDocument()
    expect(screen.getByText('Open Agent')).toBeInTheDocument()
  })

  it('shows SessionComponent (not Open Agent) when session exists', () => {
    const projWithSession: Project = {
      ...mockProject,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          session: { name: 'sess-1', running: true, status: 'idle' },
        },
      },
    }
    render(<TaskCard project={projWithSession} taskId="task-1" actions={[]} />)
    expect(screen.getByTestId('session-component')).toBeInTheDocument()
    expect(screen.queryByTestId('open-agent')).not.toBeInTheDocument()
  })

  it('shows Close button for non-closed tasks', () => {
    render(<TaskCard project={mockProject} taskId="task-1" actions={[]} />)
    expect(screen.getByText('Close')).toBeInTheDocument()
  })

  it('hides Close button for closed tasks', () => {
    const projClosed: Project = {
      ...mockProject,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          status: 'closed',
        },
      },
    }
    render(<TaskCard project={projClosed} taskId="task-1" actions={[]} forceFullRender />)
    expect(screen.queryByText('Close')).not.toBeInTheDocument()
  })

  it('renders mini card for done tasks in expandable mode', () => {
    const projDone: Project = {
      ...mockProject,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          status: 'done',
          updated_at: '2026-04-13T00:00:00',
        },
      },
    }
    render(<TaskCard project={projDone} taskId="task-1" actions={[]} expandable />)
    expect(screen.getByTestId('task-card-mini')).toBeInTheDocument()
    // Mini card shows PR numbers
    expect(screen.getByText('#456')).toBeInTheDocument()
  })

  it('transitions from mini to full without hook-order error when clicked', () => {
    // Regression: rules-of-hooks violation used to make TaskCard return early
    // BEFORE declaring useCallback / useRef / useEffect hooks, so expanding a
    // mini card at runtime crashed React.
    const projDone: Project = {
      ...mockProject,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          status: 'done',
          updated_at: '2026-04-13T00:00:00',
        },
      },
    }
    render(<TaskCard project={projDone} taskId="task-1" actions={[]} expandable />)
    const mini = screen.getByTestId('task-card-mini')
    expect(mini).toBeInTheDocument()
    // Clicking the mini card flips `expanded` and should transition into the
    // full task-card layout without any React warnings.
    fireEvent.click(mini)
    expect(screen.queryByTestId('task-card-mini')).not.toBeInTheDocument()
    expect(screen.getByTestId('task-card')).toBeInTheDocument()
  })

  it('renders full card for done tasks when forceFullRender is true', () => {
    const projDone: Project = {
      ...mockProject,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          status: 'done',
        },
      },
    }
    render(<TaskCard project={projDone} taskId="task-1" actions={[]} expandable forceFullRender />)
    expect(screen.getByTestId('task-card')).toBeInTheDocument()
    expect(screen.queryByTestId('task-card-mini')).not.toBeInTheDocument()
  })

  it('shows blocked status when dependency is not done', () => {
    const projBlocked: Project = {
      ...mockProject,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          status: 'not_started',
          dependencies: ['task-2'],
        },
        'task-2': {
          ...mockProject.tasks['task-1'],
          task_id: 'task-2',
          status: 'in_progress',
        },
      },
    }
    render(<TaskCard project={projBlocked} taskId="task-1" actions={[]} />)
    // The card should have the blocked class
    const card = screen.getByTestId('task-card')
    expect(card.className).toContain('st-blocked')
  })

  it('shows description text', () => {
    render(<TaskCard project={mockProject} taskId="task-1" actions={[]} />)
    expect(screen.getByText('Fix the bug')).toBeInTheDocument()
  })

  it('shows sync button', () => {
    render(<TaskCard project={mockProject} taskId="task-1" actions={[]} />)
    expect(screen.getByTestId('sync-btn')).toBeInTheDocument()
  })

  it('renders action buttons for matching actions', () => {
    const actions = [
      { id: 'do-task', label: 'Do Task', prompt_template: 'Do it', context: 'task', condition: '', sort_order: 0 },
    ]
    render(<TaskCard project={mockProject} taskId="task-1" actions={actions} />)
    expect(screen.getByText('Do Task')).toBeInTheDocument()
  })

  it('shows notes when present', () => {
    const projWithNotes: Project = {
      ...mockProject,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          notes: 'Important note here',
        },
      },
    }
    render(<TaskCard project={projWithNotes} taskId="task-1" actions={[]} />)
    expect(screen.getByText(/Important note here/)).toBeInTheDocument()
  })

  it('does not render History section when history is empty', () => {
    const proj: Project = {
      ...mockProject,
      tasks: {
        'task-1': { ...mockProject.tasks['task-1'], history: [] },
      },
    }
    render(<TaskCard project={proj} taskId="task-1" actions={[]} />)
    expect(screen.queryByText(/^History$/)).not.toBeInTheDocument()
  })

  it('renders History section with up to 5 entries', () => {
    const proj: Project = {
      ...mockProject,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          history: [
            { ts: '2026-04-22T10:01:00Z', text: 'entry 1' },
            { ts: '2026-04-22T10:02:00Z', text: 'entry 2' },
            { ts: '2026-04-22T10:03:00Z', text: 'entry 3' },
            { ts: '2026-04-22T10:04:00Z', text: 'entry 4' },
            { ts: '2026-04-22T10:05:00Z', text: 'entry 5' },
          ],
        },
      },
    }
    render(<TaskCard project={proj} taskId="task-1" actions={[]} />)
    expect(screen.getByText('History')).toBeInTheDocument()
    for (let i = 1; i <= 5; i++) {
      expect(screen.getByText(`entry ${i}`)).toBeInTheDocument()
    }
    // No overflow badge when exactly 5 items.
    expect(screen.queryByText(/\+ \d+ older/)).not.toBeInTheDocument()
  })

  it('shows "+ N older" badge when history has more than 5 entries', () => {
    const entries = Array.from({ length: 8 }, (_, i) => ({
      ts: `2026-04-22T10:0${i}:00Z`,
      text: `entry ${i}`,
    }))
    const proj: Project = {
      ...mockProject,
      tasks: {
        'task-1': { ...mockProject.tasks['task-1'], history: entries },
      },
    }
    render(<TaskCard project={proj} taskId="task-1" actions={[]} />)
    expect(screen.getByText('+ 3 older')).toBeInTheDocument()
  })

  it('history timestamp is rendered in MM-DD HH:MM form, localised', () => {
    const proj: Project = {
      ...mockProject,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          history: [{ ts: '2026-04-22T14:37:00Z', text: 'checkpoint' }],
        },
      },
    }
    render(<TaskCard project={proj} taskId="task-1" actions={[]} />)
    // Rendered label is the UTC instant converted to the browser's local
    // time in `MM-DD HH:MM` shape. We avoid hardcoding the exact string
    // so the test is portable across machine timezones; instead we assert
    // both the shape and that the raw UTC value is preserved in `title`.
    const label = screen.getByText(/^\d{2}-\d{2} \d{2}:\d{2}$/)
    expect(label).toBeInTheDocument()
    expect(label).toHaveAttribute('title', '2026-04-22T14:37:00Z')
  })

  it('tags auto-history entries with data-history-kind so CSS / test harness can target them', () => {
    const proj: Project = {
      ...mockProject,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          history: [
            { ts: '2026-04-22T10:00:00Z', text: 'status: not_started -> in_review' },
            { ts: '2026-04-22T10:01:00Z', text: 'linked PR #42' },
            { ts: '2026-04-22T10:02:00Z', text: 'PR #42 merged' },
            { ts: '2026-04-22T10:03:00Z', text: 'rebased on master' },
          ],
        },
      },
    }
    const { container } = render(<TaskCard project={proj} taskId="task-1" actions={[]} />)
    // Assert one row per entry carries the correct data attribute.
    const rows = container.querySelectorAll('[data-history-kind]')
    expect(rows).toHaveLength(4)
    expect(rows[0].getAttribute('data-history-kind')).toBe('status')
    expect(rows[1].getAttribute('data-history-kind')).toBe('pr_linked')
    expect(rows[2].getAttribute('data-history-kind')).toBe('pr_merged')
    expect(rows[3].getAttribute('data-history-kind')).toBe('manual')
  })

  it('renders a coloured dot marker only for auto-history entries, not manual ones', () => {
    const proj: Project = {
      ...mockProject,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          history: [
            { ts: '2026-04-22T10:00:00Z', text: 'status: X -> in_review' },
            { ts: '2026-04-22T10:01:00Z', text: 'manual note here' },
          ],
        },
      },
    }
    const { container } = render(<TaskCard project={proj} taskId="task-1" actions={[]} />)
    const statusRow = container.querySelector('[data-history-kind="status"]')
    const manualRow = container.querySelector('[data-history-kind="manual"]')
    expect(statusRow).not.toBeNull()
    expect(manualRow).not.toBeNull()
    // Auto row has an aria-hidden dot span; manual row does not.
    expect(statusRow!.querySelector('[aria-hidden="true"]')).not.toBeNull()
    expect(manualRow!.querySelector('[aria-hidden="true"]')).toBeNull()
  })

  it('shows dependencies section when deps exist', () => {
    const projWithDeps: Project = {
      ...mockProject,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          dependencies: ['task-2'],
        },
        'task-2': {
          ...mockProject.tasks['task-1'],
          task_id: 'task-2',
          status: 'done',
        },
      },
    }
    render(<TaskCard project={projWithDeps} taskId="task-1" actions={[]} />)
    expect(screen.getByText(/task-2/)).toBeInTheDocument()
  })

  it('shows follow-ups when present', () => {
    const projWithFollowUps: Project = {
      ...mockProject,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          follow_ups: ['task-3'],
        },
        'task-3': {
          ...mockProject.tasks['task-1'],
          task_id: 'task-3',
          status: 'not_started',
        },
      },
    }
    render(<TaskCard project={projWithFollowUps} taskId="task-1" actions={[]} />)
    expect(screen.getByText(/task-3/)).toBeInTheDocument()
  })

  it('shows + ticket button when no ticket exists but has_tickets is true', () => {
    const projNoTicket: Project = {
      ...mockProject,
      has_tickets: true,
      tasks: {
        'task-1': {
          ...mockProject.tasks['task-1'],
          ticket_id: null,
          ticket_url: null,
        },
      },
    }
    render(<TaskCard project={projNoTicket} taskId="task-1" actions={[]} />)
    expect(screen.getByTestId('create-ticket-btn')).toBeInTheDocument()
  })

  it('renders group name badge', () => {
    render(<TaskCard project={mockProject} taskId="task-1" actions={[]} />)
    // type badge should show
    expect(screen.getByText('bug')).toBeInTheDocument()
  })
})
