"""Edge case tests for API endpoints with low coverage:
- PR detail API: fork CI override for open repo PRs, StatusContext vs CheckRun
- GitHub poll status: debug endpoint response
- Close task: long reason, unicode reason, close task that has PRs

Plugin-specific edge cases live with the implementations.
"""

import json
import time
from datetime import date, timedelta
from io import BytesIO
from unittest.mock import patch, MagicMock, PropertyMock


# ====================================================================
# PR Detail API - fork CI and StatusContext vs CheckRun
# ====================================================================

class TestPrDetailEdgeCases:
    """Test PR detail endpoint edge cases."""

    @patch("pr_sync.fetch_fork_ci")
    @patch("server.gh_run")
    def test_open_oss_pr_gets_fork_ci_override(self, mock_gh_run, mock_fork_ci, client):
        """For open example/repo PRs, fork CI replaces statusCheckRollup."""
        call_count = [0]
        def gh_side_effect(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:  # pr view
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "number": 12345,
                        "title": "Test fork CI PR",
                        "state": "OPEN",
                        "headRefName": "my-feature-branch",
                        "statusCheckRollup": [
                            {"name": "old-check", "conclusion": "SUCCESS", "status": "COMPLETED"}
                        ],
                    }),
                )
            elif call_count[0] == 2:  # inline comments
                return MagicMock(returncode=0, stdout="[]")
            return MagicMock(returncode=1, stdout="")

        mock_gh_run.side_effect = gh_side_effect
        mock_fork_ci.return_value = [
            {"name": "Build / build-jdk17", "conclusion": "success", "status": "completed"},
            {"name": "Build / build-jdk21", "conclusion": "failure", "status": "completed"},
        ]

        resp = client.get("/api/pr-detail?repo=example/repo&number=12345")
        assert resp.status_code == 200
        data = resp.json()
        # The original "old-check" should be replaced by fork CI
        checks = data["statusCheckRollup"]
        assert len(checks) == 2
        assert checks[0]["name"] == "Build / build-jdk17"
        assert checks[0]["conclusion"] == "SUCCESS"
        assert checks[1]["conclusion"] == "FAILURE"

    @patch("pr_sync.fetch_fork_ci")
    @patch("server.gh_run")
    def test_closed_oss_pr_does_not_get_fork_ci(self, mock_gh_run, mock_fork_ci, client):
        """For closed/merged example/repo PRs, fork CI is NOT fetched."""
        call_count = [0]
        def gh_side_effect(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "number": 12345,
                        "title": "Closed PR",
                        "state": "CLOSED",
                        "headRefName": "old-branch",
                        "statusCheckRollup": [
                            {"name": "original-check", "conclusion": "SUCCESS", "status": "COMPLETED"}
                        ],
                    }),
                )
            elif call_count[0] == 2:
                return MagicMock(returncode=0, stdout="[]")
            return MagicMock(returncode=1, stdout="")

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/pr-detail?repo=example/repo&number=12345")
        assert resp.status_code == 200
        data = resp.json()
        # Fork CI should NOT have been called
        mock_fork_ci.assert_not_called()
        assert data["statusCheckRollup"][0]["name"] == "original-check"

    @patch("pr_sync.fetch_fork_ci")
    @patch("server.gh_run")
    def test_non_oss_repo_does_not_get_fork_ci(self, mock_gh_run, mock_fork_ci, client):
        """For non-example/repo repos, fork CI is not fetched."""
        call_count = [0]
        def gh_side_effect(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "number": 100,
                        "title": "Runtime PR",
                        "state": "OPEN",
                        "headRefName": "feature-branch",
                        "statusCheckRollup": [],
                    }),
                )
            elif call_count[0] == 2:
                return MagicMock(returncode=0, stdout="[]")
            return MagicMock(returncode=1, stdout="")

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/pr-detail?repo=myorg/svc&number=100")
        assert resp.status_code == 200
        mock_fork_ci.assert_not_called()

    @patch("pr_sync.fetch_fork_ci")
    @patch("server.gh_run")
    def test_fork_ci_returns_none_keeps_original_checks(self, mock_gh_run, mock_fork_ci, client):
        """When fork CI returns None, original statusCheckRollup is preserved."""
        call_count = [0]
        def gh_side_effect(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "number": 200,
                        "title": "PR with no fork CI",
                        "state": "OPEN",
                        "headRefName": "no-fork-ci-branch",
                        "statusCheckRollup": [
                            {"name": "keep-this", "conclusion": "SUCCESS", "status": "COMPLETED"}
                        ],
                    }),
                )
            elif call_count[0] == 2:
                return MagicMock(returncode=0, stdout="[]")
            return MagicMock(returncode=1, stdout="")

        mock_gh_run.side_effect = gh_side_effect
        mock_fork_ci.return_value = None  # no fork CI available

        resp = client.get("/api/pr-detail?repo=example/repo&number=200")
        assert resp.status_code == 200
        data = resp.json()
        assert data["statusCheckRollup"][0]["name"] == "keep-this"

    @patch("server._fetch_fork_ci")
    @patch("server.gh_run")
    def test_open_oss_pr_no_branch_skips_fork_ci(self, mock_gh_run, mock_fork_ci, client):
        """When open repo PR has empty headRefName, fork CI is skipped."""
        call_count = [0]
        def gh_side_effect(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "number": 300,
                        "title": "No branch PR",
                        "state": "OPEN",
                        "headRefName": "",
                        "statusCheckRollup": [],
                    }),
                )
            elif call_count[0] == 2:
                return MagicMock(returncode=0, stdout="[]")
            return MagicMock(returncode=1, stdout="")

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/pr-detail?repo=example/repo&number=300")
        assert resp.status_code == 200
        mock_fork_ci.assert_not_called()

    @patch("server.gh_run")
    def test_pr_detail_with_statuscontext_format(self, mock_gh_run, client):
        """StatusContext-style checks (from GitHub status API) are returned as-is."""
        call_count = [0]
        def gh_side_effect(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "number": 400,
                        "title": "StatusContext PR",
                        "state": "CLOSED",
                        "headRefName": "ctx-branch",
                        "statusCheckRollup": [
                            {
                                "__typename": "StatusContext",
                                "context": "ci/jenkins",
                                "state": "SUCCESS",
                                "targetUrl": "http://jenkins.example.com/build/123",
                            },
                            {
                                "__typename": "CheckRun",
                                "name": "GitHub Actions",
                                "conclusion": "SUCCESS",
                                "status": "COMPLETED",
                            },
                        ],
                    }),
                )
            elif call_count[0] == 2:
                return MagicMock(returncode=0, stdout="[]")
            return MagicMock(returncode=1, stdout="")

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/pr-detail?repo=example/repo&number=400")
        assert resp.status_code == 200
        data = resp.json()
        checks = data["statusCheckRollup"]
        assert len(checks) == 2
        # StatusContext has 'context' and 'state'
        assert checks[0]["__typename"] == "StatusContext"
        assert checks[0]["context"] == "ci/jenkins"
        # CheckRun has 'name' and 'conclusion'
        assert checks[1]["__typename"] == "CheckRun"
        assert checks[1]["name"] == "GitHub Actions"


# ====================================================================
# GitHub poll status - debug endpoint
# ====================================================================

class TestGhPollStatus:
    """Test the /api/gh-poll-status debug endpoint."""

    def test_returns_poll_status_fields(self, client, patched_server):
        """The debug endpoint returns thread_alive, last_poll_ts, seen_ids_count."""
        resp = client.get("/api/gh-poll-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "thread_alive" in data
        assert "last_poll_ts" in data
        assert "seen_ids_count" in data
        assert isinstance(data["thread_alive"], bool)
        assert isinstance(data["last_poll_ts"], (int, float))
        assert isinstance(data["seen_ids_count"], int)

    def test_poll_status_reflects_job_state(self, client, patched_server):
        """Poll status reports whether the scheduled github_poller job is
        armed. In the test env (`EVA_DISABLE_SCHEDULER=1`) no jobs are
        registered, so the endpoint reports job_registered=False."""
        resp = client.get("/api/gh-poll-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["thread_alive"] is False
        assert data["job_registered"] is False

    def test_poll_status_seen_ids_count_non_negative(self, client, patched_server):
        """seen_ids_count should always be >= 0."""
        resp = client.get("/api/gh-poll-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["seen_ids_count"] >= 0


# ====================================================================
# Close task - edge cases
# ====================================================================

class TestCloseTaskEdgeCases:
    """Test close task edge cases: long reason, unicode, tasks with PRs."""

    def test_close_task_with_very_long_reason(self, client, patched_server):
        """Closing a task with a very long reason string should succeed."""
        long_reason = "x" * 10000
        resp = client.post(
            "/api/projects/test-proj/tasks/task-d/close",
            json={"reason": long_reason},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"
        assert long_reason in data["notes"]

    def test_close_task_with_unicode_reason(self, client, patched_server):
        """Closing a task with unicode characters in reason should succeed."""
        unicode_reason = "Closed because: feature shipped successfully"
        resp = client.post(
            "/api/projects/test-proj/tasks/task-d/close",
            json={"reason": unicode_reason},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"
        assert unicode_reason in data["notes"]

    def test_close_task_with_special_characters_in_reason(self, client, patched_server):
        """Closing with special chars like newlines, tabs, quotes should work."""
        special_reason = 'Has "quotes" and\nnewlines\tand\ttabs'
        resp = client.post(
            "/api/projects/test-proj/tasks/task-d/close",
            json={"reason": special_reason},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"
        assert special_reason in data["notes"]

    def test_close_task_that_has_prs(self, client, patched_server):
        """Closing a task that has associated PRs should succeed. PRs remain."""
        # task-b has PR #200 (open)
        resp = client.post(
            "/api/projects/test-proj/tasks/task-b/close",
            json={"reason": "Abandoning this approach"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"
        assert "[Closed] Abandoning this approach" in data["notes"]

        # Verify PRs are still there via get_task which includes prs
        task = patched_server._db.get_task("test-proj", "task-b")
        assert len(task["prs"]) > 0
        assert any(p["number"] == 200 for p in task["prs"])

    def test_close_done_task(self, client, patched_server):
        """Closing a task with status 'done' should succeed (override to closed)."""
        # task-a is status=done
        resp = client.post(
            "/api/projects/test-proj/tasks/task-a/close",
            json={"reason": "Superseded by new approach"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"

    def test_close_task_with_dependencies_set(self, client, patched_server):
        """Closing a task that has dependencies should succeed."""
        # task-c depends on task-b
        resp = client.post(
            "/api/projects/test-proj/tasks/task-c/close",
            json={"reason": "No longer needed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"

    def test_close_in_progress_task(self, client, patched_server):
        """Closing an in-progress task should succeed."""
        # task-b is in_progress
        resp = client.post(
            "/api/projects/test-proj/tasks/task-b/close",
            json={"reason": "Pivoting to different approach"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"
        assert "[Closed] Pivoting to different approach" in data["notes"]

    def test_close_task_reason_with_only_whitespace(self, client, patched_server):
        """Closing with a whitespace-only reason should still append the close note."""
        resp = client.post(
            "/api/projects/test-proj/tasks/task-d/close",
            json={"reason": "   "},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"
        # The reason is "   " which is truthy, so it should be appended
        assert "[Closed]" in data["notes"]
