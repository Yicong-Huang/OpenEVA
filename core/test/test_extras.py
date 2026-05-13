"""Extra tests for additional coverage: list_all_project_sessions,
list_all_sessions (PR-based), all-prs endpoint, static routes,
and the GitHub-account router."""

import common
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---- list_all_project_sessions ----

class TestListAllProjectSessions:
    def test_returns_grouped_sessions(self, client, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("sess-x", "test-proj", "sess-x")
        resp = client.get("/api/all-sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "test-proj" in data
        assert data["test-proj"]["name"] == "Test Project"
        assert len(data["test-proj"]["sessions"]) == 1

    def test_returns_empty_for_no_sessions(self, client, mock_tmux):
        mock_tmux["exists"].return_value = False
        resp = client.get("/api/all-sessions")
        assert resp.status_code == 200
        data = resp.json()
        # No sessions saved, so no projects should appear
        assert isinstance(data, dict)

    def test_session_task_with_deps_gets_deps_attached(
        self, client, patched_server, mock_tmux
    ):
        """When a session points at a real task, the response must embed
        the task AND each of its direct dependencies so the frontend can
        compute `isTaskBlocked` without a second request.

        test-proj seeds task-c depends on task-b; a session on task-c
        should surface both tasks under .test-proj.tasks.
        """
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("task-c", "test-proj", "task-c")
        resp = client.get("/api/all-sessions")
        assert resp.status_code == 200
        data = resp.json()
        tasks = data["test-proj"]["tasks"]
        # Session's own task must be loaded.
        assert "task-c" in tasks
        # Direct dependency task-b must also be loaded (dep fan-out).
        assert "task-b" in tasks
        # The session's task should carry the attached session info so
        # TaskCard can render SessionCard from task.session.
        assert "session" in tasks["task-c"]
        assert tasks["task-c"]["session"]["name"] == "task-c"

    def test_session_payload_carries_db_columns_not_live_overlay(
        self, client, patched_server, mock_tmux,
    ):
        """`/api/all-sessions` returns the DB row + a `running` flag --
        no live-state overlay. The frontend reads live state from
        `/api/sessions/snapshot` (the consolidated session-status
        service) so double-stamping `status` here was just two copies
        of the same data going stale at different rates."""
        from routes import sessions as routes_sessions
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session(
            "live-state-task", "test-proj", "live-state-task",
        )
        # Stamp a live state. The /api/all-sessions response should
        # NOT inherit it any more -- it's the snapshot service's job.
        routes_sessions._session_states["live-state-task"] = {
            "state": "thinking", "detail": "", "ts": 0,
        }
        try:
            resp = client.get("/api/all-sessions")
            assert resp.status_code == 200
            row = next(s for s in resp.json()["test-proj"]["sessions"]
                       if s.get("tmux_name") == "live-state-task")
            # `running` is still computed from tmux probe -- that's
            # cheap and useful for the chip's existence indicator.
            assert row["running"] is True
            # `status` is the DB column ('not_started' on a freshly
            # created session). Live overlay is gone.
            assert row["status"] != "thinking"
        finally:
            routes_sessions._session_states.pop("live-state-task", None)


# ---- static file routes ----

class TestStaticRoutes:
    def test_index_route(self, client):
        resp = client.get("/")
        # Should return the index.html file (200 if it exists, or 500 if not)
        # Since we are using the real static dir, it should exist
        assert resp.status_code == 200


# ---- usage history ----

class TestUsageHistory:
    def test_usage_history_empty(self, tmp_path, monkeypatch):
        """GET /api/usage/history returns empty history list with temp DB."""
        import server
        from starlette.testclient import TestClient

        import app_state, routes.system
        from common import system as common_system
        db_path = tmp_path / "usage.db"
        monkeypatch.setattr(app_state, "_USAGE_DB_PATH", db_path)
        monkeypatch.setattr(routes.system, "_USAGE_DB_PATH", db_path)
        monkeypatch.setattr(common_system, "_USAGE_DB_PATH", db_path)
        common_system._init_usage_db()

        test_client = TestClient(server.app)
        resp = test_client.get("/api/usage/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["history"] == []
        assert data["total_records"] == 0


# ---- gh_account_for_repo ----

class TestGhAccountForRepo:
    """Account-resolver tests with monkeypatched rules so the
    assertions describe the routing logic, not the maintainer's
    specific two-account split. Tests duplicated in test_app_state.py
    are kept here as a regression check that the resolver is
    re-exported via `server.gh_account_for_repo`."""

    def _setup(self, monkeypatch, request):
        from _test_constants import (
            TEST_USER_LOGIN, TEST_USER_LOGIN_ALT, TEST_COMPANY_ORG,
        )
        from adapters import github as gh
        monkeypatch.setattr(gh, "_account_rules", [
            {"match": TEST_COMPANY_ORG, "account": TEST_USER_LOGIN_ALT},
            {"match": "", "account": TEST_USER_LOGIN},  # catch-all
        ])
        # `_gh_tokens` is a module-level dict imported by other modules
        # (`app_state._gh_tokens` is the same object), so we can't
        # `monkeypatch.setattr` the binding -- we mutate in place and
        # restore via a request finalizer so the next test sees a clean
        # tokens table.
        original = dict(gh._gh_tokens)
        gh._gh_tokens.clear()
        gh._gh_tokens.update({
            TEST_USER_LOGIN: "tok",
            TEST_USER_LOGIN_ALT: "tok",
        })

        def _restore():
            gh._gh_tokens.clear()
            gh._gh_tokens.update(original)
        request.addfinalizer(_restore)
        return TEST_USER_LOGIN, TEST_USER_LOGIN_ALT

    def test_company_repo(self, patched_server, monkeypatch, request):
        from _test_constants import TEST_COMPANY_REPO_RUNTIME
        _, alt = self._setup(monkeypatch, request)
        assert patched_server.gh_account_for_repo(
            TEST_COMPANY_REPO_RUNTIME) == alt

    def test_oss_repo(self, patched_server, monkeypatch, request):
        from _test_constants import TEST_OSS_REPO
        login, _ = self._setup(monkeypatch, request)
        assert patched_server.gh_account_for_repo(TEST_OSS_REPO) == login

    def test_unknown_repo_falls_back_to_catch_all(
        self, patched_server, monkeypatch, request,
    ):
        login, _ = self._setup(monkeypatch, request)
        assert patched_server.gh_account_for_repo("some/repo") == login
