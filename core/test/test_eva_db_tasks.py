"""Tests for EvaDB (formerly TaskDB) - SQLite schema and CRUD operations."""

import tempfile
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eva_db import EvaDB


@pytest.fixture
def db():
    """Provide an EvaDB backed by a temporary file, cleaned up after the test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    tdb = EvaDB(db_path)
    yield tdb
    tdb.close()
    os.unlink(db_path)


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------

def test_init_creates_tables(db):
    """All three tables and indexes should be created on init."""
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row["name"] for row in cur.fetchall()}
    assert "tasks" in tables
    assert "task_dependencies" in tables
    assert "prs" in tables

    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
    )
    indexes = {row["name"] for row in cur.fetchall()}
    assert "idx_tasks_project" in indexes
    assert "idx_tasks_project_status" in indexes
    assert "idx_prs_project_task" in indexes


# ------------------------------------------------------------------
# create_task
# ------------------------------------------------------------------

def test_create_task(db):
    """create_task should insert a row and return a dict with all expected fields."""
    task = db.create_task(
        project="proj-a",
        task_id="task-1",
        description="First task",
        type="feature",
        status="not_started",
        group_name="core",
        notes="some notes",
        ticket_id="EX-1",
        ticket_url="https://issues.example.org/jira/browse/EX-1",
    )

    assert task["project"] == "proj-a"
    assert task["task_id"] == "task-1"
    assert task["description"] == "First task"
    assert task["type"] == "feature"
    assert task["status"] == "not_started"
    assert task["group_name"] == "core"
    assert task["notes"] == "some notes"
    assert task["ticket_id"] == "EX-1"
    assert task["ticket_url"] == "https://issues.example.org/jira/browse/EX-1"
    assert task["created_at"] is not None
    assert task["updated_at"] is not None
    assert task["dependencies"] == []
    assert task["prs"] == []


def test_create_duplicate_raises(db):
    """Inserting a task with the same (project, task_id) should raise an error."""
    db.create_task("proj-a", "task-1", description="First")
    with pytest.raises(Exception):
        db.create_task("proj-a", "task-1", description="Duplicate")


# ------------------------------------------------------------------
# update_task
# ------------------------------------------------------------------

def test_update_task(db):
    """update_task should modify only the specified fields."""
    db.create_task("proj-a", "task-1", description="Old desc", status="not_started")
    updated = db.update_task("proj-a", "task-1", status="in_progress", description="New desc")

    assert updated["status"] == "in_progress"
    assert updated["description"] == "New desc"
    # updated_at should be refreshed
    assert updated["updated_at"] is not None

    # Fields not provided should remain unchanged
    assert updated["type"] == "feature"


def test_update_task_ignores_unknown_fields(db):
    """update_task should silently ignore fields not in the allowed set."""
    db.create_task("proj-a", "task-1", description="desc", status="not_started")
    result = db.update_task("proj-a", "task-1", bogus_field="value", status="done")
    assert result["status"] == "done"


# ------------------------------------------------------------------
# get_task
# ------------------------------------------------------------------

def test_get_nonexistent_returns_none(db):
    """get_task should return None for an unknown (project, task_id)."""
    result = db.get_task("no-such-project", "no-such-task")
    assert result is None


# ------------------------------------------------------------------
# list_tasks
# ------------------------------------------------------------------

def test_list_tasks(db):
    """list_tasks should return only tasks belonging to the requested project."""
    db.create_task("proj-a", "task-1", description="A1")
    db.create_task("proj-a", "task-2", description="A2")
    db.create_task("proj-b", "task-1", description="B1")

    proj_a_tasks = db.list_tasks("proj-a")
    assert len(proj_a_tasks) == 2
    ids = {t["task_id"] for t in proj_a_tasks}
    assert ids == {"task-1", "task-2"}

    proj_b_tasks = db.list_tasks("proj-b")
    assert len(proj_b_tasks) == 1
    assert proj_b_tasks[0]["task_id"] == "task-1"

    empty = db.list_tasks("proj-c")
    assert empty == []


# ------------------------------------------------------------------
# delete_task
# ------------------------------------------------------------------

def test_delete_task(db):
    """delete_task should remove the task and cascade to dependencies and prs."""
    db.create_task("proj-a", "task-1")

    # Insert a dependency and a PR manually to verify cascade
    db._conn.execute(
        "INSERT INTO task_dependencies (project, task_id, depends_on) VALUES (?, ?, ?)",
        ("proj-a", "task-1", "task-0"),
    )
    db._conn.execute(
        """
        INSERT INTO prs (project, task_id, number, url, status, title)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("proj-a", "task-1", 42, "https://github.com/example/repo/pull/42", "open", "My PR"),
    )
    db._conn.commit()

    # Confirm the related rows exist
    assert db.get_task("proj-a", "task-1")["dependencies"] == ["task-0"]
    assert len(db.get_task("proj-a", "task-1")["prs"]) == 1

    deleted = db.delete_task("proj-a", "task-1")
    assert deleted is True

    # Task should be gone
    assert db.get_task("proj-a", "task-1") is None

    # Cascade: dependency and PR rows should also be gone
    cur = db._conn.execute(
        "SELECT COUNT(*) AS cnt FROM task_dependencies WHERE project='proj-a' AND task_id='task-1'"
    )
    assert cur.fetchone()["cnt"] == 0

    cur = db._conn.execute(
        "SELECT COUNT(*) AS cnt FROM prs WHERE project='proj-a' AND task_id='task-1'"
    )
    assert cur.fetchone()["cnt"] == 0


def test_delete_nonexistent_returns_false(db):
    """delete_task on a missing task should return False."""
    result = db.delete_task("no-project", "no-task")
    assert result is False


# ------------------------------------------------------------------
# TestDependencies
# ------------------------------------------------------------------

class TestDependencies:
    def test_add_dependency(self, db):
        """add_dependency inserts a row visible via get_task dependencies."""
        db.create_task("proj-a", "task-1")
        db.create_task("proj-a", "task-0")
        db.add_dependency("proj-a", "task-1", "task-0")
        task = db.get_task("proj-a", "task-1")
        assert "task-0" in task["dependencies"]

    def test_remove_dependency(self, db):
        """remove_dependency deletes the dep row, leaving dependencies empty."""
        db.create_task("proj-a", "task-1")
        db.create_task("proj-a", "task-0")
        db.add_dependency("proj-a", "task-1", "task-0")
        db.remove_dependency("proj-a", "task-1", "task-0")
        task = db.get_task("proj-a", "task-1")
        assert task["dependencies"] == []

    def test_set_dependencies(self, db):
        """set_dependencies replaces all deps atomically."""
        db.create_task("proj-a", "task-1")
        db.create_task("proj-a", "task-0")
        db.create_task("proj-a", "task-2")
        db.add_dependency("proj-a", "task-1", "task-0")
        db.set_dependencies("proj-a", "task-1", ["task-2", "task-0"])
        task = db.get_task("proj-a", "task-1")
        assert set(task["dependencies"]) == {"task-0", "task-2"}

    def test_is_blocked(self, db):
        """is_task_blocked returns True when dep not done, False when dep done."""
        db.create_task("proj-a", "task-0", status="in_progress")
        db.create_task("proj-a", "task-1")
        db.add_dependency("proj-a", "task-1", "task-0")

        assert db.is_task_blocked("proj-a", "task-1") is True

        db.update_task("proj-a", "task-0", status="done")
        assert db.is_task_blocked("proj-a", "task-1") is False

        db.update_task("proj-a", "task-0", status="closed")
        assert db.is_task_blocked("proj-a", "task-1") is False

    def test_is_blocked_nonexistent_dep(self, db):
        """is_task_blocked returns True when the dep task does not exist in tasks."""
        db.create_task("proj-a", "task-1")
        db._conn.execute(
            "INSERT INTO task_dependencies (project, task_id, depends_on) VALUES (?, ?, ?)",
            ("proj-a", "task-1", "ghost-task"),
        )
        db._conn.commit()
        assert db.is_task_blocked("proj-a", "task-1") is True


# ------------------------------------------------------------------
# TestPRs
# ------------------------------------------------------------------

class TestPRs:
    def test_add_pr(self, db):
        """add_pr inserts a PR row visible via get_task prs list."""
        db.create_task("proj-a", "task-1")
        db.add_pr(
            "proj-a", "task-1", 42,
            url="https://github.com/example/repo/pull/42",
            status="open",
            title="My PR",
            session="sess-1",
            working_dir="~/repo",
        )
        task = db.get_task("proj-a", "task-1")
        assert len(task["prs"]) == 1
        pr = task["prs"][0]
        assert pr["number"] == 42
        assert pr["url"] == "https://github.com/example/repo/pull/42"
        assert pr["status"] == "open"
        assert pr["title"] == "My PR"
        assert pr["session"] == "sess-1"
        assert pr["working_dir"] == "~/repo"

    def test_add_duplicate_pr_raises(self, db):
        """Adding a PR with the same project+task_id+number should raise."""
        db.create_task("proj-a", "task-1")
        db.add_pr("proj-a", "task-1", 42)
        with pytest.raises(Exception):
            db.add_pr("proj-a", "task-1", 42)

    def test_delete_pr(self, db):
        """delete_pr removes the PR row."""
        db.create_task("proj-a", "task-1")
        db.add_pr("proj-a", "task-1", 42)
        deleted = db.delete_pr("proj-a", "task-1", 42)
        assert deleted is True
        task = db.get_task("proj-a", "task-1")
        assert task["prs"] == []

    def test_update_pr(self, db):
        """update_pr modifies only the specified fields."""
        db.create_task("proj-a", "task-1")
        db.add_pr("proj-a", "task-1", 42, status="open", title="Original")
        db.update_pr("proj-a", "task-1", 42, status="merged")
        task = db.get_task("proj-a", "task-1")
        pr = task["prs"][0]
        assert pr["status"] == "merged"
        assert pr["title"] == "Original"


# ------------------------------------------------------------------
# TestStats
# ------------------------------------------------------------------

class TestStats:
    def test_project_stats(self, db):
        """project_stats returns correct counts and progress for a mixed project."""
        db.create_task("proj-s", "t1", status="done")
        db.create_task("proj-s", "t2", status="not_started")
        db.create_task("proj-s", "t3", status="in_review")

        stats = db.project_stats("proj-s")

        assert stats["total"] == 3
        assert stats["counts"]["done"] == 1
        assert stats["counts"]["not_started"] == 1
        assert stats["counts"]["in_review"] == 1
        assert stats["counts"]["in_progress"] == 0
        # `blocked` is computed, not stored -- not a key in counts now.
        assert "blocked" not in stats["counts"]
        assert stats["progress"] == 33.3

    def test_empty_project_stats(self, db):
        """project_stats returns zeros and 0.0 progress when no tasks exist."""
        stats = db.project_stats("no-such-project")

        assert stats["total"] == 0
        assert stats["progress"] == 0.0
        for cnt in stats["counts"].values():
            assert cnt == 0


# ------------------------------------------------------------------
# TestGraph
# ------------------------------------------------------------------

class TestGraph:
    def test_dependency_graph(self, db):
        """dependency_graph returns nodes, edges, and groups for a simple project."""
        db.create_task("proj-g", "t1", status="done", group_name="alpha")
        db.create_task("proj-g", "t2", status="not_started", group_name="beta")
        db.add_dependency("proj-g", "t2", "t1")

        graph = db.dependency_graph("proj-g")

        node_ids = {n["task_id"] for n in graph["nodes"]}
        assert node_ids == {"t1", "t2"}
        assert {"from": "t1", "to": "t2"} in graph["edges"]
        assert "alpha" in graph["groups"]
        assert "beta" in graph["groups"]

    def test_graph_blocked_status(self, db):
        """dependency_graph overrides status to 'blocked' when dep is not done."""
        db.create_task("proj-g", "blocker", status="not_started")
        db.create_task("proj-g", "dependent", status="not_started")
        db.add_dependency("proj-g", "dependent", "blocker")

        graph = db.dependency_graph("proj-g")

        dep_node = next(n for n in graph["nodes"] if n["task_id"] == "dependent")
        assert dep_node["status"] == "blocked"

    def test_graph_unblocked(self, db):
        """dependency_graph keeps status 'not_started' when dep is done."""
        db.create_task("proj-g", "done-task", status="done")
        db.create_task("proj-g", "next-task", status="not_started")
        db.add_dependency("proj-g", "next-task", "done-task")

        graph = db.dependency_graph("proj-g")

        next_node = next(n for n in graph["nodes"] if n["task_id"] == "next-task")
        assert next_node["status"] == "not_started"


# ------------------------------------------------------------------
# find_task_by_ticket
# ------------------------------------------------------------------

def test_find_task_by_ticket(db):
    """find_task_by_ticket returns (project, task_id) when ticket_id matches."""
    db.create_task("proj-a", "task-1", ticket_id="EX-123")
    result = db.find_task_by_ticket("EX-123")
    assert result == ("proj-a", "task-1")


def test_find_task_by_ticket_not_found(db):
    """find_task_by_ticket returns None when no match exists."""
    result = db.find_task_by_ticket("EX-999")
    assert result is None


def test_find_task_by_ticket_description_fallback(db):
    """find_task_by_ticket falls back to description search when no ticket_id match."""
    db.create_task("proj-a", "task-2", description="Implements EX-456 behavior")
    result = db.find_task_by_ticket("EX-456")
    assert result == ("proj-a", "task-2")


# ------------------------------------------------------------------
# rename_task
# ------------------------------------------------------------------

def test_rename_task(db):
    """rename_task renames the task, transfers deps and PRs, removes old task."""
    db.create_task("proj-a", "task-old")
    db.create_task("proj-a", "dep-task")
    db.add_dependency("proj-a", "task-old", "dep-task")
    db.add_pr("proj-a", "task-old", 99, url="https://github.com/example/repo/pull/99", status="open", title="PR")

    result = db.rename_task("proj-a", "task-old", "task-new")
    assert result is True

    # New task exists with the PR and dependency transferred
    new_task = db.get_task("proj-a", "task-new")
    assert new_task is not None
    assert "dep-task" in new_task["dependencies"]
    assert len(new_task["prs"]) == 1
    assert new_task["prs"][0]["number"] == 99

    # Old task is gone
    assert db.get_task("proj-a", "task-old") is None


def test_rename_task_not_found(db):
    """rename_task returns False when the source task does not exist."""
    result = db.rename_task("proj-a", "nonexistent", "task-new")
    assert result is False


def test_rename_task_target_exists(db):
    """rename_task returns False when the target task_id already exists."""
    db.create_task("proj-a", "task-1")
    db.create_task("proj-a", "task-2")
    result = db.rename_task("proj-a", "task-1", "task-2")
    assert result is False


def test_rename_task_reverse_deps(db):
    """rename_task updates reverse dependencies so other tasks point to new name."""
    db.create_task("proj-a", "task-a")
    db.create_task("proj-a", "task-b")
    db.add_dependency("proj-a", "task-b", "task-a")

    db.rename_task("proj-a", "task-a", "task-a-renamed")

    task_b = db.get_task("proj-a", "task-b")
    assert "task-a-renamed" in task_b["dependencies"]
    assert "task-a" not in task_b["dependencies"]


# ------------------------------------------------------------------
# Edge cases: task CRUD
# ------------------------------------------------------------------

def test_create_task_duplicate(db):
    """create_task with duplicate (project, task_id) should raise IntegrityError."""
    from pysqlite3 import dbapi2 as sqlite3
    db.create_task("proj-a", "task-dup", description="First")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_task("proj-a", "task-dup", description="Second")


def test_update_task_nonexistent(db):
    """update_task on a nonexistent task should return None (no rows matched)."""
    result = db.update_task("proj-x", "no-such-task", status="done")
    assert result is None


def test_delete_task_nonexistent(db):
    """delete_task on a nonexistent task should return False."""
    result = db.delete_task("proj-x", "no-such-task")
    assert result is False


def test_update_task_ignores_unknown_fields_new(db):
    """update_task with only unknown fields should be a silent no-op returning the task."""
    db.create_task("proj-a", "task-1", description="desc", status="not_started")
    result = db.update_task("proj-a", "task-1", unknown_field="value", another_bad="x")
    # Should return unchanged task, not None
    assert result is not None
    assert result["status"] == "not_started"


# ------------------------------------------------------------------
# Edge cases: PR
# ------------------------------------------------------------------

def test_add_pr_duplicate(db):
    """add_pr with same project+task_id+number should raise an exception."""
    db.create_task("proj-a", "task-1")
    db.add_pr("proj-a", "task-1", 42)
    with pytest.raises(Exception):
        db.add_pr("proj-a", "task-1", 42)


def test_update_pr_nonexistent(db):
    """update_pr on a nonexistent PR number should be a silent no-op (no error)."""
    db.create_task("proj-a", "task-1")
    # Should not raise; just does nothing
    db.update_pr("proj-a", "task-1", 9999, status="merged")


def test_list_all_prs_with_search(db):
    """list_all_prs with search= should filter by title substring."""
    db.create_task("proj-a", "task-1")
    db.create_task("proj-a", "task-2")
    db.add_pr("proj-a", "task-1", 10, title="Fix Arrow cast in UDF")
    db.add_pr("proj-a", "task-2", 20, title="Refactor serializer code")

    results = db.list_all_prs(search="Arrow")
    assert len(results) == 1
    assert results[0]["number"] == 10

    all_results = db.list_all_prs(search="")
    assert len(all_results) == 2


def test_list_all_prs_with_status_filter(db):
    """list_all_prs with status= should return only matching PRs."""
    db.create_task("proj-a", "task-1")
    db.create_task("proj-a", "task-2")
    db.add_pr("proj-a", "task-1", 10, status="open")
    db.add_pr("proj-a", "task-2", 20, status="merged")

    open_prs = db.list_all_prs(status="open")
    assert len(open_prs) == 1
    assert open_prs[0]["number"] == 10

    merged_prs = db.list_all_prs(status="merged")
    assert len(merged_prs) == 1
    assert merged_prs[0]["number"] == 20


# ------------------------------------------------------------------
# Edge cases: dependencies
# ------------------------------------------------------------------

def test_is_task_blocked_missing_dependency(db):
    """A task depending on a nonexistent task should be reported as blocked."""
    db.create_task("proj-a", "task-1")
    db._conn.execute(
        "INSERT INTO task_dependencies (project, task_id, depends_on) VALUES (?, ?, ?)",
        ("proj-a", "task-1", "ghost"),
    )
    db._conn.commit()
    assert db.is_task_blocked("proj-a", "task-1") is True


def test_set_dependencies_empty_list(db):
    """set_dependencies with [] should clear all dependencies."""
    db.create_task("proj-a", "task-1")
    db.create_task("proj-a", "task-0")
    db.add_dependency("proj-a", "task-1", "task-0")
    assert db.get_task("proj-a", "task-1")["dependencies"] == ["task-0"]

    db.set_dependencies("proj-a", "task-1", [])
    assert db.get_task("proj-a", "task-1")["dependencies"] == []


def test_circular_dependency_allowed(db):
    """DB allows circular deps (A->B, B->A); set_dependencies just stores them."""
    db.create_task("proj-a", "task-a")
    db.create_task("proj-a", "task-b")
    # A depends on B, B depends on A
    db.set_dependencies("proj-a", "task-a", ["task-b"])
    db.set_dependencies("proj-a", "task-b", ["task-a"])

    deps_a = db.get_task("proj-a", "task-a")["dependencies"]
    deps_b = db.get_task("proj-a", "task-b")["dependencies"]
    assert "task-b" in deps_a
    assert "task-a" in deps_b


# ------------------------------------------------------------------
# Edge cases: project_stats
# ------------------------------------------------------------------

def test_project_stats_empty_project(db):
    """project_stats for a project with no tasks returns total=0, progress=0.0."""
    stats = db.project_stats("no-tasks-proj")
    assert stats["total"] == 0
    assert stats["progress"] == 0.0
    for cnt in stats["counts"].values():
        assert cnt == 0


def test_project_stats_all_done(db):
    """project_stats when all tasks are done should report progress=100.0."""
    db.create_task("proj-all-done", "t1", status="done")
    db.create_task("proj-all-done", "t2", status="done")
    db.create_task("proj-all-done", "t3", status="done")

    stats = db.project_stats("proj-all-done")
    assert stats["total"] == 3
    assert stats["counts"]["done"] == 3
    assert stats["progress"] == 100.0
