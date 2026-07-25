import { describe, it, expect } from 'vitest'
import type { Ticket } from '../api'
import {
  TICKET_TABS, myEmails, isUnassigned, tabForTicket,
  kindGroupForTicket, groupForTicket,
} from '../utils/ticketBuckets'

const T = (overrides: Partial<Ticket> = {}): Ticket => ({
  key: 'EX-1', summary: 'A bug', description: '',
  status: 'Open', priority: 'Medium', issue_type: 'Bug',
  project_key: 'EX', assignee_email: 'me@example.com',
  reporter_email: 'pm@example.com', url: '', created_at: '',
  updated_at: '', synced_at: '', status_category: 'new',
  labels: [],
  ...overrides,
})

const ME = new Set(['me@example.com'])

describe('TICKET_TABS', () => {
  it('orders tabs open, in_progress, resolved, triaged (triaged last)', () => {
    expect(TICKET_TABS.map((t) => t.key)).toEqual(
      ['open', 'in_progress', 'resolved', 'triaged'])
  })
})

describe('myEmails', () => {
  it('collects non-empty instance emails lower-cased', () => {
    expect(myEmails([
      { email: 'Me@Example.com' }, { email: '' },
    ])).toEqual(new Set(['me@example.com']))
  })
})

describe('isUnassigned', () => {
  it('true for empty or whitespace assignee', () => {
    expect(isUnassigned(T({ assignee_email: '' }))).toBe(true)
    expect(isUnassigned(T({ assignee_email: '  ' }))).toBe(true)
    expect(isUnassigned(T())).toBe(false)
  })
})

describe('tabForTicket', () => {
  it('mine + new -> open', () => {
    expect(tabForTicket(T(), ME)).toBe('open')
  })
  it('mine + indeterminate -> in_progress', () => {
    expect(tabForTicket(T({ status_category: 'indeterminate' }), ME))
      .toBe('in_progress')
  })
  it('mine + done -> resolved', () => {
    expect(tabForTicket(T({ status_category: 'done' }), ME))
      .toBe('resolved')
  })
  it('assigned to someone else -> triaged, regardless of status', () => {
    expect(tabForTicket(T({
      assignee_email: 'other@example.com', status_category: 'done',
    }), ME)).toBe('triaged')
  })
  it('unassigned -> triaged', () => {
    expect(tabForTicket(T({ assignee_email: '' }), ME)).toBe('triaged')
  })
  it('assignee match is case-insensitive', () => {
    expect(tabForTicket(T({ assignee_email: 'ME@example.com' }), ME))
      .toBe('open')
  })
  it('mine + empty status_category -> open (never-synced fallback)', () => {
    expect(tabForTicket(T({ status_category: '' }), ME)).toBe('open')
  })
})

describe('kindGroupForTicket', () => {
  it('semantic label wins over the project-prefix fallback', () => {
    // A flaky/perf label classifies the ticket regardless of its key.
    expect(kindGroupForTicket(T({
      key: 'ABC-1', summary: 'flaky regression benchmark',
    })).name).toBe('Flaky tests')
  })
  it('testman-automation label -> Flaky tests', () => {
    expect(kindGroupForTicket(T({
      labels: ['testman-automation'],
    })).name).toBe('Flaky tests')
  })
  it('failing-target summary -> Flaky tests', () => {
    expect(kindGroupForTicket(T({
      summary: 'Failing target detected: //a/b:suite',
    })).name).toBe('Flaky tests')
  })
  it('benchmarking-regression label -> Performance', () => {
    expect(kindGroupForTicket(T({
      labels: ['benchmarking-regression', 'release-blocker'],
    })).name).toBe('Performance')
  })
  it('perf-not-triaged label -> Performance', () => {
    expect(kindGroupForTicket(T({
      labels: ['release-1.2-perf-not-triaged'],
    })).name).toBe('Performance')
  })
  it('regression keyword in summary -> Performance', () => {
    expect(kindGroupForTicket(T({
      summary: 'Perf regression in query 7',
    })).name).toBe('Performance')
  })
  it('flaky beats performance when both match', () => {
    expect(kindGroupForTicket(T({
      labels: ['testman-automation', 'benchmarking-regression'],
    })).name).toBe('Flaky tests')
  })
  it('unlabelled ticket groups under its own project prefix', () => {
    expect(kindGroupForTicket(T({
      key: 'ABC-1', summary: 'Customer incident',
    })).name).toBe('ABC')
    expect(kindGroupForTicket(T({
      key: 'PROJ-99', summary: 'Customer incident',
    })).name).toBe('PROJ')
  })
  it('falls back to project_key then Other when the key has no prefix', () => {
    expect(kindGroupForTicket(T({ key: '', project_key: 'XYZ' })).name)
      .toBe('XYZ')
    expect(kindGroupForTicket(T({ key: '', project_key: '' })).name)
      .toBe('Other')
  })
  it('group priorities order Flaky < Performance < prefix fallback', () => {
    const p = (t: Ticket) => kindGroupForTicket(t).priority
    expect(p(T({ labels: ['testman-automation'] })))
      .toBeLessThan(p(T({ labels: ['benchmarking-regression'] })))
    expect(p(T({ labels: ['benchmarking-regression'] })))
      .toBeLessThan(p(T({})))
  })
})

describe('groupForTicket', () => {
  it('unassigned in triaged tab -> pinned-to-top Unassigned group', () => {
    const g = groupForTicket(T({ assignee_email: '' }), 'triaged')
    expect(g.name).toBe('Unassigned')
    expect(g.priority).toBeLessThan(0)
  })
  it('assigned-to-other in triaged tab keeps its kind group', () => {
    expect(groupForTicket(T({
      assignee_email: 'other@example.com', labels: ['testman-automation'],
    }), 'triaged').name).toBe('Flaky tests')
  })
  it('non-triaged tabs always use the kind group', () => {
    expect(groupForTicket(T({ key: 'EX-1' }), 'open').name).toBe('EX')
  })
})
