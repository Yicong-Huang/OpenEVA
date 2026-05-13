import { describe, it, expect } from 'vitest'
import { applicableActions, renderPrompt, TICKET_ACTIONS } from '../utils/ticketActions'
import type { Ticket } from '../api'


const TICKET = (overrides: Partial<Ticket> = {}): Ticket => ({
  key: 'ANY-1',
  summary: '',
  description: '',
  status: 'Open',
  priority: 'Medium',
  issue_type: 'Task',
  project_key: 'ANY',
  assignee_email: '',
  reporter_email: '',
  url: 'https://j.example/browse/ANY-1',
  created_at: '',
  updated_at: '',
  synced_at: '',
  labels: [],
  components: [],
  fix_versions: [],
  ...overrides,
})


describe('ticketActions registry', () => {
  it('always includes the generic Investigate fallback', () => {
    const acts = applicableActions(TICKET())
    expect(acts.find((a) => a.id === 'investigate')).toBeTruthy()
  })

  it('matches Fix flaky test by `flaky` label', () => {
    const acts = applicableActions(
      TICKET({ labels: ['flaky-test'] }),
    )
    expect(acts.find((a) => a.id === 'fix-flaky-test')).toBeTruthy()
  })

  it('matches Fix flaky test by summary keyword', () => {
    const acts = applicableActions(
      TICKET({ summary: 'Example test_arrow_basic is flaky' }),
    )
    expect(acts.find((a) => a.id === 'fix-flaky-test')).toBeTruthy()
  })

  it('matches Bisect regression by `perf` label', () => {
    const acts = applicableActions(
      TICKET({ labels: ['perf-regression'] }),
    )
    expect(acts.find((a) => a.id === 'bisect-perf-regression')).toBeTruthy()
  })

  it('matches Bisect regression by summary "regression"', () => {
    const acts = applicableActions(
      TICKET({ summary: 'GroupByApply regression on 4.x' }),
    )
    expect(acts.find((a) => a.id === 'bisect-perf-regression')).toBeTruthy()
  })

  it('matches Reproduce bug for issue_type=Bug', () => {
    const acts = applicableActions(TICKET({ issue_type: 'Bug' }))
    expect(acts.find((a) => a.id === 'reproduce-bug')).toBeTruthy()
  })

  it('does NOT match Reproduce bug for issue_type=Task', () => {
    const acts = applicableActions(TICKET({ issue_type: 'Task' }))
    expect(acts.find((a) => a.id === 'reproduce-bug')).toBeFalsy()
  })

  it('renderPrompt substitutes {key} and {summary}', () => {
    const action = TICKET_ACTIONS.find((a) => a.id === 'investigate')!
    const out = renderPrompt(action, TICKET({
      key: 'EX-99', summary: 'Fix something',
    }))
    expect(out).toContain('EX-99')
    expect(out).toContain('Fix something')
    expect(out).not.toContain('{key}')
    expect(out).not.toContain('{summary}')
  })

  it('renderPrompt falls back to key when summary is empty', () => {
    const action = TICKET_ACTIONS.find((a) => a.id === 'investigate')!
    const out = renderPrompt(action, TICKET({
      key: 'X-1', summary: '',
    }))
    expect(out).toContain('X-1')
  })
})
