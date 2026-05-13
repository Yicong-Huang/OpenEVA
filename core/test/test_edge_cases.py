"""Edge case tests for existing modules: task creation with invalid data,
circular dependencies, empty projects, PR edge cases, session hook edge cases,
session status with stale data, graph API with empty deps, tasks with no group."""

import json
from unittest.mock import patch as _patch
import yaml
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


# ---- Task creation with invalid data ----

class TestTaskCreationEdgeCases:
    def test_create_task_missing_required_fields(self, client):
        # Missing 'id' field
        resp = client.post("/api/projects/test-proj/tasks", json={
            "description": "No ID provided",
            "type": "feature",
        })
        assert resp.status_code == 422  # Pydantic validation error

    def test_create_task_missing_description(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "no-desc",
        })
        assert resp.status_code == 422

    def test_create_task_empty_id(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "",
            "description": "Empty ID task",
            "type": "feature",
        })
        # Server accepts empty string as ID (no validation against it)
        assert resp.status_code in (201, 422)

    def test_create_task_with_all_defaults(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "defaults-task",
            "description": "Only required fields",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "feature"  # default
        assert data["group"] == ""  # default
        assert data["status"] == "not_started"  # default
        assert data["dependencies"] == []  # default

    def test_create_task_with_nonexistent_dependency(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "phantom-dep",
            "description": "Depends on nonexistent",
            "type": "feature",
            "dependencies": ["does-not-exist"],
        })
        # Server does not validate dependency existence on create
        assert resp.status_code == 201
        data = resp.json()
        assert data["dependencies"] == ["does-not-exist"]

    def test_create_task_with_custom_status(self, client):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "custom-status",
            "description": "Custom initial status",
            "type": "bug",
            "status": "in_progress",
        })
        assert resp.status_code == 201
        assert resp.json()["status"] == "in_progress"


# ---- Circular dependencies ----

class TestCircularDependencies:
    def test_task_blocked_with_self_dependency(self, patched_server):
        tasks = {
            "self-dep": {
                "description": "Depends on itself",
                "status": "not_started",
                "dependencies": ["self-dep"],
            }
        }
        # Self-referencing: the dep (self-dep) status is not_started (not done/merged)
        assert patched_server.is_task_blocked("self-dep", tasks) is True

    def test_mutual_circular_deps_both_blocked(self, patched_server):
        tasks = {
            "a": {
                "description": "A depends on B",
                "status": "not_started",
                "dependencies": ["b"],
            },
            "b": {
                "description": "B depends on A",
                "status": "not_started",
                "dependencies": ["a"],
            },
        }
        assert patched_server.is_task_blocked("a", tasks) is True
        assert patched_server.is_task_blocked("b", tasks) is True

    def test_circular_deps_in_stats(self, patched_server):
        # Create a project with circular deps via DB
        config = patched_server.load_config()
        config["projects"]["circular-proj"] = {"name": "Circular", "repo": "test"}
        patched_server.save_config(config)

        db = patched_server._db
        db.create_task(project="circular-proj", task_id="x",
                       description="Task x", status="not_started")
        db.create_task(project="circular-proj", task_id="y",
                       description="Task y", status="not_started")
        db.set_dependencies("circular-proj", "x", ["y"])
        db.set_dependencies("circular-proj", "y", ["x"])

        stats = patched_server.compute_project_stats("circular-proj")
        assert stats["total"] == 2
        # Both are not_started in DB; blocked is computed separately
        assert stats["counts"]["not_started"] == 2

    def test_three_way_circular(self, patched_server):
        tasks = {
            "a": {"status": "not_started", "dependencies": ["c"]},
            "b": {"status": "not_started", "dependencies": ["a"]},
            "c": {"status": "not_started", "dependencies": ["b"]},
        }
        assert patched_server.is_task_blocked("a", tasks) is True
        assert patched_server.is_task_blocked("b", tasks) is True
        assert patched_server.is_task_blocked("c", tasks) is True


# ---- Empty project with no tasks ----

class TestEmptyProjectEdgeCases:
    def test_empty_project_graph_has_no_groups(self, client):
        resp = client.get("/api/projects/empty-proj/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"] == []
        assert data["edges"] == []
        assert data["groups"] == []

    def test_empty_project_enriched(self, patched_server):
        config = patched_server.load_config()
        proj = config["projects"]["empty-proj"]
        enriched = patched_server.enrich_project("empty-proj", proj)
        assert enriched["tasks"] == {}
        assert enriched["progress"] == 0.0
        assert enriched["task_counts"]["done"] == 0
        assert enriched["task_counts"]["not_started"] == 0

    def test_create_first_task_in_empty_project(self, client):
        resp = client.post("/api/projects/empty-proj/tasks", json={
            "id": "first-task",
            "description": "First task in empty project",
            "type": "feature",
        })
        assert resp.status_code == 201
        # Verify it shows up
        resp2 = client.get("/api/projects/empty-proj")
        assert resp2.status_code == 200
        assert "first-task" in resp2.json()["tasks"]


# ---- PR with all fields including notes ----

class TestPRWithAllFields:
    def test_add_pr_with_session_and_working_dir(self, client):
        resp = client.post("/api/projects/test-proj/tasks/task-c/prs", json={
            "number": 777,
            "url": "https://github.com/example/repo/pull/777",
            "status": "open",
            "title": "Fix Repo PR 777",
            "session": "pr-777-session",
            "working_dir": "~/repo",
            "agent_args": "-n pr-777 --model opus",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["session"] == "pr-777-session"
        assert data["working_dir"] == "~/repo"
        assert data["agent_args"] == "-n pr-777 --model opus"

    def test_pr_persists_all_fields(self, client):
        client.post("/api/projects/test-proj/tasks/task-d/prs", json={
            "number": 888,
            "url": "https://github.com/example/repo/pull/888",
            "status": "draft",
            "title": "Draft PR 888 for runtime",
            "session": "sess-888",
            "working_dir": "~/svc",
            "agent_args": "--resume",
        })
        resp = client.get("/api/projects/test-proj/tasks/task-d")
        prs = resp.json()["prs"]
        pr888 = next(p for p in prs if p["number"] == 888)
        assert pr888["session"] == "sess-888"
        assert pr888["working_dir"] == "~/svc"

    def test_task_with_notes_roundtrip(self, client):
        client.put("/api/projects/test-proj/tasks/task-a", json={
            "notes": "Important: needs review before merge. Check perf benchmarks.",
        })
        resp = client.get("/api/projects/test-proj/tasks/task-a")
        data = resp.json()
        assert "Important: needs review" in data["notes"]


# ---- Multiple PRs on same task ----

class TestMultiplePRsOnTask:
    def test_add_multiple_prs(self, client):
        resp1 = client.post("/api/projects/test-proj/tasks/task-c/prs", json={
            "number": 901,
            "url": "https://github.com/example/repo/pull/901",
            "status": "open",
            "title": "First PR for task-c",
        })
        assert resp1.status_code == 201

        resp2 = client.post("/api/projects/test-proj/tasks/task-c/prs", json={
            "number": 902,
            "url": "https://github.com/example/repo/pull/902",
            "status": "draft",
            "title": "Second PR for task-c",
        })
        assert resp2.status_code == 201

        resp = client.get("/api/projects/test-proj/tasks/task-c")
        prs = resp.json()["prs"]
        numbers = [p["number"] for p in prs]
        assert 901 in numbers
        assert 902 in numbers

    def test_delete_one_of_multiple_prs(self, client):
        # Add two PRs to task-d
        client.post("/api/projects/test-proj/tasks/task-d/prs", json={
            "number": 1001,
            "url": "https://github.com/test/repo/pull/1001",
            "status": "open",
            "title": "PR 1001 for deletion test",
        })
        client.post("/api/projects/test-proj/tasks/task-d/prs", json={
            "number": 1002,
            "url": "https://github.com/test/repo/pull/1002",
            "status": "open",
            "title": "PR 1002 for deletion test",
        })

        # Delete one
        resp = client.delete("/api/projects/test-proj/tasks/task-d/prs/1001")
        assert resp.status_code == 204

        # Verify other remains
        resp = client.get("/api/projects/test-proj/tasks/task-d")
        prs = resp.json()["prs"]
        numbers = [p["number"] for p in prs]
        assert 1001 not in numbers
        assert 1002 in numbers

    def test_status_suggestion_with_mixed_prs(self, patched_server):
        # One merged + one open = should suggest in_review (not done)
        task = {
            "status": "in_progress",
            "prs": [
                {"status": "merged"},
                {"status": "open"},
            ],
        }
        assert patched_server.suggest_task_status(task) == "in_review"

    def test_status_suggestion_all_merged(self, patched_server):
        task = {
            "status": "in_review",
            "prs": [
                {"status": "merged"},
                {"status": "merged"},
                {"status": "merged"},
            ],
        }
        assert patched_server.suggest_task_status(task) == "done"


# ---- Session hook with unknown event types ----

class TestSessionHookEdgeCases:
    def test_hook_unknown_event_type(self, client, patched_server):
        resp = client.post("/api/hook", json={
            "session": "test-sess",
            "event": "UnknownEventType",
            "data": {"some": "data"},
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # State should not be set for unknown events
        state = patched_server._session_states.get("test-sess", {})
        # The state might be empty or from a previous event
        # Key point: it should not crash
        assert state.get("state") != "UnknownEventType"

    def test_hook_notification_unknown_ntype(self, client, patched_server):
        # Notification event with an unrecognized notification_type
        resp = client.post("/api/hook", json={
            "session": "ntype-test",
            "event": "Notification",
            "data": {"notification_type": "some_new_type"},
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_hook_session_start_does_not_signal_ready(self, client, patched_server):
        # SessionStart should NOT set state to idle
        resp = client.post("/api/hook", json={
            "session": "start-test",
            "event": "SessionStart",
            "data": {},
        })
        assert resp.status_code == 200
        state = patched_server._session_states.get("start-test", {})
        assert state["state"] == "starting"

    def test_hook_empty_data(self, client, patched_server):
        resp = client.post("/api/hook", json={
            "session": "empty-data",
            "event": "Stop",
            "data": {},
        })
        assert resp.status_code == 200
        state = patched_server._session_states.get("empty-data", {})
        assert state["state"] == "idle"

    def test_hook_sequential_state_transitions(self, client, patched_server):
        session = "state-transition"
        # Start -> thinking -> idle -> thinking -> needs_permission -> idle
        events = [
            ("SessionStart", {}, "starting"),
            ("UserPromptSubmit", {}, "thinking"),
            ("Stop", {}, "idle"),
            ("UserPromptSubmit", {}, "thinking"),
            ("Notification", {"notification_type": "permission_prompt", "message": "Allow?"}, "needs_permission"),
            # idle_prompt collapses to 'idle' at the source -- both
            # mean "agent waiting for the user". Sharing one state
            # keeps the 3-tier urgency model coherent (yellow = ready
            # for next, distinct from red = needs_permission/crashed).
            ("Notification", {"notification_type": "idle_prompt"}, "idle"),
        ]
        for event, data, expected_state in events:
            client.post("/api/hook", json={
                "session": session, "event": event, "data": data,
            })
            state = patched_server._session_states.get(session, {})
            assert state["state"] == expected_state, (
                f"After event={event}, expected state={expected_state}, got={state.get('state')}"
            )


# ---- Session status endpoint reads from the unified cache ----

class TestSessionStatusFromCache:
    """`/api/sessions/{name}/status` is now a thin reader of
    `session_state`. The previous "30s stale fallback" + "tmux
    pane parse" logic is gone -- the cache is authoritative, kept
    fresh by agent hooks + the periodic reaper.
    """

    def _seed(self, name: str, state: str):
        from common import session_state as _ssn
        with _ssn._lock:
            _ssn._states[name] = {
                "tmux_name": name, "kind": "task", "state": state,
                "detail": "", "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "agent_session_id": "", "project_id": "",
                "target_id": "", "target_instance": "",
            }

    def _clear(self, name: str):
        from common import session_state as _ssn
        with _ssn._lock:
            _ssn._states.pop(name, None)

    def test_cache_value_returned_verbatim(self, client, mock_tmux):
        mock_tmux["exists"].return_value = True
        self._seed("fresh-sess", "needs_permission")
        try:
            resp = client.get("/api/sessions/fresh-sess/status")
            assert resp.status_code == 200
            assert resp.json()["state"] == "needs_permission"
        finally:
            self._clear("fresh-sess")

    def test_tmux_dead_returns_stopped_and_evicts(self, client, mock_tmux):
        """When tmux is gone, the endpoint returns 'stopped' AND
        emits a session.state=stopped through the cache so any SSE
        listener stays in sync."""
        mock_tmux["exists"].return_value = False
        self._seed("cleared-sess", "idle")
        try:
            resp = client.get("/api/sessions/cleared-sess/status")
            assert resp.status_code == 200
            assert resp.json()["state"] == "stopped"
            # Cache row flipped to 'stopped' (not popped -- we keep
            # historical rows visible until explicit removal).
            from common import session_state as _ssn
            assert _ssn.get("cleared-sess")["state"] == "stopped"
        finally:
            self._clear("cleared-sess")

    def test_tmux_alive_but_cache_empty_seeds_via_pane_parse(
        self, client, mock_tmux,
    ):
        """If the cache lost a row but tmux is still alive (e.g. mid-
        startup before rebuild_from_tmux fires), the endpoint does a
        one-shot pane parse and seeds the cache so the next read is
        consistent."""
        mock_tmux["exists"].return_value = True
        mock_tmux["capture"].return_value = "* Analyzing...\n"
        self._clear("uncached-sess")
        try:
            resp = client.get("/api/sessions/uncached-sess/status")
            assert resp.status_code == 200
            # Pane content "* Analyzing..." -> 'thinking' classifier.
            assert resp.json()["state"] == "thinking"
            # And the cache now has the row.
            from common import session_state as _ssn
            assert _ssn.get("uncached-sess")["state"] == "thinking"
        finally:
            self._clear("uncached-sess")


# ---- Graph API with empty dependencies ----

class TestGraphApiEdgeCases:
    def test_graph_node_with_empty_dependencies(self, client):
        resp = client.get("/api/projects/test-proj/graph")
        data = resp.json()
        node_a = next(n for n in data["nodes"] if n["id"] == "task-a")
        assert node_a["dependencies"] == []
        assert isinstance(node_a["follow_ups"], list)

    def test_graph_node_with_no_follow_ups(self, client):
        resp = client.get("/api/projects/test-proj/graph")
        data = resp.json()
        # task-c is a leaf node with no follow-ups, and task-d also has none
        node_c = next(n for n in data["nodes"] if n["id"] == "task-c")
        assert node_c["follow_ups"] == []
        node_d = next(n for n in data["nodes"] if n["id"] == "task-d")
        assert node_d["follow_ups"] == []

    def test_graph_includes_ticket_and_prs(self, client):
        resp = client.get("/api/projects/test-proj/graph")
        data = resp.json()
        node_d = next(n for n in data["nodes"] if n["id"] == "task-d")
        assert node_d["ticket"] is not None
        assert node_d["ticket"]["id"] == "EX-99999"

    def test_graph_includes_notes(self, client, patched_server):
        # Add notes to a task
        tasks = patched_server.load_tasks("test-proj")
        tasks["task-a"]["notes"] = "Test note for graph"
        patched_server.save_task("test-proj", "task-a", tasks["task-a"])

        resp = client.get("/api/projects/test-proj/graph")
        data = resp.json()
        node_a = next(n for n in data["nodes"] if n["id"] == "task-a")
        assert node_a["notes"] == "Test note for graph"

    def test_graph_node_has_status_field(self, client):
        resp = client.get("/api/projects/test-proj/graph")
        data = resp.json()
        node_a = next(n for n in data["nodes"] if n["id"] == "task-a")
        # Each node should have a status field
        assert "status" in node_a
        assert node_a["status"] == "done"


# ---- Project with tasks that have no group field ----

class TestTasksWithNoGroup:
    def test_task_without_group_field(self, patched_server):
        # Create a task with empty group_name via DB (no group set)
        db = patched_server._db
        db.create_task(project="test-proj", task_id="no-group",
                       description="No group field", group_name="")

        tasks = patched_server.load_tasks("test-proj")
        assert "no-group" in tasks
        # group is aliased from group_name; empty string is the default
        assert tasks["no-group"]["group"] == ""

    def test_graph_handles_no_group(self, client, patched_server):
        # Add a task with empty group_name via DB
        db = patched_server._db
        db.create_task(project="test-proj", task_id="no-group-graph",
                       description="No group field", group_name="")

        resp = client.get("/api/projects/test-proj/graph")
        assert resp.status_code == 200
        data = resp.json()
        node = next(n for n in data["nodes"] if n["id"] == "no-group-graph")
        assert node["group"] == ""

    def test_stats_handle_no_group(self, patched_server):
        # Add a task with no group, status done
        db = patched_server._db
        db.create_task(project="test-proj", task_id="groupless",
                       description="Groupless", status="done", group_name="")

        stats = patched_server.compute_project_stats("test-proj")
        # Should count without error
        assert stats["total"] >= 5  # 4 original + at least 1 new


# ---- Wait for ready ----

class TestWaitForReady:
    def test_returns_ready_when_hook_says_idle(self, client, patched_server, mock_tmux):
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        patched_server._session_states["ready-sess"] = {
            "state": "idle",
            "detail": "Waiting for input",
            "ts": now,
        }

        resp = client.get("/api/sessions/ready-sess/wait-ready?timeout=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True
        assert data["state"] == "idle"

    def test_returns_timeout_when_not_ready(self, client, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = False
        patched_server._session_states.pop("timeout-sess", None)

        resp = client.get("/api/sessions/timeout-sess/wait-ready?timeout=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is False
        assert data["state"] == "timeout"

    def test_detects_ready_via_tmux_prompt(self, client, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = True
        # Unicode prompt character
        mock_tmux["capture"].return_value = "\u276f \n"
        patched_server._session_states.pop("tmux-ready", None)

        resp = client.get("/api/sessions/tmux-ready/wait-ready?timeout=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True

    def test_detects_ready_via_shortcuts_hint(self, client, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = True
        mock_tmux["capture"].return_value = "Press ? for shortcuts\n"
        patched_server._session_states.pop("hint-ready", None)

        resp = client.get("/api/sessions/hint-ready/wait-ready?timeout=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True

    def test_stale_idle_hook_still_polls(self, client, patched_server, mock_tmux):
        # Hook says idle but ts is old (> 10s)
        old_time = (datetime.now() - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%S")
        patched_server._session_states["stale-ready"] = {
            "state": "idle",
            "detail": "old",
            "ts": old_time,
        }
        mock_tmux["exists"].return_value = True
        mock_tmux["capture"].return_value = "\u276f \n"

        resp = client.get("/api/sessions/stale-ready/wait-ready?timeout=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True


# ---- Tmux status parsing edge cases ----

class TestTmuxStatusParsing:
    def test_prompt_with_permission_dialog(self, client, mock_tmux):
        mock_tmux["exists"].return_value = True
        mock_tmux["capture"].return_value = (
            "\u276f something\n"
            "esc to cancel\n"
            "1. yes\n"
        )
        resp = client.get("/api/sessions/perm-prompt/status")
        data = resp.json()
        assert data["state"] == "needs_permission"

    def test_prompt_without_permission(self, client, mock_tmux):
        mock_tmux["exists"].return_value = True
        mock_tmux["capture"].return_value = "\u276f\n"
        resp = client.get("/api/sessions/just-prompt/status")
        data = resp.json()
        assert data["state"] == "idle"

    def test_shortcuts_hint_detected(self, client, mock_tmux):
        mock_tmux["exists"].return_value = True
        mock_tmux["capture"].return_value = (
            "Some output\n"
            "? for shortcuts\n"
        )
        resp = client.get("/api/sessions/hint-sess/status")
        data = resp.json()
        assert data["state"] == "idle"

    def test_multiple_thinking_lines(self, client, mock_tmux):
        mock_tmux["exists"].return_value = True
        mock_tmux["capture"].return_value = (
            "* Reading files...\n"
            "* Analyzing code...\n"
        )
        resp = client.get("/api/sessions/multi-think/status")
        data = resp.json()
        assert data["state"] == "thinking"
        # Should pick the last thinking line
        assert "Analyzing code" in data["detail"]

    def test_empty_tmux_output(self, client, mock_tmux):
        mock_tmux["exists"].return_value = True
        mock_tmux["capture"].return_value = ""
        resp = client.get("/api/sessions/empty-output/status")
        data = resp.json()
        assert data["state"] == "unknown"

    def test_only_blank_lines(self, client, mock_tmux):
        mock_tmux["exists"].return_value = True
        mock_tmux["capture"].return_value = "\n\n\n"
        resp = client.get("/api/sessions/blank-sess/status")
        data = resp.json()
        assert data["state"] == "unknown"


# ---- Suggest status edge cases ----

class TestSuggestStatusEdgeCases:
    def test_task_no_prs_key(self, patched_server):
        task = {"status": "not_started"}
        # No 'prs' key at all
        assert patched_server.suggest_task_status(task) is None

    def test_task_done_status_not_overridden(self, patched_server):
        task = {
            "status": "done",
            "prs": [{"status": "open"}],
        }
        # Already done, open PR suggests in_review but current is done
        # The function checks "current not in (in_review, done)" for any_open
        assert patched_server.suggest_task_status(task) is None

    def test_merged_pr_with_done_status(self, patched_server):
        task = {
            "status": "done",
            "prs": [{"status": "merged"}],
        }
        # Already done with merged PR, no change
        assert patched_server.suggest_task_status(task) is None

    def test_empty_prs_list_with_ticket(self, patched_server):
        task = {
            "status": "not_started",
            "prs": [],
            "ticket": {"id": "EX-123"},
        }
        assert patched_server.suggest_task_status(task) == "in_progress"


# ---- Kill session cleanup ----

class TestKillSessionCleanup:
    @patch("server.subprocess.run")
    def test_kill_session_cleans_up_pty(self, mock_run, client, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = True
        mock_ps = MagicMock()
        import pty_manager
        pty_manager.sessions["cleanup-sess"] = mock_ps

        resp = client.delete("/api/sessions/cleanup-sess")
        assert resp.status_code == 200
        mock_ps.close.assert_called_once()
        assert "cleanup-sess" not in pty_manager.sessions

    @patch("server.subprocess.run")
    def test_kill_session_no_pty(self, mock_run, client, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = True
        import pty_manager
        pty_manager.sessions.pop("no-pty-sess", None)

        resp = client.delete("/api/sessions/no-pty-sess")
        assert resp.status_code == 200
        assert resp.json()["status"] == "killed"


# ---- Resume session with args ----

# ---- Misc load_tasks edge cases ----

class TestLoadTasksEdgeCases:
    def test_task_from_db_has_updated_at(self, patched_server):
        """Tasks loaded from SQLite should always have updated_at."""
        db = patched_server._db
        db.create_task(project="test-proj", task_id="no-ts",
                       description="No timestamp", status="not_started")

        tasks = patched_server.load_tasks("test-proj")
        assert "no-ts" in tasks
        # SQLite always stores updated_at
        assert "updated_at" in tasks["no-ts"]
        assert tasks["no-ts"]["updated_at"] is not None

    def test_empty_task_file_skipped(self, patched_server):
        """Tasks in DB are always valid; no empty-file concept exists."""
        tasks = patched_server.load_tasks("test-proj")
        # No task with id "empty-file" was inserted
        assert "empty-file" not in tasks


# ---- PR detail with invalid JSON chunk in inline comments ----

class TestPrDetailInvalidJsonChunk:
    @patch("server.gh_run")
    def test_skips_invalid_json_in_paginated_comments(self, mock_gh_run, client):
        call_count = [0]
        def gh_side_effect(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({"number": 42, "title": "T"}),
                )
            elif call_count[0] == 2:
                # One valid chunk, one invalid
                valid = json.dumps([{"user": "u1", "body": "ok"}])
                return MagicMock(
                    returncode=0,
                    stdout=valid + "\nNOT-VALID-JSON\n",
                )
            return MagicMock(returncode=1, stdout="")

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/pr-detail?repo=example/repo&number=42")
        assert resp.status_code == 200
        data = resp.json()
        # Should have the valid comment but skip the invalid chunk
        assert len(data["inlineComments"]) == 1
        assert data["inlineComments"][0]["user"] == "u1"


# ---- Wait for ready with invalid timestamp in hook state ----

class TestWaitForReadyInvalidTs:
    def test_bad_ts_in_hook_state_falls_through_to_poll(self, client, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = True
        mock_tmux["capture"].return_value = "\u276f \n"

        patched_server._session_states["bad-ts-wait"] = {
            "state": "idle",
            "detail": "old",
            "ts": "INVALID-TIMESTAMP",
        }

        resp = client.get("/api/sessions/bad-ts-wait/wait-ready?timeout=3")
        assert resp.status_code == 200
        data = resp.json()
        # Should fall through the exception and poll via tmux
        assert data["ready"] is True


# ---- Session cleanup loop ----

class TestNonexistentDependency:
    def test_task_blocked_by_nonexistent_dep(self, patched_server):
        """A task depending on a nonexistent task ID should be blocked."""
        tasks = {
            "real-task": {"status": "merged", "dependencies": []},
            "blocked-task": {"status": "not_started", "dependencies": ["real-task", "ghost-task"]},
        }
        # ghost-task doesn't exist -> .get("ghost-task", {}) -> status=None -> blocked
        assert patched_server.is_task_blocked("blocked-task", tasks) is True

    def test_task_not_blocked_when_all_deps_unblocking(self, patched_server):
        """Updated 2026-04-26: `merged` is a PR status not a task status;
        old test used to coincidentally pass because is_task_blocked
        accepted ('done', 'merged'). New canonical set is
        UNBLOCKING_DEP_STATUSES = {done, closed, needs_follow_up}."""
        tasks = {
            "dep1": {"status": "done"},
            "dep2": {"status": "closed"},
            "dep3": {"status": "needs_follow_up"},
            "task": {"status": "not_started",
                     "dependencies": ["dep1", "dep2", "dep3"]},
        }
        assert patched_server.is_task_blocked("task", tasks) is False

    def test_task_blocked_when_one_dep_in_progress(self, patched_server):
        tasks = {
            "dep1": {"status": "done"},
            "dep2": {"status": "in_review"},
            "task": {"status": "not_started", "dependencies": ["dep1", "dep2"]},
        }
        assert patched_server.is_task_blocked("task", tasks) is True

class TestTaskIdValidation:
    def test_slash_in_id_rejected(self, client, patched_server):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "task/slash", "description": "bad"
        })
        assert resp.status_code == 422

    def test_backslash_in_id_rejected(self, client, patched_server):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "task\\back", "description": "bad"
        })
        assert resp.status_code == 422

    def test_null_byte_in_id_rejected(self, client, patched_server):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "task\x00null", "description": "bad"
        })
        assert resp.status_code == 422

    def test_empty_id_rejected(self, client, patched_server):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "", "description": "bad"
        })
        assert resp.status_code == 422

    def test_dot_dot_id_rejected(self, client, patched_server):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "../../../etc/passwd", "description": "path traversal"
        })
        assert resp.status_code == 422

    def test_valid_id_with_hyphens_dots(self, client, patched_server):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "bench-scalar-arrow.v2", "description": "valid"
        })
        assert resp.status_code == 201

    def test_too_long_id_rejected(self, client, patched_server):
        resp = client.post("/api/projects/test-proj/tasks", json={
            "id": "a" * 201, "description": "too long"
        })
        assert resp.status_code == 422


class TestChainedDependencies:
    def test_10_prs_on_single_task(self, client, patched_server):
        """Task should handle many PRs correctly."""
        config = patched_server.load_config()
        config["projects"]["pr-stress"] = {"name": "PR Stress", "description": "test"}
        patched_server.save_config(config)

        db = patched_server._db
        db.create_task(project="pr-stress", task_id="big",
                       description="Many PRs", status="in_review")

        for p in range(10):
            resp = client.post("/api/projects/pr-stress/tasks/big/prs", json={
                "number": 20000 + p,
                "url": f"https://github.com/test/repo/pull/{20000+p}",
                "status": "merged" if p < 7 else "open",
                "title": f"PR {p}",
            })
            assert resp.status_code == 201

        resp = client.get("/api/projects/pr-stress/tasks/big")
        assert len(resp.json()["prs"]) == 10
