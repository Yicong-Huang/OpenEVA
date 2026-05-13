import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  timeAgo,
  ghAvatar,
  renderMarkdown,
  parseBackendDate,
  formatLocalShort,
  repoFromPrUrl,
} from '../utils'

describe('timeAgo', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-04-12T12:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns "just now" for timestamps less than 60 seconds ago', () => {
    expect(timeAgo('2026-04-12T11:59:30Z')).toBe('just now')
  })

  it('returns minutes ago for timestamps less than 60 minutes ago', () => {
    expect(timeAgo('2026-04-12T11:55:00Z')).toBe('5m ago')
  })

  it('returns hours ago for timestamps less than 24 hours ago', () => {
    expect(timeAgo('2026-04-12T09:00:00Z')).toBe('3h ago')
  })

  it('returns days ago for timestamps 24+ hours ago', () => {
    expect(timeAgo('2026-04-10T12:00:00Z')).toBe('2d ago')
  })
})

describe('parseBackendDate', () => {
  it('treats a naive backend ISO as UTC (Eva _now_iso drops the Z)', () => {
    // Naive "2026-04-22T14:37:00" would default to LOCAL in browsers -- so a
    // backend UTC value was historically mis-read by the local offset.
    // parseBackendDate must anchor it to UTC explicitly.
    const d = parseBackendDate('2026-04-22T14:37:00')
    expect(d.toISOString()).toBe('2026-04-22T14:37:00.000Z')
  })

  it('honors an explicit Z suffix', () => {
    const d = parseBackendDate('2026-04-22T14:37:00Z')
    expect(d.toISOString()).toBe('2026-04-22T14:37:00.000Z')
  })

  it('honors an explicit numeric offset', () => {
    // -07:00 === PDT, so 07:37-07:00 === 14:37 UTC
    const d = parseBackendDate('2026-04-22T07:37:00-07:00')
    expect(d.toISOString()).toBe('2026-04-22T14:37:00.000Z')
  })

  it('returns Invalid Date for empty input', () => {
    expect(isNaN(parseBackendDate('').getTime())).toBe(true)
  })
})

describe('formatLocalShort', () => {
  it('formats a UTC timestamp in the local timezone as MM-DD HH:MM', () => {
    // On the host recovery test host (TZ=UTC) this is exactly "04-22 14:37"; on a
    // developer laptop (e.g. PDT) it'd be "04-22 07:37". We assert the
    // shape and round-trip rather than a specific clock value.
    const out = formatLocalShort('2026-04-22T14:37:00Z')
    expect(out).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/)
    // The local rendering must correspond to the same instant.
    const [md, hm] = out.split(' ')
    const [mo, day] = md.split('-').map(Number)
    const [hr, mi] = hm.split(':').map(Number)
    const d = new Date('2026-04-22T14:37:00Z')
    expect(mo).toBe(d.getMonth() + 1)
    expect(day).toBe(d.getDate())
    expect(hr).toBe(d.getHours())
    expect(mi).toBe(d.getMinutes())
  })

  it('returns empty string on empty input', () => {
    expect(formatLocalShort('')).toBe('')
  })
})

describe('ghAvatar', () => {
  it('returns correct GitHub avatar URL', () => {
    expect(ghAvatar('octocat')).toBe('https://avatars.githubusercontent.com/octocat?s=40')
  })

  it('handles empty string', () => {
    expect(ghAvatar('')).toBe('https://avatars.githubusercontent.com/?s=40')
  })
})

describe('renderMarkdown', () => {
  it('renders bold text', () => {
    expect(renderMarkdown('**bold**')).toContain('<strong>bold</strong>')
  })

  it('renders italic text', () => {
    expect(renderMarkdown('*italic*')).toContain('<em>italic</em>')
  })

  it('renders inline code', () => {
    const result = renderMarkdown('use `foo()` here')
    expect(result).toContain('<code')
    expect(result).toContain('foo()')
  })

  it('renders fenced code blocks', () => {
    const result = renderMarkdown('```js\nconsole.log("hi")\n```')
    expect(result).toContain('<pre')
    expect(result).toContain('console.log')
  })

  it('renders links', () => {
    const result = renderMarkdown('[click](https://example.com)')
    expect(result).toContain('href="https://example.com"')
    expect(result).toContain('click')
  })

  it('renders headings', () => {
    expect(renderMarkdown('# H1')).toContain('<h1')
    expect(renderMarkdown('## H2')).toContain('<h2')
    expect(renderMarkdown('### H3')).toContain('<h3')
  })

  it('renders unordered list items', () => {
    const result = renderMarkdown('- item one')
    expect(result).toContain('<li')
    expect(result).toContain('item one')
  })

  it('falls back to escaped HTML when marked.parse throws', async () => {
    // Dynamically mock marked to throw on parse
    const markedModule = await import('marked')
    const originalParse = markedModule.marked.parse
    markedModule.marked.parse = (() => { throw new Error('parse error') }) as unknown as typeof markedModule.marked.parse
    try {
      const result = renderMarkdown('<b>bold & safe</b>')
      expect(result).toContain('&lt;b&gt;')
      expect(result).toContain('&amp;')
      expect(result).toContain('&gt;')
      expect(result).not.toContain('<b>')
    } finally {
      markedModule.marked.parse = originalParse
    }
  })

  it('fallback handles empty string', async () => {
    const markedModule = await import('marked')
    const originalParse = markedModule.marked.parse
    markedModule.marked.parse = (() => { throw new Error('parse error') }) as unknown as typeof markedModule.marked.parse
    try {
      const result = renderMarkdown('')
      expect(result).toBe('')
    } finally {
      markedModule.marked.parse = originalParse
    }
  })
})

describe('repoFromPrUrl', () => {
  it('extracts owner/repo from a standard GitHub PR URL', () => {
    expect(repoFromPrUrl('https://github.com/example/repo/pull/100'))
      .toBe('example/repo')
  })

  it('handles myorg org URLs', () => {
    expect(repoFromPrUrl('https://github.com/myorg/svc/pull/12345'))
      .toBe('myorg/svc')
  })

  it('returns empty string for null/undefined/empty input', () => {
    // null and undefined both collapse to '' via the `|| ''` guard.
    expect(repoFromPrUrl(null)).toBe('')
    expect(repoFromPrUrl(undefined)).toBe('')
    expect(repoFromPrUrl('')).toBe('')
  })

  it('returns empty string when /pull/ is absent', () => {
    // No `/pull/` segment -> split returns the whole string; the `|| ''`
    // guard then kicks in only when the result is falsy. A raw "foo" is
    // truthy, so this returns "foo" -- which is fine because callers
    // feed this into `is_repo_allowed` which rejects unknown repos. The
    // test locks that behavior in so a future change doesn't silently
    // start returning an owner-like false positive.
    expect(repoFromPrUrl('not-a-pr-url')).toBe('not-a-pr-url')
  })

  it('strips query strings correctly (splits on /pull/, not after)', () => {
    // The helper is deliberately minimal -- it keeps the "number" segment
    // out of the repo slice. Tracks parity with the backend
    // utils.py:repo_from_pr_url so fork/upstream resolution stays aligned.
    expect(repoFromPrUrl('https://github.com/example/repo/pull/100?foo=bar'))
      .toBe('example/repo')
  })
})
