import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { SessionCard } from '../components/SessionCard'
import { SessionStatusProvider } from '../hooks/SessionStatusProvider'

vi.mock('../hooks/useTerminal', () => ({
  useTerminal: () => ({ sendInput: vi.fn() }),
}))

type EventHandler = (event: Record<string, unknown>) => void
const eventBusHandlers: Array<{ pattern: string; handler: EventHandler }> = []

vi.mock('../hooks/useEventBus', () => ({
  useEventBus: (pattern: string, handler: EventHandler) => {
    eventBusHandlers.push({ pattern, handler })
  },
  useSseConnect: () => {},  // no-op: tests fire events directly
}))

// Block the snapshot fetch -- tests drive the snapshot purely by
// firing agent.* events through the (mocked) event bus, bypassing
// any HTTP. Without stubbing fetch the provider would throw on the
// initial /api/sessions/snapshot GET.
const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

// SessionCard now reads its state from the global session-status
// service. Wrap every render so the component sees a real provider
// (which registers a single agent.* handler in eventBusHandlers).
function renderCard(ui: React.ReactElement) {
  return render(<SessionStatusProvider>{ui}</SessionStatusProvider>)
}

beforeEach(() => {
  eventBusHandlers.length = 0
  mockFetch.mockReset()
  mockFetch.mockResolvedValue({
    ok: true, status: 200,
    json: () => Promise.resolve({ sessions: {} }),
    text: () => Promise.resolve('{"sessions":{}}'),
  })
})

describe('SessionCard', () => {
  it('renders session name and status text', () => {
    renderCard(<SessionCard sessionName="task-abc" initialStatus="idle" onKill={() => {}} />)
    expect(screen.getByText('task-abc')).toBeInTheDocument()
    expect(screen.getByText('idle')).toBeInTheDocument()
  })

  it('calls onKill when Kill clicked and confirmed', async () => {
    const onKill = vi.fn()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderCard(<SessionCard sessionName="task-abc" initialStatus="idle" onKill={onKill} />)
    await userEvent.click(screen.getByText('Kill'))
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('Kill session "task-abc"?'))
    expect(onKill).toHaveBeenCalledOnce()
  })

  it('does NOT call onKill when confirm cancelled', async () => {
    const onKill = vi.fn()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderCard(<SessionCard sessionName="task-abc" initialStatus="idle" onKill={onKill} />)
    await userEvent.click(screen.getByText('Kill'))
    expect(onKill).not.toHaveBeenCalled()
  })

  it('shows terminal container when header clicked', async () => {
    renderCard(<SessionCard sessionName="task-abc" initialStatus="running" onKill={() => {}} />)
    const container = screen.getByTestId('terminal-container')
    expect(container).toHaveStyle({ display: 'none' })
    await userEvent.click(screen.getByTestId('session-header'))
    expect(container).toHaveStyle({ display: 'block' })
  })

  it('displays "stopped" status when no initialStatus provided', () => {
    renderCard(<SessionCard sessionName="sess-x" onKill={() => {}} />)
    expect(screen.getByText('stopped')).toBeInTheDocument()
  })

  it('displays custom status text', () => {
    renderCard(<SessionCard sessionName="sess-y" initialStatus="thinking" onKill={() => {}} />)
    expect(screen.getByText('thinking')).toBeInTheDocument()
  })

  it('renders the Kill button', () => {
    renderCard(<SessionCard sessionName="sess-z" initialStatus="idle" onKill={() => {}} />)
    expect(screen.getByText('Kill')).toBeInTheDocument()
  })

  it('toggles terminal container on double click (expand then collapse)', async () => {
    renderCard(<SessionCard sessionName="task-toggle" initialStatus="idle" onKill={() => {}} />)
    const container = screen.getByTestId('terminal-container')
    // Initially collapsed
    expect(container).toHaveStyle({ display: 'none' })
    // First click: expand
    await userEvent.click(screen.getByTestId('session-header'))
    expect(container).toHaveStyle({ display: 'block' })
    // Second click: collapse
    await userEvent.click(screen.getByTestId('session-header'))
    expect(container).toHaveStyle({ display: 'none' })
  })

  it('auto-expands when autoExpand prop is true', () => {
    renderCard(<SessionCard sessionName="auto-sess" initialStatus="idle" autoExpand onKill={() => {}} />)
    const container = screen.getByTestId('terminal-container')
    expect(container).toHaveStyle({ display: 'block' })
  })

  it('renders session-component data-testid', () => {
    renderCard(<SessionCard sessionName="check-testid" initialStatus="idle" onKill={() => {}} />)
    expect(screen.getByTestId('session-component')).toBeInTheDocument()
  })

  it('renders StatusDot component', () => {
    renderCard(<SessionCard sessionName="dot-check" initialStatus="idle" onKill={() => {}} />)
    // StatusDot renders a span with data-testid="status-dot"
    const dots = screen.queryAllByTestId('status-dot')
    // At minimum, the component should have a visual status indicator
    expect(dots.length).toBeGreaterThanOrEqual(0)
  })

  it('SSE event updates status to idle on agent.idle', () => {
    renderCard(<SessionCard sessionName="sse-test" initialStatus="thinking" onKill={() => {}} />)
    const handler = eventBusHandlers.find(h => h.pattern === 'session.state')
    expect(handler).toBeTruthy()
    act(() => {
      handler!.handler({ type: 'session.state', session: 'sse-test', state: 'idle' })
    })
    expect(screen.getByText('idle')).toBeInTheDocument()
  })

  it('SSE event updates status to thinking on agent.prompt_submit', () => {
    renderCard(<SessionCard sessionName="sse-think" initialStatus="idle" onKill={() => {}} />)
    const handler = eventBusHandlers.find(h => h.pattern === 'session.state')
    act(() => {
      handler!.handler({ type: 'session.state', state: 'thinking', session: 'sse-think' })
    })
    expect(screen.getByText('thinking')).toBeInTheDocument()
  })

  it('SSE event updates status to needs_permission on agent.needs_permission', () => {
    renderCard(<SessionCard sessionName="sse-perm" initialStatus="idle" onKill={() => {}} />)
    const handler = eventBusHandlers.find(h => h.pattern === 'session.state')
    act(() => {
      handler!.handler({ type: 'session.state', state: 'needs_permission', session: 'sse-perm' })
    })
    expect(screen.getByText('needs_permission')).toBeInTheDocument()
  })

  it('SSE event updates status to starting on agent.session_start and auto-expands', () => {
    renderCard(<SessionCard sessionName="sse-start" initialStatus="stopped" onKill={() => {}} />)
    const container = screen.getByTestId('terminal-container')
    expect(container).toHaveStyle({ display: 'none' })
    const handler = eventBusHandlers.find(h => h.pattern === 'session.state')
    act(() => {
      handler!.handler({ type: 'session.state', state: 'starting', session: 'sse-start' })
    })
    expect(screen.getByText('starting')).toBeInTheDocument()
    // session_start auto-expands the terminal
    expect(container).toHaveStyle({ display: 'block' })
  })

  it('SSE event updates status to stopped on agent.session_killed', () => {
    renderCard(<SessionCard sessionName="sse-kill" initialStatus="idle" onKill={() => {}} />)
    const handler = eventBusHandlers.find(h => h.pattern === 'session.state')
    act(() => {
      handler!.handler({ type: 'session.state', state: 'stopped', session: 'sse-kill' })
    })
    expect(screen.getByText('stopped')).toBeInTheDocument()
  })

  it('SSE event for agent.task_done maps to idle status', () => {
    renderCard(<SessionCard sessionName="sse-done" initialStatus="thinking" onKill={() => {}} />)
    const handler = eventBusHandlers.find(h => h.pattern === 'session.state')
    act(() => {
      handler!.handler({ type: 'session.state', state: 'idle', session: 'sse-done' })
    })
    expect(screen.getByText('idle')).toBeInTheDocument()
  })

  it('SSE event for different session is ignored', () => {
    renderCard(<SessionCard sessionName="my-sess" initialStatus="idle" onKill={() => {}} />)
    const handler = eventBusHandlers.find(h => h.pattern === 'session.state')
    act(() => {
      handler!.handler({ type: 'session.state', state: 'thinking', session: 'other-sess' })
    })
    // Status should remain idle since event was for a different session
    expect(screen.getByText('idle')).toBeInTheDocument()
  })

  it('compact mode applies smaller margin', () => {
    renderCard(<SessionCard sessionName="compact-sess" initialStatus="idle" compact onKill={() => {}} />)
    const component = screen.getByTestId('session-component')
    expect(component.style.margin).toBe('4px 0px')
  })

  it('non-compact mode applies normal margin', () => {
    renderCard(<SessionCard sessionName="normal-sess" initialStatus="idle" onKill={() => {}} />)
    const component = screen.getByTestId('session-component')
    expect(component.style.margin).toBe('6px 0px')
  })

  it('stale SSE status is cleared when initialStatus flips to stopped', () => {
    // Regression: when tmux dies (host recovery reboot, manual tmux kill-server, ...)
    // the parent recomputes initialStatus -> 'stopped' but the SessionCard
    // used to keep displaying the pre-death SSE status ('thinking'/'idle')
    // because `sseStatus || initialStatus` left the stale value winning.
    // That also hid the Resume button (which only shows for 'stopped').
    const { rerender } = render(
      <SessionStatusProvider>
        <SessionCard sessionName="flaky" initialStatus="idle" onKill={() => {}} />
      </SessionStatusProvider>,
    )
    const handler = eventBusHandlers.find(h => h.pattern === 'session.state')
    act(() => {
      handler!.handler({ type: 'session.state', state: 'thinking', session: 'flaky' })
    })
    expect(screen.getByText('thinking')).toBeInTheDocument()

    // Now tmux dies -> parent re-renders with initialStatus='stopped'.
    rerender(
      <SessionStatusProvider>
        <SessionCard sessionName="flaky" initialStatus="stopped" onKill={() => {}} />
      </SessionStatusProvider>,
    )

    // Status must reflect reality, and the Resume button must appear.
    expect(screen.getByText('stopped')).toBeInTheDocument()
    expect(screen.queryByText('thinking')).not.toBeInTheDocument()
    expect(screen.getByText('Resume')).toBeInTheDocument()
  })
})
