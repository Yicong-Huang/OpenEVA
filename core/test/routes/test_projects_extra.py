"""Tests for routes/common.projects.py -- covers smart-create, task CRUD routes,
dependency management, rename, close, and delete endpoints."""

import common
import json
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------

class TestListProjectsRoute:
    def test_list_projects_returns_200(self, client):
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data
        assert len(data["projects"]) >= 2

    def test_list_projects_contains_expected_ids(self, client):
        resp = client.get("/api/projects")
        ids = [p["id"] for p in resp.json()["projects"]]
        assert "test-proj" in ids
        assert "empty-proj" in ids


# ---------------------------------------------------------------------------
# GET /api/projects/{id}
# ---------------------------------------------------------------------------

class TestGetProjectRoute:
    def test_get_existing_project(self, client):
        resp = client.get("/api/projects/test-proj")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "test-proj"
        assert "tasks" in data

    def test_get_missing_project_returns_404(self, client):
        resp = client.get("/api/projects/does-not-exist")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/projects/{id}/graph
# ---------------------------------------------------------------------------

class TestGetGraphRoute:
    def test_graph_returns_nodes_and_edges(self, client):
        resp = client.get("/api/projects/test-proj/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data

    def test_graph_missing_project_returns_404(self, client):
        resp = client.get("/api/projects/nonexistent/graph")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/projects/{id}/tasks -- create task
# ---------------------------------------------------------------------------

class TestCreateTaskRoute:
    def test_create_task_success(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "route-new-task",
            "description": "Created via route test",
            "type": "feature",
            "group": "testing",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "route-new-task"
        assert data["description"] == "Created via route test"

    def test_create_duplicate_returns_409(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "task-a",
            "description": "Duplicate of task-a",
            "type": "feature",
        })
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_create_task_invalid_project_returns_404(self, client):
        resp = client.post("/api/projects/nonexistent/tasks", json={
            "id": "orphan-task",
            "description": "No project",
            "type": "feature",
        })
        assert resp.status_code == 404

    def test_create_task_invalid_id_returns_422(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "../bad-id",
            "description": "Invalid id",
            "type": "feature",
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PUT /api/projects/{id}/tasks/{tid} -- update task
# ---------------------------------------------------------------------------

class TestUpdateTaskRoute:
    def test_update_description(self, client):
        resp = client.put("/api/projects/test-proj/tasks/task-a", json={
            "description": "Updated via route test",
        })
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated via route test"

    def test_update_nonexistent_task_returns_404(self, client):
        resp = client.put("/api/projects/test-proj/tasks/nonexistent", json={
            "description": "No such task",
        })
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/projects/{id}/tasks/{tid}/close -- close task
# ---------------------------------------------------------------------------

class TestCloseTaskRoute:
    def test_close_task_with_reason(self, client):
        resp = client.post("/api/projects/test-proj/tasks/task-d/close", json={
            "reason": "No longer needed",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"
        assert "No longer needed" in data.get("notes", "")

    def test_close_nonexistent_task_returns_404(self, client):
        resp = client.post("/api/projects/test-proj/tasks/nonexistent/close", json={
            "reason": "gone",
        })
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/projects/{id}/tasks/{tid} -- delete task
# ---------------------------------------------------------------------------

class TestDeleteTaskRoute:
    def test_delete_task_with_ticket_returns_403(self, client):
        # task-c has ticket EX-123
        resp = client.delete("/api/projects/test-proj/tasks/task-c")
        assert resp.status_code == 403
        assert "ticket" in resp.json()["detail"].lower()

    def test_delete_task_without_ticket_success(self, client):
        # First create a task without a ticket
        client.post("/api/projects/test-proj/tasks", json={
            "id": "deletable-task",
            "description": "Can be deleted",
            "type": "chore",
        })
        resp = client.delete("/api/projects/test-proj/tasks/deletable-task")
        assert resp.status_code == 204

    def test_delete_nonexistent_task_returns_404(self, client):
        resp = client.delete("/api/projects/test-proj/tasks/no-such-task")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/projects/{id}/tasks/{tid}/rename
# ---------------------------------------------------------------------------

class TestRenameTaskRoute:
    def test_rename_task_success(self, client, patched_server):
        # Create a task to rename
        client.post("/api/projects/test-proj/tasks", json={
            "id": "rename-src",
            "description": "Will be renamed",
            "type": "feature",
        })
        resp = client.post("/api/projects/test-proj/tasks/rename-src/rename", json={
            "new_id": "rename-dst",
        })
        assert resp.status_code == 200

        # Verify old id is gone, new id exists
        assert client.get("/api/projects/test-proj/tasks/rename-src").status_code == 404
        assert client.get("/api/projects/test-proj/tasks/rename-dst").status_code == 200

    def test_rename_to_existing_returns_409(self, client):
        # task-a already exists
        resp = client.post("/api/projects/test-proj/tasks/task-b/rename", json={
            "new_id": "task-a",
        })
        assert resp.status_code == 409

    def test_rename_nonexistent_task_returns_404(self, client):
        resp = client.post("/api/projects/test-proj/tasks/no-such-task/rename", json={
            "new_id": "whatever",
        })
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/projects/{id}/tasks/{tid}/deps -- add dependency
# ---------------------------------------------------------------------------

class TestAddDependencyRoute:
    def test_add_dep_success(self, client):
        resp = client.post("/api/projects/test-proj/tasks/task-d/deps", json={
            "depends_on": "task-a",
        })
        assert resp.status_code == 201
        assert resp.json()["ok"] is True

    def test_add_dep_nonexistent_target_returns_404(self, client):
        resp = client.post("/api/projects/test-proj/tasks/task-d/deps", json={
            "depends_on": "nonexistent",
        })
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/projects/{id}/tasks/{tid}/deps/{dep} -- remove dependency
# ---------------------------------------------------------------------------

class TestRemoveDependencyRoute:
    def test_remove_dep_success(self, client):
        # task-b depends on task-a
        resp = client.delete("/api/projects/test-proj/tasks/task-b/deps/task-a")
        assert resp.status_code == 204

        # Verify the dependency was removed
        resp = client.get("/api/projects/test-proj/tasks/task-b")
        assert "task-a" not in resp.json().get("dependencies", [])


# ---------------------------------------------------------------------------
# POST /api/projects/{id}/tasks/{tid}/check-status
# ---------------------------------------------------------------------------

class TestCheckStatusRoute:
    def test_check_status_returns_result(self, client):
        resp = client.post("/api/projects/test-proj/tasks/task-d/check-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "changed" in data
        assert "old_status" in data
        assert "new_status" in data

    def test_check_status_nonexistent_returns_404(self, client):
        resp = client.post("/api/projects/test-proj/tasks/nonexistent/check-status")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/projects/{id}/tasks/smart-create -- AI-based task creation (SSE)
# ---------------------------------------------------------------------------

class TestSmartCreateRoute:
    """Tests for the smart-create endpoint.

    Smart-create routes the natural-language context through
    `adapters.agent.analyze`; these tests patch that single surface
    instead of mocking subprocess internals."""

    def _plan(self, **overrides):
        """Build a task-creation plan dict with sane defaults."""
        base = {
            "task_id": "smart-new-task",
            "description": "AI-created task",
            "type": "feature",
            "group": "",
            "status": "not_started",
            "ticket_id": None,
            "ticket_url": None,
            "dependencies": [],
            "duplicate_of": None,
            "duplicate_reason": None,
        }
        base.update(overrides)
        return base

    def _collect_sse(self, response):
        """Parse SSE data lines from a streaming response."""
        events = []
        for line in response.text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
        return events

    def test_smart_create_success(self, client, patched_server):
        """Successful smart-create returns SSE events ending with done."""
        plan = self._plan(task_id="smart-new-task", group="core")
        with patch("routes.tasks._agent.analyze", return_value=plan):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "Add a new feature for testing"},
            )

        assert resp.status_code == 200
        events = self._collect_sse(resp)
        texts = [e.get("text", "") for e in events if "text" in e]
        assert any("smart-new-task" in t for t in texts)
        assert any(e.get("done") for e in events)

    def test_smart_create_duplicate_detection(self, client, patched_server):
        """When AI detects a duplicate, SSE returns error text and done."""
        plan = self._plan(
            task_id="duplicate-task",
            duplicate_of="task-a",
            duplicate_reason="Same task already exists",
        )
        with patch("routes.tasks._agent.analyze", return_value=plan):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "Do the same as task-a"},
            )

        assert resp.status_code == 200
        events = self._collect_sse(resp)
        texts = [e.get("text", "") for e in events if "text" in e]
        assert any("Duplicate" in t or "duplicate" in t.lower() for t in texts)
        assert any(e.get("done") for e in events)

    def test_smart_create_invalid_project_returns_404(self, client):
        resp = client.post(
            "/api/projects/nonexistent/tasks/smart-create",
            json={"context": "anything"},
        )
        assert resp.status_code == 404

    def test_smart_create_missing_dependency_skips_silently(
        self, client, patched_server
    ):
        """When the AI plan references a dependency that doesn't exist,
        smart-create must skip it (ValueError from core_add_dep) rather
        than fail the whole task -- otherwise a single hallucinated dep
        would block every smart-create run."""
        plan = self._plan(
            task_id="smart-with-ghost-dep",
            dependencies=["does-not-exist-task"],
        )
        with patch("routes.tasks._agent.analyze", return_value=plan):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "try to depend on a ghost"},
            )

        assert resp.status_code == 200
        events = self._collect_sse(resp)
        texts = [e.get("text", "") for e in events if "text" in e]
        # The task itself still got created.
        assert any("smart-with-ghost-dep" in t for t in texts)
        # No "Added dependency:" event because the ghost dep was skipped.
        assert not any("Added dependency" in t for t in texts)
        # Stream still terminated cleanly with done.
        assert any(e.get("done") for e in events)

    def test_smart_create_with_ticket_emits_ticket_event(
        self, client, patched_server
    ):
        """A plan carrying ticket_id should update the task and emit a
        'Set ticket' SSE event -- the dashboard relies on this to render
        the ticket chip after AI creation."""
        plan = self._plan(
            task_id="smart-ticketed",
            ticket_id="EX-55555",
            ticket_url="https://issues.example.org/jira/browse/EX-55555",
        )
        with patch("routes.tasks._agent.analyze", return_value=plan):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "ticketed task"},
            )

        assert resp.status_code == 200
        events = self._collect_sse(resp)
        texts = [e.get("text", "") for e in events if "text" in e]
        assert any("Set ticket: EX-55555" in t for t in texts)

    def test_smart_create_persists_ai_summary_as_notes(
        self, client, patched_server
    ):
        """The AI-summarised `notes` field on the plan must land on
        the task record so the original prompt context isn't lost."""
        plan = self._plan(
            task_id="smart-with-notes",
            notes="Why: blocking the launch. AC: docs updated by Friday.",
        )
        with patch("routes.tasks._agent.analyze", return_value=plan):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "long original prompt the user typed in"},
            )
        assert resp.status_code == 200
        events = self._collect_sse(resp)
        texts = [e.get("text", "") for e in events if "text" in e]
        assert any("Saved context as task notes" in t for t in texts)
        task = patched_server._db.get_task("test-proj", "smart-with-notes")
        assert task is not None
        assert "blocking the launch" in (task.get("notes") or "")

    def test_smart_create_falls_back_to_raw_context_when_notes_missing(
        self, client, patched_server
    ):
        """If the AI elides the notes field, we save the user's raw
        context (truncated) so something is preserved -- the user's
        chief complaint was 'context lost'."""
        plan = self._plan(task_id="smart-fallback-notes")
        # No 'notes' key in plan.
        with patch("routes.tasks._agent.analyze", return_value=plan):
            client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "Investigate the OOM in stage 14 hourly job"},
            )
        task = patched_server._db.get_task(
            "test-proj", "smart-fallback-notes",
        )
        assert task is not None
        notes = task.get("notes") or ""
        assert "OOM in stage 14" in notes

    def test_smart_create_project_disappearing_mid_stream_errors(
        self, client, patched_server
    ):
        """The entry-level 404 guard ensures the project existed when the
        request arrived, but a long-running SSE stream must still cope if
        core_create_task raises KeyError (project deleted concurrently).
        Covers routes/common.tasks.py:295-296 -- the KeyError branch inside the
        stream's create block."""
        plan = self._plan(task_id="smart-deleted-proj")
        with patch("routes.tasks._agent.analyze", return_value=plan), \
             patch(
                 "routes.tasks.core_create_task",
                 side_effect=KeyError("test-proj"),
             ):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "race condition"},
            )

        assert resp.status_code == 200
        events = self._collect_sse(resp)
        errors = [e.get("error", "") for e in events if "error" in e]
        assert any("Project not found" in err for err in errors)

    def test_smart_create_unexpected_exception_in_stream_surfaces_error(
        self, client, patched_server
    ):
        """If something inside the stream raises unexpectedly (e.g. the
        DB write path), the outer handler emits a single SSE error event
        so the frontend doesn't hang on a half-closed stream."""
        plan = self._plan(task_id="smart-boom")

        # Patch core_create_task (used by the stream) to raise a
        # non-ValueError, non-KeyError exception; this reaches the outer
        # `except Exception` handler (lines 321-322).
        with patch("routes.tasks._agent.analyze", return_value=plan), \
             patch(
                 "routes.tasks.core_create_task",
                 side_effect=RuntimeError("disk full"),
             ):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "trigger unexpected error"},
            )

        assert resp.status_code == 200
        events = self._collect_sse(resp)
        errors = [e.get("error", "") for e in events if "error" in e]
        assert any("disk full" in err for err in errors)

    def test_smart_create_claude_returns_none(self, client, patched_server):
        """Adapter returns None (subprocess failed, invalid JSON, timed
        out -- all collapsed to 'AI unavailable') -> SSE error."""
        with patch("routes.tasks._agent.analyze", return_value=None):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "anything"},
            )

        assert resp.status_code == 200
        events = self._collect_sse(resp)
        errors = [e.get("error", "") for e in events if "error" in e]
        assert any("failed" in err.lower() or "timed out" in err.lower()
                   for err in errors)

    def test_smart_create_with_task_id_hint(self, client, patched_server):
        """User-provided task_id + ticket_id flow through to the created task."""
        plan = self._plan(
            task_id="user-hint-task", description="Hinted description",
            type="chore", ticket_id="EX-999",
            ticket_url="https://issues.example.org/jira/browse/EX-999",
        )
        with patch("routes.tasks._agent.analyze", return_value=plan):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={
                    "context": "Handle EX-999",
                    "task_id": "user-hint-task",
                    "description": "Hinted description",
                },
            )

        assert resp.status_code == 200
        events = self._collect_sse(resp)
        texts = [e.get("text", "") for e in events if "text" in e]
        assert any("EX-999" in t for t in texts)
        assert any(e.get("done") for e in events)
        task = patched_server._db.get_task("test-proj", "user-hint-task")
        assert task is not None
        assert task["ticket_id"] == "EX-999"

    def test_smart_create_duplicate_task_id_errors(self, client, patched_server):
        """When core rejects the AI-generated task_id (already exists),
        the SSE stream reports the failure instead of silently creating
        nothing."""
        patched_server._db.create_task(
            project="test-proj", task_id="fail-create",
            description="Existing", type="feature", status="not_started",
        )
        plan = self._plan(task_id="fail-create")
        with patch("routes.tasks._agent.analyze", return_value=plan):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "anything"},
            )

        assert resp.status_code == 200
        events = self._collect_sse(resp)
        errors = [e.get("error", "") for e in events if "error" in e]
        assert any("failed" in err.lower() or "Failed" in err for err in errors)
        assert any("already exists" in err for err in errors)

    def test_smart_create_no_task_id_in_response(self, client, patched_server):
        """When AI returns empty task_id, SSE returns an error."""
        plan = self._plan(task_id="", description="No task id")
        with patch("routes.tasks._agent.analyze", return_value=plan):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "anything"},
            )

        assert resp.status_code == 200
        events = self._collect_sse(resp)
        errors = [e.get("error", "") for e in events if "error" in e]
        assert any("task ID" in err or "task_id" in err.lower() for err in errors)

    def test_smart_create_with_dependencies(self, client, patched_server):
        """Dependencies from the plan are added via core.add_dependency."""
        plan = self._plan(task_id="dep-smart-task", dependencies=["task-a"])
        with patch("routes.tasks._agent.analyze", return_value=plan):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "Depends on task-a"},
            )

        assert resp.status_code == 200
        events = self._collect_sse(resp)
        texts = [e.get("text", "") for e in events if "text" in e]
        assert any("dependency" in t.lower() or "task-a" in t for t in texts)
        task = patched_server._db.get_task("test-proj", "dep-smart-task")
        assert task is not None
        assert "task-a" in (task.get("dependencies") or [])

    def test_smart_create_with_status_override(self, client, patched_server):
        """AI-returned non-default status lands in the DB."""
        plan = self._plan(task_id="status-smart-task", status="in_progress")
        with patch("routes.tasks._agent.analyze", return_value=plan):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "Already started"},
            )

        assert resp.status_code == 200
        events = self._collect_sse(resp)
        assert any(e.get("done") for e in events)
        task = patched_server._db.get_task("test-proj", "status-smart-task")
        assert task is not None
        assert task["status"] == "in_progress"

    def test_smart_create_emits_task_created_event(self, client, patched_server):
        """Regression: smart-create used to shell out to eva-cli; the
        subprocess's `emit_event` didn't reach this server's SSE
        subscribers, so the graph/list page wouldn't auto-refresh. By
        calling core in-process, the event now lands on the bus and any
        `useEventBus('task.*')` subscriber sees it."""
        import app_state
        collected = []
        app_state.on_event("task.created", lambda ev: collected.append(ev))

        plan = self._plan(task_id="event-emit-check",
                          description="Smart-created task")
        with patch("routes.tasks._agent.analyze", return_value=plan):
            resp = client.post(
                "/api/projects/test-proj/tasks/smart-create",
                json={"context": "anything"},
            )
        assert resp.status_code == 200
        self._collect_sse(resp)

        # Event handlers run on a daemon thread -- give them a tick to fire.
        import time
        for _ in range(20):
            if collected:
                break
            time.sleep(0.05)
        assert collected, "task.created event must fire in server process"
        assert "event-emit-check" in collected[0].get("title", "")

    # (`test_smart_create_raw_json_response` removed: the route now takes
    #  a pre-parsed dict from `_claude.analyze`, so the "raw vs wrapped
    #  JSON" concern lives entirely in `tests/test_adapters_claude.py`.)


class TestSmartCreateNotesHelper:
    """Direct unit coverage for `_smart_create_notes` -- the route-level
    tests cover the AI-summary-present and short-fallback paths; these
    nail down the `notes==""` and `notes==""` + raw-too-long edges."""

    def test_returns_empty_when_both_notes_and_raw_empty(self):
        """If the AI returns no notes AND the user passed empty
        context, we save '' rather than synthesising anything."""
        from routes.tasks import _smart_create_notes
        assert _smart_create_notes({"notes": ""}, "") == ""
        assert _smart_create_notes({}, "") == ""
        assert _smart_create_notes({"notes": "   "}, "  \n  ") == ""

    def test_truncates_long_raw_with_ellipsis(self):
        """raw > 500 chars gets sliced + ' ...' appended so the notes
        column doesn't blow up with multi-paragraph dumps. 500 is the
        Eva-wide notes-length convention."""
        from routes.tasks import _smart_create_notes
        long = "a" * 800
        out = _smart_create_notes({"notes": ""}, long)
        # Output is the first 500 chars (rstripped, but rstrip on `aaaa...`
        # is a no-op) + " ..." marker.
        assert out.endswith(" ...")
        assert len(out) == 500 + 4
        assert out.startswith("a" * 500)

    def test_short_raw_passes_through_verbatim(self):
        """Boundary: <=500 chars returns raw unchanged."""
        from routes.tasks import _smart_create_notes
        s = "exactly the user's words"
        assert _smart_create_notes({"notes": ""}, s) == s

    def test_ai_notes_win_over_raw(self):
        """When the AI produces a summary, raw is ignored entirely."""
        from routes.tasks import _smart_create_notes
        out = _smart_create_notes({"notes": "AI summary"}, "raw text")
        assert out == "AI summary"
