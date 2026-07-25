import { describe, it, expect } from 'vitest'
import {
  colorForPriority, colorForStatus, colorForSeverity, shortSeverity,
} from '../utils/ticketColors'

describe('colorForSeverity', () => {
  it('Sev1 / critical / blocker -> red', () => {
    expect(colorForSeverity('Sev. 1').fg).toBe('var(--red)')
    expect(colorForSeverity('SEV1').fg).toBe('var(--red)')
    expect(colorForSeverity('Severity 0').fg).toBe('var(--red)')
    expect(colorForSeverity('Critical').fg).toBe('var(--red)')
    expect(colorForSeverity('Blocker').fg).toBe('var(--red)')
  })

  it('Sev2 / major -> orange', () => {
    expect(colorForSeverity('Sev. 2').fg).toBe('var(--orange)')
    expect(colorForSeverity('Major').fg).toBe('var(--orange)')
  })

  it('Sev3 / moderate -> yellow', () => {
    expect(colorForSeverity('Sev 3').fg).toBe('var(--yellow)')
    expect(colorForSeverity('Moderate').fg).toBe('var(--yellow)')
  })

  it('Sev4+ / minor -> dim', () => {
    expect(colorForSeverity('Sev. 4').fg).toBe('var(--text-dim)')
    expect(colorForSeverity('Sev5').fg).toBe('var(--text-dim)')
    expect(colorForSeverity('Minor').fg).toBe('var(--text-dim)')
  })

  it('empty / unknown -> dim panel', () => {
    expect(colorForSeverity('').fg).toBe('var(--text-dim)')
    expect(colorForSeverity('???').fg).toBe('var(--text-dim)')
    expect(colorForSeverity(null as unknown as string).fg).toBe('var(--text-dim)')
  })
})

describe('shortSeverity', () => {
  it('extracts the digit into SEVn', () => {
    expect(shortSeverity('Sev. 2')).toBe('SEV2')
    expect(shortSeverity('Severity 1')).toBe('SEV1')
    expect(shortSeverity('S3')).toBe('SEV3')
  })
  it('passes non-numeric through upper-cased', () => {
    expect(shortSeverity('critical')).toBe('CRITICAL')
    expect(shortSeverity('')).toBe('')
  })
})

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
