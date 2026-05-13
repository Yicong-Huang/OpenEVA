"""GitHub notification polling service.

Registered as a scheduled interval job by `services.scheduler`. Each run
incrementally polls GitHub notifications, classifies them, and emits
events via `app_state.emit_event`. Also registers handlers that update
task/PR status on the events it emits.

Previously this module owned its own daemon thread; see
`services/scheduler.py` for the migration rationale.
"""

import re as _re
import sqlite3
import time as _time

import app_state
from common import settings as _settings
from utils import repo_from_pr_url, pr_number_from_url


# Default cadence -- 10 seconds. Notifications API is cheap; the floor
# is the GitHub API minimum poll interval (`X-Poll-Interval` header is
# typically 60s on busy accounts but 10s is fine for most). Settings
# key `service.intervals.github_poll_seconds` overrides at startup.
GH_POLL_INTERVAL_SECONDS = 10

# Floor: 5s. The GitHub Notifications API throttles via the
# `X-Poll-Interval` header on its own; we just guard the bottom.
_MIN_GH_POLL_INTERVAL_SECONDS = 5


def get_interval_seconds() -> int:
    """User-tunable GitHub-poll cadence. Delegates to the shared
    poller-cadence accessor for consistent validation."""
    return _settings.get_interval_seconds(
        _settings.KEY_INTERVAL_GITHUB_POLL,
        GH_POLL_INTERVAL_SECONDS,
        min_s=_MIN_GH_POLL_INTERVAL_SECONDS,
    )


# -- Polling state --

_gh_last_poll = {"ts": 0, "seen_ids": {}, "since": {}}
_GH_POLL_INTERVAL = GH_POLL_INTERVAL_SECONDS  # back-compat alias
_SEEN_IDS_MAX = 3000


def _load_seen_ids():
    """Load seen notification IDs as {source_id: ts} from the events table."""
    try:
        with app_state._notif_db() as conn:
            rows = conn.execute(
                "SELECT source_id, ts FROM events WHERE source = 'github' AND source_id IS NOT NULL ORDER BY ts DESC LIMIT 2000"
            ).fetchall()
            return {r[0]: (r[1] or "") for r in rows if r[0]}
    except sqlite3.Error:
        return {}


def _load_since_watermarks():
    """Load since watermark from the max ts of github events."""
    try:
        with app_state._notif_db() as conn:
            row = conn.execute("SELECT MAX(ts) FROM events WHERE source = 'github'").fetchone()
            if row and row[0]:
                return row[0]
    except sqlite3.Error:
        pass
    return None


def _lookup_pr_by_branch(branch, repo=None):
    """Look up PR number by head_branch from the prs table."""
    try:
        if repo:
            upstream = app_state.FORK_TO_UPSTREAM.get(repo, repo)
            url_pattern = f"%{upstream}/pull/%"
            row = app_state._db._conn.execute(
                "SELECT number FROM prs WHERE head_branch = ? AND url LIKE ? AND status = 'open' LIMIT 1",
                (branch, url_pattern),
            ).fetchone()
            if row:
                return row[0]
        row = app_state._db._conn.execute(
            "SELECT number FROM prs WHERE head_branch = ? AND status = 'open' LIMIT 1",
            (branch,),
        ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


# -- Notification parsing --

_REASON_MAP = {
    "review_requested": "Review requested",
    "comment": "New comment",
    "mention": "Mentioned",
    "ci_activity": "CI update",
    "assign": "Assigned",
    "author": "PR update",
    "state_change": "State changed",
    "subscribed": "Subscribed",
}

_JQ_NOTIF = '.[] | "\\(.id)\\t\\(.reason)\\t\\(.subject.type)\\t\\(.subject.title)\\t\\(.repository.full_name)\\t\\(.updated_at)\\t\\(.unread)\\t\\(.subject.url // "")"'


def _parse_notification_line(line, now):
    """Parse a single tab-delimited notification line into an event dict."""
    parts = line.split("\t")
    if len(parts) < 7:
        return None
    nid, reason, subtype, title, repo, updated = parts[:6]
    unread = parts[6] == "true"
    subject_url = parts[7] if len(parts) > 7 else ""

    pr_number = None
    branch = None
    if subject_url and "/pulls/" in subject_url:
        try:
            pr_number = int(subject_url.split("/pulls/")[-1])
        except ValueError:
            pass

    if not pr_number and reason == "ci_activity" and title:
        m = _re.search(r'for (\S+) branch', title)
        if m:
            branch = m.group(1)
            if branch in ("master", "main"):
                return None
            pr_number = _lookup_pr_by_branch(branch, repo)

    if not app_state.is_repo_allowed(repo):
        return None

    return {
        "id": nid,
        "type": reason,
        "label": _REASON_MAP.get(reason, reason),
        "subject": subtype,
        "title": title,
        "repo": repo,
        "pr_number": pr_number,
        "branch": branch,
        "updated": updated,
        "unread": unread,
        "ts": now,
    }


# -- Event builders --

def _build_gh_events(events):
    """Convert raw GitHub events to notification dicts for emit_event."""
    severity_map = {
        "review_requested": "warning",
        "comment": "info",
        "mention": "warning",
        "ci_activity": "error",
        "assign": "info",
        "author": "info",
        "state_change": "info",
    }
    notifs = []
    for ev in events:
        reason = ev.get("type", "event")
        repo = (ev.get("repo") or "").split("/")[-1]
        pr_tag = " #" + str(ev["pr_number"]) if ev.get("pr_number") else ""
        url = None
        if ev.get("pr_number") and ev.get("repo"):
            upstream = app_state.FORK_TO_UPSTREAM.get(ev["repo"], ev["repo"])
            url = "https://github.com/" + upstream + "/pull/" + str(ev["pr_number"])
        notifs.append({
            "_reason": reason,
            "source_id": ev.get("id"),
            "title": ev.get("label", "") + " - " + repo + pr_tag,
            "message": ev.get("title", ""),
            "severity": severity_map.get(reason, "info"),
            "url": url,
            "ts": ev.get("updated", ""),
        })
    return notifs


# -- Polling loop --

def _poll_github_notifications():
    """Poll GitHub notifications API incrementally using 'since' parameter.

    No-op when the user disabled the GitHub poll plugin via the
    Settings UI -- avoids burning gh API quota on a paused service.
    """
    from common import settings as _settings
    if not _settings.is_plugin_enabled("github_poll"):
        return
    now = _time.time()
    if now - _gh_last_poll["ts"] < _GH_POLL_INTERVAL:
        return

    _gh_last_poll["ts"] = now

    new_events = []
    poll_repos = [r for r in app_state.ALLOWED_REPOS if not r.endswith("/*")]
    for org in app_state.ALLOWED_ORGS:
        if not any(r.startswith(org + "/") for r in poll_repos):
            poll_repos.append(f"{org}/main")
    for repo_hint in poll_repos:
        try:
            since = _gh_last_poll["since"].get(repo_hint)
            url = "notifications?all=true&per_page=100"
            if since:
                url += "&since=" + since

            cmd = ["gh", "api", url, "--jq", _JQ_NOTIF]
            result = app_state.gh_run(cmd, repo=repo_hint, timeout=15)
            if result.returncode != 0:
                print(f"[gh-poll] {repo_hint}: gh returned {result.returncode}: {result.stderr[:100]}", flush=True)
                continue

            max_updated = since
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                ev = _parse_notification_line(line, now)
                if not ev:
                    continue

                nid = ev["id"]
                updated = ev["updated"]

                if not max_updated or updated > max_updated:
                    max_updated = updated

                if _gh_last_poll["seen_ids"].get(nid) == updated:
                    continue

                ev["_is_new"] = nid not in _gh_last_poll["seen_ids"]
                _gh_last_poll["seen_ids"][nid] = updated
                new_events.append(ev)

            if max_updated:
                _gh_last_poll["since"][repo_hint] = max_updated

        except Exception as e:
            print(f"[gh-poll] {repo_hint}: error: {e}", flush=True)
            continue

    for ev in new_events:
        if ev["branch"] and not ev["pr_number"]:
            ev["pr_number"] = _lookup_pr_by_branch(ev["branch"], ev.get("repo"))

    if new_events:
        truly_new = [e for e in new_events if e.get("_is_new")]
        for n in _build_gh_events(truly_new):
            app_state.emit_event("github." + n.pop("_reason", "event"), n)

    seen = _gh_last_poll["seen_ids"]
    if len(seen) > _SEEN_IDS_MAX:
        sorted_ids = sorted(seen.items(), key=lambda x: x[1])
        for nid, _ in sorted_ids[:len(seen) - _SEEN_IDS_MAX]:
            del seen[nid]


def poll_github_once() -> None:
    """One tick of the GitHub poller. Registered on the scheduler.

    Wraps `_poll_github_notifications` so any per-run exception is logged
    rather than bubbling up into the scheduler (which would mark the job
    failed and rely on job listeners for visibility)."""
    try:
        _poll_github_notifications()
    except Exception as e:
        print(f"[gh-poll] error: {e}", flush=True)


# -- Event handlers: update task/PR status on GitHub events --

def _on_gh_notification(notification):
    """Update task status based on a single GitHub notification."""
    ntype = notification.get("type", "")
    message = notification.get("message", "")
    url = notification.get("url", "")

    pr_number = app_state._parse_pr_number(url)
    if not pr_number:
        return

    # Mark review-queue row dirty too so its CI/review/comment counts
    # get refreshed on the next dirty-only sync cadence. Independent
    # of whether the PR is tied to an Eva task -- a reviewer cares
    # about queue state whether or not they own the work.
    app_state._db.mark_review_pr_dirty(number=pr_number)

    pr_row = app_state._db.find_pr_by_number(pr_number)
    if not pr_row:
        return

    app_state._db.mark_pr_dirty(pr_number)

    pid = pr_row["project"]
    tid = pr_row["task_id"]
    task = app_state._db.get_task(pid, tid)
    if task:
        _update_task_from_notification(pid, tid, task, ntype, message)


def _update_task_from_notification(pid, tid, task, ntype, message):
    """Apply status update to a task based on a notification type."""
    changed = False
    old_status = task.get("status", "not_started")
    status = old_status

    if ntype == "github.ci_activity" and "failed" in message.lower():
        old_notes = task.get("notes", "")
        ci_note = "CI failed: " + message[:80]
        if ci_note not in old_notes:
            task["notes"] = (old_notes + " | " + ci_note).strip(" | ")
            changed = True

    if ntype == "github.review_requested" and status in ("in_progress", "not_started"):
        task["status"] = "in_review"
        changed = True

    if ntype in ("github.state_change", "github.author") and "merged" in message.lower():
        prs = [p for p in task.get("prs", []) if p.get("number")]
        # Only mark done when there's at least one tracked PR AND every one of
        # them is merged. An empty PR list would vacuously satisfy `all()` and
        # wrongly flip the task to done.
        all_merged = bool(prs) and all(p.get("status") == "merged" for p in prs)
        if all_merged and status != "done":
            task["status"] = "done"
            changed = True

    if changed:
        app_state.save_task(pid, tid, task)
        # Status-driven transitions get the same auto-history trail as the
        # other five write sites (update_task, close_task, add_pr, merge
        # detection, auto-promote). Notes-only edits skip history by design.
        new_status = task.get("status", old_status)
        if new_status != old_status:
            from common.tasks import _record_status_transition
            _record_status_transition(pid, tid, old_status, new_status)
        print(f"[notif] Updated task {pid}/{tid}: status={task.get('status')}", flush=True)


def _on_gh_pr_status_update(notification):
    """Update PR data in DB based on GitHub events."""
    ntype = notification.get("type", "")
    message = notification.get("message", "")
    url = notification.get("url", "")

    pr_number = pr_number_from_url(url)
    if not pr_number:
        return

    existing = app_state._db.find_pr_by_number(pr_number)
    if not existing:
        return

    changes = {}

    if ntype in ("github.state_change", "github.author"):
        msg_lower = message.lower()
        if "merged" in msg_lower:
            changes["status"] = "merged"
            changes["ci_status"] = "success"
        elif "closed" in msg_lower:
            repo = repo_from_pr_url(url)
            from common.prs import _resolve_pr_status
            changes["status"] = _resolve_pr_status("CLOSED", repo, pr_number)
            if changes["status"] == "merged":
                changes["ci_status"] = "success"
        elif "reopened" in msg_lower:
            changes["status"] = "open"

    if ntype == "github.ci_activity":
        msg_lower = message.lower()
        if "failed" in msg_lower or "failure" in msg_lower:
            changes["ci_status"] = "failure"
        elif "succeeded" in msg_lower or "success" in msg_lower or "passed" in msg_lower:
            changes["ci_status"] = "success"
        elif "pending" in msg_lower or "queued" in msg_lower:
            changes["ci_status"] = "pending"

    if ntype == "github.review_requested":
        if not existing.get("review_status"):
            changes["review_status"] = "review_requested"

    if ntype == "github.comment":
        changes["comment_count"] = (existing.get("comment_count") or 0) + 1

    if changes:
        app_state._db.update_pr_by_number(pr_number, **changes)
        print(f"[pr-sync] PR #{pr_number}: {changes}", flush=True)


# -- Init: seed state + register event handlers (NO thread) --

# Tracks whether `init()` has registered its listeners on the global
# event bus. Without this guard, repeat invocations (server restart in
# the same process; pytest fixtures that exercise the init path)
# would accumulate duplicate `github.*` subscriptions, each writing
# to the prs / tasks tables for every notification. The leak fired in
# tests: a test calling `init()` left two listeners behind on
# `app_state._event_listeners`, and any subsequent emit_event for a
# `github.*` type then fanned out to listener daemons that read
# `app_state._db` -- which by default points to the production
# data/eva.db before `patched_server` swaps it.
_listeners_registered = False


def init() -> None:
    """Seed polling state from the event DB and wire up event handlers.

    Must be called once at server startup (before the scheduler fires
    the first `poll_github_once`). Idempotent: seed state is recomputed
    each call (harmless), but listener registration is guarded so
    repeat calls don't double-fire updates per notification."""
    global _listeners_registered
    _gh_last_poll["seen_ids"] = _load_seen_ids()
    initial_since = _load_since_watermarks()
    if initial_since:
        poll_repos = [r for r in app_state.ALLOWED_REPOS if not r.endswith("/*")]
        for org in app_state.ALLOWED_ORGS:
            if not any(r.startswith(org + "/") for r in poll_repos):
                poll_repos.append(f"{org}/main")
        _gh_last_poll["since"] = {r: initial_since for r in poll_repos}

    if _listeners_registered:
        return
    app_state.on_event("github.*", _on_gh_notification)
    app_state.on_event("github.*", _on_gh_pr_status_update)
    _listeners_registered = True
