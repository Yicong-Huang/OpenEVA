"""Integration tests for project API endpoints."""


class TestListProjects:
    def test_returns_all_projects(self, client):
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data
        ids = [p["id"] for p in data["projects"]]
        assert "test-proj" in ids
        assert "empty-proj" in ids

    def test_project_has_expected_fields(self, client):
        resp = client.get("/api/projects")
        data = resp.json()
        proj = next(p for p in data["projects"] if p["id"] == "test-proj")
        assert proj["name"] == "Test Project"
        assert proj["description"] == "A test project"
        assert "progress" in proj
        assert "total_tasks" in proj
        assert "task_counts" in proj

    def test_project_stats_correct(self, client):
        resp = client.get("/api/projects")
        data = resp.json()
        proj = next(p for p in data["projects"] if p["id"] == "test-proj")
        assert proj["total_tasks"] == 4
        assert proj["progress"] == 25.0

    def test_empty_project_stats(self, client):
        resp = client.get("/api/projects")
        data = resp.json()
        proj = next(p for p in data["projects"] if p["id"] == "empty-proj")
        assert proj["total_tasks"] == 0
        assert proj["progress"] == 0.0


class TestGetProject:
    def test_get_existing_project(self, client):
        resp = client.get("/api/projects/test-proj")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "test-proj"
        assert data["name"] == "Test Project"
        assert "tasks" in data
        assert len(data["tasks"]) == 4

    def test_get_project_includes_tasks(self, client):
        resp = client.get("/api/projects/test-proj")
        data = resp.json()
        assert "task-a" in data["tasks"]
        assert "task-b" in data["tasks"]
        task_a = data["tasks"]["task-a"]
        assert task_a["description"] == "Task A - foundation work"

    def test_get_project_has_computed_fields(self, client):
        resp = client.get("/api/projects/test-proj")
        data = resp.json()
        assert "progress" in data
        assert "task_counts" in data
        # follow_ups is a DB field; defaults to empty list
        assert isinstance(data["tasks"]["task-a"]["follow_ups"], list)

    def test_get_nonexistent_project(self, client):
        resp = client.get("/api/projects/nonexistent")
        assert resp.status_code == 404

    def test_get_empty_project(self, client):
        resp = client.get("/api/projects/empty-proj")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "empty-proj"
        assert data["tasks"] == {}
        assert data["progress"] == 0.0

    def test_get_project_not_found(self, client):
        resp = client.get("/api/projects/does-not-exist")
        assert resp.status_code == 404

    def test_project_has_progress(self, client):
        resp = client.get("/api/projects/test-proj")
        assert resp.status_code == 200
        data = resp.json()
        assert "progress" in data
        # 1 of 4 tasks is done -> 25%
        assert data["progress"] == 25.0


class TestProjectGraph:
    def test_graph_has_follow_ups_default_empty(self, client):
        """follow_ups defaults to empty list when none are set in the DB."""
        resp = client.get("/api/projects/test-proj/graph")
        assert resp.status_code == 200
        data = resp.json()
        node_a = next(n for n in data["nodes"] if n["id"] == "task-a")
        assert "follow_ups" in node_a
        assert node_a["follow_ups"] == []

    def test_graph_has_follow_ups_from_db(self, patched_server, client):
        """follow_ups set via update_task appear on graph nodes."""
        patched_server._db.update_task(
            "test-proj", "task-a",
            follow_ups=["rebase on latest master", "fix type hints"],
        )
        resp = client.get("/api/projects/test-proj/graph")
        assert resp.status_code == 200
        data = resp.json()
        node_a = next(n for n in data["nodes"] if n["id"] == "task-a")
        assert node_a["follow_ups"] == ["rebase on latest master", "fix type hints"]

    def test_graph_blocked_status(self, client):
        """task-c depends on task-b (in_progress); its status should be shown as blocked."""
        resp = client.get("/api/projects/test-proj/graph")
        assert resp.status_code == 200
        data = resp.json()
        node_c = next(n for n in data["nodes"] if n["id"] == "task-c")
        assert node_c["status"] == "blocked"
