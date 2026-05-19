export type TaskStatus = 'not_started' | 'in_progress' | 'in_review' | 'done' | 'needs_follow_up' | 'closed' | 'blocked'

export interface Project {
  id: string
  name: string
  description: string
  has_tickets: boolean
  /** When true the sidebar hides this project by default;
   * see `core/src/common/settings.KEY_HIDDEN_PROJECTS`. */
  hidden?: boolean
  progress: number
  task_counts: Record<string, number>
  tasks: Record<string, Task>
}

export interface Task {
  task_id: string
  project: string
  description: string
  type: string
  status: TaskStatus
  group_name: string
  notes: string
  priority: number
  ticket_id: string | null
  ticket_url: string | null
  dependencies: string[]
  follow_ups: string[]
  prs: PR[]
  history?: Array<{ ts: string; text: string }>
  session?: SessionInfo
  created_at: string
  updated_at: string
}

// Values for `PR.my_review_state` -- mirrors the sqlite CHECK +
// Python enum on `review_prs.my_review_state`. Empty string means
// "I haven't been asked to review AND haven't reviewed".
export type MyReviewState =
  | ''
  | 'pending_review'
  | 'approved'
  | 'changes_requested'
  | 'commented'

export interface PR {
  number: number
  url: string
  status: string
  title: string
  ci_status: string
  review_status: string
  my_review_state?: MyReviewState
  comment_count: number
  // Server-computed delta: comment_count - last_seen_comment_count.
  // Drives the "N new" badge on the PR node when > 0. Resets to 0
  // when the user opens the review (backend call to /api/reviews/seen).
  unread_comment_count?: number
  additions: number
  deletions: number
  author: string
  head_branch: string
  base_branch: string
  last_updated: string
  task_id?: string
  project?: string
  // Review-PR-only: tmux name of the agent session bound to this
  // review (set when the user starts a review session). Used as the
  // key into the global session-status snapshot. Live state itself
  // (alive / running / status) is owned by the snapshot service, not
  // serialized per row.
  session_name?: string
}

export interface SessionInfo {
  name: string
  running: boolean
  status: string
}

export interface PRDetail {
  number: number
  title: string
  body: string
  state: string
  author: { login: string }
  url: string
  headRefName: string
  baseRefName: string
  additions: number
  deletions: number
  mergeable: string
  reviewDecision: string
  labels: Array<{ name: string }>
  // `body` is the review-level summary the user types in the GitHub
  // "Review changes" dialog -- distinct from inline review comments.
  // Reviewers who only left inline notes have body == '', so the UI
  // skips those.
  reviews: Array<{
    id?: number
    author: { login: string }
    state: string
    body?: string
    submittedAt?: string
  }>
  comments: Array<{ id?: number; author: { login: string }; body: string; createdAt: string }>
  files: Array<{ path: string; additions: number; deletions: number }>
  statusCheckRollup: Array<{
    name?: string
    context?: string
    conclusion?: string
    state?: string
    status?: string
  }>
  inlineComments: Array<{
    id: string
    user: string
    avatar: string
    path: string
    line: number
    body: string
    createdAt: string
    diffHunk: string
    inReplyToId: string | null
    threadId?: string
    isResolved?: boolean
    isOutdated?: boolean
  }>
}

/** A single inline comment attached to my pending (draft) review on a PR.
 *  Mirrors the subset of GitHub's review-comment fields the UI needs to
 *  render the comment inline at its file:line + offer a delete button. */
export interface PendingReviewComment {
  id: number
  path: string
  /** File line number when GitHub has anchored the draft to one, OR a
   *  fallback to `position` when not (drafts often only carry the diff
   *  position). For matching against a rendered diff prefer `position`. */
  line: number | null
  /** 1-based position within the unified diff (counts every line below
   *  the first `@@`). Stable across drafts/finalized comments, so this
   *  is what the file-diff renderer matches on to place the comment
   *  inline at the right row. */
  position: number | null
  side: 'LEFT' | 'RIGHT'
  start_line: number | null
  start_side: 'LEFT' | 'RIGHT' | null
  body: string
  created_at: string
  in_reply_to_id: number | null
}

export interface ActionDef {
  id: string
  label: string
  prompt_template: string
  context: string
  condition: string
  sort_order: number
}

export interface EvaEvent {
  id: string
  ts: string
  source: string
  type: string
  title: string
  message: string
  severity: string
  url: string | null
  read: number
}

export interface MealInfo {
  meal: string | null
  bucket: string | null
  status: string
  venue: string | null
  image: string | null
}

export interface ForkableData {
  today: MealInfo
  tomorrow: MealInfo
  alerts?: Array<{ date: string; venue: string }>
}

export interface GraphData {
  nodes: Array<Task & { id: string; follow_ups: string[] }>
  edges: Array<{ from: string; to: string }>
  groups: string[]
  has_tickets: boolean
}
