"""
Session state cache -- the backend's single source of truth for
"what is each tmux+agent session doing right now".

Architecture:
    tmux daemon (ground truth)
            |
            v
    _states  (this module's in-memory map)
            |
            v
    SSE event bus -> frontend's mirror cache

The map is rebuilt from tmux on server startup, updated by agent
hooks during normal operation, and reaped periodically to detect
sessions that died externally (no hook fired). DB is NOT the source
of truth -- the previous `sessions.status` column had drifted from
reality too many times. Any persisted row gets overwritten next time
the truth is queried.

State enum (6 values):
    starting | thinking | idle | needs_permission | stopped | unknown

Frontend is fed by SSE: every state change emits one `session.state`
event with the full row. On (re)connect, the events stream replays
the entire map row-by-row so the frontend can rebuild its mirror
without a separate snapshot endpoint.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional

import app_state


# tmux_name -> {tmux_name, kind, state, detail, ts, agent_session_id,
#               project_id, target_id, target_instance}
_states: dict[str, dict] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def all_states() -> dict[str, dict]:
    """Return a shallow copy of the entire map -- safe to iterate
    without holding the lock."""
    with _lock:
        return {k: dict(v) for k, v in _states.items()}


def get(name: str) -> Optional[dict]:
    """Return the row for `name`, or None if no entry."""
    with _lock:
        row = _states.get(name)
        return dict(row) if row else None


def set_state(
    name: str,
    *,
    state: str,
    detail: str = "",
    kind: str = "",
    agent_session_id: str = "",
    project_id: str = "",
    target_id: str = "",
    target_instance: str = "",
    quiet: bool = False,
) -> dict:
    """Update the cache + emit `session.state` event.

    On a new session, callers MUST supply `kind` and (where applicable)
    `target_id` / `project_id` / `target_instance`. On an update of an
    existing session, those parameters can be omitted -- the previous
    row's values are preserved.

    `quiet=True` skips the event emit. Used by the startup rebuild
    where there are no SSE subscribers yet (and the next connect
    will replay the full map anyway).
    """
    now = _now_iso()
    with _lock:
        existing = _states.get(name, {})
        row = {
            "tmux_name": name,
            "kind": kind or existing.get("kind") or _infer_kind(name),
            "state": state,
            "detail": detail,
            "ts": now,
            "agent_session_id": agent_session_id
                or existing.get("agent_session_id", ""),
            "project_id": project_id or existing.get("project_id", ""),
            "target_id": target_id or existing.get("target_id", ""),
            "target_instance": target_instance
                or existing.get("target_instance", ""),
        }
        _states[name] = row

    if not quiet:
        # Emit OUTSIDE the lock so listeners can't deadlock us.
        # persist=False because the events.db isn't the source of
        # truth for state -- the cache is. Replaying historical
        # session.state rows on reconnect would just resurrect stale
        # snapshots; we replay the LIVE map instead.
        app_state.emit_event(
            "session.state",
            {
                **row,
                "session": name,
                "title": f"session.{state}: {name}",
                "message": detail,
            },
            persist=False,
        )
    return dict(row)


def remove(name: str) -> None:
    """Drop the entry from the cache entirely.

    Use sparingly -- usually you want `set_state(state='stopped')`
    instead so the row stays visible until the user explicitly
    dismisses or until the next eviction sweep. `remove` is for the
    handful of cases (e.g. `eva-cli` deletes a task that owned this
    session) where the row should disappear.
    """
    with _lock:
        existed = _states.pop(name, None)
    if existed is not None:
        app_state.emit_event(
            "session.removed",
            {
                "session": name,
                "title": f"session.removed: {name}",
                "message": "",
            },
            persist=False,
        )


# ---------------------------------------------------------------------------
# Lifecycle: rebuild + reap
# ---------------------------------------------------------------------------

def rebuild_from_tmux() -> None:
    """Called on server startup. Lists every tmux session, parses
    each one's pane to recover its state, and seeds the cache.

    No event emit -- this runs before any SSE subscriber connects,
    and the connect handler replays the entire map anyway. Safe to
    call multiple times (idempotent).
    """
    from adapters import tmux as _tmux
    try:
        names = _tmux.list_sessions()
    except Exception:
        names = []

    with _lock:
        _states.clear()

    for name in names:
        kind = _infer_kind(name)
        state, detail = _state_from_tmux_pane(name)
        meta = _resolve_target_metadata(name, kind)
        set_state(
            name,
            state=state, detail=detail,
            kind=kind,
            **meta,
            quiet=True,
        )


def recover_crashed_sessions() -> dict:
    """Walk DB rows whose tmux pane is gone but whose `agent_session_id`
    is still on record, and try to bring them back via
    `sessions.resume_session`. Run AFTER `rebuild_from_tmux` on
    server startup so the cache is already seeded with whatever's
    actually alive.

    Returns a summary `{resumed: [...], crashed: [...], skipped: [...]}`.
    """
    from adapters import tmux as _tmux
    out = {"resumed": [], "crashed": [], "skipped": []}

    candidates: list[tuple[str, str, str]] = []  # (name, kind, uuid)

    # User sessions: rows in `sessions` table with agent_session_id.
    # Regular project tasks and ticket sessions both live here; infer
    # the kind from tmux_name so ticket-* rows resume through the
    # ticket-aware metadata path instead of being treated as project
    # tasks.
    try:
        for s in app_state._db.list_sessions():
            uuid = (s.get("agent_session_id") or "").strip()
            if not uuid:
                continue
            name = s["tmux_name"]
            try:
                if _tmux.session_exists(name):
                    continue
            except Exception:
                continue
            candidates.append((name, _infer_kind(name), uuid))
    except Exception as e:
        print(f"[session_state] recover task scan failed: {e}", flush=True)

    # Review sessions: rows on `review_prs` with both `session_name` and
    # `agent_session_id` set (the user started a review session, then
    # tmux died).
    try:
        for r in app_state._db.list_review_prs():
            name = (r.get("session_name") or "").strip()
            uuid = (r.get("agent_session_id") or "").strip()
            if not name or not uuid:
                continue
            try:
                if _tmux.session_exists(name):
                    continue
            except Exception:
                continue
            candidates.append((name, "review", uuid))
    except Exception as e:
        print(f"[session_state] recover review scan failed: {e}", flush=True)

    if not candidates:
        return out

    # Mark each candidate `crashed` first so the cache reflects
    # reality immediately. Subsequent successful resumes will flip
    # them back to `starting` (via sessions.resume_session ->
    # session.opened path) and eventually `idle` (via SessionStart
    # hook). Failed resumes leave the row at `crashed`.
    for name, kind, uuid in candidates:
        meta = _resolve_target_metadata(name, kind)
        # `meta` already contains agent_session_id from the DB lookup;
        # prefer the candidate's explicit uuid (same value, but clearer
        # intent + avoids "multiple values for keyword argument").
        meta["agent_session_id"] = uuid
        set_state(
            name, state="crashed",
            detail="startup: tmux gone, awaiting resume",
            kind=kind, quiet=True,
            **meta,
        )

    # Lazy import to avoid `session_state -> sessions`
    # cycle on module load.
    from . import sessions as _sessions
    for name, kind, _uuid in candidates:
        try:
            res = _resume_review(name) if kind == "review" \
                  else _sessions.resume_session(name)
            if res and res.get("running") is not False:
                out["resumed"].append(name)
                # state stays 'crashed' until the SessionStart hook
                # fires for the resumed pane; that updates it to
                # 'starting' / 'idle'. We could optimistically flip
                # to 'starting' here but the hook is the truth.
            else:
                out["skipped"].append(name)
        except Exception as e:
            print(f"[session_state] resume {name} failed: {e}", flush=True)
            out["crashed"].append(name)

    print(
        f"[session_state] recovery: resumed={len(out['resumed'])} "
        f"crashed={len(out['crashed'])} skipped={len(out['skipped'])}",
        flush=True,
    )
    return out


def _resume_review(name: str) -> dict | None:
    """Re-launch a review session whose tmux died. Looks up the
    review_prs row by `session_name`, calls the review-specific
    launcher with the persisted agent_session_id."""
    for r in app_state._db.list_review_prs():
        if (r.get("session_name") or "") != name:
            continue
        uuid = (r.get("agent_session_id") or "").strip()
        if not uuid:
            return None
        from adapters.tmux import launch_session_argv
        from . import agent as _agent
        from . import sessions as _sessions
        # Resume with the agent that launched this review session (empty
        # agent_impl -> default agent), not the global active agent. `resume_argv`
        # routes by id shape (local `--resume` UUID vs cloud `resume`
        # id). Local resume is cwd-sensitive, so launch from the dir the
        # session was created in (recovered from the transcript), falling
        # back to `~` for cloud ids / missing transcripts.
        sess_agent = _agent.get_agent_by_id(r.get("agent_impl", "") or "")
        argv = sess_agent.resume_argv(uuid)
        cwd = _sessions._local_transcript_cwd(uuid) or "~"
        try:
            launch_session_argv(name, cwd, argv)
            return {"session": name, "action": "resumed", "running": True}
        except Exception:
            return None
    return None


def reap_dead_sessions() -> None:
    """Periodic job: detect sessions that died externally (machine
    rebooted, `tmux kill-server`, host recovery recovered without socket, ...).

    Two outcomes depending on whether the session is recoverable:

      * has `agent_session_id` in DB (or in our cache row) -> flip to
        'crashed'. Means "we have the claude UUID, you can resume
        the conversation". The startup recovery path will auto-resume
        these on the next Eva boot; live UI exposes a Resume button.
      * no agent_session_id -> flip to 'stopped'. Either an ephemeral
        session (cron) or a row that never finished SessionStart;
        nothing to recover, mark dead.
    """
    from adapters import tmux as _tmux
    for name, row in all_states().items():
        if row["state"] in ("stopped", "crashed"):
            continue
        try:
            alive = _tmux.session_exists(name)
        except Exception:
            continue
        if alive:
            continue
        recoverable = bool(_recover_agent_uuid(name, row))
        new_state = "crashed" if recoverable else "stopped"
        set_state(name, state=new_state, detail="reaper: tmux gone")


def _recover_agent_uuid(name: str, row: dict) -> str:
    """Find the claude UUID for `name` in the cache or DB. Returns
    "" when nothing is on record (so the session is unrecoverable).
    """
    cached = row.get("agent_session_id", "") or ""
    if cached:
        return cached
    # DB rows: project-task sessions live in `sessions`; review
    # sessions stash the UUID on review_prs.
    try:
        s = app_state._db.get_session(name)
        if s and s.get("agent_session_id"):
            return s["agent_session_id"]
    except Exception:
        pass
    if name.startswith("review-"):
        try:
            for r in app_state._db.list_review_prs():
                if (r.get("session_name") or "") == name:
                    return r.get("agent_session_id", "") or ""
        except Exception:
            pass
    return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _infer_kind(name: str) -> str:
    """Best-effort kind inference from a tmux name. Mirrors the
    naming conventions used by cron_jobs.session_name_for_job /
    reviews.session_name_for / tickets.session_name_for_ticket.
    """
    if name.startswith("cron-"):
        return "cron"
    if name.startswith("review-"):
        return "review"
    if name.startswith("ticket-"):
        return "ticket"
    return "task"


def _state_from_tmux_pane(name: str) -> tuple[str, str]:
    """Recover state from the tmux pane content. Used at startup
    when the in-memory cache has been wiped by a restart -- the
    pane's last few lines tell us whether the agent is sitting at a
    prompt (idle) vs producing output (thinking) vs awaiting a
    permission prompt.
    """
    try:
        from routes.sessions import _parse_session_state
        from adapters.tmux import capture_output
        state, detail = _parse_session_state(capture_output(name, 8))
        # `_parse_session_state` returns 'unknown' when it can't
        # classify -- treat that as 'idle' for the rebuild path
        # so the user sees a green dot instead of a question mark.
        if state == "unknown":
            state = "idle"
        return state, detail
    except Exception:
        return "idle", ""


def _resolve_target_metadata(name: str, kind: str) -> dict:
    """Backfill (target_id, project_id, target_instance, agent_session_id)
    by looking up the relevant DB row. The DB still owns these
    relational fields (a task's project, a review's URL, etc.); only
    the *state* column is gone."""
    out = {
        "target_id": "", "project_id": "",
        "target_instance": "", "agent_session_id": "",
    }
    try:
        if kind == "task":
            row = app_state._db.get_session(name) or {}
            out["target_id"] = row.get("task_id", "") or ""
            out["project_id"] = row.get("project", "") or ""
            out["agent_session_id"] = row.get("agent_session_id", "") or ""
        elif kind == "cron":
            # Convention: cron-<slug>-<id>; only the trailing id is
            # the stable target_id (the slug is decorative and would
            # mismatch if the user renamed the job).
            from .cron_jobs import _job_id_from_session
            jid = _job_id_from_session(name)
            if jid is not None:
                out["target_id"] = str(jid)
        elif kind == "review":
            for r in app_state._db.list_review_prs():
                if (r.get("session_name") or "") == name:
                    out["target_id"] = r.get("url", "") or ""
                    out["agent_session_id"] = r.get("agent_session_id", "") or ""
                    break
        elif kind == "ticket":
            from .tickets import session_name_for_ticket
            for t in app_state._db.list_tickets(limit=1000):
                inst = t.get("instance_name", "") or ""
                if session_name_for_ticket(t["key"], instance_name=inst) == name:
                    out["target_id"] = t["key"]
                    out["target_instance"] = inst
                    break
    except Exception:
        pass
    return out
