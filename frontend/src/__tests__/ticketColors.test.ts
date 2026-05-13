import { describe, it, expect } from 'vitest'
import { colorForPriority, colorForStatus } from '../utils/ticketColors'

describe('colorForPriority', () => {
  it('Highest / Critical / P0 / P1 -> red', () => {
    expect(colorForPriority('Highest').fg).toBe('var(--red)')
    expect(colorForPriority('Critical').fg).toBe('var(--red)')
    expect(colorForPriority('P0').fg).toBe('var(--red)')
    expect(colorForPriority('P1').fg).toBe('var(--red)')
  })

  it('High / P2 -> orange', () => {
    expect(colorForPriority('High').fg).toBe('var(--orange)')
    expect(colorForPriority('P2').fg).toBe('var(--orange)')
  })

  it('Medium / P3 -> yellow', () => {
    expect(colorForPriority('Medium').fg).toBe('var(--yellow)')
    expect(colorForPriority('P3').fg).toBe('var(--yellow)')
  })

  it('Low / P4 / P5 -> dim', () => {
    expect(colorForPriority('Low').fg).toBe('var(--text-dim)')
    expect(colorForPriority('P4').fg).toBe('var(--text-dim)')
    expect(colorForPriority('P5').fg).toBe('var(--text-dim)')
  })

  it('case-insensitive', () => {
    expect(colorForPriority('CRITICAL').fg).toBe('var(--red)')
    expect(colorForPriority('mEdIuM').fg).toBe('var(--yellow)')
  })

  it('empty / unknown -> dim panel', () => {
    expect(colorForPriority('').fg).toBe('var(--text-dim)')
    expect(colorForPriority('???').fg).toBe('var(--text-dim)')
    expect(colorForPriority(null as unknown as string).fg).toBe('var(--text-dim)')
  })

  it('precedence: highest beats high (substring overlap)', () => {
    // "Highest" contains "high" -- the matcher must hit the
    // Highest arm first so it doesn't fall through to orange.
    expect(colorForPriority('Highest').fg).toBe('var(--red)')
  })
})


describe('colorForStatus', () => {
  it('Done / Closed / Resolved -> green', () => {
    expect(colorForStatus('Done').fg).toBe('var(--green)')
    expect(colorForStatus('Closed').fg).toBe('var(--green)')
    expect(colorForStatus('Resolved').fg).toBe('var(--green)')
  })

  it('In Progress / In Review -> accent', () => {
    expect(colorForStatus('In Progress').fg).toBe('var(--accent)')
    expect(colorForStatus('In Review').fg).toBe('var(--accent)')
    // Repo workflow: "Patch Available" doesn't match progress/review.
    expect(colorForStatus('Patch Available').fg).toBe('var(--text-dim)')
  })

  it('Blocked / Blocker -> red', () => {
    expect(colorForStatus('Blocked').fg).toBe('var(--red)')
    expect(colorForStatus('Blocker').fg).toBe('var(--red)')
  })

  it('case-insensitive substring match', () => {
    expect(colorForStatus('DONE').fg).toBe('var(--green)')
    expect(colorForStatus('In progress').fg).toBe('var(--accent)')
  })

  it('empty / unknown -> dim panel', () => {
    expect(colorForStatus('').fg).toBe('var(--text-dim)')
    expect(colorForStatus('Custom Workflow Stage').fg).toBe('var(--text-dim)')
  })

  it('returns full ChipColors object with bg + fg', () => {
    const out = colorForStatus('Done')
    expect(out).toHaveProperty('bg')
    expect(out).toHaveProperty('fg')
  })
})
