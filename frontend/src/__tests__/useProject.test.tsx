import { renderHook, waitFor, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

// Capture bus handlers so we can simulate agent/github events.
const eventBusHandlers: Array<{ pattern: string; handler: (event: Record<string, unknown>) => void }> = []
vi.mock('../hooks/useEventBus', () => ({
  useEventBus: (pattern: string, handler: (event: Record<string, unknown>) => void) => {
    eventBusHandlers.push({ pattern, handler })
  },
}))

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

function jsonResponse(body: unknown) {
  const text = JSON.stringify(body)
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(text),
  })
}

const projFixture = {
  id: 'proj-1', name: 'P1', description: '', has_tickets: false,
  progress: 0, task_counts: {}, tasks: {},
}

beforeEach(() => {
  mockFetch.mockReset()
  eventBusHandlers.length = 0
  mockFetch.mockImplementation((url: string) => {
    if (typeof url === 'string' && url.startsWith('/api/projects/')) {
      return jsonResponse(projFixture)
    }
    return jsonResponse({})
  })
})

describe('useProject', () => {
  it('fetches the project for a given id', async () => {
    const { useProject } = await import('../hooks/useProject')
    const { result } = renderHook(() => useProject('proj-1'))
    await waitFor(() => {
      expect(result.current.project).toEqual(projFixture)
    })
  })

  it('clears state and does not fetch when id is null', async () => {
    const { useProject } = await import('../hooks/useProject')
    const { result } = renderHook(() => useProject(null))
    await waitFor(() => expect(result.current.project).toBeNull())
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('re-fetches when agent.* events fire', async () => {
    const { useProject } = await import('../hooks/useProject')
    renderHook(() => useProject('proj-1'))
    await waitFor(() => expect(mockFetch).toHaveBeenCalled())
    const before = mockFetch.mock.calls.length
    const agentHandler = eventBusHandlers.find(h => h.pattern === 'agent.*')
    expect(agentHandler).toBeTruthy()
    act(() => { agentHandler!.handler({ type: 'agent.idle' }) })
    await waitFor(() => expect(mockFetch.mock.calls.length).toBeGreaterThan(before))
  })

  it('re-fetches when github.* events fire', async () => {
    const { useProject } = await import('../hooks/useProject')
    renderHook(() => useProject('proj-1'))
    await waitFor(() => expect(mockFetch).toHaveBeenCalled())
    const before = mockFetch.mock.calls.length
    const gh = eventBusHandlers.find(h => h.pattern === 'github.*')
    act(() => { gh!.handler({ type: 'github.comment' }) })
    await waitFor(() => expect(mockFetch.mock.calls.length).toBeGreaterThan(before))
  })

  it('does not fetch on events when id is null', async () => {
    const { useProject } = await import('../hooks/useProject')
    renderHook(() => useProject(null))
    await waitFor(() => expect(mockFetch).not.toHaveBeenCalled())
    const agentHandler = eventBusHandlers.find(h => h.pattern === 'agent.*')
    act(() => { agentHandler!.handler({ type: 'agent.idle' }) })
    // Still no fetches; nothing to refresh.
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('manual refetch() re-invokes getProject', async () => {
    const { useProject } = await import('../hooks/useProject')
    const { result } = renderHook(() => useProject('proj-1'))
    await waitFor(() => expect(result.current.project).toEqual(projFixture))
    const before = mockFetch.mock.calls.length
    act(() => { result.current.refetch() })
    await waitFor(() => expect(mockFetch.mock.calls.length).toBeGreaterThan(before))
  })

  it('swallows fetch errors and resets state', async () => {
    mockFetch.mockImplementationOnce(() => Promise.resolve({
      ok: false, status: 500, text: () => Promise.resolve('err'),
    }))
    const { useProject } = await import('../hooks/useProject')
    const { result } = renderHook(() => useProject('proj-1'))
    // Expect null after rejection -- useProject swallows and clears.
    await waitFor(() => expect(result.current.project).toBeNull())
  })
})
