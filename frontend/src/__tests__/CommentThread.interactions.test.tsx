import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { InlineComments, GeneralComments } from '../components/pr/CommentThread'
import type { PRDetail } from '../types'

type InlineComment = PRDetail['inlineComments'][number]

// Mock api module
vi.mock('../api', () => ({
  api: {
    replyToComment: vi.fn().mockResolvedValue({ ok: true }),
    editComment: vi.fn().mockResolvedValue({ ok: true }),
    resolveThread: vi.fn().mockResolvedValue({ ok: true }),
  },
}))

import { api } from '../api'
const mockReply = vi.mocked(api.replyToComment)
const mockEdit = vi.mocked(api.editComment)
const mockResolve = vi.mocked(api.resolveThread)

const defaultProps = { repo: 'org/repo', prNumber: 42, onRefresh: vi.fn() }

const makeInline = (overrides: Partial<InlineComment>): InlineComment => ({
  id: '101',
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

beforeEach(() => {
  vi.clearAllMocks()
  // jsdom does not implement scrollIntoView
  Element.prototype.scrollIntoView = vi.fn()
})

describe('InlineComments - interactions', () => {
  it('opens reply editor when Reply... placeholder is clicked', async () => {
    const comments = [makeInline({ id: '101', user: 'alice', body: 'Need changes' })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    const replyPlaceholder = screen.getByText('Reply...')
    fireEvent.click(replyPlaceholder)

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Write a reply...')).toBeInTheDocument()
    })
  })

  it('submits a reply and shows it optimistically', async () => {
    const comments = [makeInline({ id: '101', user: 'alice', body: 'Need changes' })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    // Open reply editor
    fireEvent.click(screen.getByText('Reply...'))
    const textarea = screen.getByPlaceholderText('Write a reply...')
    fireEvent.change(textarea, { target: { value: 'Fixed it!' } })

    // Submit
    fireEvent.click(screen.getByText('Reply'))

    await waitFor(() => {
      expect(mockReply).toHaveBeenCalledWith('org/repo', 42, 101, 'Fixed it!', true)
    })

    // Optimistic reply should appear
    await waitFor(() => {
      expect(screen.getByText('Fixed it!')).toBeInTheDocument()
    })
  })

  it('cancels reply when Cancel is clicked', async () => {
    const comments = [makeInline({ id: '101', user: 'alice', body: 'Need changes' })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    fireEvent.click(screen.getByText('Reply...'))
    expect(screen.getByPlaceholderText('Write a reply...')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Cancel'))
    await waitFor(() => {
      expect(screen.queryByPlaceholderText('Write a reply...')).toBeNull()
    })
  })

  it('opens edit mode for own comment', async () => {
    const comments = [makeInline({ id: '101', user: 'me', body: 'My comment' })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    // Click the 3-dot menu
    const menuBtn = screen.getByTitle('Actions')
    fireEvent.click(menuBtn)

    await waitFor(() => {
      expect(screen.getByText('Edit')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Edit'))

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Edit comment...')).toBeInTheDocument()
    })
  })

  it('submits edit and updates body', async () => {
    const comments = [makeInline({ id: '101', user: 'me', body: 'Original' })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    // Open 3-dot menu -> Edit
    fireEvent.click(screen.getByTitle('Actions'))
    await waitFor(() => { fireEvent.click(screen.getByText('Edit')) })

    const textarea = screen.getByPlaceholderText('Edit comment...')
    fireEvent.change(textarea, { target: { value: 'Updated text' } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      expect(mockEdit).toHaveBeenCalledWith('org/repo', 101, 'Updated text', true)
    })

    await waitFor(() => {
      expect(screen.getByText('Updated text')).toBeInTheDocument()
    })
  })

  it('shows resolve button for unresolved threads with threadId', async () => {
    const comments = [makeInline({ id: '101', user: 'alice', body: 'Fix this', threadId: 'thread-1', isResolved: false })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    await waitFor(() => {
      const resolveBtn = screen.getByTitle('Resolve conversation')
      expect(resolveBtn).toBeInTheDocument()
    })
  })

  it('resolves a thread on click', async () => {
    const comments = [makeInline({ id: '101', user: 'alice', body: 'Fix this', threadId: 'thread-1', isResolved: false })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    const resolveBtn = screen.getByTitle('Resolve conversation')
    fireEvent.click(resolveBtn)

    await waitFor(() => {
      expect(mockResolve).toHaveBeenCalledWith('thread-1', true, 'org/repo')
    })
  })

  it('shows collapsed resolved thread with "Show resolved" button', async () => {
    const comments = [makeInline({ id: '101', user: 'alice', body: 'Already resolved issue', threadId: 'thread-1', isResolved: true })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    await waitFor(() => {
      expect(screen.getByText('Show resolved')).toBeInTheDocument()
    })
  })

  it('expands resolved thread on "Show resolved" click', async () => {
    const comments = [makeInline({ id: '101', user: 'alice', body: 'Resolved content', threadId: 'thread-1', isResolved: true })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    fireEvent.click(screen.getByText('Show resolved'))

    await waitFor(() => {
      expect(screen.getByText('Resolved content')).toBeInTheDocument()
      expect(screen.getByText('Hide resolved')).toBeInTheDocument()
    })
  })

  it('hides expanded resolved thread on "Hide resolved" click', async () => {
    const comments = [makeInline({ id: '101', user: 'alice', body: 'Resolved content', threadId: 'thread-1', isResolved: true })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    // Expand
    fireEvent.click(screen.getByText('Show resolved'))
    await waitFor(() => { expect(screen.getByText('Hide resolved')).toBeInTheDocument() })

    // Collapse
    fireEvent.click(screen.getByText('Hide resolved'))
    await waitFor(() => { expect(screen.getByText('Show resolved')).toBeInTheDocument() })
  })

  it('unresolves a thread', async () => {
    const comments = [makeInline({ id: '101', user: 'alice', body: 'Resolved', threadId: 'thread-1', isResolved: true })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    // Expand resolved thread first
    fireEvent.click(screen.getByText('Show resolved'))
    await waitFor(() => { expect(screen.getByText('Unresolve')).toBeInTheDocument() })

    fireEvent.click(screen.getByText('Unresolve'))
    await waitFor(() => {
      expect(mockResolve).toHaveBeenCalledWith('thread-1', false, 'org/repo')
    })
  })

  it('shows "Ask Agent" button when onAskAgent is provided', () => {
    const onAskAgent = vi.fn()
    const comments = [makeInline({ id: '101', user: 'alice', body: 'Question' })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" onAskAgent={onAskAgent} />)

    expect(screen.getByText('Draft')).toBeInTheDocument()
  })

  it('calls onAskAgent with thread URL', () => {
    const onAskAgent = vi.fn()
    const comments = [makeInline({ id: '101', user: 'alice', body: 'Question' })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" onAskAgent={onAskAgent} />)

    fireEvent.click(screen.getByText('Draft'))
    expect(onAskAgent).toHaveBeenCalledWith('https://github.com/org/repo/pull/42#discussion_r101')
  })

  it('shows quote reply in action menu', async () => {
    const comments = [makeInline({ id: '101', user: 'alice', body: 'Some feedback' })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    fireEvent.click(screen.getByTitle('Actions'))
    await waitFor(() => {
      expect(screen.getByText('Quote reply')).toBeInTheDocument()
    })
  })

  it('does not show Edit for non-own comments', async () => {
    const comments = [makeInline({ id: '101', user: 'other-user', body: 'Not mine' })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    fireEvent.click(screen.getByTitle('Actions'))
    await waitFor(() => {
      expect(screen.getByText('Quote reply')).toBeInTheDocument()
      expect(screen.queryByText('Edit')).toBeNull()
    })
  })

  it('handles reply failure with alert', async () => {
    mockReply.mockRejectedValueOnce(new Error('Server error'))
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})

    const comments = [makeInline({ id: '101', user: 'alice', body: 'Fix this' })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    fireEvent.click(screen.getByText('Reply...'))
    const textarea = screen.getByPlaceholderText('Write a reply...')
    fireEvent.change(textarea, { target: { value: 'My reply' } })
    fireEvent.click(screen.getByText('Reply'))

    await waitFor(() => {
      expect(alertSpy).toHaveBeenCalledWith(expect.stringContaining('Server error'))
    })

    alertSpy.mockRestore()
  })

  it('opens quote reply and pre-fills reply editor', async () => {
    const comments = [makeInline({ id: '101', user: 'alice', body: 'Some feedback' })]
    render(<InlineComments inlineComments={comments} {...defaultProps} myLogins={['me']} myLogin="me" />)

    // Open action menu
    fireEvent.click(screen.getByTitle('Actions'))
    await waitFor(() => {
      expect(screen.getByText('Quote reply')).toBeInTheDocument()
    })

    // Click quote reply -- this should open the reply editor
    fireEvent.click(screen.getByText('Quote reply'))

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Write a reply...')).toBeInTheDocument()
    })
  })

  it('edits a reply comment (not top-level)', async () => {
    const top = makeInline({ id: '101', user: 'alice', body: 'Top comment' })
    const reply = makeInline({ id: '102', user: 'me', body: 'My reply', inReplyToId: '101' })
    render(<InlineComments inlineComments={[top, reply]} {...defaultProps} myLogins={['me']} myLogin="me" />)

    // There should be two action menus -- one for top, one for reply
    const menus = screen.getAllByTitle('Actions')
    expect(menus.length).toBe(2)

    // Click the reply's action menu (second one)
    fireEvent.click(menus[1])
    await waitFor(() => {
      expect(screen.getByText('Edit')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Edit'))

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Edit reply...')).toBeInTheDocument()
    })

    const textarea = screen.getByPlaceholderText('Edit reply...')
    fireEvent.change(textarea, { target: { value: 'Edited reply' } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      expect(mockEdit).toHaveBeenCalledWith('org/repo', 102, 'Edited reply', true)
    })
  })

  it('shows reply count in collapsed resolved thread', async () => {
    const comments = [
      makeInline({ id: '101', user: 'alice', body: 'Top', threadId: 'thread-1', isResolved: true }),
      makeInline({ id: 'r1', user: 'bob', body: 'Reply 1', inReplyToId: '101' }),
      makeInline({ id: 'r2', user: 'carol', body: 'Reply 2', inReplyToId: '101' }),
    ]
    render(<InlineComments inlineComments={comments} {...defaultProps} />)

    await waitFor(() => {
      expect(screen.getByText(/2 replies/)).toBeInTheDocument()
    })
  })
})

describe('GeneralComments - interactions', () => {
  it('opens edit mode for own general comment', async () => {
    const comments = [
      { id: 1, author: { login: 'me' }, body: 'My general comment', createdAt: new Date().toISOString() },
    ]
    render(<GeneralComments comments={comments} {...defaultProps} myLogins={['me']} />)

    fireEvent.click(screen.getByTitle('Actions'))
    await waitFor(() => { fireEvent.click(screen.getByText('Edit')) })

    expect(screen.getByPlaceholderText('Edit comment...')).toBeInTheDocument()
  })

  it('submits edit for general comment', async () => {
    const comments = [
      { id: 1, author: { login: 'me' }, body: 'Original comment', createdAt: new Date().toISOString() },
    ]
    render(<GeneralComments comments={comments} {...defaultProps} myLogins={['me']} />)

    fireEvent.click(screen.getByTitle('Actions'))
    await waitFor(() => { fireEvent.click(screen.getByText('Edit')) })

    const textarea = screen.getByPlaceholderText('Edit comment...')
    fireEvent.change(textarea, { target: { value: 'Edited comment' } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      expect(mockEdit).toHaveBeenCalledWith('org/repo', 1, 'Edited comment', false)
    })
  })

  it('cancels edit for general comment', async () => {
    const comments = [
      { id: 1, author: { login: 'me' }, body: 'My comment', createdAt: new Date().toISOString() },
    ]
    render(<GeneralComments comments={comments} {...defaultProps} myLogins={['me']} />)

    // Open edit
    fireEvent.click(screen.getByTitle('Actions'))
    await waitFor(() => { fireEvent.click(screen.getByText('Edit')) })
    expect(screen.getByPlaceholderText('Edit comment...')).toBeInTheDocument()

    // Cancel
    fireEvent.click(screen.getByText('Cancel'))
    await waitFor(() => {
      expect(screen.queryByPlaceholderText('Edit comment...')).toBeNull()
      // Original comment should still be visible
      expect(screen.getByText('My comment')).toBeInTheDocument()
    })
  })

  it('triggers quote reply for general comment', async () => {
    // Provide a comment-input textarea in the DOM for the quote reply to target
    render(
      <div>
        <GeneralComments
          comments={[{ id: 1, author: { login: 'alice' }, body: 'Interesting point', createdAt: new Date().toISOString() }]}
          {...defaultProps}
          myLogins={['me']}
        />
        <textarea data-testid="comment-input" />
      </div>,
    )

    // Open action menu and click Quote reply
    fireEvent.click(screen.getByTitle('Actions'))
    await waitFor(() => {
      expect(screen.getByText('Quote reply')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Quote reply'))

    // The textarea should receive the quoted text
    await waitFor(() => {
      const textarea = screen.getByTestId('comment-input') as HTMLTextAreaElement
      // The handleQuoteReply sets value via nativeInputValueSetter
      expect(textarea.value).toContain('> Interesting point')
    })
  })

  it('handles edit failure with alert', async () => {
    mockEdit.mockRejectedValueOnce(new Error('Network error'))
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})

    const comments = [
      { id: 1, author: { login: 'me' }, body: 'My comment', createdAt: new Date().toISOString() },
    ]
    render(<GeneralComments comments={comments} {...defaultProps} myLogins={['me']} />)

    fireEvent.click(screen.getByTitle('Actions'))
    await waitFor(() => { fireEvent.click(screen.getByText('Edit')) })

    const textarea = screen.getByPlaceholderText('Edit comment...')
    fireEvent.change(textarea, { target: { value: 'Updated' } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      expect(alertSpy).toHaveBeenCalledWith(expect.stringContaining('Network error'))
    })

    alertSpy.mockRestore()
  })

  it('handles undefined comments array', () => {
    render(<GeneralComments comments={undefined as unknown as PRDetail['comments']} {...defaultProps} />)
    expect(screen.getByText('Comments (0)')).toBeInTheDocument()
  })
})
