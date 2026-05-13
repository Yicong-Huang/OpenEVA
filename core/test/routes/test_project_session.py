"""Tests for the project-manager session: long-lived agent that audits +
suggests for a project, never writes code.

Backend covers:
- build_project_background_system content (role + boundaries + eva-cli hint)
- open_project_session: launches new + idempotent on re-open
- get_project_session: live tmux probe vs. DB row
- kill_project_session: tmux kill + DB row removal + event
- /api/projects/{pid}/manager routes (open / get / kill / run)
"""

from unittest.mock import patch


# ---------------------------------------------------------------------------
# build_project_background_system
# ---------------------------------------------------------------------------

class TestBuildProjectBackground:
    def test_includes_role_and_boundaries(self, patched_server):
        from common.sessions import build_project_background_system
        proj = {"id": "p1", "name": "P One", "description": "d", "has_tickets": True}
        bg = build_project_background_system(proj, {})
        # Role line + don't-write-code boundary
        assert "[Role]" in bg
        assert "project manager" in bg.lower()
        assert "[Boundaries]" in bg
        assert "Do NOT" in bg

    def test_includes_status_counts_and_sample_tasks(self, patched_server):
        from common.sessions import build_project_background_system
        proj = {"id": "p1", "name": "P One"}
        tasks = {
            "a": {"task_id": "a", "status": "done"},
            "b": {"task_id": "b", "status": "in_progress"},
            "c": {"task_id": "c", "status": "in_progress"},
        }
        bg = build_project_background_system(proj, tasks)
        assert "done=1" in bg
        assert "in_progress=2" in bg
        # Sample task ids appear in the snapshot.
        assert "a" in bg and "b" in bg

    def test_includes_eva_cli_pointers(self, patched_server):
        from common.sessions import build_project_background_system
        proj = {"id": "demo-proj", "name": "Demo"}
        bg = build_project_background_system(proj, {})
        assert "eva-cli" in bg
        assert "list-tasks" in bg
        assert "list-prs" in bg
        # Project id is interpolated into the example commands.
        assert "demo-proj" in bg

    def test_design_doc_line_renders_when_present(self, patched_server):
        # The PM background renders a `Design doc:` line ONLY when the
        # project carries a design_doc URL. Otherwise the line is
        # omitted to keep the prompt tight.
        from common.sessions import build_project_background_system
        proj = {"id": "p", "name": "P",
                "design_doc": "https://docs.google.com/d/PROJ-DOC"}
        bg = build_project_background_system(proj, {})
        assert "Design doc: https://docs.google.com/d/PROJ-DOC" in bg

    def test_design_doc_line_omitted_when_blank(self, patched_server):
        from common.sessions import build_project_background_system
        proj = {"id": "p", "name": "P", "design_doc": ""}
        bg = build_project_background_system(proj, {})
        assert "Design doc:" not in bg


# ---------------------------------------------------------------------------
# open_project_session
# ---------------------------------------------------------------------------

class TestOpenProjectSession:
    def test_new_project_launches_tmux_with_argv(self, patched_server, mock_tmux):
        from common.sessions import open_project_session
        info = open_project_session("test-proj")
        assert info["project_id"] == "test-proj"
        assert info["tmux_name"] == "pm-test-proj"
        # Launched via argv (not send-keys) so multi-line bg works.
        mock_tmux["launch_argv"].assert_called_once()
        argv = mock_tmux["launch_argv"].call_args.args[2]
        assert argv[0] in ("agent", "claude")  # active agent binary
        # Bg is in --append-system-prompt and contains Role marker.
        bg = argv[argv.index("--append-system-prompt") + 1]
        assert "[Role]" in bg
        # DB row created.
        assert patched_server._db.get_project_session("test-proj") is not None

    def test_reopen_existing_tmux_skips_relaunch(self, patched_server, mock_tmux):
        from common.sessions import open_project_session
        # First open creates the row + launches.
        open_project_session("test-proj")
        mock_tmux["launch_argv"].reset_mock()
        # Second open with tmux still alive: no relaunch.
        mock_tmux["exists"].return_value = True
        open_project_session("test-proj")
        mock_tmux["launch_argv"].assert_not_called()

    def test_tmux_dead_relaunches_with_fresh_bg(self, patched_server, mock_tmux):
        from common.sessions import open_project_session
        # Seed an existing record but tmux is dead.
        patched_server._db.create_project_session("test-proj", "pm-test-proj")
        mock_tmux["exists"].return_value = False
        open_project_session("test-proj")
        mock_tmux["launch_argv"].assert_called_once()

    def test_unknown_project_raises_value_error(self, patched_server, mock_tmux):
        import pytest
        from common.sessions import open_project_session
        with pytest.raises(ValueError, match="Project not found"):
            open_project_session("ghost-project-xyz")

    def test_emits_session_opened_event_on_first_open(self, patched_server, mock_tmux):
        from common.sessions import open_project_session
        emitted = []
        with patch("app_state.emit_event",
                   side_effect=lambda t, d, **k: emitted.append((t, d, k))):
            open_project_session("test-proj")
        types = [e[0] for e in emitted]
        assert "session.opened" in types


# ---------------------------------------------------------------------------
# get_project_session
# ---------------------------------------------------------------------------

class TestGetProjectSession:
    def test_returns_none_when_no_row(self, patched_server, mock_tmux):
        from common.sessions import get_project_session
        assert get_project_session("test-proj") is None

    def test_returns_record_with_running_flag(self, patched_server, mock_tmux):
        from common.sessions import get_project_session, open_project_session
        open_project_session("test-proj")
        mock_tmux["exists"].return_value = True
        info = get_project_session("test-proj")
        assert info is not None
        assert info["running"] is True
        mock_tmux["exists"].return_value = False
        info2 = get_project_session("test-proj")
        assert info2["running"] is False


# ---------------------------------------------------------------------------
# kill_project_session
# ---------------------------------------------------------------------------

class TestKillProjectSession:
    def test_kill_removes_row_and_kills_tmux(self, patched_server, mock_tmux):
        from common.sessions import kill_project_session, open_project_session
        open_project_session("test-proj")
        mock_tmux["exists"].return_value = True
        with patch("common.sessions.graceful_kill_session") as gk:
            r = kill_project_session("test-proj")
        assert r["killed"] is True
        gk.assert_called_once_with("pm-test-proj")
        assert patched_server._db.get_project_session("test-proj") is None

    def test_kill_when_no_record(self, patched_server, mock_tmux):
        from common.sessions import kill_project_session
        r = kill_project_session("test-proj")
        assert r["killed"] is False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class TestRoutes:
    def test_open_route(self, client, patched_server, mock_tmux):
        resp = client.post("/api/projects/test-proj/manager")
        assert resp.status_code == 200
        d = resp.json()
        assert d["project_id"] == "test-proj"
        assert d["tmux_name"] == "pm-test-proj"

    def test_open_route_unknown_project(self, client, patched_server, mock_tmux):
        resp = client.post("/api/projects/no-such-project/manager")
        assert resp.status_code == 404

    def test_get_route_404_when_no_session(self, client, patched_server, mock_tmux):
        resp = client.get("/api/projects/test-proj/manager")
        assert resp.status_code == 404

    def test_get_route_after_open(self, client, patched_server, mock_tmux):
        client.post("/api/projects/test-proj/manager")
        resp = client.get("/api/projects/test-proj/manager")
        assert resp.status_code == 200
        assert resp.json()["project_id"] == "test-proj"

    def test_kill_route(self, client, patched_server, mock_tmux):
        client.post("/api/projects/test-proj/manager")
        with patch("common.sessions.graceful_kill_session"):
            resp = client.delete("/api/projects/test-proj/manager")
        assert resp.status_code == 200
        assert resp.json()["killed"] is True

    def test_run_action_sends_keys_to_tmux(self, client, patched_server, mock_tmux):
        # Open first so session exists.
        client.post("/api/projects/test-proj/manager")
        mock_tmux["exists"].return_value = True
        with patch("routes.sessions.send_keys") as send_keys:
            resp = client.post("/api/projects/test-proj/manager/run",
                               json={"prompt": "audit anomalies"})
        assert resp.status_code == 200
        d = resp.json()
        assert d["ok"] is True
        assert d["ran"] is True
        send_keys.assert_called_once_with("pm-test-proj", "audit anomalies")

    def test_run_action_auto_opens_when_not_running(
            self, client, patched_server, mock_tmux):
        # No session yet -- run should auto-open then send.
        mock_tmux["exists"].return_value = False  # tmux probe says not running
        with patch("routes.sessions.send_keys"):
            resp = client.post("/api/projects/test-proj/manager/run",
                               json={"prompt": "x"})
        assert resp.status_code == 200
        # open_project_session called -> launch_argv invoked.
        mock_tmux["launch_argv"].assert_called_once()

    def test_run_action_empty_prompt_reports_ran_false(
            self, client, patched_server, mock_tmux):
        """Regression: used to claim `ran=True` whenever the prompt was
        truthy, even if we never actually sent keys. An empty prompt is
        a no-op and the response must reflect that."""
        client.post("/api/projects/test-proj/manager")
        mock_tmux["exists"].return_value = True
        with patch("routes.sessions.send_keys") as send_keys:
            resp = client.post("/api/projects/test-proj/manager/run",
                               json={"prompt": ""})
        assert resp.status_code == 200
        assert resp.json()["ran"] is False
        send_keys.assert_not_called()

    def test_run_action_dead_session_reports_ran_false(
            self, client, patched_server, mock_tmux):
        """Regression: if tmux says the session isn't running (e.g. agent
        crashed right after launch), we must NOT claim we sent keys and
        must NOT actually call send_keys."""
        # First open succeeds (exists flips True after launch),
        # but by the time run_action probes, session is dead.
        mock_tmux["exists"].return_value = False
        with patch("routes.sessions.send_keys") as send_keys:
            resp = client.post("/api/projects/test-proj/manager/run",
                               json={"prompt": "sync project"})
        assert resp.status_code == 200
        assert resp.json()["ran"] is False
        send_keys.assert_not_called()
