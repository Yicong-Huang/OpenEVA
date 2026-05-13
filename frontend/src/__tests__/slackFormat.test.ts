import { describe, it, expect } from 'vitest'
import { markdownToSlack } from '../utils/slackFormat'

describe('markdownToSlack', () => {
  it('drops the top-level date header', () => {
    const md = [
      '- Tue Apr 21 standup',
      '    - Status:',
      '        - Repo Query Events:',
      '            - [EX-1001] Add QueryEnd',
    ].join('\n')
    const out = markdownToSlack(md)
    expect(out).not.toContain('Tue Apr 21 standup')
    expect(out.startsWith('Status:')).toBe(true)
  })

  it('flattens nested bullets to flush-left lines', () => {
    const md = [
      '- Header',
      '    - Status:',
      '        - Proj:',
      '            - entry one',
      '            - entry two',
    ].join('\n')
    const out = markdownToSlack(md)
    // No `- ` bullet markers in the output.
    expect(out).not.toMatch(/^- /m)
    expect(out).toContain('entry one')
    expect(out).toContain('entry two')
  })

  it('converts markdown links to Slack <url|text> syntax (clickable in paste)', () => {
    // Regression: used to strip the URL entirely, so the pasted standup
    // had no way back to the PR. Now we emit Slack's native mrkdwn link.
    const md = [
      '- Header',
      '    - Status:',
      '        - P:',
      '            - [[EX-123] My PR](https://github.com/a/b/pull/1) :white_check_mark:',
    ].join('\n')
    const out = markdownToSlack(md)
    expect(out).toContain('<https://github.com/a/b/pull/1|[EX-123] My PR>')
    expect(out).toContain(':white_check_mark:')
    // Neither the raw markdown form nor the naked URL should survive.
    expect(out).not.toMatch(/\[\[EX-123\] My PR\]\(https:/)
  })

  it('inserts blank line before each section header', () => {
    const md = [
      '- Header',
      '    - Status:',
      '        - Proj A:',
      '            - item a',
      '        - Proj B:',
      '            - item b',
      '    - Meeting Notes:',
    ].join('\n')
    const out = markdownToSlack(md)
    const lines = out.split('\n')
    // Blank line separates project groups and Meeting Notes.
    const idxA = lines.indexOf('Proj A:')
    const idxB = lines.indexOf('Proj B:')
    const idxMeeting = lines.indexOf('Meeting Notes:')
    expect(idxA).toBeGreaterThan(-1)
    expect(idxB).toBeGreaterThan(-1)
    expect(idxMeeting).toBeGreaterThan(-1)
    expect(lines[idxB - 1]).toBe('')
    expect(lines[idxMeeting - 1]).toBe('')
  })

  it('preserves ES ticket brackets inside the link label', () => {
    const md = [
      '- Header',
      '    - Status:',
      '        - ES tickets:',
      '            - [[EX-1001] Fix flaky](https://gh.com/a/b/pull/1)',
    ].join('\n')
    const out = markdownToSlack(md)
    // Label (inside `<url|...>`) still carries the `[EX-...]` prefix.
    expect(out).toContain('|[EX-1001] Fix flaky>')
  })

  it('indents task sub-bullets for readability', () => {
    const md = [
      '- Header',
      '    - Status:',
      '        - Proj:',
      '            - [EX-100] main line',
      '                - first-note detail',
    ].join('\n')
    const out = markdownToSlack(md)
    const lines = out.split('\n')
    const mainIdx = lines.findIndex(l => l.includes('main line'))
    const subIdx = lines.findIndex(l => l.includes('first-note detail'))
    expect(mainIdx).toBeGreaterThan(-1)
    expect(subIdx).toBe(mainIdx + 1)
    // Sub-bullet is prefixed with whitespace for continuation feel.
    expect(lines[subIdx].startsWith('  ')).toBe(true)
  })

  it('end-to-end sample matches the expected Slack paste shape', () => {
    const md = [
      '- Tue Apr 21 standup',
      '    - Status:',
      '        - Repo Query Events:',
      '            - [[EX-1001] Add QueryEnd](https://x)',
      '            - [[EX-1002] physicalPlan](https://y) :white_check_mark:',
      '        - ES tickets:',
      '            - [[EX-1001] Fix flaky suite](https://z)',
      '    - Meeting Notes:',
    ].join('\n')
    const out = markdownToSlack(md)
    expect(out).toBe([
      'Status:',
      '',
      'Repo Query Events:',
      '<https://x|[EX-1001] Add QueryEnd>',
      '<https://y|[EX-1002] physicalPlan> :white_check_mark:',
      '',
      'ES tickets:',
      '<https://z|[EX-1001] Fix flaky suite>',
      '',
      'Meeting Notes:',
    ].join('\n'))
  })

  it('handles empty input', () => {
    expect(markdownToSlack('')).toBe('')
  })

  it('passes through non-bullet lines (no leading "- ") with links converted', () => {
    // Real-world: an LLM-generated standup sometimes includes a free-text
    // sentence between bullets. parseLine returns null for those; the helper
    // must still emit the line so the Slack paste keeps the prose AND the
    // clickable link (Slack mrkdwn form).
    const md = 'Bare line with a [link](https://x) inside.\n'
              + '- Tue Apr 21 standup\n'
              + '    - Status:\n'
              + '        - P:\n'
              + '            - item'
    const out = markdownToSlack(md)
    expect(out).toContain('Bare line with a <https://x|link> inside.')
  })

  it('keeps the Slack paste link text short enough to cover the whole title', () => {
    // Regression: even when the label contains a long PR title with
    // bracketed ticket, the Slack form should capture the entire thing
    // (e.g. `<url|[EX-55608][PYTHON] Refactor ...>`).
    const md = [
      '- Header',
      '    - Status:',
      '        - Repo:',
      '            - [[EX-55608][PYTHON] Refactor](https://github.com/example/repo/pull/55222)',
    ].join('\n')
    const out = markdownToSlack(md)
    expect(out).toContain('<https://github.com/example/repo/pull/55222|[EX-55608][PYTHON] Refactor>')
  })
})
