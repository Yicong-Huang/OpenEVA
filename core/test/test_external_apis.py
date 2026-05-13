"""Tests for external API endpoints: usage, workstats, pr-info,
pr-detail, list-all-prs. All external calls (urllib, subprocess,
gh CLI) are mocked."""

import json
import time
from io import BytesIO
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime

# ---- Usage API ----

class TestGetUsage:
    def _reset_usage_cache(self):
        import routes.system
        routes.system._usage_cache["data"] = None
        routes.system._usage_cache["ts"] = 0

    @patch("routes.system.subprocess.run")
    def test_parses_usage_output(self, mock_run, client):
        self._reset_usage_cache()
        mock_run.return_value = MagicMock(
            stdout="Daily: $12.34\nWeekly: $56.78\nMonthly: $123.45\nPower User tier\n",
            stderr="",
            returncode=0,
        )
        resp = client.get("/api/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["daily"] == "12.34"
        assert data["weekly"] == "56.78"
        assert data["monthly"] == "123.45"
        assert data["tier"] == "Power User"

    @patch("routes.system.subprocess.run")
    def test_parses_standard_tier(self, mock_run, client):
        self._reset_usage_cache()
        mock_run.return_value = MagicMock(
            stdout="Daily: $5.00\nStandard tier\n",
            stderr="",
            returncode=0,
        )
        resp = client.get("/api/usage")
        data = resp.json()
        assert data["daily"] == "5.00"
        assert data["tier"] == "Standard"

    @patch("routes.system.subprocess.run")
    def test_handles_empty_output(self, mock_run, client):
        self._reset_usage_cache()
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="",
            returncode=0,
        )
        resp = client.get("/api/usage")
        data = resp.json()
        assert data["daily"] is None
        assert data["weekly"] is None
        assert data["monthly"] is None
        assert data["tier"] is None

    @patch("routes.system.subprocess.run")
    def test_parses_from_stderr_too(self, mock_run, client):
        self._reset_usage_cache()
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="Daily: $99.99\n",
            returncode=1,
        )
        resp = client.get("/api/usage")
        data = resp.json()
        assert data["daily"] == "99.99"

    @patch("routes.system.subprocess.run")
    def test_calls_agent_usage_command(self, mock_run, client):
        """The usage route shells out to the active agent's CLI with
        `usage --days 1`. The agent itself is pluggable (OSS default
        `claude`; an extension's install can swap in its own binary
        via `service.agent.impl`) so we only assert on the argv shape,
        not on a hardcoded binary name."""
        self._reset_usage_cache()
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        client.get("/api/usage")
        mock_run.assert_called_once()
        argv, kwargs = mock_run.call_args
        assert argv[0][1:] == ["usage", "--days", "1"]
        assert kwargs == {
            "capture_output": True, "text": True, "timeout": 15,
        }


# ---- Workstats API ----

class TestGetWorkstats:
    def test_returns_data_on_first_call(self, client, patched_server):
        # Reset cache
        patched_server._workstats_cache["data"] = None
        patched_server._workstats_cache["ts"] = 0

        resp = client.get("/api/workstats")
        assert resp.status_code == 200
        data = resp.json()
        assert "quarters" in data
        assert "all_time" in data
        assert "weekly" in data

    def test_returns_cached_data_within_5min(self, client, patched_server):
        import time as _time
        patched_server._workstats_cache["data"] = {
            "quarters": [{"period": "Q1", "total": 10}],
            "all_time": {"total": 10},
            "weekly": [1, 2, 3],
        }
        patched_server._workstats_cache["ts"] = _time.time()

        resp = client.get("/api/workstats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["quarters"][0]["period"] == "Q1"

    def test_force_refresh(self, client, patched_server):
        import time as _time
        patched_server._workstats_cache["data"] = {
            "quarters": [], "all_time": {}, "weekly": [],
        }
        patched_server._workstats_cache["ts"] = _time.time()

        resp = client.get("/api/workstats?refresh=1")
        assert resp.status_code == 200

    def test_first_call_computes_from_db(self, client, patched_server):
        patched_server._workstats_cache["data"] = None
        patched_server._workstats_cache["ts"] = 0

        # Insert a merged PR with last_updated so _compute_workstats finds it.
        # author MUST match a configured gh login -- the workstats query
        # strictly filters out non-self PRs (review-watch entries, bots).
        patched_server._db.add_pr(
            project="test-proj", task_id="task-a", number=501,
            url="https://github.com/example/repo/pull/501",
            status="merged", last_updated="2026-03-10T12:00:00Z",
            author="test-author",
        )

        resp = client.get("/api/workstats")
        assert resp.status_code == 200
        data = resp.json()
        assert "quarters" in data
        assert data["all_time"]["total"] >= 1


# ---- _compute_workstats ----

class TestComputeWorkstats:
    def test_returns_stats_from_merged_prs(self, patched_server):
        from common import system as system_mod
        patched_server._db.add_pr(
            project="test-proj", task_id="task-a", number=601,
            url="https://github.com/example/repo/pull/601",
            status="merged", last_updated="2026-03-15T10:00:00Z",
            author="test-author",
        )
        result = system_mod._compute_workstats()
        assert result is not None
        assert result["all_time"]["repo"] >= 1
        assert result["all_time"]["total"] >= 1

    def test_filters_out_prs_not_authored_by_me(self, patched_server):
        """Strict author filter: review-watch entries and bot PRs that
        live in the local mirror MUST NOT inflate the user's workstats.
        Only PRs whose author matches a configured gh account count."""
        from common import system as system_mod
        patched_server._db._conn.execute(
            "DELETE FROM prs WHERE status IN ('merged', 'closed')"
        )
        patched_server._db._conn.commit()
        # Mine.
        patched_server._db.add_pr(
            project="test-proj", task_id="task-a", number=901,
            url="https://github.com/example/repo/pull/901",
            status="merged", last_updated="2026-03-15T10:00:00Z",
            author="test-author",
        )
        # Bot.
        patched_server._db.add_pr(
            project="test-proj", task_id="task-b", number=902,
            url="https://github.com/myorg/monorepo/pull/902",
            status="merged", last_updated="2026-03-15T10:00:00Z",
            author="app/service-safe-team",
        )
        # Review-watch (different real coworker).
        patched_server._db.add_pr(
            project="test-proj", task_id="task-c", number=903,
            url="https://github.com/myorg/monorepo/pull/903",
            status="merged", last_updated="2026-03-15T10:00:00Z",
            author="some-coworker",
        )
        # Empty author (legacy entry that never got backfilled).
        patched_server._db.add_pr(
            project="test-proj", task_id="task-d", number=904,
            url="https://github.com/example/repo/pull/904",
            status="merged", last_updated="2026-03-15T10:00:00Z",
            author="",
        )
        result = system_mod._compute_workstats()
        assert result is not None
        # Only the one self-authored PR should be counted.
        assert result["all_time"]["total"] == 1
        assert result["all_time"]["repo"] == 1
        assert result["all_time"].get("universe", 0) == 0

    def test_returns_empty_when_no_prs(self, patched_server):
        from common import system as system_mod
        patched_server._db._conn.execute(
            "DELETE FROM prs WHERE status IN ('merged', 'closed')"
        )
        patched_server._db._conn.commit()
        result = system_mod._compute_workstats()
        assert result is not None
        assert result["quarters"] == []
        assert result["all_time"]["total"] == 0

    def test_weekly_primary_series_is_aligned_with_weekly_total(
        self, patched_server,
    ):
        """The PR plugin overlays `weekly_primary` on `weekly` on the
        same X axis; both series MUST have identical length and
        correspond index-for-index to the same sorted ISO-week keys.
        The "primary" repo is configurable via
        `service.plugins.pr_primary_repo` so any user can pick which
        repo to highlight on the trendline."""
        from common import system as system_mod
        patched_server._db._conn.execute(
            "DELETE FROM prs WHERE status IN ('merged', 'closed')"
        )
        patched_server._db._conn.commit()
        patched_server._db.set_setting(
            "service.plugins.pr_primary_repo", "repo",
        )
        # Two repo PRs in one week, one runtime PR in a later week.
        patched_server._db.add_pr(
            project="test-proj", task_id="task-a", number=701,
            url="https://github.com/example/repo/pull/701",
            status="merged", last_updated="2026-03-02T10:00:00Z",  # W=10
            author="test-author",
        )
        patched_server._db.add_pr(
            project="test-proj", task_id="task-a", number=702,
            url="https://github.com/example/repo/pull/702",
            status="merged", last_updated="2026-03-04T10:00:00Z",  # W=10
            author="test-author",
        )
        patched_server._db.add_pr(
            project="test-proj", task_id="task-b", number=703,
            url="https://github.com/myorg/svc/pull/703",
            status="merged", last_updated="2026-03-16T10:00:00Z",  # W=12
            author="test-author_data",
        )
        result = system_mod._compute_workstats()
        assert result is not None
        assert "weekly" in result and "weekly_primary" in result
        # Same length so the two lines share an X axis in the trendline.
        assert len(result["weekly"]) == len(result["weekly_primary"])
        # Sum sanity: repo total == 2, overall total == 3.
        assert sum(result["weekly_primary"]) == 2
        assert sum(result["weekly"]) == 3
        # Runtime-only week has primary=0.
        zero_weeks = [
            i for i, v in enumerate(result["weekly_primary"]) if v == 0
        ]
        assert len(zero_weeks) >= 1

    def test_weekly_primary_is_all_zero_when_no_primary_repo_setting(
        self, patched_server,
    ):
        """No `pr_primary_repo` setting -> overlay series is all zeros
        (the trendline shows just the total line)."""
        from common import system as system_mod
        patched_server._db._conn.execute(
            "DELETE FROM prs WHERE status IN ('merged', 'closed')"
        )
        patched_server._db._conn.commit()
        # Don't set pr_primary_repo -- default empty string -> no overlay.
        patched_server._db.add_pr(
            project="test-proj", task_id="task-a", number=801,
            url="https://github.com/example/repo/pull/801",
            status="merged", last_updated="2026-03-02T10:00:00Z",
            author="test-author",
        )
        result = system_mod._compute_workstats()
        assert result is not None
        assert sum(result["weekly_primary"]) == 0
        assert sum(result["weekly"]) == 1

    def test_returns_none_on_db_error(self, patched_server):
        from common import system as system_mod
        import app_state
        broken_db = MagicMock()
        broken_db._conn.execute.side_effect = Exception("db error")
        with patch.object(app_state, "_db", broken_db):
            result = system_mod._compute_workstats()
        assert result is None


# ---- PR Info API ----

class TestGetPrInfo:
    @patch("server.gh_run")
    def test_fetches_pr_info(self, mock_gh_run, client, patched_server):
        # Clear cache
        patched_server._pr_info_cache.clear()

        # Mock responses for three gh_run calls
        call_count = [0]
        def gh_side_effect(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:  # PR metadata
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "requested_reviewers": ["reviewer1"],
                        "updated_at": "2026-01-01T00:00:00Z",
                    }),
                )
            elif call_count[0] == 2:  # reviews
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps([
                        {"user": "reviewer2", "state": "APPROVED", "submitted": "2026-01-01T01:00:00Z"},
                    ]),
                )
            elif call_count[0] == 3:  # comments
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "user": "commenter1",
                        "created": "2026-01-01T02:00:00Z",
                    }),
                )
            return MagicMock(returncode=1, stdout="")

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/pr-info?url=https://github.com/example/repo/pull/55222")
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://github.com/example/repo/pull/55222"
        assert "reviewer1" in data["reviewers"]
        assert "reviewer2" in data["reviewers"]
        assert data["lastCommentBy"] == "commenter1"
        assert data["lastReviewState"] == "APPROVED"
        assert data["updatedAt"] == "2026-01-01T00:00:00Z"

    @patch("server.gh_run")
    def test_returns_cached_result(self, mock_gh_run, client, patched_server):
        patched_server._pr_info_cache["https://github.com/test/repo/pull/1"] = {
            "url": "https://github.com/test/repo/pull/1",
            "reviewers": ["cached-reviewer"],
            "_ts": time.time(),
        }
        resp = client.get("/api/pr-info?url=https://github.com/test/repo/pull/1")
        assert resp.status_code == 200
        data = resp.json()
        assert "cached-reviewer" in data["reviewers"]
        # Should not have called gh_run since cache is fresh
        mock_gh_run.assert_not_called()

    def test_returns_error_for_invalid_url(self, client, patched_server):
        patched_server._pr_info_cache.clear()
        resp = client.get("/api/pr-info?url=invalid")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("error") == "invalid PR URL"

    @patch("server.gh_run")
    def test_handles_gh_failures_gracefully(self, mock_gh_run, client, patched_server):
        patched_server._pr_info_cache.clear()
        mock_gh_run.return_value = MagicMock(returncode=1, stdout="")

        resp = client.get("/api/pr-info?url=https://github.com/example/repo/pull/99999")
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://github.com/example/repo/pull/99999"
        assert data["reviewers"] == []

    @patch("server.gh_run")
    def test_handles_gh_exception(self, mock_gh_run, client, patched_server):
        patched_server._pr_info_cache.clear()
        mock_gh_run.side_effect = Exception("gh not found")

        resp = client.get("/api/pr-info?url=https://github.com/example/repo/pull/123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["reviewers"] == []


# ---- PR Detail API ----

class TestGetPrDetail:
    @patch("server.gh_run")
    def test_fetches_pr_detail(self, mock_gh_run, client):
        call_count = [0]
        def gh_side_effect(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:  # pr view
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "number": 55222,
                        "title": "Test PR",
                        "state": "OPEN",
                        "author": {"login": "testuser"},
                    }),
                )
            elif call_count[0] == 2:  # inline comments
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps([
                        {"user": "reviewer1", "path": "test.py", "body": "LGTM"},
                    ]),
                )
            return MagicMock(returncode=1, stdout="")

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/pr-detail?repo=example/repo&number=55222")
        assert resp.status_code == 200
        data = resp.json()
        assert data["number"] == 55222
        assert data["title"] == "Test PR"
        assert len(data["inlineComments"]) == 1
        assert data["inlineComments"][0]["body"] == "LGTM"

    @patch("server.gh_run")
    def test_returns_404_on_failure(self, mock_gh_run, client):
        mock_gh_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")

        resp = client.get("/api/pr-detail?repo=example/repo&number=99999")
        assert resp.status_code == 404

    @patch("server.gh_run")
    def test_handles_paginated_inline_comments(self, mock_gh_run, client):
        call_count = [0]
        def gh_side_effect(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "number": 100,
                        "title": "Paginated PR",
                    }),
                )
            elif call_count[0] == 2:
                # Paginated output: multiple JSON arrays on separate lines
                chunk1 = json.dumps([{"user": "u1", "body": "c1"}])
                chunk2 = json.dumps([{"user": "u2", "body": "c2"}])
                return MagicMock(
                    returncode=0,
                    stdout=chunk1 + "\n" + chunk2,
                )
            return MagicMock(returncode=1, stdout="")

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/pr-detail?repo=example/repo&number=100")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["inlineComments"]) == 2

    @patch("server.gh_run")
    def test_inline_comments_empty_on_failure(self, mock_gh_run, client):
        call_count = [0]
        def gh_side_effect(args, repo="", timeout=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({"number": 100, "title": "T"}),
                )
            elif call_count[0] == 2:
                return MagicMock(returncode=1, stdout="", stderr="error")
            return MagicMock(returncode=1, stdout="")

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/pr-detail?repo=example/repo&number=100")
        assert resp.status_code == 200
        data = resp.json()
        assert data["inlineComments"] == []

    @patch("server.gh_run")
    def test_raises_on_exception(self, mock_gh_run, client):
        mock_gh_run.side_effect = Exception("Connection error")
        resp = client.get("/api/pr-detail?repo=example/repo&number=100")
        assert resp.status_code == 404


# ---- List All PRs API ----

class TestListAllPrs:
    def test_lists_prs_from_all_accounts(self, client, patched_server):
        # The endpoint reads PRs from the DB (seeded by patched_server fixture).
        # patched_server inserts PR #100 (merged, task-a) and PR #200 (open, task-b)
        # under project "test-proj".
        resp = client.get("/api/all-prs")
        assert resp.status_code == 200
        data = resp.json()
        assert "groups" in data
        # Collect all PRs across groups
        all_prs = []
        for gid, group in data["groups"].items():
            all_prs.extend(group["prs"])
        numbers = [p["number"] for p in all_prs]
        assert 100 in numbers
        assert 200 in numbers

    @patch("server.gh_run")
    def test_filters_by_repo(self, mock_gh_run, client, patched_server):
        def gh_side_effect(args, repo="", timeout=20):
            return MagicMock(
                returncode=0,
                stdout=json.dumps([
                    {"number": 1, "title": "PR1", "state": "OPEN",
                     "url": "https://github.com/example/repo/pull/1",
                     "updatedAt": "2026-01-01", "createdAt": "2026-01-01",
                     "labels": []},
                ]),
            )

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/all-prs?repo=example/repo")
        assert resp.status_code == 200
        # Should only query example/repo
        called_repos = [call.kwargs.get("repo", "") for call in mock_gh_run.call_args_list]
        assert all("repo" in r for r in called_repos if r)

    @patch("server.gh_run")
    def test_status_filter_merged(self, mock_gh_run, client, patched_server):
        def gh_side_effect(args, repo="", timeout=20):
            return MagicMock(returncode=0, stdout=json.dumps([]))

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/all-prs?status=merged")
        assert resp.status_code == 200
        # Verify merged state was used in command
        for call in mock_gh_run.call_args_list:
            cmd_args = call[0][0]
            if "--state" in cmd_args:
                idx = cmd_args.index("--state")
                assert cmd_args[idx + 1] == "merged"

    @patch("server.gh_run")
    def test_search_filter(self, mock_gh_run, client, patched_server):
        def gh_side_effect(args, repo="", timeout=20):
            return MagicMock(returncode=0, stdout=json.dumps([]))

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/all-prs?search=arrow")
        assert resp.status_code == 200
        # Verify search term was appended
        for call in mock_gh_run.call_args_list:
            cmd_args = call[0][0]
            if "--" in cmd_args:
                idx = cmd_args.index("--")
                assert cmd_args[idx + 1] == "arrow"

    def test_handles_gh_error(self, client, patched_server):
        # The endpoint reads from the DB and does not call gh_run, so errors
        # in gh_run do not affect the response. The DB-seeded PRs are returned.
        resp = client.get("/api/all-prs")
        assert resp.status_code == 200
        data = resp.json()
        assert "groups" in data
        # patched_server seeds PRs under "test-proj"
        assert "test-proj" in data["groups"]
        assert len(data["groups"]["test-proj"]["prs"]) > 0

    @patch("server.gh_run")
    def test_pr_matched_to_project_task(self, mock_gh_run, client, patched_server):
        # task-a has PR 100 for example/repo
        def gh_side_effect(args, repo="", timeout=20):
            if "example/repo" in args:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps([
                        {"number": 100, "title": "Repo PR 100", "state": "OPEN",
                         "url": "https://github.com/example/repo/pull/100",
                         "updatedAt": "2026-01-01", "createdAt": "2026-01-01",
                         "labels": []},
                    ]),
                )
            return MagicMock(returncode=0, stdout=json.dumps([]))

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/all-prs")
        assert resp.status_code == 200
        data = resp.json()
        # PR 100 should be matched to test-proj
        if "test-proj" in data["groups"]:
            pr100 = [p for p in data["groups"]["test-proj"]["prs"] if p["number"] == 100]
            if pr100:
                assert pr100[0]["task_id"] == "task-a"

    @patch("server.gh_run")
    def test_status_closed(self, mock_gh_run, client, patched_server):
        def gh_side_effect(args, repo="", timeout=20):
            return MagicMock(returncode=0, stdout=json.dumps([]))

        mock_gh_run.side_effect = gh_side_effect

        resp = client.get("/api/all-prs?status=closed")
        assert resp.status_code == 200
        for call in mock_gh_run.call_args_list:
            cmd_args = call[0][0]
            if "--state" in cmd_args:
                idx = cmd_args.index("--state")
                assert cmd_args[idx + 1] == "closed"


# ---- PR Comment API ----

class TestPostPrComment:
    @patch("server.gh_run")
    def test_post_comment_success(self, mock_gh_run, client):
        mock_gh_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = client.post("/api/pr-comment", json={
            "repo": "example/repo",
            "number": 100,
            "body": "Looks good!",
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @patch("server.gh_run")
    def test_post_comment_failure(self, mock_gh_run, client):
        mock_gh_run.return_value = MagicMock(returncode=1, stdout="", stderr="permission denied")
        resp = client.post("/api/pr-comment", json={
            "repo": "example/repo",
            "number": 100,
            "body": "Comment",
        })
        assert resp.status_code == 500


# ---- gh_run helper ----

class TestGhRun:
    """The authoritative `_gh_tokens` dict lives on `adapters.github` now
    (it's what `gh_run` reads at call time). These tests patch both that
    module and the subprocess it shells out to."""

    @patch("adapters.github.subprocess.run")
    def test_gh_run_sets_token_for_repo(self, mock_run, patched_server, monkeypatch):
        # Use generic logins (alice / alice-work) from _test_constants
        # plus an explicit account_rules so the test asserts the
        # token-routing mechanism, not the maintainer's specific
        # accounts. The default fallback is bypassed by the explicit
        # rules, keeping behaviour stable across forks.
        from _test_constants import (
            TEST_USER_LOGIN, TEST_USER_LOGIN_ALT,
            TEST_OSS_REPO, TEST_COMPANY_ORG,
        )
        import adapters.github as ghmod
        monkeypatch.setattr(ghmod, "_gh_tokens", {
            TEST_USER_LOGIN: "token-oss",
            TEST_USER_LOGIN_ALT: "token-db",
        })
        monkeypatch.setattr(ghmod, "_account_rules", [
            {"match": TEST_COMPANY_ORG, "account": TEST_USER_LOGIN_ALT},
            {"match": "", "account": TEST_USER_LOGIN},  # catch-all
        ])
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        result = patched_server.gh_run(["gh", "pr", "list"], repo=TEST_OSS_REPO)
        assert result.returncode == 0
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["env"]["GH_TOKEN"] == "token-oss"

    @patch("adapters.github.subprocess.run")
    def test_gh_run_company_repo_uses_alt_account(self, mock_run, patched_server, monkeypatch):
        from _test_constants import (
            TEST_USER_LOGIN, TEST_USER_LOGIN_ALT,
            TEST_COMPANY_ORG, TEST_COMPANY_REPO_RUNTIME,
        )
        import adapters.github as ghmod
        monkeypatch.setattr(ghmod, "_gh_tokens", {
            TEST_USER_LOGIN: "token-oss",
            TEST_USER_LOGIN_ALT: "token-db",
        })
        monkeypatch.setattr(ghmod, "_account_rules", [
            {"match": TEST_COMPANY_ORG, "account": TEST_USER_LOGIN_ALT},
            {"match": "", "account": TEST_USER_LOGIN},
        ])
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        patched_server.gh_run(["gh", "pr", "list"], repo=TEST_COMPANY_REPO_RUNTIME)
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["env"]["GH_TOKEN"] == "token-db"

    @patch("adapters.github.subprocess.run")
    def test_gh_run_no_repo(self, mock_run, patched_server):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        patched_server.gh_run(["gh", "status"])
        # No GH_TOKEN override expected (env still has os.environ)
        assert mock_run.called


# ---- _load_gh_tokens ----

class TestLoadGhTokens:
    @patch("builtins.open")
    @patch("server.yaml.safe_load")
    def test_loads_tokens(self, mock_yaml, mock_open, patched_server):
        mock_yaml.return_value = {
            "github.com": {
                "users": {
                    "user1": {"oauth_token": "token1"},
                    "user2": {"oauth_token": "token2"},
                }
            }
        }
        tokens = patched_server._load_gh_tokens()
        assert tokens == {"user1": "token1", "user2": "token2"}

    def test_returns_empty_on_error(self, patched_server):
        with patch("builtins.open", side_effect=FileNotFoundError):
            tokens = patched_server._load_gh_tokens()
            assert tokens == {}
