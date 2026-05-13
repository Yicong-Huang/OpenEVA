"""Tests for the multiplex SSE terminal transport:
  GET  /api/terminals/stream      -> SSE (hello frame with client_id)
  POST /api/terminals/subscribe   -> adds a session to the client's stream
  POST /api/terminals/unsubscribe -> drops a subscription

All external side effects (tmux, PTY) are mocked so nothing touches real tmux.
"""

import asyncio
import base64
import json
from unittest.mock import patch, MagicMock

import pytest

from routes import terminal as mux


@pytest.fixture(autouse=True)
def _isolate_mux_state():
    """Reset module-level registries between tests so they don't leak."""
    mux._clients.clear()
    mux._session_subscribers.clear()
    for t in list(mux._session_readers.values()):
        t.cancel()
    mux._session_readers.clear()
    yield
    mux._clients.clear()
    mux._session_subscribers.clear()
    for t in list(mux._session_readers.values()):
        t.cancel()
    mux._session_readers.clear()


class TestHelloFrame:
    """First SSE event must be a hello frame with a client_id.

    We bypass the HTTP layer here -- TestClient.stream() buffers until the
    response closes, which never happens for an infinite SSE. Instead we
    invoke the route coroutine directly, drive its async generator one
    iteration, and assert on the first yielded chunk.
    """

    def test_hello_frame_emits_client_id(self):
        async def run():
            resp = await mux.terminals_stream()
            # The returned StreamingResponse holds the generator in .body_iterator
            it = resp.body_iterator
            first = await it.__anext__()
            # Cancel the generator so the background task doesn't leak.
            await it.aclose()
            return first

        first = asyncio.run(run())
        assert first.startswith("data: ")
        payload = json.loads(first[len("data: "):].strip())
        assert payload["type"] == "hello"
        assert isinstance(payload["client_id"], str)
        assert len(payload["client_id"]) >= 8

    def test_cancelled_error_is_swallowed_and_client_dropped(self):
        """When the SSE connection cancels mid-stream (browser tab
        close, network drop), the generator catches CancelledError
        and the finally clause drops the client_id from `_clients`
        so its queue is GC'd."""
        async def run():
            resp = await mux.terminals_stream()
            it = resp.body_iterator
            # Read the hello frame first to seed _clients[client_id].
            first = await it.__anext__()
            payload = json.loads(first[len("data: "):].strip())
            cid = payload["client_id"]
            assert cid in mux._clients
            # Inject CancelledError. The generator catches it (CancelledError
            # branch) and runs the finally to clean up; athrow re-raises
            # StopAsyncIteration at the caller, which we expect.
            try:
                await it.athrow(asyncio.CancelledError())
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            return cid

        cid = asyncio.run(run())
        # finally: ran -> client entry removed.
        assert cid not in mux._clients


class TestSubscribeValidation:
    def test_unknown_client_id_returns_404(self, client):
        resp = client.post("/api/terminals/subscribe", json={
            "client_id": "nonexistent",
            "name": "any-sess",
        })
        assert resp.status_code == 404
        assert "client_id" in resp.json()["detail"]

    def test_missing_tmux_session_returns_404(self, client, mock_tmux):
        # Register a valid client_id manually to isolate from stream lifecycle.
        mux._clients["cid1"] = {"queue": asyncio.Queue(maxsize=8), "subs": set()}
        mock_tmux["exists"].return_value = False
        resp = client.post("/api/terminals/subscribe", json={
            "client_id": "cid1",
            "name": "no-such-sess",
        })
        assert resp.status_code == 404
        assert "tmux session" in resp.json()["detail"]


class TestSubscribeReplayAndFanOut:
    @patch("routes.terminal._tmux_capture", return_value=b"prior\nhistory\n")
    @patch("routes.terminal._ensure_reader")
    def test_subscribe_emits_replay_frame(self, _mock_ensure, _mock_capture,
                                          client, mock_tmux):
        mock_tmux["exists"].return_value = True
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        mux._clients["cid2"] = {"queue": q, "subs": set()}

        resp = client.post("/api/terminals/subscribe", json={
            "client_id": "cid2",
            "name": "sess-1",
        })
        assert resp.status_code == 200

        frame = q.get_nowait()
        assert frame["name"] == "sess-1"
        assert frame["replay"] is True
        assert base64.b64decode(frame["data"]) == b"prior\nhistory\n"

    @patch("routes.terminal._tmux_capture", return_value=b"")
    @patch("routes.terminal._ensure_reader")
    def test_subscribe_skips_replay_when_capture_empty(self, _mock_ensure,
                                                       _mock_capture,
                                                       client, mock_tmux):
        mock_tmux["exists"].return_value = True
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        mux._clients["cid3"] = {"queue": q, "subs": set()}

        resp = client.post("/api/terminals/subscribe", json={
            "client_id": "cid3",
            "name": "sess-empty",
        })
        assert resp.status_code == 200
        assert q.empty(), "no replay should be enqueued for empty capture"

    @patch("routes.terminal._tmux_capture", return_value=b"")
    @patch("routes.terminal._ensure_reader")
    def test_subscribe_registers_queue_in_session_subscribers(
            self, _mock_ensure, _mock_capture, client, mock_tmux):
        mock_tmux["exists"].return_value = True
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        mux._clients["cid4"] = {"queue": q, "subs": set()}

        client.post("/api/terminals/subscribe", json={
            "client_id": "cid4",
            "name": "sess-a",
        })

        assert "sess-a" in mux._session_subscribers
        assert q in mux._session_subscribers["sess-a"]
        assert "sess-a" in mux._clients["cid4"]["subs"]


class TestUnsubscribe:
    def test_unsubscribe_removes_queue_from_session_subscribers(self, client):
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        mux._clients["cid5"] = {"queue": q, "subs": {"sess-x"}}
        mux._session_subscribers["sess-x"] = {q}

        resp = client.post("/api/terminals/unsubscribe", json={
            "client_id": "cid5", "name": "sess-x",
        })
        assert resp.status_code == 200
        # Subscription cleared.
        assert "sess-x" not in mux._session_subscribers
        assert "sess-x" not in mux._clients["cid5"]["subs"]

    def test_unsubscribe_unknown_client_is_noop(self, client):
        resp = client.post("/api/terminals/unsubscribe", json={
            "client_id": "never-existed", "name": "whatever",
        })
        assert resp.status_code == 200

    def test_unsubscribe_twice_is_idempotent(self, client):
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        mux._clients["cid6"] = {"queue": q, "subs": {"sess-y"}}
        mux._session_subscribers["sess-y"] = {q}

        client.post("/api/terminals/unsubscribe",
                    json={"client_id": "cid6", "name": "sess-y"})
        # Second call must not raise.
        resp = client.post("/api/terminals/unsubscribe",
                           json={"client_id": "cid6", "name": "sess-y"})
        assert resp.status_code == 200

    def test_unsubscribe_preserves_other_subscribers(self, client):
        """Dropping one client must NOT clobber other clients' subscriptions
        to the same session."""
        q_a: asyncio.Queue = asyncio.Queue(maxsize=8)
        q_b: asyncio.Queue = asyncio.Queue(maxsize=8)
        mux._clients["A"] = {"queue": q_a, "subs": {"shared"}}
        mux._clients["B"] = {"queue": q_b, "subs": {"shared"}}
        mux._session_subscribers["shared"] = {q_a, q_b}

        client.post("/api/terminals/unsubscribe",
                    json={"client_id": "A", "name": "shared"})

        # Only A dropped, B still subscribed.
        assert q_a not in mux._session_subscribers["shared"]
        assert q_b in mux._session_subscribers["shared"]


class TestBoundedQueueDropOldest:
    def test_overflow_drops_oldest(self):
        q: asyncio.Queue = asyncio.Queue(maxsize=3)
        for i in range(3):
            mux._push_frame(q, {"i": i})
        # Queue full; pushing one more should drop i=0 and keep i=1,2,new.
        mux._push_frame(q, {"i": 99})

        remaining = []
        while not q.empty():
            remaining.append(q.get_nowait())
        assert remaining == [{"i": 1}, {"i": 2}, {"i": 99}]

    def test_push_into_empty_queue(self):
        q: asyncio.Queue = asyncio.Queue(maxsize=2)
        mux._push_frame(q, {"i": 0})
        assert q.get_nowait() == {"i": 0}

    def test_many_overflows_bounded(self):
        """Pushing 1000 frames into a size-4 queue must never grow past 4."""
        q: asyncio.Queue = asyncio.Queue(maxsize=4)
        for i in range(1000):
            mux._push_frame(q, {"i": i})
        assert q.qsize() == 4


class TestTmuxCapture:
    """The replay helper must NEVER raise -- it falls back to b'' on any
    subprocess error so that a broken tmux install still lets live streaming
    work. It also post-processes output to fix alignment when the browser
    xterm is narrower than the source tmux pane."""

    @patch("routes.terminal.subprocess.run")
    def test_returns_stdout_with_reset_prefix_and_crlf(self, mock_run):
        """Replay prepends clear-screen (CSI 2J/3J) + home so xterm paints
        onto a clean buffer. Each physical line ends with CRLF."""
        mock_run.return_value = MagicMock(stdout=b"hello\nworld\n")
        out = mux._tmux_capture("sess")
        assert out.startswith(mux._REPLAY_RESET)
        assert b"\x1b[H" in mux._REPLAY_RESET  # home cursor
        assert b"\x1b[2J" in mux._REPLAY_RESET  # erase screen
        # Body uses CRLF between lines.
        assert out.endswith(b"hello\r\nworld\r\n")

    @patch("routes.terminal.subprocess.run")
    def test_strips_trailing_whitespace_per_line(self, mock_run):
        """tmux pads each line to pane width with trailing spaces. Without
        stripping, a narrower xterm would see those spaces as content and
        re-wrap lines weirdly. Trailing spaces must be gone."""
        mock_run.return_value = MagicMock(stdout=b"abc       \ndef  \t  \n")
        out = mux._tmux_capture("sess")
        assert out.endswith(b"abc\r\ndef\r\n")

    @patch("routes.terminal.subprocess.run")
    def test_does_not_use_join_flag(self, mock_run):
        """Regression: the -J flag ('join wrapped lines') PRESERVES trailing
        spaces to the full pane width, which breaks rendering in a narrower
        xterm. The capture command must NOT include -J."""
        mock_run.return_value = MagicMock(stdout=b"")
        mux._tmux_capture("sess")
        cmd = mock_run.call_args[0][0]
        assert "-J" not in cmd
        # Sanity check: should still use -e (preserve ANSI colors) and -p.
        assert "-e" in cmd
        assert "-p" in cmd

    @patch("routes.terminal.subprocess.run")
    def test_preserves_inline_content_after_stripping_trailing(self, mock_run):
        """Only trailing whitespace is stripped; leading/mid-line whitespace
        (significant to the user -- indentation of logs, code) must remain."""
        mock_run.return_value = MagicMock(stdout=b"  indented  text  \n")
        out = mux._tmux_capture("sess")
        assert out.endswith(b"  indented  text\r\n")

    @patch("routes.terminal.subprocess.run", side_effect=FileNotFoundError)
    def test_returns_empty_bytes_when_tmux_missing(self, _mock_run):
        assert mux._tmux_capture("sess") == b""

    @patch("routes.terminal.subprocess.run", side_effect=Exception("boom"))
    def test_returns_empty_bytes_on_any_error(self, _mock_run):
        assert mux._tmux_capture("sess") == b""

    @patch("routes.terminal.subprocess.run")
    def test_returns_empty_bytes_when_stdout_none(self, mock_run):
        mock_run.return_value = MagicMock(stdout=None)
        assert mux._tmux_capture("sess") == b""

    @patch("routes.terminal.subprocess.run")
    def test_returns_empty_bytes_when_stdout_is_empty(self, mock_run):
        mock_run.return_value = MagicMock(stdout=b"")
        assert mux._tmux_capture("sess") == b""


class TestFanOut:
    """Multiple subscribers must each receive a copy of the same frame via
    _push_frame -- this validates that a single PTY read can serve N clients."""

    def test_multiple_subscribers_get_the_same_frame(self):
        q_a: asyncio.Queue = asyncio.Queue(maxsize=4)
        q_b: asyncio.Queue = asyncio.Queue(maxsize=4)
        frame = {"name": "sess", "data": "hello"}
        for q in (q_a, q_b):
            mux._push_frame(q, frame)
        assert q_a.get_nowait() == frame
        assert q_b.get_nowait() == frame

    def test_one_client_overflow_does_not_affect_others(self):
        """A slow client (full queue) drops its own oldest but the fast
        client still gets every frame intact."""
        slow: asyncio.Queue = asyncio.Queue(maxsize=2)
        fast: asyncio.Queue = asyncio.Queue(maxsize=100)
        for i in range(5):
            frame = {"i": i}
            mux._push_frame(slow, frame)
            mux._push_frame(fast, frame)

        # fast received all 5.
        assert fast.qsize() == 5
        # slow kept only the 2 newest.
        assert slow.qsize() == 2
        remaining_slow = [slow.get_nowait() for _ in range(2)]
        assert remaining_slow == [{"i": 3}, {"i": 4}]


class TestDropClientCleanup:
    """When a client's SSE stream closes, `_drop_client` must remove the
    queue from every session's subscriber set so the session's reader task
    doesn't keep pushing into an abandoned queue forever."""

    def test_drop_client_removes_from_multiple_sessions(self):
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        mux._clients["C"] = {"queue": q, "subs": {"s1", "s2"}}
        mux._session_subscribers["s1"] = {q}
        mux._session_subscribers["s2"] = {q}

        mux._drop_client("C")
        assert "C" not in mux._clients
        assert "s1" not in mux._session_subscribers
        assert "s2" not in mux._session_subscribers

    def test_drop_unknown_client_is_noop(self):
        # No raise.
        mux._drop_client("never-existed")

    def test_drop_client_preserves_other_clients_on_shared_session(self):
        """A session shared by two clients must keep the remaining client's
        queue registered when one client drops."""
        q_a: asyncio.Queue = asyncio.Queue(maxsize=8)
        q_b: asyncio.Queue = asyncio.Queue(maxsize=8)
        mux._clients["A"] = {"queue": q_a, "subs": {"shared"}}
        mux._clients["B"] = {"queue": q_b, "subs": {"shared"}}
        mux._session_subscribers["shared"] = {q_a, q_b}

        mux._drop_client("A")
        assert "A" not in mux._clients
        assert "shared" in mux._session_subscribers
        assert q_a not in mux._session_subscribers["shared"]
        assert q_b in mux._session_subscribers["shared"]


class TestEnsureReader:
    """`_ensure_reader` starts a reader task when none is running for the
    session, and is a no-op when one already exists."""

    def test_starts_reader_when_none(self):
        async def run():
            mux._session_readers.pop("fresh", None)
            with patch("routes.terminal._pty.get_or_create") as get_or_create, \
                 patch("routes.terminal._pty.get") as get_pty:
                ps = MagicMock()
                ps.alive = False  # reader loop exits immediately
                get_or_create.return_value = ps
                get_pty.return_value = ps
                mux._ensure_reader("fresh")
                task = mux._session_readers.get("fresh")
                assert task is not None
                get_or_create.assert_called_once_with("fresh")
                await task  # drain

        asyncio.run(run())

    def test_noop_when_task_running(self):
        async def run():
            # Pre-register a running task so _ensure_reader must not spawn a new one.
            async def idle():
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    pass
            existing = asyncio.create_task(idle())
            try:
                mux._session_readers["busy"] = existing
                with patch("routes.terminal._pty.get_or_create") as get_or_create:
                    mux._ensure_reader("busy")
                    get_or_create.assert_not_called()
                assert mux._session_readers["busy"] is existing
            finally:
                existing.cancel()
                try:
                    await existing
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())


class TestStreamGeneratorLoop:
    """Exercise the SSE generator's post-hello loop: dequeue -> yield frame,
    TimeoutError -> yield keepalive, CancelledError -> clean exit."""

    def test_yields_queued_frame(self):
        async def run():
            resp = await mux.terminals_stream()
            it = resp.body_iterator
            # Consume hello.
            await it.__anext__()
            # Push a frame into the client's queue (look up the single client).
            assert len(mux._clients) == 1
            q = next(iter(mux._clients.values()))["queue"]
            q.put_nowait({"name": "s", "data": "xyz"})
            frame_chunk = await it.__anext__()
            await it.aclose()
            return frame_chunk

        chunk = asyncio.run(run())
        assert chunk.startswith("data: ")
        assert json.loads(chunk[len("data: "):].strip()) == {"name": "s", "data": "xyz"}

    def test_emits_keepalive_on_timeout(self):
        async def run():
            # Patch the wait_for to raise TimeoutError immediately so we can
            # assert the keepalive emission without actually waiting 20s.
            real_wait_for = asyncio.wait_for

            async def fast_timeout(coro, timeout):
                # Cancel the coroutine we were given and raise TimeoutError.
                try:
                    coro.close()
                except Exception:
                    pass
                raise asyncio.TimeoutError()

            resp = await mux.terminals_stream()
            it = resp.body_iterator
            await it.__anext__()  # hello
            with patch("routes.terminal.asyncio.wait_for", side_effect=fast_timeout):
                chunk = await it.__anext__()
            await it.aclose()
            return chunk

        chunk = asyncio.run(run())
        assert chunk.startswith(":")  # SSE comment
        assert "keepalive" in chunk

    def test_cancelled_error_ends_cleanly(self):
        """Cancelling the body iterator must tear down the client entry
        without leaking the queue in _clients."""
        async def run():
            resp = await mux.terminals_stream()
            it = resp.body_iterator
            await it.__anext__()  # hello
            clients_before = dict(mux._clients)
            assert len(clients_before) == 1
            await it.aclose()
            # `finally` in the generator should drop the client on close.
            assert not mux._clients

        asyncio.run(run())


class TestPushFrameOverflowRace:
    """Cover the (asyncio.QueueEmpty, asyncio.QueueFull) rescue branch in
    `_push_frame`: if by the time we try get_nowait the queue is already
    empty -- or fills up again before put_nowait -- the second exception
    must be swallowed so the fan-out loop never crashes."""

    def test_rescue_when_get_nowait_raises(self):
        from unittest.mock import MagicMock
        q = MagicMock()
        q.put_nowait.side_effect = [asyncio.QueueFull(), None]
        q.get_nowait.side_effect = asyncio.QueueEmpty()
        # Must not raise.
        mux._push_frame(q, {"x": 1})

    def test_rescue_when_second_put_raises(self):
        from unittest.mock import MagicMock
        q = MagicMock()
        q.put_nowait.side_effect = asyncio.QueueFull()
        q.get_nowait.return_value = {"old": True}
        # Must not raise.
        mux._push_frame(q, {"x": 1})


class TestReaderTaskLifecycle:
    """Verify the background reader cleans up from the registry when the PTY
    dies and fans frames out to subscribed queues."""

    def test_reader_exits_when_pty_dead_and_removes_itself(self):
        async def run():
            ps = MagicMock()
            ps.alive = False
            with patch("routes.terminal._pty.get", return_value=ps):
                # Pre-register a task entry to simulate a running reader.
                task = asyncio.create_task(mux._session_reader_task("dead-sess"))
                mux._session_readers["dead-sess"] = task
                await task
            assert "dead-sess" not in mux._session_readers

        asyncio.run(run())

    def test_reader_exits_cleanly_when_pty_missing(self):
        async def run():
            with patch("routes.terminal._pty.get", return_value=None):
                # Pre-register to prove cleanup.
                task = asyncio.create_task(mux._session_reader_task("gone"))
                mux._session_readers["gone"] = task
                await task
            assert "gone" not in mux._session_readers

        asyncio.run(run())

    def test_reader_fans_data_to_all_subscribers(self):
        """A single PTY read produces one frame per subscribed queue."""
        async def run():
            ps = MagicMock()
            # Return data once, then flip alive False so the loop exits.
            call_n = [0]

            def read():
                call_n[0] += 1
                if call_n[0] == 1:
                    return b"payload"
                ps.alive = False
                return b""

            ps.alive = True
            ps.read = read
            q_a: asyncio.Queue = asyncio.Queue(maxsize=8)
            q_b: asyncio.Queue = asyncio.Queue(maxsize=8)
            mux._session_subscribers["s"] = {q_a, q_b}

            with patch("routes.terminal._pty.get", return_value=ps):
                await mux._session_reader_task("s")

            # Both queues received a frame with the encoded payload.
            for q in (q_a, q_b):
                f = q.get_nowait()
                assert f["name"] == "s"
                assert base64.b64decode(f["data"]) == b"payload"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Ring buffer + incremental replay (lossless reconnect)
# ---------------------------------------------------------------------------

class TestRingBuffer:
    """`_ring_append` records every PTY emission with a monotonic seq.
    `_ring_since(seq)` returns chunks newer than seq, or None when the
    ring rolled past so the caller falls back to a tmux snapshot.

    Ring state is module-global (one per session); each test isolates
    by using a unique session name."""

    def setup_method(self):
        # Wipe any state left by other tests so seq counters are
        # deterministic. Ring is keyed by session, so we just clear
        # ours.
        mux._session_rings.pop("ring-test", None)

    def test_append_assigns_monotonic_seq(self):
        s1 = mux._ring_append("ring-test", b"a")
        s2 = mux._ring_append("ring-test", b"b")
        s3 = mux._ring_append("ring-test", b"c")
        assert s1 == 1 and s2 == 2 and s3 == 3

    def test_since_returns_only_newer_chunks(self):
        mux._ring_append("ring-test", b"a")
        mux._ring_append("ring-test", b"b")
        mux._ring_append("ring-test", b"c")
        out = mux._ring_since("ring-test", since_seq=1)
        # Returns chunks 2 and 3 (newer than 1).
        assert [s for s, _ in out] == [2, 3]
        assert [d for _, d in out] == [b"b", b"c"]

    def test_since_zero_returns_everything(self):
        mux._ring_append("ring-test", b"x")
        mux._ring_append("ring-test", b"y")
        out = mux._ring_since("ring-test", since_seq=0)
        # Caller should fall back to snapshot when since=0 BUT here
        # since=0 < oldest_seq-1 (=0), so we return the whole list.
        # The route layer's check is "if since_seq" -- it skips this
        # when 0. So no incremental replay on cold-start.
        assert out is not None and len(out) == 2

    def test_since_returns_empty_when_caller_is_caught_up(self):
        mux._ring_append("ring-test", b"a")
        out = mux._ring_since("ring-test", since_seq=1)
        # Caller already saw everything -- empty delta, no snapshot.
        assert out == []

    def test_since_returns_none_when_ring_empty(self):
        # No appends ever -> can't tell "nothing happened" from "lost
        # the gap". Be safe: caller does a snapshot.
        out = mux._ring_since("ring-test", since_seq=5)
        assert out is None

    def test_since_returns_none_on_overflow(self):
        # Push enough bytes that trimming drops chunks 1 AND 2. With
        # cap=256KB and 64KB/chunk, after 6 pushes the trimmer pops
        # 2 chunks to bring size back to 256KB. A client at
        # since_seq=1 then asks for chunk 2, which is gone.
        chunk_size = mux._RING_BYTES_CAP // 4
        for _ in range(6):
            mux._ring_append("ring-test", b"x" * chunk_size)
        out = mux._ring_since("ring-test", since_seq=1)
        # Oldest seq in ring is now 3 (1 and 2 were trimmed); client
        # wanted chunk 2 onwards but it's gone -> snapshot.
        oldest = mux._session_rings["ring-test"]["chunks"][0][0]
        assert oldest >= 3
        assert out is None

    def test_overflow_trims_to_cap(self):
        # The ring respects _RING_BYTES_CAP. After many pushes the
        # total payload size never exceeds the cap.
        for _ in range(20):
            mux._ring_append("ring-test", b"x" * 4096)
        ring = mux._session_rings["ring-test"]
        assert ring["size"] <= mux._RING_BYTES_CAP


class TestSubscribeIncrementalDelta:
    """When the client sends `since_seq` and the ring has the gap,
    `/api/terminals/subscribe` replays JUST the delta -- no tmux
    snapshot, no terminal reset on the client."""

    def setup_method(self):
        mux._session_rings.pop("inc-test", None)

    @patch("routes.terminal._ensure_reader")
    @patch("routes.terminal.session_exists", return_value=True)
    def test_incremental_replay_when_ring_has_gap(
        self, _mock_exists, _mock_ensure, client,
    ):
        # Seed the ring with some chunks. _ring_append assigns seqs
        # 1, 2, 3.
        mux._ring_append("inc-test", b"frame-1")
        mux._ring_append("inc-test", b"frame-2")
        mux._ring_append("inc-test", b"frame-3")
        # Open a stream so we have a valid client_id.
        cid = "delta-cid"
        mux._clients[cid] = {"queue": asyncio.Queue(maxsize=8),
                             "subs": set()}
        try:
            resp = client.post("/api/terminals/subscribe", json={
                "client_id": cid, "name": "inc-test", "since_seq": 1,
            })
            assert resp.status_code == 200
            body = resp.json()
            assert body["mode"] == "incremental"
            assert body["chunks"] == 2  # frames 2 and 3
            # Two delta frames in the queue, each marked incremental.
            q = mux._clients[cid]["queue"]
            f2 = q.get_nowait()
            f3 = q.get_nowait()
            assert f2["incremental"] is True
            assert f2["seq"] == 2
            assert base64.b64decode(f2["data"]) == b"frame-2"
            assert f3["seq"] == 3
            assert base64.b64decode(f3["data"]) == b"frame-3"
        finally:
            mux._clients.pop(cid, None)

    @patch("routes.terminal._ensure_reader")
    @patch("routes.terminal.session_exists", return_value=True)
    @patch("routes.terminal._tmux_capture", return_value=b"snapshot")
    def test_falls_back_to_snapshot_when_ring_lost_gap(
        self, _mock_cap, _mock_exists, _mock_ensure, client,
    ):
        # Ring has nothing -> can't serve incrementally; snapshot.
        cid = "snap-cid"
        mux._clients[cid] = {"queue": asyncio.Queue(maxsize=8),
                             "subs": set()}
        try:
            resp = client.post("/api/terminals/subscribe", json={
                "client_id": cid, "name": "inc-test", "since_seq": 99,
            })
            assert resp.status_code == 200
            body = resp.json()
            assert body["mode"] == "snapshot"
            q = mux._clients[cid]["queue"]
            f = q.get_nowait()
            assert f["replay"] is True
            assert base64.b64decode(f["data"]) == b"snapshot"
        finally:
            mux._clients.pop(cid, None)

    @patch("routes.terminal._ensure_reader")
    @patch("routes.terminal.session_exists", return_value=True)
    @patch("routes.terminal._tmux_capture", return_value=b"")
    def test_cold_start_with_since_seq_zero_does_snapshot(
        self, _mock_cap, _mock_exists, _mock_ensure, client,
    ):
        # since_seq=0 => "I don't know what I've seen" -> snapshot path.
        # Empty tmux capture means no replay frame queued; mode still
        # 'snapshot' so the client knows to reset xterm.
        cid = "cold-cid"
        mux._clients[cid] = {"queue": asyncio.Queue(maxsize=8),
                             "subs": set()}
        try:
            resp = client.post("/api/terminals/subscribe", json={
                "client_id": cid, "name": "inc-test", "since_seq": 0,
            })
            assert resp.status_code == 200
            assert resp.json()["mode"] == "snapshot"
        finally:
            mux._clients.pop(cid, None)


class TestReaderTaskTagsSeq:
    """The PTY reader task assigns a seq to every chunk it emits and
    pushes the same seq into both the ring and the live frame."""

    def test_reader_emits_seq_in_frame(self):
        mux._session_rings.pop("seq-sess", None)

        class _PS:
            alive = True
            count = 0

            def read(self):
                self.count += 1
                if self.count > 1:
                    self.alive = False
                    return b""
                return b"once"

        ps = _PS()
        q = asyncio.Queue(maxsize=8)
        mux._session_subscribers["seq-sess"] = {q}
        try:
            with patch("routes.terminal._pty.get", return_value=ps):
                asyncio.run(mux._session_reader_task("seq-sess"))
            f = q.get_nowait()
            # First read produced seq=1; ring holds it for resubscribe.
            assert f["seq"] == 1
            assert mux._session_rings["seq-sess"]["chunks"][0][0] == 1
        finally:
            mux._session_subscribers.pop("seq-sess", None)
            mux._session_rings.pop("seq-sess", None)
