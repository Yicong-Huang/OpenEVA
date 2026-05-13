"""Tests for notification handlers and event bus in server.py."""

import sys
import time
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import (
    _update_task_from_notification,
    on_event,
    _build_gh_events,
)
import server as _server_module


# ---- _update_task_from_notification ----

class TestUpdateTaskFromNotification:

    def test_ci_failed_adds_note(self, patched_server):
        """CI activity with 'failed' message appends a note to the task."""
        db = patched_server._db
        task = db.get_task("test-proj", "task-b")
        old_notes = task.get("notes", "")

        with patch("server.save_task") as mock_save:
            _update_task_from_notification(
                "test-proj", "task-b", task,
                "github.ci_activity", "Build failed on PR #200",
            )
            assert mock_save.called
        # Note should contain the CI failure snippet
        assert "CI failed:" in task.get("notes", "")

    def test_ci_failed_not_duplicate(self, patched_server):
        """CI failure note is not added twice if already present."""
        db = patched_server._db
        task = db.get_task("test-proj", "task-b")
        # Pre-seed the note so it looks like it was already added
        ci_note = "CI failed: Build failed on PR #200"
        task["notes"] = ci_note

        with patch("server.save_task") as mock_save:
            _update_task_from_notification(
                "test-proj", "task-b", task,
                "github.ci_activity", "Build failed on PR #200",
            )
            assert not mock_save.called

    def test_review_requested_status_change(self, patched_server):
        """review_requested changes task status from not_started/in_progress to in_review."""
        db = patched_server._db
        # task-c is not_started
        task = db.get_task("test-proj", "task-c")
        assert task["status"] == "not_started"

        with patch("server.save_task") as mock_save:
            _update_task_from_notification(
                "test-proj", "task-c", task,
                "github.review_requested", "Review requested on PR",
            )
            assert mock_save.called
        assert task["status"] == "in_review"

    def test_review_requested_in_progress_status_change(self, patched_server):
        """review_requested also changes in_progress to in_review."""
        db = patched_server._db
        task = db.get_task("test-proj", "task-b")
        assert task["status"] == "in_progress"

        with patch("server.save_task") as mock_save:
            _update_task_from_notification(
                "test-proj", "task-b", task,
                "github.review_requested", "Review requested",
            )
            assert mock_save.called
        assert task["status"] == "in_review"

    def test_merged_all_prs_marks_done(self, patched_server):
        """state_change with 'merged' message and all PRs merged sets status=done."""
        db = patched_server._db
        # task-a already has PR #100 with status=merged (from conftest)
        task = db.get_task("test-proj", "task-a")
        # Override status from 'done' to something else to verify transition
        db.update_task("test-proj", "task-a", status="in_review")
        task = db.get_task("test-proj", "task-a")
        assert task["status"] == "in_review"
        # All PRs are already merged
        assert all(p.get("status") == "merged" for p in task.get("prs", []))

        with patch("server.save_task") as mock_save:
            _update_task_from_notification(
                "test-proj", "task-a", task,
                "github.state_change", "PR was merged into master",
            )
            assert mock_save.called
        assert task["status"] == "done"

    def test_merged_with_no_tracked_prs_is_no_op(self, patched_server):
        """Regression: a task with zero tracked PRs must NOT be flipped to
        'done' by a stray 'merged' notification -- `all([])` is True but the
        task never actually had a PR to merge."""
        db = patched_server._db
        # task-c in conftest has no PRs.
        task = db.get_task("test-proj", "task-c")
        assert task.get("prs", []) == []
        original_status = task["status"]

        with patch("server.save_task") as mock_save:
            _update_task_from_notification(
                "test-proj", "task-c", task,
                "github.state_change", "PR was merged into master",
            )
            assert not mock_save.called
        assert task["status"] == original_status

    def test_merged_not_all_prs_no_change(self, patched_server):
        """state_change with 'merged' but not all PRs merged -> status unchanged."""
        db = patched_server._db
        # task-b has PR #200 with status=open (from conftest)
        task = db.get_task("test-proj", "task-b")
        original_status = task["status"]
        # At least one PR is open, so not all merged
        assert any(p.get("status") != "merged" for p in task.get("prs", []))

        with patch("server.save_task") as mock_save:
            _update_task_from_notification(
                "test-proj", "task-b", task,
                "github.state_change", "PR was merged",
            )
            # save_task may or may not be called for other reasons but status unchanged
        assert task["status"] == original_status

    def test_unknown_ntype_no_change(self, patched_server):
        """Unknown notification type produces no change and no save_task call."""
        db = patched_server._db
        task = db.get_task("test-proj", "task-b")
        original_status = task["status"]
        original_notes = task.get("notes", "")

        with patch("server.save_task") as mock_save:
            _update_task_from_notification(
                "test-proj", "task-b", task,
                "github.unknown_event_xyz", "Some random message",
            )
            assert not mock_save.called
        assert task["status"] == original_status
        assert task.get("notes", "") == original_notes

    def test_review_requested_records_auto_history(self, patched_server):
        """A GitHub notification that flips task status must leave a
        `status: X -> Y` line in task_history -- same as the other five
        state-transition sites. Regression guard for the auto-history hook
        added 2026-04-24."""
        db = patched_server._db
        task = db.get_task("test-proj", "task-c")  # not_started
        _update_task_from_notification(
            "test-proj", "task-c", task,
            "github.review_requested", "Review requested",
        )
        texts = [e["text"] for e in db.list_task_history("test-proj", "task-c")]
        assert any("status:" in t and "-> in_review" in t for t in texts)

    def test_ci_failed_no_status_no_history(self, patched_server):
        """A CI-failed notification updates notes but never status --
        history must stay untouched so the timeline doesn't fill with
        phantom `status: X -> X` lines."""
        db = patched_server._db
        task = db.get_task("test-proj", "task-b")
        before = len(db.list_task_history("test-proj", "task-b"))
        _update_task_from_notification(
            "test-proj", "task-b", task,
            "github.ci_activity", "Build failed on CI",
        )
        after = db.list_task_history("test-proj", "task-b")
        # Status didn't change, so no new status line. Notes edits don't log.
        assert not any("status:" in e["text"] for e in after[: len(after) - before])


# ---- _on_gh_notification ----

class TestOnGhNotification:

    def test_on_gh_notification_with_pr_url(self, patched_server):
        """Notification with a known PR URL calls _update_task_from_notification."""
        # PR #200 exists in task-b (from conftest)
        notification = {
            "type": "github.review_requested",
            "message": "Review requested",
            "title": "PR review",
            "url": "https://github.com/example/repo/pull/200",
        }
        with patch("services.github_poller._update_task_from_notification") as mock_update:
            patched_server._on_gh_notification(notification)
            assert mock_update.called
            args = mock_update.call_args[0]
            assert args[0] == "test-proj"
            assert args[1] == "task-b"

    def test_on_gh_notification_no_pr_url(self, patched_server):
        """Notification without /pull/ in URL returns early without error."""
        notification = {
            "type": "github.ci_activity",
            "message": "Build passed",
            "title": "CI",
            "url": "https://github.com/example/repo/issues/999",
        }
        # Should not raise; no PR number extracted
        with patch("services.github_poller._update_task_from_notification") as mock_update:
            patched_server._on_gh_notification(notification)
            assert not mock_update.called

    def test_on_gh_notification_unknown_pr(self, patched_server):
        """Notification with a PR number not in DB returns silently."""
        notification = {
            "type": "github.review_requested",
            "message": "Review requested",
            "title": "Unknown PR",
            "url": "https://github.com/example/repo/pull/99999",
        }
        with patch("services.github_poller._update_task_from_notification") as mock_update:
            patched_server._on_gh_notification(notification)
            assert not mock_update.called

    def test_on_gh_notification_missing_url(self, patched_server):
        """Notification with no URL key returns early without error."""
        notification = {
            "type": "github.comment",
            "message": "Someone commented",
            "title": "Comment",
        }
        with patch("services.github_poller._update_task_from_notification") as mock_update:
            patched_server._on_gh_notification(notification)
            assert not mock_update.called


# ---- _on_gh_pr_status_update ----

class TestOnGhPrStatusUpdate:

    def test_pr_status_update_state_change_merged(self, patched_server):
        """state_change notification with 'merged' updates PR status to merged."""
        notification = {
            "type": "github.state_change",
            "message": "PR was merged into master",
            "url": "https://github.com/example/repo/pull/200",
        }
        patched_server._on_gh_pr_status_update(notification)

        pr = patched_server._db.find_pr_by_number(200)
        assert pr["status"] == "merged"
        assert pr["ci_status"] == "success"

    def test_pr_status_update_ci_activity_failed(self, patched_server):
        """ci_activity notification with 'failed' updates ci_status to failure."""
        notification = {
            "type": "github.ci_activity",
            "message": "Build failed: 3 tests failed",
            "url": "https://github.com/example/repo/pull/200",
        }
        patched_server._on_gh_pr_status_update(notification)

        pr = patched_server._db.find_pr_by_number(200)
        assert pr["ci_status"] == "failure"

    def test_pr_status_update_ci_activity_success(self, patched_server):
        """ci_activity notification with 'success' updates ci_status to success."""
        notification = {
            "type": "github.ci_activity",
            "message": "All checks passed successfully",
            "url": "https://github.com/example/repo/pull/200",
        }
        patched_server._on_gh_pr_status_update(notification)

        pr = patched_server._db.find_pr_by_number(200)
        assert pr["ci_status"] == "success"

    def test_pr_status_update_unknown_pr(self, patched_server):
        """Notification with unknown PR number does nothing and does not raise."""
        notification = {
            "type": "github.state_change",
            "message": "merged",
            "url": "https://github.com/example/repo/pull/88888",
        }
        # Should not raise
        patched_server._on_gh_pr_status_update(notification)

    def test_pr_status_update_no_pull_url(self, patched_server):
        """Notification without /pull/ in URL returns early without error."""
        notification = {
            "type": "github.state_change",
            "message": "merged",
            "url": "https://github.com/example/repo/issues/123",
        }
        with patch.object(patched_server._db, "update_pr_by_number") as mock_update:
            patched_server._on_gh_pr_status_update(notification)
            assert not mock_update.called


# ---- on_event / event bus ----

class TestOnEvent:

    def _wait_for(self, event, timeout=2.0):
        """Wait for threading.Event to be set, returning True if set in time."""
        return event.wait(timeout)

    def test_on_event_registers_callback(self):
        """Register a callback, emit matching event, verify callback is called."""
        called = threading.Event()
        received = []

        def handler(notification):
            received.append(notification)
            called.set()

        # Use a unique event name to avoid cross-test interference
        event_name = "test.specific_event_abc123"
        on_event(event_name, handler)

        try:
            with patch("server._notif_db") as mock_notif_db:
                mock_conn = MagicMock()
                mock_conn.execute.return_value.fetchone.return_value = None
                mock_notif_db.return_value = mock_conn

                _server_module.emit_event(event_name, {
                    "title": "Test event",
                    "message": "hello",
                    "severity": "info",
                })

            assert self._wait_for(called), "Callback was not called within timeout"
            assert len(received) == 1
            assert received[0]["type"] == event_name
        finally:
            # Clean up registered listener to avoid polluting other tests
            _server_module._event_listeners.pop(event_name, None)

    def test_on_event_wildcard(self):
        """Wildcard 'github.*' callback is triggered by 'github.review_requested'."""
        called = threading.Event()
        received = []

        def handler(notification):
            received.append(notification)
            called.set()

        wildcard_key = "github.__test_wildcard__"
        on_event(wildcard_key, handler)

        try:
            with patch("server._notif_db") as mock_notif_db:
                mock_conn = MagicMock()
                mock_conn.execute.return_value.fetchone.return_value = None
                mock_notif_db.return_value = mock_conn

                _server_module.emit_event("github.__test_wildcard__", {
                    "title": "Review",
                    "message": "Please review",
                    "severity": "warning",
                })

            assert self._wait_for(called), "Wildcard callback was not called within timeout"
            assert received[0]["type"] == "github.__test_wildcard__"
        finally:
            _server_module._event_listeners.pop(wildcard_key, None)

    def test_on_event_wildcard_prefix_match(self):
        """Wildcard 'github.*' listener is triggered by any 'github.<x>' event."""
        called = threading.Event()
        received = []

        def handler(notification):
            received.append(notification)
            called.set()

        # Register with wildcard pattern
        on_event("github.*", handler)

        try:
            with patch("server._notif_db") as mock_notif_db:
                mock_conn = MagicMock()
                mock_conn.execute.return_value.fetchone.return_value = None
                mock_notif_db.return_value = mock_conn

                _server_module.emit_event("github.review_requested", {
                    "title": "Review requested",
                    "message": "Please review",
                    "severity": "warning",
                })

            assert self._wait_for(called), "Wildcard prefix callback was not called"
            assert received[0]["type"] == "github.review_requested"
        finally:
            # Remove only the handler we added from the wildcard list
            listeners = _server_module._event_listeners.get("github.*", [])
            if handler in listeners:
                listeners.remove(handler)


# ---- _build_gh_events ----

class TestBuildGhEvents:
    """Tests use generic acme/widgets + alice/widgets in place of
    example/repo + test-author/repo so the assertions describe
    the fork-resolution mechanism, not the maintainer's specific
    repos. The autouse fixture below monkeypatches `FORK_TO_UPSTREAM`
    to register the generic pair for the duration of each test."""

    @pytest.fixture(autouse=True)
    def _generic_fork_map(self, monkeypatch):
        # `app_state.FORK_TO_UPSTREAM` is a re-export of
        # `adapters.github.FORK_TO_UPSTREAM` -- the same dict object.
        # Replace it on both bindings so production code and the
        # `from app_state import FORK_TO_UPSTREAM` shim agree.
        from adapters import github as gh
        new_map = {"alice/widgets": "acme/widgets"}
        monkeypatch.setattr(gh, "FORK_TO_UPSTREAM", new_map)
        import app_state
        monkeypatch.setattr(app_state, "FORK_TO_UPSTREAM", new_map)

    def test_build_gh_events_empty(self):
        """Empty input returns empty list."""
        assert _build_gh_events([]) == []

    def test_build_gh_events_review_requested(self):
        """review_requested event maps to warning severity and correct type."""
        events = [
            {
                "id": "evt-1",
                "type": "review_requested",
                "repo": "alice/widgets",
                "pr_number": 55301,
                "label": "Review requested",
                "title": "Please review this PR",
            }
        ]
        result = _build_gh_events(events)
        assert len(result) == 1
        notif = result[0]
        assert notif["severity"] == "warning"
        assert notif["_reason"] == "review_requested"
        assert "55301" in notif["title"] or "widgets" in notif["title"].lower()
        # URL should be resolved to upstream via FORK_TO_UPSTREAM.
        assert "acme/widgets" in notif["url"]
        assert "/pull/55301" in notif["url"]

    def test_build_gh_events_ci_activity(self):
        """ci_activity event maps to error severity."""
        events = [
            {
                "id": "evt-2",
                "type": "ci_activity",
                "repo": "alice/widgets",
                "pr_number": 12345,
                "label": "CI failed",
                "title": "Build failure on main",
            }
        ]
        result = _build_gh_events(events)
        assert len(result) == 1
        assert result[0]["severity"] == "error"

    def test_build_gh_events_state_change(self):
        """state_change event maps to info severity."""
        events = [
            {
                "id": "evt-3",
                "type": "state_change",
                "repo": "alice/widgets",
                "pr_number": 99,
                "label": "PR merged",
                "title": "Merged into master",
            }
        ]
        result = _build_gh_events(events)
        assert len(result) == 1
        assert result[0]["severity"] == "info"

    def test_build_gh_events_no_pr_number(self):
        """Event without pr_number has no URL and no PR tag in title."""
        events = [
            {
                "id": "evt-4",
                "type": "assign",
                "repo": "alice/widgets",
                "label": "Assigned",
                "title": "You were assigned",
            }
        ]
        result = _build_gh_events(events)
        assert len(result) == 1
        assert result[0]["url"] is None
        assert "#" not in result[0]["title"]

    def test_build_gh_events_multiple(self):
        """Multiple events produce multiple notifications in order."""
        events = [
            {
                "id": "a",
                "type": "comment",
                "repo": "alice/widgets",
                "pr_number": 1,
                "label": "Comment",
                "title": "Someone commented",
            },
            {
                "id": "b",
                "type": "mention",
                "repo": "alice/widgets",
                "pr_number": 2,
                "label": "Mention",
                "title": "You were mentioned",
            },
        ]
        result = _build_gh_events(events)
        assert len(result) == 2
        assert result[0]["_reason"] == "comment"
        assert result[1]["_reason"] == "mention"
        assert result[1]["severity"] == "warning"

    def test_build_gh_events_unknown_type_defaults_info(self):
        """Unknown event type defaults to info severity."""
        events = [
            {
                "id": "c",
                "type": "some_future_type",
                "repo": "alice/widgets",
                "pr_number": 42,
                "label": "Unknown",
                "title": "Some future event",
            }
        ]
        result = _build_gh_events(events)
        assert len(result) == 1
        assert result[0]["severity"] == "info"
