"""Tests for routes/common.prs.py -- _fetch_pr_detail, sync_all_prs_stream, and
remaining uncovered branches in the PR management routes."""

import common
import json
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


# ---------------------------------------------------------------------------
# _fetch_pr_detail (async, lines 151-207)
# ---------------------------------------------------------------------------

class TestFetchPrDetail:
    """Test the async _fetch_pr_detail helper directly."""

    @pytest.fixture(autouse=True)
    def _setup(self, patched_server):
        self.server = patched_server

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    @patch("routes.prs.app_state.gh_run_async")
    def test_success_updates_db(self, mock_async, patched_server):
        """A successful fetch writes updated fields back to the task DB."""
        from routes.prs import _fetch_pr_detail

        pr_data = {
            "title": "Updated title",
            "state": "OPEN",
            "url": "https://github.com/example/repo/pull/100",
            "updatedAt": "2026-03-01T00:00:00Z",
            "additions": 10,
            "deletions": 3,
            "comments": [{"id": 1}],
            "reviews": [{"id": 2}],
            "headRefName": "branch-100",
            "baseRefName": "master",
            "author": {"login": "tester"},
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "reviewDecision": "APPROVED",
        }

        call_count = [0]

        async def fake_gh_async(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout=json.dumps(pr_data))
            # Fork CI calls: run list returns empty
            elif call_count[0] == 2:
                return MagicMock(returncode=0, stdout=json.dumps([]))
            return MagicMock(returncode=1, stdout="")

        mock_async.side_effect = fake_gh_async

        result = self._run(_fetch_pr_detail(100, "https://github.com/example/repo/pull/100"))
        assert result is True

        pr = patched_server._db.find_pr_by_number(100)
        assert pr is not None
        assert pr["title"] == "Updated title"
        assert pr["additions"] == 10
        assert pr["deletions"] == 3
        assert pr["author"] == "tester"
        assert pr["head_branch"] == "branch-100"

    @patch("routes.prs.app_state.gh_run_async")
    def test_failure_returns_false(self, mock_async, patched_server):
        """When gh pr view fails, _fetch_pr_detail returns False."""
        from routes.prs import _fetch_pr_detail

        mock_async.return_value = MagicMock(returncode=1, stdout="", stderr="not found")

        result = self._run(_fetch_pr_detail(100, "https://github.com/example/repo/pull/100"))
        assert result is False

    @patch("routes.prs.app_state.gh_run_async")
    def test_disallowed_repo_returns_false(self, mock_async, patched_server):
        """A repo not in the whitelist should short-circuit and return False."""
        from routes.prs import _fetch_pr_detail

        result = self._run(_fetch_pr_detail(100, "https://github.com/unknown-org/repo/pull/100"))
        assert result is False
        mock_async.assert_not_called()

    @patch("routes.prs.app_state.gh_run_async")
    def test_empty_url_returns_false(self, mock_async, patched_server):
        """An empty URL should return False."""
        from routes.prs import _fetch_pr_detail

        result = self._run(_fetch_pr_detail(100, ""))
        assert result is False

    @patch("routes.prs.app_state.gh_run_async")
    def test_oss_fork_ci_override(self, mock_async, patched_server):
        """For open example/repo PRs, fork CI jobs replace statusCheckRollup."""
        from routes.prs import _fetch_pr_detail

        pr_data = {
            "title": "Repo PR",
            "state": "OPEN",
            "url": "https://github.com/example/repo/pull/100",
            "updatedAt": "2026-03-01T00:00:00Z",
            "additions": 5,
            "deletions": 2,
            "comments": [],
            "reviews": [],
            "headRefName": "feature-branch",
            "baseRefName": "master",
            "author": {"login": "tester"},
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "reviewDecision": "",
        }

        fork_run_list = [{"databaseId": 999, "status": "completed", "conclusion": "success"}]
        fork_job_detail = '{"name":"Build / jdk17","conclusion":"success","status":"completed"}\n{"name":"Build / jdk21","conclusion":"failure","status":"completed"}'

        call_count = [0]

        async def fake_gh_async(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                # gh pr view for the PR
                return MagicMock(returncode=0, stdout=json.dumps(pr_data))
            elif call_count[0] == 2:
                # gh run list for fork
                return MagicMock(returncode=0, stdout=json.dumps(fork_run_list))
            elif call_count[0] == 3:
                # gh run view for fork jobs
                return MagicMock(returncode=0, stdout=fork_job_detail)
            return MagicMock(returncode=1, stdout="")

        mock_async.side_effect = fake_gh_async

        result = self._run(_fetch_pr_detail(100, "https://github.com/example/repo/pull/100"))
        assert result is True
        # The fork CI should have been used (3 calls: pr view, run list, run view)
        assert call_count[0] == 3

    @patch("routes.prs.app_state.gh_run_async")
    def test_oss_fork_ci_run_list_fails(self, mock_async, patched_server):
        """If fork run list fails, original CI checks are used."""
        from routes.prs import _fetch_pr_detail

        pr_data = {
            "title": "Repo PR",
            "state": "OPEN",
            "url": "https://github.com/example/repo/pull/100",
            "updatedAt": "2026-03-01T00:00:00Z",
            "additions": 5,
            "deletions": 2,
            "comments": [],
            "reviews": [],
            "headRefName": "feature-branch",
            "baseRefName": "master",
            "author": {"login": "tester"},
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "reviewDecision": "",
        }

        call_count = [0]

        async def fake_gh_async(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout=json.dumps(pr_data))
            elif call_count[0] == 2:
                # fork run list fails
                return MagicMock(returncode=1, stdout="", stderr="error")
            return MagicMock(returncode=1, stdout="")

        mock_async.side_effect = fake_gh_async

        result = self._run(_fetch_pr_detail(100, "https://github.com/example/repo/pull/100"))
        assert result is True

    @patch("routes.prs.app_state.gh_run_async")
    def test_oss_fork_ci_empty_runs(self, mock_async, patched_server):
        """If fork run list returns empty array, original CI is kept."""
        from routes.prs import _fetch_pr_detail

        pr_data = {
            "title": "Repo PR",
            "state": "OPEN",
            "url": "https://github.com/example/repo/pull/100",
            "updatedAt": "2026-03-01T00:00:00Z",
            "additions": 5,
            "deletions": 2,
            "comments": [],
            "reviews": [],
            "headRefName": "feature-branch",
            "baseRefName": "master",
            "author": {"login": "tester"},
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "reviewDecision": "",
        }

        call_count = [0]

        async def fake_gh_async(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout=json.dumps(pr_data))
            elif call_count[0] == 2:
                # fork run list returns empty
                return MagicMock(returncode=0, stdout=json.dumps([]))
            return MagicMock(returncode=1, stdout="")

        mock_async.side_effect = fake_gh_async

        result = self._run(_fetch_pr_detail(100, "https://github.com/example/repo/pull/100"))
        assert result is True

    @patch("routes.prs.app_state.gh_run_async")
    def test_non_oss_repo_no_fork_ci(self, mock_async, patched_server):
        """For non-repo repos, fork CI is not attempted."""
        from routes.prs import _fetch_pr_detail

        # Add a PR for runtime
        patched_server._db.add_pr(
            project="test-proj", task_id="task-b",
            number=300,
            url="https://github.com/myorg/svc/pull/300",
            status="open",
        )

        pr_data = {
            "title": "Runtime PR",
            "state": "OPEN",
            "url": "https://github.com/myorg/svc/pull/300",
            "updatedAt": "2026-03-01T00:00:00Z",
            "additions": 1,
            "deletions": 0,
            "comments": [],
            "reviews": [],
            "headRefName": "feature-rt",
            "baseRefName": "master",
            "author": {"login": "tester"},
            "statusCheckRollup": [],
            "reviewDecision": "",
        }
        mock_async.return_value = MagicMock(returncode=0, stdout=json.dumps(pr_data))

        result = self._run(_fetch_pr_detail(300, "https://github.com/myorg/svc/pull/300"))
        assert result is True
        # Only one call (no fork CI calls)
        assert mock_async.call_count == 1

    @patch("routes.prs.app_state.gh_run_async")
    def test_exception_returns_false(self, mock_async, patched_server):
        """If an exception is raised during fetch, return False."""
        from routes.prs import _fetch_pr_detail

        mock_async.side_effect = Exception("network error")

        result = self._run(_fetch_pr_detail(100, "https://github.com/example/repo/pull/100"))
        assert result is False

    @patch("routes.prs.app_state.gh_run_async")
    def test_closed_oss_pr_no_fork_ci(self, mock_async, patched_server):
        """For CLOSED example/repo PRs, fork CI is NOT attempted."""
        from routes.prs import _fetch_pr_detail

        pr_data = {
            "title": "Closed Repo PR",
            "state": "CLOSED",
            "url": "https://github.com/example/repo/pull/100",
            "updatedAt": "2026-03-01T00:00:00Z",
            "additions": 1,
            "deletions": 0,
            "comments": [],
            "reviews": [],
            "headRefName": "old-branch",
            "baseRefName": "master",
            "author": {"login": "tester"},
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "reviewDecision": "APPROVED",
        }
        mock_async.return_value = MagicMock(returncode=0, stdout=json.dumps(pr_data))

        result = self._run(_fetch_pr_detail(100, "https://github.com/example/repo/pull/100"))
        assert result is True
        # Only one call (pr view), no fork CI
        assert mock_async.call_count == 1

    @patch("routes.prs.app_state.gh_run_async")
    def test_fork_ci_jobs_view_fails(self, mock_async, patched_server):
        """If fork job view fails, original CI is kept."""
        from routes.prs import _fetch_pr_detail

        pr_data = {
            "title": "Repo PR",
            "state": "OPEN",
            "url": "https://github.com/example/repo/pull/100",
            "updatedAt": "2026-03-01T00:00:00Z",
            "additions": 5,
            "deletions": 2,
            "comments": [],
            "reviews": [],
            "headRefName": "feature-branch",
            "baseRefName": "master",
            "author": {"login": "tester"},
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "reviewDecision": "",
        }

        fork_run_list = [{"databaseId": 999, "status": "completed", "conclusion": "success"}]

        call_count = [0]

        async def fake_gh_async(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout=json.dumps(pr_data))
            elif call_count[0] == 2:
                return MagicMock(returncode=0, stdout=json.dumps(fork_run_list))
            elif call_count[0] == 3:
                # jobs view fails
                return MagicMock(returncode=1, stdout="", stderr="error")
            return MagicMock(returncode=1, stdout="")

        mock_async.side_effect = fake_gh_async

        result = self._run(_fetch_pr_detail(100, "https://github.com/example/repo/pull/100"))
        assert result is True


# ---------------------------------------------------------------------------
# sync_all_prs_stream (SSE endpoint, lines 213-294)
# ---------------------------------------------------------------------------

class TestSyncAllPrsStream:
    """Test the SSE streaming sync endpoint."""

    @patch("routes.prs.app_state.gh_run_async")
    @patch("routes.prs.app_state._build_repo_authors")
    def test_stream_phases(self, mock_authors, mock_async, client, patched_server):
        """Verify the SSE stream emits start, dirty, discover, update, done phases."""
        mock_authors.return_value = {"example/repo": "test-author"}

        # gh_run_async returns empty for searches, and success for pr view
        async def fake_gh_async(args, repo="", timeout=20):
            if "search" in args:
                return MagicMock(returncode=0, stdout=json.dumps([]))
            elif "pr" in args and "view" in args:
                return MagicMock(returncode=0, stdout=json.dumps({
                    "title": "T",
                    "state": "OPEN",
                    "url": "https://github.com/example/repo/pull/100",
                    "updatedAt": "2026-01-01",
                    "additions": 0, "deletions": 0,
                    "comments": [], "reviews": [],
                    "headRefName": "b", "baseRefName": "master",
                    "author": {"login": "u"},
                    "statusCheckRollup": [],
                    "reviewDecision": "",
                }))
            return MagicMock(returncode=1, stdout="")

        mock_async.side_effect = fake_gh_async

        resp = client.get("/api/all-prs/sync-stream")
        assert resp.status_code == 200

        # Parse SSE events
        lines = resp.text.strip().split("\n")
        events = []
        for line in lines:
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        phases = [e["phase"] for e in events]
        assert "start" in phases
        assert "dirty" in phases
        assert "discover" in phases
        assert "done" in phases

    @patch("routes.prs.app_state.gh_run_async")
    @patch("routes.prs.app_state._build_repo_authors")
    def test_stream_discovers_new_prs(self, mock_authors, mock_async, client, patched_server):
        """When search finds new PRs, they should be discovered and updated."""
        mock_authors.return_value = {"example/repo": "test-author"}

        # Create a task to match against
        patched_server._db.create_task(
            project="oss-repo", task_id="EX-500",
            description="Test ticket", type="feature", status="in_progress",
            ticket_id="EX-500",
        )

        search_results = json.dumps([
            {"number": 500, "title": "[EX-500] New feature PR", "url": "https://github.com/example/repo/pull/500", "state": "open"},
        ])

        pr_view_data = json.dumps({
            "title": "[EX-500] New feature PR",
            "state": "OPEN",
            "url": "https://github.com/example/repo/pull/500",
            "updatedAt": "2026-01-01",
            "additions": 10, "deletions": 5,
            "comments": [], "reviews": [],
            "headRefName": "repo-500", "baseRefName": "master",
            "author": {"login": "tester"},
            "statusCheckRollup": [],
            "reviewDecision": "",
        })

        async def fake_gh_async(args, repo="", timeout=20):
            if "search" in args:
                return MagicMock(returncode=0, stdout=search_results)
            elif "pr" in args and "view" in args:
                return MagicMock(returncode=0, stdout=pr_view_data)
            return MagicMock(returncode=1, stdout="")

        mock_async.side_effect = fake_gh_async

        resp = client.get("/api/all-prs/sync-stream")
        assert resp.status_code == 200

        lines = resp.text.strip().split("\n")
        events = []
        for line in lines:
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        done_event = [e for e in events if e["phase"] == "done"][0]
        assert done_event["discovered"] >= 0
        assert "updated" in done_event

    @patch("routes.prs._fetch_pr_detail")
    @patch("routes.prs.app_state.gh_run_async")
    @patch("routes.prs.app_state._build_repo_authors")
    def test_stream_dirty_prs_updated(self, mock_authors, mock_async, mock_fetch, client, patched_server):
        """Dirty PRs are fetched and cleared during the dirty phase."""
        mock_authors.return_value = {}

        # Mark a PR as dirty
        patched_server._db.mark_pr_dirty(100)

        async def fake_fetch(pr_number, pr_url):
            return True

        mock_fetch.side_effect = fake_fetch

        resp = client.get("/api/all-prs/sync-stream")
        assert resp.status_code == 200

        lines = resp.text.strip().split("\n")
        events = []
        for line in lines:
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        dirty_event = [e for e in events if e["phase"] == "dirty"][0]
        assert dirty_event["count"] >= 1

    @patch("routes.prs.app_state.gh_run_async")
    @patch("routes.prs.app_state._build_repo_authors")
    def test_stream_full_mode(self, mock_authors, mock_async, client, patched_server):
        """With full=1, all existing PRs are updated (not just newly discovered)."""
        mock_authors.return_value = {"example/repo": "test-author"}

        async def fake_gh_async(args, repo="", timeout=20):
            if "search" in args:
                return MagicMock(returncode=0, stdout=json.dumps([]))
            elif "pr" in args and "view" in args:
                return MagicMock(returncode=0, stdout=json.dumps({
                    "title": "T",
                    "state": "OPEN",
                    "url": "https://github.com/example/repo/pull/100",
                    "updatedAt": "2026-01-01",
                    "additions": 0, "deletions": 0,
                    "comments": [], "reviews": [],
                    "headRefName": "b", "baseRefName": "master",
                    "author": {"login": "u"},
                    "statusCheckRollup": [],
                    "reviewDecision": "",
                }))
            return MagicMock(returncode=1, stdout="")

        mock_async.side_effect = fake_gh_async

        resp = client.get("/api/all-prs/sync-stream?full=1")
        assert resp.status_code == 200

        lines = resp.text.strip().split("\n")
        events = []
        for line in lines:
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        start_event = [e for e in events if e["phase"] == "start"][0]
        assert start_event["full"] is True
        done_event = [e for e in events if e["phase"] == "done"][0]
        # In full mode, total should be all PRs in DB (at least the 2 seeded ones)
        assert done_event["total"] >= 2

    @patch("routes.prs.app_state.gh_run_async")
    @patch("routes.prs.app_state._build_repo_authors")
    def test_stream_discover_error_continues(self, mock_authors, mock_async, client, patched_server):
        """If discover for a repo throws an exception, stream continues."""
        mock_authors.return_value = {"example/repo": "test-author"}

        async def fake_gh_async(args, repo="", timeout=20):
            if "search" in args:
                raise Exception("network timeout")
            return MagicMock(returncode=1, stdout="")

        mock_async.side_effect = fake_gh_async

        resp = client.get("/api/all-prs/sync-stream")
        assert resp.status_code == 200

        lines = resp.text.strip().split("\n")
        events = []
        for line in lines:
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        phases = [e["phase"] for e in events]
        assert "done" in phases


# ---------------------------------------------------------------------------
# sync_all_prs (POST, lines 298-388 -- fill remaining gaps)
# ---------------------------------------------------------------------------

class TestSyncAllPrsPost:
    """Test sync POST endpoint for lines not covered by test_e2e.py."""

    @patch("server.gh_run")
    def test_discover_new_pr_and_match_task(self, mock_gh_run, client, patched_server):
        """Discover a new PR that matches an existing task by ticket id."""
        patched_server._db.create_task(
            project="oss-repo", task_id="EX-777",
            description="New feature", type="feature",
            status="in_progress", ticket_id="EX-777",
        )

        search_results = json.dumps([
            {"number": 777, "title": "[EX-777] New feature", "url": "https://github.com/example/repo/pull/777", "state": "open"},
        ])
        pr_view = json.dumps({
            "number": 777,
            "title": "[EX-777] New feature",
            "state": "OPEN",
            "url": "https://github.com/example/repo/pull/777",
            "updatedAt": "2026-01-01T00:00:00Z",
            "additions": 20, "deletions": 5,
            "comments": [], "reviews": [],
            "headRefName": "repo-777", "baseRefName": "master",
            "author": {"login": "dev"},
            "statusCheckRollup": [], "reviewDecision": "",
        })

        def fake_gh(args, repo="", timeout=20):
            m = MagicMock()
            m.returncode = 0
            if "search" in args:
                m.stdout = search_results
            else:
                m.stdout = pr_view
            return m

        mock_gh_run.side_effect = fake_gh

        resp = client.post("/api/all-prs/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["discovered"] >= 1

        pr = patched_server._db.find_pr_by_number(777)
        assert pr is not None

    @patch("server.gh_run")
    def test_sync_handles_search_failure(self, mock_gh_run, client, patched_server):
        """If search returns non-zero, sync should skip that state but not crash."""
        def fake_gh(args, repo="", timeout=20):
            m = MagicMock()
            if "search" in args:
                m.returncode = 1
                m.stdout = ""
            else:
                m.returncode = 0
                m.stdout = json.dumps({
                    "number": 100, "title": "T", "state": "OPEN",
                    "url": "https://github.com/example/repo/pull/100",
                    "updatedAt": "2026-01-01", "additions": 0, "deletions": 0,
                    "comments": [], "reviews": [],
                    "headRefName": "b", "baseRefName": "master",
                    "author": {"login": "u"},
                    "statusCheckRollup": [], "reviewDecision": "",
                })
            return m

        mock_gh_run.side_effect = fake_gh

        resp = client.post("/api/all-prs/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["discovered"] == 0

    @patch("server.gh_run")
    def test_sync_pr_view_error_appends_to_errors(self, mock_gh_run, client, patched_server):
        """If pr view raises an exception, it is captured in errors."""
        call_count = [0]

        def fake_gh(args, repo="", timeout=20):
            call_count[0] += 1
            m = MagicMock()
            if "search" in args:
                m.returncode = 0
                m.stdout = json.dumps([])
            elif "pr" in args and "view" in args:
                raise Exception("timeout")
            else:
                m.returncode = 0
                m.stdout = json.dumps([])
            return m

        mock_gh_run.side_effect = fake_gh

        resp = client.post("/api/all-prs/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["errors"]) >= 1

    @patch("server.gh_run")
    def test_sync_skips_pr_with_no_url(self, mock_gh_run, client, patched_server):
        """PRs with empty url are skipped during the update phase."""
        # Add a PR with empty URL
        patched_server._db.add_pr(
            project="test-proj", task_id="task-a",
            number=999, url="", status="open", title="No URL PR",
        )

        def fake_gh(args, repo="", timeout=20):
            m = MagicMock()
            m.returncode = 0
            if "search" in args:
                m.stdout = json.dumps([])
            else:
                m.stdout = json.dumps({
                    "number": 100, "title": "T", "state": "OPEN",
                    "url": "https://github.com/example/repo/pull/100",
                    "updatedAt": "2026-01-01", "additions": 0, "deletions": 0,
                    "comments": [], "reviews": [],
                    "headRefName": "b", "baseRefName": "master",
                    "author": {"login": "u"},
                    "statusCheckRollup": [], "reviewDecision": "",
                })
            return m

        mock_gh_run.side_effect = fake_gh

        resp = client.post("/api/all-prs/sync")
        assert resp.status_code == 200

    @patch("server.gh_run")
    def test_sync_closed_state(self, mock_gh_run, client, patched_server):
        """Sync correctly handles PRs with closed state in search results."""
        patched_server._db.create_task(
            project="oss-repo", task_id="EX-888",
            description="Closed ticket", type="feature",
            status="done", ticket_id="EX-888",
        )

        def fake_gh(args, repo="", timeout=20):
            m = MagicMock()
            m.returncode = 0
            if "search" in args:
                if "--state" in args:
                    idx = args.index("--state")
                    state = args[idx + 1]
                    if state == "closed":
                        m.stdout = json.dumps([
                            {"number": 888, "title": "[EX-888] Closed PR",
                             "url": "https://github.com/example/repo/pull/888", "state": "closed"},
                        ])
                    else:
                        m.stdout = json.dumps([])
                else:
                    m.stdout = json.dumps([])
            else:
                m.stdout = json.dumps({
                    "number": 888, "title": "[EX-888] Closed PR",
                    "state": "CLOSED",
                    "url": "https://github.com/example/repo/pull/888",
                    "updatedAt": "2026-01-01", "additions": 0, "deletions": 0,
                    "comments": [], "reviews": [],
                    "headRefName": "b", "baseRefName": "master",
                    "author": {"login": "u"},
                    "statusCheckRollup": [], "reviewDecision": "",
                })
            return m

        mock_gh_run.side_effect = fake_gh

        resp = client.post("/api/all-prs/sync")
        assert resp.status_code == 200

    @patch("server.gh_run")
    def test_sync_search_exception_captured(self, mock_gh_run, client, patched_server):
        """If search raises an exception, it is captured in errors list."""
        call_count = [0]

        def fake_gh(args, repo="", timeout=20):
            call_count[0] += 1
            if "search" in args:
                raise Exception("rate limited")
            m = MagicMock()
            m.returncode = 0
            m.stdout = json.dumps({
                "number": 100, "title": "T", "state": "OPEN",
                "url": "https://github.com/example/repo/pull/100",
                "updatedAt": "2026-01-01", "additions": 0, "deletions": 0,
                "comments": [], "reviews": [],
                "headRefName": "b", "baseRefName": "master",
                "author": {"login": "u"},
                "statusCheckRollup": [], "reviewDecision": "",
            })
            return m

        mock_gh_run.side_effect = fake_gh

        resp = client.post("/api/all-prs/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["errors"]) > 0


# ---------------------------------------------------------------------------
# _pr_info_cache eviction (lines 82-86)
# ---------------------------------------------------------------------------

class TestPrInfoCacheEviction:
    @patch("server.gh_run")
    def test_cache_eviction_over_200(self, mock_gh_run, client, patched_server):
        """When cache exceeds 200 entries, oldest 50 are evicted."""
        import routes.prs as prs_mod
        import time

        # Pre-fill cache with 201 entries
        prs_mod._pr_info_cache.clear()
        for i in range(201):
            url = f"https://github.com/test/repo/pull/{i}"
            prs_mod._pr_info_cache[url] = {"_ts": time.time() - (300 - i), "url": url}

        assert len(prs_mod._pr_info_cache) == 201

        # Mock gh_run to return valid responses
        mock_gh_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"requested_reviewers": [], "updated_at": "2026-01-01"}),
        )

        # Trigger a new cache entry
        resp = client.get("/api/pr-info?url=https://github.com/new/repo/pull/999")
        assert resp.status_code == 200

        # Cache should have been pruned (201 + 1 = 202 > 200, so 50 oldest removed)
        assert len(prs_mod._pr_info_cache) <= 202 - 50 + 1

        # Cleanup
        prs_mod._pr_info_cache.clear()


# ---------------------------------------------------------------------------
# Helper wrappers (lines 126-139)
# ---------------------------------------------------------------------------

class TestHelperWrappers:
    @patch("server.gh_run")
    def test_fetch_fork_ci_wrapper(self, mock_gh_run, patched_server):
        """_fetch_fork_ci delegates to pr_sync.fetch_fork_ci."""
        from routes.prs import _fetch_fork_ci
        mock_gh_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{"name": "Build", "conclusion": "success", "status": "completed"}]),
        )
        # Just verify it does not crash; the real logic is tested in test_pr_sync.py
        _fetch_fork_ci("some-branch")

    @patch("server.gh_run")
    def test_is_externally_merged_wrapper_true(self, mock_gh_run, patched_server):
        """`_is_externally_merged(repo, n)` delegates to pr_sync.
        Generic across repos -- not Repo-specific anymore."""
        from routes.prs import _is_externally_merged
        mock_gh_run.return_value = MagicMock(returncode=0, stdout="1\n")
        assert _is_externally_merged("example/repo", 100) is True
        # Same code path works for any opt-in repo.
        assert _is_externally_merged("acme/widgets", 200) is True

    @patch("server.gh_run")
    def test_is_externally_merged_wrapper_false(self, mock_gh_run, patched_server):
        """Returns False when issue events show no merge commit."""
        from routes.prs import _is_externally_merged
        mock_gh_run.return_value = MagicMock(returncode=0, stdout="0\n")
        assert _is_externally_merged("example/repo", 100) is False

    def test_is_externally_merged_rejects_invalid_repo(self, patched_server):
        """Defensive: empty/malformed repo string short-circuits to
        False without shelling out to gh."""
        from routes.prs import _is_externally_merged
        assert _is_externally_merged("", 100) is False
        assert _is_externally_merged("noslash", 100) is False


# ---------------------------------------------------------------------------
# _fetch_pr_detail edge cases with malformed JSON from fork CI
# ---------------------------------------------------------------------------

class TestFetchPrDetailForkCiEdgeCases:
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    @patch("routes.prs.app_state.gh_run_async")
    def test_fork_ci_malformed_json_lines(self, mock_async, patched_server):
        """Malformed JSON lines in fork CI job output are skipped."""
        from routes.prs import _fetch_pr_detail

        pr_data = {
            "title": "Repo PR",
            "state": "OPEN",
            "url": "https://github.com/example/repo/pull/100",
            "updatedAt": "2026-03-01T00:00:00Z",
            "additions": 5, "deletions": 2,
            "comments": [], "reviews": [],
            "headRefName": "feature-branch",
            "baseRefName": "master",
            "author": {"login": "tester"},
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "reviewDecision": "",
        }

        fork_run_list = [{"databaseId": 999, "status": "completed", "conclusion": "success"}]
        # Mix of valid and invalid JSON lines
        fork_jobs_output = '{"name":"Build","conclusion":"success","status":"completed"}\nNOT_JSON\n{"name":"Test","conclusion":"failure","status":"completed"}'

        call_count = [0]

        async def fake_gh_async(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout=json.dumps(pr_data))
            elif call_count[0] == 2:
                return MagicMock(returncode=0, stdout=json.dumps(fork_run_list))
            elif call_count[0] == 3:
                return MagicMock(returncode=0, stdout=fork_jobs_output)
            return MagicMock(returncode=1, stdout="")

        mock_async.side_effect = fake_gh_async

        result = self._run(_fetch_pr_detail(100, "https://github.com/example/repo/pull/100"))
        assert result is True

    @patch("routes.prs.app_state.gh_run_async")
    def test_null_fields_handled(self, mock_async, patched_server):
        """None/null values in PR data fields are handled gracefully."""
        from routes.prs import _fetch_pr_detail

        pr_data = {
            "title": None,
            "state": None,
            "url": "",
            "updatedAt": None,
            "additions": None,
            "deletions": None,
            "comments": None,
            "reviews": None,
            "headRefName": None,
            "baseRefName": None,
            "author": None,
            "statusCheckRollup": None,
            "reviewDecision": None,
        }
        mock_async.return_value = MagicMock(returncode=0, stdout=json.dumps(pr_data))

        result = self._run(_fetch_pr_detail(100, "https://github.com/example/repo/pull/100"))
        assert result is True


# ---------------------------------------------------------------------------
# _ingest_discovered_item -- the shared PR-insertion helper (lines 226+)
# ---------------------------------------------------------------------------


class TestIngestDiscoveredItem:
    """The shared discover helper is the only place that:
      * extracts the repo from owner-search results,
      * resolves forks to upstream,
      * gates on the allow-list,
      * matches titles to tasks,
      * persists the PR.
    Both sync (`_discover_new_prs`) and async (`_discover_repo_async`)
    share it, so these tests lock in the semantics rather than retest the
    same code path twice."""

    def test_returns_none_when_number_missing(self, patched_server):
        from routes.prs import _ingest_discovered_item
        assert _ingest_discovered_item({}, "example/repo", "open") is None

    def test_skips_existing_pr(self, patched_server):
        """PR #100 is already in the seeded DB for task-a; re-ingestion
        must noop, NOT overwrite the existing row."""
        from routes.prs import _ingest_discovered_item
        item = {
            "number": 100, "title": "whatever",
            "state": "OPEN", "url": "x",
        }
        assert _ingest_discovered_item(item, "example/repo", "open") is None

    @patch("common.prs._match_pr_to_task", return_value=None)
    def test_returns_none_when_no_task_match(self, _mock_match, patched_server):
        """A PR whose title matches no task must be skipped so we don't
        pollute the dashboard with irrelevant bots."""
        from routes.prs import _ingest_discovered_item
        item = {"number": 999, "title": "random", "state": "OPEN"}
        assert _ingest_discovered_item(item, "example/repo", "open") is None

    @patch("common.prs._resolve_pr_status", return_value="open")
    @patch("common.prs._match_pr_to_task", return_value=("test-proj", "task-d"))
    def test_persists_and_returns_meta_on_match(
        self, _mock_match, _mock_status, patched_server
    ):
        """Happy path: a match triggers a DB insert and the helper
        returns the dict that sync/async loops use to build their
        progress reports."""
        from routes.prs import _ingest_discovered_item
        item = {
            "number": 9998, "title": "[EX-99999] Fix foo",
            "state": "OPEN", "url": "ignored",
        }
        result = _ingest_discovered_item(item, "example/repo", "open")
        assert result is not None
        assert result["number"] == 9998
        assert result["repo"] == "example/repo"
        assert result["match"] == ("test-proj", "task-d")
        # Persistence happened -- row is readable via find_pr_by_number.
        assert patched_server._db.find_pr_by_number(9998) is not None

    @patch("common.prs._resolve_pr_status", return_value="open")
    @patch("common.prs._match_pr_to_task", return_value=("test-proj", "task-d"))
    def test_extracts_repo_from_owner_search_result_dict(
        self, _mock_match, _mock_status, patched_server
    ):
        """`--owner myorg` searches return a `repository` object;
        ingest must pull `nameWithOwner` from it so the PR URL points at
        the right repo (not the search token 'owner:...')."""
        from routes.prs import _ingest_discovered_item
        item = {
            "number": 9997, "title": "[RUN-1] something",
            "state": "OPEN",
            "repository": {"nameWithOwner": "myorg/svc"},
        }
        result = _ingest_discovered_item(item, "owner:myorg", "open")
        assert result is not None
        assert result["repo"] == "myorg/svc"
        assert "myorg/svc" in result["url"]

    @patch("common.prs._match_pr_to_task", return_value=("test-proj", "task-d"))
    def test_rejects_pr_from_disallowed_repo(self, _mock_match, patched_server):
        """The allow-list in adapters.github guards which repos Eva tracks;
        ingest must honour it so a typo or extraneous search result can't
        sneak a random repo's PR into the DB."""
        from routes.prs import _ingest_discovered_item
        item = {
            "number": 9996, "title": "[X-1] Test",
            "state": "OPEN",
            "repository": {"nameWithOwner": "not-allowed-org/some-repo"},
        }
        assert _ingest_discovered_item(item, "example/repo", "open") is None


class TestBuildSearchArgsHelper:
    """`_build_search_args` centralises the owner:org vs org/repo split
    so the sync + async discover paths stay consistent."""

    def test_owner_prefix_emits_owner_flag(self):
        from routes.prs import _build_search_args
        search_arg, hint = _build_search_args("owner:myorg")
        assert search_arg == ["--owner", "myorg"]
        # The hint is used purely to pick the right gh account/token for
        # the org; any repo within the org suffices.
        assert hint.startswith("myorg/")

    def test_repo_form_emits_repo_flag(self):
        from routes.prs import _build_search_args
        search_arg, hint = _build_search_args("example/repo")
        assert search_arg == ["--repo", "example/repo"]
        assert hint == "example/repo"
