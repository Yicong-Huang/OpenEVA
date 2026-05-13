import { useCallback, useState } from 'react'
import { api, type Ticket } from '../api'
import { timeAgo } from '../utils'

/**
 * TicketCard -- right pane of the Tickets page.
 *
 * Naming follows the All Reviews / All PRs convention by position:
 * left list = Ticket Node, middle = Task Node, right = Ticket Card
 * (the "Task Card" slot in the user's vocabulary -- we keep the
 * Ticket prefix here because `components/TaskCard.tsx` already owns
 * the project-task main card).
 *
 * Renders the enriched JIRA detail: full field table, labels /
 * components / fix versions / parent crumb, action buttons, triage
 * panel, transition picker, comment box, and reverse-link list of
 * tracking tasks.
 */

export function TicketCard({
  ticket, onSelectLinkedTask,
}: {
  ticket: Ticket
  onSelectLinkedTask?: (project: string, taskId: string) => void
}) {
  const labels = ticket.labels ?? []
  const components = ticket.components ?? []
  const fixVersions = ticket.fix_versions ?? []
  const linkedTasks = ticket.linked_tasks ?? []
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8,
                    flexWrap: 'wrap' }}>
        {/* Key links to JIRA itself for fields we can't edit yet --
            same pattern as the PR-detail page. */}
        <a href={ticket.url} target="_blank" rel="noopener noreferrer"
           data-testid="ticket-key-link"
           style={{
             fontFamily: 'monospace', fontSize: 13, fontWeight: 700,
             color: 'var(--accent)', textDecoration: 'none',
           }}>{ticket.key}</a>
        <span style={{ fontSize: 16, fontWeight: 600 }}>
          {ticket.summary}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 12, marginTop: 8, fontSize: 11,
                    flexWrap: 'wrap' }}>
        <KV k="Status" v={ticket.status} />
        <KV k="Priority" v={ticket.priority} />
        <KV k="Type" v={ticket.issue_type} />
        <KV k="Project" v={ticket.project_key} />
        <KV k="Assignee" v={ticket.assignee_email} />
        <KV k="Reporter" v={ticket.reporter_email} />
        {ticket.resolution && <KV k="Resolution" v={ticket.resolution} />}
        {ticket.parent_key && (
          <div>
            <span style={{ color: 'var(--text-dim)' }}>Parent: </span>
            <a href={ticket.url.replace(ticket.key, ticket.parent_key)}
               target="_blank" rel="noopener noreferrer"
               data-testid="ticket-parent-link"
               style={{ fontFamily: 'monospace', color: 'var(--accent)' }}>
              {ticket.parent_key}
            </a>
          </div>
        )}
      </div>

      {(labels.length > 0 || components.length > 0 || fixVersions.length > 0) && (
        <div data-testid="ticket-chips"
             style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
          {labels.map((l) => (
            <Chip key={`l-${l}`} label={l} kind="label" />
          ))}
          {components.map((c) => (
            <Chip key={`c-${c}`} label={c} kind="component" />
          ))}
          {fixVersions.map((v) => (
            <Chip key={`v-${v}`} label={v} kind="version" />
          ))}
        </div>
      )}

      <TriagePanel ticketKey={ticket.key} instanceName={ticket.instance_name || ''} />

      {ticket.description && (
        <pre style={{
          fontSize: 11, fontFamily: 'inherit', whiteSpace: 'pre-wrap',
          background: 'var(--panel-bg)', border: '1px solid var(--border)',
          borderRadius: 4, padding: 8, marginTop: 12, color: 'var(--text)',
          maxHeight: 240, overflowY: 'auto',
        }}>{ticket.description}</pre>
      )}

      {linkedTasks.length > 0 && (
        <div data-testid="ticket-linked-tasks" style={{ marginTop: 12 }}>
          <div style={{
            fontSize: 11, fontWeight: 700, color: 'var(--accent)',
            textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4,
          }}>Linked tasks</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {linkedTasks.map((lt) => (
              <button key={`${lt.project}/${lt.task_id}`}
                      data-testid={`ticket-linked-task-${lt.task_id}`}
                      onClick={() => onSelectLinkedTask?.(lt.project, lt.task_id)}
                      disabled={!onSelectLinkedTask}
                      style={{
                        fontSize: 11, color: 'var(--accent)',
                        fontFamily: 'monospace', textAlign: 'left',
                        background: 'transparent', border: 'none',
                        padding: 0, cursor: onSelectLinkedTask ? 'pointer' : 'default',
                      }}>
                {lt.project} / {lt.task_id}
              </button>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, marginTop: 12, flexWrap: 'wrap' }}>
        <button
          className="btn-action"
          data-testid="ticket-open-jira"
          onClick={() => window.open(ticket.url, '_blank', 'noopener')}
          style={{ fontSize: 11 }}
        >Open in JIRA</button>
        <button
          className="btn-action"
          data-testid="ticket-copy-key"
          onClick={() => {
            try { navigator.clipboard?.writeText(ticket.key) } catch { /* ignore */ }
          }}
          style={{ fontSize: 11 }}
        >Copy key</button>
      </div>

      <TicketTransitionPicker ticket={ticket} />
      <TicketCommentBox ticket={ticket} />

      <div style={{ fontSize: 10, color: 'var(--text-faint)', marginTop: 12 }}>
        Updated {timeAgo(ticket.updated_at)} {' '}
        Last synced {ticket.synced_at || 'never'}
      </div>
    </div>
  )
}


/** Resolve / transition dropdown. Loads available transitions on
 * demand (single click) so we don't issue an extra round-trip per
 * ticket selection. JIRA transition ids vary per project so we
 * present `name` and post `id`. */
function TicketTransitionPicker({ ticket }: { ticket: Ticket }) {
  const [transitions, setTransitions] = useState<
    Array<{ id: string; name: string }> | null
  >(null)
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const loadTransitions = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await api.listTicketTransitions(ticket.key, ticket.instance_name)
      setTransitions(r.transitions)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load transitions')
    } finally {
      setLoading(false)
    }
  }, [ticket.key, ticket.instance_name])

  const apply = useCallback(async (t: { id: string; name: string }) => {
    setBusyId(t.id)
    setError(null)
    try {
      await api.applyTicketTransition(ticket.key, t.id, {
        instanceName: ticket.instance_name,
      })
      setDone(t.name)
      // Hide the dropdown after success; the user can re-open by
      // clicking Resolve again.
      setTransitions(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Transition failed')
    } finally {
      setBusyId(null)
    }
  }, [ticket.key, ticket.instance_name])

  return (
    <div style={{ marginTop: 12 }} data-testid="ticket-transitions">
      {!transitions && !loading && (
        <button className="btn-action accent"
                data-testid="ticket-transitions-load"
                onClick={loadTransitions}
                style={{ fontSize: 11 }}>
          Resolve / Transition...
        </button>
      )}
      {loading && (
        <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>
          Loading transitions...
        </span>
      )}
      {transitions && transitions.length === 0 && (
        <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
          No transitions available for this issue.
        </div>
      )}
      {transitions && transitions.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {transitions.map((t) => (
            <button key={t.id}
                    className="btn-action"
                    data-testid={`ticket-transition-${t.id}`}
                    onClick={() => apply(t)}
                    disabled={!!busyId}
                    style={{ fontSize: 11 }}>
              {busyId === t.id ? `${t.name}...` : t.name}
            </button>
          ))}
          <button className="btn-action"
                  data-testid="ticket-transitions-cancel"
                  onClick={() => setTransitions(null)}
                  style={{ fontSize: 11, color: 'var(--text-dim)' }}>
            Cancel
          </button>
        </div>
      )}
      {done && (
        <div data-testid="ticket-transition-done"
             style={{ fontSize: 11, color: 'var(--green)', marginTop: 4 }}>
          Applied: {done}. Click Sync to refresh status.
        </div>
      )}
      {error && (
        <div data-testid="ticket-transition-error"
             style={{ fontSize: 11, color: 'var(--red)', marginTop: 4 }}>
          {error}
        </div>
      )}
    </div>
  )
}


/** Plain-text comment box that POSTs to /api/tickets/{key}/comment.
 * Shows the success state inline ("Posted") so the user knows the
 * comment landed without a refresh; the JIRA-side comment list isn't
 * cached locally yet, so the comment shows up on the next browse
 * (Phase 2 punts on inline comment-list rendering). */
function TicketCommentBox({ ticket }: { ticket: Ticket }) {
  const [draft, setDraft] = useState('')
  const [posting, setPosting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [posted, setPosted] = useState(false)

  const submit = useCallback(async () => {
    if (!draft.trim()) return
    setPosting(true)
    setError(null)
    setPosted(false)
    try {
      await api.postTicketComment(ticket.key, draft, ticket.instance_name)
      setPosted(true)
      setDraft('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Comment failed')
    } finally {
      setPosting(false)
    }
  }, [draft, ticket.key, ticket.instance_name])

  return (
    <div style={{ marginTop: 12 }} data-testid="ticket-comment-box">
      <div style={{
        fontSize: 11, fontWeight: 700, color: 'var(--accent)',
        textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4,
      }}>Reply</div>
      <textarea
        data-testid="ticket-comment-input"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="Comment to post on this JIRA issue..."
        rows={3}
        style={{
          width: '100%', fontSize: 12, padding: 6,
          background: 'var(--input-bg)', color: 'var(--text)',
          border: '1px solid var(--border)', borderRadius: 4,
          fontFamily: 'inherit', resize: 'vertical',
        }}
      />
      <div style={{ display: 'flex', gap: 6, marginTop: 4, alignItems: 'center' }}>
        <button
          className="btn-action accent"
          data-testid="ticket-comment-submit"
          onClick={submit}
          disabled={posting || !draft.trim()}
          style={{ fontSize: 11 }}>
          {posting ? 'Posting...' : 'Post comment'}
        </button>
        {posted && (
          <span data-testid="ticket-comment-posted"
                style={{ fontSize: 11, color: 'var(--green)' }}>
            Posted. Sync to refresh.
          </span>
        )}
        {error && (
          <span data-testid="ticket-comment-error"
                style={{ fontSize: 11, color: 'var(--red)' }}>
            {error}
          </span>
        )}
      </div>
    </div>
  )
}


function KV({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <span style={{ color: 'var(--text-dim)' }}>{k}: </span>
      <span style={{ color: 'var(--text)' }}>{v || '--'}</span>
    </div>
  )
}


/** Server-side triage report shape: mirrors `core.tickets.triage`. */
interface BlameRow {
  file: string
  repo: string
  local_path: string
  author_name: string
  author_email: string
  commit: string
  committed_at: string
  subject: string
}
interface SimilarTicket {
  key: string
  summary: string
  status: string
  assignee_email: string
  components: string[]
  created_at: string
  url: string
}
interface TriageReport {
  ticket: { key: string; summary: string; status: string; priority: string;
            issue_type: string; url: string; instance_name: string }
  problem: string
  owner: { assignee: string; reporter: string; components: string[];
           labels: string[]; project_key: string }
  files_referenced: string[]
  blame: BlameRow[]
  similar_tickets: SimilarTicket[]
  most_likely_owner_team: string
  most_likely_test_owner: string
}


/** Per-ticket "Run triage" button + result display. Pulls the
 * structured triage envelope from /api/tickets/{key}/triage and
 * renders a human-readable summary (problem / owner / files /
 * git-blame placeholder). The user-facing flow:
 *
 *   click "Triage" -> spinner -> structured panel.
 *
 * Re-clicking re-runs (cache is server-side, but the user may want
 * a fresh fetch after a sync). The panel is collapsed by default so
 * normal ticket-detail viewing isn't cluttered. */
function TriagePanel({
  ticketKey, instanceName,
}: { ticketKey: string; instanceName: string }) {
  const [report, setReport] = useState<TriageReport | null>(null)
  const [phase, setPhase] = useState<'idle' | 'loading' | 'error'>('idle')
  const [error, setError] = useState('')
  const handleRun = useCallback(async () => {
    setPhase('loading')
    setError('')
    try {
      const params = new URLSearchParams()
      if (instanceName) params.set('instance_name', instanceName)
      const url = `/api/tickets/${encodeURIComponent(ticketKey)}/triage`
        + (params.toString() ? `?${params.toString()}` : '')
      const r = await fetch(url)
      if (!r.ok) {
        const body = await r.json().catch(() => ({ detail: r.statusText }))
        throw new Error(body.detail || `HTTP ${r.status}`)
      }
      const data = (await r.json()) as TriageReport
      setReport(data)
      setPhase('idle')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setPhase('error')
    }
  }, [ticketKey, instanceName])

  return (
    <div data-testid="triage-panel" style={{ marginTop: 12 }}>
      <button
        className="btn-action accent"
        data-testid="triage-run-btn"
        disabled={phase === 'loading'}
        onClick={handleRun}
        style={{ fontSize: 11 }}
      >
        {phase === 'loading' ? 'Triaging...'
          : report ? 'Re-run triage' : 'Run triage'}
      </button>
      {phase === 'error' && (
        <div data-testid="triage-error"
             style={{ color: 'var(--red)', fontSize: 11, marginTop: 8 }}>
          Triage failed: {error}
        </div>
      )}
      {report && (
        <div data-testid="triage-report" style={{
          marginTop: 8, padding: 10,
          background: 'var(--panel-bg)',
          border: '1px solid var(--border)', borderRadius: 4, fontSize: 12,
        }}>
          <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr',
                        gap: '4px 12px' }}>
            <span style={{ color: 'var(--text-dim)' }}>Problem</span>
            <span style={{ whiteSpace: 'pre-wrap',
                           maxHeight: 100, overflowY: 'auto' }}>
              {report.problem.slice(0, 600) || '(no description)'}
              {report.problem.length > 600 && '...'}
            </span>

            <span style={{ color: 'var(--text-dim)' }}>Assignee</span>
            <span data-testid="triage-assignee">
              {report.owner.assignee || '(unassigned)'}
            </span>

            <span style={{ color: 'var(--text-dim)' }}>Reporter</span>
            <span>{report.owner.reporter || '(none)'}</span>

            <span style={{ color: 'var(--text-dim)' }}>Most likely team</span>
            <span data-testid="triage-likely-team"
                  style={{ color: 'var(--accent)', fontWeight: 600 }}>
              {report.most_likely_owner_team || '(no signal)'}
            </span>

            <span style={{ color: 'var(--text-dim)' }}>Most likely owner</span>
            <span data-testid="triage-likely-owner"
                  style={{ color: 'var(--accent)', fontWeight: 600 }}>
              {report.most_likely_test_owner || '(no signal)'}
            </span>

            <span style={{ color: 'var(--text-dim)' }}>JIRA components</span>
            <span data-testid="triage-components">
              {report.owner.components.length > 0
                ? report.owner.components.join(', ')
                : '(none on this ticket)'}
            </span>

            <span style={{ color: 'var(--text-dim)' }}>Labels</span>
            <span>
              {report.owner.labels.length > 0
                ? report.owner.labels.join(', ')
                : '(none)'}
            </span>

            <span style={{ color: 'var(--text-dim)' }}>Files</span>
            <span data-testid="triage-files" style={{
              fontFamily: 'monospace', fontSize: 11,
            }}>
              {report.files_referenced.length > 0
                ? report.files_referenced.join('\n')
                : '(no file paths in description)'}
            </span>

            <span style={{ color: 'var(--text-dim)' }}>Similar tickets</span>
            <span data-testid="triage-similar">
              {report.similar_tickets.length === 0
                ? (
                  <span style={{ color: 'var(--text-dim)',
                                 fontStyle: 'italic' }}>
                    (no recent matches in JIRA for this target)
                  </span>
                )
                : (
                  <div style={{ display: 'flex', flexDirection: 'column',
                                gap: 2 }}>
                    {report.similar_tickets.map((s) => (
                      <div key={s.key}
                           style={{ fontFamily: 'monospace', fontSize: 11 }}>
                        <a href={s.url} target="_blank"
                           rel="noopener noreferrer"
                           style={{ color: 'var(--accent)' }}>
                          {s.key}
                        </a>{' '}
                        <span style={{ color: 'var(--text-dim)' }}>
                          [{s.status}]
                        </span>{' '}
                        <span style={{ color: 'var(--text)' }}>
                          {s.assignee_email || '(unassigned)'}
                        </span>{' '}
                        <span style={{ color: 'var(--text-dim)' }}>
                          {s.created_at.slice(0, 10)}
                        </span>{' '}
                        <span style={{ color: 'var(--text)' }}>
                          {s.summary.slice(0, 60)}
                          {s.summary.length > 60 && '...'}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
            </span>

            <span style={{ color: 'var(--text-dim)' }}>Git blame</span>
            <span data-testid="triage-blame">
              {report.blame.length === 0
                ? (
                  <span style={{ color: 'var(--text-dim)',
                                 fontStyle: 'italic' }}>
                    {report.files_referenced.length === 0
                      ? '(no file paths in description)'
                      : 'No matching local repos. Configure '
                        + 'service.git.local_repo_paths in Settings to '
                        + 'enable git-blame.'}
                  </span>
                )
                : (
                  <div style={{ display: 'flex', flexDirection: 'column',
                                gap: 4 }}>
                    {report.blame.map((b) => (
                      <div key={`${b.repo}:${b.file}:${b.commit}`}
                           style={{ fontFamily: 'monospace', fontSize: 11 }}>
                        <span style={{ color: 'var(--accent)' }}>
                          {b.commit}
                        </span>{' '}
                        <span style={{ color: 'var(--text)' }}>
                          {b.author_name}
                        </span>{' '}
                        <span style={{ color: 'var(--text-dim)' }}>
                          ({b.committed_at.slice(0, 10)}) -- {b.file} :
                        </span>{' '}
                        <span style={{ color: 'var(--text)' }}>
                          {b.subject}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}


function Chip({ label, kind }: { label: string; kind: 'label' | 'component' | 'version' }) {
  // A small palette per chip kind so the user can scan the row at a
  // glance: blue=label (free-form), purple=component (structural),
  // green=fix version (release-bound).
  const palette = {
    label: { bg: 'rgba(59,130,246,0.15)', fg: 'var(--blue)' },
    component: { bg: 'rgba(168,85,247,0.15)', fg: 'var(--purple)' },
    version: { bg: 'rgba(34,197,94,0.15)', fg: 'var(--green)' },
  }[kind]
  return (
    <span data-testid={`ticket-chip-${kind}-${label}`}
          style={{
            fontSize: 9, padding: '1px 6px', borderRadius: 3,
            background: palette.bg, color: palette.fg,
            fontWeight: 600, fontFamily: 'monospace',
          }}>{label}</span>
  )
}
