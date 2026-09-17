import { describe, it, expect } from 'vitest'
import { resumeNotice } from '../components/SessionCardHelpers'

describe('resumeNotice', () => {
  it('reports an ERROR when the pane died right after launch', () => {
    // Regression: the server used to hardcode running=true, and the card
    // only looked at `action` -- so a session whose agent exited at startup
    // showed as resumed while the terminal never connected.
    const n = resumeNotice({ action: 'resumed', running: false }, 'review-x')
    expect(n).not.toBeNull()
    expect(n!.kind).toBe('error')
    expect(n!.title).toMatch(/could not be resumed/i)
    expect(n!.message).toContain('review-x')
  })

  it('prefers the died-at-launch error over the relaunch warning', () => {
    // running=false can accompany either action; the hard failure wins.
    const n = resumeNotice({ action: 'relaunched', running: false }, 'sess')
    expect(n!.kind).toBe('error')
  })

  it('warns that history was lost on a relaunch', () => {
    const n = resumeNotice({ action: 'relaunched', running: true }, 'sess-1')
    expect(n).not.toBeNull()
    expect(n!.kind).toBe('warning')
    expect(n!.title).toMatch(/history not resumed/i)
    expect(n!.message).toContain('sess-1')
  })

  it('mentions BOTH reasons a relaunch can happen', () => {
    // Either no uuid was on record, or its transcript was deleted. The old
    // copy only mentioned the first and misled users hitting the second.
    const n = resumeNotice({ action: 'relaunched', running: true }, 's')
    expect(n!.message).toMatch(/nothing on record/i)
    expect(n!.message).toMatch(/transcript was deleted/i)
  })

  it('stays quiet on a clean resume', () => {
    expect(resumeNotice({ action: 'resumed', running: true }, 's')).toBeNull()
  })

  it('stays quiet on a noop (session was already alive)', () => {
    expect(resumeNotice({ action: 'noop', running: true }, 's')).toBeNull()
  })
})
