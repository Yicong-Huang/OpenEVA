import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { PRNode as PRCard, CIRing, ReviewIcon, MyReviewPill } from '../components/PRNode'
import type { PR } from '../types'

vi.mock('../hooks/useLiveClock', () => ({ useLiveClock: vi.fn() }))

const basePR: PR = {
  number: 42,
  url: 'https://github.com/org/repo/pull/42',
  title: 'Fix the widget',
  status: 'open',
  ci_status: 'success',
  review_status: 'approved',
  comment_count: 3,
  additions: 50,
  deletions: 10,
  author: 'alice',
  head_branch: 'fix-widget',
  base_branch: 'main',
  last_updated: '2026-04-13T06:00:00Z',
}

describe('PRCard', () => {
  it('renders PR number and title', () => {
    render(<PRCard pr={basePR} />)
    expect(screen.getByText('#42')).toBeInTheDocument()
    expect(screen.getByText('Fix the widget')).toBeInTheDocument()
  })

  it('renders status label', () => {
    render(<PRCard pr={basePR} />)
    expect(screen.getByText('open')).toBeInTheDocument()
  })

  it('renders CI ring when ci_status is set', () => {
    render(<PRCard pr={basePR} />)
    expect(screen.getByTestId('ci-ring')).toBeInTheDocument()
  })

  it('renders review icon when review_status is set', () => {
    render(<PRCard pr={basePR} />)
    expect(screen.getByTestId('review-icon')).toBeInTheDocument()
  })

  it('shows diff stats when additions/deletions > 0', () => {
    render(<PRCard pr={basePR} />)
    expect(screen.getByTestId('diff-stats')).toBeInTheDocument()
    expect(screen.getByText('+50')).toBeInTheDocument()
    expect(screen.getByText('-10')).toBeInTheDocument()
  })

  it('shows comment count when > 0', () => {
    render(<PRCard pr={basePR} />)
    expect(screen.getByTestId('comment-count')).toBeInTheDocument()
    expect(screen.getByText('3 comments')).toBeInTheDocument()
  })

  it('hides meta when showMeta is false', () => {
    render(<PRCard pr={basePR} showMeta={false} />)
    expect(screen.queryByTestId('pr-meta')).not.toBeInTheDocument()
  })

  it('shows task_id when showTask is true', () => {
    const pr = { ...basePR, task_id: 'my-task' }
    render(<PRCard pr={pr} showTask />)
    expect(screen.getByText('my-task')).toBeInTheDocument()
  })

  it('calls onClickNumber when PR number is clicked', () => {
    const handler = vi.fn()
    render(<PRCard pr={basePR} onClickNumber={handler} />)
    fireEvent.click(screen.getByText('#42'))
    expect(handler).toHaveBeenCalledOnce()
  })

  it('calls onClick when card is clicked', () => {
    const handler = vi.fn()
    render(<PRCard pr={basePR} onClick={handler} />)
    fireEvent.click(screen.getByText('Fix the widget'))
    expect(handler).toHaveBeenCalledOnce()
  })

  it('truncates long branch names', () => {
    const pr = { ...basePR, head_branch: 'very-long-branch-name-that-exceeds-limit' }
    render(<PRCard pr={pr} />)
    // Branch name > 25 chars should be truncated
    expect(screen.getByText('very-long-branch-name-tha..')).toBeInTheDocument()
  })

  it('shows repo name extracted from URL', () => {
    render(<PRCard pr={basePR} />)
    expect(screen.getByText('repo')).toBeInTheDocument()
  })

  it('renders refresh button when onRefresh is provided', () => {
    const handler = vi.fn()
    render(<PRCard pr={basePR} onRefresh={handler} />)
    const refreshBtn = screen.getByTitle('Refresh PR status')
    expect(refreshBtn).toBeInTheDocument()
  })

  it('does not render refresh button when onRefresh is not provided', () => {
    render(<PRCard pr={basePR} />)
    expect(screen.queryByTitle('Refresh PR status')).not.toBeInTheDocument()
  })

  it('calls onRefresh callback when refresh button is clicked', () => {
    const handler = vi.fn()
    render(<PRCard pr={basePR} onRefresh={handler} />)
    const refreshBtn = screen.getByTitle('Refresh PR status')
    fireEvent.click(refreshBtn)
    expect(handler).toHaveBeenCalledOnce()
  })

  it('refresh button shows loading state after click', async () => {
    const handler = vi.fn()
    render(<PRCard pr={basePR} onRefresh={handler} />)
    const refreshBtn = screen.getByTitle('Refresh PR status')
    fireEvent.click(refreshBtn)
    // After click, should show "..." (loading state)
    expect(refreshBtn.textContent).toBe('...')
  })

  it('does not show diff stats when additions and deletions are 0', () => {
    const pr = { ...basePR, additions: 0, deletions: 0 }
    render(<PRCard pr={pr} />)
    expect(screen.queryByTestId('diff-stats')).not.toBeInTheDocument()
  })

  it('does not show comment count when count is 0', () => {
    const pr = { ...basePR, comment_count: 0 }
    render(<PRCard pr={pr} />)
    expect(screen.queryByTestId('comment-count')).not.toBeInTheDocument()
  })

  it('does not show CI ring when ci_status is empty', () => {
    const pr = { ...basePR, ci_status: '' }
    render(<PRCard pr={pr} />)
    expect(screen.queryByTestId('ci-ring')).not.toBeInTheDocument()
  })

  it('does not show review icon when review_status is empty', () => {
    const pr = { ...basePR, review_status: '' }
    render(<PRCard pr={pr} />)
    expect(screen.queryByTestId('review-icon')).not.toBeInTheDocument()
  })

  it('short branch names are not truncated', () => {
    const pr = { ...basePR, head_branch: 'short' }
    render(<PRCard pr={pr} />)
    expect(screen.getByText('short')).toBeInTheDocument()
  })

  it('renders PR link to GitHub when onClickNumber is not set', () => {
    render(<PRCard pr={basePR} />)
    const link = screen.getByText('#42')
    expect(link.closest('a')).toHaveAttribute('href', basePR.url)
    expect(link.closest('a')).toHaveAttribute('target', '_blank')
  })
})

describe('CIRing', () => {
  it('renders SVG with correct color for success', () => {
    const { container } = render(<CIRing status="success" />)
    const circles = container.querySelectorAll('circle')
    expect(circles.length).toBe(2)
    expect(circles[1].getAttribute('stroke')).toBe('var(--green)')
  })

  it('renders red for failure', () => {
    const { container } = render(<CIRing status="failure" />)
    const circles = container.querySelectorAll('circle')
    expect(circles[1].getAttribute('stroke')).toBe('var(--red)')
  })

  it('renders yellow for pending', () => {
    const { container } = render(<CIRing status="pending" />)
    const circles = container.querySelectorAll('circle')
    expect(circles[1].getAttribute('stroke')).toBe('var(--yellow)')
  })
})

describe('ReviewIcon', () => {
  it('renders checkmark for approved', () => {
    render(<ReviewIcon status="approved" />)
    const icon = screen.getByTestId('review-icon')
    expect(icon).toHaveAttribute('title', 'Approved')
  })

  it('renders X for changes_requested', () => {
    render(<ReviewIcon status="changes_requested" />)
    const icon = screen.getByTestId('review-icon')
    expect(icon).toHaveAttribute('title', 'Changes requested')
  })

  it('renders dot for review_required', () => {
    render(<ReviewIcon status="review_required" />)
    const icon = screen.getByTestId('review-icon')
    expect(icon).toHaveAttribute('title', 'Review required')
  })

  it('returns null for unknown status', () => {
    const { container } = render(<ReviewIcon status="unknown" />)
    expect(container.innerHTML).toBe('')
  })
})

describe('MyReviewPill', () => {
  it('renders no DOM for empty / unknown state', () => {
    const { container: c1 } = render(<MyReviewPill state="" />)
    expect(c1.innerHTML).toBe('')
    const { container: c2 } = render(<MyReviewPill state={undefined} />)
    expect(c2.innerHTML).toBe('')
    const { container: c3 } = render(<MyReviewPill state="bogus" />)
    expect(c3.innerHTML).toBe('')
  })

  it('renders a Pending pill carrying the state in a data attribute', () => {
    render(<MyReviewPill state="pending_review" />)
    const pill = screen.getByTestId('my-review-pill')
    expect(pill).toHaveAttribute('data-state', 'pending_review')
    expect(pill).toHaveTextContent('Pending')
  })

  it('renders Approved / Changes / Commented variants with their labels', () => {
    const { rerender } = render(<MyReviewPill state="approved" />)
    expect(screen.getByTestId('my-review-pill')).toHaveTextContent('Approved')
    rerender(<MyReviewPill state="changes_requested" />)
    expect(screen.getByTestId('my-review-pill')).toHaveTextContent('Changes')
    rerender(<MyReviewPill state="commented" />)
    expect(screen.getByTestId('my-review-pill')).toHaveTextContent('Commented')
  })
})

describe('PRCard my_review_state pill', () => {
  it('renders the pill inside PRCard when my_review_state is set', () => {
    render(<PRCard pr={{ ...basePR, my_review_state: 'pending_review' }} />)
    expect(screen.getByTestId('my-review-pill')).toHaveTextContent('Pending')
  })

  it('omits the pill when the field is empty', () => {
    render(<PRCard pr={{ ...basePR, my_review_state: '' }} />)
    expect(screen.queryByTestId('my-review-pill')).not.toBeInTheDocument()
  })
})
