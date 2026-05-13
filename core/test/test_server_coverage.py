"""Tests targeting uncovered areas of server.py.

Covers: _poll_github_notifications, _on_gh_notification, _on_gh_pr_status_update,
build_background, emit_event persistence, session open/management,
live-stats, workstats, _build_gh_events edge cases.
"""

import json
import sqlite3
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server as _srv
from server import (
    _build_gh_events,
    build_background,
    emit_event,
    on_event,
    is_repo_allowed,
    _update_task_from_notification,
)


# ---------------------------------------------------------------------------
# _poll_github_notifications
# ---------------------------------------------------------------------------

class TestPollGithubNotifications:
    """Cover lines ~730-830 in server.py."""

    def _make_gh_line(
        self,
        nid="n1",
        reason="review_requested",
        subtype="PullRequest",
        title="Review this",
        repo="example/repo",
        updated="2026-04-12T00:00:00Z",
        unread="true",
        subject_url="https://api.github.com/repos/example/repo/pulls/555",
    ):
        return "\t".join([nid, reason, subtype, title, repo, updated, unread, subject_url])

    @pytest.fixture(autouse=True)
    def _reset_poll_state(self):
        """Ensure a clean polling state before each test."""
        original_ts = _srv._gh_last_poll["ts"]
        original_seen = _srv._gh_last_poll["seen_ids"].copy()
        _srv._gh_last_poll["ts"] = 0
        _srv._gh_last_poll["seen_ids"] = {}
        yield
        _srv._gh_last_poll["ts"] = original_ts
        _srv._gh_last_poll["seen_ids"] = original_seen

    def test_poll_emits_events_for_allowed_repo(self, patched_server):
        """Polling with a valid notification line emits a github.* event."""
        line = self._make_gh_line()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = line
        mock_result.stderr = ""

        emitted = []
        original_emit = _srv.emit_event
        def capture_emit(etype, data):
            emitted.append((etype, data))
        with patch("server.gh_run", return_value=mock_result), \
             patch("server.emit_event", side_effect=capture_emit):
            _srv._poll_github_notifications()

        assert len(emitted) > 0
        assert any("review_requested" in e[0] for e in emitted)

    def test_poll_skips_disallowed_repo(self, patched_server):
        """Notifications from repos not in the whitelist are dropped."""
        line = self._make_gh_line(repo="random-org/random-repo")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = line
        mock_result.stderr = ""

        emitted = []
        def capture_emit(etype, data):
            emitted.append((etype, data))
        with patch("server.gh_run", return_value=mock_result), \
             patch("server.emit_event", side_effect=capture_emit):
            _srv._poll_github_notifications()

        assert len(emitted) == 0

    def test_poll_dedup_by_seen_key(self, patched_server):
        """Same notification (id+updated_at) is not emitted twice."""
        line = self._make_gh_line(nid="dup1", updated="2026-04-12T01:00:00Z")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = line
        mock_result.stderr = ""

        emitted = []
        def capture_emit(etype, data):
            emitted.append((etype, data))

        with patch("server.gh_run", return_value=mock_result), \
             patch("server.emit_event", side_effect=capture_emit):
            _srv._poll_github_notifications()
            count_first = len(emitted)
            # Reset ts so interval check passes
            _srv._gh_last_poll["ts"] = 0
            _srv._poll_github_notifications()
            count_second = len(emitted)

        # Second poll should not produce new events for same id+updated
        assert count_second == count_first

    def test_poll_gh_returncode_nonzero_continues(self, patched_server):
        """Non-zero gh return code is logged but doesn't raise."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "auth error"

        with patch("server.gh_run", return_value=mock_result):
            _srv._poll_github_notifications()
            # Non-zero return code means no events processed

    def test_poll_ci_activity_skips_master_branch(self, patched_server):
        """ci_activity notifications for master/main branches are skipped."""
        line = self._make_gh_line(
            reason="ci_activity",
            title="CI for master branch",
            subject_url="",
            repo="example/repo",
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = line
        mock_result.stderr = ""

        emitted = []
        def capture_emit(etype, data):
            emitted.append((etype, data))

        with patch("server.gh_run", return_value=mock_result), \
             patch("server.emit_event", side_effect=capture_emit):
            _srv._poll_github_notifications()

        assert len(emitted) == 0

    def test_poll_ci_activity_branch_lookup_in_cache(self, patched_server):
        """ci_activity with a branch name looks up PR number from cache."""
        line = self._make_gh_line(
            reason="ci_activity",
            title="CI for feature-branch branch",
            subject_url="",
            repo="example/repo",
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = line
        mock_result.stderr = ""

        emitted = []
        def capture_emit(etype, data):
            emitted.append((etype, data))

        with patch("server.gh_run", return_value=mock_result), \
             patch("server.emit_event", side_effect=capture_emit), \
             patch("routes.events._lookup_pr_by_branch", return_value=777):
            _srv._poll_github_notifications()

        # The emitted event should have pr_number resolved from prs table lookup
        assert len(emitted) > 0

    def test_poll_short_line_skipped(self, patched_server):
        """Lines with fewer than 7 tab-separated fields are skipped."""
        short_line = "id1\treason\ttype"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = short_line
        mock_result.stderr = ""

        emitted = []
        def capture_emit(etype, data):
            emitted.append((etype, data))

        with patch("server.gh_run", return_value=mock_result), \
             patch("server.emit_event", side_effect=capture_emit):
            _srv._poll_github_notifications()

        assert len(emitted) == 0

    def test_poll_respects_interval(self, patched_server):
        """_poll_github_notifications returns early if called within interval."""
        _srv._gh_last_poll["ts"] = time.time()  # just polled

        with patch("server.gh_run") as mock_gh:
            _srv._poll_github_notifications()
            mock_gh.assert_not_called()

    def test_poll_exception_in_gh_run_handled(self, patched_server):
        """Exception from gh_run is caught and polling continues."""
        with patch("server.gh_run", side_effect=Exception("network error")):
            _srv._poll_github_notifications()
            # Exception is caught, no crash


# ---------------------------------------------------------------------------
# _on_gh_pr_status_update -- remaining uncovered branches
# ---------------------------------------------------------------------------

class TestOnGhPrStatusUpdateExtended:
    """Cover lines ~1196-1210: review_requested, comment, pending CI."""

    def test_review_requested_sets_review_status(self, patched_server):
        """review_requested sets review_status when it was previously empty."""
        notification = {
            "type": "github.review_requested",
            "message": "Review requested",
            "url": "https://github.com/example/repo/pull/200",
        }
        # Ensure review_status is empty initially
        pr = patched_server._db.find_pr_by_number(200)
        assert not pr.get("review_status")

        _srv._on_gh_pr_status_update(notification)

        pr = patched_server._db.find_pr_by_number(200)
        assert pr["review_status"] == "review_requested"

    def test_comment_increments_count(self, patched_server):
        """comment notification increments comment_count."""
        notification = {
            "type": "github.comment",
            "message": "Someone commented on this PR",
            "url": "https://github.com/example/repo/pull/200",
        }
        pr_before = patched_server._db.find_pr_by_number(200)
        count_before = pr_before.get("comment_count") or 0

        _srv._on_gh_pr_status_update(notification)

        pr_after = patched_server._db.find_pr_by_number(200)
        assert (pr_after.get("comment_count") or 0) == count_before + 1

    def test_ci_pending_sets_pending(self, patched_server):
        """ci_activity with 'pending' updates ci_status to pending."""
        notification = {
            "type": "github.ci_activity",
            "message": "Build is pending",
            "url": "https://github.com/example/repo/pull/200",
        }
        _srv._on_gh_pr_status_update(notification)

        pr = patched_server._db.find_pr_by_number(200)
        assert pr["ci_status"] == "pending"

    def test_state_change_closed(self, patched_server):
        """state_change with 'closed' sets PR status to closed.

        example/repo PRs CLOSED without merge flow through is_externally_merged
        which shells out to `gh` to check issue events. We set patched_server
        .gh_run so server.__setattr__ propagates to app_state.gh_run (the
        name the resolve code reads); the mock returns 'no merge event'
        (empty list) so resolved status is deterministically 'closed'."""
        def fake_gh_run(args, repo="", timeout=20):
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = "[]"
            mock.stderr = ""
            return mock

        orig_gh_run = patched_server.gh_run
        patched_server.gh_run = fake_gh_run
        try:
            notification = {
                "type": "github.state_change",
                "message": "PR was closed without merge",
                "url": "https://github.com/example/repo/pull/200",
            }
            _srv._on_gh_pr_status_update(notification)
            pr = patched_server._db.find_pr_by_number(200)
            assert pr["status"] == "closed"
        finally:
            patched_server.gh_run = orig_gh_run

    def test_state_change_reopened(self, patched_server):
        """state_change with 'reopened' sets PR status to open."""
        # First close it
        _srv._on_gh_pr_status_update({
            "type": "github.state_change",
            "message": "PR was closed",
            "url": "https://github.com/example/repo/pull/200",
        })
        # Then reopen
        _srv._on_gh_pr_status_update({
            "type": "github.state_change",
            "message": "PR was reopened",
            "url": "https://github.com/example/repo/pull/200",
        })

        pr = patched_server._db.find_pr_by_number(200)
        assert pr["status"] == "open"

    def test_no_changes_when_unknown_type(self, patched_server):
        """Unknown notification type produces no DB update."""
        notification = {
            "type": "github.some_unknown",
            "message": "unknown event",
            "url": "https://github.com/example/repo/pull/200",
        }
        with patch.object(patched_server._db, "update_pr_by_number") as mock_update:
            _srv._on_gh_pr_status_update(notification)
            mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# emit_event -- persistence and listener dispatch
# ---------------------------------------------------------------------------

class TestEmitEventPersistence:
    """Cover emit_event DB persistence branch and SSE subscriber push."""

    def test_emit_event_persists_to_db(self, patched_server):
        """emit_event INSERTs a row into the events DB."""
        emit_event("test.persist_event", {
            "title": "Persistence test",
            "message": "Should be in DB",
            "severity": "info",
            "source_id": "src-1",
        })

        with sqlite3.connect(str(_srv._NOTIF_DB_PATH)) as conn:
            row = conn.execute("SELECT * FROM events WHERE source = 'test'").fetchone()
        assert row is not None

    def test_emit_event_persists_session_for_non_github_events(self, patched_server):
        """Regression: a bug in the non-github INSERT branch dropped
        the `session` column for all agent.* / task.* / cron.* rows.
        Symptom: 5065 agent rows in the live DB all had session=''
        which broke any session-paired latency analysis. The github
        branch had always persisted session correctly; this test
        locks the contract on the OTHER branch too."""
        emit_event("agent.task_done", {
            "title": "done", "message": "",
            "session": "ticket-internal-EX-1004",
            "source_id": "src-agent-1",
        })
        with sqlite3.connect(str(_srv._NOTIF_DB_PATH)) as conn:
            row = conn.execute(
                "SELECT session FROM events WHERE source_id = ?",
                ("src-agent-1",),
            ).fetchone()
        assert row is not None
        assert row[0] == "ticket-internal-EX-1004"

    def test_emit_event_coerces_invalid_severity_to_info(self, patched_server):
        """Memo item #3 contract: any value of severity outside
        (info, warning, error) silently coerces to info so a sloppy
        emitter can't poison the events DB with a custom severity
        token. Live audit on the prod DB shows 0 'unknown' severities,
        confirming the contract holds; this test locks it going forward."""
        emit_event("test.bad_sev", {
            "title": "bad", "message": "",
            "severity": "WHATEVER",   # not in the allow-list
            "source_id": "src-bad-sev",
        })
        with sqlite3.connect(str(_srv._NOTIF_DB_PATH)) as conn:
            row = conn.execute(
                "SELECT severity FROM events WHERE source_id = ?",
                ("src-bad-sev",),
            ).fetchone()
        assert row is not None
        assert row[0] == "info"  # coerced

    def test_emit_event_accepts_warning_and_error_severities(self, patched_server):
        """The other two valid severities pass through unchanged."""
        emit_event("test.warn", {
            "severity": "warning", "source_id": "src-warn",
        })
        emit_event("test.err", {
            "severity": "error", "source_id": "src-err",
        })
        with sqlite3.connect(str(_srv._NOTIF_DB_PATH)) as conn:
            r1 = conn.execute(
                "SELECT severity FROM events WHERE source_id = 'src-warn'"
            ).fetchone()
            r2 = conn.execute(
                "SELECT severity FROM events WHERE source_id = 'src-err'"
            ).fetchone()
        assert r1[0] == "warning"
        assert r2[0] == "error"

    def test_emit_event_pushes_to_sse_subscribers(self, patched_server, tmp_path):
        """emit_event pushes to all SSE subscriber queues."""
        import asyncio
        q = asyncio.Queue(maxsize=100)
        _srv._event_subscribers.append(q)
        try:
            emit_event("test.sse_push", {
                "title": "SSE test",
                "message": "hello",
            })
            assert not q.empty()
            item = q.get_nowait()
            assert item["type"] == "test.sse_push"
        finally:
            if q in _srv._event_subscribers:
                _srv._event_subscribers.remove(q)

    def test_emit_event_removes_dead_subscribers(self, patched_server):
        """emit_event removes subscribers whose queue is full."""
        import asyncio
        full_q = asyncio.Queue(maxsize=1)
        full_q.put_nowait({"dummy": True})  # fill it
        _srv._event_subscribers.append(full_q)

        try:
            emit_event("test.overflow", {
                "title": "Overflow",
                "message": "queue full",
            })
            # The dead queue should have been removed
            assert full_q not in _srv._event_subscribers
        finally:
            if full_q in _srv._event_subscribers:
                _srv._event_subscribers.remove(full_q)

    def test_emit_event_generates_ts_when_missing(self, patched_server):
        """When data has no 'ts' key, emit_event generates one."""
        emit_event("test.auto_ts", {
            "title": "Auto TS",
            "message": "no ts provided",
        })

        with sqlite3.connect(str(_srv._NOTIF_DB_PATH)) as conn:
            row = conn.execute("SELECT ts FROM events").fetchone()
        assert row is not None
        assert row[0]  # ts is non-empty
        assert "T" in row[0]  # ISO-ish format


# ---------------------------------------------------------------------------
# build_background
# ---------------------------------------------------------------------------

class TestBuildBackground:
    """Cover build_background lines ~2214-2284."""

    def _make_task(self, **overrides):
        base = {
            "task_id": "my-task",
            "description": "Implement feature X",
            "status": "in_progress",
            "project": "test-proj",
            "dependencies": [],
            "prs": [],
        }
        base.update(overrides)
        return base

    def test_basic_output(self):
        """build_background produces [Background] and [Action] sections."""
        task = self._make_task()
        result = build_background(task, "Test Project", "Do the thing", {})
        assert "[Background]" in result
        assert "[Action]" in result
        assert "Do the thing" in result
        assert "my-task" in result
        assert "Test Project" in result

    def test_empty_prompt_no_action_section(self):
        """When prompt_template is empty, [Action] section is omitted."""
        task = self._make_task()
        result = build_background(task, "Test Project", "", {})
        assert "[Background]" in result
        assert "[Action]" not in result
        assert "[Post-Action]" not in result

    def test_with_ticket(self):
        """Ticket info appears when task has ticket_id."""
        task = self._make_task(
            ticket_id="EX-123",
            ticket_url="https://issues.example.org/jira/browse/EX-123",
        )
        result = build_background(task, "Test Project", "Do it", {})
        assert "EX-123" in result
        assert "issues.example.org" in result

    def test_with_ticket_no_url(self):
        """Ticket without URL still appears."""
        task = self._make_task(ticket_id="EX-999")
        result = build_background(task, "Test Project", "Do it", {})
        assert "EX-999" in result

    def test_with_dependencies(self):
        """Dependencies and their statuses appear."""
        task = self._make_task(dependencies=["dep-a", "dep-b"])
        dep_statuses = {"dep-a": "done", "dep-b": "in_progress"}
        result = build_background(task, "Test Project", "Do it", dep_statuses)
        assert "dep-a(done)" in result
        assert "dep-b(in_progress)" in result

    def test_with_prs(self):
        """PR info appears in the background block."""
        task = self._make_task(prs=[
            {"number": 100, "url": "https://github.com/example/repo/pull/100", "status": "merged", "title": "PR one"},
            {"number": 200, "url": "https://github.com/example/repo/pull/200", "status": "open", "title": "PR two"},
        ])
        result = build_background(task, "Test Project", "Do it", {})
        assert "pull/100" in result
        assert "[merged]" in result
        assert "pull/200" in result

    def test_with_pr_context_matched(self):
        """PR context adds detail section when PR matches a task PR."""
        prs = [
            {"number": 200, "url": "https://github.com/example/repo/pull/200",
             "status": "open", "title": "My PR", "head_branch": "feature-x",
             "ci_status": "success", "review_status": "approved"},
        ]
        task = self._make_task(prs=prs)
        pr_context = {"number": 200, "repo": "example/repo"}
        result = build_background(task, "Test Project", "Fix CI", {}, pr_context=pr_context)
        assert "Focus PR #200:" in result
        assert "feature-x" in result

    def test_with_pr_context_unmatched(self):
        """PR context with non-matching number does not add detail section."""
        task = self._make_task(prs=[
            {"number": 100, "url": "https://github.com/example/repo/pull/100", "status": "open", "title": "PR"},
        ])
        pr_context = {"number": 999, "repo": "example/repo"}
        result = build_background(task, "Test Project", "Fix CI", {}, pr_context=pr_context)
        assert "Focus PR" not in result

    def test_pr_without_url_gets_fallback(self):
        """PRs with no url get a fallback URL."""
        task = self._make_task(prs=[
            {"number": 42, "url": "", "status": "open", "title": "No URL PR"},
        ])
        result = build_background(task, "Test Project", "Do it", {})
        assert "unknown/pull/42" in result

    def test_tools_section_always_present(self):
        """[Tools] section is always emitted."""
        task = self._make_task()
        result = build_background(task, "Test Project", "Do it", {})
        assert "[Tools]" in result
        assert "eva-cli" in result

    def test_history_instructions_present(self):
        """[History] section tells the agent to use append-history for the
        append-only per-step timeline (replaced the old mutable [Progress]
        notes instruction)."""
        task = self._make_task()
        result = build_background(task, "Test Project", "Do it", {})
        assert "[History]" in result
        assert "append-history" in result


# ---------------------------------------------------------------------------
# Session open/management (lines ~2300-2390)
# ---------------------------------------------------------------------------

class TestSessionManagement:
    """Cover open_session endpoint with the new --append-system-prompt flow."""

    @staticmethod
    def _bg_arg(mock_tmux):
        argv = mock_tmux["launch_argv"].call_args.args[2]
        return argv[argv.index("--append-system-prompt") + 1]

    def test_open_session_new(self, client, patched_server, mock_tmux):
        """Opening for a new task creates a session and launches agent with bg."""
        resp = client.post("/api/sessions/open", json={
            "task_id": "task-b",
            "project_id": "test-proj",
            "action_id": "open",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["new"] is True
        # Background no longer travels through `prompt`; it's in the launched argv
        assert "[Background]" not in (data["prompt"] or "")
        assert "[Background]" in self._bg_arg(mock_tmux)

    def test_open_session_existing_tmux_dead_relaunches(
            self, client, patched_server, mock_tmux):
        """Existing session w/ dead tmux: relaunches agent with fresh bg in argv."""
        patched_server._db.create_session("task-b", "test-proj", "task-b")
        mock_tmux["exists"].return_value = False

        resp = client.post("/api/sessions/open", json={
            "task_id": "task-b",
            "project_id": "test-proj",
            "action_id": "open",
        })
        assert resp.status_code == 200
        assert resp.json()["new"] is False
        assert "[Background]" in self._bg_arg(mock_tmux)

    def test_open_session_existing_tmux_alive_skips_relaunch(
            self, client, patched_server, mock_tmux):
        """Existing session + tmux alive: just returns action prompt, no relaunch."""
        patched_server._db.create_session("task-b", "test-proj", "task-b")
        mock_tmux["exists"].return_value = True

        resp = client.post("/api/sessions/open", json={
            "task_id": "task-b",
            "project_id": "test-proj",
            "action_id": "open",
        })
        assert resp.status_code == 200
        assert resp.json()["new"] is False
        mock_tmux["launch_argv"].assert_not_called()

    def test_open_session_bad_action(self, client, patched_server, mock_tmux):
        """Opening a session with invalid action_id returns 404."""
        resp = client.post("/api/sessions/open", json={
            "task_id": "task-b",
            "project_id": "test-proj",
            "action_id": "nonexistent-action",
        })
        assert resp.status_code == 404

    def test_open_session_bad_task(self, client, patched_server, mock_tmux):
        """Opening a session with invalid task_id returns 404."""
        resp = client.post("/api/sessions/open", json={
            "task_id": "nonexistent-task",
            "project_id": "test-proj",
            "action_id": "open",
        })
        assert resp.status_code == 404

    def test_open_session_with_custom_prompt(self, client, patched_server, mock_tmux):
        """custom_prompt overrides the action's prompt_template."""
        resp = client.post("/api/sessions/open", json={
            "task_id": "task-b",
            "project_id": "test-proj",
            "action_id": "open",
            "custom_prompt": "Custom instruction here",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "Custom instruction here" in data["prompt"]

    def test_open_session_with_pr_context(self, client, patched_server, mock_tmux):
        """PR context is included in injected background when pr_number+repo provided."""
        resp = client.post("/api/sessions/open", json={
            "task_id": "task-b",
            "project_id": "test-proj",
            "action_id": "open",
            "pr_number": 200,
            "pr_repo": "example/repo",
        })
        assert resp.status_code == 200
        # PR focus section appears in the system-prompt argv since task-b has PR #200
        assert "Focus PR #200:" in self._bg_arg(mock_tmux)


# ---------------------------------------------------------------------------
# Live stats (lines ~1220-1310)
# ---------------------------------------------------------------------------

class TestLiveStats:
    """Cover _fetch_live_stats and get_live_stats endpoint."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        original = _srv._live_stats_cache.copy()
        _srv._live_stats_cache["data"] = None
        _srv._live_stats_cache["ts"] = 0
        yield
        _srv._live_stats_cache.update(original)

    def test_fetch_live_stats_open_prs(self):
        """_fetch_live_stats counts open PRs from mocked gh output."""
        def mock_gh_run(args, repo="", timeout=20):
            result = MagicMock()
            if "search" in args:
                result.returncode = 0
                result.stdout = json.dumps([{"number": 1}, {"number": 2}])
            elif "contributors" in " ".join(args):
                result.returncode = 1
                result.stdout = ""
            else:
                result.returncode = 1
                result.stdout = ""
            result.stderr = ""
            return result

        with patch("server.gh_run", side_effect=mock_gh_run):
            stats = _srv._fetch_live_stats()

        assert stats["open_prs"]["total"] >= 2

    def test_fetch_live_stats_contributor_rank(self):
        """_fetch_live_stats finds contributor rank when present."""
        joined_args_log = []

        def mock_gh_run(args, repo="", timeout=20):
            result = MagicMock()
            result.stderr = ""
            args_str = " ".join(str(a) for a in args)
            joined_args_log.append(args_str)
            if "search" in args:
                result.returncode = 0
                result.stdout = "[]"
            elif "contributors?per_page=100" in args_str:
                result.returncode = 0
                result.stdout = "alice 500\ntest-author 123\nbob 50\n"
            elif "contributors?per_page=1" in args_str:
                result.returncode = 0
                result.stdout = 'link: <...page=500>; rel="last"'
            else:
                result.returncode = 1
                result.stdout = ""
            return result

        with patch("server.gh_run", side_effect=mock_gh_run):
            stats = _srv._fetch_live_stats()

        assert stats["contributor_rank"] == 2
        assert stats["contributor_contributions"] == 123

    def test_get_live_stats_endpoint_returns_fallback(self, client, patched_server):
        """First call to live-stats does a sync fetch (mocked)."""
        def mock_gh_run(args, repo="", timeout=20):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "[]"
            result.stderr = ""
            return result

        with patch("server.gh_run", side_effect=mock_gh_run):
            resp = client.get("/api/live-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "open_prs" in data

    def test_get_live_stats_cached(self, client, patched_server):
        """Second call within 2 min returns cached data, no gh call."""
        _srv._live_stats_cache["data"] = {"open_prs": {"total": 5}, "contributor_rank": 10}
        _srv._live_stats_cache["ts"] = time.time()

        with patch("server.gh_run") as mock_gh:
            resp = client.get("/api/live-stats")
            mock_gh.assert_not_called()
        assert resp.status_code == 200
        assert resp.json()["open_prs"]["total"] == 5

    def test_get_live_stats_stale_triggers_bg_refresh(self, client, patched_server):
        """Stale cache triggers a background thread refresh."""
        _srv._live_stats_cache["data"] = {"open_prs": {"total": 1}}
        _srv._live_stats_cache["ts"] = time.time() - 300  # 5 min ago (stale)

        threads_before = threading.active_count()
        with patch("server._fetch_live_stats", return_value={"open_prs": {"total": 99}}) as mock_fetch:
            resp = client.get("/api/live-stats")
        # Should return stale data immediately
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Workstats (lines ~1310-1418)
# ---------------------------------------------------------------------------

class TestWorkstats:
    """Cover get_workstats and _compute_workstats."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        original = _srv._workstats_cache.copy()
        _srv._workstats_cache["data"] = None
        _srv._workstats_cache["ts"] = 0
        yield
        _srv._workstats_cache.update(original)

    def test_workstats_returns_data_on_first_call(self, client, patched_server):
        """First call returns live data from DB with quarters key."""
        resp = client.get("/api/workstats")
        assert resp.status_code == 200
        data = resp.json()
        assert "quarters" in data

    def test_workstats_cached(self, client, patched_server):
        """Cached data within 5 min returns without recompute."""
        cached = {
            "quarters": [{"period": "Q1", "total": 10}],
            "all_time": {"total": 10},
            "weekly": [1, 2, 3],
        }
        _srv._workstats_cache["data"] = cached
        _srv._workstats_cache["ts"] = time.time()

        with patch("common.system._compute_workstats") as mock_compute:
            resp = client.get("/api/workstats")
            mock_compute.assert_not_called()
        assert resp.status_code == 200
        assert resp.json()["quarters"][0]["period"] == "Q1"

    def test_compute_workstats_success(self, patched_server):
        """_compute_workstats returns quarterly data from merged PRs in DB."""
        from common import system as system_mod
        # Insert some merged PRs with last_updated timestamps. Author
        # MUST match a configured gh login -- workstats now strictly
        # filters out non-self PRs (review-watch / bots).
        db = patched_server._db
        db.add_pr(
            project="test-proj", task_id="task-a", number=301,
            url="https://github.com/example/repo/pull/301",
            status="merged", last_updated="2026-03-15T10:00:00Z",
            author="test-author",
        )
        db.add_pr(
            project="test-proj", task_id="task-a", number=302,
            url="https://github.com/myorg/svc/pull/302",
            status="merged", last_updated="2026-06-01T10:00:00Z",
            author="test-author_data",
        )

        result = system_mod._compute_workstats()
        assert result is not None
        assert "quarters" in result
        assert "weekly" in result
        assert "all_time" in result
        assert result["all_time"]["total"] >= 2

    def test_compute_workstats_empty_db(self, patched_server):
        """_compute_workstats returns empty quarters when DB has no merged PRs."""
        from common import system as system_mod
        # The patched_server fixture has PRs but only #100 is merged (with
        # empty last_updated), so _compute_workstats filters them out.
        # Clear all PRs to ensure empty result.
        patched_server._db._conn.execute(
            "DELETE FROM prs WHERE status IN ('merged', 'closed')"
        )
        patched_server._db._conn.commit()

        result = system_mod._compute_workstats()
        assert result is not None
        assert result["quarters"] == []
        assert result["all_time"]["total"] == 0

    def test_compute_workstats_exception(self, patched_server):
        """_compute_workstats returns None when DB access fails."""
        from common import system as system_mod
        import app_state
        broken_db = MagicMock()
        broken_db._conn.execute.side_effect = Exception("boom")
        with patch.object(app_state, "_db", broken_db):
            result = system_mod._compute_workstats()
        assert result is None


# ---------------------------------------------------------------------------
# _build_gh_events -- additional edge cases
# ---------------------------------------------------------------------------

class TestBuildGhEventsExtended:
    """Supplementary coverage for _build_gh_events."""

    def test_event_with_no_repo(self):
        """Event with missing 'repo' key produces empty repo portion in title."""
        events = [{
            "id": "x1",
            "type": "comment",
            "repo": None,
            "pr_number": 10,
            "label": "Comment",
            "title": "Some comment",
        }]
        result = _build_gh_events(events)
        assert len(result) == 1
        # URL cannot be built without repo
        assert result[0]["url"] is None

    def test_event_with_fork_repo_resolves_upstream(self):
        """Event from a fork repo resolves to upstream URL."""
        events = [{
            "id": "x2",
            "type": "author",
            "repo": "test-author/repo",
            "pr_number": 42,
            "label": "PR update",
            "title": "Updated",
            "updated": "2026-04-12T01:00:00Z",
        }]
        result = _build_gh_events(events)
        assert len(result) == 1
        assert "example/repo/pull/42" in result[0]["url"]

    def test_event_with_updated_field_passthrough(self):
        """The 'updated' field is passed through as 'ts'."""
        events = [{
            "id": "x3",
            "type": "subscribed",
            "repo": "example/repo",
            "pr_number": None,
            "label": "Subscribed",
            "title": "You subscribed",
            "updated": "2026-01-01T00:00:00Z",
        }]
        result = _build_gh_events(events)
        assert result[0]["ts"] == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# _load_seen_ids (reads from unified events table)
# ---------------------------------------------------------------------------

class TestLoadSeenIds:
    """Cover _load_seen_ids reading from the events table."""

    def test_load_seen_ids_from_events_table(self):
        """_load_seen_ids returns {source_id: ts} from github events in events table."""
        # emit_event writes to the events table
        emit_event("github.comment", {
            "title": "Test",
            "message": "hello",
            "source_id": "notif-123",
            "ts": "2026-04-12T00:00:00Z",
        })

        seen = _srv._load_seen_ids()
        assert "notif-123" in seen
        assert seen["notif-123"] == "2026-04-12T00:00:00Z"

    def test_load_seen_ids_ignores_non_github(self):
        """_load_seen_ids only returns github source events."""
        emit_event("agent.done", {
            "title": "Agent done",
            "source_id": "agent-456",
        })

        seen = _srv._load_seen_ids()
        assert "agent-456" not in seen


# ---------------------------------------------------------------------------
# is_repo_allowed edge cases
# ---------------------------------------------------------------------------

class TestIsRepoAllowed:

    def test_empty_string(self):
        assert is_repo_allowed("") is False

    def test_none(self):
        assert is_repo_allowed(None) is False

    def test_allowed_org_repo(self):
        assert is_repo_allowed("myorg/svc") is True

    def test_allowed_specific_repo(self):
        assert is_repo_allowed("example/repo") is True

    def test_fork_mapped_to_allowed(self):
        assert is_repo_allowed("test-author/repo") is True

    def test_random_repo(self):
        assert is_repo_allowed("random-org/random-repo") is False


# ---------------------------------------------------------------------------
# _parse_pr_number
# ---------------------------------------------------------------------------

class TestParsePrNumber:

    def test_valid_url(self):
        assert _srv._parse_pr_number("https://github.com/example/repo/pull/123") == 123

    def test_url_with_trailing_slash(self):
        assert _srv._parse_pr_number("https://github.com/example/repo/pull/456/") == 456

    def test_url_with_query_params(self):
        assert _srv._parse_pr_number("https://github.com/example/repo/pull/789?diff=unified") == 789

    def test_no_pull(self):
        assert _srv._parse_pr_number("https://github.com/example/repo/issues/123") is None

    def test_empty(self):
        assert _srv._parse_pr_number("") is None

    def test_none(self):
        assert _srv._parse_pr_number(None) is None


# ---------------------------------------------------------------------------
# _lookup_pr_by_branch
# ---------------------------------------------------------------------------

class TestLookupPrByBranch:
    """Cover _lookup_pr_by_branch: branch+repo lookup, fork resolution, fallback."""

    def _add_test_pr(self, db, number, branch, url, status="open"):
        """Helper: insert a PR with given head_branch and url into the temp DB."""
        db.add_pr(
            project="test-proj",
            task_id="task-b",
            number=number,
            url=url,
            status=status,
            head_branch=branch,
        )

    def test_fork_repo_resolves_to_upstream(self, patched_server):
        """test-author/repo resolves to example/repo via FORK_TO_UPSTREAM."""
        self._add_test_pr(
            patched_server._db,
            number=500,
            branch="feature-x",
            url="https://github.com/example/repo/pull/500",
        )
        result = _srv._lookup_pr_by_branch("feature-x", repo="test-author/repo")
        assert result == 500

    def test_upstream_repo_directly(self, patched_server):
        """Passing upstream repo directly (example/repo) also matches."""
        self._add_test_pr(
            patched_server._db,
            number=501,
            branch="feature-y",
            url="https://github.com/example/repo/pull/501",
        )
        result = _srv._lookup_pr_by_branch("feature-y", repo="example/repo")
        assert result == 501

    def test_no_repo_fallback(self, patched_server):
        """When repo is None, falls back to branch-only lookup."""
        self._add_test_pr(
            patched_server._db,
            number=502,
            branch="feature-z",
            url="https://github.com/example/repo/pull/502",
        )
        result = _srv._lookup_pr_by_branch("feature-z")
        assert result == 502

    def test_branch_not_found_returns_none(self, patched_server):
        """Non-existent branch returns None."""
        result = _srv._lookup_pr_by_branch("no-such-branch", repo="example/repo")
        assert result is None

    def test_only_open_prs_match(self, patched_server):
        """Merged PRs are not returned."""
        self._add_test_pr(
            patched_server._db,
            number=503,
            branch="merged-branch",
            url="https://github.com/example/repo/pull/503",
            status="merged",
        )
        result = _srv._lookup_pr_by_branch("merged-branch", repo="example/repo")
        assert result is None

    def test_same_branch_different_repos_selects_correct(self, patched_server):
        """Two open PRs with same branch but different upstream URLs: repo disambiguates."""
        self._add_test_pr(
            patched_server._db,
            number=600,
            branch="shared-branch",
            url="https://github.com/example/repo/pull/600",
        )
        self._add_test_pr(
            patched_server._db,
            number=601,
            branch="shared-branch",
            url="https://github.com/myorg/svc/pull/601",
        )
        # Ask for the repo one via fork repo
        result = _srv._lookup_pr_by_branch("shared-branch", repo="test-author/repo")
        assert result == 600

        # Ask for the runtime one via fork repo
        result = _srv._lookup_pr_by_branch("shared-branch", repo="test-author_data/svc")
        assert result == 601

    def test_repo_mismatch_falls_back_to_branch_only(self, patched_server):
        """When repo URL does not match any PR, falls back to branch-only lookup."""
        self._add_test_pr(
            patched_server._db,
            number=700,
            branch="fallback-branch",
            url="https://github.com/example/repo/pull/700",
        )
        # Use a repo whose upstream does not match the URL
        result = _srv._lookup_pr_by_branch("fallback-branch", repo="some-org/other-repo")
        assert result == 700
