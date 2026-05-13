"""Tests for EvaDB -- config, session, and action methods plus events/usage tables."""

import json
import os
import tempfile

import pytest

from eva_db import EvaDB


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    cdb = EvaDB(path)
    yield cdb
    cdb.close()
    os.unlink(path)


# ============================================================
# Schema and seed tests
# ============================================================


def test_schema_creates_tables(db):
    tables = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = [r[0] for r in tables]
    assert "action_definitions" in names
    assert "sessions" in names
    assert "events" in names
    assert "usage_history" in names
    assert "projects" in names
    assert "tasks" in names
    assert "prs" in names
    assert "task_dependencies" in names


def test_seed_data_populates_actions(db):
    actions = db.list_actions()
    ids = [a["id"] for a in actions]
    assert "open" in ids
    assert "do-task" in ids
    assert "fix-ci" in ids
    assert "review" in ids
    assert len(actions) >= 9


def test_seed_data_idempotent(db):
    count1 = len(db.list_actions())
    db.seed_defaults()
    count2 = len(db.list_actions())
    assert count1 == count2


# Every review-style action prompt MUST open with the no-post-to-GH
# warning. This is what stops agent from reflexively running
# `gh pr review --approve` / `gh pr comment` when given a "Review PR"
# action button. Tests guard each id explicitly so a future
# prompt-template edit can't silently drop the rule.
_REVIEW_STYLE_ACTION_IDS = (
    "review",            # context=pr, "Review PR" on your own PR
    "address-comments",  # context=pr, draft replies/fixes
    "draft-reply",       # context=pr, draft replies only
    "review-pr",         # context=review, full review of someone else's PR
    "review-reply",      # context=review, draft replies on a review
    "review-sync",       # context=review, read-only sync
)


def test_review_actions_open_with_no_post_to_github_default(db):
    """Lead the prompt with the no-auto-submit rule so the model sees
    it before the work brief. Bury it at the end (as we used to) and
    Sonnet/Opus will sometimes still `gh pr review --approve` because
    recency-weighted attention foregrounds the work brief.

    Two equivalent canonical phrasings have shipped in the seed; both
    enforce "do not auto-submit anything to GitHub":
      - Old: `IMPORTANT: DO NOT post anything to GitHub ...`
      - New (PENDING-comments approach): `IMPORTANT: Add findings as
        PENDING (draft) inline review comments on GitHub via gh api ...
        do NOT include event ...`
    Either is accepted as long as the lead line says IMPORTANT, says
    GitHub, and contains a no-submit signal (`DO NOT post`,
    `Do NOT run`, or `PENDING`/`do NOT include event`).
    """
    no_submit_signals = (
        "DO NOT post",         # old phrasing
        "Do NOT run",          # new phrasing forbids submit commands
        "do NOT include `event`",  # new phrasing keeps state PENDING
        "PENDING",             # canonical state target in new copy
    )
    for action_id in _REVIEW_STYLE_ACTION_IDS:
        action = db.get_action(action_id)
        assert action is not None, f"missing seed for {action_id!r}"
        prompt = action["prompt_template"]
        # First non-empty line must contain the rule.
        first = next((l for l in prompt.splitlines() if l.strip()), "")
        assert "IMPORTANT" in first, (
            f"{action_id!r} doesn't lead with IMPORTANT: {first!r}"
        )
        assert any(sig in first for sig in no_submit_signals), (
            f"{action_id!r} lead line lacks any no-submit signal "
            f"({no_submit_signals}): {first!r}"
        )
        assert "GitHub" in first, (
            f"{action_id!r} doesn't say GitHub on the lead line: "
            f"{first!r}"
        )


def test_seed_force_refreshes_review_style_prompts_on_existing_rows(db):
    """Existing users who already have an action_definitions row from
    the previous Eva version must pick up the tightened prompt on the
    next boot. `INSERT OR IGNORE` alone wouldn't update them; the
    seed has an explicit UPDATE pass for review-style ids."""
    # Plant a stale prompt as if it came from an older Eva version.
    db._conn.execute(
        "UPDATE action_definitions SET prompt_template=? WHERE id=?",
        ("OUTDATED stale prompt without the rule", "review-pr"),
    )
    db._conn.commit()
    # Re-run the seed -- the force-refresh pass should overwrite.
    db.seed_defaults()
    refreshed = db.get_action("review-pr")
    assert refreshed is not None
    assert "IMPORTANT" in refreshed["prompt_template"]
    # Either canonical no-submit signal is acceptable (see
    # `test_review_actions_open_with_no_post_to_github_default`
    # for the rationale).
    body = refreshed["prompt_template"]
    assert any(sig in body for sig in (
        "DO NOT post", "Do NOT run", "do NOT include `event`", "PENDING",
    )), body[:200]


def test_seed_does_not_force_refresh_unrelated_pr_actions(db):
    """fix-ci and update-pr aren't review-style and have no global
    no-post rule. Their templates should survive a user customisation
    across re-seeds."""
    custom = "MY CUSTOM fix-ci prompt"
    db._conn.execute(
        "UPDATE action_definitions SET prompt_template=? WHERE id=?",
        (custom, "fix-ci"),
    )
    db._conn.commit()
    db.seed_defaults()
    assert db.get_action("fix-ci")["prompt_template"] == custom


# ============================================================
# Action definitions
# ============================================================


def test_get_action(db):
    action = db.get_action("fix-ci")
    assert action is not None
    assert action["label"] == "Fix CI"
    assert action["context"] == "pr"
    assert action["condition"] == "ci_failed"


def test_get_action_not_found(db):
    assert db.get_action("nonexistent") is None


def test_list_actions_by_context(db):
    task_actions = db.list_actions(context="task")
    for a in task_actions:
        assert a["context"] in ("task", "all")
    pr_actions = db.list_actions(context="pr")
    for a in pr_actions:
        assert a["context"] in ("pr", "all")


def test_list_actions_empty_context(db):
    """list_actions with an empty string returns all actions."""
    actions = db.list_actions("")
    assert len(actions) >= 9


def test_list_actions_sorted_by_sort_order(db):
    """Actions are returned sorted by sort_order."""
    actions = db.list_actions()
    orders = [a["sort_order"] for a in actions]
    assert orders == sorted(orders)


def test_get_action_fields(db):
    """All expected fields present on a seeded action."""
    action = db.get_action("do-task")
    assert action is not None
    for field in ("id", "label", "prompt_template", "context", "condition", "sort_order"):
        assert field in action
    assert action["label"] == "Do This Task"
    assert "Execute this task" in action["prompt_template"]


# ============================================================
# Session CRUD
# ============================================================


def test_create_session(db):
    db.create_session("my-task", "my-project", "my-task")
    s = db.get_session("my-task")
    assert s is not None
    assert s["project"] == "my-project"
    assert s["tmux_name"] == "my-task"
    assert s["status"] == "not_started"


def test_get_session_not_found(db):
    assert db.get_session("nonexistent") is None


def test_update_session_status(db):
    db.create_session("t1", "p1", "t1")
    db.update_session("t1", status="idle")
    s = db.get_session("t1")
    assert s["status"] == "idle"


def test_update_session_tmux_name(db):
    """update_session can change the tmux_name."""
    db.create_session("t1", "p1", "original-name")
    db.update_session("t1", tmux_name="new-name")
    s = db.get_session("t1")
    assert s["tmux_name"] == "new-name"


def test_update_session_project(db):
    """update_session can change the project."""
    db.create_session("t1", "p1", "t1")
    db.update_session("t1", project="p2")
    s = db.get_session("t1")
    assert s["project"] == "p2"


def test_update_session_multiple_fields(db):
    """update_session can change multiple fields at once."""
    db.create_session("t1", "p1", "t1")
    db.update_session("t1", status="active", project="p2")
    s = db.get_session("t1")
    assert s["status"] == "active"
    assert s["project"] == "p2"


def test_update_session_stamps_updated_at(db):
    """update_session auto-stamps updated_at."""
    db.create_session("t1", "p1", "t1")
    s1 = db.get_session("t1")
    original_updated = s1["updated_at"]
    db.update_session("t1", status="running")
    s2 = db.get_session("t1")
    assert s2["updated_at"] >= original_updated


def test_delete_session(db):
    db.create_session("t1", "p1", "t1")
    assert db.delete_session("t1") is True
    assert db.get_session("t1") is None


def test_delete_session_not_found(db):
    assert db.delete_session("nonexistent") is False


def test_list_sessions(db):
    db.create_session("t1", "p1", "t1")
    db.create_session("t2", "p1", "t2")
    db.create_session("t3", "p2", "t3")
    all_sessions = db.list_sessions()
    assert len(all_sessions) == 3
    p1_sessions = db.list_sessions(project="p1")
    assert len(p1_sessions) == 2


def test_list_sessions_empty(db):
    """list_sessions returns empty list when no sessions exist."""
    assert db.list_sessions() == []
    assert db.list_sessions(project="nonexistent") == []


def test_list_sessions_ordered_by_updated_at(db):
    """Sessions returned in updated_at DESC order."""
    db.create_session("t1", "p1", "t1")
    db.create_session("t2", "p1", "t2")
    # Update t1 so it has a later updated_at
    db.update_session("t1", status="running")
    sessions = db.list_sessions()
    assert sessions[0]["task_id"] == "t1"


def test_create_session_replaces_existing(db):
    """create_session with the same task_id replaces the session and resets status."""
    db.create_session("t1", "p1", "t1")
    db.update_session("t1", status="idle")

    db.create_session("t1", "p1", "t1")
    s = db.get_session("t1")
    assert s["status"] == "not_started"


def test_update_session_ignores_unknown_fields(db):
    """update_session with an unknown field does not raise and makes no change."""
    db.create_session("t1", "p1", "t1")
    result = db.update_session("t1", nonexistent_field="value")
    s = db.get_session("t1")
    assert s["status"] == "not_started"
    assert "nonexistent_field" not in s


def test_update_session_no_op(db):
    """update_session with no valid fields returns current session."""
    db.create_session("t1", "p1", "t1")
    result = db.update_session("t1")
    assert result is not None
    assert result["task_id"] == "t1"


# ============================================================
# Events table methods
# ============================================================


def test_events_table_exists(db):
    """Events table is created by EvaDB schema."""
    tables = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = [r[0] for r in tables]
    assert "events" in names


def test_insert_and_query_events(db):
    """Can insert and query events via raw SQL."""
    db._conn.execute(
        "INSERT INTO events (id, source, source_id, title, message, type, severity, url, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("e1", "test", "src1", "Test Event", "msg", "info", "info", "", "2026-01-01T00:00:00"),
    )
    db._conn.commit()
    rows = db._conn.execute("SELECT * FROM events WHERE id='e1'").fetchall()
    assert len(rows) == 1
    assert dict(rows[0])["title"] == "Test Event"


def test_events_index_exists(db):
    """Indexes on events table should exist."""
    indexes = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='events'"
    ).fetchall()
    index_names = [r[0] for r in indexes]
    assert "idx_events_ts" in index_names
    assert "idx_events_source" in index_names


def test_events_session_column(db):
    """Events table has the session column."""
    db._conn.execute(
        "INSERT INTO events (id, source, title, ts, session) "
        "VALUES (?, ?, ?, ?, ?)",
        ("e2", "test", "With Session", "2026-01-01T00:00:00", "my-session"),
    )
    db._conn.commit()
    row = db._conn.execute("SELECT session FROM events WHERE id='e2'").fetchone()
    assert dict(row)["session"] == "my-session"


def test_events_read_flag(db):
    """Events default to unread (read=0)."""
    db._conn.execute(
        "INSERT INTO events (id, source, title, ts) VALUES (?, ?, ?, ?)",
        ("e3", "test", "Unread Event", "2026-01-01T00:00:00"),
    )
    db._conn.commit()
    row = db._conn.execute("SELECT read FROM events WHERE id='e3'").fetchone()
    assert dict(row)["read"] == 0

    # Mark as read
    db._conn.execute("UPDATE events SET read=1 WHERE id='e3'")
    db._conn.commit()
    row = db._conn.execute("SELECT read FROM events WHERE id='e3'").fetchone()
    assert dict(row)["read"] == 1


# ============================================================
# Usage history table
# ============================================================


def test_usage_history_table_exists(db):
    """usage_history table is created by EvaDB schema."""
    tables = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = [r[0] for r in tables]
    assert "usage_history" in names


def test_insert_and_query_usage(db):
    """Can insert and query usage history."""
    db._conn.execute(
        "INSERT INTO usage_history (ts, daily, weekly, monthly) VALUES (?, ?, ?, ?)",
        ("2026-01-01T00:00:00", 5.25, 30.0, 120.50),
    )
    db._conn.commit()
    rows = db._conn.execute("SELECT * FROM usage_history WHERE ts='2026-01-01T00:00:00'").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["daily"] == 5.25
    assert row["weekly"] == 30.0
    assert row["monthly"] == 120.50


def test_usage_history_autoincrement(db):
    """usage_history id auto-increments."""
    db._conn.execute(
        "INSERT INTO usage_history (ts, daily, weekly, monthly) VALUES (?, ?, ?, ?)",
        ("2026-01-01T00:00:00", 1.0, 2.0, 3.0),
    )
    db._conn.execute(
        "INSERT INTO usage_history (ts, daily, weekly, monthly) VALUES (?, ?, ?, ?)",
        ("2026-01-02T00:00:00", 4.0, 5.0, 6.0),
    )
    db._conn.commit()
    rows = db._conn.execute("SELECT id FROM usage_history ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0][0] < rows[1][0]


# ============================================================
# Project CRUD (overlaps with EvaDB -- test config-specific paths)
# ============================================================


def test_project_crud_roundtrip(db):
    """Full project CRUD: create, get, update, delete."""
    db.create_project("test-p", name="Test", description="desc")
    p = db.get_project("test-p")
    assert p is not None
    assert p["id"] == "test-p"
    assert p["name"] == "Test"
    assert p["has_tickets"] is False
    assert p["umbrella_tickets"] == []

    # Update
    db.update_project("test-p", name="Updated Test", has_tickets=True)
    p = db.get_project("test-p")
    assert p["name"] == "Updated Test"
    assert p["has_tickets"] is True

    # Delete
    assert db.delete_project("test-p") is True
    assert db.get_project("test-p") is None


def test_project_exists(db):
    """project_exists returns True/False correctly."""
    assert db.project_exists("nope") is False
    db.create_project("yes")
    assert db.project_exists("yes") is True


def test_list_projects_sorted(db):
    """list_projects returns projects sorted by ID."""
    db.create_project("z-proj")
    db.create_project("a-proj")
    projects = db.list_projects()
    ids = [p["id"] for p in projects]
    assert ids.index("a-proj") < ids.index("z-proj")


def test_update_project_umbrella_tickets(db):
    """update_project can set umbrella_tickets as a list."""
    db.create_project("tp")
    db.update_project("tp", umbrella_tickets=["EX-100", "EX-200"])
    p = db.get_project("tp")
    assert p["umbrella_tickets"] == ["EX-100", "EX-200"]


def test_update_project_no_changes(db):
    """update_project with no matching fields returns project unchanged."""
    db.create_project("tp", name="Original")
    result = db.update_project("tp", unknown_field="val")
    assert result["name"] == "Original"


def test_delete_project_not_found(db):
    """delete_project returns False for nonexistent project."""
    assert db.delete_project("ghost") is False


# ============================================================
# Task-level methods via EvaDB
# ============================================================


def test_task_follow_ups_validation(db):
    """follow_ups that are task IDs should be rejected."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    db.create_task("p1", "t2")
    with pytest.raises(ValueError, match="task ID"):
        db.update_task("p1", "t1", follow_ups=["t2"])


def test_task_follow_ups_valid(db):
    """follow_ups with natural language descriptions are accepted."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    db.update_task("p1", "t1", follow_ups=["rebase on latest master"])
    t = db.get_task("p1", "t1")
    assert t["follow_ups"] == ["rebase on latest master"]


def test_task_follow_ups_non_string_rejected(db):
    """follow_ups with non-string items are rejected."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    with pytest.raises(ValueError, match="must be a string"):
        db.update_task("p1", "t1", follow_ups=[123])


def test_task_follow_ups_not_list_rejected(db):
    """follow_ups that is not a list is rejected."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    with pytest.raises(ValueError, match="must be a list"):
        db.update_task("p1", "t1", follow_ups="not a list")


# ============================================================
# Dirty PR methods
# ============================================================


def test_dirty_pr_workflow(db):
    """mark_pr_dirty / list_dirty_prs / clear_pr_dirty lifecycle."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    db.add_pr("p1", "t1", number=42, url="https://github.com/x/y/pull/42", title="PR 42")
    db.add_pr("p1", "t1", number=43, url="https://github.com/x/y/pull/43", title="PR 43")

    db.mark_pr_dirty(42)
    dirty = db.list_dirty_prs()
    assert len(dirty) == 1
    assert dirty[0]["number"] == 42

    db.clear_pr_dirty(42)
    assert len(db.list_dirty_prs()) == 0


def test_clear_all_dirty(db):
    """clear_all_dirty clears all dirty flags."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    db.add_pr("p1", "t1", number=10, url="u1", title="PR10")
    db.add_pr("p1", "t1", number=11, url="u2", title="PR11")
    db.mark_pr_dirty(10)
    db.mark_pr_dirty(11)
    assert len(db.list_dirty_prs()) == 2
    db.clear_all_dirty()
    assert len(db.list_dirty_prs()) == 0


# ============================================================
# find_task_by_ticket / find_tasks_by_ticket
# ============================================================


def test_find_task_by_ticket(db):
    """find_task_by_ticket finds by exact ticket_id."""
    db.create_project("p1")
    db.create_task("p1", "t1", ticket_id="EX-100")
    result = db.find_task_by_ticket("EX-100")
    assert result is not None
    assert result == ("p1", "t1")


def test_find_task_by_ticket_in_description(db):
    """find_task_by_ticket falls back to description search."""
    db.create_project("p1")
    db.create_task("p1", "t1", description="Implements EX-200 feature")
    result = db.find_task_by_ticket("EX-200")
    assert result is not None
    assert result == ("p1", "t1")


def test_find_task_by_ticket_not_found(db):
    """find_task_by_ticket returns None when not found."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    assert db.find_task_by_ticket("EX-999") is None


def test_find_tasks_by_ticket(db):
    """find_tasks_by_ticket returns all matches."""
    db.create_project("p1")
    db.create_task("p1", "t1", ticket_id="EX-100")
    db.create_task("p1", "t2", ticket_id="EX-100")
    results = db.find_tasks_by_ticket("EX-100")
    assert len(results) == 2


# ============================================================
# Rename task
# ============================================================


def test_rename_task_updates_deps(db):
    """rename_task updates forward and reverse dependencies."""
    db.create_project("p1")
    db.create_task("p1", "a")
    db.create_task("p1", "b")
    db.create_task("p1", "c")
    db.add_dependency("p1", "b", "a")  # b depends on a
    db.add_dependency("p1", "c", "b")  # c depends on b

    db.rename_task("p1", "b", "b-new")

    # b-new should still depend on a
    b = db.get_task("p1", "b-new")
    assert "a" in b["dependencies"]

    # c should now depend on b-new, not b
    c = db.get_task("p1", "c")
    assert "b-new" in c["dependencies"]
    assert "b" not in c["dependencies"]


def test_rename_task_not_found(db):
    """rename_task returns False if source doesn't exist."""
    db.create_project("p1")
    assert db.rename_task("p1", "ghost", "new") is False


def test_rename_task_target_exists(db):
    """rename_task returns False if target already exists."""
    db.create_project("p1")
    db.create_task("p1", "a")
    db.create_task("p1", "b")
    assert db.rename_task("p1", "a", "b") is False


# ============================================================
# is_task_blocked
# ============================================================


def test_is_task_blocked_true(db):
    """Task is blocked when dependency is not done."""
    db.create_project("p1")
    db.create_task("p1", "a", status="in_progress")
    db.create_task("p1", "b", status="not_started")
    db.add_dependency("p1", "b", "a")
    assert db.is_task_blocked("p1", "b") is True


def test_is_task_blocked_false(db):
    """Task is not blocked when all dependencies are done."""
    db.create_project("p1")
    db.create_task("p1", "a", status="done")
    db.create_task("p1", "b", status="not_started")
    db.add_dependency("p1", "b", "a")
    assert db.is_task_blocked("p1", "b") is False


def test_is_task_blocked_no_deps(db):
    """Task with no dependencies is not blocked."""
    db.create_project("p1")
    db.create_task("p1", "a")
    assert db.is_task_blocked("p1", "a") is False


def test_is_task_blocked_missing_dep(db):
    """Task is blocked if dependency doesn't exist in tasks table."""
    db.create_project("p1")
    db.create_task("p1", "a")
    # Manually insert a dep pointing to nonexistent task
    db._conn.execute(
        "INSERT INTO task_dependencies (project, task_id, depends_on) VALUES (?, ?, ?)",
        ("p1", "a", "ghost"),
    )
    db._conn.commit()
    assert db.is_task_blocked("p1", "a") is True


# ============================================================
# PR methods on EvaDB
# ============================================================


def test_list_all_prs_filter(db):
    """list_all_prs can filter by status and search."""
    db.create_project("p1")
    db.create_task("p1", "t1", description="Search target")
    db.add_pr("p1", "t1", number=1, url="u1", status="open", title="Alpha PR")
    db.add_pr("p1", "t1", number=2, url="u2", status="merged", title="Beta PR")

    open_prs = db.list_all_prs(status="open")
    assert len(open_prs) == 1
    assert open_prs[0]["number"] == 1

    search_prs = db.list_all_prs(search="Beta")
    assert len(search_prs) == 1
    assert search_prs[0]["number"] == 2


def test_find_pr_by_number(db):
    """find_pr_by_number finds across projects."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    db.add_pr("p1", "t1", number=99, url="u", title="PR 99")

    found = db.find_pr_by_number(99)
    assert found is not None
    assert found["number"] == 99

    assert db.find_pr_by_number(9999) is None


def test_update_pr_by_number(db):
    """update_pr_by_number updates fields across projects."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    db.add_pr("p1", "t1", number=50, url="u", title="Old Title")

    db.update_pr_by_number(50, title="New Title", ci_status="passing")
    pr = db.find_pr_by_number(50)
    assert pr["title"] == "New Title"
    assert pr["ci_status"] == "passing"


def test_update_pr_by_number_no_fields(db):
    """update_pr_by_number with no valid fields is a no-op."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    db.add_pr("p1", "t1", number=50, url="u", title="Title")
    # Should not raise
    db.update_pr_by_number(50, invalid_field="val")
    pr = db.find_pr_by_number(50)
    assert pr["title"] == "Title"


def test_update_pr_and_update_pr_by_number_share_whitelist(db):
    """Both entry points must accept the same set of writable columns
    (since they go through `_update_pr_rows`). A regression where one
    accepts a column the other doesn't would silently drop writes.
    """
    db.create_project("p1")
    db.create_task("p1", "t1")
    db.add_pr("p1", "t1", number=51, url="u", title="t")

    # `dirty` is NOT in the whitelist -> both must drop it.
    db.update_pr("p1", "t1", 51, dirty=1, title="A")
    db.update_pr_by_number(51, dirty=1, title="B")
    pr = db.find_pr_by_number(51)
    assert pr["title"] == "B"
    assert pr["dirty"] == 0  # still default; whitelist drops the write

    # An allowed column lands the same way through both paths.
    db.update_pr("p1", "t1", 51, ci_status="pending")
    pr = db.find_pr_by_number(51)
    assert pr["ci_status"] == "pending"
    db.update_pr_by_number(51, ci_status="passing")
    pr = db.find_pr_by_number(51)
    assert pr["ci_status"] == "passing"


def test_update_pr_by_number_status_changed_at_one_way_stamp(db):
    """First status flip stamps `status_changed_at`. Subsequent flips
    don't overwrite it (one-way backfill so worklog filters by the
    real first-transition time)."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    db.add_pr("p1", "t1", number=52, url="u", title="t", status="open")

    db.update_pr_by_number(52, status="merged")
    pr1 = db.find_pr_by_number(52)
    stamped = pr1["status_changed_at"]
    assert stamped, "first status flip must stamp status_changed_at"

    # Second flip with status changing again -- the stamp must NOT
    # rewrite (one-way contract).
    db.update_pr_by_number(52, status="closed")
    pr2 = db.find_pr_by_number(52)
    assert pr2["status_changed_at"] == stamped
    assert pr2["status"] == "closed"


# ============================================================
# dependency_graph
# ============================================================


def test_dependency_graph(db):
    """dependency_graph returns nodes, edges, and groups."""
    db.create_project("p1")
    db.create_task("p1", "a", group_name="g1", status="done")
    db.create_task("p1", "b", group_name="g1", status="not_started")
    db.create_task("p1", "c", group_name="g2", status="not_started")
    db.add_dependency("p1", "b", "a")
    db.add_dependency("p1", "c", "b")

    graph = db.dependency_graph("p1")
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2
    assert "g1" in graph["groups"]
    assert "g2" in graph["groups"]

    # c should be blocked (dep b is not done)
    c_node = next(n for n in graph["nodes"] if n["task_id"] == "c")
    assert c_node["status"] == "blocked"


# ============================================================
# project_stats
# ============================================================


def test_project_stats(db):
    """project_stats returns counts and progress."""
    db.create_project("p1")
    db.create_task("p1", "t1", status="done")
    db.create_task("p1", "t2", status="in_progress")
    db.create_task("p1", "t3", status="not_started")

    stats = db.project_stats("p1")
    assert stats["total"] == 3
    assert stats["counts"]["done"] == 1
    assert stats["counts"]["in_progress"] == 1
    assert stats["counts"]["not_started"] == 1
    assert stats["progress"] == pytest.approx(33.3, abs=0.1)


def test_project_stats_empty(db):
    """project_stats on empty project returns 0 progress."""
    db.create_project("p1")
    stats = db.project_stats("p1")
    assert stats["total"] == 0
    assert stats["progress"] == 0.0


# ============================================================
# EvaDB.close
# ============================================================


def test_close_db():
    """close() closes the connection without error."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db2 = EvaDB(path)
    db2.close()
    os.unlink(path)


# ============================================================
# _validate_follow_ups -- reject task IDs, enforce list-of-strings
# ============================================================


def test_follow_ups_accepts_natural_language(db):
    """Natural-language descriptions are valid follow-ups."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    db.update_task("p1", "t1", follow_ups=["Wait for review", "Update docs later"])
    t = db.get_task("p1", "t1")
    assert "Wait for review" in t["follow_ups"]


def test_follow_ups_rejects_task_id_reference(db):
    """Using a task ID as a follow-up must raise -- use dependencies instead."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    db.create_task("p1", "t2")
    with pytest.raises(ValueError, match="is a task ID"):
        db.update_task("p1", "t1", follow_ups=["t2"])


def test_follow_ups_rejects_non_list(db):
    """follow_ups must be a list, not a string or dict."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    with pytest.raises(ValueError, match="must be a list"):
        db.update_task("p1", "t1", follow_ups="just a string")
    with pytest.raises(ValueError, match="must be a list"):
        db.update_task("p1", "t1", follow_ups={"not": "list"})


def test_follow_ups_rejects_non_string_items(db):
    """Every element of follow_ups must be a string."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    with pytest.raises(ValueError, match="must be a string"):
        db.update_task("p1", "t1", follow_ups=["valid", 42])


def test_follow_ups_empty_list_is_allowed(db):
    """Clearing follow_ups to an empty list should work (for reset)."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    db.update_task("p1", "t1", follow_ups=["tmp"])
    db.update_task("p1", "t1", follow_ups=[])
    t = db.get_task("p1", "t1")
    assert t["follow_ups"] == []


# ============================================================
# find_tasks_by_ticket edge cases (plural: list of all matches)
# ============================================================


def test_find_tasks_by_ticket_includes_all_projects(db):
    """find_tasks_by_ticket crosses projects (umbrella ticket covers many)."""
    db.create_project("p1")
    db.create_project("p2")
    db.create_task("p1", "t-a", ticket_id="UMB-1")
    db.create_task("p2", "t-b", ticket_id="UMB-1")
    results = db.find_tasks_by_ticket("UMB-1")
    project_ids = {r[0] for r in results}
    assert project_ids == {"p1", "p2"}


def test_find_tasks_by_ticket_empty_list_when_no_match(db):
    """No matches returns an empty list, never None."""
    db.create_project("p1")
    assert db.find_tasks_by_ticket("UNKNOWN") == []


def test_find_tasks_by_ticket_includes_status(db):
    """Result tuples carry (project, task_id, status)."""
    db.create_project("p1")
    db.create_task("p1", "t1", ticket_id="XYZ-1", status="in_progress")
    results = db.find_tasks_by_ticket("XYZ-1")
    assert results == [("p1", "t1", "in_progress")]


# ============================================================
# review_prs workflow columns (session_name, my_workflow_state, ...)
# Track my review-lifecycle state separately from the GitHub PR state
# the queue was populated with.
# ============================================================


def _seed_review_pr(db, url="https://github.com/example/repo/pull/42"):
    db.upsert_review_pr(
        url=url, repo="example/repo", number=42,
        title="Test review PR", author="someone-else",
        status="open", last_updated="2026-04-24T00:00:00Z",
        source="github",
    )
    return url


def test_upsert_review_pr_accepts_workflow_columns(db):
    """Workflow columns (session_name, my_workflow_state, ...) flow
    through upsert_review_pr without errors and show up on read."""
    url = _seed_review_pr(db)
    db.upsert_review_pr(
        url, "example/repo", 42,
        session_name="review-example-repo-42",
        agent_session_id="uuid-abc",
        my_workflow_state="active",
        started_at="2026-04-24T10:00:00Z",
    )
    row = db.get_review_pr(url)
    assert row["session_name"] == "review-example-repo-42"
    assert row["agent_session_id"] == "uuid-abc"
    assert row["my_workflow_state"] == "active"
    assert row["started_at"] == "2026-04-24T10:00:00Z"


def test_upsert_review_pr_defaults_workflow_state_to_queued(db):
    """Every new review_prs row lands in 'queued' so the queue can
    trivially count "needs action" = everything not 'done'/'dismissed'."""
    url = _seed_review_pr(db)
    row = db.get_review_pr(url)
    assert row["my_workflow_state"] == "queued"


def test_review_workflow_states_enum_is_exported(db):
    """REVIEW_WORKFLOW_STATES is the Python-side enforcement for the
    CHECK constraint on my_workflow_state. Keep the set pinned down so
    a caller bug (e.g. 'in_progress' instead of 'active') fails fast."""
    assert db.REVIEW_WORKFLOW_STATES == frozenset({
        "queued", "active", "done", "dismissed",
    })


# ============================================================
# review_history (append-only timeline per review PR)
# Mirrors task_history. Keeps review actions visible long after the
# tmux session dies (tmux is the workspace, history is the log).
# ============================================================


def test_append_review_history_inserts_row(db):
    url = _seed_review_pr(db)
    out = db.append_review_history(url, "started review")
    assert out["text"] == "started review"
    assert out["source"] == "manual"
    rows = db.list_review_history(url)
    assert len(rows) == 1
    assert rows[0]["text"] == "started review"


def test_append_review_history_rejects_empty_text(db):
    url = _seed_review_pr(db)
    with pytest.raises(ValueError, match="empty"):
        db.append_review_history(url, "   ")


def test_append_review_history_rejects_long_text(db):
    """100-char cap mirrors task_history. Keeps entries terse so the UI
    timeline stays readable."""
    url = _seed_review_pr(db)
    with pytest.raises(ValueError, match="chars"):
        db.append_review_history(url, "x" * 101)


def test_history_text_validator_message_is_consistent_across_paths(db):
    """task_history and review_history used to maintain their own
    validator copies which drifted -- task said
    'one line, what changed', review said just 'one line'. The shared
    `_validate_history_text` helper now generates one wording for
    both. Lock that contract so a future edit doesn't fork them."""
    db.create_project("p1")
    db.create_task("p1", "t1")
    url = _seed_review_pr(db)

    # Both paths raise with the SAME message body for an oversize text.
    with pytest.raises(ValueError) as ei_task:
        db.append_task_history("p1", "t1", "x" * 200)
    with pytest.raises(ValueError) as ei_review:
        db.append_review_history(url, "x" * 200)
    # Strip the leading number-of-chars prefix; the explanatory tail
    # must match.
    task_tail = str(ei_task.value).split(";", 1)[1]
    review_tail = str(ei_review.value).split(";", 1)[1]
    assert task_tail == review_tail
    assert "one line" in task_tail

    # Empty text raises the same error from both paths too.
    with pytest.raises(ValueError, match="is empty"):
        db.append_task_history("p1", "t1", "   ")
    with pytest.raises(ValueError, match="is empty"):
        db.append_review_history(url, "   ")


def test_append_review_history_rejects_missing_review(db):
    """History entry without a corresponding review_prs row is a bug
    (likely a stale URL). Raise instead of silently dangling."""
    with pytest.raises(ValueError, match="not found"):
        db.append_review_history(
            "https://github.com/nope/nope/pull/1", "hi")


def test_list_review_history_is_newest_first(db):
    """UI renders timelines top-down, newest at top -- assert the
    sort order is DESC by ts."""
    url = _seed_review_pr(db)
    db.append_review_history(url, "first",  ts="2026-04-24T10:00:00Z")
    db.append_review_history(url, "second", ts="2026-04-24T11:00:00Z")
    db.append_review_history(url, "third",  ts="2026-04-24T12:00:00Z")
    rows = db.list_review_history(url)
    assert [r["text"] for r in rows] == ["third", "second", "first"]


def test_list_review_history_respects_limit(db):
    url = _seed_review_pr(db)
    for i in range(5):
        db.append_review_history(url, f"step {i}",
                                 ts=f"2026-04-24T1{i}:00:00Z")
    assert len(db.list_review_history(url, limit=2)) == 2


def test_append_review_history_persists_source(db):
    """`source` tags entries so the UI can style agent/github writes
    differently from manual ones. Default is 'manual'."""
    url = _seed_review_pr(db)
    db.append_review_history(url, "auto update", source="agent")
    row = db.list_review_history(url)[0]
    assert row["source"] == "agent"


def test_concurrent_settings_writes_no_interface_error(db):
    """Real-world bug: APScheduler's gh-poll + uvicorn worker + cron
    runner all hit `db.set_setting` / `db.list_settings` at the same
    time and the bare `check_same_thread=False` connection surfaced
    `sqlite3.InterfaceError: bad parameter or other API misuse` (4
    occurrences in 84 log lines on the live install). The
    `_LockedConnection` proxy serialises every call.

    Eight threads, each doing 50 mixed read/write hits. Without the
    lock this would surface InterfaceError within a couple of
    iterations on a contended host.
    """
    import threading
    errors: list = []

    def hammer(idx: int) -> None:
        try:
            for i in range(50):
                db.set_setting(f"concurrent.thread{idx}.iter{i}",
                               {"i": i, "thread": idx})
                db.list_settings()
        except Exception as e:
            errors.append((idx, type(e).__name__, str(e)[:100]))

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == [], f"concurrent DB ops surfaced errors: {errors!r}"
    # Sanity: every write landed.
    rows = db.list_settings()
    assert sum(
        1 for k in rows if k.startswith("concurrent.thread")
    ) == 8 * 50


def test_concurrent_list_projects_no_interface_error(db):
    """`list_projects()` was using the same unsafe pattern as
    list_settings() (execute then .fetchall() outside the lock).
    Convert was mechanical -- this test guards against the regression
    creeping back. Same 8x50 contention shape as the settings test
    but for the projects table.
    """
    import threading
    db.create_project(project_id="p1", name="P1")
    errors: list = []

    def hammer(idx: int) -> None:
        try:
            for i in range(50):
                # Mix writes + reads across threads.
                db.create_project(
                    project_id=f"p-t{idx}-i{i}",
                    name=f"thread {idx} iter {i}",
                )
                db.list_projects()
        except Exception as e:
            errors.append((idx, type(e).__name__, str(e)[:100]))

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert errors == [], f"concurrent list_projects surfaced errors: {errors!r}"
    # Sanity: every project landed. `list_projects` renames project_id -> id.
    rows = db.list_projects()
    assert sum(
        1 for r in rows if r["id"].startswith("p-t")
    ) == 8 * 50


def test_execute_fetchall_helper_holds_lock_through_iteration(db):
    """White-box: `_LockedConnection.execute_fetchall` must materialize
    rows BEFORE releasing the lock. Without that, callers that index
    into row tuples (`row[0]`, `row['key']`) race with concurrent
    writes mid-fetch."""
    import threading
    db._conn._conn  # sanity: proxy exposes the wrapped sqlite conn
    db.set_setting("a", 1)
    db.set_setting("b", 2)
    errors: list = []

    def reader() -> None:
        for _ in range(200):
            try:
                rows = db._conn.execute_fetchall(
                    "SELECT key, value FROM settings ORDER BY key")
                # If the lock didn't span fetch, indexing would race.
                for r in rows:
                    _ = r["key"], r["value"]
            except Exception as e:
                errors.append(("reader", type(e).__name__, str(e)[:100]))

    def writer() -> None:
        for i in range(200):
            try:
                db.set_setting(f"hammer.{i}", i)
            except Exception as e:
                errors.append(("writer", type(e).__name__, str(e)[:100]))

    threads = [threading.Thread(target=reader) for _ in range(4)] + \
              [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert errors == [], f"execute_fetchall surfaced errors: {errors!r}"
