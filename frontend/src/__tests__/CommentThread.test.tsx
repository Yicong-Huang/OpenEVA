import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { InlineComments, GeneralComments } from '../components/pr/CommentThread'
import type { PRDetail } from '../types'

type InlineComment = PRDetail['inlineComments'][number]
type Comment = PRDetail['comments'][number]

const defaultProps = { repo: 'org/repo', prNumber: 42, onRefresh: vi.fn() }

const makeInline = (overrides: Partial<InlineComment>): InlineComment => ({
  id: 'c1',
  user: 'alice',
  avatar: '',
  path: 'src/app.tsx',
  line: 42,
  body: 'Looks good',
  createdAt: new Date().toISOString(),
  diffHunk: '@@ -10,3 +10,4 @@ function foo() {',
  inReplyToId: null,
  ...overrides,
})

describe('InlineComments', () => {
  it('renders comment author and body', () => {
    const comments = [makeInline({ id: 'c1', user: 'alice', body: 'Nice change' })]
    render(<InlineComments inlineComments={comments} {...defaultProps} />)
    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('Nice change')).toBeInTheDocument()
  })

  it('groups inline comments by file', () => {
    const comments = [
      makeInline({ id: 'c1', path: 'src/a.ts', body: 'Comment on A' }),
      makeInline({ id: 'c2', path: 'src/b.ts', body: 'Comment on B' }),
      makeInline({ id: 'c3', path: 'src/a.ts', body: 'Another on A' }),
    ]
    render(<InlineComments inlineComments={comments} {...defaultProps} />)
    expect(screen.getByText('src/a.ts')).toBeInTheDocument()
    expect(screen.getByText('src/b.ts')).toBeInTheDocument()
    expect(screen.getByText('Comment on A')).toBeInTheDocument()
    expect(screen.getByText('Comment on B')).toBeInTheDocument()
    expect(screen.getByText('Another on A')).toBeInTheDocument()
  })

  it('shows threaded replies indented', () => {
    const top = makeInline({ id: 'top1', user: 'alice', body: 'Top comment' })
    const reply = makeInline({
      id: 'reply1',
      user: 'bob',
      body: 'Reply comment',
      inReplyToId: 'top1',
    })
    render(<InlineComments inlineComments={[top, reply]} {...defaultProps} />)
    // Both comments should be visible
    expect(screen.getByText('Top comment')).toBeInTheDocument()
    expect(screen.getByText('Reply comment')).toBeInTheDocument()
  })

  it('shows diff hunk context', () => {
    const comments = [makeInline({ id: 'c1', diffHunk: '@@ -1,5 +1,6 @@ import React' })]
    render(<InlineComments inlineComments={comments} {...defaultProps} />)
    expect(screen.getByText('@@ -1,5 +1,6 @@ import React')).toBeInTheDocument()
  })

  it('shows the comment count header', () => {
    const comments = [
      makeInline({ id: 'c1' }),
      makeInline({ id: 'c2', path: 'other.ts' }),
    ]
    render(<InlineComments inlineComments={comments} {...defaultProps} />)
    expect(screen.getByText('Code Comments (2)')).toBeInTheDocument()
  })

  it('returns null for empty inline comments', () => {
    const { container } = render(<InlineComments inlineComments={[]} {...defaultProps} />)
    expect(container.innerHTML).toBe('')
  })

  it('returns null for undefined inline comments', () => {
    const { container } = render(
      <InlineComments inlineComments={undefined as unknown as PRDetail['inlineComments']} {...defaultProps} />,
    )
    expect(container.innerHTML).toBe('')
  })
})

describe('GeneralComments', () => {
  it('renders comment author and body', () => {
    const comments: Comment[] = [
      { id: 1, author: { login: 'carol' }, body: 'Great PR!', createdAt: new Date().toISOString() },
    ]
    render(<GeneralComments comments={comments} {...defaultProps} />)
    expect(screen.getByText('carol')).toBeInTheDocument()
    expect(screen.getByText('Great PR!')).toBeInTheDocument()
  })

  it('shows comment count', () => {
    const comments: Comment[] = [
      { id: 1, author: { login: 'a' }, body: 'x', createdAt: new Date().toISOString() },
      { id: 2, author: { login: 'b' }, body: 'y', createdAt: new Date().toISOString() },
    ]
    render(<GeneralComments comments={comments} {...defaultProps} />)
    expect(screen.getByText('Comments (2)')).toBeInTheDocument()
  })

  it('shows 3-dot menu on comments with IDs', () => {
    const comments: Comment[] = [
      { id: 1, author: { login: 'alice' }, body: 'test', createdAt: new Date().toISOString() },
    ]
    render(<GeneralComments comments={comments} {...defaultProps} />)
    // 3-dot button should be present
    expect(screen.getByTitle('Actions')).toBeInTheDocument()
  })

  it('handles empty comments array', () => {
    render(<GeneralComments comments={[]} {...defaultProps} />)
    expect(screen.getByText('Comments (0)')).toBeInTheDocument()
  })
})
