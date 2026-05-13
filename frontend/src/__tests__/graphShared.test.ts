import { describe, it, expect } from 'vitest'
import { latestPrCiStatus, ciPillStyle } from '../components/graphShared'

describe('latestPrCiStatus', () => {
  it('returns empty string when no PRs', () => {
    expect(latestPrCiStatus([])).toBe('')
  })

  it('picks ci_status of the most-recently-updated PR', () => {
    const prs = [
      { ci_status: 'success', last_updated: '2026-01-01T00:00:00Z' },
      { ci_status: 'failure', last_updated: '2026-04-01T00:00:00Z' },
      { ci_status: 'pending', last_updated: '2026-03-15T00:00:00Z' },
    ]
    expect(latestPrCiStatus(prs)).toBe('failure')
  })

  it('handles PRs with missing last_updated by treating as oldest', () => {
    const prs = [
      { ci_status: 'success', last_updated: '2026-01-01T00:00:00Z' },
      { ci_status: 'pending' /* no last_updated */ },
    ]
    expect(latestPrCiStatus(prs)).toBe('success')
  })
})


describe('ciPillStyle', () => {
  it('returns null for empty status', () => {
    expect(ciPillStyle('')).toBeNull()
  })

  it('green palette for success', () => {
    const s = ciPillStyle('success')!
    expect(s.fg).toBe('var(--green)')
    expect(s.bg).toContain('var(--green)')
    expect(s.border).toContain('var(--green)')
  })

  it('red palette for failure', () => {
    const s = ciPillStyle('failure')!
    expect(s.fg).toBe('var(--red)')
  })

  it('also handles "failed" alias as red', () => {
    expect(ciPillStyle('failed')!.fg).toBe('var(--red)')
  })

  it('yellow palette for any other status (in-flight)', () => {
    expect(ciPillStyle('pending')!.fg).toBe('var(--yellow)')
    expect(ciPillStyle('running')!.fg).toBe('var(--yellow)')
    expect(ciPillStyle('queued')!.fg).toBe('var(--yellow)')
    expect(ciPillStyle('unknown')!.fg).toBe('var(--yellow)')
  })
})
