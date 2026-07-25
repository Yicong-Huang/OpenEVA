"""Session management routes: launch, hooks, status."""

import asyncio
from adapters.tmux import (
    session_exists,
    capture_output,
    send_keys,
    launch_session,
    launch_session_argv,
    graceful_kill_session,
    line_is_prompt,
)
import time as _time
from datetime import datetime

from fastapi import HTTPException
from pydantic import BaseModel
from typing import Optional

import pty_manager as _pty
import app_state

# Dict-like handle over `session_state`. The session-state cache
# is rebuilt from tmux at startup and never persists to disk, so this
# proxy is the single read/write surface for routes + tests; a write
# here both updates the cache and emits the right event for the
# frontend.
class _SessionStatesProxy:
    def get(self, name, default=None):
        from common import session_state
        row = session_state.get(name)
        if row is None:
            return default if default is not None else {}
        return row

    def pop(self, name, _default=None):
        # Honour `pop` by flipping to 'stopped' (which emits the right
        # event for the frontend) rather than silently deleting.
        from common import session_state
        if session_state.get(name):
            session_state.set_state(name, state="stopped", detail="cleared")

    def __setitem__(self, name, row):
        from common import session_state
        session_state.set_state(
            name,
            state=row.get("state", "unknown"),
            detail=row.get("detail", ""),
        )


_session_states = _SessionStatesProxy()


def live_state(session_name: str) -> str:
    """Return the freshest known state for `session_name` from the
    `session_state` cache, or "" if not in the cache."""
    from common import session_state
    row = session_state.get(session_name)
    return row["state"] if row else ""


@app_state.app.get("/api/projects/{project_id}/sessions")
def list_project_sessions(project_id: str):
    """List all sessions for a project with running status."""
    sessions = app_state._db.list_sessions(project=project_id)
    result = []
    for s in sessions:
        result.append({
            **s,
            "running": session_exists(s["tmux_name"]),
        })
    return {"sessions": result}


@app_state.app.get("/api/project-managers")
def list_project_managers():
    """List live project-manager sessions for the Live Tasks page."""
    from common.sessions import list_project_manager_sessions as _list
    return {"sessions": _list(live_only=True)}


# build_background moved to core/common.sessions.py
from common.sessions import build_background  # noqa: F401


# ---------------------------------------------------------------------------
# Project-manager session endpoints (one long-lived agent per project)
# ---------------------------------------------------------------------------

class ProjectSessionRun(BaseModel):
    prompt: str


@app_state.app.post("/api/projects/{project_id}/manager")
def open_project_manager(project_id: str):
    """Open or resume the project-manager agent session. Idempotent."""
    from common.sessions import open_project_session as _open
    try:
        return _open(project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app_state.app.get("/api/projects/{project_id}/manager")
def get_project_manager(project_id: str):
    """Return current state of the project-manager session, or 404."""
    from common.sessions import get_project_session as _get
    info = _get(project_id)
    if not info:
        raise HTTPException(status_code=404, detail="No project session")
    return info


@app_state.app.delete("/api/projects/{project_id}/manager")
def kill_project_manager(project_id: str):
    """Kill the project-manager session and forget the row."""
    from common.sessions import kill_project_session as _kill
    return _kill(project_id)


@app_state.app.post("/api/projects/{project_id}/manager/run")
def run_project_manager_action(project_id: str, body: ProjectSessionRun):
    """Send a prompt (action) to the running project session via tmux.
    Auto-opens the session if it's not running yet, so action buttons in
    the UI can be one-shot regardless of session state."""
    from common.sessions import open_project_session as _open
    info = _open(project_id)
    tmux_name = info["tmux_name"]
    # `ran` reflects what actually happened: we only send-keys when the
    # session is live AND the caller provided a non-empty prompt. Returning
    # True on an empty prompt or a dead session would mislead the UI into
    # thinking the action ran when it didn't.
    ran = bool(info.get("running") and body.prompt)
    if ran:
        send_keys(tmux_name, body.prompt)
    return {"ok": True, "tmux_name": tmux_name, "ran": ran}


@app_state.app.get("/api/actions")
def list_actions(context: str = ""):
    """List available action definitions, optionally filtered by context."""
    actions = app_state._db.list_actions(context=context)
    return {"actions": actions}


class KillByStatus(BaseModel):
    statuses: list[str] = []


class SessionOpen(BaseModel):
    # `kind` selects which session builder runs. Defaults to "task"
    # for back-compat with older callers (TaskCard + tests). "review"
    # dispatches to the review-session builder so frontend
    # `useSessionLauncher` can hit one endpoint regardless of context.
    kind: str = "task"
    # task-context fields
    task_id: Optional[str] = None
    project_id: Optional[str] = None
    pr_number: Optional[int] = None
    pr_repo: Optional[str] = None
    # review-context fields
    review_url: Optional[str] = None
    # common
    action_id: str
    custom_prompt: Optional[str] = None


@app_state.app.post("/api/sessions/open")
def open_session(body: SessionOpen):
    """Open or resume an agent session.

    `kind='task'` (default): launch / resume the task session at
    `task_id` with the action's prompt, plus task background as
    Claude's system prompt.

    `kind='review'`: launch / resume the review session for
    `review_url`, with PR metadata as Claude's system prompt.
    """
    if body.kind == "review":
        if not body.review_url:
            raise HTTPException(
                status_code=422,
                detail="review_url is required when kind='review'",
            )
    else:  # task
        if not body.task_id or not body.project_id:
            raise HTTPException(
                status_code=422,
                detail="task_id and project_id are required when kind='task'",
            )
    try:
        if body.kind == "review":
            from common.reviews import open_review_session as core_open_review
            return core_open_review(
                review_url=body.review_url,
                action_id=body.action_id,
                custom_prompt=body.custom_prompt,
            )
        # default: task session
        from common.sessions import open_session as core_open_session
        return core_open_session(
            task_id=body.task_id,
            project_id=body.project_id,
            action_id=body.action_id,
            custom_prompt=body.custom_prompt,
            pr_number=body.pr_number,
            pr_repo=body.pr_repo,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# -- API: session management (launch/resume agent for a PR) --

class SessionLaunch(BaseModel):
    session_name: str
    working_dir: str = "~"
    # Extra argv tokens passed to the agent CLI verbatim (split on
    # spaces). Field name kept for back-compat with the existing
    # frontend caller; reads naturally as "agent args".
    agent_args: str = ""
    prompt: str = ""
    project_id: str = ""
    action: str = ""


@app_state.app.post("/api/sessions/launch")
def launch_session_route(body: SessionLaunch):
    """Launch a raw tmux + agent session."""
    from common import agent as _agent
    active = _agent.get_active_agent()
    binary = active.binary
    if body.agent_args:
        command = f"{binary} {body.agent_args}"
    else:
        command = f'{binary} -n "{body.session_name}"'
    already_exists = session_exists(body.session_name)
    print(f"[launch] session={body.session_name} exists={already_exists} action={body.action} dir={body.working_dir}", flush=True)
    launch_session(body.session_name, body.working_dir, command)
    return {
        "session": body.session_name,
        "running": True,
    }


@app_state.app.post("/api/sessions/rebuild")
def rebuild_sessions():
    """Rebuild tmux sessions for all DB sessions where tmux is dead."""
    config = app_state.load_config()
    all_sessions = app_state._db.list_sessions()
    rebuilt = []
    skipped = []
    failed = []
    from common import agent as _agent
    from common import sessions as _sessions
    for s in all_sessions:
        session_name = s["tmux_name"]
        if session_exists(session_name):
            skipped.append(session_name)
            continue
        project_id = s["project"]
        working_dir = config.get("projects", {}).get(project_id, {}).get("working_dir", "~")
        # Resume with the agent that launched this session (empty
        # agent_impl -> default agent), not the global active agent -- codex
        # ids and Claude UUIDs aren't interchangeable. Prefer the
        # recorded UUID (cwd-sensitive local resume); fall back to a
        # fresh launch when we never captured one.
        sess_agent = _agent.get_agent_by_id(s.get("agent_impl", "") or "")
        uuid = (s.get("agent_session_id") or "").strip()
        try:
            if uuid:
                transcript_cwd = _sessions._local_transcript_cwd(uuid)
                if transcript_cwd:
                    working_dir = transcript_cwd
                argv = sess_agent.resume_argv(uuid)
            else:
                argv = sess_agent.launch_argv(session_name)
            launch_session_argv(session_name, working_dir, argv)
            rebuilt.append(session_name)
        except Exception as e:
            failed.append({"session": session_name, "error": str(e)})
    return {"rebuilt": rebuilt, "skipped": skipped, "failed": failed}


@app_state.app.post("/api/sessions/kill-by-status")
def kill_sessions_by_status(body: KillByStatus):
    """Kill all sessions matching given statuses (e.g. 'stopped', 'idle').

    Body: `{statuses: ["stopped", "idle", ...]}`. Empty list is a no-op.
    """
    statuses = body.statuses
    if not statuses:
        return {"killed": []}
    from common import session_state
    all_sessions = app_state._db.list_sessions()
    killed = []
    for s in all_sessions:
        session_name = s["tmux_name"]
        running = session_exists(session_name)
        # State comes from the unified cache (single source). DB
        # column lookup is gone -- it was the drift culprit.
        cache_row = session_state.get(session_name) or {}
        if running:
            effective_status = cache_row.get("state") or "idle"
        else:
            effective_status = "stopped"
        if effective_status not in statuses:
            continue
        if running:
            graceful_kill_session(session_name)
        _pty.remove(session_name)
        app_state._db.delete_session(session_name)
        # Drop the row from the cache and broadcast 'stopped'.
        session_state.set_state(session_name, state="stopped",
                                 detail="killed by kill-by-status")
        killed.append(session_name)
        app_state.emit_event("session.killed", {
            "title": f"Session killed: {session_name}",
            "message": s.get("project", ""),
            "severity": "info",
            "session": s.get("project", ""),
        }, persist=False)
    return {"killed": killed}


@app_state.app.delete("/api/sessions/{session_name}")
def kill_session(session_name: str):
    """Kill tmux session and clean up DB record. Emits session.killed
    for UI refresh (the agent's Stop hook can't fire after tmux-kill).

    For `review-*` sessions, we clear `session_name` / `agent_session_id`
    on the review_prs row instead of the sessions table -- reviews don't
    live in `sessions` so delete_session would no-op the cleanup and
    leave the card showing a ghost session pointer.
    """
    from common import session_state
    session_row = app_state._db.get_session(session_name)
    project = session_row.get("project", "") if session_row else ""
    if session_exists(session_name):
        graceful_kill_session(session_name)
    _pty.remove(session_name)
    # Always update the unified state cache (single source). The
    # session row stays as 'stopped' until the next eviction sweep
    # so the user can see the historical state in the UI.
    session_state.set_state(session_name, state="stopped",
                             detail="killed via DELETE")
    if session_name.startswith("review-"):
        review_row = _find_review_by_session(session_name)
        if review_row:
            app_state._db.upsert_review_pr(
                url=review_row["url"],
                repo=review_row["repo"],
                number=review_row["number"],
                session_name="",
                agent_session_id="",
            )
            try:
                app_state._db.append_review_history(
                    review_row["url"], "session killed",
                    source="system",
                )
            except ValueError:
                pass
            app_state.emit_event("review.session.killed", {
                "title": f"Review session killed: {session_name}",
                "message": review_row["url"],
                "severity": "info",
                "session": session_name,
            }, persist=False)
            return {"status": "killed", "session": session_name}
    app_state._db.delete_session(session_name)
    # Broadcast `session.killed` so frontend views that aren't subscribed
    # to per-state patches (e.g. SessionsPage / All Live Tasks) know to
    # refetch their `/api/all-sessions` cache and drop the now-deleted
    # row. Mirrors what `kill_sessions_by_status` already does.
    app_state.emit_event("session.killed", {
        "title": f"Session killed: {session_name}",
        "message": project,
        "severity": "info",
        "session": session_name,
    }, persist=False)
    return {"status": "killed", "session": session_name}


@app_state.app.post("/api/sessions/{session_name}/resume")
def resume_session_route(session_name: str):
    """Re-launch tmux and resume the existing agent conversation.

    Idempotent: returns noop if tmux is already running. Raises 404 if
    there's no DB row for this session.
    """
    from common.sessions import resume_session as _resume
    try:
        return _resume(session_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app_state.app.post("/api/sessions/{session_name}/restart")
def restart_session_route(session_name: str):
    """Kill the live tmux and resume the SAME agent session by UUID.

    For picking up an agent-binary or config update without losing the
    conversation. Keeps the DB row (and agent_session_id) across the
    restart. Raises 404 if there's no DB row for this session.
    """
    from common.sessions import restart_session as _restart
    try:
        return _restart(session_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# -- Hook-driven session state --

# Hook event -> (new_state, default_detail, emitted event name, title prefix,
#                severity, message-from-data callable)
# Each rule describes how `receive_hook` should react to one agent hook event.
# The Notification event is resolved via ntype first (see _resolve_hook_rule).
_HOOK_RULES = {
    "Stop": (
        "idle", "Finished responding", "agent.task_done", "Agent done", "info",
        lambda data: "Finished responding",
    ),
    "UserPromptSubmit": (
        "thinking", "Processing...", "agent.prompt_submit", "Prompt sent", "info",
        lambda data: (data.get("prompt", "") or "")[:100],
    ),
    "SessionStart": (
        "starting", "Session starting", "agent.session_start", "Session started", "info",
        lambda data: data.get("cwd", ""),
    ),
    "Notification:idle_prompt": (
        # Mapped to `idle` (not a distinct `needs_input`) on purpose:
        # from the user's POV "agent just finished" and "agent sitting
        # at prompt for a while" are the same situation -- "waiting
        # for me". Keeping them as one state avoids a stale-agent
        # session getting categorized as "Needs Attention" alongside
        # genuinely-blocked sessions (permission prompts, crashes).
        "idle", "Waiting for input", "agent.needs_input",
        "Agent waiting for input", "info",
        lambda data: "Waiting for input",
    ),
    "Notification:permission_prompt": (
        "needs_permission", None, "agent.needs_permission",
        "Permission needed", "warning",
        lambda data: data.get("message", "Waiting for approval"),
    ),
}


def _resolve_hook_rule(event: str, ntype: str):
    """Pick the matching rule for a hook event + notification_type pair."""
    if event == "Notification":
        return _HOOK_RULES.get(f"Notification:{ntype}")
    return _HOOK_RULES.get(event)


def _apply_cron_session_hook(session: str, event: str, detail: str) -> bool:
    """When a cron-* session fires the Stop hook, the agent has gone
    idle. Stamp the in-flight run terminal so the run's `finished_at`
    reflects "agent done", not "tick fired" -- the bookkeeping that
    makes long-running cron jobs show their actual duration.

    Cron sessions follow `cron-<slug>-<id>` (see
    `common.cron_jobs.session_name_for_job`); the slug is decorative
    and only the trailing id is parsed back out, so we delegate the
    name match to `_job_id_from_session` rather than prefix-checking.

    Returns True when the session was a cron job (so the dispatcher
    knows to stop here) regardless of whether the finish_run call
    succeeded; subsequent branches don't apply to cron sessions."""
    if event != "Stop":
        return False
    from common import cron_jobs as _cron
    if _cron._job_id_from_session(session) is None:
        return False
    try:
        _cron.finish_run_for_session(session, output=detail or "")
    except Exception as exc:
        print(f"[hook] cron finish_run failed for {session}: {exc}",
              flush=True)
    return True


def _apply_review_session_hook(session: str, event: str, new_state: str,
                                data: dict) -> bool:
    """Mirror the hook fields onto the review_prs row when the session
    is a review-* one. Returns True when the session matched the
    review-* prefix (dispatcher should stop here)."""
    if not session.startswith("review-"):
        return False
    review_row = _find_review_by_session(session)
    if not review_row:
        return True
    fields: dict = {"my_workflow_state": "active"}
    # Capture the agent's session UUID on SessionStart so a later
    # resume can survive tmux death.
    if event == "SessionStart":
        sid = data.get("session_id") or ""
        if sid:
            fields["agent_session_id"] = sid
    app_state._db.upsert_review_pr(
        url=review_row["url"],
        repo=review_row["repo"],
        number=review_row["number"],
        **fields,
    )
    try:
        app_state._db.append_review_history(
            review_row["url"],
            f"hook {event}: {new_state}"[:100],
            source="agent",
        )
    except ValueError:
        pass
    return True


def _apply_ticket_session_hook(session: str, event: str, _new_state: str,
                               data: dict) -> bool:
    """Persist the agent UUID for ticket sessions.

    Ticket sessions are stored in the `sessions` table for recovery,
    but they are not project tasks. Handling the prefix explicitly
    keeps SessionStart persistence while preventing ticket rows from
    falling through into the generic task path as ordinary work items.
    """
    if not session.startswith("ticket-"):
        return False
    if event == "SessionStart":
        sid = data.get("session_id") or ""
        if sid and app_state._db.get_session(session):
            app_state._db.update_session(session, agent_session_id=sid)
    return True


def _apply_task_session_hook(session: str, event: str, _new_state: str,
                              data: dict) -> bool:
    """Persist the agent's session UUID for a regular task session so a
    later resume can survive tmux death. Returns True when a row matched.

    The session's *state* is no longer written here -- the
    `session_state` cache is the single source of truth. We
    still update the row's `agent_session_id` because that's a
    relational fact (which agent UUID does this row resume to),
    not a state field.
    """
    if not app_state._db.get_session(session):
        return False
    if event == "SessionStart":
        sid = data.get("session_id") or ""
        if sid:
            app_state._db.update_session(session, agent_session_id=sid)
    return True


@app_state.app.post("/api/hook")
def receive_hook(request_body: dict):
    """Receive events from the agent hook script.

    Three independent session families dispatch in priority order:
    cron-job-* (closes the run), review-* (mirrors onto review_prs),
    and task sessions (updates the sessions table). Each helper
    returns True when it handled the session so the dispatcher can
    stop -- prevents a cron Stop hook from also leaking into the
    review path, etc."""
    session = request_body.get("session", "")
    event = request_body.get("event", "")
    data = request_body.get("data", {})
    if not session:
        return {"ok": False}

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    ntype = data.get("notification_type", "")
    print(f"[hook] session={session} event={event} ntype={ntype}", flush=True)

    rule = _resolve_hook_rule(event, ntype)
    if rule is None:
        return {"ok": True}

    new_state, default_detail, emit_type, title_prefix, severity, msg_fn = rule
    message = msg_fn(data)
    # When default_detail is None, message doubles as detail (permission prompt case).
    detail = default_detail if default_detail is not None else message

    # Capture the agent's session UUID on SessionStart -- needed for resume.
    sid = data.get("session_id", "") if event == "SessionStart" else ""

    # Update the single source of truth (session_state map).
    # This emits `session.state` on the bus, which the frontend
    # consumes via SessionStatusProvider.
    from common import session_state
    session_state.set_state(
        session,
        state=new_state, detail=detail,
        agent_session_id=sid or "",
    )

    # Emit a notification-bus event for non-state listeners (toast
    # notifications, badge counter, navigation triggers in EventStatus /
    # App). This is a NOTIFICATION event, not a state mirror -- consumers
    # that want state read it from the cache.
    app_state.emit_event(emit_type, {
        "title": f"{title_prefix}: {session}",
        "message": message,
        "severity": severity,
        "session": session,
    })

    # Per-kind side effects (close cron run, mirror review workflow,
    # persist task agent_session_id). State writes are gone -- only
    # relational metadata + business-side bookkeeping remain.
    if _apply_cron_session_hook(session, event, detail):
        return {"ok": True}
    if _apply_review_session_hook(session, event, new_state, data):
        return {"ok": True}
    if _apply_ticket_session_hook(session, event, new_state, data):
        return {"ok": True}
    _apply_task_session_hook(session, event, new_state, data)
    return {"ok": True}


def _find_review_by_session(session_name: str) -> dict | None:
    """Look up the review_prs row whose `session_name` matches. O(n) over
    active reviews; acceptable because the list is tiny (tens at most)
    and we hit this only on hook fires."""
    for row in app_state._db.list_review_prs():
        if (row.get("session_name") or "") == session_name:
            return row
    return None


@app_state.app.get("/api/sessions/{session_name}/wait-ready")
async def wait_for_ready(session_name: str, timeout: int = 30):
    """Wait until an agent session becomes idle.

    The caller's `timeout` is capped at 60 seconds to avoid holding an
    HTTP connection open indefinitely. A client that wants to wait longer
    should poll this endpoint in a loop.

    Returns {"ready": bool, "state": "idle" | "timeout"}.
    """
    from common import session_state
    row = session_state.get(session_name) or {}
    if row.get("state") == "idle":
        print(f"[wait-ready] {session_name}: idle via cache", flush=True)
        return {"ready": True, "state": "idle"}

    print(f"[wait-ready] {session_name}: polling up to {timeout}s...", flush=True)
    deadline = _time.time() + min(timeout, 60)
    while _time.time() < deadline:
        row = session_state.get(session_name) or {}
        if row.get("state") == "idle":
            print(f"[wait-ready] {session_name}: idle via cache", flush=True)
            return {"ready": True, "state": "idle"}

        if session_exists(session_name):
            lines = capture_output(session_name, 5)
            all_lines = lines.split("\n")
            for line in all_lines:
                stripped = line.strip()
                # Match either agent family's input-prompt arrow (Claude's
                # `\u276f`, Codex's `\u203a`), each of which may be followed
                # by content separated by a normal space or a non-breaking
                # space (U+00A0). `line_is_prompt` centralizes both.
                if line_is_prompt(stripped):
                    print(f"[wait-ready] {session_name}: idle via tmux prompt detection", flush=True)
                    return {"ready": True, "state": "idle"}
                if "? for shortcuts" in stripped:
                    print(f"[wait-ready] {session_name}: idle via tmux shortcuts hint", flush=True)
                    return {"ready": True, "state": "idle"}

        await asyncio.sleep(1)

    print(f"[wait-ready] {session_name}: timeout", flush=True)
    return {"ready": False, "state": "timeout"}


def _parse_session_state(tmux_output: str):
    """Classify an Agent session state by scanning its recent tmux output.

    Returns (state, detail). Pure function -- no globals, no side effects --
    so it can be unit-tested against fixture strings without a real tmux.

    Recognised states:
      * "needs_permission" -- waiting for a yes/no prompt
      * "idle"             -- waiting for user input (prompt is visible)
      * "thinking"         -- running a tool / producing output
      * "unknown"          -- nothing recognisable in the buffer
    """
    all_lines = tmux_output.split("\n")
    all_text = tmux_output.lower()
    # Match either agent family's prompt arrow (Claude `\u276f`, Codex
    # `\u203a`), standalone or followed by whitespace. `line_is_prompt`
    # is the shared detector used by wait_until_ready + the wait-ready
    # route so all three stay consistent across agent TUIs.
    has_prompt = any(line_is_prompt(l.strip()) for l in all_lines)
    needs_permission = "esc to cancel" in all_text and "1. yes" in all_text

    if has_prompt:
        if needs_permission:
            return "needs_permission", "Waiting for approval"
        return "idle", "Waiting for input"
    if needs_permission:
        return "needs_permission", "Waiting for approval"

    for line in reversed(all_lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("*") and "..." in stripped:
            return "thinking", stripped.lstrip("* ").rstrip(".")
        if "? for shortcuts" in stripped:
            return "idle", "Waiting for input"
    return "unknown", ""


@app_state.app.get("/api/sessions/{session_name}/status")
def get_session_status(session_name: str):
    """Read state from the unified `session_state` cache.

    The cache is the single source of truth (rebuilt from tmux on
    startup, patched by agent hooks during operation, reaped
    periodically for sessions that died externally). Earlier code
    here had a "30-second freshness" check + tmux-parse fallback;
    that logic is gone because the cache is now authoritative -- if
    it doesn't have the row, the session genuinely doesn't exist
    from the backend's point of view.
    """
    from common import session_state
    if not session_exists(session_name):
        # Sync the cache to ground truth: emit `state=stopped` so any
        # SSE listener gets the correct view, then return.
        if session_state.get(session_name):
            session_state.set_state(
                session_name, state="stopped",
                detail="status endpoint: tmux gone",
            )
        return {"session": session_name, "state": "stopped"}

    row = session_state.get(session_name)
    if row:
        return {
            "session": session_name,
            "state": row["state"],
            "detail": row.get("detail", ""),
        }

    # Tmux is alive but cache has nothing -- do a one-shot pane parse
    # and seed the cache so subsequent reads (and SSE listeners) are
    # consistent. Don't massage 'unknown' here; the parser's verdict
    # is the truth, and seeding 'unknown' lets the next hook overwrite.
    state, detail = _parse_session_state(capture_output(session_name, 8))
    if state != "unknown":
        # Only seed the cache when we have a real classification --
        # planting 'unknown' rows would clutter the snapshot replay
        # and confuse the indicator counts.
        session_state.set_state(session_name, state=state, detail=detail)
    return {"session": session_name, "state": state, "detail": detail}


# `GET /api/sessions` was removed. It used to list sessions referenced
# by PRs via `common.prs.session`, but that column is 0/238 in practice
# (column deprecated long ago). The live UI uses `GET /api/all-sessions`
# which reads from the `sessions` table directly.


# `GET /api/sessions/snapshot` removed: the SSE stream now seeds
# the frontend cache by replaying every session.state row at
# (re)connect time. See routes/events.py:event_stream and
# core/session_state.py:rebuild_from_tmux. There is no separate
# pull endpoint for state any more -- the cache is the only source.
