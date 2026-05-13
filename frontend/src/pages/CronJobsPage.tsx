import { useState, useEffect, useCallback } from 'react'
import { api, type CronJob, type CronJobRun } from '../api'
import { SessionCard } from '../components/SessionCard'
import { useEventBus } from '../hooks/useEventBus'
import { useLayoutRatios } from '../hooks/useLayoutRatios'
import { useSettingNumber } from '../hooks/useSettingNumber'
import { useSessionState } from '../hooks/SessionStatusProvider'
import { isLive, sessionDotColor, sessionDotAnim } from '../utils/sessionState'
import { timeAgo, parseBackendDate } from '../utils'

// Default 2-pane width split when a job is selected: 40% list / 60%
// detail. Configurable via `ui.layout.cron_jobs_col_ratios`.
const DEFAULT_CRON_RATIOS = [40, 60] as const

// JobCard history-strip horizon. Default mirrors the previous
// `api.listCronJobRuns(id)` default; bounds match the route clamp.
const DEFAULT_CRON_RUNS_HISTORY_LIMIT = 20

/**
 * Cron Jobs page: each job is a "long-running agent" card -- the user
 * configures a schedule + command (e.g. /loop 30min /yh-code-sync-my-prs)
 * and Eva fires it on cadence. The page lists every job, lets the user
 * CRUD them, and shows recent run history per card.
 *
 * Layout mirrors ReviewsPage: list on the left, expanded detail on the
 * right when one job is selected. New-job form lives at the top of the
 * left column for quick capture.
 */

const DEFAULT_DRAFT = {
  name: '',
  schedule: '30min',
  command: '',
  description: '',
}


interface CronJobsPageProps {
  // Selection lives in App-level state (and round-trips through the
  // URL via `?cron_job=<id>`) so refresh / deep-link / back-forward
  // all land on the same job. Old form (no props) is preserved for
  // backwards-compat with the test suite that renders the page in
  // isolation.
  selectedJobId?: number | null
  onSelectJob?: (id: number | null) => void
}

export function CronJobsPage({
  selectedJobId,
  onSelectJob,
}: CronJobsPageProps = {}) {
  const [jobs, setJobs] = useState<CronJob[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Local fallback when used standalone (no props): keeps the test
  // suite's existing render-without-context calls working.
  const [localSelectedId, setLocalSelectedId] = useState<number | null>(null)
  const selectedId = selectedJobId !== undefined
    ? selectedJobId
    : localSelectedId
  const setSelectedId = useCallback((id: number | null) => {
    if (onSelectJob) onSelectJob(id)
    else setLocalSelectedId(id)
  }, [onSelectJob])
  const [draft, setDraft] = useState(DEFAULT_DRAFT)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const r = await api.listCronJobs()
      setJobs(r.jobs)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // Re-fetch the job list whenever the agent fires a hook for a cron-job
  // session: that's when session_alive / session_status flip and the
  // 'live' badge needs to repaint. Filter on the session prefix so
  // events from unrelated sessions (review-*, task-*) don't churn.
  useEventBus('agent.*', useCallback((event: Record<string, unknown>) => {
    const session = String(event.session || '')
    if (session.startsWith('cron-job-')) {
      refresh()
    }
  }, [refresh]))

  const onCreate = useCallback(async () => {
    if (!draft.name.trim() || !draft.command.trim()) {
      setCreateError('name and command are required')
      return
    }
    setCreating(true)
    setCreateError(null)
    try {
      await api.createCronJob({
        name: draft.name.trim(),
        schedule: draft.schedule.trim(),
        command: draft.command.trim(),
        description: draft.description.trim(),
      })
      setDraft(DEFAULT_DRAFT)
      await refresh()
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : 'Create failed')
    } finally {
      setCreating(false)
    }
  }, [draft, refresh])

  const selected = jobs?.find((j) => j.id === selectedId) ?? null
  const [listRatio, detailRatio] = useLayoutRatios(
    'ui.layout.cron_jobs_col_ratios',
    [...DEFAULT_CRON_RATIOS],
  )

  return (
    <div data-testid="cron-jobs-page" style={{
      display: 'flex', height: '100%', overflow: 'hidden',
    }}>
      <div style={{
        width: selected ? `${listRatio}%` : '100%',
        overflowY: 'auto', padding: 12, flexShrink: 0,
        transition: 'width 0.2s',
        borderRight: selected ? '1px solid var(--border)' : undefined,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <span style={{ fontSize: 14, fontWeight: 700 }}>Cron Jobs</span>
          <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>
            {jobs ? `${jobs.length} job(s)` : ''}
          </span>
          <button
            className="btn-action"
            style={{ fontSize: 11, padding: '2px 8px', marginLeft: 'auto' }}
            onClick={refresh}
            data-testid="cron-jobs-refresh"
          >Refresh</button>
        </div>

        <CreateJobForm
          draft={draft} setDraft={setDraft}
          onSubmit={onCreate}
          creating={creating} error={createError}
        />

        {error && (
          <div style={{ color: 'var(--red)', fontSize: 12, marginBottom: 8 }}>
            {error}
          </div>
        )}
        {jobs && jobs.length === 0 && !error && (
          <div style={{ color: 'var(--text-dim)', fontSize: 12, marginTop: 12 }}>
            No cron jobs yet. Create one above.
          </div>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
          {jobs?.map((j) => (
            <JobCard
              key={j.id}
              job={j}
              selected={selectedId === j.id}
              onClick={() => setSelectedId(j.id === selectedId ? null : j.id)}
            />
          ))}
        </div>
      </div>

      {selected && (
        <div data-testid="cron-job-detail" style={{
          width: `${detailRatio}%`, overflowY: 'auto', padding: 12, flexShrink: 0,
        }}>
          <CronCard
            job={selected}
            onChanged={refresh}
            onDeleted={() => { setSelectedId(null); refresh() }}
          />
        </div>
      )}
    </div>
  )
}


function CreateJobForm({
  draft, setDraft, onSubmit, creating, error,
}: {
  draft: typeof DEFAULT_DRAFT
  setDraft: (d: typeof DEFAULT_DRAFT) => void
  onSubmit: () => void
  creating: boolean
  error: string | null
}) {
  const update = (k: keyof typeof DEFAULT_DRAFT, v: string) =>
    setDraft({ ...draft, [k]: v })
  const preview = useSchedulePreview(draft.schedule)
  const scheduleBorder =
    preview.kind === 'invalid' && draft.schedule.trim()
      ? 'var(--red)' : 'var(--border)'
  return (
    <div style={{
      border: '1px solid var(--border)', borderRadius: 6,
      padding: 10, marginBottom: 12, background: 'var(--card-bg)',
    }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--accent)', marginBottom: 6 }}>
        New job
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        <input
          data-testid="cron-create-name"
          placeholder="Name (e.g. sync my PRs)"
          value={draft.name}
          onChange={(e) => update('name', e.target.value)}
          style={INPUT_STYLE}
        />
        <div style={{ display: 'flex', gap: 4 }}>
          <input
            data-testid="cron-create-schedule"
            placeholder="Schedule (30min / 2h / 0 9 * * *)"
            value={draft.schedule}
            onChange={(e) => update('schedule', e.target.value)}
            style={{ ...INPUT_STYLE, flex: 1, border: `1px solid ${scheduleBorder}` }}
          />
          <input
            data-testid="cron-create-command"
            placeholder="/yh-code-sync-my-prs"
            value={draft.command}
            onChange={(e) => update('command', e.target.value)}
            style={{ ...INPUT_STYLE, flex: 2 }}
          />
        </div>
        <SchedulePreview preview={preview} text={draft.schedule} />
        <input
          data-testid="cron-create-description"
          placeholder="Description (optional)"
          value={draft.description}
          onChange={(e) => update('description', e.target.value)}
          style={INPUT_STYLE}
        />
      </div>
      {error && (
        <div style={{ color: 'var(--red)', fontSize: 10, marginTop: 4 }}>{error}</div>
      )}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 6 }}>
        <button
          className="btn-action accent"
          data-testid="cron-create-submit"
          onClick={onSubmit}
          disabled={creating}
          style={{ fontSize: 11 }}
        >{creating ? 'Creating...' : 'Create job'}</button>
      </div>
    </div>
  )
}


type SchedulePreviewState = {
  kind: 'idle' | 'interval' | 'cron' | 'invalid'
  intervalSeconds: number
  cronExpr: string
  error: string
}


/**
 * Debounced preview of a schedule string. Hits the same parser the
 * backend uses so the form's "every 30 minutes" hint always matches
 * what eva actually saves.
 */
function useSchedulePreview(text: string): SchedulePreviewState {
  const [state, setState] = useState<SchedulePreviewState>({
    kind: 'idle', intervalSeconds: 0, cronExpr: '', error: '',
  })
  useEffect(() => {
    const trimmed = text.trim()
    if (!trimmed) {
      setState({ kind: 'idle', intervalSeconds: 0, cronExpr: '', error: '' })
      return
    }
    const t = window.setTimeout(() => {
      api.parseCronSchedule(trimmed).then((r) => {
        setState({
          kind: r.kind, intervalSeconds: r.interval_seconds,
          cronExpr: r.cron_expr, error: r.error,
        })
      }).catch(() => { /* leave state as-is on transient failure */ })
    }, 250)
    return () => window.clearTimeout(t)
  }, [text])
  return state
}


function SchedulePreview({
  preview, text,
}: { preview: SchedulePreviewState; text: string }) {
  if (preview.kind === 'idle' || !text.trim()) return null
  if (preview.kind === 'invalid') {
    return (
      <div data-testid="cron-schedule-preview"
           style={{ fontSize: 9, color: 'var(--red)' }}>
        {preview.error || 'invalid schedule'}
      </div>
    )
  }
  const human = preview.kind === 'interval'
    ? `fires every ${humanSeconds(preview.intervalSeconds)}`
    : `cron: ${preview.cronExpr}`
  return (
    <div data-testid="cron-schedule-preview"
         style={{ fontSize: 9, color: 'var(--text-dim)' }}>
      {human}
    </div>
  )
}


function humanSeconds(s: number): string {
  if (s < 60) return `${s}s`
  if (s < 3600) return `${s / 60}min`
  if (s < 86400) return `${s / 3600}h`
  return `${s / 86400}d`
}


function JobCard({
  job, selected, onClick,
}: { job: CronJob; selected: boolean; onClick: () => void }) {
  const enabled = job.enabled === 1
  const statusColor = job.last_status === 'done' ? 'var(--green)'
    : job.last_status === 'failed' ? 'var(--red)'
    : job.last_status === 'running' ? 'var(--yellow)'
    : 'var(--text-dim)'
  // Live session state comes from the central snapshot, not from any
  // per-row enrichment on the cron payload. The job carries
  // `session_name` purely as a key into the snapshot.
  const sessionRow = useSessionState(job.session_name)
  const sessionState = sessionRow?.state ?? ''
  const sessionAlive = isLive(sessionState)
  return (
    <div
      data-testid={`cron-job-row-${job.id}`}
      onClick={onClick}
      style={{
        border: `1px solid ${selected ? 'var(--accent)' : 'var(--border)'}`,
        outline: selected ? '1px solid var(--accent)' : undefined,
        borderRadius: 6, padding: 8, cursor: 'pointer',
        background: 'var(--card-bg)',
        opacity: enabled ? 1 : 0.55,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{job.name}</span>
        {sessionAlive && (() => {
          // Color comes from the canonical 3-tier palette in
          // `utils/sessionState.ts` -- same map used by GraphView,
          // SessionCard, LiveSessionChip, etc., so the cron row's
          // pill matches whatever shade the rest of the page paints
          // for the same session state. Label is just the raw state.
          const fg = sessionDotColor(sessionState)
          const animClass = sessionDotAnim(sessionState)
          return (
            <span
              data-testid={`cron-job-session-${job.id}`}
              title={`tmux ${job.session_name} -> ${sessionState || 'live'}`}
              className={animClass}
              style={{
                fontSize: 9, color: fg,
                border: `1px solid ${fg}`, padding: '0 5px',
                borderRadius: 3, fontFamily: 'monospace',
              }}>{sessionState || 'live'}</span>
          )
        })()}
        <span style={{
          fontSize: 9, fontFamily: 'monospace',
          background: 'var(--panel-bg)', padding: '1px 6px', borderRadius: 4,
          color: 'var(--text-dim)',
        }}>{job.schedule}</span>
        {!enabled && (
          <span style={{
            fontSize: 9, color: 'var(--text-dim)',
            border: '1px solid var(--border)', padding: '0 5px', borderRadius: 3,
          }}>paused</span>
        )}
      </div>
      <div style={{
        fontSize: 10, color: 'var(--text-dim)', fontFamily: 'monospace',
        marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}>{job.command}</div>
      {job.last_status && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 4 }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%', background: statusColor,
          }} />
          <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>
            last: {job.last_status}{job.last_run_at ? ` (${job.last_run_at})` : ''}
          </span>
        </div>
      )}
    </div>
  )
}


/**
 * CronCard -- the per-cron-job detail card.
 *
 * Originally lived inside CronJobsPage as `JobDetail` (right-pane
 * detail). Promoted to an exported component so the All Live Tasks
 * page can render it inline alongside TaskCard / ReviewCard --
 * lets the user manage cron sessions without leaving All Live Tasks.
 */
export function CronCard({
  job, onChanged, onDeleted,
}: {
  job: CronJob
  onChanged: () => void
  onDeleted: () => void
}) {
  const [runs, setRuns] = useState<CronJobRun[]>([])
  // Open in edit mode by default -- the primary reason to click a
  // job in the list is to tweak the schedule / command, so jumping
  // straight into the editable form removes the "select then click
  // Edit" extra step. User can hit "Cancel" to drop into the read-
  // only summary view if they only wanted to inspect.
  const [editing, setEditing] = useState(true)
  // Reset to "edit on open" whenever a different job is selected --
  // otherwise switching from a job you cancelled out of would inherit
  // that read-only state.
  useEffect(() => { setEditing(true) }, [job.id])
  const [running, setRunning] = useState(false)
  const runsHistoryLimit = useSettingNumber(
    'ui.cron_jobs.runs_history_limit',
    DEFAULT_CRON_RUNS_HISTORY_LIMIT,
    { min: 5, max: 500 },
  )

  const refreshRuns = useCallback(() => {
    api.listCronJobRuns(job.id, runsHistoryLimit)
      .then((r) => setRuns(r.runs))
      .catch(() => setRuns([]))
  }, [job.id, runsHistoryLimit])

  useEffect(() => { refreshRuns() }, [refreshRuns])

  const toggleEnabled = useCallback(async () => {
    await api.updateCronJob(job.id, { enabled: job.enabled !== 1 })
    onChanged()
  }, [job, onChanged])

  const onRunNow = useCallback(async () => {
    setRunning(true)
    try {
      await api.runCronJobNow(job.id)
      refreshRuns()
      onChanged()
    } finally {
      setRunning(false)
    }
  }, [job.id, refreshRuns, onChanged])

  const onDelete = useCallback(async () => {
    if (!window.confirm(`Delete job '${job.name}'? Run history is also deleted.`)) {
      return
    }
    await api.deleteCronJob(job.id)
    onDeleted()
  }, [job, onDeleted])

  return (
    <div>
      {editing ? (
        <EditForm job={job} onDone={() => { setEditing(false); onChanged() }} />
      ) : (
        <ReadOnlyHeader job={job} />
      )}
      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
        <button
          className="btn-action accent"
          data-testid="cron-job-run-now"
          onClick={onRunNow}
          disabled={running}
          style={{ fontSize: 11 }}
        >{running ? 'Running...' : 'Run now'}</button>
        {!editing && (
          <button
            className="btn-action"
            data-testid="cron-job-edit"
            onClick={() => setEditing(true)}
            style={{ fontSize: 11 }}
          >Edit</button>
        )}
        <button
          className="btn-action"
          data-testid="cron-job-toggle"
          onClick={toggleEnabled}
          style={{ fontSize: 11 }}
        >{job.enabled === 1 ? 'Pause' : 'Resume'}</button>
        <button
          className="btn-action"
          data-testid="cron-job-delete"
          onClick={onDelete}
          style={{ fontSize: 11, color: 'var(--red)' }}
        >Delete</button>
      </div>

      {job.session_name && (
        <CronSessionPanel job={job} onChanged={onChanged} />
      )}

      <div style={{ marginTop: 16 }}>
        <div style={{
          fontSize: 11, fontWeight: 700, color: 'var(--accent)',
          textTransform: 'uppercase', letterSpacing: 0.5,
          marginBottom: 6,
        }}>Recent runs ({runs.length})</div>
        {runs.length === 0 && (
          <div style={{ color: 'var(--text-dim)', fontSize: 11 }}>No runs yet.</div>
        )}
        {runs.map((r) => <RunRow key={r.id} run={r} />)}
      </div>
    </div>
  )
}


/**
 * Live-session panel for a single cron job. Reads state from the
 * global session-status snapshot via `useSessionState`. The cron
 * row payload only carries `session_name`; `session_alive` and
 * `session_status` are derived here, not transmitted.
 */
function CronSessionPanel({
  job, onChanged,
}: { job: CronJob; onChanged: () => void }) {
  const sessionRow = useSessionState(job.session_name)
  const state = sessionRow?.state ?? ''
  const alive = isLive(state)
  return (
    <div style={{ marginTop: 16 }} data-testid="cron-job-session-card">
      <div style={{
        fontSize: 11, fontWeight: 700, color: 'var(--accent)',
        textTransform: 'uppercase', letterSpacing: 0.5,
        marginBottom: 6,
      }}>Live session</div>
      <SessionCard
        sessionName={job.session_name!}
        // The snapshot service drives `state` via SSE; pass the
        // current value as initialStatus so the card paints the right
        // colour on first render. SessionCard itself reads the
        // snapshot too, so this becomes a no-op once mounted.
        initialStatus={state || (alive ? 'idle' : 'stopped')}
        autoExpand={alive}
        onKill={onChanged}
      />
    </div>
  )
}


function ReadOnlyHeader({ job }: { job: CronJob }) {
  // Snapshot-driven liveness for the "alive / dead" badge. Replaces
  // the old `job.session_alive` / `job.session_status` row fields.
  const sessionRow = useSessionState(job.session_name)
  const sessionAlive = isLive(sessionRow?.state ?? '')
  return (
    <div>
      <div style={{ fontSize: 16, fontWeight: 700 }}>{job.name}</div>
      {job.description && (
        <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>
          {job.description}
        </div>
      )}
      <div style={{ display: 'flex', gap: 12, marginTop: 8, fontSize: 11 }}>
        <KV k="Schedule" v={job.schedule} mono />
        <KV k="Command" v={job.command} mono />
      </div>
      <div style={{ display: 'flex', gap: 12, marginTop: 4, fontSize: 11,
                    alignItems: 'center', flexWrap: 'wrap' }}>
        <KV k="Created" v={job.created_at} />
        {job.last_run_at && <KV k="Last run" v={job.last_run_at} />}
        {job.session_name && (
          <span data-testid="cron-job-session-info"
                style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <span style={{ color: 'var(--text-dim)' }}>Session:</span>
            <span style={{ fontFamily: 'monospace' }}>{job.session_name}</span>
            <span style={{
              fontSize: 9, padding: '0 5px', borderRadius: 3,
              border: `1px solid ${sessionAlive ? 'var(--green)' : 'var(--text-dim)'}`,
              color: sessionAlive ? 'var(--green)' : 'var(--text-dim)',
            }}>{sessionAlive ? 'alive' : 'dead'}</span>
            {sessionAlive && (
              <span style={{
                fontSize: 10, fontFamily: 'monospace',
                color: 'var(--text-dim)',
              }}>
                ({`tmux a -t ${job.session_name}`})
              </span>
            )}
          </span>
        )}
      </div>
    </div>
  )
}


function KV({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div>
      <span style={{ color: 'var(--text-dim)' }}>{k}: </span>
      <span style={{ fontFamily: mono ? 'monospace' : 'inherit' }}>{v}</span>
    </div>
  )
}


function EditForm({ job, onDone }: { job: CronJob; onDone: () => void }) {
  const [draft, setDraft] = useState({
    name: job.name, schedule: job.schedule,
    command: job.command, description: job.description,
  })
  const [saving, setSaving] = useState(false)
  const update = (k: keyof typeof draft, v: string) =>
    setDraft({ ...draft, [k]: v })
  const save = async () => {
    setSaving(true)
    try {
      await api.updateCronJob(job.id, draft)
      onDone()
    } finally {
      setSaving(false)
    }
  }
  return (
    <div style={{ display: 'grid', gap: 4 }}>
      <input data-testid="cron-edit-name" value={draft.name}
             onChange={(e) => update('name', e.target.value)} style={INPUT_STYLE} />
      <input data-testid="cron-edit-schedule" value={draft.schedule}
             onChange={(e) => update('schedule', e.target.value)} style={INPUT_STYLE} />
      <input data-testid="cron-edit-command" value={draft.command}
             onChange={(e) => update('command', e.target.value)} style={INPUT_STYLE} />
      <input data-testid="cron-edit-description" value={draft.description}
             onChange={(e) => update('description', e.target.value)} style={INPUT_STYLE} />
      <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
        <button className="btn-action accent" data-testid="cron-edit-save"
                onClick={save} disabled={saving}
                style={{ fontSize: 11 }}>
          {saving ? 'Saving...' : 'Save'}
        </button>
        <button className="btn-action" onClick={onDone}
                style={{ fontSize: 11 }}>Cancel</button>
      </div>
    </div>
  )
}


/** Format the elapsed time between two backend timestamps as a
 * compact human string ("4s", "2m 13s", "1h 4m"). Returns ''
 * when either side is empty / unparseable -- the row's caller
 * decides what to render in that case (e.g. "running"). */
function formatDuration(startIso: string, endIso: string): string {
  if (!startIso || !endIso) return ''
  const a = parseBackendDate(startIso).getTime()
  const b = parseBackendDate(endIso).getTime()
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return ''
  let secs = Math.floor((b - a) / 1000)
  if (secs < 1) return '<1s'
  if (secs < 60) return `${secs}s`
  const h = Math.floor(secs / 3600); secs -= h * 3600
  const m = Math.floor(secs / 60); secs -= m * 60
  if (h > 0) return secs > 0 || m > 0 ? `${h}h ${m}m` : `${h}h`
  return secs > 0 ? `${m}m ${secs}s` : `${m}m`
}


function RunRow({ run }: { run: CronJobRun }) {
  const color = run.status === 'done' ? 'var(--green)'
    : run.status === 'failed' ? 'var(--red)'
    : run.status === 'cancelled' ? 'var(--text-dim)'
    : 'var(--yellow)'
  // Friendlier than the raw ISO timestamps the backend stores. The
  // tooltip preserves the absolute value for users who want it.
  const startedAgo = timeAgo(run.started_at)
  const dur = formatDuration(run.started_at, run.finished_at)
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 2,
      borderLeft: `2px solid ${color}`, paddingLeft: 6, marginBottom: 6,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
        <span style={{ color, fontWeight: 600 }}>{run.status}</span>
        <span title={`${run.started_at}${run.finished_at ? ' -> ' + run.finished_at : ''}`}
              style={{ color: 'var(--text-dim)', fontFamily: 'monospace' }}>
          {startedAgo}{dur ? ` (${dur})` : run.finished_at ? '' : ' (running)'}
        </span>
      </div>
      {run.output_excerpt && (
        <pre style={{
          fontSize: 10, fontFamily: 'monospace',
          color: 'var(--text-dim)', whiteSpace: 'pre-wrap',
          margin: 0, maxHeight: 80, overflowY: 'auto',
          background: 'var(--panel-bg)', padding: 4, borderRadius: 3,
        }}>{run.output_excerpt}</pre>
      )}
      {run.error_message && (
        <div style={{ fontSize: 10, color: 'var(--red)' }}>
          {run.error_message}
        </div>
      )}
    </div>
  )
}


const INPUT_STYLE: React.CSSProperties = {
  padding: '4px 6px', fontSize: 11,
  background: 'var(--panel-bg)', border: '1px solid var(--border)',
  borderRadius: 4, color: 'var(--text)',
  fontFamily: 'inherit',
}
