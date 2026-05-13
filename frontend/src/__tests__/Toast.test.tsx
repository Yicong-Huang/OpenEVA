import { render, screen, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { ToastProvider, useToast } from '../components/Toast'

afterEach(() => {
  vi.useRealTimers()
})

function TestTrigger({ title, message, type }: { title: string; message?: string; type?: 'info' | 'warning' | 'error' | 'success' }) {
  const { showToast } = useToast()
  return (
    <button
      data-testid="trigger"
      onClick={() => showToast({ title, message, type: type || 'info' })}
    >
      Trigger
    </button>
  )
}

describe('Toast', () => {
  it('showToast renders message', async () => {
    render(
      <ToastProvider>
        <TestTrigger title="Build complete" message="All checks passed" type="success" />
      </ToastProvider>,
    )

    act(() => {
      screen.getByTestId('trigger').click()
    })

    expect(screen.getByText('Build complete')).toBeInTheDocument()
    expect(screen.getByText('All checks passed')).toBeInTheDocument()
  })

  it('auto-dismisses after duration', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    function ShortToast() {
      const { showToast } = useToast()
      return (
        <button
          data-testid="trigger"
          onClick={() => showToast({ title: 'Quick toast', type: 'info', duration: 100 })}
        >
          Trigger
        </button>
      )
    }

    render(
      <ToastProvider>
        <ShortToast />
      </ToastProvider>,
    )

    act(() => {
      screen.getByTestId('trigger').click()
    })

    expect(screen.getByText('Quick toast')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(200)
    })

    await waitFor(() => {
      expect(screen.queryByText('Quick toast')).not.toBeInTheDocument()
    })
  })

  it('renders multiple toasts', () => {
    function MultiTrigger() {
      const { showToast } = useToast()
      return (
        <button
          data-testid="trigger"
          onClick={() => {
            showToast({ title: 'First', type: 'info', duration: 0 })
            showToast({ title: 'Second', type: 'error', duration: 0 })
          }}
        >
          Trigger
        </button>
      )
    }

    render(
      <ToastProvider>
        <MultiTrigger />
      </ToastProvider>,
    )

    act(() => {
      screen.getByTestId('trigger').click()
    })

    expect(screen.getByText('First')).toBeInTheDocument()
    expect(screen.getByText('Second')).toBeInTheDocument()
    expect(screen.getAllByTestId('toast-item')).toHaveLength(2)
  })
})
