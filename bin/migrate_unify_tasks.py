#!/usr/bin/env python3
"""One-shot Phase-1 migration: unify reviews + tickets into the tasks table.

Decisions (locked-in with user):
- tasks PK becomes a single `task_id` (was: (project, task_id))
- tasks.project becomes nullable (project = folder)
- tasks gets new columns:
    * ticket_* family (JIRA cache fields, was the `tickets` table)
    * review_* family (review workflow state, was the `review_prs` table)
- The JIRA description goes into `tasks.description`. We keep `tasks.notes`
  separate for the user's own annotations.
- `prs` table FK simplifies from (project, task_id) to (task_id).
- `task_dependencies` FK simplifies the same way.
- `tickets` and `review_prs` tables are dropped after their rows are
  copied into `tasks`.

Type classification for ticket rows:
- flaky-test  if labels/summary match the flaky regex
- slow-test   if labels/summary match the slow regex
- compliance  if labels match compliance/security
- bug         if issue_type == 'Bug'
- task        if issue_type == 'Task'
- feature     if issue_type == 'Story'
- epic        if issue_type == 'Epic'
- subtask     if issue_type contains 'sub-task'
- bug         default fallback

Collision handling:
- If a ticket maps to task_id == its JIRA key AND a task already has
  `ticket_id == key`, MERGE the ticket fields into that existing task
  (the user has been tracking the work there).
- If no existing match but the JIRA key collides with an unrelated task,
  rename the new ticket task_id to `<key>-jira` and report.

Reviews:
- Each `review_prs` row becomes a new task with type='review' and
  task_id = `review-<owner>-<repo>-<n>` (mirrors the existing session
  name). PR data goes into the `prs` table as the single attached PR.

Backwards-compat:
- After migration, old API endpoints (`/api/tickets`, `/api/review-requests`)
  keep working by querying the new schema. That's a follow-up commit in
  the routes/ files; this script doesn't touch them.

Run from repo root:
    ~/venv312/bin/python bin/migrate_unify_tasks.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "eva.db"


FLAKY_RE = re.compile(r"flaky|testman|test[- ]?failure|test failure|failing target", re.I)
SLOW_RE = re.compile(r"\bslow\b|slowness|slow[- ]?test", re.I)
COMPLIANCE_RE = re.compile(r"\bcompliance\b|\bsecurity\b|cve-", re.I)


def classify_ticket_type(summary: str, labels_json: str, issue_type: str) -> str:
    """Return a concrete task `type` for a migrated ticket row."""
    try:
        labels = json.loads(labels_json) if labels_json else []
    except (json.JSONDecodeError, ValueError):
        labels = []
    text_bag = " ".join([summary or "", *(labels or [])])
    if FLAKY_RE.search(text_bag):
        return "flaky-test"
    if SLOW_RE.search(text_bag):
        return "slow-test"
    if COMPLIANCE_RE.search(" ".join(labels or [])):
        return "compliance"
    it = (issue_type or "").lower().strip()
    if "sub-task" in it or "subtask" in it:
        return "subtask"
    return {
        "bug": "bug",
        "task": "task",
        "story": "feature",
        "epic": "epic",
        "improvement": "feature",
        "new feature": "feature",
    }.get(it, "bug")


def review_task_id(repo: str, number: int) -> str:
    """`review-<owner>-<repo>-<n>` -- mirrors current session naming."""
    safe_repo = re.sub(r"[^a-zA-Z0-9]+", "-", repo).strip("-").lower()
    return f"review-{safe_repo}-{number}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Read everything but skip writes.")
    p.add_argument("--db", default=str(DB_PATH),
                   help=f"Path to eva.db (default {DB_PATH})")
    return p.parse_args()


def open_conn(path: str) -> sqlite3.Connection:
    """Prefer pysqlite3 to match the server's runtime engine. Crucial:
    using stdlib here while the server uses pysqlite3 is what caused
    today's WAL corruption episode."""
    try:
        from pysqlite3 import dbapi2 as _sq
    except ImportError:
        _sq = sqlite3
    conn = _sq.connect(path)
    conn.row_factory = _sq.Row
    conn.execute("PRAGMA foreign_keys=OFF")  # we rebuild FKs ourselves
    return conn


def has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def add_new_columns(conn: sqlite3.Connection) -> None:
    """Phase 1.A: additive ALTERs so the migration can write the rows.

    Splitting the schema change into two passes (additive ALTERs here,
    PK + FK rebuild at the end) keeps the in-between state queryable
    -- helpful if we need to abort mid-migration."""
    new_cols = [
        # Type + project nullable handling (project NOT NULL still in
        # place; we rebuild the table later to drop NOT NULL).
        # JIRA cache fields (was: tickets table). description re-used.
        ("ticket_summary",          "TEXT DEFAULT ''"),
        ("ticket_priority",         "TEXT DEFAULT ''"),
        ("ticket_issue_type",       "TEXT DEFAULT ''"),
        ("ticket_project_key",      "TEXT DEFAULT ''"),
        ("ticket_assignee_email",   "TEXT DEFAULT ''"),
        ("ticket_reporter_email",   "TEXT DEFAULT ''"),
        ("ticket_status",           "TEXT DEFAULT ''"),
        ("ticket_status_category",  "TEXT DEFAULT ''"),
        ("ticket_labels",           "TEXT DEFAULT '[]'"),
        ("ticket_components",       "TEXT DEFAULT '[]'"),
        ("ticket_fix_versions",     "TEXT DEFAULT '[]'"),
        ("ticket_parent_key",       "TEXT DEFAULT ''"),
        ("ticket_resolution",       "TEXT DEFAULT ''"),
        ("ticket_instance",         "TEXT DEFAULT ''"),
        ("ticket_synced_at",        "TEXT DEFAULT ''"),
        # Review workflow state (was: review_prs row). The PR's GitHub
        # metadata (CI, title, comment counts) lives in the `prs` table.
        ("review_my_review_state",        "TEXT DEFAULT ''"),
        ("review_my_workflow_state",      "TEXT DEFAULT ''"),
        ("review_started_at",             "TEXT DEFAULT ''"),
        ("review_last_seen_comment_count","INTEGER DEFAULT 0"),
        ("review_added_at",               "TEXT DEFAULT ''"),
        ("review_source",                 "TEXT DEFAULT ''"),
        ("review_dirty",                  "INTEGER DEFAULT 0"),
    ]
    for col, typ in new_cols:
        if has_column(conn, "tasks", col):
            continue
        conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {typ}")


def existing_task_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT task_id FROM tasks")}


def upsert_ticket_into_tasks(
    conn: sqlite3.Connection, ticket: sqlite3.Row, taken: set[str],
) -> tuple[str, str]:
    """Map a tickets row -> tasks row.

    1. If any existing task has ticket_id == ticket.key, MERGE the
       JIRA cache fields into that task; bump type to the classified
       one only if the task was still on its default `feature`.
    2. Else if `key` is not already taken as a task_id, INSERT a new
       task with task_id = key.
    3. Else (rare collision), INSERT as `<key>-jira`.

    Returns (task_id, action) where action is one of {"merged",
    "inserted", "inserted_renamed"}.
    """
    key = ticket["key"]
    classified = classify_ticket_type(
        ticket["summary"] or "",
        ticket["labels"] or "[]",
        ticket["issue_type"] or "",
    )
    ticket_fields = {
        "ticket_id": key,
        "ticket_url": ticket["url"],
        "ticket_summary": ticket["summary"],
        "ticket_priority": ticket["priority"],
        "ticket_issue_type": ticket["issue_type"],
        "ticket_project_key": ticket["project_key"],
        "ticket_assignee_email": ticket["assignee_email"],
        "ticket_reporter_email": ticket["reporter_email"],
        "ticket_status": ticket["status"],
        "ticket_status_category": ticket["status_category"],
        "ticket_labels": ticket["labels"],
        "ticket_components": ticket["components"],
        "ticket_fix_versions": ticket["fix_versions"],
        "ticket_parent_key": ticket["parent_key"],
        "ticket_resolution": ticket["resolution"],
        "ticket_instance": ticket["instance_name"],
        "ticket_synced_at": ticket["synced_at"],
    }
    # 1. Try merge into an existing task whose ticket_id already points here.
    row = conn.execute(
        "SELECT project, task_id, type, description, notes FROM tasks "
        "WHERE ticket_id=? LIMIT 1",
        (key,),
    ).fetchone()
    if row is not None:
        # Preserve user's description if it's already non-trivial;
        # otherwise pick up the JIRA description as a baseline.
        existing_desc = (row["description"] or "").strip()
        new_desc = existing_desc if existing_desc else (ticket["description"] or "")
        # Upgrade type only if user hasn't set anything meaningful yet.
        cur_type = row["type"] or "feature"
        new_type = classified if cur_type in ("feature", "") else cur_type
        sets = ", ".join(f"{k}=?" for k in ticket_fields)
        params = list(ticket_fields.values()) + [new_desc, new_type, row["project"], row["task_id"]]
        conn.execute(
            f"UPDATE tasks SET {sets}, description=?, type=? "
            "WHERE project=? AND task_id=?",
            params,
        )
        return (row["task_id"], "merged")
    # 2/3. INSERT new task.
    candidate = key
    action = "inserted"
    if candidate in taken:
        candidate = f"{key}-jira"
        action = "inserted_renamed"
        if candidate in taken:
            # Shouldn't happen, but loop to ensure uniqueness.
            i = 2
            while f"{key}-jira-{i}" in taken:
                i += 1
            candidate = f"{key}-jira-{i}"
    taken.add(candidate)
    cols = list(ticket_fields) + [
        "project", "task_id", "type", "description", "status",
        "priority", "created_at", "updated_at",
    ]
    vals = list(ticket_fields.values()) + [
        "",  # project: '' = unsorted (NOT NULL still in force here)
        candidate, classified, ticket["description"] or "",
        "not_started", 5,
        ticket["created_at"] or "", ticket["updated_at"] or "",
    ]
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO tasks ({', '.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    return (candidate, action)


def upsert_review_into_tasks(
    conn: sqlite3.Connection, rv: sqlite3.Row, taken: set[str],
) -> tuple[str, str]:
    """Map a review_prs row -> tasks row + a single PR row.

    Always inserts (reviews represent a different role than authored
    PRs; even if you happen to have one of each on the same URL, the
    workflow state is separate so they stay as separate tasks)."""
    candidate = review_task_id(rv["repo"], rv["number"])
    if candidate in taken:
        # Shouldn't happen since review-* prefix is unique, but be safe.
        i = 2
        while f"{candidate}-{i}" in taken:
            i += 1
        candidate = f"{candidate}-{i}"
    taken.add(candidate)

    review_fields = {
        # `tasks.project` is still NOT NULL at this phase -- it gets
        # widened to nullable in the rebuild step below. Use '' as the
        # sentinel meaning "unsorted / no project folder". Queries that
        # group by project should treat '' and NULL identically.
        "project": "",
        "task_id": candidate,
        "type": "review",
        "description": rv["title"] or "",
        "status": "not_started",
        "priority": 5,
        "review_my_review_state": rv["my_review_state"] or "",
        "review_my_workflow_state": rv["my_workflow_state"] or "",
        "review_started_at": rv["started_at"] or "",
        "review_last_seen_comment_count": rv["last_seen_comment_count"] or 0,
        "review_added_at": rv["added_at"] or "",
        "review_source": rv["source"] or "manual",
        "review_dirty": rv["dirty"] or 0,
        "created_at": rv["added_at"] or "",
        "updated_at": rv["synced_at"] or rv["added_at"] or "",
    }
    cols = list(review_fields)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO tasks ({', '.join(cols)}) VALUES ({placeholders})",
        list(review_fields.values()),
    )

    # Attach the PR as a single row in `prs`. We use the existing schema
    # which still has the (project, task_id) FK; that FK gets simplified
    # in the rebuild step below.
    conn.execute(
        "INSERT OR IGNORE INTO prs "
        "(project, task_id, number, url, status, title, ci_status, "
        " review_status, comment_count, additions, deletions, author, "
        " head_branch, base_branch, last_updated, status_changed_at, "
        " last_seen_comment_count) "
        "VALUES ('__reviews__', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            candidate,
            rv["number"], rv["url"],
            rv["status"] or "open",
            rv["title"] or "",
            rv["ci_status"] or "",
            rv["review_status"] or "",
            rv["comment_count"] or 0,
            rv["additions"] or 0,
            rv["deletions"] or 0,
            rv["author"] or "",
            rv["head_branch"] or "",
            rv["base_branch"] or "",
            rv["last_updated"] or "",
            "",  # status_changed_at
            rv["last_seen_comment_count"] or 0,
        ),
    )
    # Mirror agent_session_id + session_name into the sessions table so
    # the existing tmux session keeps working after the new task_id is
    # in place. tmux_name keeps the legacy `review-<owner>-<repo>-<n>`
    # which equals the new task_id anyway -- no rename needed.
    if rv["session_name"]:
        conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(task_id, project, tmux_name, status, agent_session_id, "
            " created_at, updated_at) "
            "VALUES (?, '__reviews__', ?, 'idle', ?, ?, ?)",
            (
                candidate,
                rv["session_name"],
                rv["agent_session_id"] or "",
                rv["started_at"] or rv["added_at"] or "",
                rv["synced_at"] or rv["added_at"] or "",
            ),
        )
    return (candidate, "inserted")


def rebuild_pk_and_fks(conn: sqlite3.Connection) -> None:
    """Phase 1.B: rebuild tasks/prs/task_dependencies/sessions so:
      * tasks PK becomes (task_id) only
      * tasks.project becomes NULL-able
      * prs FK becomes (task_id)
      * task_dependencies FK + PK become (task_id, depends_on)
      * sessions stays as-is (task_id was already its PK)

    SQLite can't ALTER a PK or drop a NOT NULL constraint in place; the
    canonical pattern is rename-table + create-new + copy + drop-old.
    Run inside a transaction so an abort rolls back cleanly.
    """
    conn.executescript("""
        ALTER TABLE tasks RENAME TO __tasks_old;
        ALTER TABLE prs RENAME TO __prs_old;
        ALTER TABLE task_dependencies RENAME TO __td_old;
    """)

    conn.executescript("""
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            project TEXT,
            type TEXT DEFAULT 'feature',
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'not_started'
                CHECK(status IN ('not_started','in_progress','in_review',
                                 'done','needs_follow_up','closed')),
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

        INSERT INTO tasks (
            task_id, project, type, description, status, group_name, notes,
            priority, follow_ups, ticket_id, ticket_url,
            ticket_summary, ticket_priority, ticket_issue_type,
            ticket_project_key, ticket_assignee_email, ticket_reporter_email,
            ticket_status, ticket_status_category, ticket_labels,
            ticket_components, ticket_fix_versions, ticket_parent_key,
            ticket_resolution, ticket_instance, ticket_synced_at,
            review_my_review_state, review_my_workflow_state,
            review_started_at, review_last_seen_comment_count,
            review_added_at, review_source, review_dirty,
            created_at, updated_at
        )
        SELECT
            task_id, project, type, description, status, group_name, notes,
            priority, follow_ups, ticket_id, ticket_url,
            ticket_summary, ticket_priority, ticket_issue_type,
            ticket_project_key, ticket_assignee_email, ticket_reporter_email,
            ticket_status, ticket_status_category, ticket_labels,
            ticket_components, ticket_fix_versions, ticket_parent_key,
            ticket_resolution, ticket_instance, ticket_synced_at,
            review_my_review_state, review_my_workflow_state,
            review_started_at, review_last_seen_comment_count,
            review_added_at, review_source, review_dirty,
            created_at, updated_at
        FROM __tasks_old;

        CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project);
        CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(type);
        CREATE INDEX IF NOT EXISTS idx_tasks_ticket ON tasks(ticket_id)
            WHERE ticket_id IS NOT NULL AND ticket_id != '';

        CREATE TABLE prs (
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
            status_changed_at TEXT DEFAULT '',
            dirty INTEGER DEFAULT 0,
            UNIQUE(task_id, number),
            FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
        );

        INSERT INTO prs (
            task_id, number, url, status, title, session, working_dir,
            ci_status, review_status, comment_count, last_seen_comment_count,
            additions, deletions, author, head_branch, base_branch,
            last_updated, status_changed_at, dirty
        )
        SELECT
            task_id, number, url, status, title, session, working_dir,
            ci_status, review_status, comment_count, last_seen_comment_count,
            additions, deletions, author, head_branch, base_branch,
            last_updated, status_changed_at, dirty
        FROM __prs_old;

        CREATE INDEX IF NOT EXISTS idx_prs_task ON prs(task_id);

        CREATE TABLE task_dependencies (
            task_id TEXT NOT NULL,
            depends_on TEXT NOT NULL,
            PRIMARY KEY (task_id, depends_on),
            FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
        );

        INSERT INTO task_dependencies (task_id, depends_on)
        SELECT DISTINCT task_id, depends_on FROM __td_old;
    """)

    conn.executescript("""
        DROP TABLE __tasks_old;
        DROP TABLE __prs_old;
        DROP TABLE __td_old;
    """)


def archive_legacy_tables(conn: sqlite3.Connection) -> None:
    """Rename the now-redundant tables instead of dropping them.

    Old code referring to `tickets` / `review_prs` will error fast --
    that's intentional, it surfaces every call-site that still needs
    the Phase 2 migration. Once routes/services are switched to read
    from `tasks`, a follow-up `DROP TABLE __*_legacy` is safe."""
    for name in ("tickets", "review_prs", "review_history"):
        try:
            conn.execute(f"ALTER TABLE {name} RENAME TO __{name}_legacy")
        except sqlite3.OperationalError:
            # Already renamed (idempotent re-run) or never existed.
            pass


def main() -> int:
    args = parse_args()
    print(f"db: {args.db}")
    if not Path(args.db).exists():
        print("  ERROR: db not found", file=sys.stderr)
        return 2
    conn = open_conn(args.db)
    try:
        with conn:
            print("--- phase 1.A: add new columns to tasks ---")
            add_new_columns(conn)
            taken = existing_task_ids(conn)
            print(f"  existing tasks before migration: {len(taken)}")

            print("--- phase 1.B: migrate review_prs rows ---")
            try:
                reviews = conn.execute("SELECT * FROM review_prs").fetchall()
            except sqlite3.Error:
                reviews = []
            r_in = r_skip = 0
            for rv in reviews:
                tid, action = upsert_review_into_tasks(conn, rv, taken)
                if action.startswith("inserted"):
                    r_in += 1
                else:
                    r_skip += 1
            print(f"  reviews seen: {len(reviews)} -> "
                  f"inserted: {r_in}, skipped: {r_skip}")

            print("--- phase 1.C: migrate tickets rows ---")
            try:
                tickets = conn.execute("SELECT * FROM tickets").fetchall()
            except sqlite3.Error:
                tickets = []
            t_in = t_merge = t_rename = 0
            for tk in tickets:
                tid, action = upsert_ticket_into_tasks(conn, tk, taken)
                if action == "merged":
                    t_merge += 1
                elif action == "inserted_renamed":
                    t_rename += 1
                    print(f"  collision: {tk['key']} -> renamed to {tid}")
                else:
                    t_in += 1
            print(f"  tickets seen: {len(tickets)} -> "
                  f"merged: {t_merge}, inserted: {t_in}, "
                  f"renamed: {t_rename}")

            print("--- phase 1.D: rebuild PK + FKs ---")
            if not args.dry_run:
                rebuild_pk_and_fks(conn)

            print("--- phase 1.E: archive legacy tables ---")
            if not args.dry_run:
                archive_legacy_tables(conn)

            if args.dry_run:
                print("dry run -- rolling back")
                raise RuntimeError("__dry_run__")

        # Final tally outside the with-block (committed).
        n = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        by_type = conn.execute(
            "SELECT type, COUNT(*) FROM tasks GROUP BY type ORDER BY 2 DESC"
        ).fetchall()
        print("\n--- final tasks count by type ---")
        print(f"  total: {n}")
        for r in by_type:
            print(f"  {r[0]}: {r[1]}")
        print("\nintegrity_check:",
              conn.execute("PRAGMA integrity_check").fetchone()[0])
    except RuntimeError as e:
        if "__dry_run__" in str(e):
            print("(dry run completed; no changes persisted)")
            return 0
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
