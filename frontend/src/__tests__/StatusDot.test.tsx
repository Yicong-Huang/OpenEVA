import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { StatusDot } from '../components/StatusDot'

describe('StatusDot', () => {
  it('renders with done status class', () => {
    render(<StatusDot status="done" />)
    const dot = screen.getByTestId('status-dot')
    expect(dot).toHaveClass('dot', 'dot-done')
  })

  it('renders with closed status class', () => {
    render(<StatusDot status="closed" />)
    expect(screen.getByTestId('status-dot')).toHaveClass('dot-closed')
  })

  it('renders with in_progress status', () => {
    render(<StatusDot status="in_progress" />)
    expect(screen.getByTestId('status-dot')).toHaveClass('dot-in_progress')
  })

  it('applies custom style', () => {
    render(<StatusDot status="done" style={{ marginRight: 6 }} />)
    expect(screen.getByTestId('status-dot')).toHaveStyle({ marginRight: '6px' })
  })
})
