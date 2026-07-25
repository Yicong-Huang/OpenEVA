"""Global search over tasks, tickets, reviews, sessions, and PRs.

Free-text match against id / title / description / ticket_id / ticket_url /
notes / branch, plus a small filter DSL:

    type:task | type:ticket | type:review | type:pr | type:session
                                         -- restrict entity type
    status:<value>                        -- match status field
    project:<id>                          -- scope to one project
    in:task                               -- PRs-only: only PRs attached
                                             to a task (i.e. linked)
    ticket:<id-substring>                 -- substring match on ticket id
                                             (e.g. `ticket:EX` or
                                             `ticket:EX-99`). For PRs
                                             and sessions, matches against
                                             the linked task's ticket.

Any bare whitespace-separated words are joined by AND across text
fields. Filter keys are case-insensitive; values are matched
case-insensitively against the relevant field.

The endpoint is shallow by design: it returns up to `limit` rows across
all entity types so the top-bar dropdown stays snappy. Users who need a
deeper scan go to the dedicated PR / task pages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

import app_state


# Keys the filter DSL recognises. Anything else is treated as text.
_FILTER_KEYS = frozenset({"type", "status", "project", "in", "ticket"})

# Bare tokens that act as `type:<value>` shorthand. Lets users type
# "pr in:task" instead of "type:pr in:task".
_TYPE_SHORTHANDS = frozenset({
    "task", "tasks",
    "ticket", "tickets",
    "review", "reviews",
    "pr", "prs",
    "session", "sessions",
})
_TYPE_ALIASES = {
    "tasks": "task",
    "tickets": "ticket",
    "reviews": "review",
    "prs": "pr",
    "sessions": "session",
}


@dataclass
class Query:
    """Parsed search query. `text` is always lowercased."""
    text: str = ""
    type: str | None = None     # task | ticket | review | pr | session
    status: str | None = None
    project: str | None = None
    in_: str | None = None      # "task" -> PRs linked to a task
    ticket: str | None = None   # substring match against ticket_id (e.g. "EX" or "EX-99")
    # keep the raw filters dict for debugging / future keys
    extras: dict[str, str] = field(default_factory=dict)


def parse_query(raw: str) -> Query:
    """Split a raw search string into text terms + recognised filters.

    A token is `k:v` iff k is a known filter key; everything else is
    text. Unknown `k:v` tokens fall through into `text` so the user
    gets "search for literal 'foo:bar'" behaviour rather than silent
    discard."""
    q = Query()
    text_parts: list[str] = []
    for tok in raw.split():
        if ":" in tok:
            k, _, v = tok.partition(":")
            k = k.lower()
            if k in _FILTER_KEYS:
                v = v.lower()
                if k == "type":
                    q.type = _TYPE_ALIASES.get(v, v)
                elif k == "status":
                    q.status = v
                elif k == "project":
                    q.project = v
                elif k == "in":
                    q.in_ = v
                elif k == "ticket":
                    # Keep original value (not lowercased) for ticket ids,
                    # but store lowercased for case-insensitive match.
                    q.ticket = v
                continue
        # Bare type shorthand: "pr" / "task" / "session" (and plurals)
        # map to the corresponding type filter -- but only if no explicit
        # type filter is set yet, so "type:task pr" still means tasks
        # matching "pr".
        low = tok.lower()
        if q.type is None and low in _TYPE_SHORTHANDS:
            q.type = _TYPE_ALIASES.get(low, low)
            continue
        text_parts.append(low)
    q.text = " ".join(text_parts).strip()
    return q


# ---------------------------------------------------------------------------
# Per-entity matchers. Each returns a list of result dicts.
# ---------------------------------------------------------------------------

def _match_text(text: str, needle: str) -> bool:
    """needle may be '' (always match) or a space-separated list of
    words; ALL words must appear in `text` (case-insensitive)."""
    if not needle:
        return True
    hay = (text or "").lower()
    for word in needle.split():
        if word not in hay:
            return False
    return True


def _project_names() -> dict[str, str]:
    """Map project_id -> name (falls back to id)."""
    try:
        return {p["id"]: p.get("name", p["id"])
                for p in app_state._db.list_projects()}
    except Exception:
        return {}


_SUBTITLE_DESC_LIMIT = 60  # chars kept in dropdown subtitles


def _truncate(text: str, limit: int = _SUBTITLE_DESC_LIMIT) -> str:
    """Clip `text` to `limit` chars, appending "..." when truncation
    happened. Used for the search-dropdown subtitle so long task /
    session descriptions don't overflow the row."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _task_result(project_id: str, proj_name: str, t: dict) -> dict:
    """Shape a task row for the dropdown."""
    tid = t.get("task_id", "")
    desc = (t.get("description") or "").strip()
    ticket = t.get("ticket_id") or ""
    sub_parts = [proj_name]
    if ticket:
        sub_parts.append(ticket)
    if desc:
        sub_parts.append(_truncate(desc))
    return {
        "type": "task",
        "title": tid,
        "subtitle": " - ".join(sub_parts),
        "badge": t.get("status") or "",
        "project_id": project_id,
        "task_id": tid,
    }


def _ticket_result(t: dict) -> dict:
    """Shape a JIRA ticket row for the dropdown."""
    key = t.get("key") or ""
    summary = (t.get("summary") or "").strip()
    sub_parts = []
    if t.get("instance_name"):
        sub_parts.append(str(t["instance_name"]))
    if t.get("issue_type"):
        sub_parts.append(str(t["issue_type"]))
    if t.get("priority"):
        sub_parts.append(str(t["priority"]))
    if summary:
        sub_parts.append(_truncate(summary))
    return {
        "type": "ticket",
        "title": key,
        "subtitle": " - ".join(sub_parts),
        "badge": t.get("status") or "",
        "project_id": t.get("project_key") or "",
        "task_id": key,
        "ticket_key": key,
        "ticket_instance": t.get("instance_name") or "",
    }


def _review_result(r: dict) -> dict:
    """Shape a review request row for the dropdown."""
    repo = r.get("repo") or ""
    number = r.get("number")
    title = r.get("title") or ""
    sub_parts = []
    if repo:
        sub_parts.append(repo)
    if r.get("author"):
        sub_parts.append("by " + str(r["author"]))
    if title:
        sub_parts.append(_truncate(title))
    return {
        "type": "review",
        "title": ("#" + str(number)) if number else "(review)",
        "subtitle": " - ".join(sub_parts),
        "badge": (
            r.get("my_workflow_state")
            or r.get("my_review_state")
            or r.get("status")
            or ""
        ),
        "project_id": "",
        "review_url": r.get("url") or "",
        "pr_number": number,
        "pr_repo": repo,
    }


def _session_result(project_id: str, proj_name: str, s: dict, t: dict) -> dict:
    """Shape a session row. `t` is the parent task for the description."""
    name = s.get("tmux_name") or s.get("task_id") or ""
    status = s.get("status") or ""
    desc = (t.get("description") or "").strip() if t else ""
    sub = proj_name
    if desc:
        sub += " - " + _truncate(desc)
    return {
        "type": "session",
        "title": name,
        "subtitle": sub,
        "badge": status,
        "project_id": project_id,
        "task_id": s.get("task_id") or name,
    }


def _pr_result(pr: dict, proj_name: str) -> dict:
    number = pr.get("number")
    title = pr.get("title") or ""
    repo_url = pr.get("url") or ""
    # Extract "owner/repo" from the URL for the subtitle.
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/", repo_url)
    repo = m.group(1) if m else ""
    sub_parts = []
    if repo:
        sub_parts.append(repo)
    if proj_name:
        sub_parts.append(proj_name)
    if pr.get("task_id"):
        sub_parts.append("task:" + str(pr["task_id"]))
    return {
        "type": "pr",
        "title": ("#" + str(number)) if number else "(pr)",
        "subtitle": " - ".join(sub_parts) + (" - " + title if title else ""),
        "badge": pr.get("status") or "",
        "project_id": pr.get("project") or "",
        "pr_number": number,
        "pr_repo": repo,
        "task_id": pr.get("task_id") or None,
    }


def _ticket_matches(ticket_id: str | None, filter_value: str | None) -> bool:
    """True if `filter_value` is empty (no filter) or is a case-insensitive
    substring of `ticket_id`. Lets `ticket:EX` match both EX-1 and
    EX-2000, while `ticket:EX-99` narrows further."""
    if not filter_value:
        return True
    return filter_value.lower() in (ticket_id or "").lower()


def _search_tasks(q: Query, project_names: dict[str, str]) -> list[dict]:
    if q.type not in (None, "task"):
        return []
    results: list[dict] = []
    try:
        tasks = app_state._db.list_tasks(q.project if q.project else None)
    except Exception:
        return []
    for t in tasks:
        if (t.get("type") or "") == "review":
            continue
        if t.get("ticket_synced_at"):
            continue
        pid = t.get("project") or ""
        if q.project and pid != q.project:
            continue
        if q.status and (t.get("status") or "").lower() != q.status:
            continue
        if not _ticket_matches(t.get("ticket_id"), q.ticket):
            continue
        # Free-text haystack covers every human-searchable field on the
        # task: id, type, description, ticket id+url, and notes. Sessions and
        # PRs resolve their parent task so searches there hit these too.
        hay = " ".join([
            t.get("task_id") or "",
            t.get("type") or "",
            t.get("description") or "",
            t.get("ticket_id") or "",
            t.get("ticket_url") or "",
            t.get("notes") or "",
            t.get("group_name") or "",
        ])
        if _match_text(hay, q.text):
            results.append(_task_result(pid, project_names.get(pid, pid), t))
    return results


def _search_tickets(q: Query) -> list[dict]:
    if q.type not in (None, "ticket"):
        return []
    try:
        rows = app_state._db.list_tickets(limit=1000)
    except Exception:
        return []
    results: list[dict] = []
    for t in rows:
        if q.project and (t.get("project_key") or "").lower() != q.project:
            continue
        if q.status and (t.get("status") or "").lower() != q.status:
            continue
        if not _ticket_matches(t.get("key"), q.ticket):
            continue
        hay = " ".join([
            t.get("key") or "",
            t.get("summary") or "",
            t.get("description") or "",
            t.get("status") or "",
            t.get("priority") or "",
            t.get("issue_type") or "",
            t.get("project_key") or "",
            t.get("assignee_email") or "",
            t.get("reporter_email") or "",
            t.get("url") or "",
            t.get("labels") or "",
            t.get("components") or "",
            t.get("fix_versions") or "",
            t.get("parent_key") or "",
            t.get("resolution") or "",
            t.get("status_category") or "",
            t.get("severity") or "",
            t.get("instance_name") or "",
        ])
        if _match_text(hay, q.text):
            results.append(_ticket_result(t))
    return results


def _search_reviews(q: Query) -> list[dict]:
    if q.type not in (None, "review"):
        return []
    try:
        rows = app_state._db.list_review_prs()
    except Exception:
        return []
    results: list[dict] = []
    for r in rows:
        if q.status:
            statuses = {
                (r.get("status") or "").lower(),
                (r.get("ci_status") or "").lower(),
                (r.get("review_status") or "").lower(),
                (r.get("my_review_state") or "").lower(),
                (r.get("my_workflow_state") or "").lower(),
            }
            if q.status not in statuses:
                continue
        hay = " ".join([
            r.get("url") or "",
            r.get("repo") or "",
            str(r.get("number") or ""),
            r.get("title") or "",
            r.get("author") or "",
            r.get("status") or "",
            r.get("ci_status") or "",
            r.get("review_status") or "",
            r.get("my_review_state") or "",
            r.get("my_workflow_state") or "",
            r.get("head_branch") or "",
            r.get("base_branch") or "",
            r.get("source") or "",
        ])
        if _match_text(hay, q.text):
            results.append(_review_result(r))
    return results


def _search_sessions(q: Query, project_names: dict[str, str]) -> list[dict]:
    if q.type not in (None, "session"):
        return []
    try:
        sessions = app_state._db.list_sessions()
    except Exception:
        return []
    results: list[dict] = []
    for s in sessions:
        pid = s.get("project") or ""
        if q.project and pid != q.project:
            continue
        if q.status and (s.get("status") or "").lower() != q.status:
            continue
        # Pull the parent task so session search can match on ticket + notes
        # (users look for sessions by what they're for, not by their tmux name).
        t = {}
        try:
            t = app_state._db.get_task(pid, s.get("task_id", "")) or {}
        except Exception:
            pass
        if not _ticket_matches(t.get("ticket_id"), q.ticket):
            continue
        hay = " ".join([
            s.get("tmux_name") or "",
            s.get("task_id") or "",
            t.get("description") or "",
            t.get("ticket_id") or "",
            t.get("ticket_url") or "",
            t.get("notes") or "",
        ])
        if _match_text(hay, q.text):
            results.append(_session_result(pid, project_names.get(pid, pid), s, t))
    return results


def _search_prs(q: Query, project_names: dict[str, str]) -> list[dict]:
    if q.type not in (None, "pr"):
        return []
    try:
        # Fetch by status only; this layer owns richer text matching over
        # repo/url/author/branches/review fields in addition to title/task_id.
        rows = app_state._db.list_all_prs(
            status=(q.status or ""),
            search="",
        )
    except Exception:
        return []
    # Cache parent-task ticket lookups across PRs in the same project to
    # avoid N+1 hits on list_all_prs responses.
    task_ticket_cache: dict[tuple[str, str], str | None] = {}

    def _pr_ticket(pr: dict) -> str | None:
        tid = pr.get("task_id")
        pid = pr.get("project") or ""
        if not tid or not pid:
            return None
        key = (pid, tid)
        if key not in task_ticket_cache:
            try:
                t = app_state._db.get_task(pid, tid) or {}
                task_ticket_cache[key] = t.get("ticket_id")
            except Exception:
                task_ticket_cache[key] = None
        return task_ticket_cache[key]

    results: list[dict] = []
    for pr in rows:
        pid = pr.get("project") or ""
        if q.project and pid != q.project:
            continue
        if q.in_ == "task" and not pr.get("task_id"):
            continue
        if q.ticket and not _ticket_matches(_pr_ticket(pr), q.ticket):
            continue
        hay = " ".join([
            str(pr.get("number") or ""),
            pr.get("url") or "",
            pr.get("title") or "",
            pr.get("task_id") or "",
            pr.get("task_description") or "",
            pr.get("status") or "",
            pr.get("ci_status") or "",
            pr.get("review_status") or "",
            pr.get("my_review_state") or "",
            pr.get("author") or "",
            pr.get("head_branch") or "",
            pr.get("base_branch") or "",
        ])
        if not _match_text(hay, q.text):
            continue
        proj_name = project_names.get(pid, pid)
        results.append(_pr_result(pr, proj_name))
    return results


def search(raw_query: str, limit: int = 20) -> list[dict]:
    """Top-level entry: parse the query, fan out to per-entity searchers,
    and return the first `limit` rows. Keep task-like work first,
    followed by live sessions and PRs."""
    q = parse_query(raw_query)
    project_names = _project_names()
    merged: Iterable[dict] = (
        _search_tasks(q, project_names)
        + _search_tickets(q)
        + _search_reviews(q)
        + _search_sessions(q, project_names)
        + _search_prs(q, project_names)
    )
    return list(merged)[:limit]
