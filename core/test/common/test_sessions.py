"""Unit tests for core/common.sessions.py -- direct calls to core functions
with patched_server + mock_tmux fixtures (no real tmux or GitHub)."""

import json

import common
from unittest.mock import patch, MagicMock

import pytest

from common.sessions import (
    open_session,
    kill_session,
    resume_session,
    list_all_sessions,
    list_sessions,
    get_session_status,
    build_background,
)


def _strip_env_prefix(argv):
    if argv[0] != "env":
        return argv
    i = 1
    while i < len(argv) and "=" in argv[i]:
        i += 1
    return argv[i:]


@pytest.fixture
def claude_new_session(patched_server):
    """Pin the new-session agent to a Claude-family binary so these
    session-mechanics tests keep asserting the stable `-n` /
    `--append-system-prompt` argv shape, independent of whatever
    new-session default a given install configures."""
    from common import agent as _agent
    patched_server._db.set_setting(_agent.KEY_NEW_SESSION_AGENT_IMPL,
                                   "claude")
    yield


# ---------------------------------------------------------------------------
# open_session
# ---------------------------------------------------------------------------

class TestOpenSession:
    """open_session contract: launches tmux+agent with `--append-system-prompt`
    carrying the [Background] block, returns only the action prompt."""

    def _bg_arg(self, mock_tmux):
        """Pull the value of --append-system-prompt out of the launched argv."""
        call = mock_tmux["launch_argv"].call_args
        argv = call.args[2]
        idx = argv.index("--append-system-prompt")
        return argv[idx + 1]

    def test_new_session_creates_and_launches(self, patched_server, mock_tmux, claude_new_session):
        """First open for a task creates session + launches agent with bg in argv."""
        result = open_session(
            task_id="task-b",
            project_id="test-proj",
            action_id="open",
        )
        assert result["new"] is True
        assert result["session"] == "task-b"
        # Action prompt is delivered separately (no [Background] in it)
        assert "[Background]" not in (result["prompt"] or "")
        # Background lands in the system-prompt argv element
        assert "[Background]" in self._bg_arg(mock_tmux)
        mock_tmux["launch_argv"].assert_called_once()

    def test_existing_session_tmux_dead_relaunches(self, patched_server, mock_tmux, claude_new_session):
        """Re-opening a recorded session whose tmux died: relaunches with fresh bg."""
        patched_server._db.create_session("task-b", "test-proj", "task-b")
        mock_tmux["exists"].return_value = False

        result = open_session(
            task_id="task-b",
            project_id="test-proj",
            action_id="open",
        )
        assert result["new"] is False
        assert "[Background]" in self._bg_arg(mock_tmux)
        mock_tmux["launch_argv"].assert_called_once()

    def test_existing_session_tmux_alive_skips_relaunch(self, patched_server, mock_tmux):
        """Session recorded + tmux alive: just returns action prompt, no relaunch."""
        patched_server._db.create_session("task-b", "test-proj", "task-b")
        mock_tmux["exists"].return_value = True

        result = open_session(
            task_id="task-b",
            project_id="test-proj",
            action_id="open",
        )
        assert result["new"] is False
        # No tmux launch -- the agent is already running with the original bg
        mock_tmux["launch_argv"].assert_not_called()

    def test_open_session_with_pr_context(self, patched_server, mock_tmux, claude_new_session):
        """PR context (pr_number + pr_repo) appears in injected background."""
        result = open_session(
            task_id="task-b",
            project_id="test-proj",
            action_id="open",
            pr_number=200,
            pr_repo="example/repo",
        )
        assert result["new"] is True
        bg = self._bg_arg(mock_tmux)
        assert "Focus PR #200:" in bg
        assert "example/repo" in bg

    def test_open_session_project_not_found_raises(self, patched_server, mock_tmux):
        """ValueError raised when project does not exist."""
        import pytest
        with pytest.raises(ValueError, match="Project not found"):
            open_session(
                task_id="task-b",
                project_id="nonexistent-proj",
                action_id="open",
            )

    def test_open_session_action_not_found_raises(self, patched_server, mock_tmux):
        """ValueError raised when action does not exist."""
        import pytest
        with pytest.raises(ValueError, match="Action not found"):
            open_session(
                task_id="task-b",
                project_id="test-proj",
                action_id="nonexistent-action",
            )

    def test_open_session_task_not_found_raises(self, patched_server, mock_tmux):
        """ValueError raised when task does not exist."""
        import pytest
        with pytest.raises(ValueError, match="Task not found"):
            open_session(
                task_id="nonexistent-task",
                project_id="test-proj",
                action_id="open",
            )

    def test_open_session_with_custom_prompt(self, patched_server, mock_tmux):
        """Custom prompt overrides the action prompt template."""
        result = open_session(
            task_id="task-b",
            project_id="test-proj",
            action_id="open",
            custom_prompt="Do something custom",
        )
        assert "Do something custom" in result["prompt"]

    def test_open_session_includes_dependency_statuses(self, patched_server, mock_tmux, claude_new_session):
        """Background system prompt includes dependency status."""
        open_session(
            task_id="task-b",
            project_id="test-proj",
            action_id="open",
        )
        # task-b depends on task-a (done)
        assert "task-a(done)" in self._bg_arg(mock_tmux)


# ---------------------------------------------------------------------------
# kill_session
# ---------------------------------------------------------------------------

class TestKillSession:
    def test_kill_existing_session(self, patched_server, mock_tmux):
        """Kill a session that exists in DB and tmux -- the adapter's
        graceful kill (Ctrl+C then tmux-kill) is invoked so agent gets a
        chance to flush state before termination."""
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session("kill-me", "test-proj", "kill-me")

        with patch("common.sessions.graceful_kill_session") as mock_kill:
            result = kill_session("kill-me")

        assert result["status"] == "killed"
        assert result["session"] == "kill-me"
        mock_kill.assert_called_once_with("kill-me")
        assert patched_server._db.get_session("kill-me") is None

    def test_kill_nonexistent_tmux_session(self, patched_server, mock_tmux):
        """Kill when tmux session does not exist -- skip adapter, just
        clean DB."""
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("ghost", "test-proj", "ghost")

        with patch("common.sessions.graceful_kill_session") as mock_kill:
            result = kill_session("ghost")

        assert result["status"] == "killed"
        mock_kill.assert_not_called()
        assert patched_server._db.get_session("ghost") is None

    def test_kill_emits_session_killed_event(self, patched_server, mock_tmux):
        """Regression: the UI listens for `session.killed` to clear the
        'Killing...' pending state. Without this event the user has to
        manually refresh after each kill."""
        import app_state
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session("sig-kill", "test-proj", "sig-kill")

        collected = []
        app_state.on_event("session.killed", lambda ev: collected.append(ev))

        with patch("common.sessions.graceful_kill_session"):
            kill_session("sig-kill")

        import time
        for _ in range(20):
            if collected:
                break
            time.sleep(0.05)
        assert collected, "kill_session must emit session.killed"
        assert "sig-kill" in collected[0].get("title", "")
        assert collected[0].get("session") == "test-proj"


class TestOpenSessionEvents:
    """Event emission for open_session -- drives UI refresh without
    waiting for agent's SessionStart hook (2-5s latency)."""

    def test_new_session_emits_session_opened(self, patched_server, mock_tmux, claude_new_session):
        import app_state
        mock_tmux["exists"].return_value = False
        collected = []
        app_state.on_event("session.opened", lambda ev: collected.append(ev))

        open_session(task_id="task-b", project_id="test-proj", action_id="open")

        import time
        for _ in range(20):
            if collected:
                break
            time.sleep(0.05)
        assert collected, "open_session must emit session.opened on new session"
        assert "task-b" in collected[0].get("title", "")


# ---------------------------------------------------------------------------
# list_all_sessions
# ---------------------------------------------------------------------------

class TestListAllSessions:
    def test_empty_when_no_sessions(self, patched_server, mock_tmux):
        result = list_all_sessions()
        assert result == {}

    def test_groups_sessions_by_project(self, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("sess-1", "test-proj", "sess-1")
        patched_server._db.create_session("sess-2", "empty-proj", "sess-2")

        result = list_all_sessions()
        assert "test-proj" in result
        assert "empty-proj" in result
        assert result["test-proj"]["name"] == "Test Project"
        assert result["empty-proj"]["name"] == "Empty Project"
        assert len(result["test-proj"]["sessions"]) == 1
        assert len(result["empty-proj"]["sessions"]) == 1

    def test_includes_running_flag(self, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session("running-sess", "test-proj", "running-sess")

        result = list_all_sessions()
        sess = result["test-proj"]["sessions"][0]
        assert sess["running"] is True

    def test_skips_non_task_session_rows(self, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("task-sess", "test-proj", "task-sess")
        patched_server._db.create_session("ticket-OPS-1", "", "ticket-OPS-1")
        patched_server._db.create_session("review-repo-1", "", "review-repo-1")
        patched_server._db.create_session("cron-job-1", "", "cron-job-1")

        result = list_all_sessions()
        assert list(result.keys()) == ["test-proj"]
        assert [s["tmux_name"] for s in result["test-proj"]["sessions"]] == [
            "task-sess",
        ]


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

class TestListSessions:
    def test_list_sessions_all(self, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("s1", "test-proj", "s1")
        patched_server._db.create_session("s2", "empty-proj", "s2")

        result = list_sessions()
        assert len(result) == 2

    def test_list_sessions_filtered_by_project(self, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("s1", "test-proj", "s1")
        patched_server._db.create_session("s2", "empty-proj", "s2")

        result = list_sessions(project="test-proj")
        assert len(result) == 1
        assert result[0]["task_id"] == "s1"


# ---------------------------------------------------------------------------
# get_session_status
# ---------------------------------------------------------------------------

class TestGetSessionStatus:
    def test_session_in_db_and_running(self, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session("status-sess", "test-proj", "status-sess")

        result = get_session_status("status-sess")
        assert result["session"] == "status-sess"
        assert result["running"] is True
        assert result["exists_in_db"] is True

    def test_session_in_db_not_running(self, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("dead-sess", "test-proj", "dead-sess")

        result = get_session_status("dead-sess")
        assert result["running"] is False
        assert result["exists_in_db"] is True

    def test_session_not_in_db(self, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = False

        result = get_session_status("unknown-sess")
        assert result["running"] is False
        assert result["exists_in_db"] is False
        assert result["status"] == "not_found"

    def test_status_prefers_in_memory_live_state_over_db(self, patched_server, mock_tmux):
        """The /api/hook handler writes `_session_states[name]` BEFORE
        the DB. Reading DB only would surface a stale status the user
        sees as 'idle' while the agent is mid-response. Live overlay fixes
        the perceived UI lag."""
        from routes import sessions as sess_routes
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session("live-sess", "test-proj", "live-sess")
        patched_server._db.update_session("live-sess", status="idle")
        sess_routes._session_states["live-sess"] = {
            "state": "thinking", "detail": "", "ts": "2026-04-26T00:00:00",
        }
        try:
            result = get_session_status("live-sess")
            assert result["status"] == "thinking"
        finally:
            sess_routes._session_states.pop("live-sess", None)

    def test_status_unknown_when_cache_empty_but_session_in_db(
        self, patched_server, mock_tmux,
    ):
        """No hook fired yet AND the cache has no entry for this row.
        The new contract: status='unknown' (a session row exists but
        we don't know its live state). The previous DB-column fallback
        is gone -- the column was the main drift source."""
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session("cold-sess", "test-proj", "cold-sess")
        # DB-side update is irrelevant now.
        patched_server._db.update_session("cold-sess", status="thinking")
        result = get_session_status("cold-sess")
        assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# resume_session
# ---------------------------------------------------------------------------

class TestResumeSession:
    """host recovery reboot leaves agent session files intact but kills every tmux.
    `resume_session` should relaunch tmux with the same name and, when we
    have an agent session UUID on record, run `agent resume <uuid>` inside
    so the conversation history is restored."""

    def test_raises_on_missing_db_row(self, patched_server, mock_tmux):
        with pytest.raises(ValueError, match="Session not found"):
            resume_session("ghost-sess")

    def test_noop_when_tmux_already_running(self, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session("alive-sess", "test-proj", "alive-sess")
        result = resume_session("alive-sess")
        assert result["action"] == "noop"
        assert result["running"] is True
        # Shouldn't spawn a second tmux.
        mock_tmux["launch_argv"].assert_not_called()

    def test_resumes_by_uuid_when_recorded(self, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("u-sess", "test-proj", "u-sess")
        patched_server._db.update_session(
            "u-sess", agent_session_id="11111111-2222-3333-4444-555555555555",
        )
        result = resume_session("u-sess")
        assert result["action"] == "resumed"
        # A transcript UUID resumes the LOCAL conversation via
        # `--resume` (cloud-independent), NOT the cloud `resume`
        # subcommand -- the latter dies with "No sessions found" once
        # the cloud entry is gone.
        call = mock_tmux["launch_argv"].call_args
        argv = call.args[2] if len(call.args) >= 3 else call.kwargs.get("argv")
        argv = _strip_env_prefix(argv)
        # Legacy row (empty agent_impl) resumes with the default agent.
        # A transcript UUID always uses `--resume` (cwd-local resume).
        assert argv[0] == "claude"
        assert argv[1:] == ["--resume", "11111111-2222-3333-4444-555555555555"]

    def test_local_resume_launches_from_transcript_cwd(
        self, patched_server, mock_tmux, tmp_path, monkeypatch,
    ):
        """Local resume must launch from the directory the session was
        created in (recovered from the transcript), since
        `claude --resume` is cwd-sensitive."""
        import common.sessions as _sessions
        uuid = "abcdef01-2345-6789-abcd-ef0123456789"
        proj_dir = tmp_path / "-home-yicong-huang-spark-wt"
        proj_dir.mkdir()
        (proj_dir / f"{uuid}.jsonl").write_text(
            json.dumps({"cwd": "/home/yicong.huang/spark-wt", "type": "user"}) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(_sessions, "CLAUDE_PROJECTS_DIR", str(tmp_path))
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("wt-sess", "test-proj", "wt-sess")
        patched_server._db.update_session("wt-sess", agent_session_id=uuid)
        resume_session("wt-sess")
        call = mock_tmux["launch_argv"].call_args
        assert call.args[1] == "/home/yicong.huang/spark-wt"

    def test_relaunches_fresh_when_no_uuid(self, patched_server, mock_tmux):
        """Legacy session row without an agent_session_id -- start fresh
        so the card at least comes back alive. The UI surfaces this as a
        'history not resumed' warning."""
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("legacy", "test-proj", "legacy")
        result = resume_session("legacy")
        assert result["action"] == "relaunched"
        call = mock_tmux["launch_argv"].call_args
        argv = call.args[2] if len(call.args) >= 3 else call.kwargs.get("argv")
        argv = _strip_env_prefix(argv)
        # Legacy row (empty agent_impl) relaunches with the default agent.
        assert argv[0] == "claude" and argv[1] == "-n"
        assert argv[2] == "legacy"

    def test_defaults_to_home_working_dir(self, patched_server, mock_tmux):
        """Projects currently have no `working_dir` column -- resume should
        fall through to the `~` default without crashing."""
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("wd-sess", "test-proj", "wd-sess")
        patched_server._db.update_session(
            "wd-sess", agent_session_id="aaaa-bbbb-cccc-dddd",
        )
        resume_session("wd-sess")
        call = mock_tmux["launch_argv"].call_args
        # tmux_launch_session_argv(name, working_dir, argv) -- expect default.
        assert call.args[1] == "~"


# ---------------------------------------------------------------------------
# build_background -- with design_doc
# ---------------------------------------------------------------------------

class TestBuildBackgroundDesignDoc:
    def test_design_doc_included_in_background(self, patched_server):
        task_data = {
            "task_id": "doc-task",
            "description": "Task with design doc",
            "status": "not_started",
            "dependencies": [],
            "prs": [],
        }
        result = build_background(
            task_data, "Test Project", "Do the work.", {},
            design_doc="https://docs.google.com/document/d/1234",
        )
        assert "Design:" in result
        assert "https://docs.google.com/document/d/1234" in result
        assert "read and respect" in result

    def test_no_design_doc_omitted(self, patched_server):
        task_data = {
            "task_id": "no-doc-task",
            "description": "Task without design doc",
            "status": "not_started",
            "dependencies": [],
            "prs": [],
        }
        result = build_background(
            task_data, "Test Project", "Do the work.", {},
            design_doc=None,
        )
        assert "Design:" not in result

    def test_build_background_with_all_fields(self, patched_server):
        """Full background with deps, prs, pr_context, and design_doc."""
        task_data = {
            "task_id": "full-task",
            "description": "Full featured task",
            "status": "in_progress",
            "ticket_id": "EX-12345",
            "ticket_url": "https://issues.example.org/jira/browse/EX-12345",
            "dependencies": ["dep-a", "dep-b"],
            "prs": [
                {"number": 100, "status": "open", "title": "PR one",
                 "url": "https://github.com/example/repo/pull/100",
                 "head_branch": "EX-12345", "ci_status": "success",
                 "review_status": "approved"},
            ],
        }
        dep_statuses = {"dep-a": "done", "dep-b": "in_progress"}
        pr_context = {"number": 100, "repo": "example/repo"}

        result = build_background(
            task_data, "My Project", "Fix the CI.", dep_statuses,
            pr_context=pr_context,
            design_doc="https://docs.google.com/doc/design",
        )
        assert "[Background]" in result
        assert "EX-12345" in result
        assert "dep-a(done)" in result
        assert "dep-b(in_progress)" in result
        assert "Focus PR #100:" in result
        assert "Design:" in result
        assert "[Action]" in result
        assert "Fix the CI." in result

    def test_build_background_empty_prompt(self, patched_server):
        """Empty prompt_template means no [Action] section."""
        task_data = {
            "task_id": "no-action-task",
            "description": "No action",
            "status": "not_started",
            "dependencies": [],
            "prs": [],
        }
        result = build_background(task_data, "Proj", "", {})
        assert "[Background]" in result
        # Empty prompt_template should not produce [Action]
        assert "[Action]" not in result

    def test_build_background_pr_without_url(self, patched_server):
        """PR without explicit url falls back to a generated URL."""
        task_data = {
            "task_id": "pr-no-url",
            "description": "PR missing URL",
            "status": "in_review",
            "dependencies": [],
            "prs": [
                {"number": 42, "status": "open", "title": "No URL PR"},
            ],
        }
        result = build_background(task_data, "Proj", "Review.", {})
        assert "PR:" in result
        assert "42" in result


# ---------------------------------------------------------------------------
# send_terminal_input
# ---------------------------------------------------------------------------

# `send_terminal_input` removed: it was only ever called from these
# tests, never from a route, CLI, or MCP handler. Use `adapters.tmux.send_keys`
# (which appends Enter) for actual terminal input.
