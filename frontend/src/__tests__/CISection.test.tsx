import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { CISection } from '../components/pr/CISection'
import type { PRDetail } from '../types'

type Check = PRDetail['statusCheckRollup'][number]

const checks: Check[] = [
  { name: 'build', conclusion: 'SUCCESS' },
  { name: 'test', conclusion: 'SUCCESS' },
  { name: 'lint', conclusion: 'FAILURE' },
  { name: '[Non-Blocking] optional-check', conclusion: 'FAILURE' },
  { name: 'deploy', status: 'PENDING' },
]

describe('CISection', () => {
  it('renders pass/fail counts', () => {
    render(<CISection checks={checks} ciExpanded={false} onToggleExpand={() => {}} />)
    expect(screen.getByText('CI 2/5')).toBeInTheDocument()
    expect(screen.getByTestId('ci-failed-count')).toHaveTextContent('2 failed')
  })

  it('non-blocking failures shown grey when expanded', () => {
    render(<CISection checks={checks} ciExpanded={true} onToggleExpand={() => {}} />)
    expect(screen.getByTestId('ci-detail')).toBeInTheDocument()
    const nbIcons = screen.getAllByTestId('nb-fail-icon')
    expect(nbIcons.length).toBe(1)
    expect(nbIcons[0]).toHaveStyle({ color: 'var(--text-subtle)' })
  })

  it('expand/collapse CI detail list', () => {
    let expanded = false
    const toggle = () => { expanded = !expanded }

    const { rerender } = render(
      <CISection checks={checks} ciExpanded={false} onToggleExpand={toggle} />,
    )
    expect(screen.queryByTestId('ci-detail')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('ci-section'))
    rerender(
      <CISection checks={checks} ciExpanded={true} onToggleExpand={toggle} />,
    )
    expect(screen.getByTestId('ci-detail')).toBeInTheDocument()

    const items = screen.getAllByTestId('ci-check-item')
    expect(items.length).toBe(5)
  })

  it('returns null when no checks', () => {
    const { container } = render(
      <CISection checks={[]} ciExpanded={false} onToggleExpand={() => {}} />,
    )
    expect(container.innerHTML).toBe('')
  })
})
