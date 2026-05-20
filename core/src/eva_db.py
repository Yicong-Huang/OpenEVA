"""SQLite-backed unified Eva database with all tables."""

import common
import json
import threading
from datetime import datetime, timezone

# Prefer the modern bundled SQLite shipped by `pysqlite3-binary` when
# it's installed. The stdlib `sqlite3` module is linked against the
# system `libsqlite3`, which on long-supported distros (Ubuntu 20.04,
# Debian 11) is too old for features Eva relies on -- DROP COLUMN
# (3.35+), generated columns, etc. `pysqlite3-binary` ships a recent
# SQLite inside a wheel so OSS installs don't have to touch system
# packages. When it's not installed we fall back to stdlib sqlite3
# so a minimal-deps run still works.
try:
    from pysqlite3 import dbapi2 as sqlite3
except ImportError:
    import sqlite3


class _LockedConnection:
    """Thin proxy over `sqlite3.Connection` that serialises every call
    through a re-entrant lock.

    Why: Eva runs the same `EvaDB` instance from several threads --
    the uvicorn worker, APScheduler executor threads (gh-poll, jira-
    sync, cron-runner, cert-checker), and the `/api/hook` daemon
    threads spawned by `routes/common.sessions.py` and `routes/common.prs.py`. We
    pass `check_same_thread=False` to allow the share, but that only
    disables sqlite3's own thread-affinity check -- it doesn't make
    concurrent `execute()` on the same connection safe. Two threads
    racing the connection's prepared-statement state surface as
    `sqlite3.InterfaceError: bad parameter or other API misuse` (which
    actually appeared in production -- 4 occurrences in 84 log lines).

    The lock is re-entrant so call sites that nest (e.g. a method
    that opens a transaction context, then calls another method that
    also wraps in `with self._conn:`) don't deadlock.

    Reads in WAL mode could in theory go unlocked, but distinguishing
    read-vs-write at the proxy layer would require parsing SQL --
    cheap to lock everything at this granularity (the connection
    isn't a hot path; the lock is held only for the brief execute
    duration).
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.RLock()

    # Hot methods we know about; explicitly wrapped so the lock is
    # acquired BEFORE the underlying call (rather than relying on
    # __getattr__ which would just return the raw bound method).
    def execute(self, *a, **kw):
        with self._lock:
            return self._conn.execute(*a, **kw)

    def execute_fetchall(self, *a, **kw):
        """Run a query and materialize every row inside the lock so
        another thread can't mutate the connection's prepared-statement
        state mid-iteration. Cursor `.fetchall()` reads column metadata
        per row; if a parallel `execute()` resets that metadata before
        we finish, we get `IndexError: tuple index out of range`. Use
        this for any SELECT whose rows are consumed by the caller --
        plain `execute(...).fetchall()` is unsafe under contention."""
        with self._lock:
            return self._conn.execute(*a, **kw).fetchall()

    def execute_fetchone(self, *a, **kw):
        """Like `execute_fetchall` but for single-row reads. Same race
        applies: `execute(...).fetchone()` releases the lock between
        the two calls. Use this for `WHERE pk = ?` lookups."""
        with self._lock:
            return self._conn.execute(*a, **kw).fetchone()

    def executemany(self, *a, **kw):
        with self._lock:
            return self._conn.executemany(*a, **kw)

    def executescript(self, *a, **kw):
        with self._lock:
            return self._conn.executescript(*a, **kw)

    def commit(self):
        with self._lock:
            return self._conn.commit()

    def rollback(self):
        with self._lock:
            return self._conn.rollback()

    def cursor(self):
        # Cursors carry their own state; advise callers to prefer
        # connection-level execute(). Still locked here for safety.
        with self._lock:
            return self._conn.cursor()

    # Transaction context: acquire the lock for the WHOLE block so
    # `with db._conn: ... ` is atomic w.r.t. other threads. Without
    # this wrapping, two threads could both enter the BEGIN... COMMIT
    # block simultaneously and one's commit would fire mid-other's
    # statement.
    def __enter__(self):
        self._lock.acquire()
        try:
            return self._conn.__enter__()
        except Exception:
            self._lock.release()
            raise

    def __exit__(self, exc_type, exc, tb):
        try:
            return self._conn.__exit__(exc_type, exc, tb)
        finally:
            self._lock.release()

    # Pass-through for everything else (row_factory, in_transaction,
    # close, etc.). Read access is safe; writes via attribute assign
    # are wrapped in __setattr__ below.
    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        # `self._conn` and `self._lock` are real attributes; everything
        # else (e.g. `row_factory`) is forwarded to the wrapped conn.
        if name in ("_conn", "_lock"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


# Stored task statuses. `blocked` is NOT here on purpose -- it's a
# *computed* state derived from dependency graph: a task is "blocked"
# iff any of its dependencies isn't in `UNBLOCKING_DEP_STATUSES`.
# The frontend's `TaskStatus` union DOES include 'blocked' because
# it's a displayed status, just never persisted.
VALID_STATUSES = {
    "not_started", "in_progress", "in_review", "done",
    "needs_follow_up", "closed",
}

# A dependency is "unblocking" -- it lets dependent tasks proceed --
# iff its status is one of these. `closed` counts because the user
# explicitly abandoned the dep ("we don't need to wait for it").
# `needs_follow_up` counts because the main work shipped; remaining
# items are minor and shouldn't gate downstream work.
# Single source of truth shared with the frontend (TS regex parses
# this constant in `tests/test_eva_cli.py::test_frontend_unblocking...`).
UNBLOCKING_DEP_STATUSES = frozenset({
    "done", "closed", "needs_follow_up",
})

# Statuses that are terminal (the task is over, dep state no longer
# matters). `effective_status` does NOT override these to 'blocked'
# even if the dep graph would say so -- a task that's already done /
# closed isn't going to do more work regardless.
TERMINAL_TASK_STATUSES = frozenset({"done", "closed"})


def _validate_status(status: str) -> None:
    """Raise ValueError if status is not a valid task status."""
    if status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}"
        )


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


# Type classifier for migrated/synced JIRA tickets. Mirrors the logic
# in `bin/migrate_unify_tasks.py:classify_ticket_type` -- if that file
# changes its rules, this one needs to follow. Kept as a module helper
# so both `upsert_ticket` and the one-shot migration land tickets in
# the same task `type`.
import re as _re_classify  # noqa: E402 -- module-level, intentional
_FLAKY_RE = _re_classify.compile(
    r"flaky|testman|test[- ]?failure|test failure|failing target",
    _re_classify.I,
)
_SLOW_RE = _re_classify.compile(r"\bslow\b|slowness|slow[- ]?test",
                                _re_classify.I)
_COMPLIANCE_RE = _re_classify.compile(r"\bcompliance\b|\bsecurity\b|cve-",
                                      _re_classify.I)


def _classify_ticket_type(summary: str, labels_json: str, issue_type: str) -> str:
    """Pick a concrete task `type` for a synced JIRA ticket."""
    try:
        labels = json.loads(labels_json) if labels_json else []
    except (json.JSONDecodeError, ValueError):
        labels = []
    text_bag = " ".join([summary or "", *(labels or [])])
    if _FLAKY_RE.search(text_bag):
        return "flaky-test"
    if _SLOW_RE.search(text_bag):
        return "slow-test"
    if _COMPLIANCE_RE.search(" ".join(labels or [])):
        return "compliance"
    it = (issue_type or "").lower().strip()
    if "sub-task" in it or "subtask" in it:
        return "subtask"
    return {
        "bug": "bug", "task": "task", "story": "feature",
        "epic": "epic", "improvement": "feature", "new feature": "feature",
    }.get(it, "bug")


def _pr_row_to_dict(row) -> dict:
    """Convert a `prs` row to a dict and append the computed
    `unread_comment_count` so every PR-shape path (list_all_prs,
    find_pr_by_number, _get_prs embedded in tasks) carries the
    badge-driving field uniformly.
    """
    d = dict(row)
    cc = d.get("comment_count") or 0
    last_seen = d.get("last_seen_comment_count") or 0
    d["unread_comment_count"] = max(0, cc - last_seen)
    return d


class EvaDB:
    """Unified SQLite database for Eva: tasks, config, events, and usage."""

    def __init__(self, db_path: str):
        # Ensure the parent directory exists. A fresh OSS clone has no
        # `data/` -- without this `sqlite3.connect` raises `unable to
        # open database file` and the server fails to start with no
        # actionable hint.
        from pathlib import Path as _Path
        _parent = _Path(db_path).expanduser().resolve().parent
        try:
            _parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Disk full / permission denied -- let sqlite3.connect
            # raise the real error below; nothing useful to do here.
            pass
        raw_conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
        )
        # Wrap the connection in `_LockedConnection` immediately so
        # the lock is in place before _create_schema runs (schema-
        # creation can race with a freshly-spawned scheduler tick on
        # process restart).
        self._conn = _LockedConnection(raw_conn)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        # Wait up to 5s for a busy writer instead of failing immediately
        # with `database is locked`. Other connections to this same file
        # (routes/events.py, services/github_poller.py, etc.) are not
        # serialised by `_LockedConnection`, so cross-connection lock
        # contention is real -- the busy timeout lets sqlite handle it.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._create_schema()

    def _create_schema(self):
        """Create all tables and indexes if they do not exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                name TEXT DEFAULT '',
                description TEXT DEFAULT '',
                repo TEXT,
                jira TEXT,
                has_tickets INTEGER DEFAULT 0,
                design_doc TEXT,
                umbrella_tickets TEXT DEFAULT '[]',
                created_at TEXT,
                updated_at TEXT
            );

            -- Unified `tasks` table. Per the reviews/tickets merge
            -- (bin/migrate_unify_tasks.py), the `review_prs` and
            -- `tickets` tables are gone -- their rows are now task
            -- rows with type='review' or type='flaky-test' / 'bug' /
            -- 'task' / ... and their fields became ticket_* / review_*
            -- prefixed columns below.
            --
            -- PK is task_id alone (globally unique). `project` is a
            -- soft folder grouping; '' / NULL = unsorted.
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                project TEXT,
                type TEXT DEFAULT 'feature',
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'not_started'
                    CHECK(status IN ('not_started','in_progress','in_review','done','needs_follow_up','closed')),
                group_name TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                priority INTEGER DEFAULT 5,
                follow_ups TEXT DEFAULT '[]',
                ticket_id TEXT,
                ticket_url TEXT,
                ticket_summary TEXT DEFAULT '',
                ticket_priority TEXT DEFAULT '',
                ticket_issue_type TEXT DEFAULT '',
                ticket_project_key TEXT DEFAULT '',
                ticket_assignee_email TEXT DEFAULT '',
                ticket_reporter_email TEXT DEFAULT '',
                ticket_status TEXT DEFAULT '',
                ticket_status_category TEXT DEFAULT '',
                ticket_labels TEXT DEFAULT '[]',
                ticket_components TEXT DEFAULT '[]',
                ticket_fix_versions TEXT DEFAULT '[]',
                ticket_parent_key TEXT DEFAULT '',
                ticket_resolution TEXT DEFAULT '',
                ticket_instance TEXT DEFAULT '',
                ticket_synced_at TEXT DEFAULT '',
                review_my_review_state TEXT DEFAULT '',
                review_my_workflow_state TEXT DEFAULT '',
                review_started_at TEXT DEFAULT '',
                review_last_seen_comment_count INTEGER DEFAULT 0,
                review_added_at TEXT DEFAULT '',
                review_source TEXT DEFAULT '',
                review_dirty INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            );

            -- Append-only history log per task. Complements `tasks.notes`:
            -- `notes` is a single editable blob (a pinned summary the
            -- user maintains), `task_history` is an immutable timeline
            -- of events appended by agents and humans. Each entry is
            -- short (<=100 chars) and timestamped so the timeline is
            -- scannable. Now also absorbs the old review_history rows.
            CREATE TABLE IF NOT EXISTS task_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                task_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_task_history_task
                ON task_history(task_id, ts DESC);

            CREATE TABLE IF NOT EXISTS task_dependencies (
                task_id TEXT NOT NULL,
                depends_on TEXT NOT NULL,
                PRIMARY KEY (task_id, depends_on),
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS prs (
                task_id TEXT NOT NULL,
                number INTEGER NOT NULL,
                url TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                title TEXT DEFAULT '',
                session TEXT,
                working_dir TEXT DEFAULT '~',
                ci_status TEXT DEFAULT 'unknown',
                review_status TEXT DEFAULT '',
                comment_count INTEGER DEFAULT 0,
                last_seen_comment_count INTEGER NOT NULL DEFAULT 0,
                additions INTEGER DEFAULT 0,
                deletions INTEGER DEFAULT 0,
                author TEXT DEFAULT '',
                head_branch TEXT DEFAULT '',
                base_branch TEXT DEFAULT '',
                last_updated TEXT DEFAULT '',
                -- Separate from last_updated: this is the timestamp at
                -- which the PR's status field actually changed (e.g.
                -- open -> merged). `last_updated` tracks when Eva last
                -- talked to GitHub -- a normal poll bumps it even when
                -- nothing changed, which used to pollute the auto-
                -- generated worklog with PRs that "happened today"
                -- only in the Eva-refresh sense.
                status_changed_at TEXT DEFAULT '',
                dirty INTEGER DEFAULT 0,
                UNIQUE(task_id, number),
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project);
            CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(type);
            CREATE INDEX IF NOT EXISTS idx_tasks_ticket ON tasks(ticket_id)
                WHERE ticket_id IS NOT NULL AND ticket_id != '';
            CREATE INDEX IF NOT EXISTS idx_prs_task ON prs(task_id);

            -- Config tables --

            CREATE TABLE IF NOT EXISTS action_definitions (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                prompt_template TEXT NOT NULL,
                context TEXT DEFAULT 'all',
                condition TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0
            );

            -- `tmux_name` is kept for back-compat with older DBs but in
            -- practice always equals task_id. New code should treat task_id
            -- as the canonical tmux session identifier and ignore tmux_name.
            CREATE TABLE IF NOT EXISTS sessions (
                task_id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                tmux_name TEXT NOT NULL,
                status TEXT DEFAULT 'not_started',
                -- Claude Code's session UUID, captured from the SessionStart
                -- hook. Lets Eva call `claude --resume <uuid>` when the tmux
                -- wrapper dies (e.g. host reboot) but the agent session file
                -- on disk is still intact.
                agent_session_id TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT
            );

            -- Project-level agent sessions: one per project, long-lived,
            -- acts as a coordinator/manager (audits state, suggests next
            -- task, etc.). Distinct from per-task sessions in `sessions`
            -- so they don't pollute task-oriented queries.
            CREATE TABLE IF NOT EXISTS project_sessions (
                project_id TEXT PRIMARY KEY,
                tmux_name TEXT NOT NULL,
                status TEXT DEFAULT 'idle',
                created_at TEXT,
                updated_at TEXT
            );

            -- Events table (from events.db) --

            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_id TEXT,
                title TEXT NOT NULL,
                message TEXT,
                type TEXT DEFAULT 'info',
                severity TEXT DEFAULT 'info',
                url TEXT,
                ts TEXT NOT NULL,
                read INTEGER DEFAULT 0,
                session TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_events_source ON events(source, source_id);

            -- Usage table (from usage.db) --

            CREATE TABLE IF NOT EXISTS usage_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                daily REAL,
                weekly REAL,
                monthly REAL
            );

            CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_history(ts);

            -- Work logs --

            -- `log_date` (not `date`) so the column doesn't shadow
            -- Python's `datetime.date` type when rows round-trip.
            -- Pre-existing DBs are migrated below via RENAME COLUMN.
            CREATE TABLE IF NOT EXISTS work_logs (
                log_date TEXT PRIMARY KEY,
                content TEXT DEFAULT '',
                auto_generated TEXT DEFAULT '',
                updated_at TEXT
            );
        """)
        self._conn.commit()

        # Index on ticket_id for fast lookup (not unique -- umbrella tickets can have multiple tasks)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_ticket "
            "ON tasks(project, ticket_id) WHERE ticket_id IS NOT NULL AND ticket_id != ''"
        )
        self._conn.commit()

        # The legacy `review_prs` + `review_history` tables are gone --
        # data merged into `tasks` (type='review' + review_* columns)
        # and `task_history` (one shared timeline). See
        # bin/migrate_unify_tasks.py.

        # settings: a generic JSON key-value store. Plugin runtime
        # config (cookies, tokens, channel ids, ...) plus framework
        # tunables (ALLOWED_REPOS, poll intervals, ...) all live here
        # so the frontend Settings UI can edit them without touching
        # code.
        # Each row is one logical setting; `value` is JSON-encoded so
        # we can store scalars, lists, or nested dicts uniformly.
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT 'null',
                updated_at TEXT NOT NULL DEFAULT ''
            );
        """)
        self._conn.commit()

        # cron_jobs: user-defined long-running automation jobs.
        # Each row is one job card the user creates in the Cron Jobs
        # page (e.g. "every 30 min run /yh-code-sync-my-prs"). The
        # `command` column is the slash-command or shell snippet that
        # will be passed to the agent at fire time. `schedule` is a
        # human-friendly string ("30min", "2h", "every weekday 9am")
        # that the scheduler parses; we store the original wording so
        # editing in the UI round-trips losslessly.
        #
        # Run history lives in cron_job_runs so a job card can render
        # the last N invocations + their stdout snippets without
        # bloating the parent row.
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS cron_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                schedule TEXT NOT NULL DEFAULT '',
                command TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                last_run_at TEXT NOT NULL DEFAULT '',
                last_status TEXT NOT NULL DEFAULT '',
                next_run_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_cron_jobs_enabled
                ON cron_jobs(enabled, next_run_at);

            CREATE TABLE IF NOT EXISTS cron_job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running'
                    CHECK(status IN ('running','done','failed','cancelled')),
                output_excerpt TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(job_id) REFERENCES cron_jobs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_cron_job_runs_job
                ON cron_job_runs(job_id, started_at DESC);
        """)
        self._conn.commit()

        # The legacy `tickets` table is gone -- JIRA cache rows moved
        # into `tasks` (ticket_* prefixed columns; type derived from
        # labels / issue_type). See bin/migrate_unify_tasks.py.

        # Seed default action definitions
        self._seed_action_defaults()

    def _seed_action_defaults(self):
        """Insert default action definitions (INSERT OR IGNORE for idempotency)."""
        defaults = [
            ("open", "Agent", "", "task", "", 0),
            ("do-task", "Do This Task", "Execute this task. Read the code, understand the requirements, write tests, implement, and commit.", "task", "", 1),
            ("evaluate", "Evaluate", "Evaluate whether this task is ready to start. Check dependencies, prerequisites, and codebase state. Report what needs to happen before work can begin.", "task", "", 2),
            ("create-ticket", "Create Ticket",
             "Create a JIRA ticket for this task. Follow the project JIRA conventions.\n"
             "Before creating, FIRST: (a) search for an existing ticket with a similar title or "
             "the same scope -- if one already exists, DO NOT create a duplicate; report the "
             "existing ticket id and stop. (b) check the project's umbrella_tickets list (from "
             "`eva-cli list-projects --json`) and use it as the parent/epic when applicable.\n"
             "Follow the JIRA instance's local conventions for assignee + labels -- if the "
             "project (or this user) has prior tickets in that instance, mirror their pattern; "
             "otherwise leave assignee empty unless the user told you otherwise this turn.",
             "task", "", 3),
            ("sync", "Sync Status", "Sync this task with external systems. Check GitHub PRs for CI and review status, check JIRA ticket status, and update the task accordingly using eva-cli.", "task", "", 4),
            ("fix-ci", "Fix CI", "Fix the CI failures on this PR. Analyze the CI log, find root cause, and provide a fix.", "pr", "ci_failed", 10),
            # All review-style PR actions open with the no-post rule.
            # Models otherwise drift into "helpful" mode and volunteer
            # to `gh pr review --approve` / `gh pr comment` -- the
            # leading IMPORTANT line is what stops them. Putting it
            # FIRST (not buried at the end) makes it the first context
            # the model sees when it parses the prompt.
            ("review", "Review PR",
             "IMPORTANT: Add findings as PENDING (draft) inline review "
             "comments on GitHub via "
             "`gh api -X POST repos/{owner}/{repo}/pulls/{number}/reviews` "
             "with `commit_id` and a `comments` array of "
             "`{path, line, side, body}` -- do NOT include `event` so the "
             "review state stays PENDING. I'll submit/discard manually. "
             "Do NOT run `gh pr review`, `gh pr comment`, or any "
             "submission command. Verify state=='PENDING' after creation.\n\n"
             "Review this PR. Check code quality, test coverage, "
             "correctness, edge cases. For each line-anchored finding, "
             "add a pending inline comment. Then summarize in chat: TL;DR, "
             "list of pending comments (file:line + one-line summary), and "
             "any high-level concerns that don't fit as inline comments.",
             "pr", "has_pr", 11),
            ("address-comments", "Address Comments",
             "IMPORTANT: DO NOT post anything to GitHub by default. "
             "Draft replies / fixes locally; show me the diff or the "
             "draft text. I'll review and post manually unless I "
             "explicitly tell you to in this turn.\n\n"
             "Address the review comments on this PR. Go through each "
             "comment and either propose a code fix (as a diff) or a "
             "reply message (as text).",
             "pr", "has_pr", 12),
            ("draft-reply", "Draft Reply",
             "IMPORTANT: DO NOT post anything to GitHub by default. "
             "Show me the drafts in this session; I'll copy/paste or "
             "tell you to post.\n\n"
             "Read the review comments on this PR and draft concise "
             "reply messages for each unresolved comment thread.",
             "pr", "has_pr", 13),
            # `auto-pr-tend` (long-running PR auto-baby-sit skill) is
            # workflow-specific and ships via the optional extension
            # that provides it -- see each extension's `seed.py` for
            # the action insert. Core only seeds the small set of
            # generic actions above.
            # --- Review-context actions -----------------------------
            # context='review' is only shown in the All Reviews UI on a
            # selected review PR. Deliberately slim: no fix-ci /
            # address-comments / update-pr (it's not my PR), no
            # create-ticket / do-task / evaluate (it's not a task).
            # Ids are prefixed `review-*` because `action_definitions.id`
            # is the table's PK -- a bare `draft-reply` already exists
            # under context='pr' and would collide.
            #
            # Same leading IMPORTANT line as the pr-context actions
            # above so a reviewer never accidentally has the agent post
            # `/approve` or comment-spam someone else's PR.
            ("review-pr", "Review PR",
             "IMPORTANT: do NOT post anything to GitHub. Inline findings "
             "go in as PENDING comments only (state='PENDING'); do NOT "
             "include `event` in the gh api call. Do NOT run "
             "`gh pr review --approve|--request-changes|--comment`, "
             "`gh pr comment`, or any submission command. The user "
             "double-confirms before anything ships.\n\n"
             "Goal: review this PR.\n\n"
             "Rules:\n"
             "1. Read the diff carefully. Check correctness, test "
             "coverage, edge cases, design, performance, security.\n"
             "2. Anchor every line-specific finding as a PENDING inline "
             "comment via:\n"
             "     gh api -X POST repos/{owner}/{repo}/pulls/{number}/reviews\n"
             "   with `commit_id` and a `comments` array of "
             "{path, line, side, body}. Verify with "
             "`gh api repos/{owner}/{repo}/pulls/{number}/reviews` that "
             "the new review's state is 'PENDING'. If a stale pending "
             "review already exists by you, DELETE it first (GitHub "
             "allows one pending per user per PR).\n"
             "3. Comments must be SHORT. State the issue and propose a "
             "fix in 1-2 sentences. No prose. No restating the diff.\n\n"
             "After the pending comments are in, write a SUMMARY in "
             "this chat IN ENGLISH with these sections:\n"
             "  - One-line TL;DR\n"
             "  - Pending comments I added (each: file:line + one-line note)\n"
             "  - Cross-file / high-level issues (not suitable for inline)\n"
             "  - Key questions for the author\n"
             "  - Recommendation: approve? (yes / no / need more info) "
             "plus one-line reason\n"
             "  - Who else may have context (check `gh pr view ... "
             "--json reviews,comments,assignees`, CODEOWNERS, recent "
             "git blame on the touched files; list 1-3 handles + why)",
             "review", "", 20),
            ("review-reply", "Draft Reply",
             "IMPORTANT: DO NOT post anything to GitHub by default. "
             "Show me the drafts in this session only.\n\n"
             "Read the review comments on this PR and draft concise "
             "reply messages for each unresolved comment thread.",
             "review", "", 21),
            ("review-sync", "Sync Status",
             "IMPORTANT: DO NOT post anything to GitHub. This action "
             "is read-only -- it pulls state from GitHub, never "
             "writes.\n\n"
             "Sync this review's local state with GitHub. Check "
             "whether I already approved / requested changes / "
             "commented on GitHub, and update `my_workflow_state` to "
             "'done' via `eva-cli update-review <url> --state done` "
             "if appropriate.",
             "review", "", 22),
        ]
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO action_definitions
                (id, label, prompt_template, context, condition, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            defaults,
        )
        # Drop retired actions on every boot. `update-pr` was replaced
        # by `auto-pr-tend` (the latter delegates to the widgets-dev
        # `/auto-pr-tend` skill, which does rebase + comment-reply + CI
        # auto-fix in a single watching loop). Existing DBs need an
        # explicit DELETE because INSERT OR IGNORE leaves the old row
        # behind otherwise.
        self._conn.execute(
            "DELETE FROM action_definitions WHERE id = ?", ("update-pr",),
        )

        # Force-refresh review-style action templates on every boot.
        # `INSERT OR IGNORE` alone preserves rows written by the
        # previous Eva version, which means prompt-template edits
        # (e.g. switching the review action from "show in chat only"
        # to "create a PENDING GitHub review") would never take effect
        # for upgrading users.
        #
        # The set is: every `context='review'` action AND the
        # pr-context actions whose work involves writing to GitHub
        # (review / address-comments / draft-reply / auto-pr-tend).
        # Other pr-context actions (fix-ci) and ALL task-context
        # actions are left alone so user-customised templates survive.
        _FORCE_REFRESH_PR_IDS = {"review", "address-comments", "draft-reply", "auto-pr-tend"}
        for action_id, _label, prompt, ctx, _cond, _order in defaults:
            if ctx == "review" or (
                ctx == "pr" and action_id in _FORCE_REFRESH_PR_IDS
            ):
                self._conn.execute(
                    "UPDATE action_definitions SET prompt_template=? "
                    "WHERE id=?",
                    (prompt, action_id),
                )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Project CRUD
    # ------------------------------------------------------------------

    def create_project(self, project_id, name="", description="", repo=None,
                       jira=None, has_tickets=False, design_doc=None,
                       umbrella_tickets=None):
        """Insert a new project. Returns project dict."""
        now = _now_iso()
        ut_json = json.dumps(umbrella_tickets or [])
        self._conn.execute(
            """INSERT OR IGNORE INTO projects
               (project_id, name, description, repo, jira, has_tickets,
                design_doc, umbrella_tickets, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, name, description, repo, jira,
             1 if has_tickets else 0, design_doc, ut_json, now, now),
        )
        self._conn.commit()
        return self.get_project(project_id)

    def get_project(self, project_id):
        """Return project dict or None."""
        row = self._conn.execute_fetchone(
            "SELECT * FROM projects WHERE project_id=?", (project_id,))
        if not row:
            return None
        d = _row_to_dict(row)
        d["id"] = d.pop("project_id")
        d["has_tickets"] = bool(d.get("has_tickets"))
        try:
            d["umbrella_tickets"] = json.loads(d.get("umbrella_tickets") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["umbrella_tickets"] = []
        return d

    def list_projects(self):
        """Return list of all project dicts."""
        rows = self._conn.execute_fetchall(
            "SELECT * FROM projects ORDER BY project_id")
        result = []
        for row in rows:
            d = _row_to_dict(row)
            d["id"] = d.pop("project_id")
            d["has_tickets"] = bool(d.get("has_tickets"))
            try:
                d["umbrella_tickets"] = json.loads(d.get("umbrella_tickets") or "[]")
            except (json.JSONDecodeError, TypeError):
                d["umbrella_tickets"] = []
            result.append(d)
        return result

    def update_project(self, project_id, **fields):
        """Update project fields. Returns updated project dict or None."""
        allowed = {"name", "description", "repo", "jira", "has_tickets",
                   "design_doc", "umbrella_tickets"}
        updates = {}
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "has_tickets":
                updates[k] = 1 if v else 0
            elif k == "umbrella_tickets":
                updates[k] = json.dumps(v) if isinstance(v, list) else v
            else:
                updates[k] = v
        if not updates:
            return self.get_project(project_id)
        updates["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{col}=?" for col in updates)
        values = list(updates.values()) + [project_id]
        self._conn.execute(f"UPDATE projects SET {set_clause} WHERE project_id=?", values)
        self._conn.commit()
        return self.get_project(project_id)

    def delete_project(self, project_id):
        """Delete a project. Returns True if deleted."""
        cur = self._conn.execute("DELETE FROM projects WHERE project_id=?", (project_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def project_exists(self, project_id):
        """Check if a project exists in DB."""
        row = self._conn.execute_fetchone(
            "SELECT 1 FROM projects WHERE project_id=?", (project_id,))
        return row is not None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # `project` is accepted for back-compat with existing callers but
    # unused -- task_id alone is now globally unique. Eventually a
    # follow-up cleanup pass can drop the parameter.

    def _get_dependencies(self, project: str, task_id: str) -> list:
        cur = self._conn.execute(
            "SELECT depends_on FROM task_dependencies WHERE task_id=?",
            (task_id,),
        )
        return [row["depends_on"] for row in cur.fetchall()]

    def _get_prs(self, project: str, task_id: str) -> list:
        cur = self._conn.execute(
            "SELECT * FROM prs WHERE task_id=?",
            (task_id,),
        )
        return [_pr_row_to_dict(row) for row in cur.fetchall()]

    def _get_history(self, project: str, task_id: str, limit: int = 50) -> list:
        """Most-recent-first list of history entries for a task."""
        cur = self._conn.execute(
            "SELECT ts, text FROM task_history "
            "WHERE task_id=? "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (task_id, limit),
        )
        return [{"ts": row["ts"], "text": row["text"]} for row in cur.fetchall()]

    def _task_row_to_dict(self, row, project: str, task_id: str) -> dict:
        d = _row_to_dict(row)
        d["dependencies"] = self._get_dependencies(project, task_id)
        d["prs"] = self._get_prs(project, task_id)
        d["history"] = self._get_history(project, task_id)
        # Parse follow_ups JSON string to list
        raw = d.get("follow_ups") or "[]"
        try:
            d["follow_ups"] = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            d["follow_ups"] = []
        # Alias group_name -> group and build ticket dict for backward compat
        if "group_name" in d:
            d["group"] = d["group_name"]
        if d.get("ticket_id"):
            d["ticket"] = {"id": d["ticket_id"], "url": d.get("ticket_url", "")}
        return d

    # ------------------------------------------------------------------
    # Public CRUD
    # ------------------------------------------------------------------

    def create_task(
        self,
        project: str,
        task_id: str,
        description: str = "",
        type: str = "feature",
        status: str = "not_started",
        group_name: str = "",
        notes: str = "",
        priority: int = 5,
        ticket_id=None,
        ticket_url=None,
    ) -> dict:
        """Insert a new task and return it as a dict. task_id must be
        globally unique. `project` is the optional folder grouping
        (pass '' or None for unsorted)."""
        _validate_status(status)
        now = _now_iso()
        self._conn.execute(
            """
            INSERT INTO tasks
                (task_id, project, description, type, status, group_name, notes,
                 priority, ticket_id, ticket_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, project or "", description, type, status, group_name, notes,
             priority, ticket_id, ticket_url, now, now),
        )
        self._conn.commit()
        return self.get_task(project, task_id)

    def get_task(self, project: str, task_id: str):
        """Return task dict with dependencies and prs, or None if not found.

        Lookup is by task_id alone (globally unique post-merge); the
        `project` parameter is accepted for back-compat but unused.
        """
        row = self._conn.execute_fetchone(
            "SELECT * FROM tasks WHERE task_id=?",
            (task_id,),
        )
        if row is None:
            return None
        return self._task_row_to_dict(row, row["project"] or "", task_id)

    def _validate_follow_ups(self, project: str, follow_ups: list) -> None:
        """Ensure follow_ups are natural language descriptions, not task IDs."""
        if not isinstance(follow_ups, list):
            raise ValueError("follow_ups must be a list of strings")
        task_ids = {r["task_id"] for r in self._conn.execute_fetchall(
            "SELECT task_id FROM tasks"
        )}
        for item in follow_ups:
            if not isinstance(item, str):
                raise ValueError(f"follow_up must be a string, got {type(item).__name__}")
            if item in task_ids:
                raise ValueError(
                    f"follow_up '{item}' is a task ID. "
                    "Use dependencies for task-to-task relationships. "
                    "Follow-ups should be natural language descriptions."
                )

    def update_task(self, project: str, task_id: str, **fields) -> dict:
        """Update only the provided fields and auto-stamp updated_at.

        Match is by task_id alone (globally unique). `project` accepted
        for back-compat but unused for matching; if `project` appears
        in `fields` we DO write it (re-grouping).
        """
        allowed = {
            "project",
            "description", "type", "status", "group_name", "notes",
            "priority", "ticket_id", "ticket_url", "follow_ups",
            # ticket_* / review_* cache columns are settable so jira_sync
            # + review-sync can refresh them in-place.
            "ticket_summary", "ticket_priority", "ticket_issue_type",
            "ticket_project_key", "ticket_assignee_email",
            "ticket_reporter_email", "ticket_status",
            "ticket_status_category", "ticket_labels",
            "ticket_components", "ticket_fix_versions",
            "ticket_parent_key", "ticket_resolution",
            "ticket_instance", "ticket_synced_at",
            "review_my_review_state", "review_my_workflow_state",
            "review_started_at", "review_last_seen_comment_count",
            "review_added_at", "review_source", "review_dirty",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if "status" in updates:
            _validate_status(updates["status"])
        if "follow_ups" in updates:
            self._validate_follow_ups(project, updates["follow_ups"])
            updates["follow_ups"] = json.dumps(updates["follow_ups"])
        if not updates:
            return self.get_task(project, task_id)

        updates["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{col}=?" for col in updates)
        values = list(updates.values()) + [task_id]
        self._conn.execute(
            f"UPDATE tasks SET {set_clause} WHERE task_id=?",
            values,
        )
        self._conn.commit()
        return self.get_task(project, task_id)

    def delete_task(self, project: str, task_id: str) -> bool:
        """Delete a task (cascades to dependencies, prs, and history).
        Returns True if deleted. `project` is unused (task_id is global)."""
        cur = self._conn.execute(
            "DELETE FROM tasks WHERE task_id=?",
            (task_id,),
        )
        # Explicit task_history cleanup -- FOREIGN KEY ON DELETE CASCADE
        # doesn't apply here because task_history isn't FK'd to tasks.
        self._conn.execute(
            "DELETE FROM task_history WHERE task_id=?",
            (task_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_tasks(self, project: str) -> list:
        """Return list of task dicts for the given project. Pass
        `project=''` to list unsorted tasks; pass `project=None` (via
        positional ``None``) to list every task across folders."""
        if project is None:
            rows = self._conn.execute_fetchall(
                "SELECT * FROM tasks"
            )
        else:
            rows = self._conn.execute_fetchall(
                "SELECT * FROM tasks WHERE project=?",
                (project,),
            )
        return [self._task_row_to_dict(row, row["project"] or "", row["task_id"]) for row in rows]

    # ------------------------------------------------------------------
    # Task history (append-only timeline replacing the mutable `notes` blob)
    # ------------------------------------------------------------------

    # Hard cap on a single history line. Matches the Eva convention of
    # "one terse line per step" -- longer prose belongs in PR descriptions
    # or design docs, not the per-task timeline.
    TASK_HISTORY_MAX_CHARS = 100

    @staticmethod
    def _validate_history_text(text: str, max_chars: int) -> str:
        """Trim + validate a one-line history entry. Returns the cleaned
        string. Raises ValueError on empty / oversize so callers can
        map to HTTP 422 uniformly. Shared by `append_task_history` and
        `append_review_history` -- the two append paths used to maintain
        their own copies which drifted (task said "one line, what
        changed", review said "one line"). One helper, one wording.
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("history text is empty")
        if len(text) > max_chars:
            raise ValueError(
                f"history text {len(text)} > {max_chars} chars; "
                "keep entries terse (one line, what changed)"
            )
        return text

    def append_task_history(self, project: str, task_id: str, text: str,
                            ts: str = "") -> dict:
        """Insert one history entry. Raises ValueError if text is empty,
        longer than TASK_HISTORY_MAX_CHARS, or the task doesn't exist
        (otherwise history could leak across delete/recreate cycles)."""
        text = self._validate_history_text(text, self.TASK_HISTORY_MAX_CHARS)
        if not self.get_task(project, task_id):
            raise ValueError(f"task {task_id!r} not found")
        ts = ts or _now_iso()
        self._conn.execute(
            "INSERT INTO task_history (project, task_id, ts, text) VALUES (?, ?, ?, ?)",
            (project or "", task_id, ts, text),
        )
        self._conn.commit()
        return {"ts": ts, "text": text}

    def list_task_history(self, project: str, task_id: str, limit: int = 50) -> list:
        """Public wrapper around `_get_history` for callers outside the DB class."""
        return self._get_history(project, task_id, limit=limit)

    # ------------------------------------------------------------------
    # Dependency methods
    # ------------------------------------------------------------------

    def add_dependency(self, project: str, task_id: str, depends_on: str) -> None:
        """Add a dependency for a task (INSERT OR IGNORE)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO task_dependencies (task_id, depends_on) VALUES (?, ?)",
            (task_id, depends_on),
        )
        self._conn.commit()

    def list_dependents(self, project: str, depends_on: str) -> list:
        """Return task_ids that depend on `depends_on`. Direct edges
        only (callers can recurse for transitive). `project` is unused
        post-merge -- dep graph is now global."""
        cur = self._conn.execute(
            "SELECT task_id FROM task_dependencies WHERE depends_on=?",
            (depends_on,),
        )
        return [r[0] for r in cur.fetchall()]

    def remove_dependency(self, project: str, task_id: str, depends_on: str) -> None:
        """Remove a dependency edge."""
        self._conn.execute(
            "DELETE FROM task_dependencies WHERE task_id=? AND depends_on=?",
            (task_id, depends_on),
        )
        self._conn.commit()

    def set_dependencies(self, project: str, task_id: str, deps_list: list) -> None:
        """Replace all dependencies for a task atomically."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM task_dependencies WHERE task_id=?",
                (task_id,),
            )
            self._conn.executemany(
                "INSERT INTO task_dependencies (task_id, depends_on) VALUES (?, ?)",
                [(task_id, dep) for dep in deps_list],
            )

    def is_task_blocked(self, project: str, task_id: str) -> bool:
        """Return True iff any dependency's status is NOT in
        UNBLOCKING_DEP_STATUSES = {done, closed, needs_follow_up}.

        A dependency that does not exist in the tasks table is treated
        as blocking (the dep was deleted but the edge wasn't cleaned).
        """
        placeholders = ",".join("?" for _ in UNBLOCKING_DEP_STATUSES)
        cur = self._conn.execute(
            f"""
            SELECT COUNT(*) AS cnt
            FROM task_dependencies td
            LEFT JOIN tasks t ON t.task_id = td.depends_on
            WHERE td.task_id = ?
              AND (t.task_id IS NULL OR t.status NOT IN ({placeholders}))
            """,
            (task_id, *sorted(UNBLOCKING_DEP_STATUSES)),
        )
        row = cur.fetchone()
        return row["cnt"] > 0

    # ------------------------------------------------------------------
    # PR methods
    # ------------------------------------------------------------------

    def add_pr(
        self,
        project: str,
        task_id: str,
        number: int,
        url: str = "",
        status: str = "open",
        title: str = "",
        session=None,
        working_dir: str = "~",
        ci_status: str = "unknown",
        review_status: str = "",
        comment_count: int = 0,
        additions: int = 0,
        deletions: int = 0,
        author: str = "",
        head_branch: str = "",
        base_branch: str = "",
        last_updated: str = "",
    ) -> None:
        """Insert a PR row and touch tasks.updated_at. `project` accepted
        for back-compat but unused -- PR is FK'd by task_id alone now."""
        self._conn.execute(
            """
            INSERT INTO prs (task_id, number, url, status, title, session, working_dir,
                             ci_status, review_status, comment_count, additions, deletions, author, head_branch, base_branch, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, number, url, status, title, session, working_dir,
             ci_status, review_status, comment_count, additions, deletions, author, head_branch, base_branch, last_updated),
        )
        self._conn.execute(
            "UPDATE tasks SET updated_at=? WHERE task_id=?",
            (_now_iso(), task_id),
        )
        self._conn.commit()

    def delete_pr(self, project: str, task_id: str, number: int) -> bool:
        """Delete a PR row. Returns True if a row was deleted."""
        cur = self._conn.execute(
            "DELETE FROM prs WHERE task_id=? AND number=?",
            (task_id, number),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # PR-row column whitelist for `update_pr` / `update_pr_by_number`.
    # Centralised so a new column added later is picked up by both
    # entry points.
    _PR_UPDATE_ALLOWED = frozenset({
        "url", "status", "title", "session", "working_dir",
        "ci_status", "review_status", "comment_count",
        "additions", "deletions",
        "author", "head_branch", "base_branch", "last_updated",
        "status_changed_at",
    })

    def _update_pr_rows(self, *, where_sql: str, where_params: tuple,
                        fields: dict) -> None:
        """Shared writer for `update_pr` / `update_pr_by_number`. The
        two entry points differ only in their WHERE clause; this
        helper applies the column whitelist + the one-way
        `status_changed_at` stamp logic uniformly so the two paths
        can't drift.

        Optimisation (memo item #5): skip the UPDATE entirely when
        every whitelisted field already matches the current row. The
        PR poller calls this on every PR every tick (~60s); on a busy
        account that's hundreds of no-op UPDATEs per minute. SQLite
        still writes the journal + fsyncs even for value-identical
        UPDATEs, so the diff-check pays off.
        """
        updates = {k: v for k, v in fields.items()
                   if k in self._PR_UPDATE_ALLOWED}
        if not updates:
            return
        # Read the current row once: needed for both the diff check
        # AND the status_changed_at stamping below.
        current_cols = list(updates.keys())
        if "status" in updates:
            current_cols.append("status_changed_at")
        cols_csv = ", ".join(current_cols)
        current = self._conn.execute_fetchone(
            f"SELECT {cols_csv} FROM prs WHERE {where_sql}",
            where_params,
        )
        if current is None:
            # Row not found -- skip silently (unique index already
            # ensures uniqueness and old code also no-op'd here).
            return
        # Auto-stamp status_changed_at on transition. Gate on the
        # ORIGINAL `updates` dict (not the post-diff one) so a caller
        # who explicitly passed `status_changed_at=''` -- to clear --
        # is respected and we don't override their intent. Only
        # auto-stamp when the caller DIDN'T mention status_changed_at
        # at all.
        if ("status" in updates
                and "status_changed_at" not in updates
                and (current["status"] or "") != (updates["status"] or "")
                and not (current["status_changed_at"] or "").strip()):
            updates["status_changed_at"] = _now_iso()
        # Filter to fields that actually changed. `current` may not
        # carry every key in `updates` (we only SELECT'd the original
        # set; status_changed_at may have been added by the stamp
        # branch above). For absent columns, treat the prior value as
        # empty so the new value gets written.
        def _cur(k):
            try:
                return current[k] or ""
            except (IndexError, KeyError):
                return ""
        changed = {
            k: v for k, v in updates.items()
            if _cur(k) != (v or "")
        }
        if not changed:
            # Every field already matches -- skip the UPDATE entirely.
            return
        set_clause = ", ".join(f"{col}=?" for col in changed)
        values = list(changed.values()) + list(where_params)
        self._conn.execute(
            f"UPDATE prs SET {set_clause} WHERE {where_sql}",
            values,
        )
        self._conn.commit()

    def update_pr(self, project: str, task_id: str, number: int, **fields) -> None:
        """Update only the provided fields of a PR row.

        When `status` is in the update and it actually changes AND the
        row has no prior `status_changed_at`, stamp it with now() so
        the worklog generator can filter by real transitions. The
        stamp is a one-way backfill: once set (by this helper, or by
        a caller supplying a GitHub-authoritative mergedAt/closedAt),
        we never rewrite it on subsequent polls.
        """
        self._update_pr_rows(
            where_sql="task_id=? AND number=?",
            where_params=(task_id, number),
            fields=fields,
        )

    def list_all_prs(self, status: str = "", search: str = "") -> list:
        """List all PRs across all tasks, optionally filtered by status or search."""
        query = "SELECT p.*, t.description as task_description, t.status as task_status, t.project as project FROM prs p JOIN tasks t ON p.task_id=t.task_id"
        conditions = []
        params = []
        if status:
            conditions.append("p.status=?")
            params.append(status)
        if search:
            conditions.append("(p.title LIKE ? OR t.task_id LIKE ?)")
            params.extend(["%" + search + "%"] * 2)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY p.number DESC"
        rows = self._conn.execute_fetchall(query, params)
        return [_pr_row_to_dict(r) for r in rows]

    def find_pr_by_number(self, number: int):
        """Find a PR by number across all tasks. Returns dict or None."""
        row = self._conn.execute_fetchone(
            "SELECT p.*, t.description as task_description, t.project as project FROM prs p JOIN tasks t ON p.task_id=t.task_id WHERE p.number=? LIMIT 1",
            (number,),
        )
        return _pr_row_to_dict(row) if row else None

    def update_pr_by_number(self, number: int, **fields):
        """Update a PR by number (across all projects).

        Mirrors `update_pr`: one-way `status_changed_at` stamp when
        status flips AND the column is still empty. See `update_pr`
        docstring for rationale.
        """
        self._update_pr_rows(
            where_sql="number=?",
            where_params=(number,),
            fields=fields,
        )

    def mark_pr_seen(self, number: int) -> bool:
        """Snapshot the row's current comment_count into
        last_seen_comment_count. Frontend calls this when the user
        opens a PR in All-PRs / Project Page so any further
        comment_count growth surfaces as a "N new" badge on the PR
        node. Returns True if the row exists, False otherwise."""
        cur = self._conn.execute(
            "UPDATE prs "
            "SET last_seen_comment_count = comment_count "
            "WHERE number = ?",
            (number,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Dirty pin (mark PRs that need sync)
    # ------------------------------------------------------------------

    def mark_pr_dirty(self, number: int) -> None:
        """Mark a PR as dirty (needs sync) by number."""
        self._conn.execute("UPDATE prs SET dirty=1 WHERE number=?", (number,))
        self._conn.commit()

    def list_dirty_prs(self) -> list:
        """Return all dirty PRs across all tasks."""
        rows = self._conn.execute_fetchall(
            "SELECT p.*, t.description as task_description, t.status as task_status, t.project as project FROM prs p JOIN tasks t ON p.task_id=t.task_id WHERE p.dirty=1"
        )
        return [dict(r) for r in rows]

    def clear_pr_dirty(self, number: int) -> None:
        """Clear dirty flag for a PR after sync."""
        self._conn.execute("UPDATE prs SET dirty=0 WHERE number=?", (number,))
        self._conn.commit()

    def clear_all_dirty(self) -> None:
        """Clear all dirty flags."""
        self._conn.execute("UPDATE prs SET dirty=0 WHERE dirty=1")
        self._conn.commit()

    # ------------------------------------------------------------------
    # Review queue: one table for manual pins, GitHub review-requested,
    # @mentions, with enriched CI / review / diff metadata.
    # ------------------------------------------------------------------

    # Whitelist of fields that upsert_review_pr is allowed to write.
    # Keeps the surface explicit so a typo'd key doesn't end up silently
    # shadowing schema columns.
    _REVIEW_PR_FIELDS = frozenset({
        "title", "author", "status",
        "ci_status", "review_status", "my_review_state",
        "comment_count", "additions", "deletions",
        "head_branch", "base_branch",
        "last_updated", "source",
        # Review-workflow columns (added when a user starts reviewing).
        "session_name", "agent_session_id", "started_at",
        "my_workflow_state",
    })

    # Enum for `review_prs.my_workflow_state`. 'queued' = in the queue
    # but I haven't touched it. 'active' = I opened a session. 'done'
    # = I finished (approved / commented / whatever). 'dismissed' = I
    # explicitly don't intend to review.
    REVIEW_WORKFLOW_STATES = frozenset({
        "queued", "active", "done", "dismissed",
    })

    # Enum for `review_prs.my_review_state`. Matches the sqlite
    # CHECK constraint on that column; the Python layer enforces the
    # same set so caller bugs surface at write time rather than as a
    # cryptic IntegrityError later.
    REVIEW_STATES = frozenset({
        "",                   # no involvement yet
        "pending_review",     # GitHub asked me (or re-asked me)
        "approved",           # my latest review was APPROVE
        "changes_requested",  # my latest review was REQUEST_CHANGES
        "commented",          # my latest review was COMMENT
    })

    # All review_prs methods below now back onto the unified `tasks`
    # table (type='review'). Each "review row" is one task row plus one
    # `prs` row attached to it (review tasks are 1:1 with their PR).
    # The methods preserve the old return shape so route + frontend code
    # don't have to change in this commit.

    @staticmethod
    def _review_task_id_from_url(url: str) -> str:
        """`https://github.com/owner/repo/pull/N` -> `review-owner-repo-N`.

        Mirrors the migration's slug logic (`bin/migrate_unify_tasks.py
        :review_task_id`). Returns '' for un-parseable URLs."""
        import re as _re
        m = _re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", url or "")
        if not m:
            return ""
        slug = _re.sub(r"[^a-zA-Z0-9]+", "-",
                       f"{m.group(1)}/{m.group(2)}").strip("-").lower()
        return f"review-{slug}-{m.group(3)}"

    @staticmethod
    def _repo_from_review_url(url: str) -> str:
        import re as _re
        m = _re.search(r"github\.com/([^/]+)/([^/]+)/pull/", url or "")
        return f"{m.group(1)}/{m.group(2)}" if m else ""

    def _review_row_from_task(self, task_row, pr_row) -> dict:
        """Reshape a (task, pr) pair back into the old review_prs row
        dict so existing callers see the legacy shape. `task_row` is a
        Row from `tasks` (type='review'); `pr_row` is the matching prs
        row (may be None if a review task has no PR attached)."""
        url = pr_row["url"] if pr_row else ""
        # Pull session_name + agent_session_id from sessions if open.
        sess = self._conn.execute_fetchone(
            "SELECT tmux_name, agent_session_id FROM sessions WHERE task_id=?",
            (task_row["task_id"],),
        )
        return {
            "url": url,
            "repo": self._repo_from_review_url(url),
            "number": (pr_row["number"] if pr_row else 0),
            "title": (pr_row["title"] if pr_row else (task_row["description"] or "")),
            "author": (pr_row["author"] if pr_row else ""),
            "status": (pr_row["status"] if pr_row else "open"),
            "ci_status": (pr_row["ci_status"] if pr_row else "unknown"),
            "review_status": (pr_row["review_status"] if pr_row else ""),
            "my_review_state": task_row["review_my_review_state"] or "",
            "comment_count": (pr_row["comment_count"] if pr_row else 0),
            "additions": (pr_row["additions"] if pr_row else 0),
            "deletions": (pr_row["deletions"] if pr_row else 0),
            "head_branch": (pr_row["head_branch"] if pr_row else ""),
            "base_branch": (pr_row["base_branch"] if pr_row else ""),
            "last_updated": (pr_row["last_updated"] if pr_row else ""),
            "source": task_row["review_source"] or "manual",
            "added_at": task_row["review_added_at"] or "",
            "synced_at": task_row["updated_at"] or "",
            "dirty": task_row["review_dirty"] or 0,
            "last_seen_comment_count": task_row["review_last_seen_comment_count"] or 0,
            "session_name": (sess["tmux_name"] if sess else ""),
            "agent_session_id": (sess["agent_session_id"] if sess else ""),
            "started_at": task_row["review_started_at"] or "",
            "my_workflow_state": task_row["review_my_workflow_state"] or "queued",
        }

    def upsert_review_pr(self, url: str, repo: str, number: int, **fields) -> dict:
        """Insert or merge a review row. Splits the update across the
        `tasks` row (workflow state) and the `prs` row (GitHub metadata)
        so callers can keep using the old `(url, repo, number, **fields)`
        signature."""
        task_id = self._review_task_id_from_url(url)
        if not task_id:
            raise ValueError(f"can't derive review task_id from url {url!r}")
        now = _now_iso()
        existing_task = self._conn.execute_fetchone(
            "SELECT * FROM tasks WHERE task_id=?", (task_id,),
        )
        # Validate enum kwargs before any write.
        if "my_review_state" in fields and fields["my_review_state"] not in self.REVIEW_STATES:
            raise ValueError(
                f"my_review_state must be one of {sorted(self.REVIEW_STATES)}; "
                f"got {fields['my_review_state']!r}",
            )
        if ("my_workflow_state" in fields
                and fields["my_workflow_state"] not in self.REVIEW_WORKFLOW_STATES):
            raise ValueError(
                f"my_workflow_state must be one of "
                f"{sorted(self.REVIEW_WORKFLOW_STATES)}; got {fields['my_workflow_state']!r}",
            )

        # Task-side fields: workflow + tracking
        task_updates = {"updated_at": now}
        for k_old, k_new in (
            ("my_review_state", "review_my_review_state"),
            ("my_workflow_state", "review_my_workflow_state"),
            ("started_at", "review_started_at"),
            ("source", "review_source"),
        ):
            if k_old in fields:
                task_updates[k_new] = fields[k_old]
        if existing_task is None:
            # Create the review task row.
            self._conn.execute(
                """
                INSERT INTO tasks (task_id, project, type, description, status,
                    review_added_at, review_source, review_my_workflow_state,
                    review_my_review_state, review_started_at,
                    created_at, updated_at)
                VALUES (?, '', 'review', ?, 'not_started',
                    ?, ?, ?, ?, ?,
                    ?, ?)
                """,
                (
                    task_id,
                    fields.get("title", "") or "",
                    now,
                    fields.get("source", "manual"),
                    fields.get("my_workflow_state", "queued"),
                    fields.get("my_review_state", ""),
                    fields.get("started_at", ""),
                    now, now,
                ),
            )
        elif task_updates:
            set_clause = ", ".join(f"{c}=?" for c in task_updates)
            self._conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE task_id=?",
                list(task_updates.values()) + [task_id],
            )

        # PR-side fields: ci/review/title/author etc. on the prs row.
        existing_pr = self._conn.execute_fetchone(
            "SELECT * FROM prs WHERE task_id=? AND number=?", (task_id, number),
        )
        pr_field_map = {
            "title": "title", "author": "author", "status": "status",
            "ci_status": "ci_status", "review_status": "review_status",
            "comment_count": "comment_count",
            "additions": "additions", "deletions": "deletions",
            "head_branch": "head_branch", "base_branch": "base_branch",
            "last_updated": "last_updated",
        }
        pr_updates = {pr_col: fields[k]
                      for k, pr_col in pr_field_map.items() if k in fields}
        if existing_pr is None:
            self._conn.execute(
                """
                INSERT INTO prs (task_id, number, url, status, title,
                                 ci_status, review_status, comment_count,
                                 additions, deletions, author,
                                 head_branch, base_branch, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id, number, url,
                    fields.get("status", "open"),
                    fields.get("title", ""),
                    fields.get("ci_status", "unknown"),
                    fields.get("review_status", "review_required"),
                    fields.get("comment_count", 0),
                    fields.get("additions", 0),
                    fields.get("deletions", 0),
                    fields.get("author", ""),
                    fields.get("head_branch", ""),
                    fields.get("base_branch", ""),
                    fields.get("last_updated", ""),
                ),
            )
        elif pr_updates:
            set_clause = ", ".join(f"{c}=?" for c in pr_updates)
            self._conn.execute(
                f"UPDATE prs SET {set_clause} WHERE task_id=? AND number=?",
                list(pr_updates.values()) + [task_id, number],
            )
        # Session columns from the legacy upsert. session_name='' (or
        # agent_session_id='') means "clear the session reference"
        # (callers use this on kill); a non-empty value writes through
        # to the sessions table.
        if "session_name" in fields or "agent_session_id" in fields:
            clearing = (
                fields.get("session_name", None) == ""
                and fields.get("agent_session_id", None) == ""
            )
            if clearing:
                self._conn.execute(
                    "DELETE FROM sessions WHERE task_id=?", (task_id,),
                )
            else:
                sess = self._conn.execute_fetchone(
                    "SELECT * FROM sessions WHERE task_id=?", (task_id,),
                )
                tmux_name = (fields.get("session_name")
                             or (sess["tmux_name"] if sess else task_id))
                agent_sid = (fields.get("agent_session_id")
                             or (sess["agent_session_id"] if sess else ""))
                if sess is None:
                    self._conn.execute(
                        "INSERT INTO sessions (task_id, project, tmux_name, "
                        "agent_session_id, created_at, updated_at) "
                        "VALUES (?, '', ?, ?, ?, ?)",
                        (task_id, tmux_name, agent_sid, now, now),
                    )
                else:
                    self._conn.execute(
                        "UPDATE sessions SET tmux_name=?, agent_session_id=?, "
                        "updated_at=? WHERE task_id=?",
                        (tmux_name, agent_sid, now, task_id),
                    )
        self._conn.commit()
        return self.get_review_pr(url) or {}

    def get_review_pr(self, url: str):
        task_id = self._review_task_id_from_url(url)
        if not task_id:
            return None
        task_row = self._conn.execute_fetchone(
            "SELECT * FROM tasks WHERE task_id=? AND type='review'",
            (task_id,),
        )
        if task_row is None:
            return None
        pr_row = self._conn.execute_fetchone(
            "SELECT * FROM prs WHERE task_id=? LIMIT 1", (task_id,),
        )
        return self._review_row_from_task(task_row, pr_row)

    def list_review_prs(self) -> list:
        """All review tasks, most-recently-updated first. Sort key is
        the attached PR's `last_updated` (GitHub's updatedAt) so new
        activity floats to the top."""
        rows = self._conn.execute_fetchall(
            "SELECT t.*, p.url AS _pr_url FROM tasks t "
            "LEFT JOIN prs p ON p.task_id = t.task_id "
            "WHERE t.type='review' "
            "ORDER BY COALESCE(p.last_updated, t.review_added_at) DESC, "
            "         t.review_added_at DESC"
        )
        out = []
        for r in rows:
            pr_row = None
            if r["_pr_url"]:
                pr_row = self._conn.execute_fetchone(
                    "SELECT * FROM prs WHERE task_id=?", (r["task_id"],),
                )
            out.append(self._review_row_from_task(r, pr_row))
        return out

    def delete_review_pr(self, url: str) -> bool:
        task_id = self._review_task_id_from_url(url)
        if not task_id:
            return False
        cur = self._conn.execute(
            "DELETE FROM tasks WHERE task_id=? AND type='review'",
            (task_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def mark_review_seen(self, url: str) -> bool:
        """Snapshot the row's current PR comment_count into
        review_last_seen_comment_count on the task."""
        task_id = self._review_task_id_from_url(url)
        if not task_id:
            return False
        pr = self._conn.execute_fetchone(
            "SELECT comment_count FROM prs WHERE task_id=? LIMIT 1", (task_id,),
        )
        if pr is None:
            return False
        cur = self._conn.execute(
            "UPDATE tasks SET review_last_seen_comment_count=? "
            "WHERE task_id=? AND type='review'",
            (pr["comment_count"] or 0, task_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def mark_review_pr_dirty(self, *, url: str = "", number: int = 0) -> bool:
        """Flag a review task for the next sync pass. Match by url
        (preferred) or by PR number."""
        if url:
            task_id = self._review_task_id_from_url(url)
            if not task_id:
                return False
            cur = self._conn.execute(
                "UPDATE tasks SET review_dirty=1 "
                "WHERE task_id=? AND type='review'",
                (task_id,),
            )
        elif number:
            cur = self._conn.execute(
                "UPDATE tasks SET review_dirty=1 "
                "WHERE type='review' AND task_id IN "
                "  (SELECT task_id FROM prs WHERE number=?)",
                (number,),
            )
        else:
            return False
        self._conn.commit()
        return cur.rowcount > 0

    def list_dirty_review_prs(self) -> list:
        rows = self._conn.execute_fetchall(
            "SELECT t.*, p.url AS _pr_url FROM tasks t "
            "LEFT JOIN prs p ON p.task_id = t.task_id "
            "WHERE t.type='review' AND t.review_dirty=1"
        )
        out = []
        for r in rows:
            pr_row = None
            if r["_pr_url"]:
                pr_row = self._conn.execute_fetchone(
                    "SELECT * FROM prs WHERE task_id=?", (r["task_id"],),
                )
            out.append(self._review_row_from_task(r, pr_row))
        return out

    def clear_review_pr_dirty(self, url: str) -> None:
        task_id = self._review_task_id_from_url(url)
        if not task_id:
            return
        self._conn.execute(
            "UPDATE tasks SET review_dirty=0 "
            "WHERE task_id=? AND type='review'",
            (task_id,),
        )
        self._conn.commit()

    def clear_all_review_pr_dirty(self) -> None:
        self._conn.execute(
            "UPDATE tasks SET review_dirty=0 "
            "WHERE type='review' AND review_dirty=1"
        )
        self._conn.commit()

    # ----- review_history (append-only timeline per review PR) --------
    # Same terse-line convention as task_history: one line per event,
    # capped at REVIEW_HISTORY_MAX_CHARS. Longer prose belongs in the PR
    # description or a comment, not here.

    REVIEW_HISTORY_MAX_CHARS = 100

    def append_review_history(self, review_url: str, text: str,
                              source: str = "manual",
                              ts: str = "") -> dict:
        """Insert one review-history entry. Backed by task_history now;
        we synthesise the entry's `source` into a `[source] ` prefix
        on the text so the single timeline table can absorb the
        bookkeeping that used to live in `review_history`."""
        text = self._validate_history_text(text, self.REVIEW_HISTORY_MAX_CHARS)
        task_id = self._review_task_id_from_url(review_url)
        if not task_id or not self._conn.execute_fetchone(
            "SELECT 1 FROM tasks WHERE task_id=? AND type='review'", (task_id,),
        ):
            raise ValueError(f"review_pr {review_url!r} not found")
        ts = ts or _now_iso()
        src = source or "manual"
        stored_text = text if src == "manual" else f"[{src}] {text}"
        self._conn.execute(
            "INSERT INTO task_history (project, task_id, ts, text) "
            "VALUES ('', ?, ?, ?)",
            (task_id, ts, stored_text),
        )
        self._conn.commit()
        return {"ts": ts, "text": text, "source": src}

    def list_review_history(self, review_url: str, limit: int = 50) -> list:
        """Return review-history entries newest-first by undoing the
        `[source] ...` prefix that `append_review_history` writes."""
        task_id = self._review_task_id_from_url(review_url)
        if not task_id:
            return []
        rows = self._conn.execute_fetchall(
            "SELECT ts, text FROM task_history "
            "WHERE task_id=? ORDER BY ts DESC LIMIT ?",
            (task_id, max(1, int(limit or 50))),
        )
        out = []
        for r in rows:
            t = r["text"] or ""
            src = "manual"
            if t.startswith("[") and "] " in t:
                bracket = t.index("] ")
                src = t[1:bracket]
                t = t[bracket + 2:]
            out.append({"ts": r["ts"], "text": t, "source": src})
        return out

    # ----- Legacy aliases (review_watchlist semantic) ----------------
    # Kept until the last caller of the watchlist-named API is gone.
    # Backed by review_prs so there's only one row-per-URL of truth.
    # pylint: disable=missing-function-docstring

    def add_review_watch(self, url: str, repo: str, number: int, *,
                         title: str = "", author: str = "",
                         status: str = "open", last_updated: str = "") -> dict:
        return self.upsert_review_pr(
            url, repo, number,
            title=title, author=author, status=status,
            last_updated=last_updated, source="manual",
        )

    def get_review_watch(self, url: str):
        return self.get_review_pr(url)

    def list_review_watch(self) -> list:
        """Only manual pins, added-newest-first (legacy contract)."""
        rows = self._conn.execute_fetchall(
            "SELECT t.*, p.url AS _pr_url FROM tasks t "
            "LEFT JOIN prs p ON p.task_id = t.task_id "
            "WHERE t.type='review' "
            "  AND COALESCE(t.review_source, 'manual') IN ('manual', 'both') "
            "ORDER BY t.review_added_at DESC"
        )
        out = []
        for r in rows:
            pr_row = None
            if r["_pr_url"]:
                pr_row = self._conn.execute_fetchone(
                    "SELECT * FROM prs WHERE task_id=?", (r["task_id"],),
                )
            out.append(self._review_row_from_task(r, pr_row))
        return out

    def delete_review_watch(self, url: str) -> bool:
        return self.delete_review_pr(url)

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def project_stats(self, project: str) -> dict:
        """Return status counts and progress percentage for a project.

        Stored statuses only -- `blocked` is computed and never stored,
        so the count slot for it isn't reported here. Callers that
        want the live blocked count should iterate tasks and apply
        `common.tasks.is_task_blocked` (which UI does via effective_status).
        """
        counts = {s: 0 for s in sorted(VALID_STATUSES)}

        cur = self._conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM tasks WHERE project=? GROUP BY status",
            (project,),
        )
        total = 0
        for row in cur.fetchall():
            status = row["status"]
            cnt = row["cnt"]
            counts[status] = cnt
            total += cnt

        progress = round(counts["done"] / total * 100, 1) if total > 0 else 0.0
        return {"total": total, "counts": counts, "progress": progress}

    def dependency_graph(self, project: str) -> dict:
        """Return a graph representation of all tasks and their dependencies."""
        tasks = self.list_tasks(project)
        nodes = []
        edges = []
        groups_set = set()

        for task in tasks:
            task_id = task["task_id"]
            effective = dict(task)

            # Compute `blocked` for any non-terminal task with unclosed
            # deps. Terminal (`done`, `closed`) tasks are exempt -- once
            # the task is over, dep state no longer matters.
            if task["status"] not in TERMINAL_TASK_STATUSES \
                    and self.is_task_blocked(project, task_id):
                effective["status"] = "blocked"

            nodes.append(effective)

            for dep_id in task.get("dependencies", []):
                edges.append({"from": dep_id, "to": task_id})

            if task.get("group_name"):
                groups_set.add(task["group_name"])

        return {"nodes": nodes, "edges": edges, "groups": sorted(groups_set)}

    def find_task_by_ticket(self, ticket_id: str):
        """Find a task by ticket_id across all projects. Returns (project, task_id) or None."""
        row = self._conn.execute_fetchone(
            "SELECT project, task_id FROM tasks WHERE ticket_id=? LIMIT 1", (ticket_id,)
        )
        if row:
            return (row[0], row[1])
        # Also check description
        row = self._conn.execute_fetchone(
            "SELECT project, task_id FROM tasks WHERE description LIKE ? LIMIT 1",
            ("%" + ticket_id + "%",)
        )
        return (row[0], row[1]) if row else None

    def find_tasks_by_ticket(self, ticket_id: str):
        """Find OTHER tasks that reference this ticket. The ticket-task
        itself (post-merge, the JIRA row IS a task with task_id == key)
        is excluded -- the UI uses this list to render "linked tasks"
        meaning "external work attached to this ticket", not the
        ticket's own row. Returns list of (project, task_id, status)."""
        rows = self._conn.execute_fetchall(
            "SELECT project, task_id, status FROM tasks "
            "WHERE ticket_id=? AND task_id != ?",
            (ticket_id, ticket_id),
        )
        return [(r[0], r[1], r[2]) for r in rows]

    def rename_task(self, project: str, old_id: str, new_id: str) -> bool:
        """Rename a task_id atomically: update task, deps, reverse deps,
        and PRs. `project` accepted for back-compat but unused (task_id
        is globally unique post-merge)."""
        with self._conn:
            if not self._conn.execute(
                "SELECT 1 FROM tasks WHERE task_id=?", (old_id,)
            ).fetchone():
                return False
            if self._conn.execute(
                "SELECT 1 FROM tasks WHERE task_id=?", (new_id,)
            ).fetchone():
                return False
            self._conn.execute("PRAGMA foreign_keys=OFF")
            self._conn.execute(
                "UPDATE task_dependencies SET task_id=? WHERE task_id=?",
                (new_id, old_id),
            )
            self._conn.execute(
                "UPDATE task_dependencies SET depends_on=? WHERE depends_on=?",
                (new_id, old_id),
            )
            self._conn.execute(
                "UPDATE prs SET task_id=? WHERE task_id=?",
                (new_id, old_id),
            )
            self._conn.execute(
                "UPDATE tasks SET task_id=?, updated_at=? WHERE task_id=?",
                (new_id, _now_iso(), old_id),
            )
            self._conn.execute("PRAGMA foreign_keys=ON")
        return True

    # ------------------------------------------------------------------
    # Action definitions
    # ------------------------------------------------------------------

    def list_actions(self, context: str = "") -> list:
        """Return all actions, optionally filtered by context."""
        if context:
            cur = self._conn.execute(
                """
                SELECT * FROM action_definitions
                WHERE context = ? OR context = 'all'
                ORDER BY sort_order
                """,
                (context,),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM action_definitions ORDER BY sort_order"
            )
        return [_row_to_dict(row) for row in cur.fetchall()]

    def get_action(self, action_id: str):
        """Return a single action dict, or None if not found."""
        cur = self._conn.execute(
            "SELECT * FROM action_definitions WHERE id = ?",
            (action_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row is not None else None

    def seed_defaults(self):
        """Public alias for _seed_action_defaults (for backward compat)."""
        self._seed_action_defaults()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def create_session(self, task_id: str, project: str,
                       tmux_name: str | None = None) -> dict:
        """INSERT OR REPLACE a session row and return the created session dict.

        `tmux_name` defaults to `task_id`. In practice every production caller
        uses the same value for both, so the column is kept only for
        back-compat; prefer omitting `tmux_name`.
        """
        now = _now_iso()
        name = tmux_name if tmux_name is not None else task_id
        self._conn.execute(
            """
            INSERT OR REPLACE INTO sessions
                (task_id, project, tmux_name, status, created_at, updated_at)
            VALUES (?, ?, ?, 'not_started', ?, ?)
            """,
            (task_id, project, name, now, now),
        )
        self._conn.commit()
        return self.get_session(task_id)

    def get_session(self, task_id: str):
        """Return session dict or None if not found."""
        cur = self._conn.execute(
            "SELECT * FROM sessions WHERE task_id = ?",
            (task_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row is not None else None

    def update_session(self, task_id: str, **fields) -> dict:
        """Update only allowed fields and auto-stamp updated_at."""
        allowed = {"status", "tmux_name", "project", "agent_session_id"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_session(task_id)
        updates["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{col}=?" for col in updates)
        values = list(updates.values()) + [task_id]
        self._conn.execute(
            f"UPDATE sessions SET {set_clause} WHERE task_id=?",
            values,
        )
        self._conn.commit()
        return self.get_session(task_id)

    def delete_session(self, task_id: str) -> bool:
        """DELETE a session row. Return True if deleted, False otherwise."""
        cur = self._conn.execute(
            "DELETE FROM sessions WHERE task_id = ?",
            (task_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_sessions(self, project: str = "") -> list:
        """Return sessions ordered by updated_at DESC, optionally filtered by project."""
        if project:
            cur = self._conn.execute(
                "SELECT * FROM sessions WHERE project = ? ORDER BY updated_at DESC",
                (project,),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            )
        return [_row_to_dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Project session methods
    # ------------------------------------------------------------------

    def create_project_session(self, project_id: str, tmux_name: str) -> dict:
        """Insert (or replace) a project-level session row."""
        now = _now_iso()
        self._conn.execute(
            """INSERT OR REPLACE INTO project_sessions
               (project_id, tmux_name, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (project_id, tmux_name, "idle", now, now),
        )
        self._conn.commit()
        return self.get_project_session(project_id)

    def get_project_session(self, project_id: str):
        row = self._conn.execute_fetchone(
            "SELECT * FROM project_sessions WHERE project_id=?", (project_id,)
        )
        return _row_to_dict(row) if row else None

    def update_project_session(self, project_id: str, **fields) -> dict | None:
        allowed = {"status", "tmux_name"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_project_session(project_id)
        updates["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{c}=?" for c in updates)
        values = list(updates.values()) + [project_id]
        self._conn.execute(f"UPDATE project_sessions SET {set_clause} WHERE project_id=?", values)
        self._conn.commit()
        return self.get_project_session(project_id)

    def delete_project_session(self, project_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM project_sessions WHERE project_id=?", (project_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_project_sessions(self) -> list:
        cur = self._conn.execute(
            "SELECT * FROM project_sessions ORDER BY updated_at DESC"
        )
        return [_row_to_dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Work log methods
    # ------------------------------------------------------------------

    def get_work_log(self, log_date: str):
        """Get work log for a date (YYYY-MM-DD). Returns dict or None."""
        row = self._conn.execute_fetchone(
            "SELECT * FROM work_logs WHERE log_date=?", (log_date,))
        return _row_to_dict(row) if row else None

    def save_work_log(self, log_date: str, content: str, auto_generated: str = ""):
        """Save or update work log for a date."""
        now = _now_iso()
        self._conn.execute("""
            INSERT INTO work_logs (log_date, content, auto_generated, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(log_date) DO UPDATE SET content=?, updated_at=?
        """, (log_date, content, auto_generated, now, content, now))
        self._conn.commit()

    def list_work_logs(self, limit=30):
        """List recent work logs, newest first."""
        rows = self._conn.execute_fetchall(
            "SELECT * FROM work_logs ORDER BY log_date DESC LIMIT ?", (limit,)
        )
        return [_row_to_dict(r) for r in rows]

    def delete_work_log(self, log_date: str):
        """Delete the saved work log for a date (so next GET regenerates)."""
        self._conn.execute(
            "DELETE FROM work_logs WHERE log_date=?", (log_date,))
        self._conn.commit()

    # ---- Settings (key-value JSON store) ----

    def get_setting(self, key: str, default=None):
        """Read a setting value (JSON-decoded). Returns `default` when
        the key is absent or its stored value is invalid JSON."""
        row = self._conn.execute_fetchone(
            "SELECT value FROM settings WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (ValueError, TypeError):
            return default

    def set_setting(self, key: str, value) -> None:
        """Upsert a setting. `value` is JSON-encoded so any JSON-safe
        Python type (scalar, list, dict) round-trips.

        Forces a WAL checkpoint after commit so settings are durable
        even when the server gets SIGKILL'd by `bin/restart-server.sh`
        on slow graceful-shutdown. Without this, an UPSERT may sit in
        the WAL and get dropped on the next startup if the new server
        can't read the previous process's SHM cache. PASSIVE mode
        doesn't block on readers, so the cost is negligible."""
        self._conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (key, json.dumps(value), _now_iso()),
        )
        self._conn.commit()
        try:
            self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass

    def list_settings(self) -> dict:
        """Return all settings as `{key: value}` (JSON-decoded)."""
        result = {}
        # `execute_fetchall` keeps the lock held through fetch -- without
        # it, a concurrent write on another thread can reset the
        # connection's prepared-statement state mid-row-read and
        # surface `IndexError: tuple index out of range`.
        for row in self._conn.execute_fetchall(
            "SELECT key, value FROM settings ORDER BY key"
        ):
            try:
                result[row["key"]] = json.loads(row["value"])
            except (ValueError, TypeError):
                result[row["key"]] = None
        return result

    def delete_setting(self, key: str) -> bool:
        """Remove a setting. Returns True if a row was deleted."""
        cur = self._conn.execute("DELETE FROM settings WHERE key=?", (key,))
        self._conn.commit()
        return cur.rowcount > 0

    # ---- Cron jobs ----

    def create_cron_job(self, *, name: str, schedule: str, command: str,
                        description: str = "", enabled: bool = True) -> dict:
        """Insert a new cron job. Returns the inserted row as a dict."""
        now = _now_iso()
        cur = self._conn.execute(
            "INSERT INTO cron_jobs(name, schedule, command, description, "
            "enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, schedule, command, description, 1 if enabled else 0, now, now),
        )
        self._conn.commit()
        return self.get_cron_job(cur.lastrowid)

    def get_cron_job(self, job_id: int):
        row = self._conn.execute_fetchone(
            "SELECT * FROM cron_jobs WHERE id=?", (job_id,)
        )
        return _row_to_dict(row) if row else None

    def list_cron_jobs(self) -> list:
        """All jobs, newest-first. `id DESC` is the tiebreaker for
        rows inserted within the same wall-clock second."""
        return [
            _row_to_dict(r) for r in self._conn.execute_fetchall(
                "SELECT * FROM cron_jobs ORDER BY created_at DESC, id DESC"
            )
        ]

    def update_cron_job(self, job_id: int, **fields) -> dict | None:
        """Update one or more job columns. Whitelisted keys only -- a
        typo on the caller side returns the row unchanged rather than
        silently writing into the wrong column."""
        allowed = {"name", "schedule", "command", "description", "enabled",
                   "last_run_at", "last_status", "next_run_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_cron_job(job_id)
        if "enabled" in updates:
            updates["enabled"] = 1 if updates["enabled"] else 0
        updates["updated_at"] = _now_iso()
        sets = ", ".join(f"{k}=?" for k in updates)
        params = list(updates.values()) + [job_id]
        self._conn.execute(f"UPDATE cron_jobs SET {sets} WHERE id=?", params)
        self._conn.commit()
        return self.get_cron_job(job_id)

    def delete_cron_job(self, job_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM cron_jobs WHERE id=?", (job_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ---- Cron job runs (history) ----

    def create_cron_job_run(self, *, job_id: int, status: str = "running",
                            output_excerpt: str = "",
                            error_message: str = "") -> dict:
        """Record the start of a job run. Returns the inserted row."""
        cur = self._conn.execute(
            "INSERT INTO cron_job_runs(job_id, started_at, status, "
            "output_excerpt, error_message) VALUES (?, ?, ?, ?, ?)",
            (job_id, _now_iso(), status, output_excerpt, error_message),
        )
        self._conn.commit()
        row = self._conn.execute_fetchone(
            "SELECT * FROM cron_job_runs WHERE id=?", (cur.lastrowid,)
        )
        return _row_to_dict(row)

    def _get_run_and_bump_parent(
        self, run_id: int, *, terminal_status: str | None = None,
    ) -> dict | None:
        """Fetch a `cron_job_runs` row by id and reflect its timestamp
        onto the parent `cron_jobs` row. Shared tail used by
        `finish_cron_job_run` (terminal status flip) and
        `update_cron_job_run_output` (mid-run output bump that should
        leave `last_status` alone).

        `terminal_status=None` -> bump `last_run_at = started_at` only,
        so a job-list query reflects "tick fired" without overwriting
        the previous run's outcome. A string -> bump both
        `last_run_at` (= finished_at or started_at) and `last_status`.
        """
        row = self._conn.execute_fetchone(
            "SELECT * FROM cron_job_runs WHERE id=?", (run_id,),
        )
        if not row:
            return None
        if terminal_status is None:
            self._conn.execute(
                "UPDATE cron_jobs SET last_run_at=? WHERE id=?",
                (row["started_at"], row["job_id"]),
            )
        else:
            self._conn.execute(
                "UPDATE cron_jobs SET last_run_at=?, last_status=? "
                "WHERE id=?",
                (row["finished_at"] or row["started_at"],
                 terminal_status, row["job_id"]),
            )
        self._conn.commit()
        return _row_to_dict(row)

    def finish_cron_job_run(self, run_id: int, *, status: str,
                            output_excerpt: str = "",
                            error_message: str = "") -> dict | None:
        """Stamp a run as completed. Status MUST be a terminal value."""
        if status not in ("done", "failed", "cancelled"):
            raise ValueError(f"non-terminal finish status: {status!r}")
        self._conn.execute(
            "UPDATE cron_job_runs SET finished_at=?, status=?, "
            "output_excerpt=?, error_message=? WHERE id=?",
            (_now_iso(), status, output_excerpt, error_message, run_id),
        )
        self._conn.commit()
        return self._get_run_and_bump_parent(
            run_id, terminal_status=status,
        )

    def list_cron_job_runs(self, job_id: int, limit: int = 20) -> list:
        return [
            _row_to_dict(r) for r in self._conn.execute_fetchall(
                "SELECT * FROM cron_job_runs WHERE job_id=? "
                "ORDER BY started_at DESC, id DESC LIMIT ?",
                (job_id, limit),
            )
        ]

    def update_cron_job_run_output(self, run_id: int,
                                    output_excerpt: str) -> dict | None:
        """Update only the output_excerpt without stamping finished_at.

        Used for runs that successfully launched a session: the actual
        end-time of the run isn't known until the agent itself goes idle
        (Stop hook fires); meanwhile we still want the output excerpt
        to reflect what we know -- session name, command queued, etc.
        Also bumps the parent job row's last_run_at so the list view
        shows that the most recent tick fired.
        """
        self._conn.execute(
            "UPDATE cron_job_runs SET output_excerpt=? WHERE id=?",
            (output_excerpt, run_id),
        )
        self._conn.commit()
        # `terminal_status=None` -> bump last_run_at only, leave
        # last_status untouched (previous terminal status wins).
        return self._get_run_and_bump_parent(run_id, terminal_status=None)

    def latest_open_cron_job_run(self, job_id: int) -> dict | None:
        """Return the most recent run for `job_id` whose finished_at is
        empty -- i.e. still in flight. Used by the agent's Stop hook to
        find which run to stamp terminal when the cron-job-N session
        goes idle."""
        row = self._conn.execute_fetchone(
            "SELECT * FROM cron_job_runs WHERE job_id=? AND finished_at='' "
            "ORDER BY started_at DESC, id DESC LIMIT 1",
            (job_id,),
        )
        return _row_to_dict(row) if row else None

    def list_open_cron_job_runs(self, job_id: int) -> list[dict]:
        """Every in-flight run for a job, oldest-first. Used by the
        tick-start cleanup path to mark all stale-open runs terminal
        before the new tick records its own row -- without this, a
        crashed tick (server restart, hook miss) leaves a 'running'
        row that never closes."""
        return [
            _row_to_dict(r) for r in self._conn.execute_fetchall(
                "SELECT * FROM cron_job_runs WHERE job_id=? AND finished_at='' "
                "ORDER BY started_at ASC, id ASC",
                (job_id,),
            )
        ]

    # ---- Tickets (JIRA cache, now stored on `tasks`) ----

    # Ticket-shape column map: legacy `tickets` column -> the new
    # column on `tasks`. Used by both upsert and the row-shape helper
    # so the two stay aligned.
    _TICKET_COL_MAP = {
        "key": "ticket_id",
        "summary": "ticket_summary",
        "description": "description",
        "status": "ticket_status",
        "priority": "ticket_priority",
        "issue_type": "ticket_issue_type",
        "project_key": "ticket_project_key",
        "assignee_email": "ticket_assignee_email",
        "reporter_email": "ticket_reporter_email",
        "url": "ticket_url",
        "labels": "ticket_labels",
        "components": "ticket_components",
        "fix_versions": "ticket_fix_versions",
        "parent_key": "ticket_parent_key",
        "resolution": "ticket_resolution",
        "status_category": "ticket_status_category",
        "instance_name": "ticket_instance",
        "synced_at": "ticket_synced_at",
        "created_at": "created_at",
        "updated_at": "updated_at",
    }

    def _task_row_to_ticket_dict(self, row) -> dict:
        """Reshape a tasks row back into the legacy ticket dict the
        Tickets page / jira_sync code expects."""
        return {
            "instance_name": row["ticket_instance"] or "",
            "key": row["ticket_id"] or "",
            "summary": row["ticket_summary"] or "",
            "description": row["description"] or "",
            "status": row["ticket_status"] or "",
            "priority": row["ticket_priority"] or "",
            "issue_type": row["ticket_issue_type"] or "",
            "project_key": row["ticket_project_key"] or "",
            "assignee_email": row["ticket_assignee_email"] or "",
            "reporter_email": row["ticket_reporter_email"] or "",
            "url": row["ticket_url"] or "",
            "created_at": row["created_at"] or "",
            "updated_at": row["updated_at"] or "",
            "synced_at": row["ticket_synced_at"] or "",
            "labels": row["ticket_labels"] or "",
            "components": row["ticket_components"] or "",
            "fix_versions": row["ticket_fix_versions"] or "",
            "parent_key": row["ticket_parent_key"] or "",
            "resolution": row["ticket_resolution"] or "",
            "status_category": row["ticket_status_category"] or "",
        }

    def upsert_ticket(self, **fields) -> dict:
        """Insert or update a ticket-task row keyed by ticket_id (JIRA key).

        On first sight, classify the type by labels/issue_type via the
        same logic the migration uses. After that, type is sticky (user
        edits or smart-create overrides win)."""
        allowed = set(self._TICKET_COL_MAP.keys())
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unknown ticket columns: {sorted(bad)}")
        if "key" not in fields:
            raise ValueError("'key' is required")
        key = fields["key"]
        fields.setdefault("instance_name", "")
        if not fields.get("synced_at"):
            fields["synced_at"] = _now_iso()

        existing = self._conn.execute_fetchone(
            "SELECT task_id, type FROM tasks WHERE ticket_id=?",
            (key,),
        )
        # Translate legacy field keys to the new column names.
        updates = {self._TICKET_COL_MAP[k]: v for k, v in fields.items()}
        updates["ticket_id"] = key
        updates["updated_at"] = updates.get("updated_at") or _now_iso()

        if existing:
            task_id = existing["task_id"]
            set_clause = ", ".join(f"{c}=?" for c in updates)
            self._conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE task_id=?",
                list(updates.values()) + [task_id],
            )
        else:
            # Type classification (mirrors bin/migrate_unify_tasks.py).
            ttype = _classify_ticket_type(
                fields.get("summary", ""), fields.get("labels", "[]"),
                fields.get("issue_type", ""),
            )
            # Pick a globally-unique task_id; collide with `-jira` suffix.
            taken_row = self._conn.execute_fetchone(
                "SELECT 1 FROM tasks WHERE task_id=?", (key,),
            )
            candidate = f"{key}-jira" if taken_row else key
            extra = {
                "task_id": candidate, "project": "",
                "type": ttype, "status": "not_started",
                "priority": 5,
            }
            cols = list(updates) + list(extra)
            vals = list(updates.values()) + list(extra.values())
            placeholders = ", ".join("?" for _ in cols)
            self._conn.execute(
                f"INSERT INTO tasks ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )
        self._conn.commit()
        return self.get_ticket(key, instance_name=fields["instance_name"])

    def get_ticket(self, key: str, *, instance_name: str = ""):
        """Return the JIRA-cache view for `(instance_name, key)`. The
        common case (`instance_name=''`) means "any instance" --
        callers walking multiple instances pass the explicit name to
        scope the lookup."""
        if instance_name:
            row = self._conn.execute_fetchone(
                "SELECT * FROM tasks WHERE ticket_id=? AND ticket_instance=?",
                (key, instance_name),
            )
        else:
            row = self._conn.execute_fetchone(
                "SELECT * FROM tasks WHERE ticket_id=?",
                (key,),
            )
        return self._task_row_to_ticket_dict(row) if row else None

    def list_tickets(self, *, assignee_email: str = "",
                     instance_name: str | None = None,
                     limit: int = 100) -> list:
        """List JIRA-synced ticket-tasks newest-update first.

        Filters to tasks whose `ticket_synced_at` is non-empty so we
        only return the JIRA-side cache, not every task that happens
        to reference a ticket_id by hand."""
        clauses = ["ticket_id IS NOT NULL", "ticket_id != ''",
                   "ticket_synced_at != ''"]
        params: list = []
        if assignee_email:
            clauses.append("ticket_assignee_email = ?")
            params.append(assignee_email)
        if instance_name is not None:
            clauses.append("ticket_instance = ?")
            params.append(instance_name)
        where = "WHERE " + " AND ".join(clauses)
        params.append(limit)
        rows = self._conn.execute_fetchall(
            f"SELECT * FROM tasks {where} "
            f"ORDER BY updated_at DESC, ticket_id DESC LIMIT ?",
            tuple(params),
        )
        return [self._task_row_to_ticket_dict(r) for r in rows]

    def delete_ticket(self, key: str, *, instance_name: str = "") -> bool:
        """Clear the ticket fields on the matching task. We DON'T delete
        the task row -- it may carry user-authored work + history we
        want to keep. The ticket cache simply detaches."""
        cur = self._conn.execute(
            "UPDATE tasks SET "
            "  ticket_id=NULL, ticket_url='', ticket_summary='', "
            "  ticket_priority='', ticket_issue_type='', "
            "  ticket_project_key='', ticket_assignee_email='', "
            "  ticket_reporter_email='', ticket_status='', "
            "  ticket_status_category='', ticket_labels='[]', "
            "  ticket_components='[]', ticket_fix_versions='[]', "
            "  ticket_parent_key='', ticket_resolution='', "
            "  ticket_instance='', ticket_synced_at='', "
            "  updated_at=? "
            "WHERE ticket_id=?",
            (_now_iso(), key),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_tickets_synced_before(self, cutoff_iso: str,
                                     *, assignee_email: str = "",
                                     instance_name: str | None = None) -> int:
        """Sync helper: detach ticket cache from rows that JQL no longer
        returns. Mirrors `delete_ticket` -- the task itself stays."""
        clauses = ["ticket_id IS NOT NULL", "ticket_id != ''",
                   "ticket_synced_at < ?"]
        params: list = [cutoff_iso]
        if assignee_email:
            clauses.append("ticket_assignee_email = ?")
            params.append(assignee_email)
        if instance_name is not None:
            clauses.append("ticket_instance = ?")
            params.append(instance_name)
        cur = self._conn.execute(
            "UPDATE tasks SET "
            "  ticket_id=NULL, ticket_url='', ticket_summary='', "
            "  ticket_priority='', ticket_issue_type='', "
            "  ticket_project_key='', ticket_assignee_email='', "
            "  ticket_reporter_email='', ticket_status='', "
            "  ticket_status_category='', ticket_labels='[]', "
            "  ticket_components='[]', ticket_fix_versions='[]', "
            "  ticket_parent_key='', ticket_resolution='', "
            "  ticket_instance='', ticket_synced_at='', "
            "  updated_at=? "
            f"WHERE {' AND '.join(clauses)}",
            [_now_iso(), *params],
        )
        self._conn.commit()
        return cur.rowcount

    def close(self):
        """Close the database connection."""
        self._conn.close()
