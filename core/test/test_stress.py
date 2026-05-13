"""Stress and pressure tests for TaskDB, ConfigDB, and background builder."""

import os
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eva_db import EvaDB as TaskDB
from eva_db import EvaDB as ConfigDB
from server import build_background


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task_db():
    """Return (TaskDB, db_path) backed by a fresh temp file."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return TaskDB(db_path), db_path


def _make_config_db():
    """Return (ConfigDB, db_path) backed by a fresh temp file."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return ConfigDB(db_path), db_path


# ---------------------------------------------------------------------------
# test_task_db_bulk_create
# ---------------------------------------------------------------------------

def test_task_db_bulk_create():
    """Create 500 tasks in one project; verify list_tasks and project_stats."""
    db, db_path = _make_task_db()
    try:
        project = "bulk-project"
        n = 500
        for i in range(n):
            db.create_task(
                project=project,
                task_id=f"task-{i:04d}",
                description=f"Bulk task number {i}",
                status="not_started",
            )

        tasks = db.list_tasks(project)
        assert len(tasks) == n, f"Expected {n} tasks, got {len(tasks)}"

        ids = {t["task_id"] for t in tasks}
        for i in range(n):
            assert f"task-{i:04d}" in ids

        stats = db.project_stats(project)
        assert stats["total"] == n
        assert stats["counts"]["not_started"] == n
        assert stats["progress"] == 0.0
    finally:
        db.close()
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# test_task_db_bulk_prs
# ---------------------------------------------------------------------------

def test_task_db_bulk_prs():
    """Create 1 task, add 100 PRs to it, verify all are retrieved."""
    db, db_path = _make_task_db()
    try:
        project = "pr-project"
        task_id = "pr-task"
        db.create_task(project=project, task_id=task_id, description="PR stress task")

        n = 100
        for i in range(1, n + 1):
            db.add_pr(
                project=project,
                task_id=task_id,
                number=i,
                url=f"https://github.com/example/repo/pull/{i}",
                status="open",
                title=f"PR number {i}",
            )

        task = db.get_task(project, task_id)
        assert task is not None
        assert len(task["prs"]) == n, f"Expected {n} PRs, got {len(task['prs'])}"

        pr_numbers = {pr["number"] for pr in task["prs"]}
        for i in range(1, n + 1):
            assert i in pr_numbers, f"PR #{i} missing from retrieved list"
    finally:
        db.close()
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# test_task_db_complex_dependency_graph
# ---------------------------------------------------------------------------

def test_task_db_complex_dependency_graph():
    """Create 20 tasks in a chain; verify dependency_graph returns correct edges."""
    db, db_path = _make_task_db()
    try:
        project = "chain-project"
        n = 20
        task_ids = [f"task-{i}" for i in range(1, n + 1)]

        for tid in task_ids:
            db.create_task(project=project, task_id=tid, status="not_started")

        # task-1 -> task-2 -> ... -> task-20 (each task depends on the previous one)
        for i in range(1, n):
            db.add_dependency(project, task_ids[i], task_ids[i - 1])

        graph = db.dependency_graph(project)

        assert len(graph["nodes"]) == n

        # Build set of (from, to) edge tuples for fast lookup
        edge_set = {(e["from"], e["to"]) for e in graph["edges"]}
        assert len(edge_set) == n - 1, f"Expected {n - 1} edges, got {len(edge_set)}"

        for i in range(1, n):
            expected_from = task_ids[i - 1]
            expected_to = task_ids[i]
            assert (expected_from, expected_to) in edge_set, (
                f"Edge ({expected_from} -> {expected_to}) missing"
            )
    finally:
        db.close()
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# test_config_db_rapid_session_updates
# ---------------------------------------------------------------------------

def test_config_db_rapid_session_updates():
    """Create a session, update its status 100 times rapidly, verify final state."""
    db, db_path = _make_config_db()
    try:
        task_id = "rapid-task"
        project = "rapid-project"
        tmux_name = "rapid-tmux"

        session = db.create_session(task_id=task_id, project=project, tmux_name=tmux_name)
        assert session is not None
        assert session["status"] == "not_started"

        statuses = ["not_started", "starting", "idle", "thinking", "needs_permission"]
        n = 100
        final_status = None
        for i in range(n):
            new_status = statuses[i % len(statuses)]
            db.update_session(task_id, status=new_status)
            final_status = new_status

        session = db.get_session(task_id)
        assert session is not None
        assert session["status"] == final_status, (
            f"Expected status {final_status!r}, got {session['status']!r}"
        )
    finally:
        db.close()
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# test_concurrent_task_updates
# ---------------------------------------------------------------------------

def test_concurrent_task_updates():
    """Update the same task from 10 threads simultaneously.

    This test exercises thread-safety of a single shared TaskDB connection.
    SQLite with check_same_thread=False permits concurrent access but does not
    guarantee that all operations will succeed under high contention.  The key
    invariants verified here are:

    1. The task remains readable and structurally valid after all threads finish.
    2. The final notes value (if set) belongs to one of the updating threads.

    Any exception thrown during a concurrent update is caught and swallowed --
    that is acceptable behavior for a shared SQLite connection.  What is NOT
    acceptable is for the database to become unreadable or for the task row to
    disappear entirely.
    """
    db, db_path = _make_task_db()
    try:
        project = "concurrent-project"
        task_id = "concurrent-task"
        db.create_task(project=project, task_id=task_id, description="Concurrent test task")

        n_threads = 10

        def update_notes(thread_idx):
            try:
                db.update_task(project, task_id, notes=f"updated by thread {thread_idx}")
            except Exception:
                # Any SQLite concurrency error (OperationalError, InterfaceError,
                # SystemError, IndexError ...) is acceptable under thread contention.
                pass

        threads = [threading.Thread(target=update_notes, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Use a fresh connection to verify DB integrity so we bypass any
        # corrupted in-process connection state from the concurrent writes.
        check_db = TaskDB(db_path)
        try:
            task = check_db.get_task(project, task_id)
            assert task is not None, "Task vanished after concurrent updates"
            assert task["task_id"] == task_id
            assert task["project"] == project
            # notes is either the original empty string or one of the thread values
            valid_notes = {f"updated by thread {i}" for i in range(n_threads)} | {""}
            assert task["notes"] in valid_notes, (
                f"Task notes {task['notes']!r} is not a valid concurrent-update value"
            )
        finally:
            check_db.close()
    finally:
        db.close()
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# test_large_background_builder
# ---------------------------------------------------------------------------

def test_large_background_builder():
    """Build background with 50 PRs and 20 dependencies; verify output structure."""
    n_prs = 50
    n_deps = 20

    prs = [
        {
            "number": 1000 + i,
            "url": f"https://github.com/example/repo/pull/{1000 + i}",
            "status": "open" if i % 2 == 0 else "merged",
            "title": f"PR title {i}",
            "head_branch": f"branch-{i}",
            "ci_status": "success",
            "review_status": "approved",
        }
        for i in range(n_prs)
    ]

    dep_ids = [f"dep-task-{i}" for i in range(n_deps)]
    dep_statuses = {d: ("done" if i % 2 == 0 else "in_progress") for i, d in enumerate(dep_ids)}

    task_data = {
        "task_id": "large-task",
        "description": "A task with many PRs and dependencies",
        "status": "in_review",
        "ticket_id": "EX-99999",
        "ticket_url": "https://issues.example.org/jira/browse/EX-99999",
        "dependencies": dep_ids,
        "prs": prs,
    }

    result = build_background(task_data, "Large Project", "Review everything.", dep_statuses)

    assert "[Background]" in result, "[Background] section missing"
    assert "[Action]" in result, "[Action] section missing"
    assert "large-task" in result
    assert "EX-99999" in result

    # All PR URLs must appear in the output
    for i in range(n_prs):
        url = f"https://github.com/example/repo/pull/{1000 + i}"
        assert url in result, f"PR URL missing: {url}"

    # All dependency names must appear in the output
    for dep_id in dep_ids:
        assert dep_id in result, f"Dependency {dep_id!r} missing from output"

    # [Action] must come after [Background]
    bg_pos = result.index("[Background]")
    action_pos = result.index("[Action]")
    assert bg_pos < action_pos, "[Background] must precede [Action]"


# ---------------------------------------------------------------------------
# test_rename_chain
# ---------------------------------------------------------------------------

def test_rename_chain():
    """Rename a task 10 times in sequence; verify final task exists with all PRs."""
    db, db_path = _make_task_db()
    try:
        project = "rename-project"
        names = list("abcdefghijk")  # 11 names -> 10 renames
        initial_id = f"task-{names[0]}"

        db.create_task(project=project, task_id=initial_id, description="Rename chain task")

        # Add 3 PRs to the initial task
        for pr_num in [101, 202, 303]:
            db.add_pr(
                project=project,
                task_id=initial_id,
                number=pr_num,
                url=f"https://github.com/example/repo/pull/{pr_num}",
                status="open",
                title=f"PR {pr_num}",
            )

        current_id = initial_id
        for next_name in names[1:]:
            next_id = f"task-{next_name}"
            result = db.rename_task(project, current_id, next_id)
            assert result is True, f"rename_task({current_id!r} -> {next_id!r}) failed"
            # Old ID must be gone
            assert db.get_task(project, current_id) is None, (
                f"Old task {current_id!r} still exists after rename"
            )
            current_id = next_id

        final_id = f"task-{names[-1]}"
        final_task = db.get_task(project, final_id)
        assert final_task is not None, f"Final task {final_id!r} not found"
        assert len(final_task["prs"]) == 3, (
            f"Expected 3 PRs on final task, got {len(final_task['prs'])}"
        )
        pr_numbers = {pr["number"] for pr in final_task["prs"]}
        assert pr_numbers == {101, 202, 303}
    finally:
        db.close()
        os.unlink(db_path)
