import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { ReviewSection } from '../components/pr/ReviewSection'
import type { PRDetail } from '../types'

type Review = PRDetail['reviews'][number]

const approvedReview: Review = { author: { login: 'alice' }, state: 'APPROVED' }
const changesReview: Review = { author: { login: 'bob' }, state: 'CHANGES_REQUESTED' }
const commentedReview: Review = { author: { login: 'charlie' }, state: 'COMMENTED' }

/** Helper: extract the inline style string from an element. */
function styleOf(el: HTMLElement): string {
  return el.getAttribute('style') || ''
}

describe('ReviewSection', () => {
  it('renders reviewer avatars with green border for approved', () => {
    render(<ReviewSection reviews={[approvedReview]} reviewDecision="APPROVED" />)
    const img = screen.getByAltText('alice')
    expect(img).toBeInTheDocument()
    expect(styleOf(img)).toContain('2px solid var(--green)')
  })

  it('renders reviewer avatars with red border for changes_requested', () => {
    render(<ReviewSection reviews={[changesReview]} reviewDecision="CHANGES_REQUESTED" />)
    const img = screen.getByAltText('bob')
    expect(img).toBeInTheDocument()
    expect(styleOf(img)).toContain('2px solid var(--red)')
  })

  it('renders reviewer avatars with default border for commented', () => {
    render(<ReviewSection reviews={[commentedReview]} reviewDecision="" />)
    const img = screen.getByAltText('charlie')
    expect(styleOf(img)).toContain('2px solid var(--border)')
  })

  it('shows review decision text with correct color', () => {
    render(<ReviewSection reviews={[approvedReview]} reviewDecision="APPROVED" />)
    const decision = screen.getByText('APPROVED')
    expect(decision).toBeInTheDocument()
    expect(styleOf(decision)).toContain('var(--green)')
    expect(styleOf(decision)).toContain('font-weight: 600')
  })

  it('shows CHANGES_REQUESTED decision in red', () => {
    render(<ReviewSection reviews={[changesReview]} reviewDecision="CHANGES_REQUESTED" />)
    const decision = screen.getByText('CHANGES_REQUESTED')
    expect(styleOf(decision)).toContain('var(--red)')
  })

  it('shows REVIEW_REQUIRED decision in yellow', () => {
    render(<ReviewSection reviews={[commentedReview]} reviewDecision="REVIEW_REQUIRED" />)
    const decision = screen.getByText('REVIEW_REQUIRED')
    expect(styleOf(decision)).toContain('var(--yellow)')
  })

  it('deduplicates reviews by login, keeping the last review', () => {
    const firstReview: Review = { author: { login: 'alice' }, state: 'COMMENTED' }
    const laterReview: Review = { author: { login: 'alice' }, state: 'APPROVED' }
    render(<ReviewSection reviews={[firstReview, laterReview]} reviewDecision="APPROVED" />)
    const imgs = screen.getAllByRole('img')
    expect(imgs).toHaveLength(1)
    expect(styleOf(imgs[0])).toContain('2px solid var(--green)')
  })

  it('returns null when no reviews and no reviewDecision', () => {
    const { container } = render(<ReviewSection reviews={[]} reviewDecision="" />)
    expect(container.innerHTML).toBe('')
  })

  it('renders multiple reviewers', () => {
    render(
      <ReviewSection
        reviews={[approvedReview, changesReview, commentedReview]}
        reviewDecision="CHANGES_REQUESTED"
      />,
    )
    expect(screen.getByAltText('alice')).toBeInTheDocument()
    expect(screen.getByAltText('bob')).toBeInTheDocument()
    expect(screen.getByAltText('charlie')).toBeInTheDocument()
  })
})
