"""End-to-end tests for recently added features:
- Close task endpoint (POST /api/projects/{pid}/tasks/{tid}/close)
- has_tickets config (projects with has_tickets=false skip ticket-based status rules)
- Dirty pin (mark_pr_dirty, list_dirty_prs, clear_pr_dirty)
- Session info in load_tasks
- suggest_task_status with has_tickets param
"""

import yaml
from unittest.mock import patch


# ====================================================================
# Close task endpoint
# ====================================================================

class TestCloseTask:
    """POST /api/projects/{pid}/tasks/{tid}/close sets status to closed."""

    def test_close_task_with_reason(self, client):
        """Closing a task with a reason sets status=closed and appends reason to notes."""
        resp = client.post(
            "/api/projects/test-proj/tasks/task-d/close",
            json={"reason": "Superseded by task-b"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"
        assert "[Closed] Superseded by task-b" in data["notes"]

    def test_close_task_without_reason(self, client):
        """Closing a task with empty reason sets status=closed, notes unchanged."""
        resp = client.post(
            "/api/projects/test-proj/tasks/task-d/close",
            json={"reason": ""},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"

    def test_close_task_preserves_existing_notes(self, client, patched_server):
        """Closing a task with existing notes appends the close note."""
        patched_server._db.update_task(
            "test-proj", "task-d", notes="Existing note"
        )
        resp = client.post(
            "/api/projects/test-proj/tasks/task-d/close",
            json={"reason": "No longer needed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"
        assert "Existing note" in data["notes"]
        assert "[Closed] No longer needed" in data["notes"]

    def test_close_already_closed_task(self, client):
        """Closing a task that is already closed should succeed (idempotent)."""
        # First close
        resp1 = client.post(
            "/api/projects/test-proj/tasks/task-d/close",
            json={"reason": "First close"},
        )
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "closed"

        # Second close
        resp2 = client.post(
            "/api/projects/test-proj/tasks/task-d/close",
            json={"reason": "Second close"},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["status"] == "closed"
        assert "[Closed] First close" in data["notes"]
        assert "[Closed] Second close" in data["notes"]

    def test_close_nonexistent_task(self, client):
        """Closing a task that does not exist returns 404."""
        resp = client.post(
            "/api/projects/test-proj/tasks/nonexistent/close",
            json={"reason": "Gone"},
        )
        assert resp.status_code == 404

    def test_close_task_in_nonexistent_project(self, client):
        """Closing a task in a nonexistent project returns 404."""
        resp = client.post(
            "/api/projects/no-such-proj/tasks/task-a/close",
            json={"reason": "No project"},
        )
        assert resp.status_code == 404

    def test_close_task_persists(self, client):
        """After closing, a GET should reflect the closed status."""
        client.post(
            "/api/projects/test-proj/tasks/task-d/close",
            json={"reason": "Done for now"},
        )
        resp = client.get("/api/projects/test-proj/tasks/task-d")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"
        assert "[Closed] Done for now" in data["notes"]


# ====================================================================
# has_tickets config
# ====================================================================

class TestHasTicketsConfig:
    """Projects with has_tickets=false should not trigger ticket-based status rules."""

    def test_has_tickets_true_triggers_status_change(self, client, patched_server):
        """When has_tickets=true, a task with a ticket should be suggested in_progress."""
        # test-proj has has_tickets=True in conftest config
        # task-d has ticket_id=EX-99999 and status=not_started
        resp = client.post("/api/projects/test-proj/tasks/task-d/check-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["changed"] is True
        assert data["new_status"] == "in_progress"

    def test_has_tickets_false_skips_ticket_rule(self, client, patched_server, tmp_path):
        """When has_tickets=false, a task with a ticket should NOT be auto-promoted."""
        # Modify config to add a has_tickets=false project
        config = patched_server.load_config()
        config["projects"]["no-ticket-proj"] = {
            "name": "No Ticket Project",
            "description": "A project where tickets do not drive status",
            "repo": "test-repo",
            "jira": "test-jira",
            "has_tickets": False,
            "umbrella_tickets": [],
            "design_doc": None,
        }
        patched_server.save_config(config)

        # Create a task with a ticket in the no-ticket project
        patched_server._db.create_task(
            project="no-ticket-proj",
            task_id="ticket-task",
            description="Task with ticket but no ticket-driven status",
            type="feature",
            status="not_started",
            ticket_id="EX-88888",
            ticket_url="https://issues.example.org/jira/browse/EX-88888",
        )

        # check-status should NOT change status because has_tickets=false
        resp = client.post("/api/projects/no-ticket-proj/tasks/ticket-task/check-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["changed"] is False
        assert data["new_status"] == "not_started"

    def test_has_tickets_false_with_merged_pr_still_suggests_done(
        self, client, patched_server
    ):
        """Even with has_tickets=false, merged PRs should still suggest done."""
        config = patched_server.load_config()
        config["projects"]["no-ticket-proj"] = {
            "name": "No Ticket Project",
            "description": "A project where tickets do not drive status",
            "repo": "test-repo",
            "jira": "test-jira",
            "has_tickets": False,
            "umbrella_tickets": [],
            "design_doc": None,
        }
        patched_server.save_config(config)

        patched_server._db.create_task(
            project="no-ticket-proj",
            task_id="pr-done-task",
            description="Task with merged PR",
            type="feature",
            status="in_review",
            ticket_id="EX-77777",
            ticket_url="https://issues.example.org/jira/browse/EX-77777",
        )
        patched_server._db.add_pr(
            project="no-ticket-proj",
            task_id="pr-done-task",
            number=999,
            url="https://github.com/test/999",
            status="merged",
        )

        resp = client.post("/api/projects/no-ticket-proj/tasks/pr-done-task/check-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["changed"] is True
        assert data["new_status"] == "done"

    def test_has_tickets_false_project_with_ticket_data_in_listing(
        self, client, patched_server
    ):
        """A has_tickets=false project should still expose has_tickets in the listing."""
        # `/api/projects` reads from the DB (not from config), so the
        # project must exist as a row -- create it explicitly. Setting
        # `has_tickets=False` is what this test is actually asserting.
        patched_server._db.create_project(
            "no-ticket-proj",
            name="No Ticket Project",
            description="desc",
            has_tickets=False,
        )

        resp = client.get("/api/projects")
        assert resp.status_code == 200
        projects = resp.json()["projects"]
        no_ticket = next(p for p in projects if p["id"] == "no-ticket-proj")
        assert no_ticket["has_tickets"] is False

    def test_has_tickets_missing_defaults_false(self, client, patched_server):
        """When has_tickets is not in the config, it defaults to False."""
        # empty-proj does not have has_tickets key in conftest config
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        projects = resp.json()["projects"]
        empty_proj = next(p for p in projects if p["id"] == "empty-proj")
        assert empty_proj["has_tickets"] is False


# ====================================================================
# Dirty pin (mark_pr_dirty, list_dirty_prs, clear_pr_dirty)
# ====================================================================

class TestDirtyPin:
    """Test the dirty pin mechanism in task_db at the DB level."""

    def test_mark_pr_dirty(self, patched_server):
        """mark_pr_dirty sets the dirty flag on an existing PR."""
        db = patched_server._db
        # PR 200 exists on task-b
        db.mark_pr_dirty(200)
        dirty = db.list_dirty_prs()
        numbers = [p["number"] for p in dirty]
        assert 200 in numbers

    def test_list_dirty_prs_empty(self, patched_server):
        """list_dirty_prs returns empty list when no PRs are dirty."""
        db = patched_server._db
        dirty = db.list_dirty_prs()
        assert dirty == []

    def test_clear_pr_dirty(self, patched_server):
        """clear_pr_dirty removes the dirty flag from a PR."""
        db = patched_server._db
        db.mark_pr_dirty(200)
        dirty_before = db.list_dirty_prs()
        assert len(dirty_before) == 1

        db.clear_pr_dirty(200)
        dirty_after = db.list_dirty_prs()
        assert dirty_after == []

    def test_mark_nonexistent_pr_dirty(self, patched_server):
        """Marking a non-existent PR dirty is a no-op (no error, no dirty PRs)."""
        db = patched_server._db
        db.mark_pr_dirty(99999)
        dirty = db.list_dirty_prs()
        assert dirty == []

    def test_clear_nonexistent_pr_dirty(self, patched_server):
        """Clearing dirty on a non-existent PR is a no-op (no error)."""
        db = patched_server._db
        # Should not raise
        db.clear_pr_dirty(99999)
        dirty = db.list_dirty_prs()
        assert dirty == []

    def test_mark_multiple_prs_dirty(self, patched_server):
        """Multiple PRs can be dirty at the same time."""
        db = patched_server._db
        # PR 100 on task-a, PR 200 on task-b
        db.mark_pr_dirty(100)
        db.mark_pr_dirty(200)
        dirty = db.list_dirty_prs()
        numbers = {p["number"] for p in dirty}
        assert numbers == {100, 200}

    def test_mark_pr_dirty_idempotent(self, patched_server):
        """Marking the same PR dirty twice does not create duplicates."""
        db = patched_server._db
        db.mark_pr_dirty(200)
        db.mark_pr_dirty(200)
        dirty = db.list_dirty_prs()
        assert len(dirty) == 1
        assert dirty[0]["number"] == 200

    def test_clear_all_dirty(self, patched_server):
        """clear_all_dirty resets all dirty flags at once."""
        db = patched_server._db
        db.mark_pr_dirty(100)
        db.mark_pr_dirty(200)
        assert len(db.list_dirty_prs()) == 2

        db.clear_all_dirty()
        assert db.list_dirty_prs() == []

    def test_dirty_pr_includes_task_info(self, patched_server):
        """Dirty PR listing includes task_description and task_status from the join."""
        db = patched_server._db
        db.mark_pr_dirty(100)
        dirty = db.list_dirty_prs()
        assert len(dirty) == 1
        pr = dirty[0]
        assert pr["task_description"] == "Task A - foundation work"
        assert pr["task_status"] == "done"


# ====================================================================
# Session info in load_tasks
# ====================================================================

class TestSessionInfoInTasks:
    """load_tasks should merge session info into task dicts."""

    def test_task_without_session_has_no_session_key(self, patched_server):
        """A task with no session should not have a 'session' key."""
        tasks = patched_server.load_tasks("test-proj")
        # task-a has no session
        assert "session" not in tasks["task-a"]

    def test_task_with_session_includes_session_info(self, patched_server, mock_tmux):
        """A task with a session row gets session name + running flag.
        Status is read from the unified `session_state` cache, so
        it's "" until the cache is populated (which happens via agent
        hooks at runtime). The task row's DB columns no longer drive
        status."""
        mock_tmux["exists"].return_value = True
        patched_server._db.create_session("task-b", "test-proj", "task-b")
        tasks = patched_server.load_tasks("test-proj")
        assert "session" in tasks["task-b"]
        session = tasks["task-b"]["session"]
        assert session["name"] == "task-b"
        assert session["running"] is True
        # Empty until session_state cache has a row for this session.
        assert session["status"] == ""

    def test_session_info_shows_not_running(self, patched_server, mock_tmux):
        """When tmux session does not exist, running should be False."""
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("task-a", "test-proj", "task-a")
        tasks = patched_server.load_tasks("test-proj")
        session = tasks["task-a"]["session"]
        assert session["running"] is False

    def test_session_info_reflects_status_update(self, patched_server, mock_tmux):
        """After the unified session-state cache is updated, load_tasks
        should reflect it. The DB column update is no longer the
        canonical write path -- the cache is."""
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("task-c", "test-proj", "task-c")
        from common import session_state as _ssn
        _ssn.set_state("task-c", state="idle", kind="task",
                       project_id="test-proj", target_id="task-c")
        tasks = patched_server.load_tasks("test-proj")
        session = tasks["task-c"]["session"]
        assert session["status"] == "idle"

    def test_session_for_different_project_not_mixed(self, patched_server, mock_tmux):
        """Sessions from another project should not appear in load_tasks."""
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("cross-task", "empty-proj", "cross-task")
        tasks = patched_server.load_tasks("test-proj")
        for tid, t in tasks.items():
            if "session" in t:
                assert t["session"]["name"] != "cross-task"


# ====================================================================
# suggest_task_status with has_tickets param
# ====================================================================

class TestSuggestTaskStatusHasTickets:
    """Test suggest_task_status behavior with has_tickets=True vs False."""

    def test_has_tickets_true_with_ticket_suggests_in_progress(self, patched_server):
        """With has_tickets=True, a not_started task with a ticket -> in_progress."""
        task = {
            "status": "not_started",
            "ticket": {"id": "EX-123"},
            "prs": [],
        }
        result = patched_server.suggest_task_status(task, has_tickets=True)
        assert result == "in_progress"

    def test_has_tickets_false_with_ticket_no_suggestion(self, patched_server):
        """With has_tickets=False, a not_started task with a ticket -> no suggestion."""
        task = {
            "status": "not_started",
            "ticket": {"id": "EX-123"},
            "prs": [],
        }
        result = patched_server.suggest_task_status(task, has_tickets=False)
        assert result is None

    def test_has_tickets_false_with_open_pr_still_suggests_in_review(
        self, patched_server
    ):
        """PR-based suggestions work regardless of has_tickets setting."""
        task = {
            "status": "not_started",
            "ticket": {"id": "EX-123"},
            "prs": [{"status": "open"}],
        }
        result = patched_server.suggest_task_status(task, has_tickets=False)
        assert result == "in_review"

    def test_has_tickets_false_with_merged_pr_still_suggests_done(
        self, patched_server
    ):
        """All merged PRs -> done, regardless of has_tickets."""
        task = {
            "status": "in_review",
            "ticket": {"id": "EX-123"},
            "prs": [{"status": "merged"}],
        }
        result = patched_server.suggest_task_status(task, has_tickets=False)
        assert result == "done"

    def test_empty_pr_list_with_has_tickets_true(self, patched_server):
        """No PRs, has_tickets=True, no ticket -> no suggestion."""
        task = {
            "status": "not_started",
            "prs": [],
        }
        result = patched_server.suggest_task_status(task, has_tickets=True)
        assert result is None

    def test_empty_pr_list_with_has_tickets_false(self, patched_server):
        """No PRs, has_tickets=False, has ticket -> no suggestion."""
        task = {
            "status": "not_started",
            "ticket": {"id": "EX-123"},
            "prs": [],
        }
        result = patched_server.suggest_task_status(task, has_tickets=False)
        assert result is None

    def test_default_has_tickets_is_true(self, patched_server):
        """When has_tickets is not passed, it defaults to True."""
        task = {
            "status": "not_started",
            "ticket": {"id": "EX-123"},
            "prs": [],
        }
        result = patched_server.suggest_task_status(task)
        assert result == "in_progress"

    def test_has_tickets_true_no_ticket_no_suggestion(self, patched_server):
        """has_tickets=True but no ticket on the task -> no suggestion."""
        task = {
            "status": "not_started",
            "prs": [],
        }
        result = patched_server.suggest_task_status(task, has_tickets=True)
        assert result is None

    def test_has_tickets_true_already_in_progress(self, patched_server):
        """Task already in_progress with ticket -> no change suggested."""
        task = {
            "status": "in_progress",
            "ticket": {"id": "EX-123"},
            "prs": [],
        }
        result = patched_server.suggest_task_status(task, has_tickets=True)
        assert result is None
