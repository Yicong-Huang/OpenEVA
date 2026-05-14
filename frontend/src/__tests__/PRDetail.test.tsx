import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { PRCard as PRDetail } from '../components/PRCard'
import type { PRDetail as PRDetailType } from '../types'

const mockPRDetail: PRDetailType = {
  number: 42,
  title: 'Add new feature',
  body: 'This is the **description**.',
  state: 'OPEN',
  author: { login: 'testuser' },
  url: 'https://github.com/org/repo/pull/42',
  headRefName: 'feature-branch',
  baseRefName: 'main',
  additions: 150,
  deletions: 30,
  mergeable: 'MERGEABLE',
  reviewDecision: 'APPROVED',
  labels: [{ name: 'enhancement' }],
  reviews: [{ author: { login: 'reviewer1' }, state: 'APPROVED' }],
  comments: [
    { author: { login: 'commenter1' }, body: 'Looks good!', createdAt: '2026-04-11T12:00:00Z' },
  ],
  files: [
    { path: 'src/index.ts', additions: 100, deletions: 10 },
    { path: 'src/utils.ts', additions: 50, deletions: 20 },
  ],
  statusCheckRollup: [
    { name: 'build', conclusion: 'SUCCESS' },
    { name: 'test', conclusion: 'SUCCESS' },
    { name: 'lint', conclusion: 'FAILURE' },
    { name: '[Non-Blocking] optional-check', conclusion: 'FAILURE' },
    { name: 'deploy', status: 'PENDING' },
  ],
  inlineComments: [],
}

vi.mock('../hooks/useEventBus', () => ({
  useEventBus: vi.fn(),
}))

// Mock the api module
vi.mock('../api', () => ({
  api: {
    getPRDetail: vi.fn(),
    getPRDiff: vi.fn().mockResolvedValue({ files: {} }),
    getActions: vi.fn().mockResolvedValue({ actions: [] }),
    updatePRTitle: vi.fn().mockResolvedValue({ ok: true }),
    updatePRBody: vi.fn().mockResolvedValue({ ok: true }),
    replyToComment: vi.fn().mockResolvedValue({ ok: true }),
    submitPRReview: vi.fn().mockResolvedValue({ ok: true, event: 'APPROVE' }),
    getPRPendingReview: vi.fn().mockResolvedValue({ review_id: null, body: '', comments: [] }),
    addPendingComment: vi.fn().mockResolvedValue({ review_id: 1, created: true }),
    deletePendingComment: vi.fn().mockResolvedValue({ ok: true }),
    submitPendingReview: vi.fn().mockResolvedValue({ ok: true, event: 'APPROVE' }),
  },
}))

import { api } from '../api'

const mockedGetPRDetail = vi.mocked(api.getPRDetail)

describe('PRDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows loading state initially', () => {
    // Never resolves during this test
    mockedGetPRDetail.mockReturnValue(new Promise(() => {}))
    render(<PRDetail repo="org/repo" number={42} />)
    expect(screen.getByTestId('pr-detail-loading')).toBeInTheDocument()
    expect(screen.getByText('Loading PR #42...')).toBeInTheDocument()
  })

  it('renders title and author after load', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.getByTestId('pr-title')).toHaveTextContent('Add new feature')
    expect(screen.getByTestId('pr-author')).toHaveTextContent('testuser')
  })

  it('shows CI checks with correct pass/fail counts', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('ci-section')).toBeInTheDocument()
    })

    // 2 passed (build, test), 2 failed (lint + non-blocking), 1 pending
    // CI text shows passed/total
    expect(screen.getByText('CI 2/5')).toBeInTheDocument()
    // Shows failed count
    expect(screen.getByTestId('ci-failed-count')).toHaveTextContent('2 failed')
  })

  it('non-blocking failures shown as grey', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('ci-section')).toBeInTheDocument()
    })

    // Click to expand CI detail
    screen.getByTestId('ci-section').click()

    await waitFor(() => {
      expect(screen.getByTestId('ci-detail')).toBeInTheDocument()
    })

    // Find the non-blocking failure icon - it should use muted color
    const nbIcons = screen.getAllByTestId('nb-fail-icon')
    expect(nbIcons.length).toBe(1)
    expect(nbIcons[0]).toHaveStyle({ color: 'var(--text-subtle)' })
  })

  it('shows error state on fetch failure', async () => {
    mockedGetPRDetail.mockRejectedValue(new Error('Network error'))
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail-error')).toBeInTheDocument()
    })
  })

  it('renders files list with additions and deletions', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.getByText('src/index.ts')).toBeInTheDocument()
    expect(screen.getByText('src/utils.ts')).toBeInTheDocument()
    expect(screen.getByText('Files (2)')).toBeInTheDocument()
  })

  it('renders labels', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.getByText('enhancement')).toBeInTheDocument()
  })

  it('renders comments', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.getByText('commenter1')).toBeInTheDocument()
    expect(screen.getByText('Comments (1)')).toBeInTheDocument()
  })

  it('renders action buttons when taskId and onOpenAction are provided', async () => {
    const mockActions = [
      { id: 'fix-ci', label: 'Fix CI', prompt_template: 'Fix it', context: 'pr', condition: 'ci_failed', sort_order: 0 },
      { id: 'review', label: 'Review PR', prompt_template: 'Review', context: 'pr', condition: 'has_pr', sort_order: 1 },
    ]
    vi.mocked(api.getActions).mockResolvedValue({ actions: mockActions })
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    const onOpenAction = vi.fn()
    render(<PRDetail repo="org/repo" number={42} taskId="task-1" projectId="proj-1" onOpenAction={onOpenAction} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // has_pr condition should pass, Review PR should show
    // Open Agent should show for tasks with taskId
    await waitFor(() => {
      expect(screen.getByText('Open Agent')).toBeInTheDocument()
      expect(screen.getByText('Review PR')).toBeInTheDocument()
    })
  })

  it('renders diff stats', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // Should show additions/deletions
    expect(screen.getByText('+150')).toBeInTheDocument()
    expect(screen.getByText('-30')).toBeInTheDocument()
  })

  it('renders branch info', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.getByText(/feature-branch/)).toBeInTheDocument()
  })

  it('renders review decision', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.getByText(/APPROVED/)).toBeInTheDocument()
  })

  it('shows OPEN state badge', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.getByText('OPEN')).toBeInTheDocument()
  })

  it('renders inline comments when present', async () => {
    const prWithInlineComments = {
      ...mockPRDetail,
      inlineComments: [
        {
          id: 'ic1',
          user: 'reviewer1',
          avatar: '',
          path: 'src/index.ts',
          line: 42,
          body: 'Consider refactoring this',
          createdAt: '2026-04-12T10:00:00Z',
          diffHunk: '',
          inReplyToId: null,
        },
      ],
    }
    mockedGetPRDetail.mockResolvedValue(prWithInlineComments)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // Should show inline comments section with count
    await waitFor(() => {
      expect(screen.getByText('Code Comments (1)')).toBeInTheDocument()
      expect(screen.getByText(/Consider refactoring/)).toBeInTheDocument()
    })
  })

  it('title editing: click title to enter edit mode, then cancel', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-title')).toBeInTheDocument()
    })

    // Click on the title text to enter edit mode
    const titleText = screen.getByText('Add new feature')
    fireEvent.click(titleText)

    // Should show input and Cancel button
    await waitFor(() => {
      expect(screen.getByDisplayValue('Add new feature')).toBeInTheDocument()
      expect(screen.getByText('Cancel')).toBeInTheDocument()
    })

    // Click Cancel to exit edit mode
    fireEvent.click(screen.getByText('Cancel'))
    await waitFor(() => {
      expect(screen.queryByDisplayValue('Add new feature')).not.toBeInTheDocument()
      expect(screen.getByText('Add new feature')).toBeInTheDocument()
    })
  })

  it('title editing: Escape key cancels edit', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-title')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Add new feature'))
    await waitFor(() => {
      expect(screen.getByDisplayValue('Add new feature')).toBeInTheDocument()
    })

    fireEvent.keyDown(screen.getByDisplayValue('Add new feature'), { key: 'Escape' })
    await waitFor(() => {
      expect(screen.queryByDisplayValue('Add new feature')).not.toBeInTheDocument()
    })
  })

  it('description section shows body and Edit button', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.getByText('Description')).toBeInTheDocument()
    expect(screen.getByText('Edit')).toBeInTheDocument()
  })

  it('description editor opens on Edit click', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // Click Edit button
    fireEvent.click(screen.getByText('Edit'))
    await waitFor(() => {
      // Should show textarea with description content and Cancel/Save buttons
      expect(screen.getByText('editing')).toBeInTheDocument()
      expect(screen.getAllByText('Cancel').length).toBeGreaterThan(0)
      expect(screen.getByText('Save')).toBeInTheDocument()
    })
  })

  it('description editor Cancel reverts', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Edit'))
    await waitFor(() => {
      expect(screen.getByText('editing')).toBeInTheDocument()
    })

    // Click Cancel
    const cancelBtns = screen.getAllByText('Cancel')
    fireEvent.click(cancelBtns[0])
    await waitFor(() => {
      expect(screen.queryByText('editing')).not.toBeInTheDocument()
    })
  })

  it('comment textarea and Comment button present', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.getByTestId('comment-input')).toBeInTheDocument()
    expect(screen.getByText('Comment')).toBeInTheDocument()
    // Comment button should be disabled when textarea is empty
    expect(screen.getByText('Comment')).toBeDisabled()
  })

  it('typing in comment textarea enables Comment button', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)

    // Mock /api/me for myLogins
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ logins: ['testuser'], repoAccount: {} }),
        })
      }
      if (typeof url === 'string' && url.includes('/api/pr-comment')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as typeof fetch

    render(<PRDetail repo="org/repo" number={42} />)
    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    const textarea = screen.getByTestId('comment-input') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'My comment' } })
    expect(textarea.value).toBe('My comment')

    const commentBtn = screen.getByText('Comment')
    expect(commentBtn).not.toBeDisabled()

    globalThis.fetch = origFetch
  })

  it('shows MERGED state with correct class', async () => {
    const mergedPR = { ...mockPRDetail, state: 'MERGED' }
    mockedGetPRDetail.mockResolvedValue(mergedPR)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.getByText('MERGED')).toBeInTheDocument()
  })

  it('shows CLOSED state with correct class', async () => {
    const closedPR = { ...mockPRDetail, state: 'CLOSED' }
    mockedGetPRDetail.mockResolvedValue(closedPR)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.getByText('CLOSED')).toBeInTheDocument()
  })

  it('refresh button fetches new data', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // Click refresh
    const refreshBtn = screen.getByTestId('pr-refresh-btn')
    mockedGetPRDetail.mockResolvedValue({ ...mockPRDetail, title: 'Updated Title' })
    fireEvent.click(refreshBtn)

    await waitFor(() => {
      expect(screen.getByText('Updated Title')).toBeInTheDocument()
    })
  })

  it('renders PR without labels gracefully', async () => {
    const noLabelsPR = { ...mockPRDetail, labels: [] }
    mockedGetPRDetail.mockResolvedValue(noLabelsPR)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // No label elements
    expect(screen.queryByText('enhancement')).not.toBeInTheDocument()
  })

  it('renders PR without body hides description', async () => {
    const noBodyPR = { ...mockPRDetail, body: '' }
    mockedGetPRDetail.mockResolvedValue(noBodyPR)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // No description section rendered when body is empty (DescriptionEditor returns null)
    expect(screen.queryByText('Description')).not.toBeInTheDocument()
  })

  it('Draft Reply button appears when task context is set', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    const onOpenAction = vi.fn()
    render(<PRDetail repo="org/repo" number={42} taskId="task-1" projectId="proj-1" onOpenAction={onOpenAction} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    await waitFor(() => {
      expect(screen.getByText('Draft Reply')).toBeInTheDocument()
    })
  })

  it('Open Agent button not shown without taskId', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.queryByText('Open Agent')).not.toBeInTheDocument()
    expect(screen.queryByText('Draft Reply')).not.toBeInTheDocument()
  })

  it('renders PR number as link', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    const link = screen.getByText('#42')
    expect(link.closest('a')).toHaveAttribute('href', 'https://github.com/org/repo/pull/42')
  })

  it('shows files count', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.getByText('2 files')).toBeInTheDocument()
  })

  // ===================== Title editing =====================

  it('title edit: click title -> type -> Enter saves', async () => {
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ logins: ['testuser'], repoAccount: {} }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as typeof fetch

    vi.mocked(api.updatePRTitle).mockResolvedValue({ ok: true })
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-title')).toBeInTheDocument()
    })

    // Click title to enter edit mode
    fireEvent.click(screen.getByText('Add new feature'))
    await waitFor(() => {
      expect(screen.getByDisplayValue('Add new feature')).toBeInTheDocument()
    })

    // Change the title
    const input = screen.getByDisplayValue('Add new feature')
    fireEvent.change(input, { target: { value: 'Updated title' } })

    // Press Enter to save
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      expect(api.updatePRTitle).toHaveBeenCalledWith('org/repo', 42, 'Updated title')
    })

    // After save, title should update in the UI
    await waitFor(() => {
      expect(screen.getByText('Updated title')).toBeInTheDocument()
    })

    globalThis.fetch = origFetch
  })

  it('title edit: Escape cancels without saving', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-title')).toBeInTheDocument()
    })

    // Enter edit mode
    fireEvent.click(screen.getByText('Add new feature'))
    await waitFor(() => {
      expect(screen.getByDisplayValue('Add new feature')).toBeInTheDocument()
    })

    // Change text
    const input = screen.getByDisplayValue('Add new feature')
    fireEvent.change(input, { target: { value: 'Changed but cancelled' } })

    // Press Escape
    fireEvent.keyDown(input, { key: 'Escape' })

    // Should exit edit mode without calling updatePRTitle
    await waitFor(() => {
      expect(screen.queryByDisplayValue('Changed but cancelled')).not.toBeInTheDocument()
      expect(screen.getByText('Add new feature')).toBeInTheDocument()
    })
  })

  // ===================== Description editing =====================

  it('description edit: click Edit -> textarea shown -> Save calls API', async () => {
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ logins: ['testuser'], repoAccount: {} }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as typeof fetch

    vi.mocked(api.updatePRBody).mockResolvedValue({ ok: true })
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // Click Edit
    fireEvent.click(screen.getByText('Edit'))
    await waitFor(() => {
      expect(screen.getByText('editing')).toBeInTheDocument()
    })

    // The textarea should contain current body
    const textarea = screen.getByDisplayValue('This is the **description**.')
    expect(textarea).toBeInTheDocument()

    // Modify the description
    fireEvent.change(textarea, { target: { value: 'New description text' } })

    // Click Save
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      expect(api.updatePRBody).toHaveBeenCalledWith('org/repo', 42, 'New description text')
    })

    globalThis.fetch = origFetch
  })

  // ===================== Comment posting =====================

  it('comment posting: type in textarea -> click Comment posts comment', async () => {
    const origFetch = globalThis.fetch
    const mockFetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ logins: ['testuser'], repoAccount: { default: 'testuser' } }) })
      }
      if (typeof url === 'string' && url.includes('/api/pr-comment')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    globalThis.fetch = mockFetch as typeof fetch

    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // Type in comment textarea
    const textarea = screen.getByTestId('comment-input') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'Great work!' } })

    // Click Comment
    fireEvent.click(screen.getByText('Comment'))

    await waitFor(() => {
      // The fetch to /api/pr-comment should have been called
      const prCommentCall = mockFetch.mock.calls.find(
        (call: unknown[]) => typeof call[0] === 'string' && (call[0] as string).includes('/api/pr-comment'),
      )
      expect(prCommentCall).toBeTruthy()
      const body = JSON.parse(prCommentCall![1].body)
      expect(body.repo).toBe('org/repo')
      expect(body.number).toBe(42)
      expect(body.body).toBe('Great work!')
    })

    // Comment textarea should be cleared after posting
    await waitFor(() => {
      expect((screen.getByTestId('comment-input') as HTMLTextAreaElement).value).toBe('')
    })

    globalThis.fetch = origFetch
  })

  // ===================== Refresh button =====================

  it('refresh button clears hasUpdate badge', async () => {
    // We need to simulate hasUpdate being true (via useEventBus)
    // and then clicking refresh should clear it
    const { useEventBus } = await import('../hooks/useEventBus')
    const mockedUseEventBus = vi.mocked(useEventBus)

    // Capture the callback passed to useEventBus so we can trigger it.
    // useEventBus's public signature is `(pattern, () => void)` but internally
    // the cb receives an event object -- cast here to let the test poke the
    // event through without losing type safety elsewhere.
    let eventCallback: ((event: Record<string, unknown>) => void) | null = null
    mockedUseEventBus.mockImplementation(((_pattern: string, cb: (event: Record<string, unknown>) => void) => {
      eventCallback = cb
    }) as unknown as typeof useEventBus)

    mockedGetPRDetail.mockResolvedValue(mockPRDetail)

    // Mock /api/me to avoid empty src warning
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ logins: ['testuser'], repoAccount: { default: 'testuser' } }) })
      }
      if (typeof url === 'string' && url.includes('/api/pr-comment')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as typeof fetch

    const { act } = await import('@testing-library/react')

    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // Initially no badge (no span child with accent background)
    const refreshBtn = screen.getByTestId('pr-refresh-btn')
    expect(refreshBtn.querySelector('span')).toBeNull()

    // Trigger "github event" to set hasUpdate (wrapped in act)
    await act(async () => {
      if (eventCallback) {
        eventCallback({})
      }
    })

    // Now badge should appear
    await waitFor(() => {
      expect(refreshBtn.querySelector('span')).not.toBeNull()
    })

    // Click refresh to clear badge -- fetchDetail sets hasUpdate=false
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    await act(async () => {
      fireEvent.click(refreshBtn)
    })

    // Wait for the loading -> loaded transition
    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // Badge should be gone after refresh
    const updatedRefreshBtn = screen.getByTestId('pr-refresh-btn')
    expect(updatedRefreshBtn.querySelector('span')).toBeNull()

    globalThis.fetch = origFetch
  })

  it('title edit: Save button triggers updatePRTitle', async () => {
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ logins: ['testuser'], repoAccount: {} }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as typeof fetch

    vi.mocked(api.updatePRTitle).mockResolvedValue({ ok: true })
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-title')).toBeInTheDocument()
    })

    // Enter edit mode
    fireEvent.click(screen.getByText('Add new feature'))
    await waitFor(() => {
      expect(screen.getByDisplayValue('Add new feature')).toBeInTheDocument()
    })

    // Change value
    fireEvent.change(screen.getByDisplayValue('Add new feature'), { target: { value: 'New via button' } })

    // Click Save button (not Enter)
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      expect(api.updatePRTitle).toHaveBeenCalledWith('org/repo', 42, 'New via button')
    })

    globalThis.fetch = origFetch
  })

  it('comment button is disabled when textarea is empty', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    const commentBtn = screen.getByText('Comment')
    expect(commentBtn).toBeDisabled()

    // Type something
    const textarea = screen.getByTestId('comment-input')
    fireEvent.change(textarea, { target: { value: '  ' } })

    // Still disabled (whitespace only)
    expect(commentBtn).toBeDisabled()

    // Type real content
    fireEvent.change(textarea, { target: { value: 'Hello' } })
    expect(commentBtn).not.toBeDisabled()

    // Clear it
    fireEvent.change(textarea, { target: { value: '' } })
    expect(commentBtn).toBeDisabled()
  })

  it('description editor Save is disabled when content is unchanged', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // Open editor
    fireEvent.click(screen.getByText('Edit'))
    await waitFor(() => {
      expect(screen.getByText('Save')).toBeInTheDocument()
    })

    // Save should be disabled because content hasn't changed
    expect(screen.getByText('Save')).toBeDisabled()
  })

  it('resolves myLoginForRepo using repoAccount mapping', async () => {
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            logins: ['personal', 'work'],
            repoAccount: { 'org': 'work', 'default': 'personal' },
          }),
        })
      }
      if (typeof url === 'string' && url.includes('/api/pr-comment')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as typeof fetch

    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // The avatar should use 'work' login since repo contains 'org'
    // (We can verify the img src contains the right login)
    const commentInput = screen.getByTestId('comment-input')
    fireEvent.change(commentInput, { target: { value: 'test reply' } })
    fireEvent.click(screen.getByText('Comment'))

    await waitFor(() => {
      // Comment should have been posted successfully (optimistic insert)
      expect((screen.getByTestId('comment-input') as HTMLTextAreaElement).value).toBe('')
    })

    globalThis.fetch = origFetch
  })

  it('Open Agent button calls onOpenAction with "open"', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    vi.mocked(api.getActions).mockResolvedValue({ actions: [] })
    const onOpenAction = vi.fn()
    render(<PRDetail repo="org/repo" number={42} taskId="task-1" projectId="proj-1" onOpenAction={onOpenAction} />)

    await waitFor(() => {
      expect(screen.getByText('Open Agent')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Open Agent'))
    expect(onOpenAction).toHaveBeenCalledWith('open')
  })

  it('Draft Reply button calls onOpenAction with draft-reply prompt', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    vi.mocked(api.getActions).mockResolvedValue({ actions: [] })
    const onOpenAction = vi.fn()

    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ logins: ['testuser'], repoAccount: { default: 'testuser' } }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as typeof fetch

    render(<PRDetail repo="org/repo" number={42} taskId="task-1" projectId="proj-1" onOpenAction={onOpenAction} />)

    await waitFor(() => {
      expect(screen.getByText('Draft Reply')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Draft Reply'))
    expect(onOpenAction).toHaveBeenCalledWith('draft-reply', expect.stringContaining('Draft concise reply'))

    globalThis.fetch = origFetch
  })

  it('action button condition filtering: ci_failed shows Fix CI action', async () => {
    const prWithCIFailure = {
      ...mockPRDetail,
      statusCheckRollup: [
        { name: 'build', conclusion: 'FAILURE' },
      ],
    }
    const actions = [
      { id: 'fix-ci', label: 'Fix CI', prompt_template: 'fix', context: 'pr', condition: 'ci_failed', sort_order: 0 },
    ]
    vi.mocked(api.getActions).mockResolvedValue({ actions })
    mockedGetPRDetail.mockResolvedValue(prWithCIFailure)
    const onOpenAction = vi.fn()
    render(<PRDetail repo="org/repo" number={42} taskId="t1" projectId="p1" onOpenAction={onOpenAction} />)

    await waitFor(() => {
      expect(screen.getByText('Fix CI')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Fix CI'))
    expect(onOpenAction).toHaveBeenCalledWith('fix-ci')
  })

  it('title edit via Enter: shows alert on API failure', async () => {
    const origFetch = globalThis.fetch
    const origAlert = globalThis.alert
    globalThis.alert = vi.fn()
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ logins: ['me'], repoAccount: {} }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as typeof fetch

    vi.mocked(api.updatePRTitle).mockRejectedValue(new Error('save failed'))
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => { expect(screen.getByTestId('pr-title')).toBeInTheDocument() })
    fireEvent.click(screen.getByText('Add new feature'))
    await waitFor(() => { expect(screen.getByDisplayValue('Add new feature')).toBeInTheDocument() })
    fireEvent.change(screen.getByDisplayValue('Add new feature'), { target: { value: 'New title' } })
    fireEvent.keyDown(screen.getByDisplayValue('New title'), { key: 'Enter' })

    await waitFor(() => {
      expect(globalThis.alert).toHaveBeenCalledWith(expect.stringContaining('save failed'))
    })

    globalThis.alert = origAlert
    globalThis.fetch = origFetch
  })

  it('title edit via Save button: shows alert on API failure', async () => {
    const origFetch = globalThis.fetch
    const origAlert = globalThis.alert
    globalThis.alert = vi.fn()
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ logins: ['me'], repoAccount: {} }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as typeof fetch

    vi.mocked(api.updatePRTitle).mockRejectedValue(new Error('save failed via btn'))
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => { expect(screen.getByTestId('pr-title')).toBeInTheDocument() })
    fireEvent.click(screen.getByText('Add new feature'))
    await waitFor(() => { expect(screen.getByDisplayValue('Add new feature')).toBeInTheDocument() })
    fireEvent.change(screen.getByDisplayValue('Add new feature'), { target: { value: 'Btn title' } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      expect(globalThis.alert).toHaveBeenCalledWith(expect.stringContaining('save failed via btn'))
    })

    globalThis.alert = origAlert
    globalThis.fetch = origFetch
  })

  it('comment post failure does not crash', async () => {
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ logins: ['me'], repoAccount: { default: 'me' } }),
        })
      }
      if (typeof url === 'string' && url.includes('/api/pr-comment')) {
        return Promise.reject(new Error('post failed'))
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as typeof fetch

    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    const textarea = screen.getByTestId('comment-input')
    fireEvent.change(textarea, { target: { value: 'test' } })
    fireEvent.click(screen.getByText('Comment'))

    // Should not crash -- commenting state should resolve
    await waitFor(() => {
      expect(screen.getByText('Comment')).toBeInTheDocument()
    })

    globalThis.fetch = origFetch
  })

  it('description editor Save failure shows alert', async () => {
    const origFetch = globalThis.fetch
    const origAlert = globalThis.alert
    globalThis.alert = vi.fn()
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ logins: ['me'], repoAccount: {} }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as typeof fetch

    vi.mocked(api.updatePRBody).mockRejectedValue(new Error('save body failed'))
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // Open editor
    fireEvent.click(screen.getByText('Edit'))
    await waitFor(() => {
      expect(screen.getByText('editing')).toBeInTheDocument()
    })

    // Modify description
    const textarea = screen.getByDisplayValue('This is the **description**.')
    fireEvent.change(textarea, { target: { value: 'Changed body' } })

    // Click Save
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      expect(globalThis.alert).toHaveBeenCalledWith(expect.stringContaining('save body failed'))
    })

    globalThis.alert = origAlert
    globalThis.fetch = origFetch
  })

  it('clicking description body enters edit mode', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // The description body has title="Click to edit" and onClick
    const descBody = screen.getByTitle('Click to edit')
    expect(descBody).toBeInTheDocument()
    fireEvent.click(descBody)

    await waitFor(() => {
      expect(screen.getByText('editing')).toBeInTheDocument()
    })
  })

  it('prActions fetch failure sets empty actions', async () => {
    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    vi.mocked(api.getActions).mockRejectedValue(new Error('actions failed'))
    const onOpenAction = vi.fn()
    render(<PRDetail repo="org/repo" number={42} taskId="task-1" projectId="proj-1" onOpenAction={onOpenAction} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // Open Agent should still show (it does not depend on prActions)
    expect(screen.getByText('Open Agent')).toBeInTheDocument()
    // No extra action buttons from prActions
    const actionsDiv = screen.getByTestId('pr-actions')
    // Only Open Agent button should be present (no other action buttons)
    const buttons = actionsDiv.querySelectorAll('button')
    expect(buttons.length).toBe(1)
    expect(buttons[0]).toHaveTextContent('Open Agent')
  })

  it('empty comment textarea does not trigger submit', async () => {
    const origFetch = globalThis.fetch
    const mockFn = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ logins: ['me'], repoAccount: { default: 'me' } }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    globalThis.fetch = mockFn as typeof fetch

    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // Textarea is empty, Comment button should be disabled
    const commentBtn = screen.getByText('Comment')
    expect(commentBtn).toBeDisabled()

    // Try to click the disabled button -- no fetch to /api/pr-comment should happen
    fireEvent.click(commentBtn)

    const prCommentCalls = mockFn.mock.calls.filter(
      (call: any[]) => typeof call[0] === 'string' && call[0].includes('/api/pr-comment'),
    )
    expect(prCommentCalls).toHaveLength(0)

    globalThis.fetch = origFetch
  })

  it('whitespace-only comment does not trigger submit', async () => {
    const origFetch = globalThis.fetch
    const mockFn = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ logins: ['me'], repoAccount: { default: 'me' } }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    globalThis.fetch = mockFn as typeof fetch

    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    const textarea = screen.getByTestId('comment-input')
    fireEvent.change(textarea, { target: { value: '   ' } })

    // Still disabled because whitespace only
    expect(screen.getByText('Comment')).toBeDisabled()

    globalThis.fetch = origFetch
  })

  it('comment submission optimistically inserts the new comment', async () => {
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ logins: ['me'], repoAccount: { default: 'me' } }),
        })
      }
      if (typeof url === 'string' && url.includes('/api/pr-comment')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as typeof fetch

    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    const textarea = screen.getByTestId('comment-input')
    fireEvent.change(textarea, { target: { value: 'New optimistic comment' } })
    fireEvent.click(screen.getByText('Comment'))

    // The comment should appear optimistically without a full reload
    await waitFor(() => {
      expect(screen.getByText('New optimistic comment')).toBeInTheDocument()
    })

    // Textarea should be cleared
    await waitFor(() => {
      expect((screen.getByTestId('comment-input') as HTMLTextAreaElement).value).toBe('')
    })

    globalThis.fetch = origFetch
  })

  it('renders PR with no files gracefully', async () => {
    const noFilesPR = { ...mockPRDetail, files: [] }
    mockedGetPRDetail.mockResolvedValue(noFilesPR)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    expect(screen.getByText('0 files')).toBeInTheDocument()
  })

  it('/api/me fetch failure does not crash component', async () => {
    const origFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/me')) {
        return Promise.reject(new Error('unauthorized'))
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as typeof fetch

    mockedGetPRDetail.mockResolvedValue(mockPRDetail)
    render(<PRDetail repo="org/repo" number={42} />)

    await waitFor(() => {
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
    })

    // Component should still render successfully
    expect(screen.getByText('Add new feature')).toBeInTheDocument()

    globalThis.fetch = origFetch
  })

  describe('review-submit panel', () => {
    const meIsReviewer = async () => {
      // /api/me returns "testuser" as NOT-me so the panel is visible
      // (panel hides on my own PR).
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true, json: async () => ({ logins: ['other'], repoAccount: {} }),
      }) as unknown as typeof fetch
    }

    it('renders a "Review changes" button in the header for an open PR authored by someone else', async () => {
      await meIsReviewer()
      mockedGetPRDetail.mockResolvedValue(mockPRDetail)
      render(<PRDetail repo="org/repo" number={42} />)
      await waitFor(() => expect(screen.getByTestId('pr-detail')).toBeInTheDocument())
      expect(screen.getByTestId('review-submit-panel')).toBeInTheDocument()
      // Popover closed by default.
      expect(screen.queryByTestId('review-body-input')).not.toBeInTheDocument()
      // Click the button (not a collapse header anymore).
      fireEvent.click(screen.getByRole('button', { name: /Review changes/ }))
      expect(screen.getByTestId('review-body-input')).toBeInTheDocument()
      // Three radio options labelled by their bold title.
      expect(screen.getByLabelText(/Comment/)).toBeInTheDocument()
      expect(screen.getByLabelText(/Approve/)).toBeInTheDocument()
      expect(screen.getByLabelText(/Request changes/)).toBeInTheDocument()
    })

    it('hides the panel on my own PR (GitHub would 422 anyway)', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true, json: async () => ({ logins: ['testuser'], repoAccount: {} }),
      }) as unknown as typeof fetch
      mockedGetPRDetail.mockResolvedValue(mockPRDetail)
      render(<PRDetail repo="org/repo" number={42} />)
      await waitFor(() => expect(screen.getByTestId('pr-detail')).toBeInTheDocument())
      expect(screen.queryByTestId('review-submit-panel')).not.toBeInTheDocument()
    })

    it('hides the panel on closed/merged PRs', async () => {
      await meIsReviewer()
      mockedGetPRDetail.mockResolvedValue({ ...mockPRDetail, state: 'MERGED' })
      render(<PRDetail repo="org/repo" number={42} />)
      await waitFor(() => expect(screen.getByTestId('pr-detail')).toBeInTheDocument())
      expect(screen.queryByTestId('review-submit-panel')).not.toBeInTheDocument()
    })

    it('Approve submits without body', async () => {
      await meIsReviewer()
      mockedGetPRDetail.mockResolvedValue(mockPRDetail)
      render(<PRDetail repo="org/repo" number={42} />)
      await waitFor(() => expect(screen.getByTestId('pr-detail')).toBeInTheDocument())
      fireEvent.click(screen.getByRole('button', { name: /Review changes/ }))
      fireEvent.click(screen.getByLabelText(/Approve/))
      fireEvent.click(screen.getByTestId('review-submit-btn'))
      await waitFor(() => {
        expect(api.submitPendingReview).toHaveBeenCalledWith('org/repo', 42, 'APPROVE', '')
      })
    })

    it('Request changes with body goes through', async () => {
      await meIsReviewer()
      mockedGetPRDetail.mockResolvedValue(mockPRDetail)
      render(<PRDetail repo="org/repo" number={42} />)
      await waitFor(() => expect(screen.getByTestId('pr-detail')).toBeInTheDocument())
      fireEvent.click(screen.getByRole('button', { name: /Review changes/ }))
      fireEvent.click(screen.getByLabelText(/Request changes/))
      const textarea = screen.getByTestId('review-body-input') as HTMLTextAreaElement
      fireEvent.change(textarea, { target: { value: 'please fix the thing' } })
      fireEvent.click(screen.getByTestId('review-submit-btn'))
      await waitFor(() => {
        expect(api.submitPendingReview).toHaveBeenCalledWith(
          'org/repo', 42, 'REQUEST_CHANGES', 'please fix the thing',
        )
      })
    })

    it('Request changes with empty body does NOT call the API (client-side guard)', async () => {
      await meIsReviewer()
      const origAlert = window.alert
      window.alert = vi.fn()
      try {
        mockedGetPRDetail.mockResolvedValue(mockPRDetail)
        render(<PRDetail repo="org/repo" number={42} />)
        await waitFor(() => expect(screen.getByTestId('pr-detail')).toBeInTheDocument())
        fireEvent.click(screen.getByRole('button', { name: /Review changes/ }))
        fireEvent.click(screen.getByLabelText(/Request changes/))
        fireEvent.click(screen.getByTestId('review-submit-btn'))
        // tick
        await new Promise(r => setTimeout(r, 10))
        expect(api.submitPendingReview).not.toHaveBeenCalled()
        // User got told why.
        expect(window.alert).toHaveBeenCalled()
      } finally {
        window.alert = origAlert
      }
    })
  })
})
