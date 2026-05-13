import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { PRNode as PRCard } from '../components/PRNode'
import type { PR } from '../types'

const mockPR: PR = {
  number: 123,
  url: 'https://github.com/example/repo/pull/123',
  status: 'open',
  title: 'Fix something',
  ci_status: 'success',
  review_status: 'approved',
  comment_count: 5,
  additions: 100,
  deletions: 20,
  author: 'user1',
  head_branch: 'fix-branch',
  base_branch: 'main',
  last_updated: '2026-04-12T00:00:00Z',
}

describe('PRCard', () => {
  it('renders PR number and title', () => {
    render(<PRCard pr={mockPR} />)
    expect(screen.getByText('#123')).toBeInTheDocument()
    expect(screen.getByText('Fix something')).toBeInTheDocument()
  })

  it('renders status badge with correct class', () => {
    render(<PRCard pr={mockPR} />)
    const badge = screen.getByText('open')
    expect(badge).toHaveClass('pr-label', 'open')
  })

  it('renders CI ring SVG when ci_status provided', () => {
    render(<PRCard pr={mockPR} />)
    expect(screen.getByTestId('ci-ring')).toBeInTheDocument()
  })

  it('does not render CI ring when ci_status is empty', () => {
    const pr = { ...mockPR, ci_status: '' }
    render(<PRCard pr={pr} />)
    expect(screen.queryByTestId('ci-ring')).not.toBeInTheDocument()
  })

  it('renders +/- stats when showMeta=true', () => {
    render(<PRCard pr={mockPR} showMeta={true} />)
    const stats = screen.getByTestId('diff-stats')
    expect(stats).toBeInTheDocument()
    expect(stats.textContent).toContain('+100')
    expect(stats.textContent).toContain('-20')
  })

  it('hides meta when showMeta=false', () => {
    render(<PRCard pr={mockPR} showMeta={false} />)
    expect(screen.queryByTestId('pr-meta')).not.toBeInTheDocument()
  })

  it('calls onClickNumber when PR number clicked', async () => {
    const onClickNumber = vi.fn()
    render(<PRCard pr={mockPR} onClickNumber={onClickNumber} />)
    await userEvent.click(screen.getByText('#123'))
    expect(onClickNumber).toHaveBeenCalledOnce()
  })

  it('renders PR number as external link when onClickNumber not provided', () => {
    render(<PRCard pr={mockPR} />)
    const link = screen.getByText('#123').closest('a')
    expect(link).toHaveAttribute('href', 'https://github.com/example/repo/pull/123')
    expect(link).toHaveAttribute('target', '_blank')
  })

  it('renders review icon for approved status', () => {
    render(<PRCard pr={mockPR} />)
    expect(screen.getByTestId('review-icon')).toBeInTheDocument()
  })

  it('renders comment count in meta', () => {
    render(<PRCard pr={mockPR} showMeta={true} />)
    expect(screen.getByTestId('comment-count')).toHaveTextContent('5 comments')
  })
})
