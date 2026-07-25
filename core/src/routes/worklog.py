"""Work log routes: auto-generate daily logs from eva.db, editable."""

import common
# pysqlite3 fallback -- see app_state.py for why mixing engines corrupts the WAL.
try:
    from pysqlite3 import dbapi2 as sqlite3
except ImportError:
    import sqlite3  # type: ignore[no-redef]
from datetime import datetime, timedelta

from pydantic import BaseModel

import app_state


def _status_suffix(status: str) -> str:
    """Emoji suffix that matches the user's Slack worklog style.
    Only `merged` PRs get a tag (`:white_check_mark:`); other statuses stay terse."""
    if (status or "").lower() == "merged":
        return " :white_check_mark:"
    return ""


def _pr_title_line(pr: dict, indent: str = "            ") -> str:
    """Render one PR as a single-line entry.

    Style from the user's samples:
      [EX-55389][PYTHON] Consolidate ... serializer logic :white_check_mark:
      [EX-1001] Fix flaky FlakyTestSuite ...

    PR title is passed through verbatim (it already carries its own
    `[TICKET][PREFIX]` prefix); `:white_check_mark:` is appended for merged PRs.
    When the title is missing we fall back to `#<number>` as the link
    label so the line never renders an empty `[](url)`.
    """
    title = (pr.get("title") or "").strip()
    url = pr.get("url") or ""
    number = pr.get("number")
    suffix = _status_suffix(pr.get("status") or "")
    label = title or (f"#{number}" if number else "")
    if url and label:
        body = f"[{label}]({url})"
    elif number:
        body = f"#{number} {title}".rstrip()
    else:
        body = title
    return f"{indent}- {body}{suffix}"


def _task_line(task: dict, indent: str = "            ") -> tuple[str, list[str]]:
    """Render a task (no PR) as a single line. Returns `(main, [])`.

    Style: "[TICKET-12345] Short summary ..." -- ticket ID is always
    bracketed (consistent with `[EX-...]`, `[MYPROJ-...]` etc.).
    The whole "[TICKET] description" is wrapped as the link label so the
    Slack paste becomes a single clickable entry (`<url|[TICKET] desc>`)
    matching the user's manual-standup style.

    Historically this also appended the first line of `task.notes` as a
    sub-bullet. That turned out to be a major source of verbosity (stale
    random notes, "CI failed: ..." fragments) in the auto-generated
    standup, so we no longer emit sub-bullets here. The user's manual
    edits stay -- they just don't need to be pre-populated with noise.
    """
    # For a synced ticket the `description` is the full (often huge,
    # multi-line) JIRA body -- use the one-line `ticket_summary` instead
    # so the standup line stays scannable. Reviews / plain tasks keep
    # their description. Collapse whitespace so a multi-line body never
    # spills across bullets, then clamp length.
    raw_desc = (task.get("ticket_summary") or task.get("description") or "")
    desc = " ".join(raw_desc.split())
    if len(desc) > 100:
        desc = desc[:97] + "..."
    ticket = task.get("ticket_id") or ""
    ticket_url = task.get("ticket_url") or ""
    suffix = _status_suffix(task.get("status") or "")

    if ticket and ticket_url:
        # Single link containing "[TICKET] description" so Slack shows one
        # clickable entry per task instead of a bare ticket id + orphan text.
        label = f"[{ticket}] {desc}".rstrip()
        main = f"{indent}- [{label}]({ticket_url}){suffix}".rstrip()
    elif ticket:
        main = f"{indent}- [{ticket}] {desc}{suffix}".rstrip()
    else:
        main = f"{indent}- {desc}{suffix}".rstrip()

    return main, []


# Columns surfaced from the prs table for worklog rendering. Defined
# once so `_query_status_changed_prs` and `_task_prs_for_worklog`
# return rows with identical shape.
# `project` lives on `tasks` now, not `prs`. `_query_status_changed_prs`
# JOINs to tasks to pull it back into the result dict so the worklog
# renderer (which buckets by project) keeps working.
_WORKLOG_PR_COLS = (
    "number", "title", "status", "ci_status", "url", "task_id",
)
_WORKLOG_PR_OUTPUT_COLS = _WORKLOG_PR_COLS + ("project",)
_WORKLOG_TASK_COLS = (
    "project", "task_id", "description", "ticket_id", "ticket_url",
    "notes", "status", "type", "ticket_summary", "ticket_synced_at",
)


def _resolve_agent_sessions(sessions: set) -> set:
    """Map agent tmux session names to the task_ids they belong to.

    Naming conventions across the unified tasks table:
      - plain task + review sessions are named BY their task_id
        (`review-apache-spark-55552` is both the session and the
        task_id), so they match directly.
      - ticket sessions are `ticket-<instance>-<ticket_id>`, which is
        NOT the task_id -- we recover the task_id by reconstructing that
        composite from the `tasks` row and matching.

    `cron-` sessions are dropped (cron jobs aren't tasks). One DB query
    handles all three shapes."""
    sessions = {s for s in sessions if s and not s.startswith("cron-")}
    if not sessions:
        return set()
    ph = ",".join("?" for _ in sessions)
    params = tuple(sessions)
    rows = app_state._db._conn.execute(
        "SELECT task_id FROM tasks "
        f"WHERE task_id IN ({ph}) "
        "   OR ('ticket-' || COALESCE(ticket_instance, '') || '-' "
        f"       || COALESCE(ticket_id, '')) IN ({ph})",
        params + params,
    ).fetchall()
    return {r["task_id"] for r in rows}


def _collect_active_task_ids(start: str, end: str) -> set:
    """Find task_ids the user touched in the window.

    Two sources, OR'd:
      1. `task.*` events -- explicit CRUD (created / updated / deleted),
         task_id parsed from the title prefix.
      2. `agent.*` events (prompt_submit / task_done / needs_input /
         needs_permission / session_start) -- the tmux session name in
         the `session` column, resolved back to its task_id. This
         INCLUDES ticket-fixing and PR-review sessions (they're tasks in
         the unified table); only cron sessions are dropped. Earlier this
         skipped `ticket-` / `review-` sessions entirely, which is why
         "I spent today fixing EX-1234 / reviewing a PR" never made it
         into the standup.
    """
    conn = app_state._notif_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT type, title, session, ts FROM events "
        "WHERE ts >= ? AND ts < ? "
        "  AND (type LIKE 'task.%' OR type LIKE 'agent.%') "
        "ORDER BY ts",
        (start, end),
    ).fetchall()
    conn.close()
    out: set = set()
    agent_sessions: set = set()
    for e in rows:
        ev_type = e["type"] or ""
        if ev_type.startswith("task."):
            tid = (e["title"] or "")
            for prefix in ("Task created: ", "Task updated: ", "Task deleted: "):
                tid = tid.replace(prefix, "")
            if tid:
                out.add(tid)
        elif ev_type.startswith("agent."):
            sess = (e["session"] or "").strip()
            if sess:
                agent_sessions.add(sess)
    out |= _resolve_agent_sessions(agent_sessions)
    return out


def _query_status_changed_prs(start: str, end: str) -> list:
    """Return PRs whose status actually flipped in the window. We
    deliberately filter on `status_changed_at` rather than `last_updated`
    -- the latter bumps on every gh refresh, which used to sweep in
    PRs that only got a metadata re-poll today.

    Legacy rows written before `status_changed_at` existed have '' in
    that column; they're silently skipped (the auto-generator is best
    effort, and falling back to `last_updated` would re-introduce the
    noisy-refresh problem we're escaping).
    """
    cols = ", ".join(f"p.{c}" for c in _WORKLOG_PR_COLS)
    rows = app_state._db._conn.execute(
        f"SELECT {cols}, t.project AS project, t.type AS task_type, "
        "  t.ticket_synced_at AS task_ticket_synced_at FROM prs p "
        "  JOIN tasks t ON t.task_id = p.task_id "
        "WHERE p.status_changed_at >= ? AND p.status_changed_at < ? "
        "ORDER BY t.project, p.number",
        (start, end),
    ).fetchall()
    return [
        {**{c: r[c] for c in _WORKLOG_PR_OUTPUT_COLS},
         "task_type": r["task_type"],
         "task_ticket_synced_at": r["task_ticket_synced_at"]}
        for r in rows
    ]


def _task_prs_for_worklog(project: str, task_id: str) -> list:
    """All PRs on (project, task_id) that have a usable title.

    A title-less row would render as `[](url)`; production prs always
    have titles (common.prs.add_pr enforces it), but legacy / test-only
    rows can slip through. Strict filter at the SQL layer is cheaper
    than per-row guards in the renderer.
    """
    cols = ", ".join(f"p.{c}" for c in _WORKLOG_PR_COLS)
    rows = app_state._db._conn.execute(
        f"SELECT {cols}, t.project AS project FROM prs p "
        "  JOIN tasks t ON t.task_id = p.task_id "
        "WHERE p.task_id=? AND p.title != '' "
        "ORDER BY p.number DESC",
        (task_id,),
    ).fetchall()
    return [{c: r[c] for c in _WORKLOG_PR_OUTPUT_COLS} for r in rows]


def _task_bucket_kind(task: dict) -> str:
    """Which worklog section a task belongs to: 'reviews' (PR reviews),
    'tickets' (a ticket SYNCED from JIRA -- EX / INTKEY / MYPROJ ...), or
    'project' (everything else, grouped by project).

    The discriminator for Tickets is `ticket_synced_at`, NOT a bare
    ticket_id: the user's own OSS PRs carry a manually-attached
    `SPARK-xxx` id but aren't synced escalations, and belong under their
    project, not the Tickets section."""
    if (task.get("type") == "review"
            or (task.get("task_id") or "").startswith("review-")):
        return "reviews"
    if (task.get("ticket_synced_at") or "").strip():
        return "tickets"
    return "project"


def _pr_bucket_kind(pr: dict) -> str:
    """Section for a status-changed PR -- mirrors `_task_bucket_kind`,
    keyed off the PR's TASK: a review task's PR is review work; a PR on a
    JIRA-synced ticket task is ticket work; everything else (including
    the user's own `[SPARK-...]` feature PRs) stays under its project."""
    if (pr.get("task_type") == "review"
            or (pr.get("task_id") or "").startswith("review-")):
        return "reviews"
    if (pr.get("task_ticket_synced_at") or "").strip():
        return "tickets"
    return "project"


def _build_worklog_buckets(task_ids: set, prs: list) -> tuple:
    """Split active tasks + status-changed PRs into per-project buckets
    plus two dedicated sections: `tickets` (every JIRA ticket the user
    touched) and `reviews` (PR reviews). Matches the user's standup
    convention of calling those out separately.

    Then promote each task's PRs into its bucket so the rendered line is
    the PR (title + url) rather than a bare task restatement -- the user
    wants the PR link (the reviewed PR for a review, the fix PR for a
    ticket).
    """
    by_project: dict = {}
    tickets: dict = {"tasks": {}, "prs": []}
    reviews: dict = {"tasks": {}, "prs": []}

    for tid in task_ids:
        row = app_state._db._conn.execute(
            f"SELECT {', '.join(_WORKLOG_TASK_COLS)} "
            "FROM tasks WHERE task_id=?",
            (tid,),
        ).fetchone()
        if not row:
            continue
        task = dict(zip(_WORKLOG_TASK_COLS, row))
        kind = _task_bucket_kind(task)
        if kind == "reviews":
            reviews["tasks"][tid] = task
        elif kind == "tickets":
            tickets["tasks"][tid] = task
        else:
            proj = task.get("project", "other")
            by_project.setdefault(proj, {"tasks": {}, "prs": []})
            by_project[proj]["tasks"][tid] = task

    for pr in prs:
        kind = _pr_bucket_kind(pr)
        if kind == "reviews":
            reviews["prs"].append(pr)
        elif kind == "tickets":
            tickets["prs"].append(pr)
        else:
            proj = pr["project"]
            by_project.setdefault(proj, {"tasks": {}, "prs": []})
            by_project[proj]["prs"].append(pr)

    # Promote each active task's PRs (even ones whose status didn't
    # change in the window). A task touched today usually means PR work,
    # and the standup should link the PR, not re-describe the ticket.
    seen = {p["number"] for proj in by_project.values() for p in proj["prs"]}
    seen.update(p["number"] for p in tickets["prs"])
    seen.update(p["number"] for p in reviews["prs"])
    for bucket in [*by_project.values(), tickets, reviews]:
        promoted: set = set()
        for tid, task in bucket["tasks"].items():
            pr_rows = _task_prs_for_worklog(task["project"], tid)
            if not pr_rows:
                continue
            for pr in pr_rows:
                if pr["number"] in seen:
                    continue
                bucket["prs"].append(pr)
                seen.add(pr["number"])
            promoted.add(tid)
        for tid in promoted:
            bucket["tasks"].pop(tid, None)

    return by_project, tickets, reviews


def _render_worklog_lines(header: str, by_project: dict,
                          tickets: dict, reviews: dict) -> list:
    """Format the bucketed data as the standup-style markdown."""
    proj_names = app_state.project_name_map()
    lines = [header, "    - Status:"]

    def _emit_group(header_line: str, data: dict) -> None:
        lines.append(header_line)
        for pr in data.get("prs", []):
            lines.append(_pr_title_line(pr))
        pr_tasks = {p.get("task_id") for p in data.get("prs", [])}
        for tid, task in data.get("tasks", {}).items():
            if tid in pr_tasks:
                continue
            main, subs = _task_line(task)
            lines.append(main)
            lines.extend(subs)

    has_tickets = bool(tickets["tasks"] or tickets["prs"])
    has_reviews = bool(reviews["tasks"] or reviews["prs"])
    if not by_project and not has_tickets and not has_reviews:
        lines.append("        - No activity recorded")
    else:
        for pid, data in sorted(by_project.items()):
            pname = proj_names.get(pid, pid) or "Other"
            _emit_group(f"        - {pname}:", data)
        # Tickets + Reviews always land last under their own headers.
        if has_tickets:
            _emit_group("        - Tickets:", tickets)
        if has_reviews:
            _emit_group("        - Reviews:", reviews)

    lines.append("    - Meeting Notes:")
    return lines


def _generate_worklog_content(start: str, end: str, header: str) -> str:
    """Auto-generate work log markdown for any [start, end) timestamp range.

    Output style mirrors the user's Slack-style update:
      - <header>
          - Status:
              - <Project Name>:
                  - [EX-...][PYTHON] <PR title> :white_check_mark:
          - Meeting Notes:

    Rendering rules:
      * PR whose status flipped in window -> one line with the PR's own
        title + URL as a single markdown link (`[title](url)`).
      * Task active in window that has ANY PR on file -> render its PR(s)
        instead of a bare task line. The user wants the PR link, not a
        ticket restatement.
      * Task active in window with no PR -> fall back to a task line
        (`[TICKET] description` linked to the ticket URL).
    """
    task_ids = _collect_active_task_ids(start, end)
    prs = _query_status_changed_prs(start, end)
    by_project, tickets, reviews = _build_worklog_buckets(task_ids, prs)
    return "\n".join(
        _render_worklog_lines(header, by_project, tickets, reviews))


def _generate_worklog_markdown(date: str) -> str:
    """Generate a work log for a single calendar date."""
    next_date = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    return _generate_worklog_content(date, next_date, f"- {date}")


# -- API endpoints --

def _public_log(row: dict | None) -> dict | None:
    """API contract maps the DB column `log_date` back to `date` so
    existing frontend / docs / TypeScript types don't have to change.
    The rename was internal-only (memo item #4) -- the public API
    keeps `date` as the JSON key."""
    if not row:
        return row
    out = dict(row)
    if "log_date" in out:
        out["date"] = out.pop("log_date")
    return out


@app_state.app.get("/api/worklog")
def list_work_logs(limit: int = 30):
    """List recent work logs."""
    logs = app_state._db.list_work_logs(limit)
    return {"logs": [_public_log(r) for r in logs]}


@app_state.app.get("/api/worklog-range")
def get_work_log_range(start: str, end: str, label: str = ""):
    """Generate a work log for an arbitrary [start, end) range. Not persisted."""
    header = f"- {label}" if label else f"- {start} to {end}"
    content = _generate_worklog_content(start, end, header)
    return {"start": start, "end": end, "label": label, "content": content}


@app_state.app.get("/api/worklog/{date}")
def get_work_log(date: str):
    """Get work log for a date. Auto-generates if not yet saved."""
    log = app_state._db.get_work_log(date)
    if log:
        return _public_log(log)
    content = _generate_worklog_markdown(date)
    app_state._db.save_work_log(date, content, auto_generated=content)
    return _public_log(app_state._db.get_work_log(date))


class WorkLogUpdate(BaseModel):
    content: str


@app_state.app.put("/api/worklog/{date}")
def update_work_log(date: str, body: WorkLogUpdate):
    """Save edited work log content."""
    app_state._db.save_work_log(date, body.content)
    return {"ok": True}


@app_state.app.delete("/api/worklog/{date}")
def delete_work_log(date: str):
    """Delete saved log so next GET regenerates."""
    app_state._db.delete_work_log(date)
    return {"ok": True}
