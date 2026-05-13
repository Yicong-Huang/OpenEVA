import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { ActionButton } from '../components/ActionButton'

describe('ActionButton', () => {
  it('renders label and calls onClick', async () => {
    const onClick = vi.fn()
    render(<ActionButton label="Fix CI" onClick={onClick} />)
    const btn = screen.getByText('Fix CI')
    expect(btn).toBeInTheDocument()
    await userEvent.click(btn)
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('renders with accent class', () => {
    render(<ActionButton label="Open" onClick={() => {}} accent />)
    expect(screen.getByText('Open')).toHaveClass('accent')
  })

  it('can be disabled', () => {
    render(<ActionButton label="Nope" onClick={() => {}} disabled />)
    expect(screen.getByText('Nope')).toBeDisabled()
  })
})
