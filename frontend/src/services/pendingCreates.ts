/**
 * Module-scoped pending-create-task store. Lives outside any React
 * component so an in-flight smart-create fetch keeps streaming even if
 * the user navigates away from the graph or the project. Subscribers
 * (GraphView) get notified on state changes via `subscribe`.
 *
 * Persistence: state mirrors to localStorage so a hard refresh
 * preserves "newly created" markers (the actual fetch is gone of
 * course; refresh = network request lost. We keep the marker visible
 * for the same NEW_BADGE_TTL_MS window so the user knows what they
 * just made even after a reload).
 */

export type PendingState = 'creating' | 'done' | 'failed'

export interface PendingCreate {
  draftId: string             // local UUID
  projectId: string
  context: string
  manualId?: string
  manualDesc?: string
  state: PendingState
  log: string[]
  taskId?: string             // populated when smart-create returns
  errorMsg?: string
  startedAt: number
  completedAt?: number
  position?: { x: number; y: number }
}

const LS_KEY = 'eva-pending-creates'

// How long to keep the "NEW" badge after a successful create. After
// this, the entry auto-evicts on the next subscriber notification.
export const NEW_BADGE_TTL_MS = 5 * 60 * 1000   // 5 minutes

const _store: Record<string, PendingCreate> = {}
const _listeners = new Set<() => void>()

// Hydrate from LS on module load.
try {
  const raw = localStorage.getItem(LS_KEY)
  if (raw) {
    const parsed = JSON.parse(raw) as Record<string, PendingCreate>
    for (const [k, v] of Object.entries(parsed)) {
      // Don't restore stuck-in-creating entries past one hour --
      // those are almost certainly orphans from a tab that was
      // closed mid-stream.
      if (v.state === 'creating' && Date.now() - v.startedAt > 60 * 60 * 1000) {
        continue
      }
      _store[k] = v
    }
  }
} catch { /* ignore corrupt LS */ }


function _persist() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(_store))
  } catch { /* quota / disabled */ }
}

function _notify() {
  _persist()
  for (const cb of _listeners) cb()
}


/** Returns active pending creates, optionally filtered to a project.
 *  Auto-evicts entries past their NEW_BADGE_TTL_MS. */
export function listPending(projectId?: string): PendingCreate[] {
  const now = Date.now()
  let evicted = false
  for (const [k, p] of Object.entries(_store)) {
    if (p.state !== 'creating' && p.completedAt &&
        now - p.completedAt > NEW_BADGE_TTL_MS) {
      delete _store[k]
      evicted = true
    }
  }
  if (evicted) _persist()
  const all = Object.values(_store)
  return projectId ? all.filter(p => p.projectId === projectId) : all
}


export function subscribe(cb: () => void): () => void {
  _listeners.add(cb)
  return () => { _listeners.delete(cb) }
}


/** Kick off a smart-create call. Returns the draft id. The fetch is
 *  fire-and-forget at the component level -- progress streams update
 *  this module's state which subscribers observe. Survives unmount. */
export function startCreate(input: {
  projectId: string
  context: string
  manualId?: string
  manualDesc?: string
  position?: { x: number; y: number }
}): string {
  const draftId = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : `draft-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

  _store[draftId] = {
    draftId,
    projectId: input.projectId,
    context: input.context,
    manualId: input.manualId,
    manualDesc: input.manualDesc,
    state: 'creating',
    log: [],
    startedAt: Date.now(),
    position: input.position,
  }
  _notify()

  const url = `/api/projects/${encodeURIComponent(input.projectId)}/tasks/smart-create`
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      context: input.context,
      task_id: input.manualId,
      description: input.manualDesc,
    }),
  })
    .then(async (resp) => {
      const reader = resp.body?.getReader()
      const decoder = new TextDecoder()
      if (!reader) {
        _markCompleted(draftId)
        return
      }
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        const text = decoder.decode(value)
        for (const line of text.split('\n')) {
          if (!line.startsWith('data: ')) continue
          try {
            const parsed = JSON.parse(line.slice(6))
            const entry = _store[draftId]
            if (!entry) continue   // dismissed mid-stream
            if (typeof parsed.text === 'string') {
              entry.log.push(parsed.text)
              // Smart-create surfaces the resolved task id via the
              // first matching "Created task '<id>'" line. Capture
              // so we can swap the draft for the real node when done.
              const m = parsed.text.match(/Created task ['"]?([\w._-]+)['"]?/)
              if (m && !entry.taskId) entry.taskId = m[1]
            }
            if (typeof parsed.task_id === 'string') {
              entry.taskId = parsed.task_id
            }
            if (typeof parsed.error === 'string') {
              entry.errorMsg = parsed.error
            }
            // Server explicitly signals stream end via `{"done": true}`.
            // Mark completion eagerly so the UI flips out of 'creating'
            // immediately, instead of waiting for reader.read() to
            // signal stream-closed (which can lag if there's a proxy
            // buffering between us and FastAPI's StreamingResponse).
            if (parsed.done === true) {
              _markCompleted(draftId)
            }
            _notify()
          } catch { /* skip malformed line */ }
        }
      }
      _markCompleted(draftId)
    })
    .catch((e) => {
      const entry = _store[draftId]
      if (!entry) return
      entry.state = 'failed'
      entry.errorMsg = e instanceof Error ? e.message : String(e)
      entry.completedAt = Date.now()
      _notify()
    })

  return draftId
}


function _markCompleted(draftId: string) {
  const entry = _store[draftId]
  if (!entry || entry.state !== 'creating') return
  entry.state = entry.taskId && !entry.errorMsg ? 'done' : 'failed'
  entry.completedAt = Date.now()
  _notify()
}


/** Drop an entry from the store -- e.g. user clicks dismiss on the
 *  NEW badge or an error toast. Listeners get notified. */
export function dismissCreate(draftId: string) {
  if (delete _store[draftId]) _notify()
}


/** Test helper: clear everything. Not part of the public app surface;
 *  gated by name so production code grep won't find it accidentally. */
export function _resetForTests() {
  for (const k of Object.keys(_store)) delete _store[k]
  _listeners.clear()
  try { localStorage.removeItem(LS_KEY) } catch { /* ignore */ }
}
