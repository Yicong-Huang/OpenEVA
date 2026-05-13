# tests/test_actions_api.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import common
import common.session_state


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Must patch BEFORE importing server module's globals
    # server.__setattr__ propagates to app_state automatically
    from eva_db import EvaDB
    test_db = EvaDB(str(tmp_path / "eva.db"))

    # Seed projects so open_session can find them via get_project()
    test_db.create_project("test-proj", name="Test Project",
                           description="A test project", repo="repo")

    monkeypatch.setattr("server._db", test_db)

    from server import app
    return TestClient(app)


def test_get_actions_all(client):
    resp = client.get("/api/actions")
    assert resp.status_code == 200
    data = resp.json()
    assert "actions" in data
    assert len(data["actions"]) >= 9


def test_get_actions_task_context(client):
    resp = client.get("/api/actions?context=task")
    assert resp.status_code == 200
    data = resp.json()
    for a in data["actions"]:
        assert a["context"] in ("task", "all")


def test_get_actions_pr_context(client):
    resp = client.get("/api/actions?context=pr")
    assert resp.status_code == 200
    data = resp.json()
    for a in data["actions"]:
        assert a["context"] in ("pr", "all")


def test_sessions_open_creates_new_session(client):
    from server import _db
    _db.create_task("test-proj", "my-task", description="Test task")

    launch_argv = MagicMock()
    with patch("common.sessions.session_exists", return_value=False), \
         patch("common.sessions.launch_session_argv", launch_argv):
        resp = client.post("/api/sessions/open", json={
            "task_id": "my-task",
            "project_id": "test-proj",
            "action_id": "do-task",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["session"] == "my-task"
    assert data["new"] is True
    # Action prompt is delivered separately, no [Background] in it
    assert "[Background]" not in (data["prompt"] or "")
    # Background lands in the --append-system-prompt argv element
    argv = launch_argv.call_args.args[2]
    bg = argv[argv.index("--append-system-prompt") + 1]
    assert "[Background]" in bg


def test_sessions_open_existing_session_skips_relaunch(client):
    """Recorded session + tmux alive -> no relaunch, just returns action prompt."""
    from server import _db
    _db.create_task("test-proj", "my-task", description="Test task")
    _db.create_session("my-task", "test-proj", "my-task")

    launch_argv = MagicMock()
    with patch("common.sessions.session_exists", return_value=True), \
         patch("common.sessions.launch_session_argv", launch_argv):
        resp = client.post("/api/sessions/open", json={
            "task_id": "my-task",
            "project_id": "test-proj",
            "action_id": "do-task",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["session"] == "my-task"
    assert data["new"] is False
    # No [Background] in action prompt; no relaunch since tmux is alive.
    assert "[Background]" not in (data["prompt"] or "")
    launch_argv.assert_not_called()


def test_sessions_open_task_not_found(client):
    resp = client.post("/api/sessions/open", json={
        "task_id": "nonexistent",
        "project_id": "test-proj",
        "action_id": "do-task",
    })
    assert resp.status_code == 404


def test_sessions_open_action_not_found(client):
    from server import _db
    _db.create_task("test-proj", "my-task", description="Test task")

    resp = client.post("/api/sessions/open", json={
        "task_id": "my-task",
        "project_id": "test-proj",
        "action_id": "nonexistent-action",
    })
    assert resp.status_code == 404


def test_hook_updates_session_status_to_idle(client):
    """Hook fires -> `session_state` cache shows the new state.
    The previous DB-column write was removed in the single-source
    redesign; the cache (which mirrors tmux + agent hooks) is the
    only writer."""
    from server import _db
    from common import session_state
    _db.create_session("my-task", "test-proj", "my-task")

    resp = client.post("/api/hook", json={
        "session": "my-task", "event": "Stop", "data": {},
    })
    assert resp.status_code == 200
    assert common.session_state.get("my-task")["state"] == "idle"


def test_hook_updates_session_status_to_thinking(client):
    from server import _db
    from common import session_state
    _db.create_session("my-task", "test-proj", "my-task")

    resp = client.post("/api/hook", json={
        "session": "my-task", "event": "UserPromptSubmit", "data": {},
    })
    assert resp.status_code == 200
    assert common.session_state.get("my-task")["state"] == "thinking"


def test_hook_updates_needs_permission(client):
    from server import _db
    from common import session_state
    _db.create_session("my-task", "test-proj", "my-task")

    resp = client.post("/api/hook", json={
        "session": "my-task", "event": "Notification",
        "data": {"notification_type": "permission_prompt", "message": "Approve?"},
    })
    assert resp.status_code == 200
    assert common.session_state.get("my-task")["state"] == "needs_permission"


def test_apply_cron_session_hook_only_handles_cron_stop(client):
    """The dispatcher helper introduced by the receive_hook refactor:
    returns True ONLY when the session is cron-job-* AND the event
    is Stop. Any other combination must fall through so review-*
    and task hooks downstream still run.

    Imported via the module so the test asserts the public contract,
    not just that receive_hook returns a 200 (which would mask a
    silent fall-through bug)."""
    from routes.sessions import _apply_cron_session_hook
    # Wrong prefix.
    assert _apply_cron_session_hook("review-pr-123", "Stop", "") is False
    # Wrong event for the cron prefix.
    assert _apply_cron_session_hook(
        "cron-job-1", "UserPromptSubmit", "") is False
    # Right combo -- handler runs (returns True) even if the inner
    # finish_run_for_session no-ops because the run isn't cached.
    assert _apply_cron_session_hook("cron-job-99999", "Stop", "") is True


def test_apply_review_session_hook_only_handles_review_prefix(client):
    from routes.sessions import _apply_review_session_hook
    # Non-review prefix.
    assert _apply_review_session_hook(
        "task-foo", "Stop", "idle", {}) is False
    # Review prefix -- handler runs (returns True) even when the
    # session_name has no cached review row.
    assert _apply_review_session_hook(
        "review-unknown", "Stop", "idle", {}) is True


def test_apply_task_session_hook_returns_false_for_unknown_session(client):
    """Task hook helper returns False when no row matched, so callers
    can log "no row found" if they want; receive_hook itself tolerates
    that path (the session may have been just-deleted)."""
    from routes.sessions import _apply_task_session_hook
    assert _apply_task_session_hook(
        "ghost-task", "Stop", "idle", {}) is False


def test_hook_ignores_unknown_session(client):
    """Hook for a session not in config_db should still return ok."""
    resp = client.post("/api/hook", json={
        "session": "unknown-session",
        "event": "Stop",
        "data": {},
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_rename_task_endpoint(client):
    """POST rename returns 200 and the new task_id in the response."""
    from server import _db
    _db.create_task("proj-r", "old-task", description="to rename")

    resp = client.post("/api/projects/proj-r/tasks/old-task/rename", json={"new_id": "new-task"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "new-task"


def test_rename_task_not_found_endpoint(client):
    """POST rename for a nonexistent task returns 404."""
    resp = client.post("/api/projects/proj-r/tasks/ghost-task/rename", json={"new_id": "new-task"})
    assert resp.status_code == 404


def test_rename_task_target_exists_endpoint(client):
    """POST rename to an existing task_id returns 409."""
    from server import _db
    _db.create_task("proj-r", "task-a", description="first")
    _db.create_task("proj-r", "task-b", description="second")

    resp = client.post("/api/projects/proj-r/tasks/task-a/rename", json={"new_id": "task-b"})
    assert resp.status_code == 409


def test_open_session_with_pr_context(client):
    """Open session with pr_number and pr_repo, verify PR Detail in injected bg."""
    from server import _db
    _db.create_task("test-proj", "pr-ctx-task", description="PR context task")
    _db.add_pr(
        project="test-proj",
        task_id="pr-ctx-task",
        number=42,
        url="https://github.com/example/repo/pull/42",
        status="open",
        title="[EX-999] Fix something",
    )

    launch_argv = MagicMock()
    with patch("common.sessions.session_exists", return_value=False), \
         patch("common.sessions.launch_session_argv", launch_argv):
        resp = client.post("/api/sessions/open", json={
            "task_id": "pr-ctx-task",
            "project_id": "test-proj",
            "action_id": "fix-ci",
            "pr_number": 42,
            "pr_repo": "example/repo",
        })

    assert resp.status_code == 200
    assert resp.json()["new"] is True
    argv = launch_argv.call_args.args[2]
    bg = argv[argv.index("--append-system-prompt") + 1]
    assert "Focus PR #42:" in bg


def test_hook_idle_prompt_notification(client):
    """Notification(idle_prompt) -> session-state cache shows
    'needs_input'. Distinct from Stop->idle: idle_prompt fires when
    the agent is sitting at the prompt cursor (the "your turn" signal
    the UI highlights with a gentle yellow pulse). idle (from Stop)
    is just "response turn finished"."""
    from server import _db
    from common import session_state
    _db.create_session("idle-hook-sess", "test-proj", "idle-hook-sess")

    resp = client.post("/api/hook", json={
        "session": "idle-hook-sess",
        "event": "Notification",
        "data": {"notification_type": "idle_prompt"},
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # idle_prompt is collapsed into 'idle' at the source (see
    # _HOOK_RULES["Notification:idle_prompt"]). From the user's POV
    # "the agent just finished" and "the agent sitting at prompt" are the
    # same situation -- the 3-tier urgency model treats both as
    # "ready for next task" (yellow), distinct from
    # needs_permission/crashed (red).
    assert common.session_state.get("idle-hook-sess")["state"] == "idle"


def test_open_session_missing_action_id(client):
    """POST /api/sessions/open without action_id should return 422 (pydantic validation)."""
    resp = client.post("/api/sessions/open", json={
        "task_id": "my-task",
        "project_id": "test-proj",
    })
    assert resp.status_code == 422


def test_open_session_missing_task_id(client):
    """POST /api/sessions/open without task_id should return 422 (pydantic validation)."""
    resp = client.post("/api/sessions/open", json={
        "project_id": "test-proj",
        "action_id": "do-task",
    })
    assert resp.status_code == 422
