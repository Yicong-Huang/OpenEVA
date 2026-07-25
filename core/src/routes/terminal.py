"""Terminal proxy routes.

Transport is a single multiplex SSE (`/api/terminals/stream`) per browser
tab carrying frames for every subscribed session. Clients register via
POST `/api/terminals/subscribe` and the server:

- spawns a background reader task per PTY (multiple tabs sharing a session
  don't race on the master FD)
- sends a `tmux capture-pane` replay so refresh no longer loses scrollback
- fans new frames out to each subscribed client via a bounded asyncio.Queue
  with a drop-oldest overflow policy -- one noisy terminal cannot block
  the others indefinitely

POST `/api/terminal/{name}/input` and `/resize` are short-lived requests
per-session and do not consume a long-connection slot.
"""

import asyncio
import base64
import json
import re
import secrets
import subprocess

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import pty_manager as _pty
import app_state
from adapters.tmux import session_exists


# ---------------------------------------------------------------------------
# Per-session input / resize
# ---------------------------------------------------------------------------

@app_state.app.post("/api/terminal/{session_name}/input")
async def terminal_input(session_name: str, request: Request):
    """Send input to terminal. Body is raw text."""
    body = await request.body()
    ps = _pty.get(session_name)
    if not ps or not ps.alive:
        raise HTTPException(status_code=404, detail="No active terminal session")
    ps.write(body)
    return {"ok": True}


@app_state.app.post("/api/terminal/{session_name}/resize")
async def terminal_resize(session_name: str, rows: int = 24, cols: int = 80):
    """Resize terminal."""
    ps = _pty.get(session_name)
    if not ps or not ps.alive:
        raise HTTPException(status_code=404, detail="No active terminal session")
    ps.resize(rows, cols)
    return {"ok": True}


class ScrollBody(BaseModel):
    """Wheel-to-history scroll request. `dir` is 'up' / 'down', `lines`
    is how many lines this wheel tick moves."""
    dir: str = "up"
    lines: int = 3


@app_state.app.post("/api/terminal/{session_name}/scroll")
async def terminal_scroll(session_name: str, body: ScrollBody):
    """Scroll the tmux pane's scrollback (wheel-to-history).

    The browser terminal calls this when xterm itself has nothing left
    to scroll in the wheel direction -- the agent's interactive TUI
    redraws in place so its scrollback lives in tmux, not xterm. Driving
    tmux copy-mode here lets the user page through the agent's full
    history instead of the wheel escaping to the parent pane. The PTY
    reader streams tmux's copy-mode redraw straight back to xterm, so
    the scrolled view shows up in the browser. Best-effort: a missing
    session is a silent no-op (no 404 -- the wheel must never error)."""
    from adapters import tmux as _tmux
    _tmux.scroll_pane(session_name, body.dir, body.lines)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Multiplex SSE transport
# ---------------------------------------------------------------------------

_MAX_LIVE_FRAMES = 512      # per-client bounded queue size
_REPLAY_LINES = 2000        # how many lines of scrollback to replay
_READ_IDLE_SLEEP = 0.02     # seconds to sleep when PTY had no new data
# Per-session ring buffer cap (bytes). Sized to hold ~5-10 minutes
# of typical agent output so brief network blips can resume with
# zero data loss + zero terminal reset. Larger buffers help long
# disconnects but cost RSS for sessions that never reconnect.
_RING_BYTES_CAP = 256 * 1024

# client_id -> {"queue": asyncio.Queue, "subs": set[str]}
_clients: dict[str, dict] = {}

# session_name -> set of subscriber asyncio.Queue
_session_subscribers: dict[str, set] = {}

# session_name -> background reader task
_session_readers: dict[str, asyncio.Task] = {}

# session_name -> {"chunks": list[(seq, bytes)], "size": int, "next_seq": int}
# Each PTY-read emission is appended here. On resubscribe, the client
# can request "give me everything since seq=N"; if the ring still has
# all chunks with seq > N we replay just those (incremental, no
# terminal reset). If the ring rolled past N we fall back to a full
# tmux capture-pane snapshot (with reset).
_session_rings: dict[str, dict] = {}


def _new_client_id() -> str:
    return secrets.token_hex(8)


_TRAILING_SPACES_RE = re.compile(rb"[ \t]+$")

# CSI escapes prepended to every replay frame:
#   \x1b[H  -- cursor home (row 1, col 1)
#   \x1b[2J -- erase entire screen
#   \x1b[3J -- erase scrollback (xterm-specific; harmless elsewhere)
# Together they guarantee replay paints onto a clean xterm buffer, so the
# replay bytes never interleave with whatever cursor/state the xterm had
# accumulated before (especially after EventSource reconnect).
_REPLAY_RESET = b"\x1b[H\x1b[2J\x1b[3J"


def _tmux_capture(session_name: str) -> bytes:
    """Return the last `_REPLAY_LINES` of the tmux pane as raw bytes.

    Uses `-e` to keep ANSI colour codes but deliberately omits `-J`: the
    join-wrapped-lines flag pads trailing spaces out to the source pane's
    width, which looks broken when the browser xterm is narrower than the
    original tmux pane (Claude Code output wraps weirdly). Instead we keep
    each physical display line as its own output line and strip the
    trailing whitespace that tmux still emits to pad each line.

    Prepends a clear-screen-and-home reset so replay paints onto a fresh
    xterm buffer (fixes cursor drift after EventSource reconnect).

    Joins lines with \\r\\n so xterm places them on separate rows rather
    than treating the buffer as one long wrapped line.

    Returns b'' if tmux is missing or the capture fails. Never raises."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-p", "-e",
             "-S", f"-{_REPLAY_LINES}", "-t", session_name],
            capture_output=True, timeout=5,
        )
        raw = r.stdout or b""
        if not raw:
            return b""
        # Strip per-line trailing whitespace and ensure \r\n terminators so
        # xterm treats each line as its own row (without \r, xterm carries
        # the cursor column forward).
        lines = [
            _TRAILING_SPACES_RE.sub(b"", line)
            for line in raw.splitlines()
        ]
        body = b"\r\n".join(lines) + b"\r\n"
        return _REPLAY_RESET + body
    except Exception:
        return b""


def _push_frame(q: asyncio.Queue, frame: dict):
    """Enqueue with drop-oldest overflow so a noisy session can never stall
    another session's stream."""
    try:
        q.put_nowait(frame)
        return
    except asyncio.QueueFull:
        pass
    try:
        q.get_nowait()          # drop oldest
        q.put_nowait(frame)
    except (asyncio.QueueEmpty, asyncio.QueueFull):
        pass


def _ring_for(session_name: str) -> dict:
    """Lazily-created ring buffer for one session."""
    return _session_rings.setdefault(
        session_name, {"chunks": [], "size": 0, "next_seq": 1},
    )


def _ring_append(session_name: str, data: bytes) -> int:
    """Append a PTY read to the session's ring; trim oldest chunks
    until the total payload is <= `_RING_BYTES_CAP`. Returns the
    monotonic seq assigned to this chunk."""
    ring = _ring_for(session_name)
    seq = ring["next_seq"]
    ring["chunks"].append((seq, data))
    ring["size"] += len(data)
    ring["next_seq"] = seq + 1
    while ring["size"] > _RING_BYTES_CAP and ring["chunks"]:
        old_seq, old_data = ring["chunks"].pop(0)
        ring["size"] -= len(old_data)
        del old_seq, old_data
    return seq


def _ring_since(session_name: str, since_seq: int) -> "list[tuple[int, bytes]]":
    """Return chunks strictly newer than `since_seq`, in seq order.
    Empty list when nothing new. None when the gap is too old to
    serve incrementally (caller falls back to a full snapshot)."""
    ring = _session_rings.get(session_name)
    if not ring or not ring["chunks"]:
        # Fresh session OR ring lost everything; can't decide between
        # "nothing new yet" and "gap too old", so be safe and signal
        # the caller to do a full snapshot.
        return None
    oldest_seq = ring["chunks"][0][0]
    if since_seq < oldest_seq - 1:
        # Client is behind the ring; the gap can't be replayed
        # incrementally. Caller should snapshot.
        return None
    return [(s, d) for s, d in ring["chunks"] if s > since_seq]


async def _session_reader_task(session_name: str):
    """Continuously read PTY output and fan it out to all subscribed clients.

    Each chunk gets a monotonic `seq` and lands in the session's ring
    buffer (so reconnects can request a delta instead of a full
    re-paint). The same chunk is also pushed live to current
    subscribers via `_push_frame`.

    Exits when the PTY is dead. On exit the entry is removed from
    `_session_readers` so the next subscribe can re-start it.
    """
    ps = _pty.get(session_name)
    if not ps:
        _session_readers.pop(session_name, None)
        return
    loop = asyncio.get_event_loop()
    try:
        while ps.alive:
            data = await loop.run_in_executor(None, ps.read)
            if not data:
                await asyncio.sleep(_READ_IDLE_SLEEP)
                continue
            seq = _ring_append(session_name, data)
            frame = {
                "name": session_name,
                "data": base64.b64encode(data).decode(),
                "seq": seq,
            }
            for q in list(_session_subscribers.get(session_name, ())):
                _push_frame(q, frame)
    finally:
        _session_readers.pop(session_name, None)


def _ensure_reader(session_name: str):
    """Start a reader task for `session_name` if one isn't already running."""
    task = _session_readers.get(session_name)
    if task and not task.done():
        return
    _pty.get_or_create(session_name)   # make sure PTY exists
    _session_readers[session_name] = asyncio.create_task(
        _session_reader_task(session_name),
    )


def _unsubscribe(client_id: str, name: str):
    """Drop a (client, session) subscription. Safe to call with unknown ids."""
    state = _clients.get(client_id)
    if state:
        state["subs"].discard(name)
        subs = _session_subscribers.get(name)
        if subs and state["queue"] in subs:
            subs.discard(state["queue"])
            if not subs:
                _session_subscribers.pop(name, None)


def _drop_client(client_id: str):
    """Tear down all subscriptions for a client on stream disconnect."""
    state = _clients.pop(client_id, None)
    if not state:
        return
    for name in list(state["subs"]):
        subs = _session_subscribers.get(name)
        if subs:
            subs.discard(state["queue"])
            if not subs:
                _session_subscribers.pop(name, None)


@app_state.app.get("/api/terminals/stream")
async def terminals_stream():
    """Single SSE carrying frames for every session this client subscribed to.

    The first event is a `hello` frame containing the `client_id` the caller
    must use for subsequent subscribe/unsubscribe requests.
    """
    client_id = _new_client_id()
    q: asyncio.Queue = asyncio.Queue(maxsize=_MAX_LIVE_FRAMES)
    _clients[client_id] = {"queue": q, "subs": set()}

    async def gen():
        try:
            yield f"data: {json.dumps({'type': 'hello', 'client_id': client_id})}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _drop_client(client_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class SubscribeBody(BaseModel):
    client_id: str
    name: str
    # Optional: client's last-seen seq for this session. When the ring
    # still has all chunks with seq > since_seq we replay just those
    # (no terminal reset, lossless reconnect). Defaults to 0 ->
    # "I don't know what I've seen, send me everything" (full snapshot).
    since_seq: int = 0


@app_state.app.post("/api/terminals/subscribe")
async def terminals_subscribe(body: SubscribeBody):
    """Subscribe `client_id` to session `name`.

    If the client passes `since_seq` and the ring buffer still holds
    every chunk newer than that seq, we replay only the delta as one
    or more `incremental: True` frames -- the client appends them
    without resetting xterm. Otherwise (cold start, or the ring rolled
    past the gap), we send a full `tmux capture-pane` snapshot tagged
    `replay: True` and the client resets the buffer.
    """
    state = _clients.get(body.client_id)
    if not state:
        raise HTTPException(status_code=404,
                            detail="unknown client_id; reconnect the stream first")
    if not session_exists(body.name):
        raise HTTPException(status_code=404, detail="tmux session not found")

    state["subs"].add(body.name)
    _session_subscribers.setdefault(body.name, set()).add(state["queue"])
    _ensure_reader(body.name)

    delta = _ring_since(body.name, body.since_seq) if body.since_seq else None
    if delta is not None:
        # Lossless path: every chunk the client missed is still in
        # the ring. Push just those (preserving original seqs); no
        # snapshot, no terminal reset.
        for seq, chunk in delta:
            _push_frame(state["queue"], {
                "name": body.name,
                "data": base64.b64encode(chunk).decode(),
                "seq": seq,
                "incremental": True,
            })
        return {"ok": True, "mode": "incremental",
                "chunks": len(delta)}

    # Cold-start / ring-overflow path: full snapshot from tmux.
    history = _tmux_capture(body.name)
    if history:
        # Tag with the current next_seq - 1 so the client's lastSeq
        # advances past everything captured up to now. No subsequent
        # incremental frame will overlap with this snapshot.
        ring = _ring_for(body.name)
        snapshot_seq = ring["next_seq"] - 1
        _push_frame(state["queue"], {
            "name": body.name,
            "data": base64.b64encode(history).decode(),
            "seq": snapshot_seq,
            "replay": True,
        })
    return {"ok": True, "mode": "snapshot"}


@app_state.app.post("/api/terminals/unsubscribe")
async def terminals_unsubscribe(body: SubscribeBody):
    """Detach `client_id` from session `name`. Idempotent."""
    _unsubscribe(body.client_id, body.name)
    return {"ok": True}
