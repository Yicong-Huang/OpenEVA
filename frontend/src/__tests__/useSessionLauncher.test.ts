import { renderHook } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mocks must be declared before importing the hook under test.
const openSession = vi.fn()
const listAgents = vi.fn()
const choose = vi.fn()
const alert = vi.fn()

vi.mock('../api', () => ({
  api: {
    openSession: (...a: unknown[]) => openSession(...a),
    listAgents: () => listAgents(),
    // deliverPromptToSession touches these; unused here (empty prompt).
    waitReady: vi.fn(),
    sendTerminalInput: vi.fn(),
  },
}))

vi.mock('../components/Alert', () => ({
  useAlert: () => ({ alert, choose }),
}))

// Mutable session-status stub. Each test sets the sessions / reviews the
// launcher sees so we can exercise the "already-open -> reuse" path.
const sessionStatus: { sessions: Record<string, { state: string }>; reviews: Array<{ url: string; session_name?: string }> } = {
  sessions: {},
  reviews: [],
}
vi.mock('../hooks/SessionStatusProvider', () => ({
  useSessionStatus: () => sessionStatus,
}))

import { useSessionLauncher } from '../hooks/useSessionLauncher'

const TASK = { type: 'task' as const, taskId: 't1', projectId: 'p1' }
const REVIEW = { type: 'review' as const, reviewUrl: 'https://github.com/o/r/pull/9' }

describe('useSessionLauncher agent picker', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // No prompt in the result -> deliverPromptToSession is skipped.
    openSession.mockResolvedValue({ session: 's', new: true, prompt: '' })
    // Default: no live session for this surface -> picker runs as usual.
    sessionStatus.sessions = {}
    sessionStatus.reviews = []
  })

  it('one enabled agent: no prompt, agent_id left undefined', async () => {
    listAgents.mockResolvedValue({
      agents: [{ id: 'claude', name: 'Claude' }],
      enabled: ['claude'],
    })
    const { result } = renderHook(() => useSessionLauncher(TASK))
    await result.current.launch({ actionId: 'open' })
    expect(choose).not.toHaveBeenCalled()
    expect(openSession).toHaveBeenCalledTimes(1)
    expect(openSession.mock.calls[0][0].agent_id).toBeUndefined()
  })

  it('multiple enabled: prompts and forwards the picked agent_id', async () => {
    listAgents.mockResolvedValue({
      agents: [{ id: 'claude', name: 'Claude' }, { id: 'codex', name: 'Codex' }],
      enabled: ['claude', 'codex'],
    })
    choose.mockResolvedValue('codex')
    const { result } = renderHook(() => useSessionLauncher(TASK))
    await result.current.launch({ actionId: 'open' })
    expect(choose).toHaveBeenCalledTimes(1)
    expect(openSession).toHaveBeenCalledTimes(1)
    expect(openSession.mock.calls[0][0].agent_id).toBe('codex')
  })

  it('multiple enabled, dismissed: aborts without opening a session', async () => {
    listAgents.mockResolvedValue({
      agents: [{ id: 'claude', name: 'Claude' }, { id: 'codex', name: 'Codex' }],
      enabled: ['claude', 'codex'],
    })
    choose.mockResolvedValue(null)
    const { result } = renderHook(() => useSessionLauncher(TASK))
    const res = await result.current.launch({ actionId: 'open' })
    expect(res).toBeNull()
    expect(openSession).not.toHaveBeenCalled()
  })

  it('caller-supplied agentId skips the prompt', async () => {
    listAgents.mockResolvedValue({
      agents: [{ id: 'claude', name: 'Claude' }, { id: 'codex', name: 'Codex' }],
      enabled: ['claude', 'codex'],
    })
    const { result } = renderHook(() => useSessionLauncher(TASK))
    await result.current.launch({ actionId: 'open', agentId: 'claude' })
    expect(listAgents).not.toHaveBeenCalled()
    expect(choose).not.toHaveBeenCalled()
    expect(openSession.mock.calls[0][0].agent_id).toBe('claude')
  })

  it('listAgents failure degrades to no prompt', async () => {
    listAgents.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useSessionLauncher(TASK))
    await result.current.launch({ actionId: 'open' })
    expect(choose).not.toHaveBeenCalled()
    expect(openSession).toHaveBeenCalledTimes(1)
    expect(openSession.mock.calls[0][0].agent_id).toBeUndefined()
  })

  it('task session already live: reuse it, never prompt', async () => {
    // Multiple agents enabled -> normally prompts, but the task's
    // session is already running so the picker must be skipped.
    listAgents.mockResolvedValue({
      agents: [{ id: 'claude', name: 'Claude' }, { id: 'codex', name: 'Codex' }],
      enabled: ['claude', 'codex'],
    })
    sessionStatus.sessions = { t1: { state: 'idle' } }
    const { result } = renderHook(() => useSessionLauncher(TASK))
    await result.current.launch({ actionId: 'open' })
    expect(listAgents).not.toHaveBeenCalled()
    expect(choose).not.toHaveBeenCalled()
    expect(openSession).toHaveBeenCalledTimes(1)
    expect(openSession.mock.calls[0][0].agent_id).toBeUndefined()
  })

  it('stopped task session still prompts (it will relaunch fresh)', async () => {
    listAgents.mockResolvedValue({
      agents: [{ id: 'claude', name: 'Claude' }, { id: 'codex', name: 'Codex' }],
      enabled: ['claude', 'codex'],
    })
    choose.mockResolvedValue('codex')
    sessionStatus.sessions = { t1: { state: 'stopped' } }
    const { result } = renderHook(() => useSessionLauncher(TASK))
    await result.current.launch({ actionId: 'open' })
    expect(choose).toHaveBeenCalledTimes(1)
    expect(openSession.mock.calls[0][0].agent_id).toBe('codex')
  })

  it('review session already live: reuse it, never prompt', async () => {
    listAgents.mockResolvedValue({
      agents: [{ id: 'claude', name: 'Claude' }, { id: 'codex', name: 'Codex' }],
      enabled: ['claude', 'codex'],
    })
    sessionStatus.reviews = [
      { url: 'https://github.com/o/r/pull/9', session_name: 'review-o-r-9' },
    ]
    sessionStatus.sessions = { 'review-o-r-9': { state: 'thinking' } }
    const { result } = renderHook(() => useSessionLauncher(REVIEW))
    await result.current.launch({ actionId: 'review-pr' })
    expect(listAgents).not.toHaveBeenCalled()
    expect(choose).not.toHaveBeenCalled()
    expect(openSession).toHaveBeenCalledTimes(1)
  })
})
