import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { useEffect, useState } from 'react'
import { AlertProvider, useAlert } from '../components/Alert'

// Helper component: triggers a confirm/alert/prompt and stores the result.
// opts is typed loosely as `any` because TS can't narrow the union on the
// fly; each test supplies a shape valid for its kind.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Opts = any
function Harness({ kind, opts, onResult }: {
  kind: 'confirm' | 'alert' | 'prompt'
  opts: Opts
  onResult: (v: unknown) => void
}) {
  const a = useAlert()
  const [fired, setFired] = useState(false)
  useEffect(() => {
    if (fired) return
    setFired(true)
    let p: Promise<unknown>
    if (kind === 'confirm') p = a.confirm(opts)
    else if (kind === 'alert') p = a.alert(opts)
    else p = a.prompt(opts)
    p.then(onResult)
  }, [a, fired, kind, opts, onResult])
  return null
}

describe('AlertProvider - confirm', () => {
  it('renders title + message + buttons, returns true on confirm', async () => {
    let result: unknown
    render(
      <AlertProvider>
        <Harness kind="confirm"
          opts={{ title: 'Delete?', message: 'gone forever', confirmLabel: 'Yes', cancelLabel: 'No' }}
          onResult={(v) => { result = v }} />
      </AlertProvider>
    )
    await waitFor(() => screen.getByTestId('alert-dialog'))
    expect(screen.getByText('Delete?')).toBeInTheDocument()
    expect(screen.getByText('gone forever')).toBeInTheDocument()
    expect(screen.getByTestId('alert-confirm').textContent).toBe('Yes')
    expect(screen.getByTestId('alert-cancel').textContent).toBe('No')
    fireEvent.click(screen.getByTestId('alert-confirm'))
    await waitFor(() => expect(result).toBe(true))
    expect(screen.queryByTestId('alert-dialog')).not.toBeInTheDocument()
  })

  it('returns false on cancel', async () => {
    let result: unknown
    render(
      <AlertProvider>
        <Harness kind="confirm" opts={{ title: 'X' }} onResult={(v) => { result = v }} />
      </AlertProvider>
    )
    await waitFor(() => screen.getByTestId('alert-dialog'))
    fireEvent.click(screen.getByTestId('alert-cancel'))
    await waitFor(() => expect(result).toBe(false))
  })

  it('Escape key cancels', async () => {
    let result: unknown
    render(
      <AlertProvider>
        <Harness kind="confirm" opts={{ title: 'X' }} onResult={(v) => { result = v }} />
      </AlertProvider>
    )
    await waitFor(() => screen.getByTestId('alert-dialog'))
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(result).toBe(false))
  })

  it('Enter key confirms', async () => {
    let result: unknown
    render(
      <AlertProvider>
        <Harness kind="confirm" opts={{ title: 'X' }} onResult={(v) => { result = v }} />
      </AlertProvider>
    )
    await waitFor(() => screen.getByTestId('alert-dialog'))
    fireEvent.keyDown(window, { key: 'Enter' })
    await waitFor(() => expect(result).toBe(true))
  })

  it('clicking the backdrop cancels', async () => {
    let result: unknown
    render(
      <AlertProvider>
        <Harness kind="confirm" opts={{ title: 'X' }} onResult={(v) => { result = v }} />
      </AlertProvider>
    )
    await waitFor(() => screen.getByTestId('alert-backdrop'))
    fireEvent.click(screen.getByTestId('alert-backdrop'))
    await waitFor(() => expect(result).toBe(false))
  })

  it('clicks inside the dialog do not bubble to backdrop', async () => {
    let result: unknown
    render(
      <AlertProvider>
        <Harness kind="confirm" opts={{ title: 'X' }} onResult={(v) => { result = v }} />
      </AlertProvider>
    )
    await waitFor(() => screen.getByTestId('alert-dialog'))
    fireEvent.click(screen.getByTestId('alert-dialog'))
    // Still open, no resolution.
    expect(result).toBeUndefined()
    expect(screen.getByTestId('alert-dialog')).toBeInTheDocument()
  })
})

describe('AlertProvider - alert', () => {
  it('OK button resolves the promise', async () => {
    let resolved = false
    render(
      <AlertProvider>
        <Harness kind="alert" opts={{ title: 'Hi' }} onResult={() => { resolved = true }} />
      </AlertProvider>
    )
    await waitFor(() => screen.getByTestId('alert-dialog'))
    fireEvent.click(screen.getByTestId('alert-ok'))
    await waitFor(() => expect(resolved).toBe(true))
  })

  it('honors custom okLabel', async () => {
    render(
      <AlertProvider>
        <Harness kind="alert" opts={{ title: 'Hi', okLabel: 'Got it' }} onResult={() => {}} />
      </AlertProvider>
    )
    await waitFor(() => screen.getByTestId('alert-dialog'))
    expect(screen.getByTestId('alert-ok').textContent).toBe('Got it')
  })
})

describe('AlertProvider - prompt', () => {
  it('returns the entered value on confirm', async () => {
    let result: unknown
    render(
      <AlertProvider>
        <Harness kind="prompt"
          opts={{ title: 'Reason?', defaultValue: 'init' }}
          onResult={(v) => { result = v }} />
      </AlertProvider>
    )
    await waitFor(() => screen.getByTestId('alert-prompt-input'))
    const input = screen.getByTestId('alert-prompt-input') as HTMLInputElement
    expect(input.value).toBe('init')
    fireEvent.change(input, { target: { value: 'updated' } })
    fireEvent.click(screen.getByTestId('alert-confirm'))
    await waitFor(() => expect(result).toBe('updated'))
  })

  it('returns null on cancel', async () => {
    let result: unknown
    render(
      <AlertProvider>
        <Harness kind="prompt" opts={{ title: 'Reason?' }} onResult={(v) => { result = v }} />
      </AlertProvider>
    )
    await waitFor(() => screen.getByTestId('alert-prompt-input'))
    fireEvent.click(screen.getByTestId('alert-cancel'))
    await waitFor(() => expect(result).toBeNull())
  })

  it('Enter key submits a single-line prompt', async () => {
    let result: unknown
    render(
      <AlertProvider>
        <Harness kind="prompt"
          opts={{ title: 'X', defaultValue: 'val' }}
          onResult={(v) => { result = v }} />
      </AlertProvider>
    )
    await waitFor(() => screen.getByTestId('alert-prompt-input'))
    fireEvent.keyDown(window, { key: 'Enter' })
    await waitFor(() => expect(result).toBe('val'))
  })

  it('multiline mode renders a textarea, Enter does NOT submit', async () => {
    let result: unknown
    render(
      <AlertProvider>
        <Harness kind="prompt"
          opts={{ title: 'X', multiline: true, defaultValue: 'a' }}
          onResult={(v) => { result = v }} />
      </AlertProvider>
    )
    await waitFor(() => screen.getByTestId('alert-prompt-input'))
    expect((screen.getByTestId('alert-prompt-input') as HTMLElement).tagName).toBe('TEXTAREA')
    fireEvent.keyDown(window, { key: 'Enter' })
    expect(result).toBeUndefined()
    fireEvent.click(screen.getByTestId('alert-confirm'))
    await waitFor(() => expect(result).toBe('a'))
  })

  it('Escape returns null', async () => {
    let result: unknown
    render(
      <AlertProvider>
        <Harness kind="prompt" opts={{ title: 'X' }} onResult={(v) => { result = v }} />
      </AlertProvider>
    )
    await waitFor(() => screen.getByTestId('alert-prompt-input'))
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(result).toBeNull())
  })
})

describe('AlertProvider - queue', () => {
  it('shows dialogs sequentially when stacked', async () => {
    function Stack() {
      const a = useAlert()
      const [results, setResults] = useState<boolean[]>([])
      const [fired, setFired] = useState(false)
      useEffect(() => {
        if (fired) return
        setFired(true)
        ;(async () => {
          const r1 = await a.confirm({ title: 'first' })
          const r2 = await a.confirm({ title: 'second' })
          setResults([r1, r2])
        })()
      }, [a, fired])
      return <div data-testid="results">{results.join(',')}</div>
    }
    render(<AlertProvider><Stack /></AlertProvider>)
    await waitFor(() => screen.getByText('first'))
    expect(screen.queryByText('second')).not.toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByTestId('alert-confirm')) })
    await waitFor(() => screen.getByText('second'))
    fireEvent.click(screen.getByTestId('alert-cancel'))
    await waitFor(() => {
      expect(screen.getByTestId('results').textContent).toBe('true,false')
    })
  })
})

describe('useAlert without provider', () => {
  it('falls back to native window.confirm', async () => {
    const origConfirm = window.confirm
    window.confirm = (() => true) as typeof window.confirm
    let result: unknown
    function Probe() {
      const a = useAlert()
      const [done, setDone] = useState(false)
      useEffect(() => {
        if (done) return
        setDone(true)
        a.confirm({ title: 'X' }).then((v) => { result = v })
      }, [a, done])
      return null
    }
    try {
      render(<Probe />)
      await waitFor(() => expect(result).toBe(true))
    } finally {
      window.confirm = origConfirm
    }
  })
})
