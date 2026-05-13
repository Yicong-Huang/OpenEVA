import { describe, it, expect } from 'vitest'
import { taskStatusSummary, isTaskBlocked, isTerminalTaskStatus, TERMINAL_TASK_STATUSES, evalActionCondition, classifyHistoryEntry, historyKindColor } from '../utils/taskHelpers'
import type { Task, PR } from '../types'

const makePR = (overrides: Partial<PR> = {}): PR => ({
  number: 1,
  url: 'https://github.com/org/repo/pull/1',
  title: 'test PR',
  status: 'open',
  ci_status: '',
  review_status: '',
  comment_count: 0,
  additions: 0,
  deletions: 0,
  author: 'test',
  head_branch: 'feature',
  base_branch: 'main',
  last_updated: '2026-01-01',
  ...overrides,
})

const makeTask = (overrides: Partial<Task> = {}): Task => ({
  task_id: 'test',
  project: 'test-project',
  description: 'Test task',
  type: 'task',
  status: 'not_started' as const,
  group_name: '',
  notes: '',
  priority: 0,
  ticket_id: null,
  ticket_url: null,
  dependencies: [],
  follow_ups: [],
  prs: [],
  created_at: '2026-01-01',
  updated_at: '2026-01-01',
  ...overrides,
})

describe('taskStatusSummary', () => {
  it('returns done with PR info when done and has PRs', () => {
    const prs = [makePR({ number: 42 })]
    const result = taskStatusSummary('done', prs, [], {}, null, false)
    expect(result).toContain('PR #42')
  })

  it('returns blocked message with blocker name', () => {
    const tasks = { 'dep-1': makeTask({ status: 'in_progress' }) }
    const result = taskStatusSummary('blocked', [], ['dep-1'], tasks, null, false)
    expect(result).toContain('dep-1')
  })

  it('returns in_review with PR number', () => {
    const prs = [makePR({ number: 99, status: 'open' })]
    const result = taskStatusSummary('in_review', prs, [], {}, null, false)
    expect(result).toContain('PR #99')
  })

  it('returns needs ticket message when hasTickets but no ticket', () => {
    const result = taskStatusSummary('not_started', [], [], {}, null, true)
    expect(result).toContain('ticket')
  })

  it('returns closed status', () => {
    const result = taskStatusSummary('closed', [], [], {}, null, false)
    expect(result.length).toBeGreaterThan(0)
  })

  it('blocked falls back to generic message when no specific blocker found', () => {
    // Edge case: status is `blocked` but every dep is `done` -- the
    // computed-blocked logic shouldn't normally fire here, but the
    // helper is defensive. Returns the generic "blocked by deps" line.
    const tasks = {
      'dep-1': makeTask({ status: 'done' }),
      'dep-2': makeTask({ status: 'done' }),
    }
    const result = taskStatusSummary(
      'blocked', [], ['dep-1', 'dep-2'], tasks, null, false,
    )
    // Doesn't name a specific blocker (since none qualify); shows
    // the generic "deps blocked" message instead.
    expect(result).not.toContain('dep-1')
    expect(result).not.toContain('dep-2')
    expect(result.length).toBeGreaterThan(0)
  })

  it('in_review falls back to generic message when no open PR', () => {
    // Status `in_review` with only merged/closed PRs -- normally a
    // synced state would have an open PR, but the helper handles the
    // boundary case where the PR was just merged and the task hasn't
    // re-promoted yet. Generic "审查中" copy.
    const prs = [
      makePR({ number: 1, status: 'merged' }),
      makePR({ number: 2, status: 'closed' }),
    ]
    const result = taskStatusSummary('in_review', prs, [], {}, null, false)
    expect(result).not.toContain('PR #')
    expect(result.length).toBeGreaterThan(0)
  })

  it('in_progress with open PR shows PR number', () => {
    const prs = [makePR({ number: 7, status: 'open' })]
    const result = taskStatusSummary('in_progress', prs, [], {}, null, false)
    expect(result).toContain('PR #7')
  })

  it('in_progress without open PR uses generic message', () => {
    const prs = [makePR({ number: 5, status: 'merged' })]
    const result = taskStatusSummary('in_progress', prs, [], {}, null, false)
    expect(result).not.toContain('PR #')
    expect(result.length).toBeGreaterThan(0)
  })

  it('not_started without project tickets shows generic ready message', () => {
    // hasTickets=false -> skip the "needs ticket" branch.
    const result = taskStatusSummary('not_started', [], [], {}, null, false)
    expect(result).not.toContain('ticket')
    expect(result.length).toBeGreaterThan(0)
  })

  it('needs_follow_up returns its English label verbatim', () => {
    const result = taskStatusSummary('needs_follow_up', [], [], {}, null, false)
    expect(result).toBe('Needs follow-up')
  })
})

describe('isTaskBlocked', () => {
  it('returns false for task with no deps', () => {
    const tasks = { t1: makeTask() }
    expect(isTaskBlocked('t1', tasks)).toBe(false)
  })

  it('returns true when dep is not done', () => {
    const tasks = {
      t1: makeTask({ dependencies: ['t2'] }),
      t2: makeTask({ status: 'in_progress' }),
    }
    expect(isTaskBlocked('t1', tasks)).toBe(true)
  })

  it('returns false when all deps are done', () => {
    const tasks = {
      t1: makeTask({ dependencies: ['t2'] }),
      t2: makeTask({ status: 'done' }),
    }
    expect(isTaskBlocked('t1', tasks)).toBe(false)
  })

  it('returns true when dep does not exist', () => {
    const tasks = {
      t1: makeTask({ dependencies: ['missing'] }),
    }
    expect(isTaskBlocked('t1', tasks)).toBe(true)
  })

  it('returns false for unknown task id', () => {
    expect(isTaskBlocked('nonexistent', {})).toBe(false)
  })
})

describe('evalActionCondition', () => {
  it('returns true when no condition', () => {
    expect(evalActionCondition(null, {})).toBe(true)
    expect(evalActionCondition(undefined, {})).toBe(true)
    expect(evalActionCondition('', {})).toBe(true)
  })

  it('ci_failed: true when ci_status is failure', () => {
    expect(evalActionCondition('ci_failed', { ci_status: 'failure' })).toBe(true)
    expect(evalActionCondition('ci_failed', { ci_status: 'success' })).toBe(false)
  })

  it('has_pr: true when prs array is non-empty', () => {
    expect(evalActionCondition('has_pr', { prs: [makePR()] })).toBe(true)
    expect(evalActionCondition('has_pr', { prs: [] })).toBe(false)
  })

  it('has_pr: true when pr_number is set', () => {
    expect(evalActionCondition('has_pr', { pr_number: 42 })).toBe(true)
  })

  it('has_open_pr: true only when open PR exists', () => {
    expect(evalActionCondition('has_open_pr', { prs: [makePR({ status: 'open' })] })).toBe(true)
    expect(evalActionCondition('has_open_pr', { prs: [makePR({ status: 'merged' })] })).toBe(false)
  })

  it('unknown condition returns true', () => {
    expect(evalActionCondition('unknown_cond', {})).toBe(true)
  })
})

describe('classifyHistoryEntry', () => {
  it('identifies status transitions by the "status:" prefix', () => {
    expect(classifyHistoryEntry('status: not_started -> in_review')).toBe('status')
    expect(classifyHistoryEntry('status: X -> closed')).toBe('status')
  })

  it('identifies PR-linked events', () => {
    expect(classifyHistoryEntry('linked PR #123')).toBe('pr_linked')
    expect(classifyHistoryEntry('linked PR #4567')).toBe('pr_linked')
  })

  it('identifies PR-merged events only when the exact shape matches', () => {
    expect(classifyHistoryEntry('PR #100 merged')).toBe('pr_merged')
    expect(classifyHistoryEntry('PR #2 merged')).toBe('pr_merged')
    // Ambiguous / near-miss shapes that should NOT be classified as auto
    expect(classifyHistoryEntry('PR #100 was merged by bob')).toBe('manual')
    expect(classifyHistoryEntry('waiting for PR #100 merged')).toBe('manual')
    expect(classifyHistoryEntry('PR 100 merged')).toBe('manual')  // missing #
  })

  it('returns manual for arbitrary text and empty / nullish input', () => {
    expect(classifyHistoryEntry('rebased on master')).toBe('manual')
    expect(classifyHistoryEntry('investigated flaky test')).toBe('manual')
    expect(classifyHistoryEntry('')).toBe('manual')
    expect(classifyHistoryEntry(undefined)).toBe('manual')
    expect(classifyHistoryEntry(null)).toBe('manual')
  })

  it('tolerates leading whitespace in stored text', () => {
    // Backend caps at 100 chars via rstrip but older rows may exist
    expect(classifyHistoryEntry('  status: X -> Y')).toBe('status')
    expect(classifyHistoryEntry(' linked PR #9')).toBe('pr_linked')
  })
})

describe('historyKindColor', () => {
  it('maps each auto kind to a CSS var, manual to undefined', () => {
    expect(historyKindColor('status')).toBe('var(--blue)')
    expect(historyKindColor('pr_linked')).toBe('var(--green)')
    expect(historyKindColor('pr_merged')).toBe('var(--purple)')
    expect(historyKindColor('manual')).toBeUndefined()
  })
})


describe('TERMINAL_TASK_STATUSES + isTerminalTaskStatus', () => {
  it('the set is exactly {done, closed} -- mirrors backend', () => {
    // Lockstep with `eva_db.TERMINAL_TASK_STATUSES`. If backend
    // adds another terminal state we want the test to flag the
    // drift; bump both sides together.
    expect(TERMINAL_TASK_STATUSES).toEqual(new Set(['done', 'closed']))
  })

  it('isTerminalTaskStatus returns true for done / closed', () => {
    expect(isTerminalTaskStatus('done')).toBe(true)
    expect(isTerminalTaskStatus('closed')).toBe(true)
  })

  it('returns false for any other state', () => {
    expect(isTerminalTaskStatus('not_started')).toBe(false)
    expect(isTerminalTaskStatus('in_progress')).toBe(false)
    expect(isTerminalTaskStatus('in_review')).toBe(false)
    expect(isTerminalTaskStatus('blocked')).toBe(false)
    expect(isTerminalTaskStatus('needs_follow_up')).toBe(false)
  })

  it('tolerates undefined / null / empty so callers can pass task?.status', () => {
    expect(isTerminalTaskStatus(undefined)).toBe(false)
    expect(isTerminalTaskStatus(null)).toBe(false)
    expect(isTerminalTaskStatus('')).toBe(false)
  })
})
