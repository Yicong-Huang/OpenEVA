"""Integration tests for session/hook API endpoints with mocked tmux."""

from unittest.mock import patch, MagicMock


class TestReceiveHook:
    def test_hook_stop_event(self, client):
        resp = client.post("/api/hook", json={
            "session": "test-session",
            "event": "Stop",
            "data": {},
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_hook_notification_idle_prompt(self, client):
        resp = client.post("/api/hook", json={
            "session": "test-session",
            "event": "Notification",
            "data": {"notification_type": "idle_prompt"},
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_hook_notification_permission(self, client):
        resp = client.post("/api/hook", json={
            "session": "test-session",
            "event": "Notification",
            "data": {"notification_type": "permission_prompt", "message": "Allow?"},
        })
        assert resp.status_code == 200

    def test_hook_user_prompt_submit(self, client):
        resp = client.post("/api/hook", json={
            "session": "test-session",
            "event": "UserPromptSubmit",
            "data": {},
        })
        assert resp.status_code == 200

    def test_hook_session_start(self, client):
        resp = client.post("/api/hook", json={
            "session": "test-session",
            "event": "SessionStart",
            "data": {},
        })
        assert resp.status_code == 200

    def test_hook_no_session_returns_not_ok(self, client):
        resp = client.post("/api/hook", json={
            "session": "",
            "event": "Stop",
            "data": {},
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_hook_updates_session_state(self, client, patched_server):
        # Send a Stop event then check state
        client.post("/api/hook", json={
            "session": "state-test",
            "event": "Stop",
            "data": {},
        })
        state = patched_server._session_states.get("state-test", {})
        assert state["state"] == "idle"

        # Send UserPromptSubmit -> thinking
        client.post("/api/hook", json={
            "session": "state-test",
            "event": "UserPromptSubmit",
            "data": {},
        })
        state = patched_server._session_states.get("state-test", {})
        assert state["state"] == "thinking"

    def test_hook_review_session_routes_to_review_prs_not_sessions(
        self, client, patched_server
    ):
        """Review-named sessions (review-*) must update `review_prs` and
        `review_history` instead of the `sessions` table. Regression
        guard: without this, a review's agent_session_id would land on
        a non-existent sessions row and host recovery-resume would fail."""
        url = "https://github.com/example/repo/pull/42"
        patched_server._db.upsert_review_pr(
            url=url, repo="example/repo", number=42,
            title="test", author="x", status="open",
            last_updated="", source="github",
            session_name="review-example-repo-42",
        )
        client.post("/api/hook", json={
            "session": "review-example-repo-42",
            "event": "SessionStart",
            "data": {"session_id": "uuid-xyz"},
        })
        row = patched_server._db.get_review_pr(url)
        # agent_session_id landed on the review row (not sessions table).
        assert row["agent_session_id"] == "uuid-xyz"
        assert row["my_workflow_state"] == "active"
        # A history entry was appended tagged source='agent'.
        hist = patched_server._db.list_review_history(url)
        assert any(e["source"] == "agent" for e in hist)

    def test_hook_review_session_unknown_name_is_noop(
        self, client, patched_server
    ):
        """If the session name starts with review- but no matching row
        exists (e.g. stale hook after the row was deleted), the handler
        must not crash."""
        resp = client.post("/api/hook", json={
            "session": "review-nope-x-1",
            "event": "Stop",
            "data": {},
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_kill_review_session_clears_session_name(
        self, client, mock_tmux, patched_server
    ):
        """DELETE /api/sessions/review-<name> must clear `session_name` +
        `agent_session_id` on the review_prs row (and NOT touch the
        sessions table, which doesn't hold review sessions). Without
        this, the ReviewCard would keep showing a ghost SessionCard
        pointing at a dead tmux session after kill."""
        url = "https://github.com/example/repo/pull/77"
        patched_server._db.upsert_review_pr(
            url=url, repo="example/repo", number=77,
            title="test", author="x", status="open",
            last_updated="", source="github",
            session_name="review-example-repo-77",
            agent_session_id="uuid-77",
            my_workflow_state="active",
        )
        mock_tmux["exists"].return_value = True

        resp = client.delete("/api/sessions/review-example-repo-77")
        assert resp.status_code == 200

        row = patched_server._db.get_review_pr(url)
        assert row["session_name"] == ""
        assert row["agent_session_id"] == ""
        # History got a "session killed" system entry.
        hist = patched_server._db.list_review_history(url)
        assert any("killed" in e["text"] for e in hist)


class TestParseSessionState:
    """Pure-function tests for _parse_session_state: classify tmux output.

    Each case uses a canned buffer string so we don't need a live tmux.
    """

    @staticmethod
    def _call(output):
        from routes.sessions import _parse_session_state
        return _parse_session_state(output)

    def test_empty_output_is_unknown(self):
        assert self._call("") == ("unknown", "")

    def test_prompt_alone_is_idle(self):
        out = "some log\nsome output\n\u276f "
        assert self._call(out) == ("idle", "Waiting for input")

    def test_prompt_plus_permission_banner_is_needs_permission(self):
        out = "\u276f \nesc to cancel  1. Yes"
        assert self._call(out) == ("needs_permission", "Waiting for approval")

    def test_permission_banner_without_prompt_is_needs_permission(self):
        out = "esc to cancel  1. Yes  2. No"
        assert self._call(out) == ("needs_permission", "Waiting for approval")

    def test_running_star_line_is_thinking_with_detail(self):
        out = "something\n* Running tool (foo)...\n"
        state, detail = self._call(out)
        assert state == "thinking"
        assert "Running tool" in detail
        # Trailing dots stripped from detail.
        assert not detail.endswith(".")

    def test_shortcuts_footer_is_idle(self):
        out = "? for shortcuts"
        assert self._call(out) == ("idle", "Waiting for input")

    def test_unicode_prompt_variant_is_detected(self):
        # Single U+276F character alone should still be treated as a prompt.
        out = "hello\n\u276f"
        assert self._call(out) == ("idle", "Waiting for input")

    def test_only_whitespace_lines_is_unknown(self):
        assert self._call("\n  \n\t\n") == ("unknown", "")


class TestResolveHookRule:
    """Pure-function tests for _resolve_hook_rule: table-driven dispatch
    used by receive_hook."""

    @staticmethod
    def _rule(event, ntype=""):
        from routes.sessions import _resolve_hook_rule
        return _resolve_hook_rule(event, ntype)

    def test_known_simple_event(self):
        rule = self._rule("Stop")
        assert rule is not None
        assert rule[0] == "idle"  # new_state
        assert rule[2] == "agent.task_done"  # emit_type

    def test_user_prompt_submit_pulls_prompt_from_data(self):
        rule = self._rule("UserPromptSubmit")
        assert rule is not None
        msg_fn = rule[5]
        assert msg_fn({"prompt": "hello world"}) == "hello world"
        # Long prompts clipped at 100 chars
        long = "x" * 500
        assert msg_fn({"prompt": long}) == "x" * 100

    def test_notification_demultiplex_by_ntype(self):
        assert self._rule("Notification", "idle_prompt") is not None
        assert self._rule("Notification", "permission_prompt") is not None
        assert self._rule("Notification", "garbage") is None

    def test_unknown_event_returns_none(self):
        assert self._rule("NotARealEvent") is None

    def test_permission_rule_uses_msg_as_detail(self):
        """The permission rule sets default_detail=None so the message
        becomes the detail."""
        rule = self._rule("Notification", "permission_prompt")
        assert rule is not None
        _, default_detail, _, _, _, msg_fn = rule
        assert default_detail is None
        assert msg_fn({"message": "approve X?"}) == "approve X?"
        # Falls back to a human-friendly default if the hook omits message.
        assert msg_fn({}) == "Waiting for approval"


class TestGetSessionStatus:
    def test_stopped_session(self, client, mock_tmux):
        mock_tmux["exists"].return_value = False
        resp = client.get("/api/sessions/gone-session/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "stopped"

    def test_idle_session_via_hook(self, client, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = True
        # Set hook state to idle
        from datetime import datetime
        patched_server._session_states["hook-sess"] = {
            "state": "idle",
            "detail": "Waiting for input",
            "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        }
        resp = client.get("/api/sessions/hook-sess/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "idle"

    def test_thinking_session_via_tmux_fallback(self, client, mock_tmux):
        mock_tmux["exists"].return_value = True
        mock_tmux["capture"].return_value = "* Analyzing code...\n"
        resp = client.get("/api/sessions/tmux-sess/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "thinking"

    def test_permission_needed_via_tmux(self, client, mock_tmux):
        mock_tmux["exists"].return_value = True
        mock_tmux["capture"].return_value = (
            "Some output\n"
            "esc to cancel\n"
            "1. yes\n"
        )
        resp = client.get("/api/sessions/perm-sess/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "needs_permission"

    def test_unknown_session_state(self, client, mock_tmux):
        mock_tmux["exists"].return_value = True
        mock_tmux["capture"].return_value = "some random output\n"
        resp = client.get("/api/sessions/unknown-sess/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "unknown"


class TestLaunchSession:
    def test_launch_new_session(self, client, mock_tmux):
        resp = client.post("/api/sessions/launch", json={
            "session_name": "new-sess",
            "working_dir": "~/projects",
            "agent_args": "",
            "prompt": "Do something",
            "project_id": "test-proj",
            "action": "open",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["session"] == "new-sess"
        assert data["running"] is True
        mock_tmux["launch"].assert_called_once()

    def test_launch_with_agent_args(self, client, mock_tmux):
        """When `agent_args` is non-empty, the route runs the active
        agent's binary plus those argv tokens verbatim. The binary
        name itself is whatever the registered agent ships (OSS
        default `claude`)."""
        resp = client.post("/api/sessions/launch", json={
            "session_name": "args-sess",
            "working_dir": "~",
            "agent_args": "-n args-sess --model opus",
        })
        assert resp.status_code == 200
        mock_tmux["launch"].assert_called_once()
        call_args = mock_tmux["launch"].call_args
        command = call_args[0][2]  # third positional arg
        from common import agent as _agent
        binary = _agent.get_active_agent().binary
        assert command == f"{binary} -n args-sess --model opus"

    def test_launch_no_project_id(self, client, mock_tmux):
        resp = client.post("/api/sessions/launch", json={
            "session_name": "no-proj-sess",
            "working_dir": "~",
        })
        assert resp.status_code == 200


class TestKillSession:
    @patch("server.subprocess.run")
    def test_kill_existing_session(self, mock_run, client, mock_tmux):
        mock_tmux["exists"].return_value = True
        resp = client.delete("/api/sessions/kill-me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "killed"

    def test_kill_nonexistent_session(self, client, mock_tmux):
        mock_tmux["exists"].return_value = False
        resp = client.delete("/api/sessions/already-gone")
        assert resp.status_code == 200
        assert resp.json()["status"] == "killed"


class TestResumeSessionRoute:
    """POST /api/sessions/{name}/resume relaunches tmux + resumes agent by UUID."""

    def test_resume_missing_session_returns_404(self, client, mock_tmux):
        mock_tmux["exists"].return_value = False
        resp = client.post("/api/sessions/ghost/resume")
        assert resp.status_code == 404

    def test_resume_dead_tmux_with_uuid_calls_agent_resume(
            self, client, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("r-sess", "test-proj", "r-sess")
        patched_server._db.update_session(
            "r-sess", agent_session_id="deadbeef-1234-5678-90ab-cdef01234567",
        )
        resp = client.post("/api/sessions/r-sess/resume")
        assert resp.status_code == 200
        d = resp.json()
        assert d["action"] == "resumed"
        # argv must use the `agent resume <uuid>` subcommand (not
        # --resume passthrough to claude) so agent owns session discovery.
        call = mock_tmux["launch_argv"].call_args
        argv = call.args[2] if len(call.args) >= 3 else call.kwargs.get("argv")
        assert argv[0] in ("agent", "claude") and argv[1] == "resume"
        assert argv[2] == "deadbeef-1234-5678-90ab-cdef01234567"

    def test_resume_is_noop_when_tmux_alive(
            self, client, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session("alive-sess", "test-proj", "alive-sess")
        resp = client.post("/api/sessions/alive-sess/resume")
        assert resp.status_code == 200
        assert resp.json()["action"] == "noop"
        mock_tmux["launch_argv"].assert_not_called()


class TestHookCapturesAgentSessionId:
    """SessionStart hook must persist agent's session_id so Resume can use it."""

    def test_session_start_persists_session_id(self, client, patched_server):
        patched_server._db.create_session("hk-sess", "test-proj", "hk-sess")
        resp = client.post("/api/hook", json={
            "session": "hk-sess",
            "event": "SessionStart",
            "data": {
                "session_id": "feedface-1111-2222-3333-aabbccddeeff",
                "source": "startup",
                "hook_event_name": "SessionStart",
            },
        })
        assert resp.status_code == 200
        row = patched_server._db.get_session("hk-sess")
        assert row["agent_session_id"] == "feedface-1111-2222-3333-aabbccddeeff"

    def test_non_session_start_hook_does_not_overwrite_uuid(
            self, client, patched_server):
        patched_server._db.create_session("hk2", "test-proj", "hk2")
        # First SessionStart records UUID.
        client.post("/api/hook", json={
            "session": "hk2", "event": "SessionStart",
            "data": {"session_id": "first-uuid-value", "source": "startup"},
        })
        # A later Stop hook with a DIFFERENT session_id (shouldn't happen in
        # practice but guard anyway) must not overwrite the recorded one.
        client.post("/api/hook", json={
            "session": "hk2", "event": "Stop",
            "data": {"session_id": "some-other-uuid"},
        })
        row = patched_server._db.get_session("hk2")
        assert row["agent_session_id"] == "first-uuid-value"


class TestHookEdgeCases:
    def test_hook_empty_session(self, client):
        resp = client.post("/api/hook", json={
            "session": "",
            "event": "Stop",
            "data": {},
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_hook_missing_event(self, client):
        # No event key -> server defaults to empty string -> graceful, returns ok:true
        resp = client.post("/api/hook", json={
            "session": "some-session",
            "data": {},
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestListProjectSessions:
    def test_list_empty(self, client, mock_tmux):
        mock_tmux["exists"].return_value = False
        resp = client.get("/api/projects/test-proj/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data

    def test_list_after_launch(self, client, mock_tmux, patched_server):
        mock_tmux["exists"].return_value = True
        # Create a session in DB to simulate the new flow
        patched_server._db.create_session("listed-sess", "test-proj", "listed-sess")
        resp = client.get("/api/projects/test-proj/sessions")
        data = resp.json()
        assert len(data["sessions"]) > 0
        names = [s.get("task_id") or s.get("tmux_name") for s in data["sessions"]]
        assert "listed-sess" in names


class TestRebuildSessions:
    def test_all_sessions_already_running(self, client, mock_tmux, patched_server):
        """When all sessions have tmux running, they should all be skipped."""
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session("sess-1", "test-proj", "sess-1")
        patched_server._db.create_session("sess-2", "test-proj", "sess-2")
        resp = client.post("/api/sessions/rebuild")
        assert resp.status_code == 200
        data = resp.json()
        assert "sess-1" in data["skipped"]
        assert "sess-2" in data["skipped"]
        assert data["rebuilt"] == []
        assert data["failed"] == []
        mock_tmux["launch"].assert_not_called()

    def test_dead_sessions_are_rebuilt(self, client, mock_tmux, patched_server):
        """When tmux is dead for a session, it should be rebuilt."""
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("dead-sess", "test-proj", "dead-sess")
        resp = client.post("/api/sessions/rebuild")
        assert resp.status_code == 200
        data = resp.json()
        assert "dead-sess" in data["rebuilt"]
        assert data["skipped"] == []
        assert data["failed"] == []
        mock_tmux["launch"].assert_called_once()
        # Verify it was called with resume command
        call_args = mock_tmux["launch"].call_args
        assert "--resume" in call_args[0][2]

    def test_mixed_alive_and_dead_sessions(self, client, mock_tmux, patched_server):
        """Mix of alive and dead sessions should be sorted correctly."""
        patched_server._db.create_session("alive-sess", "test-proj", "alive-sess")
        patched_server._db.create_session("dead-sess", "test-proj", "dead-sess")
        # First call for alive-sess returns True, second for dead-sess returns False
        mock_tmux["exists"].side_effect = [True, False]
        resp = client.post("/api/sessions/rebuild")
        assert resp.status_code == 200
        data = resp.json()
        assert "alive-sess" in data["skipped"]
        assert "dead-sess" in data["rebuilt"]
        assert data["failed"] == []

    def test_launch_failure_is_recorded(self, client, mock_tmux, patched_server):
        """When adapters.tmux.launch_session raises, session goes to failed list."""
        mock_tmux["exists"].return_value = False
        mock_tmux["launch"].side_effect = RuntimeError("tmux not found")
        patched_server._db.create_session("fail-sess", "test-proj", "fail-sess")
        resp = client.post("/api/sessions/rebuild")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rebuilt"] == []
        assert data["skipped"] == []
        assert len(data["failed"]) == 1
        assert data["failed"][0]["session"] == "fail-sess"
        assert "tmux not found" in data["failed"][0]["error"]

    def test_no_sessions_returns_empty(self, client, mock_tmux, patched_server):
        """When there are no sessions in DB, all lists are empty."""
        resp = client.post("/api/sessions/rebuild")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rebuilt"] == []
        assert data["skipped"] == []
        assert data["failed"] == []

    def test_rebuild_launches_tmux_for_dead_sessions(self, client, mock_tmux, patched_server):
        """Sessions whose tmux died are relaunched via `agent --resume`.
        The old `background_sent` flag no longer gates anything (context
        is re-injected via --append-system-prompt on every launch), so
        rebuild is just: detect dead tmux -> launch agent --resume."""
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("reset-sess", "test-proj", "reset-sess")
        resp = client.post("/api/sessions/rebuild")
        assert resp.status_code == 200
        assert "reset-sess" in resp.json()["rebuilt"]
        mock_tmux["launch"].assert_called()


class TestKillSessionsByStatus:
    def test_empty_statuses_returns_empty(self, client, patched_server):
        """Empty statuses list should return empty killed list."""
        resp = client.post("/api/sessions/kill-by-status", json={"statuses": []})
        assert resp.status_code == 200
        assert resp.json()["killed"] == []

    def test_missing_statuses_returns_empty(self, client, patched_server):
        """Missing statuses key should return empty killed list."""
        resp = client.post("/api/sessions/kill-by-status", json={})
        assert resp.status_code == 200
        assert resp.json()["killed"] == []

    def test_non_list_statuses_returns_422(self, client, patched_server):
        """String instead of list for `statuses` used to slip past the raw
        dict handler and cause substring-match bugs (`'stop' in 'stopped'`).
        Pydantic now rejects it upfront as 422."""
        resp = client.post("/api/sessions/kill-by-status", json={"statuses": "stopped"})
        assert resp.status_code == 422

    def test_non_string_items_returns_422(self, client, patched_server):
        """List of non-strings is also rejected upfront."""
        resp = client.post("/api/sessions/kill-by-status", json={"statuses": [1, 2]})
        assert resp.status_code == 422

    @patch("subprocess.run")
    def test_kill_stopped_sessions(self, mock_subproc, client, mock_tmux, patched_server):
        """Sessions with dead tmux should be treated as 'stopped' and killed."""
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("stopped-sess", "test-proj", "stopped-sess")
        resp = client.post("/api/sessions/kill-by-status", json={"statuses": ["stopped"]})
        assert resp.status_code == 200
        data = resp.json()
        assert "stopped-sess" in data["killed"]
        # tmux kill-session should NOT be called since session is not running
        mock_subproc.assert_not_called()
        # Session should be deleted from DB
        assert patched_server._db.get_session("stopped-sess") is None

    @patch("routes.sessions._session_states", {"idle-sess": {"state": "idle"}})
    @patch("subprocess.run")
    def test_kill_idle_sessions(self, mock_subproc, client, mock_tmux, patched_server):
        """Running sessions with live state 'idle' should be killed when matching."""
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session("idle-sess", "test-proj", "idle-sess")
        resp = client.post("/api/sessions/kill-by-status", json={"statuses": ["idle"]})
        assert resp.status_code == 200
        data = resp.json()
        assert "idle-sess" in data["killed"]
        # Graceful kill = Ctrl+C then tmux kill-session. Filter to the
        # tmux-family commands FOR THIS SESSION only -- background
        # threads (scheduler, gh polling) plus session-state reapers
        # poking unrelated sessions (e.g. test-fixture `task-d`) can
        # leak subprocess.run calls into this globally-patched mock when
        # other test modules run first.
        tmux_calls = [
            c for c in mock_subproc.call_args_list
            if c[0] and isinstance(c[0][0], list) and c[0][0][:1] == ["tmux"]
            and "idle-sess" in c[0][0]
        ]
        assert len(tmux_calls) == 2, (
            "expected graceful_kill's send-keys + kill-session pair; got %r"
            % [c[0][0] for c in tmux_calls]
        )
        sent_cmds = [c[0][0][:2] for c in tmux_calls]
        assert ["tmux", "send-keys"] in sent_cmds
        assert ["tmux", "kill-session"] in sent_cmds

    @patch("subprocess.run")
    def test_non_matching_status_not_killed(self, mock_subproc, client, mock_tmux, patched_server):
        """Sessions with non-matching status should not be killed."""
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session("thinking-sess", "test-proj", "thinking-sess")
        # Seed the unified session-state cache directly. The
        # kill-by-status route reads from `session_state`, not
        # from any DB column.
        from common import session_state as _ssn
        with _ssn._lock:
            _ssn._states["thinking-sess"] = {
                "tmux_name": "thinking-sess", "kind": "task",
                "state": "thinking", "detail": "", "ts": "",
                "agent_session_id": "", "project_id": "test-proj",
                "target_id": "thinking-sess", "target_instance": "",
            }
        try:
            resp = client.post("/api/sessions/kill-by-status", json={"statuses": ["stopped", "idle"]})
            assert resp.status_code == 200
            data = resp.json()
            assert data["killed"] == []
            # No tmux calls should target the test session; ignore unrelated
            # background-thread tmux calls (session-state reaper poking
            # test-fixture sessions like `task-d`).
            thinking_calls = [
                c for c in mock_subproc.call_args_list
                if c[0] and isinstance(c[0][0], list)
                and c[0][0][:1] == ["tmux"]
                and "thinking-sess" in c[0][0]
            ]
            assert thinking_calls == []
            # Session should still exist in DB
            assert patched_server._db.get_session("thinking-sess") is not None
        finally:
            with _ssn._lock:
                _ssn._states.pop("thinking-sess", None)

    @patch("routes.sessions._session_states", {})
    @patch("subprocess.run")
    def test_running_session_without_live_state_uses_db_status(
        self, mock_subproc, client, mock_tmux, patched_server
    ):
        """Running session with no live state falls back to DB status field."""
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session("fb-sess", "test-proj", "fb-sess")
        # DB sessions default to 'idle' status if not set explicitly
        patched_server._db.update_session("fb-sess", status="idle")
        resp = client.post("/api/sessions/kill-by-status", json={"statuses": ["idle"]})
        assert resp.status_code == 200
        assert "fb-sess" in resp.json()["killed"]

    @patch("routes.sessions._session_states", {})
    @patch("subprocess.run")
    def test_kill_multiple_sessions(self, mock_subproc, client, mock_tmux, patched_server):
        """Multiple sessions matching different statuses should all be killed."""
        # One stopped (tmux dead), one idle (tmux alive, no live state -> falls back)
        patched_server._db.create_session("s1", "test-proj", "s1")
        patched_server._db.create_session("s2", "test-proj", "s2")
        patched_server._db.update_session("s2", status="idle")
        # s1: tmux dead -> stopped; s2: tmux alive -> fallback to DB "idle"
        mock_tmux["exists"].side_effect = [False, True]
        resp = client.post("/api/sessions/kill-by-status", json={
            "statuses": ["stopped", "idle"],
        })
        assert resp.status_code == 200
        killed = resp.json()["killed"]
        assert "s1" in killed
        assert "s2" in killed


class TestSessionHookHelpers:
    """The three `_apply_*_session_hook` dispatchers each swallow a
    minor failure (history rejection / cron finish raise) so a single
    bad hook payload can't crash the /api/hook handler."""

    def test_kill_session_swallows_review_history_value_error(
        self, patched_server, monkeypatch, mock_tmux,
    ):
        """`kill_session` writes a `session killed` history line on the
        review row. If `append_review_history` raises ValueError (text
        too long, etc.) we still complete the kill -- the session is
        already dead and emitting an error would leave the UI stuck."""
        from routes import sessions as routes_sessions
        url = "https://github.com/x/y/pull/801"
        patched_server._db.upsert_review_pr(
            url=url, repo="x/y", number=801,
            session_name="review-x-y-801", state="open",
            title="t", author="a", source="github",
        )

        def boom(*a, **kw):
            raise ValueError("too long")
        monkeypatch.setattr(
            patched_server._db, "append_review_history", boom,
        )
        # Should not raise.
        routes_sessions.kill_session("review-x-y-801")

    def test_cron_hook_swallows_finish_run_exception(
        self, patched_server, monkeypatch,
    ):
        """The cron-Stop hook calls `finish_run_for_session`. If THAT
        raises (e.g. concurrent supersede), we log + continue rather
        than 500ing the /api/hook endpoint."""
        from routes import sessions as routes_sessions
        from common import cron_jobs as _cron

        def boom(*a, **kw):
            raise RuntimeError("run already closed")
        monkeypatch.setattr(_cron, "finish_run_for_session", boom)
        # Returns True (matched the cron prefix) regardless of the
        # raise inside the try block.
        out = routes_sessions._apply_cron_session_hook(
            "cron-job-7", "Stop", "done",
        )
        assert out is True

    def test_review_hook_swallows_history_value_error(
        self, patched_server, monkeypatch,
    ):
        """Review hook tries to append a `hook X: state` history line.
        ValueError on that write must not break the upsert path."""
        from routes import sessions as routes_sessions
        url = "https://github.com/x/y/pull/802"
        patched_server._db.upsert_review_pr(
            url=url, repo="x/y", number=802,
            session_name="review-x-y-802", state="open",
            title="t", author="a", source="github",
        )

        def boom(*a, **kw):
            raise ValueError("history overflow")
        monkeypatch.setattr(
            patched_server._db, "append_review_history", boom,
        )
        out = routes_sessions._apply_review_session_hook(
            session="review-x-y-802", event="SessionStart",
            new_state="active",
            data={"session_id": "agent-uuid-abc"},
        )
        assert out is True
