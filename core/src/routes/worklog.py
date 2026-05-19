"""Work log routes: auto-generate daily logs from eva.db, editable."""

import common
import re
# pysqlite3 fallback -- see app_state.py for why mixing engines corrupts the WAL.
try:
    from pysqlite3 import dbapi2 as sqlite3
except ImportError:
    import sqlite3  # type: ignore[no-redef]
from datetime import datetime, timedelta

from pydantic import BaseModel

import app_state


# Matches titles / ticket ids that carry an ES ticket prefix, with or without
# brackets. Examples: "EX-1001", "[EX-1001]", "[EX-1001] Fix flaky ...".
# The brackets themselves are preserved in rendered output -- this regex is
# only for classifying an entry as "ES ticket" so it lands in its own section.
_ES_PREFIX_RE = re.compile(r"^\[?(EX-\d+)\]?\s*", re.IGNORECASE)


def _status_suffix(status: str) -> str:
    """Emoji suffix that matches the user's Slack worklog style.
    Only `merged` PRs get a tag (`:white_check_mark:`); other statuses stay terse."""
    if (status or "").lower() == "merged":
        return " :white_check_mark:"
    return ""


def _is_es_title(text: str) -> bool:
    """True when a PR title or ticket id starts with an EX-xxxxxx prefix."""
    return bool(_ES_PREFIX_RE.match(text or ""))


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
    desc = (task.get("description") or "").strip()
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
_WORKLOG_PR_COLS = (
    "number", "title", "status", "ci_status", "url", "project", "task_id",
)
_WORKLOG_TASK_COLS = (
    "project", "task_id", "description", "ticket_id", "ticket_url",
    "notes", "status",
)


def _collect_active_task_ids(start: str, end: str) -> set:
    """Find task_ids the user touched in the window.

    Two sources, OR'd:
      1. `task.*` events -- explicit CRUD (created / updated / deleted),
         task_id parsed from the title prefix.
      2. `agent.*` events (prompt_submit / task_done / needs_input /
         needs_permission / session_start) -- the tmux session name in
         the `session` column equals the task_id for task sessions.
         Skip cron / review / ticket sessions (those have their own
         worklog story).

    The previous implementation only used #1, which silently dropped
    every task the user worked on via the agent without triggering a
    CRUD event -- i.e. most of the actual work. Adding #2 catches
    "I had a long claude session on task X today, no metadata edits".
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
            # Skip non-task sessions; they have their own surfaces.
            if not sess or sess.startswith(
                ("cron-", "review-", "ticket-")
            ):
                continue
            out.add(sess)
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
        f"SELECT {cols} FROM prs p "
        "WHERE p.status_changed_at >= ? AND p.status_changed_at < ? "
        "ORDER BY p.project, p.number",
        (start, end),
    ).fetchall()
    return [{c: r[c] for c in _WORKLOG_PR_COLS} for r in rows]


def _task_prs_for_worklog(project: str, task_id: str) -> list:
    """All PRs on (project, task_id) that have a usable title.

    A title-less row would render as `[](url)`; production prs always
    have titles (common.prs.add_pr enforces it), but legacy / test-only
    rows can slip through. Strict filter at the SQL layer is cheaper
    than per-row guards in the renderer.
    """
    cols = ", ".join(_WORKLOG_PR_COLS)
    rows = app_state._db._conn.execute(
        f"SELECT {cols} FROM prs "
        "WHERE project=? AND task_id=? AND title != '' "
        "ORDER BY number DESC",
        (project, task_id),
    ).fetchall()
    return [{c: r[c] for c in _WORKLOG_PR_COLS} for r in rows]


def _build_worklog_buckets(task_ids: set, prs: list) -> tuple:
    """Split active tasks + status-changed PRs into per-project buckets
    and a single `es_bucket` that pulls every EX-ticket out into its
    own section (matches the user's Slack standup convention).

    Then promote each task's PRs into the bucket so the rendered line
    is the PR (which carries title + url) rather than a bare task
    restatement -- the user wants the PR link.
    """
    by_project: dict = {}
    es_bucket: dict = {"tasks": {}, "prs": []}

    for tid in task_ids:
        row = app_state._db._conn.execute(
            f"SELECT {', '.join(_WORKLOG_TASK_COLS)} "
            "FROM tasks WHERE task_id=?",
            (tid,),
        ).fetchone()
        if not row:
            continue
        task = dict(zip(_WORKLOG_TASK_COLS, row))
        if _is_es_title(task.get("ticket_id") or ""):
            es_bucket["tasks"][tid] = task
        else:
            proj = task.get("project", "other")
            by_project.setdefault(proj, {"tasks": {}, "prs": []})
            by_project[proj]["tasks"][tid] = task

    for pr in prs:
        if _is_es_title(pr.get("title") or ""):
            es_bucket["prs"].append(pr)
        else:
            proj = pr["project"]
            by_project.setdefault(proj, {"tasks": {}, "prs": []})
            by_project[proj]["prs"].append(pr)

    # Promote each active task's PRs (even ones whose status didn't
    # change in the window). A task touched today usually means PR work,
    # and the standup should link the PR, not re-describe the ticket.
    seen = {p["number"] for proj in by_project.values() for p in proj["prs"]}
    seen.update(p["number"] for p in es_bucket["prs"])
    for bucket in [*by_project.values(), es_bucket]:
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

    return by_project, es_bucket


def _render_worklog_lines(header: str, by_project: dict, es_bucket: dict) -> list:
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

    has_es = bool(es_bucket["tasks"] or es_bucket["prs"])
    if not by_project and not has_es:
        lines.append("        - No activity recorded")
    else:
        for pid, data in sorted(by_project.items()):
            pname = proj_names.get(pid, pid)
            _emit_group(f"        - {pname}:", data)
        # ES tickets always land last under their own section header.
        if has_es:
            _emit_group("        - ES tickets:", es_bucket)

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
    by_project, es_bucket = _build_worklog_buckets(task_ids, prs)
    return "\n".join(_render_worklog_lines(header, by_project, es_bucket))


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
