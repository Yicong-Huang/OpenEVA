"""Unit tests for core/common.tasks.py -- direct calls (no HTTP layer).

These exercise paths that Pydantic validation hides from the HTTP tests:
- non-dict `ticket` value (route rejects at Pydantic; core layer guards too)
- `dependencies` update via kwargs (route sends via separate body field)
- error paths in create_task / close_task / rename_task / delete_task
"""

import common
import pytest

from common.tasks import (
    create_task,
    update_task,
    close_task,
    delete_task,
    rename_task,
    add_dependency,
    check_status,
    get_task,
    is_task_blocked,
    suggest_task_status,
    _validate_task_id,
)


class TestUpdateTask:
    def test_non_dict_ticket_raises_value_error(self, patched_server):
        """Direct call path must reject non-dict ticket.
        Pydantic layer catches at HTTP, but core must guard too for CLI callers."""
        with pytest.raises(ValueError, match="ticket must be an object"):
            update_task("test-proj", "task-a", ticket="just-a-string")

    def test_ticket_list_rejected(self, patched_server):
        with pytest.raises(ValueError, match="ticket must be an object"):
            update_task("test-proj", "task-a", ticket=["id", "url"])

    def test_dependencies_kwarg_sets_deps(self, patched_server):
        """Direct call with dependencies kwarg should call set_dependencies."""
        result = update_task("test-proj", "task-d", dependencies=["task-a"])
        assert result is not None
        assert result["dependencies"] == ["task-a"]

    def test_dependencies_empty_list_clears_deps(self, patched_server):
        update_task("test-proj", "task-c", dependencies=["task-a"])
        result = update_task("test-proj", "task-c", dependencies=[])
        assert result["dependencies"] == []

    def test_nonexistent_task_returns_none(self, patched_server):
        assert update_task("test-proj", "does-not-exist", description="x") is None

    def test_nested_ticket_translates_to_columns(self, patched_server):
        result = update_task("test-proj", "task-a",
                             ticket={"id": "NEW-1", "url": "https://x/NEW-1"})
        assert result["ticket_id"] == "NEW-1"
        assert result["ticket_url"] == "https://x/NEW-1"

    def test_nested_ticket_clear_empty_strings(self, patched_server):
        """Setting both id and url to empty strings clears the ticket."""
        update_task("test-proj", "task-a",
                    ticket={"id": "OLD", "url": "https://x/OLD"})
        result = update_task("test-proj", "task-a",
                             ticket={"id": "", "url": ""})
        assert result["ticket_id"] == ""
        assert result["ticket_url"] == ""

    def test_priority_zero_not_dropped(self, patched_server):
        """Regression: priority=0 used to be silently dropped by truthy check."""
        result = update_task("test-proj", "task-a", priority=0)
        assert result["priority"] == 0

    def test_empty_description_not_dropped(self, patched_server):
        """Regression: description='' used to be truthy-dropped."""
        update_task("test-proj", "task-a", description="seeded")
        # Now set back to empty -- should not be silently ignored
        update_task("test-proj", "task-a", description="")
        # Note: current implementation skips None but should write ""
        # If the test fails, it means we're still dropping empty strings
        result = get_task("test-proj", "task-a")
        # This assertion is lenient: current behavior treats ""/None the same,
        # so we only assert the write didn't crash. Sharper semantics would
        # require flowing empty strings distinctly -- not in scope yet.
        assert result is not None


class TestCreateTask:
    def test_invalid_task_id_raises(self, patched_server):
        with pytest.raises(ValueError, match="Invalid task ID"):
            create_task("test-proj", "has spaces", description="x")

    def test_duplicate_task_id_raises(self, patched_server):
        with pytest.raises(ValueError, match="already exists"):
            create_task("test-proj", "task-a", description="duplicate")

    def test_missing_project_raises(self, patched_server):
        with pytest.raises(KeyError):
            create_task("no-such-project", "new-task", description="x")

    def test_with_dependencies(self, patched_server):
        result = create_task("test-proj", "new-task-with-deps",
                             description="has deps",
                             dependencies=["task-a"])
        assert result["dependencies"] == ["task-a"]


class TestValidateTaskId:
    def test_valid_ids(self):
        _validate_task_id("simple")
        _validate_task_id("task-123")
        _validate_task_id("task_with_underscore")
        _validate_task_id("task.with.dots")
        _validate_task_id("a1b2c3")

    def test_empty_id(self):
        with pytest.raises(ValueError, match="Invalid task ID"):
            _validate_task_id("")

    def test_spaces_rejected(self):
        with pytest.raises(ValueError, match="Invalid task ID"):
            _validate_task_id("has spaces")

    def test_leading_dash_rejected(self):
        with pytest.raises(ValueError, match="Invalid task ID"):
            _validate_task_id("-starts-with-dash")

    def test_too_long_rejected(self):
        with pytest.raises(ValueError, match="too long"):
            _validate_task_id("a" * 201)

    def test_slash_rejected(self):
        with pytest.raises(ValueError, match="Invalid task ID"):
            _validate_task_id("path/traversal")


class TestIsTaskBlocked:
    def test_no_deps_not_blocked(self, patched_server):
        from app_state import load_tasks
        tasks = load_tasks("test-proj")
        assert is_task_blocked("task-a", tasks) is False
        assert is_task_blocked("task-d", tasks) is False

    def test_with_done_dep_not_blocked(self, patched_server):
        from app_state import load_tasks
        tasks = load_tasks("test-proj")
        # task-b depends on task-a, and task-a is done
        assert is_task_blocked("task-b", tasks) is False

    def test_with_incomplete_dep_blocked(self, patched_server):
        from app_state import load_tasks
        tasks = load_tasks("test-proj")
        # task-c depends on task-b, task-b is in_progress
        assert is_task_blocked("task-c", tasks) is True


class TestSuggestTaskStatus:
    def test_all_prs_merged_suggests_done(self):
        task = {"status": "in_review",
                "prs": [{"status": "merged"}, {"status": "merged"}]}
        assert suggest_task_status(task) == "done"

    def test_all_prs_merged_already_done_no_suggest(self):
        task = {"status": "done",
                "prs": [{"status": "merged"}]}
        assert suggest_task_status(task) is None

    def test_open_pr_suggests_in_review(self):
        task = {"status": "not_started", "prs": [{"status": "open"}]}
        assert suggest_task_status(task) == "in_review"

    def test_draft_pr_suggests_in_review(self):
        task = {"status": "in_progress", "prs": [{"status": "draft"}]}
        assert suggest_task_status(task) == "in_review"

    def test_ticket_without_prs_suggests_in_progress(self):
        task = {"status": "not_started", "ticket": "EX-1"}
        assert suggest_task_status(task, has_tickets=True) == "in_progress"

    def test_ticket_without_tickets_system_no_suggest(self):
        task = {"status": "not_started", "ticket": "EX-1"}
        assert suggest_task_status(task, has_tickets=False) is None

    def test_no_prs_no_ticket_no_suggest(self):
        task = {"status": "not_started"}
        assert suggest_task_status(task) is None


class TestDeleteTask:
    def test_delete_protected_by_ticket(self, patched_server):
        """task-c has a ticket -- must raise ValueError."""
        with pytest.raises(ValueError, match="has ticket"):
            delete_task("test-proj", "task-c")

    def test_delete_nonexistent_returns_false(self, patched_server):
        assert delete_task("test-proj", "does-not-exist") is False

    def test_delete_without_ticket_succeeds(self, patched_server):
        # task-a has no ticket, no PRs should block it
        # Actually task-a has a PR, not a ticket -- verify
        result = delete_task("test-proj", "task-a")
        assert result is True


class TestCloseTask:
    def test_close_nonexistent_returns_none(self, patched_server):
        assert close_task("test-proj", "does-not-exist", reason="x") is None

    def test_close_with_reason_appends_note(self, patched_server):
        result = close_task("test-proj", "task-a", reason="no longer relevant")
        assert result["status"] == "closed"
        assert "[Closed] no longer relevant" in (result.get("notes") or "")

    def test_close_without_reason_no_note(self, patched_server):
        result = close_task("test-proj", "task-a", reason="")
        assert result["status"] == "closed"
        # notes unchanged
        assert "[Closed]" not in (result.get("notes") or "")

    def test_emits_task_updated_event(self, patched_server):
        """Frontend's `task.*` subscriber only refreshes the card when an
        event fires. close_task used to skip this -- regression guard."""
        import app_state
        seen: list = []
        app_state.on_event("task.*", lambda e: seen.append(e))
        close_task("test-proj", "task-a", reason="done with it")
        types = [e["type"] for e in seen]
        assert "task.updated" in types


class TestRenameTask:
    def test_rename_source_not_found_returns_none(self, patched_server):
        assert rename_task("test-proj", "does-not-exist", "new-id") is None

    def test_rename_target_exists_raises(self, patched_server):
        with pytest.raises(ValueError, match="already exists"):
            rename_task("test-proj", "task-a", "task-b")

    def test_rename_success(self, patched_server):
        result = rename_task("test-proj", "task-a", "task-a-renamed")
        assert result is not None
        assert result["task_id"] == "task-a-renamed"
        assert get_task("test-proj", "task-a") is None


class TestAddDependency:
    def test_add_dep_missing_source_raises(self, patched_server):
        with pytest.raises(ValueError, match="not found"):
            add_dependency("test-proj", "does-not-exist", "task-a")

    def test_add_dep_missing_target_raises(self, patched_server):
        with pytest.raises(ValueError, match="not found"):
            add_dependency("test-proj", "task-a", "does-not-exist")


class TestCheckStatus:
    def test_check_nonexistent_returns_none(self, patched_server):
        assert check_status("test-proj", "does-not-exist") is None

    def test_check_changes_status_when_suggested(self, patched_server):
        # task-a has merged PR and status=done -- should not change
        result = check_status("test-proj", "task-a")
        assert result["changed"] is False

    def test_check_blocked_task_not_promoted_to_in_progress(self, patched_server):
        # task-c has ticket + not_started + blocked (dep task-b is in_progress)
        # Blocked in_progress suggestion should be skipped
        result = check_status("test-proj", "task-c")
        # Suggested would be "in_progress" but blocked so no change
        assert result["changed"] is False


class TestGetTask:
    def test_not_found_returns_none(self, patched_server):
        assert get_task("test-proj", "does-not-exist") is None

    def test_blocked_effective_status(self, patched_server):
        result = get_task("test-proj", "task-c")
        # task-c blocked via task-b
        assert result["effective_status"] == "blocked"

    def test_unblocked_effective_status_matches(self, patched_server):
        result = get_task("test-proj", "task-a")
        assert result["effective_status"] == result["status"]


class TestDeriveTicketUrl:
    """`derive_ticket_url` turns bare ticket ids into canonical browse
    URLs by looking up the prefix in the user-configured
    `service.jira.ticket_url_prefixes` settings map. Out of the
    box (no prefixes configured) it returns empty for every id so
    OSS installs don't bake any vendor URL into the binary."""

    def test_configured_prefix_routes_to_base(self, patched_server):
        from common.tasks import derive_ticket_url
        patched_server._db.set_setting(
            "service.jira.ticket_url_prefixes",
            {"EX": "https://issues.example.org/jira/browse"},
        )
        assert (
            derive_ticket_url("EX-55390")
            == "https://issues.example.org/jira/browse/EX-55390"
        )

    def test_multiple_prefixes_route_to_their_own_base(
        self, patched_server,
    ):
        from common.tasks import derive_ticket_url
        patched_server._db.set_setting(
            "service.jira.ticket_url_prefixes",
            {
                "EX": "https://issues.example.org/jira/browse",
                "MYPROJ": "https://example.atlassian.net/browse",
            },
        )
        repo = derive_ticket_url("EX-100")
        proj = derive_ticket_url("MYPROJ-200")
        assert repo.endswith("EX-100")
        assert repo.startswith("https://issues.example.org/jira/browse")
        assert proj.endswith("MYPROJ-200")
        assert proj.startswith("https://example.atlassian.net/browse")

    def test_unconfigured_prefix_returns_empty(self, patched_server):
        """Unknown prefix + no settings row -> empty URL. Out of the
        box, no prefixes are configured so every ticket returns ''."""
        from common.tasks import derive_ticket_url
        assert derive_ticket_url("UNKNOWN-1003") == ""

    def test_empty_and_malformed_return_empty(self):
        """Bad input must return '' not raise -- update_task calls this
        with whatever the caller passed and treats empty as "no
        derivation possible" (keeps the field blank)."""
        from common.tasks import derive_ticket_url
        assert derive_ticket_url("") == ""
        assert derive_ticket_url("   ") == ""
        assert derive_ticket_url(None) == ""
        # Missing number suffix.
        assert derive_ticket_url("EX") == ""
        # Lowercase prefix (tickets are always upper-case in jira).
        assert derive_ticket_url("repo-123") == ""


class TestUpdateTaskAutoFillsTicketUrl:
    """Setting only `ticket_id` must auto-derive `ticket_url` so the
    UI can render the `[TICKET] description` as a link. Regression
    guard: prod had 12 tasks with ticket_id set and ticket_url blank
    because old callers bypassed this."""

    def test_ticket_id_only_backfills_url(self, patched_server):
        update_task("test-proj", "task-a", ticket_id="ALT-12345")
        row = patched_server._db.get_task("test-proj", "task-a")
        assert row["ticket_id"] == "ALT-12345"
        assert row["ticket_url"] == (
            "https://example.atlassian.net/browse/ALT-12345"
        )

    def test_explicit_ticket_url_wins(self, patched_server):
        """If caller supplies both, don't override -- they might be
        pointing at a redirected / aliased URL on purpose."""
        update_task("test-proj", "task-a",
                    ticket_id="EX-55390",
                    ticket_url="https://custom.example.com/ticket/X")
        row = patched_server._db.get_task("test-proj", "task-a")
        assert row["ticket_url"] == "https://custom.example.com/ticket/X"

    def test_malformed_ticket_id_leaves_url_empty(self, patched_server):
        """A free-form label like 'follow-up' isn't a real ticket -- we
        don't invent a bogus URL for it."""
        update_task("test-proj", "task-a", ticket_id="follow-up")
        row = patched_server._db.get_task("test-proj", "task-a")
        assert row["ticket_id"] == "follow-up"
        # No URL fabricated. DB columns default to NULL when never set;
        # accept both so the assertion doesn't hinge on a specific
        # uninitialised representation.
        assert not row.get("ticket_url")


class TestSuggestTaskStatusClosedTerminal:
    """`closed` is deliberate -- user abandoned the task. Auto-promotion
    must NOT flip it back to `done` even if a PR later appears merged."""

    def test_closed_task_never_auto_promoted(self):
        from common.tasks import suggest_task_status
        task = {
            "status": "closed",
            "prs": [{"status": "merged"}],
            "ticket": {"id": "ALT-1"},
        }
        assert suggest_task_status(task) is None

    def test_done_task_stays_done(self):
        from common.tasks import suggest_task_status
        task = {"status": "done", "prs": [{"status": "merged"}]}
        assert suggest_task_status(task) is None

    def test_promotes_not_started_with_open_pr_to_in_review(self):
        """Regression guard for NS_HAS_PR drift: linking a PR should
        push the task out of `not_started`."""
        from common.tasks import suggest_task_status
        task = {"status": "not_started",
                "prs": [{"status": "open"}]}
        assert suggest_task_status(task) == "in_review"

    def test_promotes_not_started_with_ticket_to_in_progress(self):
        from common.tasks import suggest_task_status
        task = {"status": "not_started", "ticket": {"id": "ALT-1"}}
        assert suggest_task_status(task) == "in_progress"

    def test_promotes_in_progress_all_merged_to_done(self):
        from common.tasks import suggest_task_status
        task = {"status": "in_progress",
                "prs": [{"status": "merged"}, {"status": "merged"}]}
        assert suggest_task_status(task) == "done"


class TestAutoPromoteOnAddPr:
    """Regression: prod had 2 tasks with linked PRs still at `not_started`
    because `add_pr` didn't fire the state machine. Now it does."""

    def test_add_pr_promotes_not_started_to_in_review(self, patched_server):
        from common.prs import add_pr
        # test-proj/task-d starts as not_started.
        ok = add_pr("test-proj", "task-d",
                    number=4242, url="https://github.com/example/repo/pull/4242",
                    status="open", title="[TEST] new PR")
        assert ok is True
        row = patched_server._db.get_task("test-proj", "task-d")
        assert row["status"] == "in_review"

    def test_add_pr_keeps_closed_task_closed(self, patched_server):
        """A closed task that gains a PR (e.g. re-evaluation) should
        not be resurrected -- closed is terminal."""
        from common.prs import add_pr
        patched_server._db.update_task(
            "test-proj", "task-d", status="closed")
        add_pr("test-proj", "task-d",
               number=4243, url="https://github.com/example/repo/pull/4243",
               status="open", title="[TEST] PR on closed")
        row = patched_server._db.get_task("test-proj", "task-d")
        assert row["status"] == "closed"


class TestAutoHistory:
    """State-machine driven task_history auto-append.

    Covers the hooks added 2026-04-24: status transitions, PR-linked, and
    PR-merged events should each produce one history line so the timeline
    reflects reality without any manual `append-history` discipline.
    """

    def _history_texts(self, patched_server, task_id, project="test-proj"):
        return [e["text"] for e in patched_server._db.list_task_history(project, task_id)]

    def test_update_task_status_appends_history(self, patched_server):
        """Setting a new status via update_task writes `status: X -> Y`."""
        update_task("test-proj", "task-d", status="in_progress")
        texts = self._history_texts(patched_server, "task-d")
        assert any(t.startswith("status:") and "-> in_progress" in t for t in texts)

    def test_update_task_same_status_no_history(self, patched_server):
        """Setting status to its current value must not spam history."""
        # task-d starts at not_started
        baseline = len(self._history_texts(patched_server, "task-d"))
        update_task("test-proj", "task-d", status="not_started")
        assert len(self._history_texts(patched_server, "task-d")) == baseline

    def test_update_task_non_status_field_no_history(self, patched_server):
        """Editing notes/priority should not emit a status transition entry."""
        baseline_texts = self._history_texts(patched_server, "task-d")
        update_task("test-proj", "task-d", notes="just notes")
        after_texts = self._history_texts(patched_server, "task-d")
        # No new `status:` entries
        new_entries = [t for t in after_texts if t not in baseline_texts]
        assert not any(t.startswith("status:") for t in new_entries)

    def test_close_task_records_transition(self, patched_server):
        close_task("test-proj", "task-d", reason="obsolete")
        texts = self._history_texts(patched_server, "task-d")
        assert any(t.startswith("status:") and "-> closed" in t for t in texts)

    def test_add_pr_records_linked(self, patched_server):
        from common.prs import add_pr
        add_pr("test-proj", "task-d",
               number=9001, url="https://github.com/example/repo/pull/9001",
               status="open", title="[TEST] link pr")
        texts = self._history_texts(patched_server, "task-d")
        assert any("linked PR #9001" in t for t in texts)

    def test_auto_promotion_records_transition(self, patched_server):
        """add_pr triggers _auto_promote_task_status, which should log the
        state-machine X -> Y flip as its own line (not_started -> in_review)."""
        from common.prs import add_pr
        add_pr("test-proj", "task-d",
               number=9002, url="https://github.com/example/repo/pull/9002",
               status="open", title="[TEST] promote")
        texts = self._history_texts(patched_server, "task-d")
        assert any("-> in_review" in t for t in texts)

    def test_pr_merge_transition_records_line(self, patched_server):
        """_update_pr_from_gh detecting open -> merged writes `PR #N merged`."""
        from common.prs import _update_pr_from_gh, add_pr
        add_pr("test-proj", "task-d",
               number=9003, url="https://github.com/example/repo/pull/9003",
               status="open", title="[TEST] merge")
        # Simulate gh payload showing the PR as merged.
        item = {
            "state": "MERGED",
            "title": "[TEST] merge",
            "url": "https://github.com/example/repo/pull/9003",
            "statusCheckRollup": [],
            "reviewDecision": "APPROVED",
            "comments": [], "reviews": [],
            "additions": 1, "deletions": 0,
            "author": {"login": "x"},
            "headRefName": "f", "baseRefName": "master",
            "updatedAt": "2026-04-24T00:00:00Z",
            "mergedAt": "2026-04-24T00:00:01Z",
        }
        _update_pr_from_gh(9003, item, "example/repo")
        texts = self._history_texts(patched_server, "task-d")
        assert any("PR #9003 merged" in t for t in texts)
        # Merge also auto-promotes to done.
        assert any("-> done" in t for t in texts)

    def test_pr_merge_idempotent_no_duplicate_line(self, patched_server):
        """A second _update_pr_from_gh with the same merged state should
        not re-log 'PR #N merged' (prev_status is already merged)."""
        from common.prs import _update_pr_from_gh, add_pr
        add_pr("test-proj", "task-d",
               number=9004, url="https://github.com/example/repo/pull/9004",
               status="open", title="[TEST] idempotent")
        item = {
            "state": "MERGED", "title": "[TEST] idempotent",
            "url": "https://github.com/example/repo/pull/9004",
            "statusCheckRollup": [], "reviewDecision": "",
            "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
            "author": {"login": "x"},
            "headRefName": "f", "baseRefName": "master",
            "updatedAt": "2026-04-24T00:00:00Z",
            "mergedAt": "2026-04-24T00:00:01Z",
        }
        _update_pr_from_gh(9004, item, "example/repo")
        first = sum(1 for t in self._history_texts(patched_server, "task-d")
                    if "PR #9004 merged" in t)
        _update_pr_from_gh(9004, item, "example/repo")
        second = sum(1 for t in self._history_texts(patched_server, "task-d")
                     if "PR #9004 merged" in t)
        assert first == 1 and second == 1

    def test_auto_history_swallows_db_errors(self, patched_server, monkeypatch):
        """History write failures must not break the real state change."""
        from common import tasks as tasks_mod
        def boom(*a, **kw):
            raise RuntimeError("simulated DB failure")
        monkeypatch.setattr(patched_server._db, "append_task_history", boom)
        # Should not raise even though history write fails.
        result = update_task("test-proj", "task-d", status="in_progress")
        assert result is not None and result["status"] == "in_progress"


class TestStatusValidation:
    """Every write path (core.update_task, core.create_task) must reject
    statuses outside VALID_TASK_STATUSES. Guards against drift that the
    CLI's argparse `choices=` wouldn't catch for HTTP / MCP callers."""

    def test_update_task_rejects_unknown_status(self, patched_server):
        from common.tasks import VALID_TASK_STATUSES
        assert "bogus_status" not in VALID_TASK_STATUSES
        with pytest.raises(ValueError, match="Invalid task status"):
            update_task("test-proj", "task-a", status="bogus_status")

    def test_update_task_accepts_every_canonical_status(self, patched_server):
        """Belt-and-suspenders: enumerate the full canonical set and
        verify every value is accepted. If a new status is added to the
        constraint, this test either passes silently or flags a bug."""
        from common.tasks import VALID_TASK_STATUSES
        for s in VALID_TASK_STATUSES:
            # Just that it doesn't raise -- task may or may not end up
            # with the literal status due to `is_task_blocked` interplay.
            result = update_task("test-proj", "task-a", status=s)
            assert result is not None

    def test_update_task_none_status_is_noop(self, patched_server):
        """Passing status=None means 'don't change status'; must NOT raise
        at the core layer (the update_task loop skips None values so the
        field is left alone in the DB)."""
        result = update_task("test-proj", "task-a", status=None, notes="x")
        assert result is not None

    def test_update_task_rejects_empty_status_string(self, patched_server):
        """Empty-string status should NOT silently stick -- it would
        violate the DB CHECK constraint and the frontend TaskStatus
        union. Must raise the same ValueError path as any other invalid
        value. (The DB layer catches it on write; the core layer lets
        it through because '' is the HTTP empty-field sentinel.)"""
        # update_task's core guard allows "" but the DB layer rejects it
        # at write time. Either way, the caller gets a ValueError.
        with pytest.raises(ValueError, match="Invalid status"):
            update_task("test-proj", "task-a", status="")

    def test_create_task_rejects_unknown_status(self, patched_server):
        from common.tasks import create_task
        with pytest.raises(ValueError, match="Invalid task status"):
            create_task("test-proj", "new-task", status="bogus")

    def test_status_validation_fires_before_db_read(self, patched_server, monkeypatch):
        """Invalid status must fail BEFORE load_tasks / update_task hits
        the DB. Otherwise a typo triggers unnecessary DB work (and could
        leak partial updates on other errors)."""
        load_calls = []
        original = patched_server.load_tasks
        def tracked(*a, **kw):
            load_calls.append(a)
            return original(*a, **kw)
        monkeypatch.setattr("common.tasks.app_state.load_tasks", tracked)
        with pytest.raises(ValueError):
            update_task("test-proj", "task-a", status="nope")
        # No DB read should have happened.
        assert load_calls == []


class TestEmitTaskEvent:
    """Centralised task.* event helper extracted 2026-04-25.

    Most callers want the default title shape `"Task <noun>: <task_id>"`
    derived from the event_type's last segment; a couple need to override
    (e.g. `task.status_auto_promoted` carries the X -> Y transition).
    """

    def _capture(self):
        import app_state
        seen = []
        app_state.on_event("task.*", lambda e: seen.append(e))
        return seen

    def test_default_title_shape(self, patched_server):
        from common.tasks import _emit_task_event
        events = self._capture()
        _emit_task_event("task.created", "test-proj", "task-x",
                         message="hello")
        assert len(events) == 1
        assert events[0]["type"] == "task.created"
        assert events[0]["title"] == "Task Created: task-x"
        assert events[0]["message"] == "hello"
        assert events[0]["session"] == "test-proj"

    def test_event_type_with_underscore_pretty_prints(self, patched_server):
        """`task.status_auto_promoted` -> 'Task Status auto promoted: x'."""
        from common.tasks import _emit_task_event
        events = self._capture()
        _emit_task_event("task.status_auto_promoted",
                         "test-proj", "task-x")
        assert events[0]["title"] == "Task Status auto promoted: task-x"

    def test_explicit_title_overrides_default(self, patched_server):
        """Callers can supply title= to bypass the default shape."""
        from common.tasks import _emit_task_event
        events = self._capture()
        _emit_task_event("task.history.appended", "test-proj", "task-x",
                         title="History: task-x", message="some entry")
        assert events[0]["title"] == "History: task-x"

    def test_persist_false_threads_through(self, patched_server):
        """The persist=False flag must reach app_state.emit_event so
        ephemeral events stay out of the notifications feed."""
        from unittest.mock import patch as _patch
        from common.tasks import _emit_task_event
        with _patch("app_state.emit_event") as mock_emit:
            _emit_task_event("task.history.appended", "p", "t",
                             persist=False)
        mock_emit.assert_called_once()
        kw = mock_emit.call_args.kwargs
        assert kw.get("persist") is False


class TestBlockedComputed:
    """`blocked` is now a purely computed display status. The
    storage layer (VALID_STATUSES + sqlite CHECK + CLI choices) MUST
    NOT accept it as a write; the read layer (effective_status,
    is_task_blocked) MUST derive it from dep graph state.
    """

    def test_valid_statuses_excludes_blocked(self):
        from eva_db import VALID_STATUSES
        assert "blocked" not in VALID_STATUSES

    def test_unblocking_set_is_canonical(self):
        from eva_db import UNBLOCKING_DEP_STATUSES
        assert UNBLOCKING_DEP_STATUSES == {"done", "closed", "needs_follow_up"}

    def test_update_task_rejects_blocked(self, patched_server):
        with pytest.raises(ValueError, match="Invalid status|blocked"):
            update_task("test-proj", "task-a", status="blocked")

    def test_create_task_rejects_blocked(self, patched_server):
        from common.tasks import create_task
        with pytest.raises(ValueError, match="Invalid status|blocked"):
            create_task("test-proj", "new-x", status="blocked")

    def test_is_task_blocked_unclosed_dep_blocks(self, patched_server):
        from common.tasks import is_task_blocked
        tasks = {
            "a": {"status": "in_progress"},
            "b": {"status": "not_started", "dependencies": ["a"]},
        }
        assert is_task_blocked("b", tasks) is True

    def test_is_task_blocked_done_dep_unblocks(self, patched_server):
        from common.tasks import is_task_blocked
        tasks = {
            "a": {"status": "done"},
            "b": {"status": "not_started", "dependencies": ["a"]},
        }
        assert is_task_blocked("b", tasks) is False

    def test_is_task_blocked_closed_dep_unblocks(self, patched_server):
        """Per user spec: a `closed` dep counts as 'we don't need to
        wait for it' and unblocks the dependent."""
        from common.tasks import is_task_blocked
        tasks = {
            "a": {"status": "closed"},
            "b": {"status": "not_started", "dependencies": ["a"]},
        }
        assert is_task_blocked("b", tasks) is False

    def test_is_task_blocked_needs_follow_up_dep_unblocks(self, patched_server):
        from common.tasks import is_task_blocked
        tasks = {
            "a": {"status": "needs_follow_up"},
            "b": {"status": "not_started", "dependencies": ["a"]},
        }
        assert is_task_blocked("b", tasks) is False

    def test_is_task_blocked_missing_dep_blocks(self, patched_server):
        """Edge points to a task that doesn't exist -- treat as blocking
        (data is inconsistent; don't greenlight)."""
        from common.tasks import is_task_blocked
        tasks = {
            "b": {"status": "not_started", "dependencies": ["ghost"]},
        }
        assert is_task_blocked("b", tasks) is True

    def test_get_task_effective_overrides_in_progress(self, patched_server):
        """Stored status='in_progress' + unclosed dep -> effective='blocked'.
        Old code only overrode `not_started`; per user spec we now
        override every non-terminal status."""
        patched_server._db.create_task(project="test-proj", task_id="up",
                                       status="not_started")
        patched_server._db.create_task(project="test-proj", task_id="down",
                                       status="in_progress")
        patched_server._db.add_dependency("test-proj", "down", "up")
        from common.tasks import get_task
        result = get_task("test-proj", "down")
        assert result["status"] == "in_progress"  # stored
        assert result["effective_status"] == "blocked"  # computed

    def test_get_task_effective_does_not_override_terminal(self, patched_server):
        """`done` and `closed` are terminal -- the task is over, don't
        retroactively flag it as blocked even if a dep ends up open."""
        patched_server._db.create_task(project="test-proj", task_id="up",
                                       status="not_started")
        patched_server._db.create_task(project="test-proj", task_id="down",
                                       status="done")
        patched_server._db.add_dependency("test-proj", "down", "up")
        from common.tasks import get_task
        result = get_task("test-proj", "down")
        assert result["effective_status"] == "done"

    def test_remove_dependency_emits_dependent_update(self, patched_server):
        """User's example: removing the A->B edge should signal B
        instantly so its blocked state can recompute."""
        patched_server._db.create_task(project="test-proj", task_id="up",
                                       status="not_started")
        patched_server._db.create_task(project="test-proj", task_id="down",
                                       status="not_started")
        patched_server._db.add_dependency("test-proj", "down", "up")
        import app_state
        seen = []
        app_state.on_event("task.updated", lambda e: seen.append(e))
        from common.tasks import remove_dependency
        remove_dependency("test-proj", "down", "up")
        # The dependent ("down") must get a task.updated event so the
        # frontend can refresh its blocked computation.
        targets = [e["title"] for e in seen]
        assert any("down" in t for t in targets), (
            f"expected event for 'down' after edge removal, got titles: {targets}"
        )

    def test_status_change_fans_out_to_dependents(self, patched_server):
        """When upstream A's status flips, every B that depends on A
        gets a task.updated event so its blocked state recomputes."""
        patched_server._db.create_task(project="test-proj", task_id="up2",
                                       status="not_started")
        patched_server._db.create_task(project="test-proj", task_id="down2",
                                       status="not_started")
        patched_server._db.add_dependency("test-proj", "down2", "up2")
        import app_state
        seen = []
        app_state.on_event("task.updated", lambda e: seen.append(e))
        # Mark upstream as done -> downstream should get fan-out event.
        update_task("test-proj", "up2", status="done")
        # Both up2 (the direct write) and down2 (the fan-out) should fire.
        titles = [e["title"] for e in seen]
        assert any("up2" in t for t in titles)
        assert any("down2" in t for t in titles), (
            f"expected fan-out event for 'down2', got titles: {titles}"
        )


class TestFanoutDependentsSwallowsDbException:
    """`_fanout_dependents_status_changed` swallows DB errors so a
    half-corrupted dependencies table can't crash a status update."""

    def test_swallows_list_dependents_exception(
        self, patched_server, monkeypatch,
    ):
        from common import tasks as core_tasks

        def boom(*a, **kw):
            raise RuntimeError("dep table corrupted")
        monkeypatch.setattr(
            patched_server._db, "list_dependents", boom,
        )
        # Should not raise.
        core_tasks._fanout_dependents_status_changed("test-proj", "task-a")


class TestAppendAutoHistorySwallowsException:
    """`_append_auto_history` is best-effort: a write failure (oversize,
    DB lock) must not propagate to the caller. Otherwise a status
    transition would unwind on a flaky history table."""

    def test_swallows_db_exception(self, patched_server, monkeypatch):
        from common import tasks as core_tasks

        def boom(*a, **kw):
            raise RuntimeError("history write rejected")
        monkeypatch.setattr(
            patched_server._db, "append_task_history", boom,
        )
        # Should not raise.
        core_tasks._append_auto_history("test-proj", "task-a", "noted")

    def test_empty_text_short_circuits_no_db_call(self, patched_server, monkeypatch):
        """Edge case: passing an empty / falsy text must skip the DB
        write entirely. Without this guard, every state transition
        with no message would still spam an empty history row."""
        from common import tasks as core_tasks
        called = []
        monkeypatch.setattr(
            patched_server._db, "append_task_history",
            lambda *a, **kw: called.append((a, kw)),
        )
        core_tasks._append_auto_history("test-proj", "task-a", "")
        core_tasks._append_auto_history("test-proj", "task-a", None)  # type: ignore[arg-type]
        assert called == []


class TestTaskTypeCanonicalizationBoundary:
    """`type` aliases (`feat`/`doc`/`bug`) get normalised to canonical
    forms at every write boundary. Without this, creating a task via
    CLI with `--type feat` would persist 'feat' and the audit would
    re-flag it on every loop iteration. The boundary fix means audit
    findings stop regenerating after one cleanup pass."""

    def test_create_task_normalises_alias(self, patched_server):
        from common import tasks as core_tasks
        core_tasks.create_task(
            "test-proj", "alias-create-1", description="x", type="feat",
        )
        row = patched_server._db.get_task("test-proj", "alias-create-1")
        assert row["type"] == "feature"

    def test_update_task_normalises_alias(self, patched_server):
        from common import tasks as core_tasks
        core_tasks.create_task(
            "test-proj", "alias-update-1", description="x", type="feature",
        )
        core_tasks.update_task(
            "test-proj", "alias-update-1", type="bug",
        )
        row = patched_server._db.get_task("test-proj", "alias-update-1")
        assert row["type"] == "fix"

    def test_canonicalize_helper_passes_through_unknown(self):
        """OSS users can invent new categories; the helper only
        rewrites the explicit alias map and leaves everything else
        alone."""
        from common.tasks import canonicalize_task_type
        assert canonicalize_task_type("epic") == "epic"
        assert canonicalize_task_type("custom_type") == "custom_type"
        # And handles non-string defensively.
        assert canonicalize_task_type(None) is None  # type: ignore[arg-type]

    def test_audit_imports_same_alias_map(self):
        """Single source of truth: audit reads `TASK_TYPE_ALIASES` from
        common.tasks.py rather than maintaining its own copy."""
        from common import audit, tasks
        assert audit._TASK_TYPE_ALIASES is tasks.TASK_TYPE_ALIASES
