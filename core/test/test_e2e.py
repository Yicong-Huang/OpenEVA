"""End-to-end tests for the session/action flow."""

import json
from unittest.mock import MagicMock, patch

import common
import common.session_state


class TestFullActionFlow:
    """End-to-end action delivery: open + evaluate. Background lives in the
    agent --append-system-prompt argv (no longer in the prompt return)."""

    @staticmethod
    def _bg_arg(mock_tmux):
        argv = mock_tmux["launch_argv"].call_args.args[2]
        return argv[argv.index("--append-system-prompt") + 1]

    def test_full_action_flow(self, client, patched_server, mock_tmux):
        patched_server._db.create_task(
            "test-proj", "e2e-task-1", description="E2E task one", type="feature"
        )
        mock_tmux["exists"].return_value = False

        # Step 1: first open launches agent with bg in argv
        resp = client.post("/api/sessions/open", json={
            "task_id": "e2e-task-1",
            "project_id": "test-proj",
            "action_id": "do-task",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["new"] is True
        assert "[Background]" not in (data["prompt"] or "")
        assert "[Background]" in self._bg_arg(mock_tmux)

        # Step 2: open again with a different action, tmux still alive ->
        # no relaunch, just returns the new action's prompt template.
        mock_tmux["exists"].return_value = True
        mock_tmux["launch_argv"].reset_mock()
        resp2 = client.post("/api/sessions/open", json={
            "task_id": "e2e-task-1",
            "project_id": "test-proj",
            "action_id": "evaluate",
        })
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["new"] is False
        evaluate_action = patched_server._db.get_action("evaluate")
        assert data2["prompt"] == evaluate_action["prompt_template"]
        mock_tmux["launch_argv"].assert_not_called()


class TestSessionSurvivesKillAndReopen:
    """Open session, kill tmux, open again -> relaunches with fresh bg in argv."""

    def test_session_survives_kill_and_reopen(self, client, patched_server, mock_tmux):
        patched_server._db.create_task(
            "test-proj", "e2e-kill-task", description="Kill and reopen task"
        )

        # First open: creates session, launches agent
        mock_tmux["exists"].return_value = False
        resp = client.post("/api/sessions/open", json={
            "task_id": "e2e-kill-task",
            "project_id": "test-proj",
            "action_id": "do-task",
        })
        assert resp.status_code == 200
        assert resp.json()["new"] is True
        assert mock_tmux["launch_argv"].call_count == 1

        # tmux dies, open again -> session known but tmux gone, relaunches
        mock_tmux["launch_argv"].reset_mock()
        mock_tmux["exists"].return_value = False
        resp2 = client.post("/api/sessions/open", json={
            "task_id": "e2e-kill-task",
            "project_id": "test-proj",
            "action_id": "do-task",
        })
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["new"] is False
        # Bg is re-injected on relaunch
        argv = mock_tmux["launch_argv"].call_args.args[2]
        bg = argv[argv.index("--append-system-prompt") + 1]
        assert "[Background]" in bg


class TestRenamePreservesSession:
    """Create task, open session, rename task, verify old session is gone and
    a new session is created under the new task_id."""

    def test_rename_preserves_session(self, client, patched_server, mock_tmux):
        patched_server._db.create_task(
            "test-proj", "e2e-rename-old", description="Task to rename"
        )

        # Open session for old task
        mock_tmux["exists"].return_value = False
        resp = client.post("/api/sessions/open", json={
            "task_id": "e2e-rename-old",
            "project_id": "test-proj",
            "action_id": "do-task",
        })
        assert resp.status_code == 200
        assert patched_server._db.get_session("e2e-rename-old") is not None

        # Rename the task
        rename_resp = client.post(
            "/api/projects/test-proj/tasks/e2e-rename-old/rename",
            json={"new_id": "e2e-rename-new"},
        )
        assert rename_resp.status_code == 200
        assert rename_resp.json()["task_id"] == "e2e-rename-new"

        # Old session should be gone, new one created
        old_session = patched_server._db.get_session("e2e-rename-old")
        assert old_session is None
        new_session = patched_server._db.get_session("e2e-rename-new")
        assert new_session is not None
        assert new_session["task_id"] == "e2e-rename-new"


class TestActionsFilterByCondition:
    """GET PR-context actions; fix-ci should have condition ci_failed,
    and should appear in a PR-context action list."""

    def test_actions_filter_by_condition(self, client):
        resp = client.get("/api/actions?context=pr")
        assert resp.status_code == 200
        data = resp.json()
        actions = data["actions"]

        # All returned actions should be either pr or all context
        for a in actions:
            assert a["context"] in ("pr", "all")

        # fix-ci must be present and have condition=ci_failed
        fix_ci = next((a for a in actions if a["id"] == "fix-ci"), None)
        assert fix_ci is not None, "fix-ci action not found in pr-context actions"
        assert fix_ci["condition"] == "ci_failed"

    def test_fix_ci_not_in_task_context(self, client):
        """fix-ci is pr-context only, should not appear when filtering by task."""
        resp = client.get("/api/actions?context=task")
        assert resp.status_code == 200
        actions = resp.json()["actions"]
        ids = [a["id"] for a in actions]
        assert "fix-ci" not in ids

    def test_do_task_in_task_context(self, client):
        resp = client.get("/api/actions?context=task")
        assert resp.status_code == 200
        actions = resp.json()["actions"]
        ids = [a["id"] for a in actions]
        assert "do-task" in ids


class TestFullSyncCycle:
    """POST /api/all-prs/sync with mocked gh_run updates PR data in the DB."""

    def test_full_sync_cycle(self, client, patched_server):
        """Create a task with a PR, call sync, verify PR fields are updated."""
        # task-a/PR #100 is already in the DB via conftest.
        # We patch gh_run so sync returns rich data for PR #100.
        pr_view_payload = json.dumps({
            "number": 100,
            "title": "[EX-100] Foundation work",
            "state": "MERGED",
            "url": "https://github.com/example/repo/pull/100",
            "updatedAt": "2026-01-01T00:00:00Z",
            "additions": 42,
            "deletions": 5,
            "comments": [],
            "reviews": [],
            "headRefName": "branch-repo-100",
            "baseRefName": "master",
            "author": {"login": "test-author"},
            "statusCheckRollup": [],
            "reviewDecision": "APPROVED",
        })

        search_result = json.dumps([])  # no new PRs to discover

        def fake_gh_run(args, repo="", timeout=20):
            mock = MagicMock()
            mock.returncode = 0
            if "search" in args:
                mock.stdout = search_result
            else:
                # gh pr view
                mock.stdout = pr_view_payload
            return mock

        with patch("server.gh_run", side_effect=fake_gh_run):
            resp = client.post("/api/all-prs/sync")

        assert resp.status_code == 200
        data = resp.json()
        assert "updated" in data
        assert data["updated"] >= 1

        # Verify PR #100 was updated in the DB
        pr = patched_server._db.find_pr_by_number(100)
        assert pr is not None
        assert pr["additions"] == 42
        assert pr["deletions"] == 5
        assert pr["author"] == "test-author"
        assert pr["status"] == "merged"


class TestOpenSessionAfterDbTaskDeleted:
    """Create task, open session, delete task from DB, try to open again -> 404."""

    def test_open_session_after_db_task_deleted(self, client, patched_server, mock_tmux):
        patched_server._db.create_task(
            "test-proj", "deleted-task", description="Will be deleted", type="feature"
        )

        # Open session successfully
        mock_tmux["exists"].return_value = False
        resp = client.post("/api/sessions/open", json={
            "task_id": "deleted-task",
            "project_id": "test-proj",
            "action_id": "do-task",
        })
        assert resp.status_code == 200

        # Delete the task from the DB
        patched_server._db.delete_task("test-proj", "deleted-task")

        # Try to open session again -> task no longer exists, expect 404
        mock_tmux["exists"].return_value = True
        resp2 = client.post("/api/sessions/open", json={
            "task_id": "deleted-task",
            "project_id": "test-proj",
            "action_id": "do-task",
        })
        assert resp2.status_code == 404


class TestHookToTaskStatusFlow:
    """Send a hook Stop event, verify _session_states and config_db are updated."""

    def test_hook_to_task_status_flow(self, client, patched_server, mock_tmux):
        # Create a session in config_db via the open endpoint
        patched_server._db.create_task(
            "test-proj", "hook-task", description="Hook test task", type="feature"
        )
        mock_tmux["exists"].return_value = False
        open_resp = client.post("/api/sessions/open", json={
            "task_id": "hook-task",
            "project_id": "test-proj",
            "action_id": "do-task",
        })
        assert open_resp.status_code == 200

        # The session name used by the server is the task_id
        session_name = "hook-task"

        # Verify session exists in config_db
        session = patched_server._db.get_session(session_name)
        assert session is not None

        # Send a Stop hook event
        hook_resp = client.post("/api/hook", json={
            "session": session_name,
            "event": "Stop",
            "data": {},
        })
        assert hook_resp.status_code == 200
        assert hook_resp.json()["ok"] is True

        # The unified session-state cache reflects idle. The DB
        # column is no longer written -- the cache (mirroring tmux +
        # agent hooks) is the only source of state.
        from common import session_state
        row = common.session_state.get(session_name)
        assert row is not None
        assert row["state"] == "idle"
