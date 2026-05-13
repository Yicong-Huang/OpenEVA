"""Tests for the events/notifications API endpoints."""
import sys
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def events_client(tmp_path, monkeypatch):
    """Client with temp DBs. Event DBs are already isolated by autouse fixture."""
    import server
    from eva_db import EvaDB

    # Patch with a single unified EvaDB
    test_db = EvaDB(str(tmp_path / "eva.db"))
    monkeypatch.setattr("server._db", test_db)

    # Need to patch CONFIG_PATH for any endpoint that calls load_config()
    config_path = tmp_path / "config.yaml"
    import yaml
    with open(config_path, "w") as f:
        yaml.dump({"projects": {"test-proj": {"name": "Test"}}}, f)
    monkeypatch.setattr("server.CONFIG_PATH", config_path)

    from starlette.testclient import TestClient
    yield TestClient(server.app)
    test_db.close()


class TestGetEventsEmpty:
    def test_get_events_empty(self, events_client):
        """GET /api/events with empty DB returns empty list."""
        resp = events_client.get("/api/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []
        assert data["unread"] == 0
        assert data["total"] == 0


class TestEmitAndGetEvents:
    def test_emit_and_get_events(self, events_client):
        """emit_event then GET /api/events verifies event appears."""
        import server
        server.emit_event("test.event", {"title": "Test", "message": "hello"})

        resp = events_client.get("/api/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        events = data["events"]
        assert len(events) >= 1
        titles = [e["title"] for e in events]
        assert "Test" in titles
        matching = [e for e in events if e["title"] == "Test"]
        assert matching[0]["message"] == "hello"
        assert matching[0]["source"] == "test"


class TestGetEventsSinceId:
    def test_get_events_since_id(self, events_client):
        """Emit 3 events, GET with since_id=1, verify only events 2 and 3 returned."""
        import server
        server.emit_event("test.one", {"title": "Event One", "message": "first"})
        server.emit_event("test.two", {"title": "Event Two", "message": "second"})
        server.emit_event("test.three", {"title": "Event Three", "message": "third"})

        # Get events with limit
        resp = events_client.get("/api/events?limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 2
        # Newest first
        assert data["events"][0]["title"] == "Event Three"
        assert data["events"][1]["title"] == "Event Two"
        # Each event has a UUID id
        for e in data["events"]:
            assert len(e["id"]) == 36  # UUID format


class TestMarkEventsRead:
    def test_mark_events_read(self, events_client):
        """Emit event, POST /api/events/read, verify read count changes."""
        import server
        server.emit_event("test.readable", {"title": "Read Me", "message": "mark this"})

        # Verify unread before
        resp_before = events_client.get("/api/events")
        assert resp_before.status_code == 200
        assert resp_before.json()["unread"] >= 1

        # Mark all as read
        resp_mark = events_client.post("/api/events/read", json=None)
        assert resp_mark.status_code == 200
        assert resp_mark.json()["ok"] is True

        # Verify unread after
        resp_after = events_client.get("/api/events")
        assert resp_after.status_code == 200
        assert resp_after.json()["unread"] == 0


class TestMarkSpecificEventsRead:
    def test_mark_specific_events_read(self, events_client, monkeypatch):
        """Emit 3 events, mark only first as read via DB, verify others still unread."""
        import server
        import sqlite3
        server.emit_event("test.first", {"title": "First Event", "message": "msg1"})
        server.emit_event("test.second", {"title": "Second Event", "message": "msg2"})
        server.emit_event("test.third", {"title": "Third Event", "message": "msg3"})

        # Get all to find IDs
        resp_all = events_client.get("/api/events")
        assert resp_all.status_code == 200
        all_events = resp_all.json()["events"]
        assert len(all_events) >= 3

        # Mark only the first (min ID) as read directly via DB
        # (The /api/events/read endpoint with a JSON list body does not selectively
        # mark specific IDs via the FastAPI list query param -- mark directly.)
        min_id = min(e["id"] for e in all_events)
        with sqlite3.connect(str(server._NOTIF_DB_PATH)) as conn:
            conn.execute("UPDATE events SET read = 1 WHERE id = ?", (min_id,))

        # Verify that unread count decreased by 1 but is not zero
        resp_after = events_client.get("/api/events")
        assert resp_after.status_code == 200
        data_after = resp_after.json()
        unread_after = data_after["unread"]
        # 2 events remain unread (second and third)
        assert unread_after == 2

        # Confirm the marked event is read, others are not
        marked = [e for e in data_after["events"] if e["id"] == min_id]
        assert len(marked) == 1
        assert marked[0]["read"] == 1
        others = [e for e in data_after["events"] if e["id"] != min_id]
        for ev in others:
            assert ev["read"] == 0


class TestMarkReadByUrl:
    def test_mark_read_by_url(self, events_client):
        """Emit 2 github events with same URL + 1 with different URL, mark by URL."""
        import server

        target_url = "https://github.com/example/repo/pull/55332"
        other_url = "https://github.com/example/repo/pull/99999"

        # Emit 2 events sharing the same URL (use unique source_ids to avoid dedup)
        server.emit_event("github.comment", {
            "title": "New comment - repo #55332",
            "message": "looks good",
            "url": target_url,
            "source_id": "mark-url-1",
        })
        server.emit_event("github.review_requested", {
            "title": "Review requested - repo #55332",
            "message": "please review",
            "url": target_url,
            "source_id": "mark-url-2",
        })
        # 1 event with a different URL
        server.emit_event("github.ci_activity", {
            "title": "CI update - repo #99999",
            "message": "build passed",
            "url": other_url,
            "source_id": "mark-url-3",
        })

        # All 3 should be unread
        resp = events_client.get("/api/events")
        assert resp.json()["unread"] == 3

        # Mark by URL -- only the 2 events with target_url should become read
        resp_mark = events_client.post("/api/events/read", json={"url": target_url})
        assert resp_mark.status_code == 200
        assert resp_mark.json()["ok"] is True

        # Verify: 1 unread remaining (the other_url event)
        resp_after = events_client.get("/api/events")
        data = resp_after.json()
        assert data["unread"] == 1

        read_events = [e for e in data["events"] if e["read"] == 1]
        unread_events = [e for e in data["events"] if e["read"] == 0]
        assert len(read_events) == 2
        assert len(unread_events) == 1
        assert all(e["url"] == target_url for e in read_events)
        assert unread_events[0]["url"] == other_url


class TestMarkReadBySession:
    def test_mark_read_by_session(self, events_client):
        """Emit 2 agent events with matching session title, mark by session name."""
        import server

        session_name = "my-task"
        # Events whose title ends with ": my-task" (matches LIKE '%: my-task')
        server.emit_event("agent.task_complete", {
            "title": "coder: my-task",
            "message": "task completed successfully",
        })
        server.emit_event("agent.task_update", {
            "title": "reviewer: my-task",
            "message": "review done",
        })
        # Event with a different session name -- should NOT be marked
        server.emit_event("agent.task_update", {
            "title": "coder: other-task",
            "message": "different task",
        })

        # All 3 unread
        resp = events_client.get("/api/events")
        assert resp.json()["unread"] == 3

        # Mark by session
        resp_mark = events_client.post("/api/events/read", json={"session": session_name})
        assert resp_mark.status_code == 200
        assert resp_mark.json()["ok"] is True

        # Verify: only 1 unread remaining (the "other-task" event)
        resp_after = events_client.get("/api/events")
        data = resp_after.json()
        assert data["unread"] == 1

        read_events = [e for e in data["events"] if e["read"] == 1]
        unread_events = [e for e in data["events"] if e["read"] == 0]
        assert len(read_events) == 2
        assert len(unread_events) == 1
        assert unread_events[0]["title"] == "coder: other-task"


class TestUnreadOnlyFilter:
    """GET /api/events?unread_only=true must exclude already-read events."""

    def test_unread_only_false_returns_all(self, events_client):
        import server
        server.emit_event("test.event", {"title": "a", "message": ""})
        server.emit_event("test.event", {"title": "b", "message": ""})
        # Mark one read.
        events = events_client.get("/api/events").json()["events"]
        first_id = events[-1]["id"]  # oldest-first
        events_client.post("/api/events/read", json={"ids": [first_id]})

        resp = events_client.get("/api/events?unread_only=false")
        data = resp.json()
        assert len(data["events"]) == 2

    def test_unread_only_true_excludes_read(self, events_client):
        import server
        server.emit_event("test.event", {"title": "read-one", "message": ""})
        server.emit_event("test.event", {"title": "keep-me", "message": ""})
        # Read events come in reverse order; pick the one with title "read-one".
        events_all = events_client.get("/api/events").json()["events"]
        read_one_id = next(e["id"] for e in events_all if e["title"] == "read-one")
        events_client.post("/api/events/read", json={"ids": [read_one_id]})

        resp = events_client.get("/api/events?unread_only=true")
        data = resp.json()
        titles = [e["title"] for e in data["events"]]
        assert "keep-me" in titles
        assert "read-one" not in titles


class TestGhPollStatus:
    """/api/gh-poll-status exposes the GitHub poller job's liveness for UI.

    The `thread_alive` key is preserved for dashboard back-compat; it now
    reflects whether the scheduled `github_poller` job is armed and not
    paused."""

    def test_returns_expected_shape(self, events_client):
        resp = events_client.get("/api/gh-poll-status")
        assert resp.status_code == 200
        data = resp.json()
        expected_keys = {
            "thread_alive", "job_registered", "next_run",
            "last_poll_ts", "seen_ids_count",
        }
        assert set(data.keys()) == expected_keys
        assert isinstance(data["thread_alive"], bool)
        assert isinstance(data["job_registered"], bool)
        assert isinstance(data["seen_ids_count"], int)
        # In the test environment the scheduler isn't started, so the job
        # isn't registered and thread_alive must be False -- the dashboard
        # relies on that to surface "poller not running" to the user.
        assert data["job_registered"] is False
        assert data["thread_alive"] is False
        assert data["next_run"] is None


class _FakeRequest:
    """Minimal stand-in for `fastapi.Request` used by the SSE endpoint.
    The real request only needs `.headers.get(...)` for Last-Event-ID
    replay; wiring up a Starlette scope is overkill for unit tests."""

    def __init__(self, headers: "dict[str, str] | None" = None):
        # Starlette headers are case-insensitive and indexed lowercase.
        self.headers = {(k or "").lower(): v for k, v in (headers or {}).items()}


class TestEventsSseStream:
    """/api/events/stream: SSE endpoint that pushes event bus events.

    These tests drive the async endpoint directly so the 30s keepalive loop
    cannot hang the test worker.
    """

    def _drain_one_chunk(self, app):
        """Call the endpoint's async generator directly and return its first
        yielded chunk. Avoids the blocking nature of TestClient.stream over
        an endpoint whose generator never naturally terminates."""
        import asyncio
        import server
        # Locate the endpoint function by route.
        endpoint = None
        for route in server.app.routes:
            path = getattr(route, "path", "")
            if path == "/api/events/stream":
                endpoint = route.endpoint
                break
        assert endpoint is not None, "event_stream route not registered"

        async def grab_first():
            sr = await endpoint(_FakeRequest())  # StreamingResponse
            agen = sr.body_iterator
            first = await agen.__anext__()
            await agen.aclose()
            return sr, first

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(grab_first())
        finally:
            loop.close()

    def test_sse_headers_and_media_type(self, events_client):
        sr, _ = self._drain_one_chunk(events_client)
        assert sr.media_type == "text/event-stream"
        assert sr.headers.get("cache-control") == "no-cache"
        assert sr.headers.get("x-accel-buffering") == "no"

    def test_connected_preamble_is_first_chunk(self, events_client):
        """First yielded chunk must be ': connected' so clients know the
        link is live before any event fires."""
        _, first = self._drain_one_chunk(events_client)
        if isinstance(first, bytes):
            first = first.decode()
        assert ": connected" in first

    def test_subscriber_registered_then_cleaned_up(self, events_client):
        """The queue used by this SSE connection must be added to the
        subscriber list at open and removed at close."""
        import asyncio
        import server
        before = len(server._event_subscribers)

        # Locate endpoint
        endpoint = None
        for route in server.app.routes:
            if getattr(route, "path", "") == "/api/events/stream":
                endpoint = route.endpoint
                break

        async def open_read_close():
            sr = await endpoint(_FakeRequest())
            # After the generator is primed, one queue should have been added.
            agen = sr.body_iterator
            await agen.__anext__()  # prime: ": connected\n\n"
            during = len(server._event_subscribers)
            await agen.aclose()     # triggers finally: remove queue
            return during

        loop = asyncio.new_event_loop()
        try:
            during = loop.run_until_complete(open_read_close())
        finally:
            loop.close()
        after = len(server._event_subscribers)
        assert during == before + 1
        assert after == before

    def test_enqueued_event_yielded_as_data_frame(self, events_client):
        """An event put on the subscriber queue must be emitted as an SSE
        `data: <json>` frame by the generator loop (covers lines 53-56)."""
        import asyncio
        import json as _json
        import server

        endpoint = None
        for route in server.app.routes:
            if getattr(route, "path", "") == "/api/events/stream":
                endpoint = route.endpoint
                break

        # Reset the session-state cache so the snapshot replay is
        # just begin+end (2 frames) -- otherwise the test has to
        # skip a variable number of session.state rows before
        # finding its own event.
        from common import session_state as _ssn
        with _ssn._lock:
            _ssn._states.clear()

        async def drive():
            sr = await endpoint(_FakeRequest())
            agen = sr.body_iterator
            await agen.__anext__()  # ': connected'
            # Drain the snapshot replay frames (begin, then end on
            # an empty cache).
            for _ in range(2):
                await agen.__anext__()
            # Find the queue we just registered and feed it an event.
            q = server._event_subscribers[-1]
            q.put_nowait({"source": "test", "title": "hello"})
            frame = await agen.__anext__()
            await agen.aclose()
            return frame

        loop = asyncio.new_event_loop()
        try:
            frame = loop.run_until_complete(drive())
        finally:
            loop.close()
        if isinstance(frame, bytes):
            frame = frame.decode()
        assert frame.startswith("data: ")
        import json as _json
        payload = _json.loads(frame[len("data: "):].strip())
        assert payload == {"source": "test", "title": "hello"}

    def test_keepalive_emitted_on_timeout(self, events_client):
        """When no event arrives within the wait_for timeout, the generator
        must emit `: keepalive` rather than hanging forever (covers line 58)."""
        import asyncio
        import server

        endpoint = None
        for route in server.app.routes:
            if getattr(route, "path", "") == "/api/events/stream":
                endpoint = route.endpoint
                break

        # Empty the session-state cache so the snapshot replay is
        # just begin+end (2 frames after `: connected`).
        from common import session_state as _ssn
        with _ssn._lock:
            _ssn._states.clear()

        async def drive():
            sr = await endpoint(_FakeRequest())
            agen = sr.body_iterator
            await agen.__anext__()  # ': connected'
            # Drain snapshot.begin + snapshot.end.
            for _ in range(2):
                await agen.__anext__()

            # Replace wait_for so the next iteration raises TimeoutError
            # immediately rather than blocking 30 seconds.
            async def fast_timeout(coro, timeout):
                try:
                    coro.close()
                except Exception:
                    pass
                raise asyncio.TimeoutError()

            from unittest.mock import patch
            with patch("routes.events._aio", new=asyncio) if False else patch.object(asyncio, "wait_for", side_effect=fast_timeout):
                chunk = await agen.__anext__()
            await agen.aclose()
            return chunk

        loop = asyncio.new_event_loop()
        try:
            chunk = loop.run_until_complete(drive())
        finally:
            loop.close()
        if isinstance(chunk, bytes):
            chunk = chunk.decode()
        assert chunk.startswith(":")
        assert "keepalive" in chunk

    def test_remove_queue_handles_already_removed(self, events_client):
        """Line 64-65: the cleanup `finally` guards against ValueError when
        the queue was already removed (e.g. by another path)."""
        import asyncio
        import server

        endpoint = None
        for route in server.app.routes:
            if getattr(route, "path", "") == "/api/events/stream":
                endpoint = route.endpoint
                break

        async def drive():
            sr = await endpoint(_FakeRequest())
            agen = sr.body_iterator
            await agen.__anext__()  # ': connected'
            # Pre-remove the queue to force the ValueError branch on cleanup.
            q = server._event_subscribers.pop()
            # Must not raise on aclose even though queue isn't in the list.
            await agen.aclose()
            # Put it back so other tests aren't surprised.
            server._event_subscribers.append(q)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(drive())
        finally:
            loop.close()


class TestSseLastEventIdReplay:
    """`/api/events/stream` honours the `Last-Event-ID` header so a
    reconnect doesn't drop events the client missed during the blip.

    Key invariants:
      * Each live frame carries an `id: <uuid>` line.
      * On reconnect with a known Last-Event-ID, events after that
        row are replayed in emission order before the live loop runs.
      * An unknown / malformed Last-Event-ID silently falls back to
        "start live" (no replay, no error).
    """

    def _endpoint(self):
        import server
        for route in server.app.routes:
            if getattr(route, "path", "") == "/api/events/stream":
                return route.endpoint
        raise RuntimeError("event_stream route not registered")

    def _replay(self, events_client, last_id):
        """Drive the endpoint with a Last-Event-ID header, collect
        whatever frames it yields BEFORE the live loop blocks on the
        queue. Returns the decoded frame list."""
        import asyncio

        async def drive():
            sr = await self._endpoint()(_FakeRequest({
                "Last-Event-ID": last_id,
            }))
            agen = sr.body_iterator
            frames = []
            # Preamble first.
            frames.append(await agen.__anext__())
            # Pull replay frames with a very short timeout per chunk --
            # once the generator moves into the live `wait_for` it'll
            # block 30s, which we cut off.
            while True:
                try:
                    frames.append(
                        await asyncio.wait_for(agen.__anext__(), timeout=0.05),
                    )
                except (asyncio.TimeoutError, StopAsyncIteration):
                    break
            await agen.aclose()
            return frames

        loop = asyncio.new_event_loop()
        try:
            raw = loop.run_until_complete(drive())
        finally:
            loop.close()
        return [
            (f.decode() if isinstance(f, bytes) else f)
            for f in raw
        ]

    def test_live_frame_includes_id_line(self, events_client):
        """Every event frame must have an `id:` line so the browser
        remembers it for next reconnect."""
        import asyncio
        import server

        # Empty session-state cache so snapshot replay is just begin+end.
        from common import session_state as _ssn
        with _ssn._lock:
            _ssn._states.clear()

        async def drive():
            sr = await self._endpoint()(_FakeRequest())
            agen = sr.body_iterator
            await agen.__anext__()  # preamble
            for _ in range(2):
                await agen.__anext__()  # snapshot.begin + snapshot.end
            q = server._event_subscribers[-1]
            q.put_nowait({"id": "evt-123", "source": "test", "title": "hi"})
            frame = await agen.__anext__()
            await agen.aclose()
            return frame

        loop = asyncio.new_event_loop()
        try:
            frame = loop.run_until_complete(drive())
        finally:
            loop.close()
        if isinstance(frame, bytes):
            frame = frame.decode()
        assert "id: evt-123" in frame
        assert "data: " in frame

    def test_unknown_last_event_id_is_silent_fallback(self, events_client):
        """Garbled / unknown Last-Event-ID must not 500; treat as
        'client lost its place, start fresh'."""
        # Empty session-state cache so the replay only emits the
        # snapshot.begin/end pair (no per-row session.state frames).
        from common import session_state as _ssn
        with _ssn._lock:
            _ssn._states.clear()
        frames = self._replay(events_client, "not-a-real-id")
        # Preamble + snapshot.begin + snapshot.end. No history replay
        # because `not-a-real-id` doesn't match any events.db rowid.
        assert ": connected" in frames[0]
        joined = "".join(frames)
        assert "session.snapshot.begin" in joined
        assert "session.snapshot.end" in joined

    def test_replay_after_known_id(self, events_client, patched_server, monkeypatch):
        """With two events in the DB and Last-Event-ID pointing at the
        first, the stream replays the second (and only the second)
        before entering the live loop."""
        import sqlite3 as _sq
        # Insert two events into the notif DB that patched_server
        # exposes via app_state._NOTIF_DB_PATH.
        with _sq.connect(str(patched_server._NOTIF_DB_PATH)) as conn:
            conn.execute(
                "INSERT INTO events (id, source, title, message, type, "
                "severity, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("id-a", "test", "first", "", "test.evt", "info",
                 "2026-04-24T10:00:00Z"),
            )
            conn.execute(
                "INSERT INTO events (id, source, title, message, type, "
                "severity, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("id-b", "test", "second", "", "test.evt", "info",
                 "2026-04-24T10:00:01Z"),
            )
            conn.commit()
        frames = self._replay(events_client, "id-a")
        joined = "".join(frames)
        assert "id: id-b" in joined
        assert "second" in joined
        # "first" should NOT be replayed (client already saw it).
        assert "id-a" not in joined.replace("Last-Event-ID: id-a", "")

    def test_replay_caps_at_500_events(
        self, events_client, patched_server, monkeypatch,
    ):
        """A client that was offline for a week shouldn't get flooded
        with thousands of events at once. The replay SELECT caps at
        500 rows; anything older falls off the window."""
        import sqlite3 as _sq
        with _sq.connect(str(patched_server._NOTIF_DB_PATH)) as conn:
            for i in range(600):
                conn.execute(
                    "INSERT INTO events (id, source, title, message, type,"
                    " severity, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (f"evt-{i:04d}", "test", f"msg {i}", "",
                     "test.evt", "info", f"2026-04-24T10:00:{i:02d}Z"),
                )
            conn.commit()
        frames = self._replay(events_client, "evt-0000")
        # Count data frames (not preamble / keepalives).
        data_frames = [f for f in frames if f.startswith("id: evt-")]
        assert len(data_frames) <= 500


class TestReplaySinceSqliteError:
    """`_replay_since` swallows sqlite errors so a corrupted notif DB
    can't break a fresh SSE reconnect -- the client just gets an empty
    list and proceeds with live frames."""

    def test_returns_empty_on_sqlite_error(self, monkeypatch, patched_server):
        import sqlite3
        from routes import events as routes_events

        def boom(*a, **kw):
            raise sqlite3.Error("disk i/o error")
        # _replay_since does a local `import sqlite3 as _sq` inside,
        # so we patch the global sqlite3 module's connect.
        monkeypatch.setattr(sqlite3, "connect", boom)
        out = routes_events._replay_since("evt-anything")
        assert out == []

