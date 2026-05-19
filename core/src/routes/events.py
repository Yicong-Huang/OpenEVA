"""Event API routes: SSE stream, get events, mark read, poll status."""

import json as _json
# pysqlite3 fallback -- see app_state.py for why mixing engines corrupts the WAL.
try:
    from pysqlite3 import dbapi2 as sqlite3
except ImportError:
    import sqlite3  # type: ignore[no-redef]

from pydantic import BaseModel
from typing import Any, Optional
from fastapi import Request
from fastapi.responses import StreamingResponse

import app_state


# -- Internal: relay events emitted in another process (eva-cli) --
#
# `eva-cli create-task / update-task / open-session / ...` triggers
# `common.tasks._emit_task_event(...)` inside the CLI process. Without a
# relay, that event only writes to the events DB -- the web UI's SSE
# subscribers live in this server's in-memory list and never see it.
# This endpoint takes the event and re-emits it server-side with
# `persist=False` (the CLI already wrote the DB row) so subscribers
# get the push without double-persisting.

class EmitRelayBody(BaseModel):
    type: str
    data: dict[str, Any]
    persist: bool = False


@app_state.app.post("/api/internal/emit-relay")
def emit_relay(body: EmitRelayBody):
    """Relay an event emitted in a sibling process (typically eva-cli)
    onto this server's event bus + SSE subscribers."""
    app_state.emit_event(body.type, body.data, persist=body.persist)
    return {"ok": True}

# These are imported for re-export via server.py so tests can patch
# them as ``server._xxx`` for backward compat. Keep the imports even
# though this module only uses _gh_last_poll directly.
from services.github_poller import (  # noqa: F401
    _gh_last_poll,
    _GH_POLL_INTERVAL,
    _poll_github_notifications,
    _build_gh_events,
    _on_gh_notification,
    _update_task_from_notification,
    _on_gh_pr_status_update,
    _load_seen_ids,
    _lookup_pr_by_branch,
)


# -- Poll status endpoint --

_GH_POLL_JOB_ID = "github_poller"


@app_state.app.get("/api/gh-poll-status")
def gh_poll_status():
    """Check GitHub notification poller health.

    Returns whether the scheduled job is registered and not paused,
    plus the last time a poll ran and how many notification IDs we've
    already seen (so the dashboard can distinguish stuck vs healthy)."""
    from services.scheduler import get_scheduler
    job = get_scheduler().get_job(_GH_POLL_JOB_ID)
    running = bool(job and job.next_run_time is not None)
    return {
        # `thread_alive` kept for dashboard/API back-compat; now reflects
        # whether the scheduled job is armed.
        "thread_alive": running,
        "job_registered": job is not None,
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "last_poll_ts": _gh_last_poll["ts"],
        "seen_ids_count": len(_gh_last_poll["seen_ids"]),
    }


# -- SSE event stream --

@app_state.app.get("/api/events/stream")
async def event_stream(request: Request):
    """SSE endpoint: pushes all events from the event bus in real-time.

    Supports `Last-Event-ID` resume. EventSource auto-sets this header
    on reconnect; we use the events table's monotonic rowid to replay
    every event the client missed while its TCP socket was down. This
    turns Eva-restart / flaky-wifi from "silently drop events" into
    "blink and everything catches up".

    `retry: 3000` at the head of the stream tells the browser to
    reconnect in 3s on drop -- the EventSource default is 3s already
    but being explicit lets us tune it later.
    """
    import asyncio as _aio

    last_id = request.headers.get("last-event-id", "") or ""

    q = _aio.Queue(maxsize=100)
    app_state._event_subscribers.append(q)

    async def generate():
        try:
            # Retry hint + connected preamble in one frame.
            yield "retry: 3000\n: connected\n\n"

            # Session-state seed: replay every row in the backend's
            # in-memory cache as a separate `session.state` event so
            # the frontend can rebuild its mirror from scratch every
            # time it (re)connects. The `session.snapshot.begin/end`
            # pair lets the frontend buffer + atomic-swap to avoid
            # flicker mid-replay. This entirely replaces the old
            # `/api/sessions/snapshot` GET endpoint.
            from common import session_state
            from datetime import datetime
            now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            yield _format_sse({
                "type": "session.snapshot.begin",
                "ts": now, "title": "snapshot.begin", "message": "",
            })
            from common import session_state as _ssn
            for name, row in _ssn.all_states().items():
                yield _format_sse({
                    "type": "session.state",
                    "session": name,
                    **row,
                    "title": f"session.{row['state']}: {name}",
                    "message": row.get("detail", ""),
                })
            yield _format_sse({
                "type": "session.snapshot.end",
                "ts": now, "title": "snapshot.end", "message": "",
            })

            # Replay anything the client missed since Last-Event-ID.
            # Only persisted events have a rowid -- ephemeral
            # (persist=False) events are unreplayable by definition.
            # session.state is persist=False on purpose: replaying
            # historical state would just resurrect stale snapshots.
            if last_id:
                for ev in _replay_since(last_id):
                    yield _format_sse(ev)

            while True:
                try:
                    event = await _aio.wait_for(q.get(), timeout=30)
                    yield _format_sse(event)
                except _aio.TimeoutError:
                    yield ": keepalive\n\n"
        except _aio.CancelledError:
            pass
        finally:
            try:
                app_state._event_subscribers.remove(q)
            except ValueError:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _format_sse(event: dict) -> str:
    """Render one event dict as an SSE frame, including the `id:` field
    so the browser can reconnect with `Last-Event-ID`."""
    eid = event.get("id") or ""
    # `id:` field must not contain newlines per SSE spec; UUIDs are
    # fine, but guard anyway in case of a custom non-UUID source_id.
    eid = str(eid).replace("\n", "").replace("\r", "")
    prefix = f"id: {eid}\n" if eid else ""
    return prefix + f"data: {_json.dumps(event)}\n\n"


def _replay_since(last_event_id: str) -> "list[dict]":
    """Fetch all events with `rowid > rowid(last_event_id)`, ordered
    oldest-first so replay arrives in emission order. Returns an empty
    list when the last id isn't in the table (client reconnected after
    a pruning event, or the id is garbled).

    Caps at 500 events so a client that's been offline for a week
    doesn't drown the server in one SSE burst.
    """
    import sqlite3 as _sq
    try:
        with _sq.connect(str(app_state._NOTIF_DB_PATH)) as conn:
            conn.row_factory = _sq.Row
            pivot_row = conn.execute(
                "SELECT rowid FROM events WHERE id=? LIMIT 1",
                (last_event_id,),
            ).fetchone()
            if not pivot_row:
                return []
            pivot = pivot_row["rowid"]
            rows = conn.execute(
                "SELECT id, source, source_id, title, message, type, "
                "severity, url, ts, session FROM events "
                "WHERE rowid > ? ORDER BY rowid ASC LIMIT 500",
                (pivot,),
            ).fetchall()
    except _sq.Error:
        return []
    return [dict(r) for r in rows]


# -- Notification API --

@app_state.app.get("/api/events")
def get_notifications(limit: int = 30, unread_only: bool = False):
    """Get system events, newest first."""
    with app_state._notif_db() as conn:
        conn.row_factory = sqlite3.Row
        where = "WHERE read = 0" if unread_only else "WHERE 1=1"
        rows = conn.execute(
            f"SELECT * FROM events {where} ORDER BY ts DESC, rowid DESC LIMIT ?",
            [limit],
        ).fetchall()
        unread = conn.execute("SELECT COUNT(*) FROM events WHERE read = 0").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return {"events": [dict(r) for r in rows], "unread": unread, "total": total}


class MarkReadBody(BaseModel):
    ids: Optional[list] = None
    url: Optional[str] = None
    session: Optional[str] = None


@app_state.app.post("/api/events/read")
def mark_notifications_read(body: MarkReadBody = MarkReadBody()):
    """Mark events as read. Supports: ids (list), url (PR URL), session (name), or all."""
    with app_state._notif_db() as conn:
        if body.ids:
            conn.executemany("UPDATE events SET read = 1 WHERE id = ?", [(i,) for i in body.ids])
        elif body.url:
            conn.execute("UPDATE events SET read = 1 WHERE url = ? AND read = 0", (body.url,))
        elif body.session:
            pattern = f"%: {body.session}"
            conn.execute("UPDATE events SET read = 1 WHERE title LIKE ? AND read = 0", (pattern,))
        else:
            conn.execute("UPDATE events SET read = 1 WHERE read = 0")
    return {"ok": True}
