import { render, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { useRef } from 'react'
import { useClickOutside } from '../hooks/useClickOutside'

function TestComponent({ onClickOutside }: { onClickOutside: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useClickOutside(ref, onClickOutside)
  return (
    <div>
      <div ref={ref} data-testid="inside">Inside</div>
      <div data-testid="outside">Outside</div>
    </div>
  )
}

describe('useClickOutside', () => {
  it('calls handler when clicking outside the ref element', () => {
    const handler = vi.fn()
    render(<TestComponent onClickOutside={handler} />)
    fireEvent.mouseDown(document.querySelector('[data-testid="outside"]')!)
    expect(handler).toHaveBeenCalledOnce()
  })

  it('does not call handler when clicking inside the ref element', () => {
    const handler = vi.fn()
    render(<TestComponent onClickOutside={handler} />)
    fireEvent.mouseDown(document.querySelector('[data-testid="inside"]')!)
    expect(handler).not.toHaveBeenCalled()
  })

  it('cleans up the event listener on unmount', () => {
    const handler = vi.fn()
    const { unmount } = render(<TestComponent onClickOutside={handler} />)
    unmount()
    fireEvent.mouseDown(document.body)
    expect(handler).not.toHaveBeenCalled()
  })
})
