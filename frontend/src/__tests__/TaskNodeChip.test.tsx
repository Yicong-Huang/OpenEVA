import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { TaskNodeChip } from '../components/TaskNodeChip'
import type { Task } from '../types'

const baseTask: Task = {
  task_id: 't-1',
  project: 'p',
  description: 'demo',
  type: 'feature',
  status: 'in_progress',
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
}


describe('TaskNodeChip session status indicator', () => {
  it('renders no session-status dot when sessionStatus is omitted', () => {
    const { container } = render(
      <TaskNodeChip taskId="t-1" task={baseTask} hasSession={true} />,
    )
    expect(container.querySelector('[data-testid="session-status-dot-t-1"]')).toBeNull()
  })

  it('renders no session-status dot when sessionStatus is empty string', () => {
    const { container } = render(
      <TaskNodeChip taskId="t-1" task={baseTask}
                    hasSession={true} sessionStatus="" />,
    )
    expect(container.querySelector('[data-testid="session-status-dot-t-1"]')).toBeNull()
  })

  it('renders a dot for every static (non-blink) status', () => {
    /* The 4 non-attention statuses use a static dot. needs_input and
     * needs_permission have their own animated tests. */
    for (const st of ['thinking', 'starting', 'idle', 'stopped']) {
      const { container } = render(
        <TaskNodeChip taskId="t-x" task={baseTask}
                      hasSession={true} sessionStatus={st} />,
      )
      const dot = container.querySelector('[data-testid="session-status-dot-t-x"]')
      expect(dot).not.toBeNull()
      expect(dot?.getAttribute('data-status')).toBe(st)
      // Static statuses carry neither animation class.
      expect(dot?.classList.contains('session-dot-blink')).toBe(false)
      expect(dot?.classList.contains('session-dot-soft')).toBe(false)
    }
  })

  it('blinks (session-dot-blink class) for needs_permission', () => {
    const { container } = render(
      <TaskNodeChip taskId="t-perm" task={baseTask}
                    hasSession={true} sessionStatus="needs_permission" />,
    )
    const dot = container.querySelector('[data-testid="session-status-dot-t-perm"]') as HTMLElement
    expect(dot).not.toBeNull()
    expect(dot.classList.contains('session-dot-blink')).toBe(true)
    // Glow makes it stand out further from neutral dots.
    expect(dot.style.boxShadow).not.toBe('')
    expect(dot.style.boxShadow).not.toBe('none')
  })

  it('does NOT blink for needs_input (yellow "ready for next" tier)', () => {
    /* In the 3-tier urgency model needs_input collapses with idle --
     * both mean "the agent finished, awaiting the user's next instruction".
     * That's a soft cue (yellow dot, no animation) -- the urgent red
     * tier is reserved for needs_permission and crashed where the user
     * has to act NOW. */
    const { container } = render(
      <TaskNodeChip taskId="t-input" task={baseTask}
                    hasSession={true} sessionStatus="needs_input" />,
    )
    const dot = container.querySelector('[data-testid="session-status-dot-t-input"]') as HTMLElement
    expect(dot).not.toBeNull()
    expect(dot.classList.contains('session-dot-blink')).toBe(false)
  })

  it('idle stays static (no animation class)', () => {
    /* `idle` fires on Stop -- response turn just finished. Yellow
     * "ready for next task" tier: prompt cursor live, waiting for the
     * user. Static dot (no halo / animation). */
    const { container } = render(
      <TaskNodeChip taskId="t-idle" task={baseTask}
                    hasSession={true} sessionStatus="idle" />,
    )
    const dot = container.querySelector('[data-testid="session-status-dot-t-idle"]') as HTMLElement
    expect(dot).not.toBeNull()
    expect(dot.classList.contains('session-dot-soft')).toBe(false)
    expect(dot.classList.contains('session-dot-blink')).toBe(false)
  })

  it('renders no dot when sessionStatus is an unknown value', () => {
    /* Defensive: an unrecognised status (typo / new state we haven't
     * styled yet) should fall back to "no dot" rather than render an
     * invisible element with the wrong colour. */
    const { container } = render(
      <TaskNodeChip taskId="t-1" task={baseTask}
                    hasSession={true} sessionStatus="frobnicating" />,
    )
    expect(container.querySelector('[data-testid="session-status-dot-t-1"]')).toBeNull()
  })

  it('does not render any session indicators when hasSession is false', () => {
    const { container } = render(
      <TaskNodeChip taskId="t-1" task={baseTask}
                    hasSession={false} sessionStatus="thinking" />,
    )
    // No dot, no claude-favicon img.
    expect(container.querySelector('[data-testid="session-status-dot-t-1"]')).toBeNull()
    expect(container.querySelector('img[alt="session"]')).toBeNull()
  })

  it('still renders the claude-favicon icon alongside the live status dot', () => {
    /* The icon is the "this task has a session" tell; the dot adds
     * status. Both should coexist when hasSession=true. */
    const { container } = render(
      <TaskNodeChip taskId="t-1" task={baseTask}
                    hasSession={true} sessionStatus="thinking" />,
    )
    expect(container.querySelector('[data-testid="session-status-dot-t-1"]')).not.toBeNull()
    expect(container.querySelector('img[alt="session"]')).not.toBeNull()
  })
})


describe('TaskNodeChip blocked status (computed via tasksMap)', () => {
  it('renders blocked status when a dep is not in an unblocking state', () => {
    // Task t-2 (not_started) depends on t-1 which is in_progress.
    // The chip should render `blocked`, not `not_started`, because
    // the dep hasn't reached an unblocking status (done / closed /
    // needs_follow_up).
    const tasksMap: Record<string, Task> = {
      't-1': { ...baseTask, task_id: 't-1', status: 'in_progress' },
      't-2': { ...baseTask, task_id: 't-2', status: 'not_started',
               dependencies: ['t-1'] },
    }
    const { container } = render(
      <TaskNodeChip taskId="t-2" task={tasksMap['t-2']}
                    tasksMap={tasksMap} />,
    )
    const node = container.querySelector('[data-testid="task-node-chip-t-2"]')
    expect(node?.getAttribute('data-status')).toBe('blocked')
  })

  it('renders the stored status when deps are all unblocking', () => {
    const tasksMap: Record<string, Task> = {
      't-1': { ...baseTask, task_id: 't-1', status: 'done' },
      't-2': { ...baseTask, task_id: 't-2', status: 'not_started',
               dependencies: ['t-1'] },
    }
    const { container } = render(
      <TaskNodeChip taskId="t-2" task={tasksMap['t-2']}
                    tasksMap={tasksMap} />,
    )
    const node = container.querySelector('[data-testid="task-node-chip-t-2"]')
    expect(node?.getAttribute('data-status')).toBe('not_started')
  })

  it('does NOT compute blocked when stored status is already terminal', () => {
    // A done/closed task with regressed deps still reads as done --
    // mirrors backend `effective_status`.
    const tasksMap: Record<string, Task> = {
      't-1': { ...baseTask, task_id: 't-1', status: 'in_progress' },
      't-2': { ...baseTask, task_id: 't-2', status: 'done',
               dependencies: ['t-1'] },
    }
    const { container } = render(
      <TaskNodeChip taskId="t-2" task={tasksMap['t-2']}
                    tasksMap={tasksMap} />,
    )
    const node = container.querySelector('[data-testid="task-node-chip-t-2"]')
    expect(node?.getAttribute('data-status')).toBe('done')
  })

  it('falls back to stored status when no tasksMap is supplied', () => {
    // Back-compat: callers that haven't migrated to passing tasksMap
    // (e.g. tests, third-party usages) shouldn't crash; they get the
    // pre-fix behaviour where `not_started` shows in red.
    const { container } = render(
      <TaskNodeChip taskId="t-1"
                    task={{ ...baseTask, status: 'not_started',
                            dependencies: ['missing'] }} />,
    )
    const node = container.querySelector('[data-testid="task-node-chip-t-1"]')
    expect(node?.getAttribute('data-status')).toBe('not_started')
  })

  it('renders status text in the footer (matches GraphView TaskNode style)', () => {
    const { container } = render(
      <TaskNodeChip taskId="t-1"
                    task={{ ...baseTask, status: 'in_review' }} />,
    )
    // Status label in footer: replaces underscore with space, then
    // upper-cased via CSS text-transform.
    expect(container.textContent).toContain('in review')
  })
})


describe('TaskNodeChip PR pill CI status colour', () => {
  const _pr = (overrides = {}) => ({
    number: 1, url: 'https://x/y/pull/1', status: 'open', title: '',
    ci_status: '', review_status: '', comment_count: 0,
    additions: 0, deletions: 0, author: '',
    head_branch: '', base_branch: '', last_updated: '2026-04-01T00:00:00Z',
    ...overrides,
  })

  it('exposes the latest PR ci_status via data-ci-status', () => {
    // Two PRs: pr-2 is newer. Its ci_status drives the pill colour.
    const task: Task = {
      ...baseTask,
      prs: [
        _pr({ number: 1, ci_status: 'success', last_updated: '2026-01-01T00:00:00Z' }),
        _pr({ number: 2, ci_status: 'failure', last_updated: '2026-04-01T00:00:00Z' }),
      ],
    }
    const { container } = render(
      <TaskNodeChip taskId="t-1" task={task} />,
    )
    const pill = container.querySelector('[data-testid="task-pr-pill-t-1"]')
    expect(pill).not.toBeNull()
    expect(pill?.getAttribute('data-ci-status')).toBe('failure')
    // Background pulls from the failure palette (red color-mix).
    const bg = (pill as HTMLElement).style.background
    expect(bg).toContain('var(--red)')
  })

  it('paints success-coloured pill for success', () => {
    const task: Task = {
      ...baseTask,
      prs: [_pr({ ci_status: 'success' })],
    }
    const { container } = render(
      <TaskNodeChip taskId="t-1" task={task} />,
    )
    const pill = container.querySelector('[data-testid="task-pr-pill-t-1"]')
    const bg = (pill as HTMLElement).style.background
    expect(bg).toContain('var(--green)')
  })

  it('paints in-flight (yellow) for any non-success-non-failure ci', () => {
    const task: Task = {
      ...baseTask,
      prs: [_pr({ ci_status: 'pending' })],
    }
    const { container } = render(
      <TaskNodeChip taskId="t-1" task={task} />,
    )
    const pill = container.querySelector('[data-testid="task-pr-pill-t-1"]')
    const bg = (pill as HTMLElement).style.background
    expect(bg).toContain('var(--yellow)')
  })

  it('uses neutral palette when ci_status is empty', () => {
    const task: Task = {
      ...baseTask,
      prs: [_pr({ ci_status: '' })],
    }
    const { container } = render(
      <TaskNodeChip taskId="t-1" task={task} />,
    )
    const pill = container.querySelector('[data-testid="task-pr-pill-t-1"]')
    const bg = (pill as HTMLElement).style.background
    // Falls back to the neutral badge background.
    expect(bg).toContain('--node-badge-bg')
  })

  it('does not render the pill when there are no PRs', () => {
    const { container } = render(
      <TaskNodeChip taskId="t-1" task={{ ...baseTask, prs: [] }} />,
    )
    expect(container.querySelector('[data-testid="task-pr-pill-t-1"]'))
      .toBeNull()
  })
})
