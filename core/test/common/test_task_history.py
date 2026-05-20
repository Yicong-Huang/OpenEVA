"""Tests for the append-only task history timeline.

Covers:
- DB: append validates length + task existence; list orders newest first;
  cascade on task delete; unique IDs across multiple appends.
- Core: append_history wraps DB + emits event.
- Route: POST/GET /api/projects/{pid}/tasks/{tid}/history with status codes.
- Get-task path: `history` field embedded in task dict.
"""

import common
from unittest.mock import patch


# ---------------------------------------------------------------------------
# DB layer
# ---------------------------------------------------------------------------

class TestDBAppend:
    def test_append_basic(self, patched_server):
        db = patched_server._db
        entry = db.append_task_history("test-proj", "task-a", "finished impl")
        assert entry["text"] == "finished impl"
        assert entry["ts"]  # non-empty iso timestamp

    def test_rejects_empty_text(self, patched_server):
        import pytest
        db = patched_server._db
        for bad in ("", "   ", None):
            with pytest.raises(ValueError, match="empty"):
                db.append_task_history("test-proj", "task-a", bad or "")

    def test_rejects_over_100_chars(self, patched_server):
        import pytest
        db = patched_server._db
        with pytest.raises(ValueError, match="100"):
            db.append_task_history("test-proj", "task-a", "x" * 101)

    def test_100_chars_exact_is_allowed(self, patched_server):
        db = patched_server._db
        db.append_task_history("test-proj", "task-a", "x" * 100)

    def test_rejects_unknown_task(self, patched_server):
        import pytest
        db = patched_server._db
        with pytest.raises(ValueError, match="not found"):
            db.append_task_history("test-proj", "ghost-xyz", "hi")

    def test_unknown_project_is_silently_accepted(self, patched_server):
        # Post-Phase-2 merge: task_id is globally unique, so the
        # `project` argument to append_task_history is informational
        # only -- a mismatch no longer raises. The task lookup is
        # still by task_id, which we know exists in the seed data
        # under "test-proj".
        db = patched_server._db
        out = db.append_task_history("nope", "task-a", "hi")
        assert out["text"] == "hi"


class TestDBList:
    def test_newest_first(self, patched_server):
        db = patched_server._db
        db.append_task_history("test-proj", "task-a", "first", ts="2026-04-20T10:00:00Z")
        db.append_task_history("test-proj", "task-a", "second", ts="2026-04-20T11:00:00Z")
        db.append_task_history("test-proj", "task-a", "third", ts="2026-04-20T12:00:00Z")
        hist = db.list_task_history("test-proj", "task-a")
        assert [e["text"] for e in hist] == ["third", "second", "first"]

    def test_honors_limit(self, patched_server):
        db = patched_server._db
        for i in range(5):
            db.append_task_history("test-proj", "task-a", f"entry {i}")
        assert len(db.list_task_history("test-proj", "task-a", limit=2)) == 2

    def test_empty_when_no_history(self, patched_server):
        db = patched_server._db
        assert db.list_task_history("test-proj", "task-a") == []

    def test_scoped_to_single_task(self, patched_server):
        db = patched_server._db
        db.append_task_history("test-proj", "task-a", "for a")
        db.append_task_history("test-proj", "task-b", "for b")
        hist_a = db.list_task_history("test-proj", "task-a")
        assert len(hist_a) == 1 and hist_a[0]["text"] == "for a"


class TestDBCascade:
    def test_delete_task_wipes_history(self, patched_server):
        db = patched_server._db
        db.append_task_history("test-proj", "task-a", "about to delete")
        assert db.list_task_history("test-proj", "task-a")
        db.delete_task("test-proj", "task-a")
        assert db.list_task_history("test-proj", "task-a") == []


class TestGetTaskEmbedsHistory:
    def test_get_task_includes_history_array(self, patched_server):
        db = patched_server._db
        db.append_task_history("test-proj", "task-a", "step 1")
        db.append_task_history("test-proj", "task-a", "step 2")
        t = db.get_task("test-proj", "task-a")
        assert "history" in t
        assert [e["text"] for e in t["history"]] == ["step 2", "step 1"]

    def test_get_task_history_empty_array_when_none(self, patched_server):
        t = patched_server._db.get_task("test-proj", "task-a")
        assert t["history"] == []


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

class TestCore:
    def test_append_history_emits_event(self, patched_server):
        from common.tasks import append_history
        emitted = []
        with patch("app_state.emit_event",
                   side_effect=lambda t, d, **k: emitted.append((t, d, k))):
            append_history("test-proj", "task-a", "did a thing")
        types = [e[0] for e in emitted]
        assert "task.history.appended" in types


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class TestRoutes:
    def test_post_returns_201_and_entry(self, client, patched_server):
        resp = client.post(
            "/api/projects/test-proj/tasks/task-a/history",
            json={"text": "checkpoint"},
        )
        assert resp.status_code == 201
        d = resp.json()
        assert d["text"] == "checkpoint"
        assert d["ts"]

    def test_post_too_long_returns_422(self, client, patched_server):
        resp = client.post(
            "/api/projects/test-proj/tasks/task-a/history",
            json={"text": "x" * 200},
        )
        assert resp.status_code == 422
        assert "100" in resp.json()["detail"]

    def test_post_empty_returns_422(self, client, patched_server):
        resp = client.post(
            "/api/projects/test-proj/tasks/task-a/history",
            json={"text": "  "},
        )
        assert resp.status_code == 422

    def test_post_unknown_task_returns_404(self, client, patched_server):
        resp = client.post(
            "/api/projects/test-proj/tasks/ghost/history",
            json={"text": "hi"},
        )
        assert resp.status_code == 404

    def test_get_returns_history(self, client, patched_server):
        patched_server._db.append_task_history("test-proj", "task-a", "earlier")
        patched_server._db.append_task_history("test-proj", "task-a", "later")
        resp = client.get("/api/projects/test-proj/tasks/task-a/history")
        assert resp.status_code == 200
        items = resp.json()["history"]
        assert [e["text"] for e in items] == ["later", "earlier"]

    def test_get_honors_limit(self, client, patched_server):
        for i in range(4):
            patched_server._db.append_task_history("test-proj", "task-a", f"e{i}")
        resp = client.get("/api/projects/test-proj/tasks/task-a/history?limit=2")
        assert len(resp.json()["history"]) == 2
