import common
"""Integration tests for task CRUD and PR management API endpoints."""


class TestGetTask:
    def test_get_existing_task(self, client):
        resp = client.get("/api/projects/test-proj/tasks/task-a")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "task-a"
        assert data["description"] == "Task A - foundation work"
        assert data["status"] == "done"
        assert "updated_at" in data

    def test_get_task_effective_status_blocked(self, client):
        resp = client.get("/api/projects/test-proj/tasks/task-c")
        assert resp.status_code == 200
        data = resp.json()
        # task-c depends on task-b (in_progress), so effective_status is blocked
        assert data["effective_status"] == "blocked"
        # But raw status is still not_started
        assert data["status"] == "not_started"

    def test_get_task_effective_status_normal(self, client):
        resp = client.get("/api/projects/test-proj/tasks/task-b")
        assert resp.status_code == 200
        data = resp.json()
        # task-b depends on task-a (done), not blocked
        assert data["effective_status"] == "in_progress"

    def test_get_nonexistent_task(self, client):
        resp = client.get("/api/projects/test-proj/tasks/nonexistent")
        assert resp.status_code == 404

    def test_get_task_from_nonexistent_project(self, client):
        resp = client.get("/api/projects/nonexistent/tasks/task-a")
        assert resp.status_code == 404


class TestCreateTask:
    def test_create_task(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "new-task",
            "description": "A brand new task",
            "type": "feature",
            "group": "testing",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "new-task"
        assert data["description"] == "A brand new task"
        assert data["type"] == "feature"
        assert data["status"] == "not_started"
        assert data["prs"] == []

    def test_create_task_with_dependencies(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "dep-task",
            "description": "Depends on task-a",
            "type": "feature",
            "dependencies": ["task-a"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["dependencies"] == ["task-a"]

    def test_create_task_persists(self, client):
        client.post("/api/projects/test-proj/tasks", json={
            "id": "persisted",
            "description": "Should be loadable",
            "type": "feature",
        })
        resp = client.get("/api/projects/test-proj/tasks/persisted")
        assert resp.status_code == 200
        assert resp.json()["description"] == "Should be loadable"

    def test_create_duplicate_task(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "task-a",
            "description": "Duplicate",
            "type": "feature",
        })
        assert resp.status_code == 409

    def test_create_task_in_nonexistent_project(self, client):
        resp = client.post("/api/projects/nonexistent/tasks", json={
            "id": "orphan",
            "description": "No project",
            "type": "feature",
        })
        assert resp.status_code == 404

    def test_create_task_invalid_id_slashes(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "../../etc",
            "description": "Path traversal attempt",
            "type": "feature",
        })
        assert resp.status_code == 422

    def test_create_task_invalid_id_spaces(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "task with spaces",
            "description": "Spaces in id",
            "type": "feature",
        })
        assert resp.status_code == 422

    def test_create_task_invalid_id_empty(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "",
            "description": "Empty id",
            "type": "feature",
        })
        assert resp.status_code == 422

    def test_create_task_invalid_id_too_long(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "a" * 201,
            "description": "Too long id",
            "type": "feature",
        })
        assert resp.status_code == 422

    def test_create_task_valid_id_with_dots(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "my-task.v2",
            "description": "Task with dots and dashes",
            "type": "feature",
        })
        assert resp.status_code in (200, 201)
        assert resp.json()["id"] == "my-task.v2"

    def test_create_task_project_not_found(self, client):
        resp = client.post("/api/projects/does-not-exist/tasks", json={
            "id": "some-task",
            "description": "Task in missing project",
            "type": "feature",
        })
        assert resp.status_code == 404


class TestUpdateTask:
    def test_update_description(self, client):
        resp = client.put("/api/projects/test-proj/tasks/task-a", json={
            "description": "Updated description",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["description"] == "Updated description"
        assert data["id"] == "task-a"

    def test_update_status(self, client):
        resp = client.put("/api/projects/test-proj/tasks/task-d", json={
            "status": "in_progress",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    def test_update_notes(self, client):
        resp = client.put("/api/projects/test-proj/tasks/task-b", json={
            "notes": "Working on this",
        })
        assert resp.status_code == 200
        assert resp.json()["notes"] == "Working on this"

    def test_update_persists(self, client):
        client.put("/api/projects/test-proj/tasks/task-a", json={
            "notes": "Persistent note",
        })
        resp = client.get("/api/projects/test-proj/tasks/task-a")
        assert resp.json()["notes"] == "Persistent note"

    def test_update_nonexistent_task(self, client):
        resp = client.put("/api/projects/test-proj/tasks/nonexistent", json={
            "description": "No such task",
        })
        assert resp.status_code == 404

    def test_partial_update_preserves_other_fields(self, client):
        # Only update notes; description and status should remain
        client.put("/api/projects/test-proj/tasks/task-b", json={
            "notes": "Just a note",
        })
        resp = client.get("/api/projects/test-proj/tasks/task-b")
        data = resp.json()
        assert data["notes"] == "Just a note"
        assert data["description"] == "Task B - depends on A"
        assert data["status"] == "in_progress"

    def test_update_follow_ups_persists_via_http(self, client):
        """Regression: `TaskUpdate` was missing the `follow_ups` field, so HTTP
        PUT silently dropped the value even though EvaDB supported it. Now
        confirm the full HTTP path persists."""
        resp = client.put("/api/projects/test-proj/tasks/task-a", json={
            "follow_ups": ["rebase", "update-docs"],
        })
        assert resp.status_code == 200
        assert resp.json().get("follow_ups") == ["rebase", "update-docs"]
        # Re-read to confirm DB persistence (not just echo from update_task).
        check = client.get("/api/projects/test-proj/tasks/task-a")
        assert check.json().get("follow_ups") == ["rebase", "update-docs"]

    def test_update_rejects_follow_up_task_id_reference(self, client):
        """DB-level _validate_follow_ups enforces that follow_ups are natural
        language, not task IDs. HTTP PUT translates the ValueError into a 422
        rather than letting it surface as a 500 traceback."""
        resp = client.put("/api/projects/test-proj/tasks/task-b", json={
            "follow_ups": ["task-a"],  # task-a exists -> should be rejected
        })
        assert resp.status_code == 422
        detail = resp.json().get("detail", "")
        assert "task ID" in detail

    def test_update_rejects_invalid_status(self, client):
        """Invalid status value should be a 422 (bad request), not a 500."""
        resp = client.put("/api/projects/test-proj/tasks/task-a", json={
            "status": "not-a-real-status",
        })
        assert resp.status_code == 422

    def test_update_group_persists_via_http(self, client):
        """The HTTP body field is `group` but the DB column is `group_name`.
        app_state.save_task does the rename -- verify the chain works."""
        resp = client.put("/api/projects/test-proj/tasks/task-a", json={
            "group": "infra",
        })
        assert resp.status_code == 200
        # Route returns the updated task row; re-read to make sure it was saved.
        check = client.get("/api/projects/test-proj/tasks/task-a")
        assert check.json().get("group_name") == "infra"

    def test_update_priority_zero_persists(self, client):
        """Priority 0 = highest. Must not be confused with 'unset' by
        falsy checks in any layer of the HTTP pipeline."""
        resp = client.put("/api/projects/test-proj/tasks/task-a", json={
            "priority": 0,
        })
        assert resp.status_code == 200
        check = client.get("/api/projects/test-proj/tasks/task-a")
        assert check.json().get("priority") == 0

    def test_update_description_empty_string_clears_field(self, client):
        """Setting description to '' should clear it, not be ignored."""
        # First set description to something.
        client.put("/api/projects/test-proj/tasks/task-a", json={
            "description": "temporary text",
        })
        # Then clear.
        resp = client.put("/api/projects/test-proj/tasks/task-a", json={
            "description": "",
        })
        assert resp.status_code == 200
        check = client.get("/api/projects/test-proj/tasks/task-a")
        assert check.json().get("description") == ""

    def test_update_ticket_nested_dict_translates_to_columns(self, client):
        """HTTP body shape `{"ticket": {"id": X, "url": Y}}` is translated by
        app_state.save_task into ticket_id + ticket_url columns."""
        resp = client.put("/api/projects/test-proj/tasks/task-a", json={
            "ticket": {"id": "EX-9999", "url": "https://jira/EX-9999"},
        })
        assert resp.status_code == 200
        check = client.get("/api/projects/test-proj/tasks/task-a")
        data = check.json()
        assert data.get("ticket_id") == "EX-9999"
        assert data.get("ticket_url") == "https://jira/EX-9999"

    def test_update_ticket_nested_empty_clears_columns(self, client):
        """Regression: `{"ticket": {"id": "", "url": ""}}` via HTTP PUT must
        clear ticket_id / ticket_url. Previously, the echoed-back ticket dict
        from _task_row_to_dict could shadow explicit clears during round-trip."""
        # Seed a ticket.
        client.put("/api/projects/test-proj/tasks/task-a", json={
            "ticket": {"id": "OLD-1", "url": "https://old/OLD-1"},
        })
        # Clear via nested form.
        resp = client.put("/api/projects/test-proj/tasks/task-a", json={
            "ticket": {"id": "", "url": ""},
        })
        assert resp.status_code == 200
        check = client.get("/api/projects/test-proj/tasks/task-a")
        data = check.json()
        assert data.get("ticket_id") == ""
        assert data.get("ticket_url") == ""

    def test_update_ticket_nested_non_dict_returns_422(self, client):
        """Passing a non-dict value for `ticket` must be rejected cleanly."""
        resp = client.put("/api/projects/test-proj/tasks/task-a", json={
            "ticket": "just-a-string",
        })
        # Pydantic accepts `Optional[dict]`; it coerces strings -> 422.
        assert resp.status_code == 422


class TestCheckAndUpdateStatus:
    def test_suggests_done_when_all_merged(self, client):
        resp = client.post("/api/projects/test-proj/tasks/task-a/check-status")
        assert resp.status_code == 200
        data = resp.json()
        # task-a already done with merged PR, no change expected
        assert data["changed"] is False

    def test_suggests_in_progress_when_has_ticket(self, client):
        resp = client.post("/api/projects/test-proj/tasks/task-d/check-status")
        assert resp.status_code == 200
        data = resp.json()
        # task-d has ticket but status is not_started -> should become in_progress
        assert data["changed"] is True
        assert data["new_status"] == "in_progress"
        assert data["old_status"] == "not_started"

    def test_suggests_in_review_when_open_pr(self, client, patched_server):
        # Add an open PR to task-d via DB, then check status
        patched_server._db.add_pr(
            project="test-proj",
            task_id="task-d",
            number=300,
            url="https://github.com/test/300",
            status="open",
        )
        resp = client.post("/api/projects/test-proj/tasks/task-d/check-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_status"] == "in_review"

    def test_check_status_nonexistent_task(self, client):
        resp = client.post("/api/projects/test-proj/tasks/nonexistent/check-status")
        assert resp.status_code == 404


# ---- PR management ----

class TestAddPR:
    def test_add_pr_to_task(self, client):
        resp = client.post("/api/projects/test-proj/tasks/task-c/prs", json={
            "number": 500,
            "url": "https://github.com/example/repo/pull/500",
            "status": "open",
            "title": "Fix something in Repo",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["number"] == 500
        assert data["url"] == "https://github.com/example/repo/pull/500"

    def test_add_pr_persists(self, client):
        client.post("/api/projects/test-proj/tasks/task-c/prs", json={
            "number": 501,
            "url": "https://github.com/example/repo/pull/501",
            "status": "draft",
            "title": "Draft PR for task C",
        })
        resp = client.get("/api/projects/test-proj/tasks/task-c")
        prs = resp.json()["prs"]
        assert any(p["number"] == 501 for p in prs)

    def test_add_duplicate_pr(self, client):
        # task-a already has PR 100
        resp = client.post("/api/projects/test-proj/tasks/task-a/prs", json={
            "number": 100,
            "url": "https://github.com/example/repo/pull/100",
            "title": "Duplicate PR",
        })
        assert resp.status_code == 409

    def test_add_pr_to_nonexistent_task(self, client):
        resp = client.post("/api/projects/test-proj/tasks/nonexistent/prs", json={
            "number": 600,
            "url": "https://github.com/test/repo/pull/600",
            "title": "PR for missing task",
        })
        assert resp.status_code == 404

    def test_add_pr_without_title_rejected(self, client):
        resp = client.post("/api/projects/test-proj/tasks/task-c/prs", json={
            "number": 502,
            "url": "https://github.com/example/repo/pull/502",
        })
        assert resp.status_code == 422
        assert "title" in resp.json()["detail"].lower()

    def test_add_pr_with_bare_repo_name_rejected(self, client):
        # Real incident: a malformed URL ("universe") slipped into the
        # common.prs.url column and broke the frontend PR-detail render. The
        # write boundary now rejects anything that doesn't look like
        # a github.com PR URL so this can't happen again.
        resp = client.post("/api/projects/test-proj/tasks/task-c/prs", json={
            "number": 9002,
            "url": "universe",
            "title": "should be rejected",
        })
        assert resp.status_code == 422
        assert "url" in resp.json()["detail"].lower()

    def test_add_pr_with_non_github_url_rejected(self, client):
        # Defence in depth -- enterprise GitHub URLs use github.com only
        # in this codebase; everything else is wrong shape.
        resp = client.post("/api/projects/test-proj/tasks/task-c/prs", json={
            "number": 7,
            "url": "https://gitlab.com/acme/proj/merge_requests/7",
            "title": "wrong host",
        })
        assert resp.status_code == 422
        assert "url" in resp.json()["detail"].lower()


class TestDeletePR:
    def test_delete_pr(self, client):
        # task-a has PR 100
        resp = client.delete("/api/projects/test-proj/tasks/task-a/prs/100")
        assert resp.status_code == 204

        # Verify it's gone
        resp = client.get("/api/projects/test-proj/tasks/task-a")
        prs = resp.json()["prs"]
        assert not any(p["number"] == 100 for p in prs)

    def test_delete_nonexistent_pr(self, client):
        resp = client.delete("/api/projects/test-proj/tasks/task-a/prs/99999")
        assert resp.status_code == 404

    def test_delete_pr_from_nonexistent_task(self, client):
        resp = client.delete("/api/projects/test-proj/tasks/nonexistent/prs/100")
        assert resp.status_code == 404
