import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api } from '../api'

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

function mockOk(data: unknown) {
  mockFetch.mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  })
}

function mock204() {
  mockFetch.mockResolvedValue({
    ok: true,
    status: 204,
    json: () => Promise.resolve(null),
    text: () => Promise.resolve(''),
  })
}

beforeEach(() => mockFetch.mockReset())

describe('api', () => {
  it('getProjects fetches /api/projects', async () => {
    mockOk({ projects: [] })
    const result = await api.getProjects()
    expect(result.projects).toEqual([])
    expect(mockFetch).toHaveBeenCalledWith('/api/projects', undefined)
  })

  it('getProject encodes the project ID', async () => {
    mockOk({ id: 'my project' })
    await api.getProject('my project')
    expect(mockFetch.mock.calls[0][0]).toBe('/api/projects/my%20project')
  })

  it('getGraph calls correct URL', async () => {
    mockOk({ nodes: [], edges: [] })
    await api.getGraph('proj-1')
    expect(mockFetch.mock.calls[0][0]).toBe('/api/projects/proj-1/graph')
  })

  it('closeTask sends POST with reason', async () => {
    mockOk({ status: 'closed' })
    await api.closeTask('proj', 'task-1', 'duplicate')
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/projects/proj/tasks/task-1/close')
    expect(opts.method).toBe('POST')
    expect(JSON.parse(opts.body)).toEqual({ reason: 'duplicate' })
  })

  it('checkStatus sends POST', async () => {
    mockOk({ changed: true, old_status: 'in_progress', new_status: 'done' })
    const result = await api.checkStatus('proj', 'task-1')
    expect(result.changed).toBe(true)
    expect(mockFetch.mock.calls[0][0]).toContain('/check-status')
  })

  it('openSession sends correct body', async () => {
    mockOk({ session: 'sess-1', new: true, background_sent: false, prompt: 'test' })
    await api.openSession({ task_id: 't1', project_id: 'p1', action_id: 'open' })
    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body.task_id).toBe('t1')
    expect(body.action_id).toBe('open')
  })

  it('killSession sends DELETE', async () => {
    mock204()
    await api.killSession('sess-1')
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/sessions/sess-1')
    expect(opts.method).toBe('DELETE')
  })

  it('waitReady uses default timeout', async () => {
    mockOk({ ready: true })
    await api.waitReady('sess-1')
    expect(mockFetch.mock.calls[0][0]).toContain('timeout=30')
  })

  it('waitReady accepts custom timeout', async () => {
    mockOk({ ready: true })
    await api.waitReady('sess-1', 10)
    expect(mockFetch.mock.calls[0][0]).toContain('timeout=10')
  })

  it('getAllPRs includes status and search params', async () => {
    mockOk({ groups: {} })
    await api.getAllPRs('merged', 'repo')
    expect(mockFetch.mock.calls[0][0]).toContain('status=merged')
    expect(mockFetch.mock.calls[0][0]).toContain('search=repo')
  })

  it('getPRDetail includes repo and number', async () => {
    mockOk({ number: 42, title: 'test' })
    await api.getPRDetail('org/repo', 42)
    expect(mockFetch.mock.calls[0][0]).toContain('repo=org%2Frepo')
    expect(mockFetch.mock.calls[0][0]).toContain('number=42')
  })

  it('getActions includes context param', async () => {
    mockOk({ actions: [] })
    await api.getActions('task')
    expect(mockFetch.mock.calls[0][0]).toContain('context=task')
  })

  it('getLiveStats adds refresh param when true', async () => {
    mockOk({})
    await api.getLiveStats(true)
    expect(mockFetch.mock.calls[0][0]).toContain('refresh=1')
  })

  it('getLiveStats omits refresh param when false', async () => {
    mockOk({})
    await api.getLiveStats(false)
    expect(mockFetch.mock.calls[0][0]).not.toContain('refresh')
  })

  it('sendTerminalInput sends POST with text body', async () => {
    mockFetch.mockResolvedValue({ ok: true })
    await api.sendTerminalInput('sess-1', 'ls\r')
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/terminal/sess-1/input')
    expect(opts.method).toBe('POST')
    expect(opts.body).toBe('ls\r')
  })

  it('resizeTerminal sends POST with rows and cols', async () => {
    mockFetch.mockResolvedValue({ ok: true })
    await api.resizeTerminal('sess-1', 24, 80)
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toContain('rows=24')
    expect(url).toContain('cols=80')
    expect(opts.method).toBe('POST')
  })

  it('throws on non-OK response', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 404,
      text: () => Promise.resolve('Not found'),
    })
    await expect(api.getProjects()).rejects.toThrow('404: Not found')
  })

  it('returns null for 204 responses', async () => {
    mock204()
    const result = await api.killSession('sess-1')
    expect(result).toBeNull()
  })

  it('getUsageHistory includes days param', async () => {
    mockOk({ history: [], total_records: 0 })
    await api.getUsageHistory(7)
    expect(mockFetch.mock.calls[0][0]).toContain('days=7')
  })

  it('renewCert posts to correct URL', async () => {
    mockOk({})
    await api.renewCert('mycert')
    expect(mockFetch.mock.calls[0][0]).toBe('/api/certs/renew/mycert')
  })

  it('updatePRTitle posts correct body', async () => {
    mockOk({ ok: true })
    await api.updatePRTitle('org/repo', 42, 'New Title')
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/pr-title')
    expect(opts.method).toBe('POST')
    const body = JSON.parse(opts.body)
    expect(body.repo).toBe('org/repo')
    expect(body.number).toBe(42)
    expect(body.title).toBe('New Title')
  })

  it('getPRDiff includes repo and number params', async () => {
    mockOk({ files: { 'a.py': '+line' } })
    await api.getPRDiff('org/repo', 10)
    const url = mockFetch.mock.calls[0][0]
    expect(url).toContain('repo=org%2Frepo')
    expect(url).toContain('number=10')
  })

  it('getTask fetches correct path', async () => {
    mockOk({ task_id: 't1' })
    await api.getTask('proj', 't1')
    expect(mockFetch.mock.calls[0][0]).toBe('/api/projects/proj/tasks/t1')
  })

  it('createTask posts body', async () => {
    mockOk({ task_id: 'new' })
    await api.createTask('proj', { id: 'new', description: 'A task' })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/projects/proj/tasks')
    expect(opts.method).toBe('POST')
    const body = JSON.parse(opts.body)
    expect(body.id).toBe('new')
    expect(body.description).toBe('A task')
  })

  it('addDep posts correct body', async () => {
    mockOk({ ok: true })
    await api.addDep('proj', 't1', 't2')
    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body.depends_on).toBe('t2')
  })

  it('removeDep sends DELETE', async () => {
    mock204()
    await api.removeDep('proj', 't1', 't2')
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/projects/proj/tasks/t1/deps/t2')
    expect(opts.method).toBe('DELETE')
  })

  it('rebuildSessions posts', async () => {
    mockOk({ rebuilt: ['a'], skipped: [] })
    const result = await api.rebuildSessions()
    expect(result.rebuilt).toEqual(['a'])
    expect(mockFetch.mock.calls[0][1].method).toBe('POST')
  })

  it('killSessionsByStatus posts statuses', async () => {
    mockOk({ killed: ['s1'] })
    await api.killSessionsByStatus(['idle', 'stopped'])
    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body.statuses).toEqual(['idle', 'stopped'])
  })

  it('updatePRBody posts repo, number, body', async () => {
    mockOk({ ok: true })
    await api.updatePRBody('org/repo', 5, 'New body')
    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body.repo).toBe('org/repo')
    expect(body.number).toBe(5)
    expect(body.body).toBe('New body')
  })

  it('refreshPR posts to correct URL', async () => {
    mockOk({ ok: true })
    await api.refreshPR(42)
    expect(mockFetch.mock.calls[0][0]).toBe('/api/pr-refresh/42')
    expect(mockFetch.mock.calls[0][1].method).toBe('POST')
  })

  it('lookupPR fetches correct URL', async () => {
    mockOk({ found: true, project: 'proj', task_id: 't1', number: 42 })
    const result = await api.lookupPR(42)
    expect(result.found).toBe(true)
    expect(mockFetch.mock.calls[0][0]).toBe('/api/pr-lookup/42')
  })

  it('replyToComment posts correct body', async () => {
    mockOk({ ok: true })
    await api.replyToComment('org/repo', 10, 555, 'Reply text', true)
    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body.repo).toBe('org/repo')
    expect(body.number).toBe(10)
    expect(body.comment_id).toBe(555)
    expect(body.body).toBe('Reply text')
    expect(body.is_review_comment).toBe(true)
  })

  it('editComment posts correct body', async () => {
    mockOk({ ok: true })
    await api.editComment('org/repo', 555, 'Edited body', false)
    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body.repo).toBe('org/repo')
    expect(body.comment_id).toBe(555)
    expect(body.body).toBe('Edited body')
    expect(body.is_review_comment).toBe(false)
  })

  it('resolveThread posts correct body', async () => {
    mockOk({ ok: true })
    await api.resolveThread('thread-123', true)
    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body.thread_id).toBe('thread-123')
    expect(body.resolve).toBe(true)
  })

  it('getEvents includes limit param', async () => {
    mockOk({ events: [], unread: 0, total: 0 })
    await api.getEvents(50)
    expect(mockFetch.mock.calls[0][0]).toContain('limit=50')
  })

  it('markEventsRead posts opts', async () => {
    mockOk({})
    await api.markEventsRead({ ids: ['a', 'b'] })
    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body.ids).toEqual(['a', 'b'])
  })

  it('markEventsRead with no opts posts empty object', async () => {
    mockOk({})
    await api.markEventsRead()
    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body).toEqual({})
  })

  it('getCerts fetches /api/certs', async () => {
    mockOk({ cert1: 'valid' })
    await api.getCerts()
    expect(mockFetch.mock.calls[0][0]).toBe('/api/certs')
  })

  it('getBoba fetches /api/boba', async () => {
    mockOk({ active: false, message: 'none' })
    const result = await api.getBoba()
    expect(result.active).toBe(false)
  })

  it('getUsage fetches /api/usage', async () => {
    mockOk({ daily: 100 })
    await api.getUsage()
    expect(mockFetch.mock.calls[0][0]).toBe('/api/usage')
  })

  it('getForkable fetches /api/forkable', async () => {
    mockOk({ today: { meal: 'tacos' } })
    const result = await api.getForkable()
    expect(result.today.meal).toBe('tacos')
  })

  it('getWorkstats fetches /api/workstats', async () => {
    mockOk({ commits: 42 })
    const result = await api.getWorkstats()
    expect((result as Record<string, unknown>).commits).toBe(42)
  })

  it('getUberEats fetches /api/ubereats', async () => {
    mockOk({ orders: [] })
    const result = await api.getUberEats()
    expect((result as Record<string, unknown>).orders).toEqual([])
  })

  it('getAllPRs default params', async () => {
    mockOk({ groups: {} })
    await api.getAllPRs()
    const url = mockFetch.mock.calls[0][0]
    expect(url).toContain('status=open')
    expect(url).toContain('search=')
  })

  it('error includes status and body text', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      text: () => Promise.resolve('Internal Server Error'),
    })
    await expect(api.getProjects()).rejects.toThrow('500: Internal Server Error')
  })
})
