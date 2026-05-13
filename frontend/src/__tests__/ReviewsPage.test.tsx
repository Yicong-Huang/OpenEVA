import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

// The page only needs api.getReviewRequests and PRCard (render).
vi.mock('../api', () => ({
  api: {
    getReviewRequests: vi.fn(),
    addReviewWatch: vi.fn(),
    removeReviewWatch: vi.fn(),
    syncReviewRequests: vi.fn().mockResolvedValue({ status: 'sync started' }),
    markReviewSeen: vi.fn().mockResolvedValue({}),
    waitReady: vi.fn(),
    sendTerminalInput: vi.fn(),
    // ReviewCard pulls review-context actions on mount via this. The
    // ReviewsPage tests don't drive review actions through the middle
    // pane (they go through PRDetail), so an empty list is enough.
    getActions: vi.fn().mockResolvedValue({ actions: [] }),
  },
}))
// ReviewsPage subscribes to `github.review.updated` to refetch after a
// sync completes. Tests drive the sync explicitly so we only need to
// install a no-op subscription; individual tests that care can grab
// the handler via this captured array.
const eventBusHandlers: Array<{ pattern: string; handler: (e: Record<string, unknown>) => void }> = []
vi.mock('../hooks/useEventBus', () => ({
  useEventBus: (pattern: string, handler: (e: Record<string, unknown>) => void) => {
    eventBusHandlers.push({ pattern, handler })
  },
}))
// Stub PRNode (left-pane chip strip) + PRCard (right-pane detail)
// to simple placeholders so we can assert what the page wires up
// without pulling in their full dependency trees. ReviewNode renders
// the PRNode mock for each row.
vi.mock('../components/PRNode', () => ({
  PRNode: ({ pr, onClick, showMeta }: {
    pr: { number: number; title: string; repo: string }
    onClick?: () => void
    showMeta?: boolean
  }) => (
    <div data-testid={`review-pr-${pr.repo}-${pr.number}`}
         data-show-meta={String(showMeta)}
         onClick={onClick}>
      {pr.title}
    </div>
  ),
  CIRing: () => null,
  ReviewIcon: () => null,
  MyReviewPill: () => null,
}))
// PRCard (right pane). Review-mode launch lives inside it via
// useSessionLauncher; ReviewsPage doesn't pass onOpenAction.
vi.mock('../components/PRCard', () => ({
  PRCard: ({ repo, number }: { repo: string; number: number }) => (
    <div data-testid="pr-detail">detail:{repo}#{number}</div>
  ),
}))

import { api } from '../api'
import { ReviewsPage } from '../pages/ReviewsPage'

beforeEach(() => {
  vi.clearAllMocks()
  eventBusHandlers.length = 0
})

const MOCK_PR = (overrides = {}) => ({
  number: 1, title: 'pr', url: 'https://github.com/a/b/pull/1',
  status: 'open', ci_status: '', review_status: '', comment_count: 0,
  additions: 0, deletions: 0, author: '', head_branch: '', base_branch: '',
  last_updated: '2026-04-22T00:00:00Z', repo: 'a/b',
  ...overrides,
})

describe('ReviewsPage', () => {
  it('fetches /api/review-requests and renders a grouped list', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [
        MOCK_PR({ number: 111, title: 'Repo PR', repo: 'example/repo' }),
        MOCK_PR({ number: 222, title: 'Runtime PR', repo: 'myorg/svc' }),
        MOCK_PR({ number: 112, title: 'Another Repo', repo: 'example/repo' }),
      ],
    })
    render(<ReviewsPage />)
    await waitFor(() => {
      expect(screen.getByTestId('review-pr-example/repo-111')).toBeInTheDocument()
      expect(screen.getByTestId('review-pr-example/repo-112')).toBeInTheDocument()
      expect(screen.getByTestId('review-pr-myorg/svc-222')).toBeInTheDocument()
    })
    // Repo headers present (alphabetical for stability).
    expect(screen.getByText('example/repo')).toBeInTheDocument()
    expect(screen.getByText('myorg/svc')).toBeInTheDocument()
    // Count pill reflects visible-PR count (merged-and-already-reviewed
    // PRs would be filtered out, but none of these mocks are merged).
    expect(screen.getByText('3 shown')).toBeInTheDocument()
  })

  it('shows empty-state when backend returns no PRs', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({ prs: [] })
    render(<ReviewsPage />)
    await waitFor(() => {
      expect(screen.getByText('Nothing to review right now.')).toBeInTheDocument()
    })
  })

  it('hides merged PRs that I already approved or commented on, and surfaces the count', async () => {
    // 4 PRs: 1 open, 1 merged-but-untouched (folded), 1 merged+approved
    // (hidden), 1 merged+commented (hidden). Header should read
    // "2 shown (2 hidden)" -- the open one + the folded one.
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [
        MOCK_PR({ number: 1, title: 'open one', repo: 'a/b', status: 'open' }),
        MOCK_PR({ number: 2, title: 'merged untouched', repo: 'a/b', status: 'merged' }),
        MOCK_PR({ number: 3, title: 'merged i approved', repo: 'a/b', status: 'merged', my_review_state: 'approved' }),
        MOCK_PR({ number: 4, title: 'merged i commented', repo: 'a/b', status: 'merged', my_review_state: 'commented' }),
      ],
    })
    render(<ReviewsPage />)
    await waitFor(() => {
      expect(screen.getByText('open one')).toBeInTheDocument()
    })
    // Hidden: my_review_state in (approved, commented) on merged PRs.
    expect(screen.queryByText('merged i approved')).not.toBeInTheDocument()
    expect(screen.queryByText('merged i commented')).not.toBeInTheDocument()
    // Folded but visible: untouched merged PR.
    expect(screen.getByText('merged untouched')).toBeInTheDocument()
    // Header counts.
    expect(screen.getByText('2 shown')).toBeInTheDocument()
    expect(screen.getByText('(2 hidden)')).toBeInTheDocument()
  })

  it('flags merged-but-visible PRs as folded so the row can dim itself', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [
        MOCK_PR({ number: 1, title: 'open', repo: 'a/b', status: 'open' }),
        MOCK_PR({ number: 2, title: 'merged needs me', repo: 'a/b', status: 'merged', my_review_state: 'pending_review' }),
      ],
    })
    render(<ReviewsPage />)
    await waitFor(() => {
      expect(screen.getByText('merged needs me')).toBeInTheDocument()
    })
    // Both rows present, but only the merged one is folded.
    const rows = screen.getAllByTestId('review-row')
    const folded = rows.filter((r) => r.getAttribute('data-folded') === 'true')
    const open = rows.filter((r) => !r.getAttribute('data-folded'))
    expect(folded).toHaveLength(1)
    expect(open).toHaveLength(1)
    expect(folded[0].textContent).toContain('merged needs me')
  })

  it('shows error message when backend call fails', async () => {
    vi.mocked(api.getReviewRequests).mockRejectedValue(new Error('gh down'))
    render(<ReviewsPage />)
    await waitFor(() => {
      expect(screen.getByText(/Failed to load: gh down/)).toBeInTheDocument()
    })
  })

  it('Refresh button calls the async sync endpoint (not the list)', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [MOCK_PR({ number: 1, title: 'x' })],
    })
    render(<ReviewsPage />)
    await waitFor(() => screen.getByTestId('review-pr-a/b-1'))
    expect(api.getReviewRequests).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByText('Refresh'))
    await waitFor(() => {
      // Refresh kicks off a server-side sync; it does NOT re-GET the
      // list directly. The `github.review.updated` event does that.
      expect(api.syncReviewRequests).toHaveBeenCalledTimes(1)
    })
    expect(api.getReviewRequests).toHaveBeenCalledTimes(1)
  })

  it('`github.review.updated` event triggers a refetch of the queue', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [MOCK_PR({ number: 1, title: 'x' })],
    })
    render(<ReviewsPage />)
    await waitFor(() => screen.getByTestId('review-pr-a/b-1'))
    expect(api.getReviewRequests).toHaveBeenCalledTimes(1)
    const handler = eventBusHandlers.find(h => h.pattern === 'github.review.updated')
    expect(handler).toBeTruthy()
    await (async () => {
      handler!.handler({})
      await waitFor(() => {
        expect(api.getReviewRequests).toHaveBeenCalledTimes(2)
      })
    })()
  })

  it('clicking a PRCard opens PRDetail in a right-side pane (no new tab)', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [MOCK_PR({ number: 99, title: 't', url: 'https://gh.com/x/y/pull/99', repo: 'x/y' })],
    })
    render(<ReviewsPage />)
    await waitFor(() => screen.getByTestId('review-pr-x/y-99'))
    fireEvent.click(screen.getByTestId('review-pr-x/y-99'))
    await waitFor(() => {
      // The detail pane renders PRDetail with this repo/number.
      expect(screen.getByTestId('reviews-detail-pane')).toBeInTheDocument()
      expect(screen.getByTestId('pr-detail').textContent).toBe('detail:x/y#99')
    })
    // No new tab opened.
    expect(openSpy).not.toHaveBeenCalled()
    openSpy.mockRestore()
  })

  it('Close button on the detail pane hides it again', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [MOCK_PR({ number: 1, title: 't', repo: 'a/b' })],
    })
    render(<ReviewsPage />)
    await waitFor(() => screen.getByTestId('review-pr-a/b-1'))
    fireEvent.click(screen.getByTestId('review-pr-a/b-1'))
    await waitFor(() => screen.getByTestId('reviews-detail-pane'))
    fireEvent.click(screen.getByText('Close'))
    await waitFor(() => {
      expect(screen.queryByTestId('reviews-detail-pane')).not.toBeInTheDocument()
    })
  })

  it('"+ Add PR" button prompts for a URL and pins it', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({ prs: [] })
    vi.mocked(api.addReviewWatch).mockResolvedValue({
      url: 'https://github.com/a/b/pull/1',
      repo: 'a/b', number: 1, title: 't', added_at: '2026-04-23T00:00:00Z',
    })
    const origPrompt = window.prompt
    window.prompt = vi.fn().mockReturnValue('https://github.com/a/b/pull/1')
    try {
      render(<ReviewsPage />)
      await waitFor(() => screen.getByText('+ Add PR'))
      fireEvent.click(screen.getByText('+ Add PR'))
      await waitFor(() => {
        expect(api.addReviewWatch).toHaveBeenCalledWith(
          'https://github.com/a/b/pull/1',
        )
      })
      // Triggers a refetch so the new entry shows up.
      expect(api.getReviewRequests).toHaveBeenCalledTimes(2)
    } finally {
      window.prompt = origPrompt
    }
  })

  it('Add is a no-op if the user cancels the prompt', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({ prs: [] })
    const origPrompt = window.prompt
    window.prompt = vi.fn().mockReturnValue(null)
    try {
      render(<ReviewsPage />)
      await waitFor(() => screen.getByText('+ Add PR'))
      fireEvent.click(screen.getByText('+ Add PR'))
      // Give any async handlers a tick to fire.
      await new Promise(r => setTimeout(r, 10))
      expect(api.addReviewWatch).not.toHaveBeenCalled()
    } finally {
      window.prompt = origPrompt
    }
  })

  it('Unpin button only renders for manual/both PRs and calls removeReviewWatch', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [
        MOCK_PR({ number: 10, title: 'gh only', repo: 'a/b', source: 'github' }),
        MOCK_PR({ number: 11, title: 'manual only', repo: 'a/b', source: 'manual',
                  url: 'https://github.com/a/b/pull/11' }),
        MOCK_PR({ number: 12, title: 'both', repo: 'a/b', source: 'both',
                  url: 'https://github.com/a/b/pull/12' }),
      ],
    })
    vi.mocked(api.removeReviewWatch).mockResolvedValue({
      removed: true, url: 'https://github.com/a/b/pull/11',
    })
    const origConfirm = window.confirm
    window.confirm = vi.fn().mockReturnValue(true)
    try {
      render(<ReviewsPage />)
      await waitFor(() => screen.getByTestId('review-pr-a/b-10'))
      const unpinBtns = screen.getAllByText('Unpin')
      // Two manual/both entries -> two Unpin buttons. github-only has none.
      expect(unpinBtns).toHaveLength(2)
      fireEvent.click(unpinBtns[0])
      await waitFor(() => {
        expect(api.removeReviewWatch).toHaveBeenCalledWith(
          'https://github.com/a/b/pull/11',
        )
      })
    } finally {
      window.confirm = origConfirm
    }
  })

  it('sync failure surfaces an error alert and clears the spinner', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({ prs: [] })
    vi.mocked(api.syncReviewRequests).mockRejectedValue(
      new Error('gh CLI blew up'),
    )
    const origAlert = window.alert
    const alertSpy = vi.fn()
    window.alert = alertSpy
    try {
      render(<ReviewsPage />)
      await waitFor(() => screen.getByText('Refresh'))
      fireEvent.click(screen.getByText('Refresh'))
      await waitFor(() => expect(alertSpy).toHaveBeenCalled())
      const callArg = alertSpy.mock.calls[0][0] as string
      expect(callArg).toContain('Sync failed')
      expect(callArg).toContain('gh CLI blew up')
    } finally {
      window.alert = origAlert
    }
  })

  it('Add PR failure surfaces an error alert', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({ prs: [] })
    vi.mocked(api.addReviewWatch).mockRejectedValue(
      new Error('not a valid PR url'),
    )
    const origPrompt = window.prompt
    const origAlert = window.alert
    window.prompt = vi.fn().mockReturnValue('https://github.com/a/b/pull/1')
    const alertSpy = vi.fn()
    window.alert = alertSpy
    try {
      render(<ReviewsPage />)
      await waitFor(() => screen.getByText('+ Add PR'))
      fireEvent.click(screen.getByText('+ Add PR'))
      await waitFor(() => expect(alertSpy).toHaveBeenCalled())
      const msg = alertSpy.mock.calls[0][0] as string
      expect(msg).toContain('Could not add PR')
      expect(msg).toContain('not a valid PR url')
    } finally {
      window.prompt = origPrompt
      window.alert = origAlert
    }
  })

  it('Remove is a no-op when the user cancels the confirm dialog', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [MOCK_PR({ number: 11, repo: 'a/b', source: 'manual',
                      url: 'https://github.com/a/b/pull/11' })],
    })
    const origConfirm = window.confirm
    window.confirm = vi.fn().mockReturnValue(false)
    try {
      render(<ReviewsPage />)
      await waitFor(() => screen.getByText('Unpin'))
      fireEvent.click(screen.getByText('Unpin'))
      // Give any async handlers a tick to fire.
      await new Promise(r => setTimeout(r, 20))
      expect(api.removeReviewWatch).not.toHaveBeenCalled()
    } finally {
      window.confirm = origConfirm
    }
  })

  it('Remove failure surfaces an error alert (backend throws)', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [MOCK_PR({ number: 11, repo: 'a/b', source: 'manual',
                      url: 'https://github.com/a/b/pull/11' })],
    })
    vi.mocked(api.removeReviewWatch).mockRejectedValue(
      new Error('PR not found'),
    )
    const origConfirm = window.confirm
    const origAlert = window.alert
    window.confirm = vi.fn().mockReturnValue(true)
    const alertSpy = vi.fn()
    window.alert = alertSpy
    try {
      render(<ReviewsPage />)
      await waitFor(() => screen.getByText('Unpin'))
      fireEvent.click(screen.getByText('Unpin'))
      await waitFor(() => expect(alertSpy).toHaveBeenCalled())
      const msg = alertSpy.mock.calls[0][0] as string
      expect(msg).toContain('Remove failed')
      expect(msg).toContain('PR not found')
    } finally {
      window.confirm = origConfirm
      window.alert = origAlert
    }
  })

  it('URL-param fallback: selected PR rendering when queue is empty / mismatched', async () => {
    // selectedReviewUrl points to a PR that isn't in the queue result
    // (race between URL load and fetch). The page must still parse
    // owner/repo/num out of the URL so PRDetail can render immediately.
    // Covers the regex-fallback branch lines 49-51.
    vi.mocked(api.getReviewRequests).mockResolvedValue({ prs: [] })
    render(
      <ReviewsPage
        selectedReviewUrl="https://github.com/myorg/myrepo/pull/314"
        onSelectReview={vi.fn()}
      />,
    )
    await waitFor(() => {
      expect(screen.getByTestId('pr-detail').textContent)
        .toBe('detail:myorg/myrepo#314')
    })
  })

  it('URL-param fallback: unparseable URL renders no detail pane', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({ prs: [] })
    render(
      <ReviewsPage
        selectedReviewUrl="https://not-a-github-url.com/some/path"
        onSelectReview={vi.fn()}
      />,
    )
    // Give any pending fetches a tick to resolve so the empty-queue
    // state stabilises.
    await new Promise(r => setTimeout(r, 10))
    expect(screen.queryByTestId('pr-detail')).toBeNull()
  })

  it('removing the currently-selected PR clears the right-pane selection', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [MOCK_PR({ number: 11, repo: 'a/b', source: 'manual',
                      url: 'https://github.com/a/b/pull/11' })],
    })
    vi.mocked(api.removeReviewWatch).mockResolvedValue({
      removed: true, url: 'https://github.com/a/b/pull/11',
    })
    const onSelectReview = vi.fn()
    const origConfirm = window.confirm
    window.confirm = vi.fn().mockReturnValue(true)
    try {
      render(
        <ReviewsPage
          selectedReviewUrl="https://github.com/a/b/pull/11"
          onSelectReview={onSelectReview}
        />,
      )
      await waitFor(() => screen.getByText('Unpin'))
      // Detail pane is visible before removal
      expect(screen.getByTestId('pr-detail')).toBeInTheDocument()
      fireEvent.click(screen.getByText('Unpin'))
      await waitFor(() => {
        // selection is cleared (lifted-state controller is told to drop the url)
        expect(onSelectReview).toHaveBeenCalledWith(null)
      })
    } finally {
      window.confirm = origConfirm
    }
  })

  it('Unpin button is a flex sibling, not absolute -- prevents overlap at narrow column widths', async () => {
    // Regression: 3-column mode collapses the queue column to 25%
    // width. The Unpin button used to be position:absolute on top of
    // PRCard and overlapped the title text; now it's a flex sibling
    // so PRCard ellipsifies cleanly while Unpin keeps its full width.
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [
        MOCK_PR({
          number: 1, title: 'manually pinned',
          repo: 'a/b', source: 'manual',
        }),
      ],
    })
    render(<ReviewsPage />)
    const row = await screen.findByTestId('review-row-pinned')
    const unpin = await screen.findByTestId('review-unpin-btn')
    expect(row).toBeInTheDocument()
    expect(unpin).toBeInTheDocument()
    // Wrapper must be a flex container so PRCard + Unpin sit side-by-side.
    expect(row.style.display).toBe('flex')
    // Unpin must NOT be absolutely positioned -- that's the bug.
    expect(unpin.style.position).not.toBe('absolute')
    // Unpin's flexShrink prevents it from being squeezed to 0 width.
    expect(unpin.style.flexShrink).toBe('0')
  })

  it('queue rows drop pr-meta in 3-column mode so Unpin doesn\'t look stranded above a wrapped meta block', async () => {
    // Regression: at narrow queue widths (3-col mode collapses the
    // queue to ~25% of viewport), pr-meta (`flex-wrap: wrap`) wraps
    // onto multiple lines and the row balloons to ~120-140px tall.
    // The Unpin button sits at the top with `align-self: flex-start`
    // and `marginTop: 6`, so visually it floats above an extended
    // meta block -- looks broken even though there's no DOM overlap.
    // Fix: drop pr-meta from queue rows when a PR is selected; the
    // detail pane already shows the metadata.
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [
        MOCK_PR({
          number: 7, title: 'narrow-queue case',
          repo: 'a/b', source: 'manual',
          additions: 100, deletions: 50, comment_count: 20,
          head_branch: 'long-branch-name-that-would-wrap',
        }),
      ],
    })
    render(<ReviewsPage />)
    // Initially (no selection): the row passes showMeta=true to PRCard
    // so the meta block (branch / +N -M / timeAgo) renders.
    const card = await screen.findByTestId('review-pr-a/b-7')
    expect(card.getAttribute('data-show-meta')).toBe('true')
    // Select the PR -> 3-column mode (queue collapses to ~25%). The
    // row should now pass showMeta=false so the meta is dropped --
    // otherwise pr-meta wraps onto multiple lines and the Unpin
    // button looks stranded above a tall meta block.
    fireEvent.click(card)
    await waitFor(() => {
      const c = screen.getByTestId('review-pr-a/b-7')
      expect(c.getAttribute('data-show-meta')).toBe('false')
    })
  })

  it('non-pinned rows render WITHOUT the Unpin button', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [
        // source = 'github' (or undefined) -> not manual.
        MOCK_PR({ number: 99, title: 'auto', repo: 'a/b', source: 'github' }),
      ],
    })
    render(<ReviewsPage />)
    await screen.findByTestId('review-row')
    expect(screen.queryByTestId('review-unpin-btn')).toBeNull()
  })

  // The review-mode launch flow used to live on ReviewsPage
  // (handleOpenAction); it's now self-contained inside PRDetail
  // (review mode -> useSessionLauncher) and ReviewCard. Tests for the
  // POST-and-deliver dance moved to those components' own test files.
  // ReviewsPage no longer wires `onOpenAction` for the review pane,
  // so the cross-component plumbing test is also dropped.

  it('layout ratios come from `ui.layout.reviews_col_ratios` setting', async () => {
    // The 3-pane layout used to hardcode 25/35/40 across all installs.
    // The user explicitly asked to make this tweakable -- now driven
    // by `ui.layout.reviews_col_ratios`. Verify a custom setting
    // flows into the rendered pane widths.
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [MOCK_PR({ number: 1, repo: 'a/b' })],
    })
    // Stub the settings fetch the page does on mount.
    const settingsMock = vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url)
      if (u.endsWith('/api/settings/ui.layout.reviews_col_ratios')) {
        return new Response(JSON.stringify({
          key: 'ui.layout.reviews_col_ratios',
          value: [50, 30, 20],
        }), { status: 200 })
      }
      return new Response('', { status: 404 })
    })
    vi.stubGlobal('fetch', settingsMock)
    try {
      render(<ReviewsPage />)
      await screen.findByTestId('review-pr-a/b-1')
      fireEvent.click(screen.getByTestId('review-pr-a/b-1'))
      await waitFor(() => screen.getByTestId('reviews-card-pane'))
      // Card pane width comes from the setting's middle value.
      const card = screen.getByTestId('reviews-card-pane')
      expect((card as HTMLElement).style.width).toBe('30%')
      const detail = screen.getByTestId('reviews-detail-pane')
      expect((detail as HTMLElement).style.width).toBe('20%')
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('falls back to 25/35/40 ratios when the settings fetch fails', async () => {
    vi.mocked(api.getReviewRequests).mockResolvedValue({
      prs: [MOCK_PR({ number: 2, repo: 'a/b' })],
    })
    // Settings endpoint returns 500 -- the page must NOT crash; just
    // use the default ratios.
    const errMock = vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url)
      if (u.includes('/api/settings/')) {
        return new Response('', { status: 500 })
      }
      return new Response('', { status: 404 })
    })
    vi.stubGlobal('fetch', errMock)
    try {
      render(<ReviewsPage />)
      await screen.findByTestId('review-pr-a/b-2')
      fireEvent.click(screen.getByTestId('review-pr-a/b-2'))
      await waitFor(() => screen.getByTestId('reviews-card-pane'))
      // Default 25/35/40 applies.
      expect((screen.getByTestId('reviews-card-pane') as HTMLElement).style.width)
        .toBe('35%')
      expect((screen.getByTestId('reviews-detail-pane') as HTMLElement).style.width)
        .toBe('40%')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
