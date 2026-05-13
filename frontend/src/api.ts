import type { Project, Task, PR, PRDetail, ActionDef, EvaEvent, ForkableData, GraphData } from './types'

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(path, options)
  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(`${resp.status}: ${text}`)
  }
  if (resp.status === 204) return null as T
  return resp.json()
}

function post<T>(path: string, body: unknown): Promise<T> {
  return fetchApi<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export const api = {
  getProjects: () => fetchApi<{ projects: Project[] }>('/api/projects'),
  getProject: (id: string) => fetchApi<Project>(`/api/projects/${encodeURIComponent(id)}`),
  createProject: (body: { id: string; name?: string; description?: string;
                          repo?: string | null; jira?: string | null;
                          has_tickets?: boolean }) =>
    post<Project>('/api/projects', body),
  setProjectVisibility: (id: string, hidden: boolean) =>
    post<{ project_id: string; hidden: boolean; hidden_projects: string[] }>(
      `/api/projects/${encodeURIComponent(id)}/visibility`, { hidden },
    ),
  getGraph: (id: string) => fetchApi<GraphData>(`/api/projects/${encodeURIComponent(id)}/graph`),
  getTask: (pid: string, tid: string) => fetchApi<Task>(`/api/projects/${encodeURIComponent(pid)}/tasks/${encodeURIComponent(tid)}`),
  createTask: (pid: string, body: { id: string; description?: string; type?: string; status?: string; group?: string }) =>
    post<Task>(`/api/projects/${encodeURIComponent(pid)}/tasks`, body),
  addDep: (pid: string, tid: string, dependsOn: string) => post<{ ok: boolean }>(`/api/projects/${encodeURIComponent(pid)}/tasks/${encodeURIComponent(tid)}/deps`, { depends_on: dependsOn }),
  removeDep: (pid: string, tid: string, dependsOn: string) => fetchApi<void>(`/api/projects/${encodeURIComponent(pid)}/tasks/${encodeURIComponent(tid)}/deps/${encodeURIComponent(dependsOn)}`, { method: 'DELETE' }),
  closeTask: (pid: string, tid: string, reason: string) => post<Task>(`/api/projects/${encodeURIComponent(pid)}/tasks/${encodeURIComponent(tid)}/close`, { reason }),
  checkStatus: (pid: string, tid: string) => post<Task & { changed: boolean; old_status: string; new_status: string }>(`/api/projects/${encodeURIComponent(pid)}/tasks/${encodeURIComponent(tid)}/check-status`, {}),
  openSession: (body: { kind?: 'task' | 'review'; task_id?: string; project_id?: string; review_url?: string; action_id: string; pr_number?: number; pr_repo?: string; custom_prompt?: string }) => post<{ session: string; new: boolean; prompt: string }>('/api/sessions/open', body),
  openProjectManager: (pid: string) =>
    post<{ project_id: string; tmux_name: string; running: boolean; status?: string }>(
      `/api/projects/${encodeURIComponent(pid)}/manager`, {}),
  getProjectManager: (pid: string) =>
    fetchApi<{ project_id: string; tmux_name: string; running: boolean; status?: string }>(
      `/api/projects/${encodeURIComponent(pid)}/manager`),
  killProjectManager: (pid: string) =>
    fetchApi<{ killed: boolean; tmux_name?: string }>(
      `/api/projects/${encodeURIComponent(pid)}/manager`, { method: 'DELETE' }),
  runProjectManagerAction: (pid: string, prompt: string) =>
    post<{ ok: boolean; tmux_name: string; ran: boolean }>(
      `/api/projects/${encodeURIComponent(pid)}/manager/run`, { prompt }),
  killSession: (name: string) => fetchApi<void>(`/api/sessions/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  resumeSession: (name: string) =>
    post<{ session: string; action: 'resumed' | 'relaunched' | 'noop'; running: boolean; agent_session_id?: string }>(
      `/api/sessions/${encodeURIComponent(name)}/resume`, {}),
  rebuildSessions: () => post<{ rebuilt: string[]; skipped: string[] }>('/api/sessions/rebuild', {}),
  killSessionsByStatus: (statuses: string[]) => post<{ killed: string[] }>('/api/sessions/kill-by-status', { statuses }),
  waitReady: (name: string, timeout = 30) => fetchApi<{ ready: boolean }>(`/api/sessions/${encodeURIComponent(name)}/wait-ready?timeout=${timeout}`),
  getAllPRs: (status = 'open', search = '') => fetchApi<{ groups: Record<string, { name: string; prs: PR[] }> }>(`/api/all-prs?status=${encodeURIComponent(status)}&search=${encodeURIComponent(search)}`),
  getReviewRequests: () => fetchApi<{ prs: Array<PR & { repo: string; source?: 'github' | 'manual' | 'both' }> }>('/api/review-requests'),
  syncReviewRequests: () => post<{ status: string }>('/api/review-requests/sync', {}),
  // Snapshot comment_count -> last_seen_comment_count so the "N new"
  // badge clears. Called when ReviewsPage selects a PR. Returns the
  // updated row.
  markReviewSeen: (url: string) => post<unknown>(
    '/api/reviews/seen?url=' + encodeURIComponent(url), {},
  ),
  // All-PRs equivalent of markReviewSeen. Called when PRsPage /
  // ProjectPage select a task PR.
  markPrSeen: (number: number) => post<unknown>(
    '/api/prs/' + encodeURIComponent(String(number)) + '/seen', {},
  ),
  submitPRReview: (repo: string, number: number, event: 'APPROVE' | 'REQUEST_CHANGES' | 'COMMENT', body: string) =>
    post<{ ok: boolean; event: string }>(
      '/api/pr-review', { repo, number, event, body }),
  addReviewWatch: (url: string) =>
    post<{ url: string; repo: string; number: number; title: string; added_at: string }>(
      '/api/review-requests/watchlist', { url }),
  removeReviewWatch: (url: string) =>
    fetchApi<{ removed: boolean; url: string }>(
      `/api/review-requests/watchlist?url=${encodeURIComponent(url)}`,
      { method: 'DELETE' },
    ),
  getPRDetail: (repo: string, number: number) => fetchApi<PRDetail>(`/api/pr-detail?repo=${encodeURIComponent(repo)}&number=${number}`),
  updatePRBody: (repo: string, number: number, body: string) => post<{ ok: boolean }>('/api/pr-body', { repo, number, body }),
  updatePRTitle: (repo: string, number: number, title: string) => post<{ ok: boolean }>('/api/pr-title', { repo, number, title }),
  getPRDiff: (repo: string, number: number) => fetchApi<{ files: Record<string, string> }>(`/api/pr-diff?repo=${encodeURIComponent(repo)}&number=${number}`),
  refreshPR: (number: number) => post<{ ok: boolean }>(`/api/pr-refresh/${number}`, {}),
  lookupPR: (number: number) => fetchApi<{ found: boolean; project?: string; task_id?: string; number?: number; url?: string }>(`/api/pr-lookup/${number}`),
  replyToComment: (repo: string, number: number, commentId: number, body: string, isReviewComment = false) =>
    post<{ ok: boolean }>('/api/pr-comment-reply', { repo, number, comment_id: commentId, body, is_review_comment: isReviewComment }),
  editComment: (repo: string, commentId: number, body: string, isReviewComment = false) =>
    post<{ ok: boolean }>('/api/pr-comment-edit', { repo, comment_id: commentId, body, is_review_comment: isReviewComment }),
  resolveThread: (threadId: string, resolve = true, repo = '') =>
    post<{ ok: boolean }>('/api/pr-thread-resolve', { thread_id: threadId, resolve, repo }),
  getActions: (context: string) => fetchApi<{ actions: ActionDef[] }>(`/api/actions?context=${encodeURIComponent(context)}`),
  getEvents: (limit = 30) => fetchApi<{ events: EvaEvent[]; unread: number; total: number }>(`/api/events?limit=${limit}`),
  markEventsRead: (opts?: { ids?: string[]; url?: string; session?: string }) => post<void>('/api/events/read', opts || {}),
  getCerts: () => fetchApi<Record<string, unknown>>('/api/certs'),
  getBoba: () => fetchApi<{ active: boolean; message: string; estimated_time?: string; location?: string; url?: string }>('/api/boba'),
  renewCert: (certId: string) => post<{ ok: boolean; output?: string }>(
    '/api/certs/renew/' + encodeURIComponent(certId), {},
  ),
  getUsage: () => fetchApi<Record<string, unknown>>('/api/usage'),
  getUsageHistory: (days = 1) => fetchApi<{ history: Array<{ ts: string; daily: number; weekly: number; monthly: number }>; total_records: number }>(`/api/usage/history?days=${days}`),
  getForkable: () => fetchApi<ForkableData>('/api/forkable'),
  getLiveStats: (refresh = false) => fetchApi<Record<string, unknown>>(`/api/live-stats${refresh ? '?refresh=1' : ''}`),
  getWorkstats: () => fetchApi<Record<string, unknown>>('/api/workstats'),
  getWorkLogs: (limit = 30) => fetchApi<{ logs: Array<{ date: string; content: string; updated_at: string }> }>(`/api/worklog?limit=${limit}`),
  getWorkLog: (date: string) => fetchApi<{ date: string; content: string; auto_generated: string; updated_at: string }>(`/api/worklog/${date}`),
  getWorkLogRange: (start: string, end: string, label: string) => fetchApi<{ start: string; end: string; label: string; content: string }>(`/api/worklog-range?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&label=${encodeURIComponent(label)}`),
  saveWorkLog: (date: string, content: string) => fetchApi<{ ok: boolean }>(`/api/worklog/${date}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }) }),
  getUberEats: () => fetchApi<Record<string, unknown>>('/api/ubereats'),
  search: (q: string, limit = 20) => fetchApi<{ results: Array<{
    type: 'task' | 'pr' | 'session'
    title: string
    subtitle: string
    badge: string
    project_id: string
    task_id?: string | null
    pr_number?: number
    pr_repo?: string
  }> }>(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  sendTerminalInput: (name: string, text: string) =>
    fetch(`/api/terminal/${encodeURIComponent(name)}/input`, { method: 'POST', body: text }),
  resizeTerminal: (name: string, rows: number, cols: number) =>
    fetch(`/api/terminal/${encodeURIComponent(name)}/resize?rows=${rows}&cols=${cols}`, { method: 'POST' }),
  // Settings: generic JSON key/value store backing the SettingsModal.
  // Values can be any JSON (string, number, list, dict).
  listSettings: () => fetchApi<{ settings: Record<string, unknown> }>('/api/settings'),
  setSetting: (key: string, value: unknown) =>
    fetchApi<{ key: string; value: unknown }>(`/api/settings/${encodeURIComponent(key)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    }),
  deleteSetting: (key: string) =>
    fetchApi<{ ok: boolean }>(`/api/settings/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  // Repos: list of rules + the live resolved repo set those rules
  // currently match (driven by the local prs table). Powers the
  // Settings UI's Repos tab.
  resolveRepos: () => fetchApi<{
    rules: string[]
    fork_to_upstream: Record<string, string>
    resolved: Array<{
      repo: string
      source: 'rule' | 'wildcard'
      wildcard?: string
      pr_count: number
    }>
  }>('/api/repos/resolved'),
  // Auto-fill fork_to_upstream by scanning each loaded gh CLI
  // account for forks whose parent is in the allow-list.
  detectForks: () => fetchApi<{
    detected: Record<string, string>
    scanned_accounts: string[]
    errors: Array<{ account: string; message: string }>
  }>('/api/repos/detect-forks'),
  // First-boot health check: gh CLI, accounts, allow-list, rules.
  getSetupStatus: () => fetchApi<{
    all_ok: boolean
    checks: Array<{
      id: string
      label: string
      ok: boolean
      detail: string
      hint: string
    }>
  }>('/api/system/setup-status'),
  // ---- Cron jobs ----
  // CRUD over user-defined long-running automation jobs.
  listCronJobs: () => fetchApi<{ jobs: CronJob[] }>('/api/cron-jobs'),
  createCronJob: (body: {
    name: string; schedule: string; command: string;
    description?: string; enabled?: boolean
  }) => post<CronJob>('/api/cron-jobs', body),
  updateCronJob: (id: number, body: Partial<{
    name: string; schedule: string; command: string;
    description: string; enabled: boolean
  }>) =>
    fetchApi<CronJob>(`/api/cron-jobs/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  deleteCronJob: (id: number) =>
    fetchApi<{ ok: boolean }>(`/api/cron-jobs/${id}`, { method: 'DELETE' }),
  listCronJobRuns: (id: number, limit = 20) =>
    fetchApi<{ runs: CronJobRun[] }>(`/api/cron-jobs/${id}/runs?limit=${limit}`),
  runCronJobNow: (id: number) =>
    post<CronJobRun>(`/api/cron-jobs/${id}/run`, {}),
  parseCronSchedule: (text: string) =>
    fetchApi<{
      kind: 'interval' | 'cron' | 'invalid'
      original: string
      interval_seconds: number
      cron_expr: string
      error: string
    }>(`/api/cron-jobs/parse-schedule?text=${encodeURIComponent(text)}`),
  // Tickets (JIRA-backed). `configured` tells the UI whether to
  // show the "set up JIRA in Settings" empty state instead of "no
  // tickets" when the cache is empty.
  listTickets: (limit = 100) =>
    fetchApi<{
      tickets: Ticket[]
      configured: boolean
      instances?: JiraInstance[]
    }>(`/api/tickets?limit=${limit}`),
  getTicket: (key: string, instanceName?: string) =>
    fetchApi<Ticket>(
      `/api/tickets/${encodeURIComponent(key)}` +
      (instanceName ? `?instance_name=${encodeURIComponent(instanceName)}` : ''),
    ),
  syncTickets: () =>
    fetchApi<{
      instances?: Array<{ name: string; count: number; pruned: number; jql: string }>
      total_count?: number
      errors?: Array<{ name: string; error: string }>
      // Legacy single-instance shape preserved for back-compat.
      count?: number
      pruned?: number
      jql?: string
    }>('/api/tickets/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    }),
  openTicketSession: (key: string,
                       opts?: { instanceName?: string;
                                customPrompt?: string }) =>
    post<{ session: string; new: boolean; ticket_key: string;
           prompt_sent?: boolean }>(
      `/api/tickets/${encodeURIComponent(key)}/session`,
      {
        instance_name: opts?.instanceName,
        custom_prompt: opts?.customPrompt,
      },
    ),
  // -- Phase 4: auto-track --
  trackTicket: (key: string, instanceName?: string) =>
    post<Ticket>(
      `/api/tickets/${encodeURIComponent(key)}/track`,
      { instance_name: instanceName },
    ),
  // -- Phase 2: write-side --
  postTicketComment: (key: string, commentBody: string, instanceName?: string) =>
    post<{ id?: string; author?: { displayName?: string; name?: string };
           body?: string }>(
      `/api/tickets/${encodeURIComponent(key)}/comment`,
      { body: commentBody, instance_name: instanceName },
    ),
  listTicketTransitions: (key: string, instanceName?: string) =>
    fetchApi<{ transitions: Array<{ id: string; name: string;
                                     to?: { name?: string } }> }>(
      `/api/tickets/${encodeURIComponent(key)}/transitions` +
      (instanceName ? `?instance_name=${encodeURIComponent(instanceName)}` : ''),
    ),
  applyTicketTransition: (key: string, transitionId: string,
                           opts?: { instanceName?: string;
                                    resolution?: string;
                                    comment?: string }) =>
    post<unknown>(
      `/api/tickets/${encodeURIComponent(key)}/transition`,
      {
        transition_id: transitionId,
        instance_name: opts?.instanceName,
        resolution: opts?.resolution,
        comment: opts?.comment,
      },
    ),
  // Multi-instance JIRA config. The Settings UI's JIRA section
  // edits these directly so a fork user can configure two JIRAs
  // (e.g. Apache server + Atlassian Cloud) without code changes.
  upsertJiraInstance: (body: {
    name: string
    base_url: string
    auth_type: 'basic' | 'bearer'
    email?: string
    api_token: string
    jql?: string
  }) =>
    fetchApi<{ ok: boolean; name: string }>(
      `/api/tickets/instances/${encodeURIComponent(body.name)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    ),
  deleteJiraInstance: (name: string) =>
    fetchApi<{ ok: boolean }>(
      `/api/tickets/instances/${encodeURIComponent(name)}`,
      { method: 'DELETE' },
    ),
}

export interface JiraInstance {
  name: string
  base_url: string
  auth_type: 'basic' | 'bearer'
  email: string
  jql: string
  has_token: boolean
}

export interface Ticket {
  instance_name?: string
  key: string
  summary: string
  description: string
  status: string
  priority: string
  issue_type: string
  project_key: string
  assignee_email: string
  reporter_email: string
  url: string
  created_at: string
  updated_at: string
  synced_at: string
  // Phase-1 enrichment (parsed by backend `enrich_for_view`):
  labels?: string[]
  components?: string[]
  fix_versions?: string[]
  parent_key?: string
  resolution?: string
  status_category?: string
  category?: string  // project prefix, e.g. EX / ES / INTKEY
  linked_tasks?: { project: string; task_id: string; status?: string }[]
  // tmux name of the agent session bound to this ticket. Used as
  // the key into the global session-status snapshot; live state
  // (alive / status) is owned by that service, not the row.
  session_name?: string
}

export interface CronJob {
  id: number
  name: string
  schedule: string
  command: string
  description: string
  enabled: number  // sqlite stores 0/1, JSON-serialised as number
  created_at: string
  updated_at: string
  last_run_at: string
  last_status: string
  next_run_at: string
  // tmux name of the agent session bound to this cron job. Used as
  // the key into the global session-status snapshot; live state
  // (alive / status) is owned by that service, not the row.
  session_name?: string
}

export interface CronJobRun {
  id: number
  job_id: number
  started_at: string
  finished_at: string
  status: 'running' | 'done' | 'failed' | 'cancelled'
  output_excerpt: string
  error_message: string
}
