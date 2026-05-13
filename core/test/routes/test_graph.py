"""Integration tests for the dependency graph API endpoint."""


class TestDependencyGraph:
    def test_graph_returns_nodes_and_edges(self, client):
        resp = client.get("/api/projects/test-proj/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert "groups" in data

    def test_graph_has_correct_node_count(self, client):
        resp = client.get("/api/projects/test-proj/graph")
        data = resp.json()
        assert len(data["nodes"]) == 4

    def test_graph_node_fields(self, client):
        resp = client.get("/api/projects/test-proj/graph")
        data = resp.json()
        node_a = next(n for n in data["nodes"] if n["id"] == "task-a")
        assert node_a["description"] == "Task A - foundation work"
        assert node_a["type"] == "feature"
        assert node_a["group"] == "core"
        assert node_a["status"] == "done"

    def test_graph_blocked_status(self, client):
        resp = client.get("/api/projects/test-proj/graph")
        data = resp.json()
        node_c = next(n for n in data["nodes"] if n["id"] == "task-c")
        # task-c is not_started but blocked because task-b is in_progress
        assert node_c["status"] == "blocked"

    def test_graph_edges_from_dependencies(self, client):
        resp = client.get("/api/projects/test-proj/graph")
        data = resp.json()
        edges = data["edges"]
        # task-b depends on task-a -> edge from task-a to task-b
        assert {"from": "task-a", "to": "task-b"} in edges
        # task-c depends on task-b -> edge from task-b to task-c
        assert {"from": "task-b", "to": "task-c"} in edges

    def test_graph_follow_ups_default_empty(self, client):
        """follow_ups defaults to empty list when none are set in the DB."""
        resp = client.get("/api/projects/test-proj/graph")
        data = resp.json()
        node_a = next(n for n in data["nodes"] if n["id"] == "task-a")
        assert node_a["follow_ups"] == []
        node_b = next(n for n in data["nodes"] if n["id"] == "task-b")
        assert node_b["follow_ups"] == []

    def test_graph_follow_ups_from_db(self, patched_server, client):
        """follow_ups set via update_task appear on graph nodes."""
        patched_server._db.update_task(
            "test-proj", "task-a",
            follow_ups=["rebase on latest master", "fix type hints"],
        )
        resp = client.get("/api/projects/test-proj/graph")
        data = resp.json()
        node_a = next(n for n in data["nodes"] if n["id"] == "task-a")
        assert node_a["follow_ups"] == ["rebase on latest master", "fix type hints"]

    def test_graph_groups(self, client):
        resp = client.get("/api/projects/test-proj/graph")
        data = resp.json()
        groups = data["groups"]
        assert "core" in groups
        assert "extension" in groups

    def test_graph_nonexistent_project(self, client):
        resp = client.get("/api/projects/nonexistent/graph")
        assert resp.status_code == 404

    def test_graph_empty_project(self, client):
        resp = client.get("/api/projects/empty-proj/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"] == []
        assert data["edges"] == []
