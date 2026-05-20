"""Data-integrity audit: scan the eva DB for stale / inconsistent rows.

The user runs this via `eva-cli audit` (and we surface it in the
Settings UI later if useful). Each `check_*` function is pure data --
it queries the DB and returns a list of `Finding`s without mutating
anything. `fix_finding` performs the corresponding repair when a
finding marks itself `fixable=True`.

Scope of checks (kept narrow on purpose -- false positives erode
trust):

  * Task<->PR drift: `merged` PRs whose task hasn't moved past
    `not_started`, or all-merged tasks left non-terminal.
  * Ticket fields: `ticket_id` and `ticket_url` should both be set or
    both be empty (one without the other is a UI hazard).
  * Orphans: rows in `prs`, `task_history`, `task_dependencies`
    pointing at a project/task that no longer exists.
  * PR data quality: duplicate URLs across tasks, missing author.

Each finding has a stable `kind` so callers can filter / dedupe.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import app_state


SEVERITY_INFO = "info"
SEVERITY_WARN = "warn"
SEVERITY_ERROR = "error"


@dataclass
class Finding:
    kind: str          # stable string id, e.g. "orphan_pr"
    severity: str      # info | warn | error
    message: str       # human-readable one-liner
    ref: dict          # row identifiers (project, task_id, url, etc.)
    fixable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ---- Individual checks ----

# Statuses considered "still active" for the purpose of "PR merged but
# task didn't move" detection. `closed` is excluded -- a closed task
# with a merged PR is fine (user explicitly abandoned the task).
_NON_TERMINAL_TASK_STATUSES = ("not_started", "in_progress", "in_review",
                               "needs_follow_up")


def check_pr_task_drift() -> list[Finding]:
    """PRs marked merged but the parent task is still `not_started`.

    Strong signal of stale state -- once a PR is merged, the task
    should at minimum be `in_review` or `done`.
    """
    findings: list[Finding] = []
    rows = app_state._db._conn.execute(
        "SELECT t.project as project, p.task_id, p.url, "
        "       p.status as pr_status, t.status as task_status "
        "FROM prs p JOIN tasks t ON t.task_id = p.task_id "
        "WHERE p.status = 'merged' AND t.status = 'not_started'"
    ).fetchall()
    for r in rows:
        findings.append(Finding(
            kind="pr_merged_task_not_started",
            severity=SEVERITY_WARN,
            message=(
                f"task '{r['task_id']}' in project '{r['project']}' is "
                f"still not_started even though PR {r['url']} is merged"
            ),
            ref={
                "project": r["project"], "task_id": r["task_id"],
                "url": r["url"],
            },
            fixable=False,
        ))
    return findings


def check_all_prs_merged_but_task_open() -> list[Finding]:
    """Task has >=1 PR, ALL are merged, but task is not done/closed.

    Common after merging the last PR in a feature -- the task should
    auto-close. We surface this rather than auto-close so the user can
    review (some workflows want a follow-up task before closing).
    """
    findings: list[Finding] = []
    rows = app_state._db._conn.execute(
        "SELECT t.project as project, p.task_id, "
        "       SUM(CASE WHEN p.status = 'merged' THEN 1 ELSE 0 END) as merged_n, "
        "       COUNT(*) as total_n, "
        "       t.status as task_status "
        "FROM prs p JOIN tasks t ON t.task_id = p.task_id "
        "WHERE t.status NOT IN ('done', 'closed') "
        "GROUP BY p.task_id "
        "HAVING merged_n = total_n AND total_n > 0"
    ).fetchall()
    for r in rows:
        findings.append(Finding(
            kind="all_prs_merged_task_open",
            severity=SEVERITY_INFO,
            message=(
                f"task '{r['task_id']}' in '{r['project']}' has "
                f"{r['total_n']} PR(s), all merged, but status is "
                f"'{r['task_status']}' -- candidate for close"
            ),
            ref={"project": r["project"], "task_id": r["task_id"]},
            fixable=False,
        ))
    return findings


def check_ticket_fields_paired() -> list[Finding]:
    """`ticket_id` and `ticket_url` should both be set or both empty."""
    findings: list[Finding] = []
    rows = app_state._db._conn.execute(
        "SELECT project, task_id, ticket_id, ticket_url FROM tasks "
        "WHERE (ticket_id IS NOT NULL AND ticket_id != '' "
        "       AND (ticket_url IS NULL OR ticket_url = '')) "
        "   OR (ticket_url IS NOT NULL AND ticket_url != '' "
        "       AND (ticket_id IS NULL OR ticket_id = ''))"
    ).fetchall()
    for r in rows:
        has_id = bool(r["ticket_id"])
        side = "ticket_url" if has_id else "ticket_id"
        findings.append(Finding(
            kind="ticket_fields_unpaired",
            severity=SEVERITY_WARN,
            message=(
                f"task '{r['task_id']}' in '{r['project']}' has "
                f"{'ticket_id' if has_id else 'ticket_url'} set but "
                f"missing {side}"
            ),
            ref={"project": r["project"], "task_id": r["task_id"]},
            fixable=False,
        ))
    return findings


def check_orphan_prs() -> list[Finding]:
    """PRs whose task no longer exists."""
    findings: list[Finding] = []
    rows = app_state._db._conn.execute(
        "SELECT p.task_id, p.url FROM prs p "
        "LEFT JOIN tasks t ON t.task_id = p.task_id "
        "WHERE t.task_id IS NULL"
    ).fetchall()
    for r in rows:
        findings.append(Finding(
            kind="orphan_pr",
            severity=SEVERITY_ERROR,
            message=(
                f"PR {r['url']} references "
                f"task '{r['task_id']}' which doesn't exist"
            ),
            ref={"task_id": r["task_id"], "url": r["url"]},
            fixable=True,
        ))
    return findings


def check_orphan_history() -> list[Finding]:
    """task_history rows pointing at a non-existent task."""
    findings: list[Finding] = []
    rows = app_state._db._conn.execute(
        "SELECT h.task_id, COUNT(*) as n "
        "FROM task_history h "
        "LEFT JOIN tasks t ON t.task_id = h.task_id "
        "WHERE t.task_id IS NULL "
        "GROUP BY h.task_id"
    ).fetchall()
    for r in rows:
        findings.append(Finding(
            kind="orphan_history",
            severity=SEVERITY_ERROR,
            message=(
                f"{r['n']} task_history entries reference "
                f"non-existent task '{r['task_id']}'"
            ),
            ref={"task_id": r["task_id"], "count": r["n"]},
            fixable=True,
        ))
    return findings


def check_orphan_dependencies() -> list[Finding]:
    """task_dependencies rows where either endpoint task is gone."""
    findings: list[Finding] = []
    rows = app_state._db._conn.execute(
        "SELECT d.task_id, d.depends_on "
        "FROM task_dependencies d "
        "LEFT JOIN tasks t1 ON t1.task_id = d.task_id "
        "LEFT JOIN tasks t2 ON t2.task_id = d.depends_on "
        "WHERE t1.task_id IS NULL OR t2.task_id IS NULL"
    ).fetchall()
    for r in rows:
        findings.append(Finding(
            kind="orphan_dependency",
            severity=SEVERITY_ERROR,
            message=(
                f"dependency '{r['task_id']}' -> '{r['depends_on']}' "
                f"references a missing task"
            ),
            ref={
                "task_id": r["task_id"],
                "depends_on": r["depends_on"],
            },
            fixable=True,
        ))
    return findings


def check_duplicate_pr_urls() -> list[Finding]:
    """Same PR URL attached to multiple tasks. Usually a sync bug."""
    findings: list[Finding] = []
    rows = app_state._db._conn.execute(
        "SELECT p.url, "
        "       GROUP_CONCAT(COALESCE(t.project,'')||'/'||p.task_id, ', ') AS tasks, "
        "       COUNT(*) as n "
        "FROM prs p "
        "  LEFT JOIN tasks t ON t.task_id = p.task_id "
        "WHERE p.url IS NOT NULL AND p.url != '' "
        "GROUP BY p.url HAVING n > 1"
    ).fetchall()
    for r in rows:
        findings.append(Finding(
            kind="duplicate_pr_url",
            severity=SEVERITY_WARN,
            message=(
                f"PR {r['url']} is attached to {r['n']} tasks: {r['tasks']}"
            ),
            ref={"url": r["url"], "tasks": r["tasks"], "count": r["n"]},
            fixable=False,
        ))
    return findings


def check_prs_missing_author() -> list[Finding]:
    """PR rows with no author -- likely pre-author-column legacy
    entries that never got backfilled. Affects PR-stats author
    filtering.

    Emits one finding per affected row (with `(project, task_id,
    number, url)` in `ref`) so the user can drill down via
    `eva-cli audit --kind pr_missing_author` and see which PRs need
    manual cleanup. Aggregate counts hid the structure.
    """
    rows = app_state._db._conn.execute(
        "SELECT t.project AS project, p.task_id, p.number, p.url "
        "FROM prs p LEFT JOIN tasks t ON t.task_id = p.task_id "
        "WHERE p.author IS NULL OR p.author = '' "
        "ORDER BY t.project, p.task_id, p.number"
    ).fetchall()
    if not rows:
        return []
    return [
        Finding(
            kind="pr_missing_author",
            severity=SEVERITY_INFO,
            message=(
                f"PR {r['url'] or f'#{r['number']}'} in "
                f"{r['project']}/{r['task_id']} has an empty `author` "
                f"column -- run `eva-cli audit --fix` to backfill via "
                f"`gh pr view`. Workstats currently filters this row "
                f"out of your merged-total."
            ),
            ref={
                "project": r["project"],
                "task_id": r["task_id"],
                "number": r["number"],
                "url": r["url"],
            },
            fixable=True,
        )
        for r in rows
    ]


def check_duplicate_pr_rows() -> list[Finding]:
    """Same `(project, task_id, number)` triple stored more than once.

    `prs` lacks a declared PRIMARY KEY (see DDL audit item E in
    loop-notes), so SQLite happily lets a second `add_pr` insert a
    duplicate row. The de-facto contract is "one row per
    (project, task_id, number)" -- enforced today only by callers
    using `update_pr_by_number` instead of `add_pr` for re-syncs.
    Any drift here is a bug we'd otherwise miss.

    Distinct from `check_duplicate_pr_urls`, which finds the same URL
    attached to *different* tasks (intentional umbrella PR pattern).
    """
    rows = app_state._db._conn.execute(
        "SELECT task_id, number, COUNT(*) AS n FROM prs "
        "GROUP BY task_id, number "
        "HAVING n > 1"
    ).fetchall()
    findings: list[Finding] = []
    for r in rows:
        findings.append(Finding(
            kind="duplicate_pr_row",
            severity=SEVERITY_WARN,
            message=(
                f"PR #{r['number']} on task {r['task_id']} "
                f"appears {r['n']} times in the prs table -- duplicate "
                f"rows accumulate when a write path uses INSERT instead "
                f"of UPSERT; loop-notes item E proposes adding a PK"
            ),
            ref={"task_id": r["task_id"],
                 "number": r["number"], "count": r["n"]},
            fixable=False,
        ))
    return findings


def check_stale_cron_runs() -> list[Finding]:
    """Cron-job runs left in 'running' with finished_at='' whose tmux
    session is gone.

    Normally `supersede_open_runs` drains these at tick start, and the
    the agent's Stop hook stamps them when the session goes idle. This check
    catches cases that slip through: a job that was deleted (so no
    next tick will fire), or a tmux session that died without the
    Stop hook firing (server crash, kill -9, etc.). The fix-up flips
    them to `cancelled` with a `stale: session gone` note so the run
    history reflects reality.
    """
    findings: list[Finding] = []
    open_runs = app_state._db._conn.execute(
        "SELECT cjr.id, cjr.job_id, cjr.started_at, cj.name AS job_name "
        "FROM cron_job_runs cjr "
        "LEFT JOIN cron_jobs cj ON cj.id = cjr.job_id "
        "WHERE cjr.status = 'running' AND cjr.finished_at = ''"
    ).fetchall()
    if not open_runs:
        return findings
    from adapters import tmux as _tmux
    from .cron_jobs import session_name_for_job
    for r in open_runs:
        session = session_name_for_job(int(r["job_id"]), r["job_name"] or "")
        if _tmux.session_exists(session):
            continue
        findings.append(Finding(
            kind="stale_cron_run",
            severity=SEVERITY_WARN,
            message=(
                f"cron run #{r['id']} (job={r['job_id']}, started "
                f"{r['started_at']}) is still 'running' but its tmux "
                f"session `{session}` no longer exists -- the agent's Stop hook "
                f"likely missed; auto-fix flips it to `cancelled`"
            ),
            ref={"run_id": r["id"], "job_id": r["job_id"],
                 "session": session},
            fixable=True,
        ))
    return findings


# Task / review session statuses that mean "the agent is currently doing
# something". A row stuck at one of these is suspicious if the tmux
# session is gone -- the Stop hook would normally have flipped it.
_LIVE_SESSION_STATES = ("working", "thinking", "idle", "active",
                        "needs_permission")


def check_stale_task_sessions() -> list[Finding]:
    """Task sessions whose status looks live but tmux session is gone.

    Normally the the agent's Stop hook flips the row to `stopped` when the
    session ends. When tmux dies without firing the hook (kill -9,
    server crash mid-stream, hook script timeout), the row stays
    `working` / `idle` and the All-Live-Tasks page shows ghost cards.
    """
    findings: list[Finding] = []
    placeholders = ",".join("?" * len(_LIVE_SESSION_STATES))
    rows = app_state._db._conn.execute(
        f"SELECT task_id, project, tmux_name, status, updated_at "
        f"FROM sessions WHERE status IN ({placeholders})",
        _LIVE_SESSION_STATES,
    ).fetchall()
    if not rows:
        return findings
    from adapters import tmux as _tmux
    for r in rows:
        name = r["tmux_name"] or r["task_id"]
        if _tmux.session_exists(name):
            continue
        findings.append(Finding(
            kind="stale_task_session",
            severity=SEVERITY_WARN,
            message=(
                f"task session `{name}` (project={r['project']}, "
                f"task={r['task_id']}, status={r['status']}, updated "
                f"{r['updated_at']}) is non-terminal but its tmux "
                f"session no longer exists -- auto-fix flips it to "
                f"`stopped`"
            ),
            ref={"task_id": r["task_id"], "tmux_name": name,
                 "project": r["project"]},
            fixable=True,
        ))
    return findings


def check_long_idle_input_sessions() -> list[Finding]:
    """Task sessions stuck in `needs_input` / `starting` for >24h.

    Distinct from `stale_task_session`: those have a DEAD tmux pane.
    These have a LIVE pane blocked on user input that nobody answered.
    Symptom: rows accumulate in the All-Live-Tasks queue showing
    ghost-style stuck cards. Live audit on the maintainer install
    found 18 such rows, the oldest 4+ days old.

    Threshold: 24h. Anything sooner risks false positives during
    legit pauses (the user steps away for lunch). Anything longer
    misses real garbage. 24h is a clean "definitely not actively
    being worked" boundary.
    """
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    findings: list[Finding] = []
    rows = app_state._db._conn.execute(
        "SELECT task_id, project, tmux_name, status, updated_at "
        "FROM sessions "
        "WHERE status IN ('needs_input', 'starting')"
    ).fetchall()
    for r in rows:
        ts = r["updated_at"] or ""
        try:
            updated = datetime.fromisoformat(
                ts.replace("Z", "+00:00")
            )
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if updated > cutoff:
            continue
        age_hours = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
        findings.append(Finding(
            kind="long_idle_input_session",
            severity=SEVERITY_INFO,
            message=(
                f"session {r['tmux_name'] or r['task_id']!r} "
                f"({r['project']}/{r['task_id']}) has been in "
                f"`{r['status']}` for {age_hours:.1f}h. The user "
                f"likely walked away from a permission prompt. "
                f"Auto-fix flips status to `stopped` so the "
                f"All-Live-Tasks queue stops showing the ghost card."
            ),
            ref={
                "task_id": r["task_id"], "project": r["project"],
                "tmux_name": r["tmux_name"] or r["task_id"],
                "status": r["status"],
                "age_hours": round(age_hours, 1),
            },
            fixable=True,
        ))
    return findings


def check_stale_review_sessions() -> list[Finding]:
    """Review tasks whose `review_my_workflow_state='active'` but the
    tmux session is gone. Mirrors `check_stale_task_sessions` for the
    review-type task family."""
    findings: list[Finding] = []
    rows = app_state._db._conn.execute(
        "SELECT t.task_id, p.url, p.number, "
        "       s.tmux_name AS session_name, t.review_started_at "
        "FROM tasks t "
        "  LEFT JOIN prs p ON p.task_id = t.task_id "
        "  LEFT JOIN sessions s ON s.task_id = t.task_id "
        "WHERE t.type='review' "
        "  AND t.review_my_workflow_state='active' "
        "  AND COALESCE(s.tmux_name, '') != ''"
    ).fetchall()
    if not rows:
        return findings
    from adapters import tmux as _tmux
    for r in rows:
        name = r["session_name"]
        if _tmux.session_exists(name):
            continue
        findings.append(Finding(
            kind="stale_review_session",
            severity=SEVERITY_WARN,
            message=(
                f"review session `{name}` (PR #{r['number']}, started "
                f"{r['review_started_at'] or '?'}) is `active` but its "
                f"tmux session no longer exists -- auto-fix flips it "
                f"back to `queued`"
            ),
            ref={"url": r["url"] or "", "session_name": name},
            fixable=True,
        ))
    return findings


# Single source of truth for task-type aliases lives in `tasks`
# (where create/update enforce normalisation at the write boundary).
# The audit only catches rows that landed before the boundary check
# was added, or those written via direct DB calls bypassing the core
# layer (e.g. legacy migrations).
from .tasks import TASK_TYPE_ALIASES as _TASK_TYPE_ALIASES


def check_terminal_status_missing_transition() -> list[Finding]:
    """Detect tasks where current status is terminal (`done`/`closed`)
    but `task_history` has no matching `-> <status>` transition row.

    Filtered to tasks that DO have at least one history row, so a
    freshly imported task with empty history isn't flagged. The check
    surfaces:
      * Direct-SQL updates that bypassed the proper update path.
      * Migration imports that moved the task to a terminal status
        without bringing the closure transition entry along.
      * Integrations that promoted status without writing to history.

    Info severity -- the data isn't broken, just missing audit trail.
    Not auto-fixable because synthesizing a transition row would use
    a fabricated timestamp; we leave the repair to the user (who can
    add the transition manually with a known timestamp, or just
    accept the gap).
    """
    findings: list[Finding] = []
    rows = app_state._db._conn.execute(
        """
        SELECT t.project, t.task_id, t.status
        FROM tasks t
        WHERE t.status IN ('done', 'closed')
          AND EXISTS (
            SELECT 1 FROM task_history h1
            WHERE h1.project=t.project AND h1.task_id=t.task_id
          )
          AND NOT EXISTS (
            SELECT 1 FROM task_history h2
            WHERE h2.project=t.project AND h2.task_id=t.task_id
              AND h2.text LIKE '%-> ' || t.status || '%'
          )
        """,
    ).fetchall()
    for project, task_id, status in rows:
        findings.append(Finding(
            kind="terminal_status_missing_transition",
            severity=SEVERITY_INFO,
            message=(
                f"task '{project}/{task_id}' is `{status}` but "
                f"task_history has no `-> {status}` transition row "
                f"(direct-DB / migration bypass)."
            ),
            ref={"project": project, "task_id": task_id, "status": status},
            fixable=False,
        ))
    return findings


def check_task_type_canonicalization() -> list[Finding]:
    """Surface tasks whose `type` is a known alias of a canonical value.

    Fixable: rewrites `tasks.type` to the canonical form. Safe because
    no code path branches on the alias spelling -- the value is only
    rendered as a badge label.
    """
    findings: list[Finding] = []
    for project, task_id, ttype in app_state._db._conn.execute(
        "SELECT project, task_id, type FROM tasks WHERE type IN ("
        + ",".join("?" * len(_TASK_TYPE_ALIASES)) + ")",
        list(_TASK_TYPE_ALIASES.keys()),
    ).fetchall():
        canonical = _TASK_TYPE_ALIASES[ttype]
        findings.append(Finding(
            kind="noncanonical_task_type",
            severity=SEVERITY_INFO,
            message=f"task '{project}/{task_id}' uses type='{ttype}' (canonical: '{canonical}')",
            ref={"project": project, "task_id": task_id,
                 "from_type": ttype, "to_type": canonical},
            fixable=True,
        ))
    return findings


# ---- Top-level entry point ----

ALL_CHECKS = (
    check_pr_task_drift,
    check_all_prs_merged_but_task_open,
    check_ticket_fields_paired,
    check_orphan_prs,
    check_orphan_history,
    check_orphan_dependencies,
    check_duplicate_pr_urls,
    check_duplicate_pr_rows,
    check_prs_missing_author,
    check_stale_cron_runs,
    check_stale_task_sessions,
    check_long_idle_input_sessions,
    check_stale_review_sessions,
    check_task_type_canonicalization,
    check_terminal_status_missing_transition,
)

# Stable enumeration of every `kind` an `ALL_CHECKS` finding can emit.
# Used by `eva-cli audit --kind <k>` to validate the user input (an
# invalid kind used to silently filter to zero findings, which read as
# "all clean"). `audit_check_error` is included because that's what
# `run_audit` synthesises when a check itself raises.
KNOWN_KINDS: tuple[str, ...] = (
    "all_prs_merged_task_open",
    "audit_check_error",
    "duplicate_pr_row",
    "duplicate_pr_url",
    "long_idle_input_session",
    "noncanonical_task_type",
    "orphan_dependency",
    "orphan_history",
    "orphan_pr",
    "pr_merged_task_not_started",
    "pr_missing_author",
    "stale_cron_run",
    "stale_review_session",
    "stale_task_session",
    "terminal_status_missing_transition",
    "ticket_fields_unpaired",
)

# Severities a CLI / API caller can filter on. Mirrors the SEVERITY_*
# constants above so we can validate `--severity` flag values.
KNOWN_SEVERITIES: tuple[str, ...] = (
    SEVERITY_INFO, SEVERITY_WARN, SEVERITY_ERROR,
)


def run_audit() -> dict:
    """Run every check; return `{findings, summary}`.

    Summary aggregates counts by severity + kind so a CLI can render
    a one-line headline before drilling into details.
    """
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        try:
            findings.extend(check())
        except Exception as e:
            # A bad check shouldn't cripple the whole audit -- record
            # the failure as its own finding so the user can file a bug.
            findings.append(Finding(
                kind="audit_check_error",
                severity=SEVERITY_ERROR,
                message=f"check {check.__name__} raised: {e}",
                ref={"check": check.__name__},
                fixable=False,
            ))

    by_severity: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for f in findings:
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1

    return {
        "findings": [f.to_dict() for f in findings],
        "summary": {
            "total": len(findings),
            "by_severity": by_severity,
            "by_kind": by_kind,
        },
    }


# ---- Auto-fix for fixable findings ----

def _delete_orphan_pr(ref: dict) -> bool:
    cur = app_state._db._conn.execute(
        "DELETE FROM prs WHERE task_id = ? AND url = ?",
        (ref["task_id"], ref["url"]),
    )
    app_state._db._conn.commit()
    return cur.rowcount > 0


def _delete_orphan_history(ref: dict) -> bool:
    cur = app_state._db._conn.execute(
        "DELETE FROM task_history WHERE task_id = ?",
        (ref["task_id"],),
    )
    app_state._db._conn.commit()
    return cur.rowcount > 0


def _delete_orphan_dependency(ref: dict) -> bool:
    cur = app_state._db._conn.execute(
        "DELETE FROM task_dependencies "
        "WHERE task_id = ? AND depends_on = ?",
        (ref["task_id"], ref["depends_on"]),
    )
    app_state._db._conn.commit()
    return cur.rowcount > 0


def _close_stale_cron_run(ref: dict) -> bool:
    # Lazy import: keeps `audit` importable in tests that don't
    # touch cron jobs.
    from . import cron_jobs as _cron
    out = _cron.record_run_end(
        int(ref["run_id"]), status="cancelled",
        output="", error_message="stale: session gone",
    )
    return out is not None


def _close_stale_task_session(ref: dict) -> bool:
    """Flip a stuck task session row to `stopped` so the All-Live-Tasks
    page stops showing a ghost card. The tmux session is already gone
    -- this just heals the DB."""
    cur = app_state._db._conn.execute(
        "UPDATE sessions SET status='stopped' WHERE task_id=?",
        (ref["task_id"],),
    )
    app_state._db._conn.commit()
    return cur.rowcount > 0


def _close_stale_review_session(ref: dict) -> bool:
    """Flip a stuck review task's `review_my_workflow_state` back to
    `queued` AND drop the dead `sessions` row so the queue stops
    showing a ghost session."""
    from eva_db import EvaDB
    task_id = EvaDB._review_task_id_from_url(ref["url"])
    if not task_id:
        return False
    cur = app_state._db._conn.execute(
        "UPDATE tasks SET review_my_workflow_state='queued' "
        "WHERE task_id=? AND type='review'",
        (task_id,),
    )
    app_state._db._conn.execute(
        "DELETE FROM sessions WHERE task_id=?", (task_id,),
    )
    app_state._db._conn.commit()
    return cur.rowcount > 0


def _canonicalize_task_type(ref: dict) -> bool:
    """Rewrite a single task's `type` column to its canonical form."""
    cur = app_state._db._conn.execute(
        "UPDATE tasks SET type = ? WHERE task_id = ?",
        (ref["to_type"], ref["task_id"]),
    )
    app_state._db._conn.commit()
    return cur.rowcount > 0


def _backfill_pr_author(ref: dict) -> bool:
    """Re-fetch the PR via `gh pr view` and write the author column.

    `pr_missing_author` findings carry `{project, task_id, number, url}`
    so we can extract the repo from the URL, ask GitHub for the
    author, then UPDATE the row in place. Workstats filters out PRs
    with empty authors -- this backfill makes them count again.

    Returns False on any failure (gh missing, network, no author in
    response) so the audit's bulk-fix path treats it as a no-op.
    """
    from utils import repo_from_pr_url
    url = ref.get("url") or ""
    repo = repo_from_pr_url(url)
    number = ref.get("number")
    if not (repo and number):
        return False
    # Use the existing gh helper so allow-list checks + account
    # selection work the same as everywhere else. `gh_run_json`
    # passes `cmd` as the full subprocess argv (caller must include
    # the `gh` binary as `cmd[0]`); `repo=` selects the right
    # auth token via `gh_account_for_repo`.
    try:
        info = app_state.gh_run_json(
            ["gh", "pr", "view", str(number), "--repo", repo,
             "--json", "author"],
            repo=repo,
            default=None,
        )
    except Exception:
        return False
    if not info:
        return False
    author = (info.get("author") or {}).get("login") or ""
    if not author:
        return False
    cur = app_state._db._conn.execute(
        "UPDATE prs SET author = ? WHERE task_id = ? AND number = ?",
        (author, ref["task_id"], int(number)),
    )
    app_state._db._conn.commit()
    return cur.rowcount > 0


_FIXERS = {
    "orphan_pr": _delete_orphan_pr,
    "orphan_history": _delete_orphan_history,
    "orphan_dependency": _delete_orphan_dependency,
    "stale_cron_run": _close_stale_cron_run,
    "stale_task_session": _close_stale_task_session,
    # Long-idle (>24h waiting on input) sessions get the same UPDATE
    # treatment -- flip to `stopped`. Same SQL, different finding kind.
    "long_idle_input_session": _close_stale_task_session,
    "stale_review_session": _close_stale_review_session,
    "noncanonical_task_type": _canonicalize_task_type,
    "pr_missing_author": _backfill_pr_author,
}


def fix_finding(finding: dict) -> bool:
    """Apply the registered fix for a single finding. Returns True on
    success, False when no fixer is registered or the fix didn't match
    any rows (already gone)."""
    fixer = _FIXERS.get(finding.get("kind", ""))
    if not fixer:
        return False
    try:
        return fixer(finding.get("ref") or {})
    except Exception:
        return False


def fix_all(findings: list[dict]) -> dict:
    """Apply fixers to every fixable finding in `findings`. Returns
    `{fixed, skipped}` counts."""
    fixed = 0
    skipped = 0
    for f in findings:
        if not f.get("fixable"):
            skipped += 1
            continue
        if fix_finding(f):
            fixed += 1
        else:
            skipped += 1
    return {"fixed": fixed, "skipped": skipped}
