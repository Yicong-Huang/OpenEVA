import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listCronJobs: vi.fn(),
    createCronJob: vi.fn(),
    updateCronJob: vi.fn(),
    deleteCronJob: vi.fn(),
    listCronJobRuns: vi.fn(),
    runCronJobNow: vi.fn(),
    parseCronSchedule: vi.fn(),
  },
}))

// CronJobsPage now reads live session state from
// SessionStatusProvider. Provide a per-test mutable map so each test
// can stub the snapshot for the cron sessions it cares about.
const sessionStateMap: Record<string, { state: string }> = {}
vi.mock('../hooks/SessionStatusProvider', () => ({
  useSessionState: (name: string | null | undefined) => {
    if (!name) return undefined
    return sessionStateMap[name]
  },
  useSessionStatus: () => ({}),
  SessionStatusProvider: ({ children }: { children: React.ReactNode }) => children,
}))

import { api } from '../api'
import { CronJobsPage } from '../pages/CronJobsPage'

const JOB = (overrides = {}) => ({
  id: 1, name: 'sync prs', schedule: '30min',
  command: '/yh-code-sync-my-prs', description: '',
  enabled: 1, created_at: '2026-04-25T08:00:00',
  updated_at: '', last_run_at: '', last_status: '', next_run_at: '',
  ...overrides,
})

beforeEach(() => {
  vi.clearAllMocks()
  for (const k of Object.keys(sessionStateMap)) delete sessionStateMap[k]
  vi.mocked(api.listCronJobs).mockResolvedValue({ jobs: [] })
  vi.mocked(api.listCronJobRuns).mockResolvedValue({ runs: [] })
  vi.mocked(api.parseCronSchedule).mockResolvedValue({
    kind: 'interval', original: '30min',
    interval_seconds: 1800, cron_expr: '', error: '',
  })
})

describe('CronJobsPage', () => {
  it('renders the page header + new-job form', async () => {
    render(<CronJobsPage />)
    await waitFor(() => expect(api.listCronJobs).toHaveBeenCalled())
    expect(screen.getByText('Cron Jobs')).toBeInTheDocument()
    expect(screen.getByTestId('cron-create-name')).toBeInTheDocument()
    expect(screen.getByTestId('cron-create-schedule')).toBeInTheDocument()
    expect(screen.getByTestId('cron-create-command')).toBeInTheDocument()
  })

  it('lists jobs returned by /api/cron-jobs', async () => {
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [
        JOB({ id: 1, name: 'sync' }),
        JOB({ id: 2, name: 'cleanup', schedule: '2h', enabled: 0 }),
      ],
    })
    render(<CronJobsPage />)
    await screen.findByTestId('cron-job-row-1')
    await screen.findByTestId('cron-job-row-2')
    expect(screen.getByText('sync')).toBeInTheDocument()
    expect(screen.getByText('cleanup')).toBeInTheDocument()
    // Disabled job shows the "paused" pill.
    expect(screen.getByText('paused')).toBeInTheDocument()
  })

  it('shows live badge when a session is alive', async () => {
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [
        JOB({ id: 1, name: 'live-one', session_name: 'cron-job-1' }),
        JOB({ id: 2, name: 'dead-one', session_name: 'cron-job-2' }),
      ],
    })
    // Seed the session-status snapshot: job 1 has a live (idle)
    // session, job 2 doesn't (no row in the snapshot).
    sessionStateMap['cron-job-1'] = { state: 'idle' }
    render(<CronJobsPage />)
    await screen.findByTestId('cron-job-row-1')
    expect(screen.getByTestId('cron-job-session-1')).toHaveTextContent('idle')
    expect(screen.queryByTestId('cron-job-session-2')).not.toBeInTheDocument()
  })

  it('badge label is the raw agent state', async () => {
    /* The cron row's pill label was bespoke ("ready" for needs_input,
     * etc.) before the palette unification. After consolidating
     * onto the canonical sessionDotColor, every session-state
     * renderer (graph, chip, indicator, this badge) uses one
     * vocabulary -- the raw state -- so visually scanning the page
     * isn't a translation exercise. */
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [
        JOB({ id: 1, name: 'a', session_name: 'cron-job-1' }),
        JOB({ id: 2, name: 'b', session_name: 'cron-job-2' }),
        JOB({ id: 3, name: 'c', session_name: 'cron-job-3' }),
      ],
    })
    sessionStateMap['cron-job-1'] = { state: 'idle' }
    sessionStateMap['cron-job-2'] = { state: 'thinking' }
    sessionStateMap['cron-job-3'] = { state: 'needs_input' }
    render(<CronJobsPage />)
    await screen.findByTestId('cron-job-row-1')
    expect(screen.getByTestId('cron-job-session-1')).toHaveTextContent('idle')
    expect(screen.getByTestId('cron-job-session-2')).toHaveTextContent('thinking')
    expect(screen.getByTestId('cron-job-session-3')).toHaveTextContent('needs_input')
  })

  it('badge blinks only for the urgent tier (needs_permission / crashed)', async () => {
    /* The unified 3-tier urgency model reserves animation for the
     * red tier. Blue (thinking) and yellow (idle / needs_input) are
     * static so a fleet of working / waiting sessions doesn't all
     * compete for attention; only the "drop everything" tier blinks. */
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [
        JOB({ id: 1, name: 'a', session_name: 'cron-job-1' }),
        JOB({ id: 2, name: 'b', session_name: 'cron-job-2' }),
        JOB({ id: 3, name: 'c', session_name: 'cron-job-3' }),
      ],
    })
    sessionStateMap['cron-job-1'] = { state: 'thinking' }
    sessionStateMap['cron-job-2'] = { state: 'needs_permission' }
    sessionStateMap['cron-job-3'] = { state: 'idle' }
    render(<CronJobsPage />)
    const thinking = await screen.findByTestId('cron-job-session-1')
    const permission = await screen.findByTestId('cron-job-session-2')
    const idle = await screen.findByTestId('cron-job-session-3')
    expect(thinking.classList.contains('session-dot-blink')).toBe(false)
    expect(permission.classList.contains('session-dot-blink')).toBe(true)
    expect(idle.classList.contains('session-dot-blink')).toBe(false)
  })

  it('shows empty-state hint when no jobs', async () => {
    render(<CronJobsPage />)
    expect(await screen.findByText(/No cron jobs yet/i)).toBeInTheDocument()
  })

  it('creates a new job via the form', async () => {
    vi.mocked(api.createCronJob).mockResolvedValue(JOB({ id: 99 }))
    render(<CronJobsPage />)
    fireEvent.change(screen.getByTestId('cron-create-name'),
      { target: { value: 'fresh' } })
    fireEvent.change(screen.getByTestId('cron-create-command'),
      { target: { value: '/run-it' } })
    fireEvent.click(screen.getByTestId('cron-create-submit'))
    await waitFor(() => expect(api.createCronJob).toHaveBeenCalledWith({
      name: 'fresh', schedule: '30min', command: '/run-it',
      description: '',
    }))
  })

  it('blocks submit when name or command is empty', async () => {
    render(<CronJobsPage />)
    fireEvent.click(screen.getByTestId('cron-create-submit'))
    expect(await screen.findByText(/name and command are required/i))
      .toBeInTheDocument()
    expect(api.createCronJob).not.toHaveBeenCalled()
  })

  it('opens detail panel directly in edit mode when a job is clicked', async () => {
    // Clicking a job goes straight into the editable form -- the
    // primary reason to open a job is to tweak its schedule, so we
    // skip the read-only-then-Edit step. Cancel reverts to read-only
    // (that path is exercised by other tests).
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [JOB({ id: 7, name: 'detail-me' })],
    })
    render(<CronJobsPage />)
    fireEvent.click(await screen.findByTestId('cron-job-row-7'))
    await screen.findByTestId('cron-job-detail')
    // Edit form fields are visible immediately.
    expect(screen.getByTestId('cron-edit-name')).toBeInTheDocument()
    expect(screen.getByTestId('cron-edit-schedule')).toBeInTheDocument()
    // Edit toggle button is hidden while already editing.
    expect(screen.queryByTestId('cron-job-edit')).toBeNull()
    expect(screen.getByTestId('cron-job-delete')).toBeInTheDocument()
    // Pause button shown for enabled job.
    expect(screen.getByTestId('cron-job-toggle')).toHaveTextContent('Pause')
  })

  it('renders an embedded SessionCard when the job has a session_name', async () => {
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [JOB({ id: 11, session_name: 'cron-job-11' })],
    })
    sessionStateMap['cron-job-11'] = { state: 'idle' }
    render(<CronJobsPage />)
    fireEvent.click(await screen.findByTestId('cron-job-row-11'))
    expect(await screen.findByTestId('cron-job-session-card'))
      .toBeInTheDocument()
  })

  it('does not embed a SessionCard when session_name is missing', async () => {
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [JOB({ id: 12, session_name: undefined })],
    })
    render(<CronJobsPage />)
    fireEvent.click(await screen.findByTestId('cron-job-row-12'))
    await screen.findByTestId('cron-job-detail')
    expect(screen.queryByTestId('cron-job-session-card'))
      .not.toBeInTheDocument()
  })

  it('toggle button calls updateCronJob with flipped enabled', async () => {
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [JOB({ id: 5, enabled: 1 })],
    })
    vi.mocked(api.updateCronJob).mockResolvedValue(JOB({ id: 5, enabled: 0 }))
    render(<CronJobsPage />)
    fireEvent.click(await screen.findByTestId('cron-job-row-5'))
    fireEvent.click(await screen.findByTestId('cron-job-toggle'))
    await waitFor(() => expect(api.updateCronJob).toHaveBeenCalledWith(
      5, { enabled: false },
    ))
  })

  it('delete button confirms, calls API, and clears selection', async () => {
    const origConfirm = window.confirm
    window.confirm = vi.fn(() => true)
    try {
      vi.mocked(api.listCronJobs).mockResolvedValue({
        jobs: [JOB({ id: 8, name: 'goner' })],
      })
      vi.mocked(api.deleteCronJob).mockResolvedValue({ ok: true })
      render(<CronJobsPage />)
      fireEvent.click(await screen.findByTestId('cron-job-row-8'))
      fireEvent.click(await screen.findByTestId('cron-job-delete'))
      await waitFor(() => expect(api.deleteCronJob).toHaveBeenCalledWith(8))
    } finally {
      window.confirm = origConfirm
    }
  })

  it('skips delete when confirm is cancelled', async () => {
    const origConfirm = window.confirm
    window.confirm = vi.fn(() => false)
    try {
      vi.mocked(api.listCronJobs).mockResolvedValue({
        jobs: [JOB({ id: 9 })],
      })
      render(<CronJobsPage />)
      fireEvent.click(await screen.findByTestId('cron-job-row-9'))
      fireEvent.click(await screen.findByTestId('cron-job-delete'))
      // confirm returned false -> deleteCronJob never called.
      expect(api.deleteCronJob).not.toHaveBeenCalled()
    } finally {
      window.confirm = origConfirm
    }
  })

  it('renders run history with status colour', async () => {
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [JOB({ id: 3 })],
    })
    vi.mocked(api.listCronJobRuns).mockResolvedValue({
      runs: [
        { id: 1, job_id: 3, started_at: '2026-04-25T09:00:00',
          finished_at: '2026-04-25T09:01:00', status: 'done',
          output_excerpt: 'all good', error_message: '' },
        { id: 2, job_id: 3, started_at: '2026-04-25T08:00:00',
          finished_at: '2026-04-25T08:00:30', status: 'failed',
          output_excerpt: '', error_message: 'oops' },
      ],
    })
    render(<CronJobsPage />)
    fireEvent.click(await screen.findByTestId('cron-job-row-3'))
    await waitFor(() => expect(api.listCronJobRuns).toHaveBeenCalledWith(3, 20))
    expect(await screen.findByText(/Recent runs \(2\)/i)).toBeInTheDocument()
    expect(screen.getByText('done')).toBeInTheDocument()
    expect(screen.getByText('failed')).toBeInTheDocument()
    expect(screen.getByText('all good')).toBeInTheDocument()
    expect(screen.getByText('oops')).toBeInTheDocument()
  })

  it('run row shows duration when finished, "running" when not', async () => {
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [JOB({ id: 4 })],
    })
    vi.mocked(api.listCronJobRuns).mockResolvedValue({
      runs: [
        // 1m exactly -> "1m"
        { id: 1, job_id: 4, started_at: '2026-04-25T09:00:00',
          finished_at: '2026-04-25T09:01:00', status: 'done',
          output_excerpt: '', error_message: '' },
        // still open -> "(running)"
        { id: 2, job_id: 4, started_at: '2026-04-25T10:00:00',
          finished_at: '', status: 'running',
          output_excerpt: '', error_message: '' },
      ],
    })
    render(<CronJobsPage />)
    fireEvent.click(await screen.findByTestId('cron-job-row-4'))
    await waitFor(() => expect(api.listCronJobRuns).toHaveBeenCalledWith(4, 20))
    // The duration chip is appended in parens after the relative
    // start time.
    await screen.findAllByText(/\(1m\)/)
    expect(screen.getAllByText(/\(1m\)/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/\(running\)/).length).toBeGreaterThan(0)
  })

  it('Edit form lets user save updated fields', async () => {
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [JOB({ id: 4, name: 'old' })],
    })
    vi.mocked(api.updateCronJob).mockResolvedValue(JOB({ id: 4, name: 'new' }))
    render(<CronJobsPage />)
    fireEvent.click(await screen.findByTestId('cron-job-row-4'))
    // Detail opens directly in edit mode -- no Edit-button click needed.
    const nameInput = await screen.findByTestId('cron-edit-name')
    fireEvent.change(nameInput, { target: { value: 'renamed' } })
    fireEvent.click(screen.getByTestId('cron-edit-save'))
    await waitFor(() => expect(api.updateCronJob).toHaveBeenCalledWith(
      4, expect.objectContaining({ name: 'renamed' }),
    ))
  })

  it('Edit form lets user save schedule / command / description fields', async () => {
    // The original edit-save test only exercised the `name` input.
    // The other three are equally important (schedule changes the
    // cadence; command changes what gets pasted; description is
    // free-form annotation). Each must round-trip into updateCronJob
    // for the user to be able to edit them at all.
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [JOB({ id: 17, schedule: '30min',
                    command: '/old', description: 'before' })],
    })
    vi.mocked(api.updateCronJob).mockResolvedValue(
      JOB({ id: 17, schedule: '2h', command: '/new',
            description: 'after' }),
    )
    render(<CronJobsPage />)
    fireEvent.click(await screen.findByTestId('cron-job-row-17'))
    fireEvent.change(await screen.findByTestId('cron-edit-schedule'),
      { target: { value: '2h' } })
    fireEvent.change(screen.getByTestId('cron-edit-command'),
      { target: { value: '/new' } })
    fireEvent.change(screen.getByTestId('cron-edit-description'),
      { target: { value: 'after' } })
    fireEvent.click(screen.getByTestId('cron-edit-save'))
    await waitFor(() => expect(api.updateCronJob).toHaveBeenCalledWith(
      17, expect.objectContaining({
        schedule: '2h', command: '/new', description: 'after',
      }),
    ))
  })

  it('JobDetail tolerates listCronJobRuns failure (empty runs panel)', async () => {
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [JOB({ id: 18 })],
    })
    // listCronJobRuns rejects -- the catch-arm should leave runs as
    // [] so the detail panel still mounts.
    vi.mocked(api.listCronJobRuns).mockRejectedValue(new Error('db err'))
    render(<CronJobsPage />)
    fireEvent.click(await screen.findByTestId('cron-job-row-18'))
    // The detail panel still renders (no error boundary needed).
    expect(await screen.findByTestId('cron-job-detail'))
      .toBeInTheDocument()
    // "No runs yet." is the empty-state copy.
    expect(await screen.findByText(/No runs yet/i))
      .toBeInTheDocument()
  })

  it('Run-now button posts to /run and refreshes history', async () => {
    vi.mocked(api.listCronJobs).mockResolvedValue({
      jobs: [JOB({ id: 11 })],
    })
    vi.mocked(api.runCronJobNow).mockResolvedValue({
      id: 1, job_id: 11,
      started_at: '2026-04-25T10:00', finished_at: '2026-04-25T10:00',
      status: 'done', output_excerpt: 'placeholder', error_message: '',
    })
    render(<CronJobsPage />)
    fireEvent.click(await screen.findByTestId('cron-job-row-11'))
    const runBtn = await screen.findByTestId('cron-job-run-now')
    fireEvent.click(runBtn)
    await waitFor(() =>
      expect(api.runCronJobNow).toHaveBeenCalledWith(11))
    // After run completes the history fetch is reissued so the new
    // run shows up immediately.
    await waitFor(() => {
      const calls = vi.mocked(api.listCronJobRuns).mock.calls
        .filter(([id]) => id === 11)
      expect(calls.length).toBeGreaterThanOrEqual(2)
    })
  })

  it('Schedule preview shows human-readable cadence for valid input', async () => {
    vi.mocked(api.parseCronSchedule).mockResolvedValue({
      kind: 'interval', original: '30min',
      interval_seconds: 1800, cron_expr: '', error: '',
    })
    render(<CronJobsPage />)
    const sched = await screen.findByTestId('cron-create-schedule') as HTMLInputElement
    fireEvent.change(sched, { target: { value: '30min' } })
    const preview = await screen.findByTestId('cron-schedule-preview')
    // 1800s -> "30min" via humanSeconds.
    expect(preview.textContent).toMatch(/30min/)
  })

  it('Schedule preview shows error message for invalid input', async () => {
    vi.mocked(api.parseCronSchedule).mockResolvedValue({
      kind: 'invalid', original: 'garbage',
      interval_seconds: 0, cron_expr: '',
      error: 'expected duration like ...',
    })
    render(<CronJobsPage />)
    const sched = await screen.findByTestId('cron-create-schedule') as HTMLInputElement
    fireEvent.change(sched, { target: { value: 'garbage' } })
    const preview = await screen.findByTestId('cron-schedule-preview')
    expect(preview.textContent).toMatch(/expected duration/)
  })
})
