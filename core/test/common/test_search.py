"""Tests for core/common.search.py and the /api/search route.

Relies on `patched_server`'s seeded fixture (test-proj: task-a merged PR
#100, task-b open PR #200, task-c blocked, task-d with ticket).
"""

import common
from unittest.mock import patch


class TestParseQuery:
    def test_plain_text(self):
        from common.search import parse_query
        q = parse_query("hello world")
        assert q.text == "hello world"
        assert q.type is None
        assert q.status is None

    def test_type_filter(self):
        from common.search import parse_query
        assert parse_query("type:task").type == "task"
        assert parse_query("type:PR").type == "pr"  # lowercased
        assert parse_query("type:session").type == "session"

    def test_status_filter(self):
        from common.search import parse_query
        assert parse_query("status:open").status == "open"

    def test_project_filter(self):
        from common.search import parse_query
        assert parse_query("project:test-proj").project == "test-proj"

    def test_in_task_filter(self):
        from common.search import parse_query
        assert parse_query("in:task").in_ == "task"

    def test_ticket_filter(self):
        from common.search import parse_query
        q = parse_query("ticket:EX-99999")
        # Stored lowercased for case-insensitive matching.
        assert q.ticket == "ex-99999"

    def test_ticket_filter_substring_text(self):
        """ticket:EX picks up all EX-prefixed IDs."""
        from common.search import parse_query
        q = parse_query("ticket:EX my feature")
        assert q.ticket == "ex"
        assert q.text == "my feature"

    def test_mixed_filter_and_text(self):
        from common.search import parse_query
        q = parse_query("type:pr in:task my feature")
        assert q.type == "pr"
        assert q.in_ == "task"
        assert q.text == "my feature"

    def test_unknown_key_falls_through_to_text(self):
        """`foo:bar` is NOT a recognised filter -> treated as search
        text. Prevents silent-drop surprises."""
        from common.search import parse_query
        q = parse_query("foo:bar hello")
        assert q.text == "foo:bar hello"

    def test_empty_query(self):
        from common.search import parse_query
        q = parse_query("")
        assert q.text == ""
        assert q.type is None

    def test_bare_type_shorthand_pr(self):
        """`pr` on its own acts like `type:pr` so `pr in:task` works."""
        from common.search import parse_query
        q = parse_query("pr in:task")
        assert q.type == "pr"
        assert q.in_ == "task"
        assert q.text == ""

    def test_bare_type_shorthand_session(self):
        from common.search import parse_query
        q = parse_query("session status:running")
        assert q.type == "session"
        assert q.status == "running"

    def test_bare_type_shorthand_task_plural(self):
        from common.search import parse_query
        assert parse_query("tasks").type == "task"
        assert parse_query("prs").type == "pr"
        assert parse_query("sessions").type == "session"

    def test_explicit_type_wins_over_shorthand(self):
        """`type:task pr` means tasks matching text 'pr', not tasks then
        PRs -- explicit filter stays authoritative."""
        from common.search import parse_query
        q = parse_query("type:task pr")
        assert q.type == "task"
        assert q.text == "pr"


class TestSearchTasks:
    def test_returns_all_tasks_on_empty_query(self, patched_server):
        from common.search import search
        results = search("")
        tids = [r["task_id"] for r in results if r["type"] == "task"]
        assert set(tids) >= {"task-a", "task-b", "task-c", "task-d"}

    def test_matches_by_task_id(self, patched_server):
        from common.search import search
        results = search("task-a")
        assert any(r["type"] == "task" and r["task_id"] == "task-a" for r in results)

    def test_matches_by_description(self, patched_server):
        from common.search import search
        results = search("foundation")  # task-a description
        assert any(r["task_id"] == "task-a" for r in results)

    def test_matches_by_ticket_id(self, patched_server):
        from common.search import search
        results = search("EX-99999")  # task-d ticket
        assert any(r["task_id"] == "task-d" for r in results)

    def test_case_insensitive(self, patched_server):
        from common.search import search
        results = search("TASK-A")
        assert any(r["task_id"] == "task-a" for r in results)

    def test_type_task_restricts_to_tasks_only(self, patched_server):
        from common.search import search
        results = search("type:task")
        assert all(r["type"] == "task" for r in results)

    def test_status_filter(self, patched_server):
        from common.search import search
        results = search("type:task status:done")
        assert all(r["badge"] == "done" for r in results)

    def test_project_filter(self, patched_server):
        from common.search import search
        results = search("type:task project:test-proj")
        assert all(r["project_id"] == "test-proj" for r in results)

    def test_task_result_shape(self, patched_server):
        from common.search import search
        results = search("task-a")
        tasks = [r for r in results if r["type"] == "task"]
        assert tasks, "task-a should be found"
        t = tasks[0]
        for key in ("type", "title", "subtitle", "badge", "project_id", "task_id"):
            assert key in t, f"missing key: {key}"

    def test_matches_by_notes(self, patched_server):
        """Free-text search covers the notes field."""
        patched_server._db.update_task(
            "test-proj", "task-a", notes="magic-notes-marker pending refactor"
        )
        from common.search import search
        results = search("magic-notes-marker")
        assert any(r["type"] == "task" and r["task_id"] == "task-a" for r in results)

    def test_matches_by_ticket_url(self, patched_server):
        """Free-text search covers the ticket_url field too."""
        from common.search import search
        # task-d has ticket_url=".../EX-99999"
        results = search("issues.example.org")
        assert any(r["type"] == "task" and r["task_id"] == "task-d" for r in results)

    def test_ticket_filter_exact(self, patched_server):
        """ticket:EX-99999 narrows to that one task."""
        from common.search import search
        results = search("ticket:EX-99999")
        tasks = [r for r in results if r["type"] == "task"]
        tids = {r["task_id"] for r in tasks}
        assert "task-d" in tids
        assert "task-c" not in tids

    def test_ticket_filter_substring(self, patched_server):
        """ticket:EX picks up every EX-prefixed task."""
        from common.search import search
        results = search("ticket:EX")
        tasks = [r for r in results if r["type"] == "task"]
        tids = {r["task_id"] for r in tasks}
        assert {"task-c", "task-d"}.issubset(tids)

    def test_ticket_filter_drops_no_ticket_tasks(self, patched_server):
        """Tasks without a ticket (task-a, task-b) are excluded when a
        ticket filter is active."""
        from common.search import search
        results = search("ticket:EX")
        tids = {r["task_id"] for r in results if r["type"] == "task"}
        assert "task-a" not in tids
        assert "task-b" not in tids


class TestSearchPrs:
    def test_returns_prs(self, patched_server):
        from common.search import search
        results = search("type:pr")
        prs = [r for r in results if r["type"] == "pr"]
        assert any(r["pr_number"] == 100 for r in prs)
        assert any(r["pr_number"] == 200 for r in prs)

    def test_pr_result_shape(self, patched_server):
        from common.search import search
        results = search("type:pr")
        prs = [r for r in results if r["type"] == "pr"]
        pr = prs[0]
        for key in ("type", "title", "subtitle", "badge", "project_id",
                    "pr_number", "pr_repo"):
            assert key in pr
        assert pr["title"].startswith("#")  # "#100"

    def test_in_task_filter(self, patched_server):
        """in:task only returns PRs linked to a task. In patched_server
        all seeded PRs are linked, so the result set stays the same --
        but the filter must not drop linked PRs."""
        from common.search import search
        with_filter = [r for r in search("type:pr in:task") if r["type"] == "pr"]
        assert any(r["pr_number"] == 100 for r in with_filter)

    def test_status_filter(self, patched_server):
        from common.search import search
        results = search("type:pr status:merged")
        prs = [r for r in results if r["type"] == "pr"]
        assert all(r["badge"] == "merged" for r in prs)
        assert any(r["pr_number"] == 100 for r in prs)

    def test_ticket_filter_narrows_to_linked_prs(self, patched_server):
        """PRs inherit their parent task's ticket; ticket:EX-123 keeps
        only the PR whose linked task has EX-123."""
        # Link a PR to task-c (which has ticket EX-123).
        patched_server._db.add_pr(
            project="test-proj", task_id="task-c", number=777,
            url="https://github.com/example/repo/pull/777",
            status="open", title="ticket-linked PR",
        )
        from common.search import search
        prs = [r for r in search("type:pr ticket:EX-123") if r["type"] == "pr"]
        numbers = {r["pr_number"] for r in prs}
        assert 777 in numbers
        # PRs on task-a / task-b (no ticket) are excluded.
        assert 100 not in numbers
        assert 200 not in numbers


class TestSearchSessions:
    def test_finds_session_by_task_id(self, patched_server):
        from common.search import search
        patched_server._db.create_session("task-b", "test-proj")
        results = search("type:session task-b")
        sessions = [r for r in results if r["type"] == "session"]
        assert any(r["task_id"] == "task-b" for r in sessions)

    def test_session_result_shape(self, patched_server):
        from common.search import search
        patched_server._db.create_session("task-b", "test-proj")
        results = search("type:session")
        sessions = [r for r in results if r["type"] == "session"]
        s = sessions[0]
        for key in ("type", "title", "subtitle", "badge", "project_id", "task_id"):
            assert key in s

    def test_matches_session_by_parent_ticket(self, patched_server):
        """Sessions should surface when their parent task's ticket matches."""
        from common.search import search
        # task-d has ticket EX-99999.
        patched_server._db.create_session("task-d", "test-proj")
        results = search("EX-99999")
        sessions = [r for r in results if r["type"] == "session"]
        assert any(s["task_id"] == "task-d" for s in sessions)

    def test_matches_session_by_parent_notes(self, patched_server):
        """Session search free-text should hit parent task notes."""
        patched_server._db.update_task(
            "test-proj", "task-a", notes="find-me-session-notes please"
        )
        patched_server._db.create_session("task-a", "test-proj")
        from common.search import search
        results = search("type:session find-me-session-notes")
        sessions = [r for r in results if r["type"] == "session"]
        assert any(s["task_id"] == "task-a" for s in sessions)

    def test_project_filter_excludes_other_projects(self, patched_server):
        """`project:test-proj` must drop sessions owned by other projects.

        Exercises the `if q.project and pid != q.project: continue` branch
        in _search_sessions (core/common.search.py:237-238).
        """
        patched_server._db.create_session("task-a", "test-proj")
        # Seed a session under a different project_id so we can assert
        # the project filter drops it.
        patched_server._db.create_session("task-other", "empty-proj")
        from common.search import search
        results = search("type:session project:test-proj")
        sessions = [r for r in results if r["type"] == "session"]
        assert all(s["project_id"] == "test-proj" for s in sessions)
        # Sanity check: the unfiltered search includes the other-project
        # session so the filter is what dropped it.
        all_results = search("type:session")
        all_sessions = [r for r in all_results if r["type"] == "session"]
        assert any(s["project_id"] == "empty-proj" for s in all_sessions)

    def test_status_filter_on_sessions(self, patched_server):
        """`status:stopped` should filter sessions by their DB status.

        Exercises the status-filter branch in _search_sessions
        (core/common.search.py:239-240). `create_session` defaults to status
        'running', so explicitly flipping one row covers both branches.
        """
        patched_server._db.create_session("task-a", "test-proj")
        patched_server._db.create_session("task-b", "test-proj")
        # Force one into a non-matching status via the public API so we
        # don't couple the test to EvaDB's internal sqlite connection.
        patched_server._db.update_session("task-a", status="running")
        patched_server._db.update_session("task-b", status="stopped")
        from common.search import search
        stopped = [r for r in search("type:session status:stopped")
                   if r["type"] == "session"]
        assert all(r["task_id"] == "task-b" for r in stopped)

    def test_ticket_filter_applies_to_sessions(self, patched_server):
        """ticket:EX filter picks up sessions whose parent task's
        ticket starts with EX."""
        patched_server._db.create_session("task-c", "test-proj")  # EX-123
        patched_server._db.create_session("task-a", "test-proj")  # no ticket
        from common.search import search
        results = search("ticket:EX")
        sessions = [r for r in results if r["type"] == "session"]
        tids = {s["task_id"] for s in sessions}
        assert "task-c" in tids
        assert "task-a" not in tids


class TestTruncateHelper:
    def test_short_text_passes_through(self):
        from common.search import _truncate
        assert _truncate("short") == "short"
        # Exactly at limit: no ellipsis.
        assert _truncate("x" * 60) == "x" * 60

    def test_long_text_is_clipped_with_ellipsis(self):
        """Long search-dropdown descriptions get clipped to 60 chars + '...'
        so they don't overflow a table row. Covers core/common.search.py:131-133."""
        from common.search import _truncate
        long_desc = "x" * 150
        out = _truncate(long_desc)
        assert out == "x" * 60 + "..."

    def test_long_task_description_appears_truncated_in_subtitle(
        self, patched_server
    ):
        """Through the public `search()` entry: a task whose description
        is >60 chars surfaces with an ellipsis in the dropdown subtitle."""
        from common import search as search_mod
        patched_server._db.update_task(
            "test-proj", "task-b",
            description="A" * 100,  # well past the 60-char limit
        )
        rows = [r for r in search_mod.search("type:task task-b")
                if r["type"] == "task" and r["task_id"] == "task-b"]
        assert rows
        assert "..." in rows[0]["subtitle"]


class TestSearchErrorHandling:
    """Defensive `try/except` branches in the search module must return
    graceful empty/partial results when the DB raises. Previously only the
    happy paths were exercised; a corrupted DB would silently crash the
    search dropdown."""

    def test_list_projects_failure_returns_no_tasks(self, patched_server):
        """_project_names catches list_projects exceptions and falls back
        to {}, so search never 500s when the projects table is unreadable."""
        from common import search as search_mod
        with patch.object(
            patched_server._db, "list_projects", side_effect=RuntimeError("io")
        ):
            # The top-level search still returns a list, not raise.
            results = search_mod.search("whatever")
            assert isinstance(results, list)

    def test_list_tasks_failure_per_project_is_swallowed(self, patched_server):
        """`_search_tasks` wraps the per-project list_tasks in try/except
        so one bad project can't block results from other projects.

        Simulates a locked DB on test-proj and asserts the search still
        returns (empty for that project) without raising.
        """
        from common import search as search_mod
        with patch.object(
            patched_server._db, "list_tasks", side_effect=RuntimeError("locked")
        ):
            results = search_mod.search("type:task anything")
            # No tasks should surface, but no exception.
            assert results == []

    def test_list_sessions_failure_returns_empty(self, patched_server):
        """_search_sessions catches list_sessions failure (sqlite busy,
        schema drift) so the dropdown still shows tasks/PRs."""
        from common import search as search_mod
        with patch.object(
            patched_server._db, "list_sessions", side_effect=RuntimeError("busy")
        ):
            results = search_mod.search("type:session")
            assert results == []

    def test_list_all_prs_failure_returns_empty(self, patched_server):
        """_search_prs catches list_all_prs failures so the other entity
        types still appear. Guards the same way _search_sessions does."""
        from common import search as search_mod
        with patch.object(
            patched_server._db, "list_all_prs", side_effect=RuntimeError("busy")
        ):
            results = search_mod.search("type:pr")
            assert results == []

    def test_session_parent_get_task_failure_is_swallowed(self, patched_server):
        """`_search_sessions` looks up the parent task for each session
        so it can match on the task's ticket / notes / description. When
        that lookup raises, the session row must still be considered (so
        a session rooted on a damaged task isn't invisible)."""
        from common import search as search_mod
        # Seed a session tied to a real task.
        patched_server._db.create_session("task-b", "test-proj")
        # Make get_task raise for every call.
        with patch.object(
            patched_server._db, "get_task", side_effect=RuntimeError("oops")
        ):
            sessions = [r for r in search_mod.search("type:session task-b")
                        if r["type"] == "session"]
            assert any(s["task_id"] == "task-b" for s in sessions)

    def test_pr_ticket_lookup_get_task_failure_is_swallowed(self, patched_server):
        """`_pr_ticket` caches parent-task lookups while filtering PRs by
        ticket. If get_task raises the lookup must cache None and move
        on instead of crashing the whole search.

        Exercises core/common.search.py:289-290 -- the Exception branch inside
        the `_pr_ticket` closure.
        """
        from common import search as search_mod
        with patch.object(
            patched_server._db, "get_task", side_effect=RuntimeError("io")
        ):
            # ticket-filtered PR search triggers the cached lookup.
            results = search_mod.search("type:pr ticket:EX")
            # No exception; just an empty PR list (no ticket_id could be
            # resolved) so no PR matches the ticket filter.
            pr_rows = [r for r in results if r["type"] == "pr"]
            assert pr_rows == []

    def test_pr_project_filter_drops_other_projects(self, patched_server):
        """`project:test-proj` on a PR search must drop PRs that live
        under a different project (exercises core/common.search.py:297)."""
        from common import search as search_mod
        # Seed a PR under empty-proj (the other seeded project).
        patched_server._db.create_task(
            project="empty-proj", task_id="e-task", description="x",
        )
        patched_server._db.add_pr(
            project="empty-proj", task_id="e-task", number=7777,
            url="https://github.com/example/repo/pull/7777",
            title="other-project PR", status="open",
        )
        filtered = [r for r in search_mod.search("type:pr project:test-proj")
                    if r["type"] == "pr"]
        pr_numbers = {r["pr_number"] for r in filtered}
        assert 100 in pr_numbers  # seeded under test-proj
        assert 7777 not in pr_numbers  # under empty-proj


class TestLimitAndOrdering:
    def test_honours_limit(self, patched_server):
        from common.search import search
        results = search("", limit=2)
        assert len(results) == 2

    def test_tasks_before_prs(self, patched_server):
        """Stable ordering: tasks -> sessions -> PRs. Keeps the dropdown
        predictable so users learn keyboard shortcuts."""
        from common.search import search
        results = search("", limit=100)
        types = [r["type"] for r in results]
        # If both task and pr appear, task must come before pr.
        if "task" in types and "pr" in types:
            assert types.index("task") < types.index("pr")


class TestSearchRoute:
    def test_endpoint_returns_results(self, client, patched_server):
        resp = client.get("/api/search?q=task-a")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert any(r["task_id"] == "task-a" for r in data["results"])

    def test_endpoint_empty_query_returns_everything(self, client, patched_server):
        resp = client.get("/api/search")
        assert resp.status_code == 200
        assert resp.json()["results"]

    def test_endpoint_limit_param(self, client, patched_server):
        resp = client.get("/api/search?limit=1")
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 1

    def test_endpoint_type_filter(self, client, patched_server):
        resp = client.get("/api/search?q=type:pr")
        assert resp.status_code == 200
        data = resp.json()
        assert all(r["type"] == "pr" for r in data["results"])


class TestPrTicketCacheSwallowsException:
    """`_pr_ticket` (inner helper of `_search_prs`) caches the parent
    task's ticket. If `get_task` raises mid-walk we cache None so the
    next PR with the same parent doesn't retry forever."""

    def test_get_task_exception_caches_none(
        self, patched_server, monkeypatch,
    ):
        from common import search as core_search

        def boom(*a, **kw):
            raise RuntimeError("db read corrupted")
        monkeypatch.setattr(
            patched_server._db, "get_task", boom,
        )
        # Run a ticket-filtered search (forces _pr_ticket lookup); the
        # exception must NOT propagate. PR rows just look like they
        # have no ticket -> excluded by the filter.
        out = core_search.search("ticket:DOESNT-MATTER")
        assert isinstance(out, list)
