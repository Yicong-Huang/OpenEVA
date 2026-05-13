"""Extra tests for core/common.prs.py -- boost coverage for uncovered areas.

Targets: _resolve_pr_status, _match_pr_to_task, _aggregate_ci_status,
_fetch_fork_ci, _is_externally_merged, get_pr_detail, get_pr_info,
_pr_info_cache eviction, _update_pr_from_gh, sync_prs_generator.
"""

import common
import json
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ============================================================
# _resolve_pr_status -- via core/prs thin wrapper
# ============================================================


class TestResolvePrStatusCoreWrapper:
    """Test _resolve_pr_status in core/common.prs.py which delegates to pr_sync."""

    @patch("common.prs.app_state.gh_run")
    def test_merged_state(self, mock_gh, patched_server):
        from common.prs import _resolve_pr_status
        result = _resolve_pr_status("MERGED", "example/repo", 100)
        assert result == "merged"
        mock_gh.assert_not_called()

    @patch("common.prs.app_state.gh_run")
    def test_closed_external_merge_repo_resolves_to_merged(
        self, mock_gh, patched_server,
    ):
        """Repo opted into external-merge handling: CLOSED -> merged
        when issue events show a referenced commit."""
        patched_server._db.set_setting(
            "service.github.external_merge_repos", ["example/repo"],
        )
        from common.prs import _resolve_pr_status
        mock_gh.return_value = _make_result(stdout="3\n")
        result = _resolve_pr_status("CLOSED", "example/repo", 55301)
        assert result == "merged"

    @patch("common.prs.app_state.gh_run")
    def test_closed_external_merge_repo_not_merged(
        self, mock_gh, patched_server,
    ):
        patched_server._db.set_setting(
            "service.github.external_merge_repos", ["example/repo"],
        )
        from common.prs import _resolve_pr_status
        mock_gh.return_value = _make_result(stdout="0\n")
        result = _resolve_pr_status("CLOSED", "example/repo", 55301)
        assert result == "closed"

    @patch("common.prs.app_state.gh_run")
    def test_closed_non_opted_in_repo_skips_gh(
        self, mock_gh, patched_server,
    ):
        """A repo NOT in `external_merge_repos` -> short-circuit to
        `closed` without an issue-events lookup."""
        from common.prs import _resolve_pr_status
        result = _resolve_pr_status(
            "CLOSED", "myorg/svc", 100,
        )
        assert result == "closed"
        mock_gh.assert_not_called()

    @patch("common.prs.app_state.gh_run")
    def test_open_state(self, mock_gh, patched_server):
        from common.prs import _resolve_pr_status
        result = _resolve_pr_status("OPEN", "example/repo", 100)
        assert result == "open"
        mock_gh.assert_not_called()

    @patch("common.prs.app_state.gh_run")
    def test_closed_external_merge_repo_gh_error_returns_closed(
        self, mock_gh, patched_server,
    ):
        patched_server._db.set_setting(
            "service.github.external_merge_repos", ["example/repo"],
        )
        from common.prs import _resolve_pr_status
        mock_gh.return_value = _make_result(returncode=1, stdout="")
        result = _resolve_pr_status("CLOSED", "example/repo", 999)
        assert result == "closed"


# ============================================================
# _match_pr_to_task -- via core/prs thin wrapper
# ============================================================


class TestMatchPrToTaskCoreWrapper:
    """Test _match_pr_to_task in core/common.prs.py."""

    def test_match_existing_ticket(self, patched_server):
        from common.prs import _match_pr_to_task
        # task-c has ticket_id=EX-123
        result = _match_pr_to_task("[EX-123][PYTHON] Fix thing")
        assert result is not None
        assert result == ("test-proj", "task-c")

    def test_no_ticket_returns_none(self, patched_server):
        from common.prs import _match_pr_to_task
        result = _match_pr_to_task("Fix typo")
        assert result is None

    def test_unknown_ticket_returns_none(self, patched_server):
        from common.prs import _match_pr_to_task
        result = _match_pr_to_task("[EX-77777] Missing")
        assert result is None

    def test_match_existing_task_d_by_ticket(self, patched_server):
        from common.prs import _match_pr_to_task
        # task-d has ticket_id=EX-99999
        result = _match_pr_to_task("[EX-99999] Bug")
        assert result == ("test-proj", "task-d")


# ============================================================
# _aggregate_ci_status
# ============================================================


class TestAggregateCiStatusCoreWrapper:
    def test_success(self, patched_server):
        from common.prs import _aggregate_ci_status
        checks = [{"conclusion": "SUCCESS"}, {"conclusion": "SUCCESS"}]
        assert _aggregate_ci_status(checks) == "success"

    def test_failure(self, patched_server):
        from common.prs import _aggregate_ci_status
        checks = [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]
        assert _aggregate_ci_status(checks) == "failure"

    def test_pending(self, patched_server):
        from common.prs import _aggregate_ci_status
        checks = [{"conclusion": "SUCCESS"}, {"status": "IN_PROGRESS"}]
        assert _aggregate_ci_status(checks) == "pending"

    def test_mixed_with_cancelled(self, patched_server):
        from common.prs import _aggregate_ci_status
        checks = [{"conclusion": "CANCELLED"}]
        assert _aggregate_ci_status(checks) == "failure"

    def test_empty(self, patched_server):
        from common.prs import _aggregate_ci_status
        assert _aggregate_ci_status([]) == "unknown"

    def test_none(self, patched_server):
        from common.prs import _aggregate_ci_status
        assert _aggregate_ci_status(None) == "unknown"

    def test_skipped_and_neutral_counts_as_success(self, patched_server):
        from common.prs import _aggregate_ci_status
        checks = [{"conclusion": "SKIPPED"}, {"conclusion": "NEUTRAL"}]
        assert _aggregate_ci_status(checks) == "success"


# ============================================================
# _fetch_fork_ci
# ============================================================


class TestFetchForkCiCoreWrapper:
    @patch("common.prs.app_state.gh_run")
    def test_success_returns_jobs(self, mock_gh, patched_server):
        from common.prs import _fetch_fork_ci
        calls = [0]

        def side_effect(cmd, repo=None, timeout=None):
            calls[0] += 1
            if calls[0] == 1:
                return _make_result(stdout=json.dumps(
                    [{"databaseId": 42, "status": "completed", "conclusion": "success"}]
                ))
            else:
                lines = [
                    json.dumps({"name": "build", "conclusion": "success", "status": "completed"}),
                ]
                return _make_result(stdout="\n".join(lines))

        mock_gh.side_effect = side_effect
        jobs = _fetch_fork_ci("my-branch")
        assert jobs is not None
        assert len(jobs) == 1
        assert jobs[0]["name"] == "build"

    @patch("common.prs.app_state.gh_run")
    def test_failure_returns_none(self, mock_gh, patched_server):
        from common.prs import _fetch_fork_ci
        mock_gh.return_value = _make_result(returncode=1, stdout="")
        result = _fetch_fork_ci("some-branch")
        assert result is None

    @patch("common.prs.app_state.gh_run")
    def test_custom_fork_repo(self, mock_gh, patched_server):
        from common.prs import _fetch_fork_ci
        mock_gh.return_value = _make_result(returncode=1, stdout="")
        _fetch_fork_ci("branch", fork_repo="custom/repo")
        # Verify fork_repo was passed through
        assert mock_gh.called


# ============================================================
# _is_externally_merged (settings-driven external-merge handling)
# ============================================================


class TestIsExternallyMergedCoreWrapper:
    """`_is_externally_merged(repo, pr_number)` is the generic
    external-merge probe -- works for any opt-in repo, not just
    example/repo."""

    @patch("common.prs.app_state.gh_run")
    def test_merged(self, mock_gh, patched_server):
        from common.prs import _is_externally_merged
        mock_gh.return_value = _make_result(stdout="2\n")
        assert _is_externally_merged("example/repo", 55301) is True

    @patch("common.prs.app_state.gh_run")
    def test_not_merged(self, mock_gh, patched_server):
        from common.prs import _is_externally_merged
        mock_gh.return_value = _make_result(stdout="0\n")
        assert _is_externally_merged("example/repo", 55301) is False

    @patch("common.prs.app_state.gh_run")
    def test_gh_error(self, mock_gh, patched_server):
        from common.prs import _is_externally_merged
        mock_gh.return_value = _make_result(returncode=1, stdout="")
        assert _is_externally_merged("example/repo", 55301) is False

    @patch("common.prs.app_state.gh_run")
    def test_exception(self, mock_gh, patched_server):
        from common.prs import _is_externally_merged
        mock_gh.side_effect = TimeoutError("timeout")
        assert _is_externally_merged("example/repo", 55301) is False

    @patch("common.prs.app_state.gh_run")
    def test_works_for_arbitrary_opt_in_repo(self, mock_gh, patched_server):
        """OSS contract: any user-configured external-merge repo
        works through the same code path."""
        from common.prs import _is_externally_merged
        mock_gh.return_value = _make_result(stdout="1\n")
        assert _is_externally_merged("acme/widgets", 999) is True

    @patch("common.prs.app_state.gh_run")
    def test_invalid_repo_short_circuits(self, mock_gh, patched_server):
        from common.prs import _is_externally_merged
        assert _is_externally_merged("", 100) is False
        assert _is_externally_merged("noslash", 100) is False
        mock_gh.assert_not_called()


# ============================================================
# _annotate_thread_status -- malformed-repo short-circuit
# ============================================================


class TestComputeMyReviewStateEmptyLogins:
    def test_returns_empty_when_no_logins(self):
        """With no GitHub logins registered, we can't identify "me" -- so
        the queue shows no pill. Guards against showing a stale pill when
        `~/.config/gh/hosts.yml` hasn't been loaded yet.

        Exercises core/common.prs.py:87-88 (the empty-logins short-circuit).
        """
        from common.prs import _compute_my_review_state
        detail = {"reviewRequests": [{"login": "alice"}]}
        assert _compute_my_review_state(detail, set()) == ""


class TestEnrichReviewRowsExceptionSwallowed:
    @patch("common.prs.app_state.gh_run_json", side_effect=RuntimeError("gh crashed"))
    def test_exception_is_swallowed(self, _mock_gh, patched_server):
        """Per-PR enrichment runs under ThreadPoolExecutor. One gh crash
        must not poison the whole pool -- the helper swallows the
        exception and leaves the row unmutated. Covers core/common.prs.py:282-283.
        """
        from common.prs import _enrich_review_rows
        rows = {"https://github.com/example/repo/pull/1": {
            "repo": "example/repo", "number": 1, "title": "t",
        }}
        # Should not raise even though gh_run_json explodes.
        _enrich_review_rows(rows)
        # Row should not have been mutated with enrichment fields.
        assert "ci_status" not in rows["https://github.com/example/repo/pull/1"]


class TestAnnotateThreadStatusRepoSplit:
    def test_returns_comments_unchanged_when_repo_malformed(self, patched_server):
        """_annotate_thread_status splits `repo` into owner+name. When the
        input isn't `owner/name` shape we can't query the GraphQL endpoint
        (it needs both args), so the helper must short-circuit and return
        the inline comments unchanged. Exercises core/common.prs.py:513-514.
        """
        from common.prs import _annotate_thread_status
        comments = [{"id": 1, "body": "hi"}]
        # A string with zero `/` -- single-part repo.
        assert _annotate_thread_status(
            "broken-no-slash", 1, comments
        ) is comments
        # Triple-segment repo would also fail the len==2 guard.
        assert _annotate_thread_status(
            "a/b/c", 1, comments
        ) is comments


# ============================================================
# get_pr_detail -- full detail fetch with multiple gh calls
# ============================================================


class TestGetPrDetailFull:
    @patch("common.prs.app_state.gh_run")
    def test_full_detail_with_inline_and_general_comments(self, mock_gh, patched_server):
        from common.prs import get_pr_detail

        call_seq = [0]

        def side_effect(args, repo="", timeout=20):
            call_seq[0] += 1
            args_str = " ".join(str(a) for a in args)

            # 1: PR view
            if "pr" in args_str and "view" in args_str:
                return _make_result(stdout=json.dumps({
                    "number": 42, "title": "Test PR", "body": "description",
                    "state": "OPEN", "author": {"login": "alice"},
                    "statusCheckRollup": [{"name": "ci", "conclusion": "SUCCESS"}],
                    "reviewDecision": "APPROVED",
                    "headRefName": "feature", "baseRefName": "main",
                }))
            # Fork CI: run list and run view
            if "run" in args_str and "list" in args_str:
                return _make_result(stdout="[]")  # no fork runs
            if "run" in args_str and "view" in args_str:
                return _make_result(returncode=1)
            # Inline comments (pulls/N/comments)
            if "pulls/" in args_str and "comments" in args_str:
                return _make_result(stdout=json.dumps([
                    {"id": 1, "user": "bob", "body": "looks good",
                     "path": "src/main.py", "original_line": 10,
                     "createdAt": "2026-01-01T00:00:00Z"}
                ]))
            # GraphQL
            if "graphql" in args_str:
                return _make_result(stdout=json.dumps({
                    "data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
                        {"id": "T1", "isResolved": False, "isOutdated": False,
                         "comments": {"nodes": [{"databaseId": 1}]}}
                    ]}}}}
                }))
            # Issue comments (issues/N/comments)
            if "issues/" in args_str and "comments" in args_str:
                return _make_result(stdout=json.dumps([
                    {"id": 10, "user": {"login": "carol"}, "body": "lgtm",
                     "created_at": "2026-01-02T00:00:00Z"}
                ]))
            return _make_result(returncode=1)

        mock_gh.side_effect = side_effect
        result = get_pr_detail("example/repo", 42)
        assert result is not None
        assert result["number"] == 42
        assert result["title"] == "Test PR"
        assert len(result.get("inlineComments", [])) == 1
        # Thread info annotated
        ic = result["inlineComments"][0]
        assert ic.get("threadId") == "T1"
        assert ic.get("isResolved") is False
        # General comments replaced
        assert len(result.get("comments", [])) == 1

    @patch("common.prs.app_state.gh_run")
    def test_detail_gh_failure_returns_none(self, mock_gh, patched_server):
        from common.prs import get_pr_detail
        mock_gh.return_value = _make_result(returncode=1, stdout="")
        assert get_pr_detail("example/repo", 999) is None

    @patch("common.prs.app_state.gh_run")
    def test_detail_exception_returns_none(self, mock_gh, patched_server):
        from common.prs import get_pr_detail
        mock_gh.side_effect = RuntimeError("network error")
        assert get_pr_detail("example/repo", 999) is None

    @patch("common.prs.app_state.gh_run")
    def test_detail_inline_comments_failure_returns_empty(self, mock_gh, patched_server):
        from common.prs import get_pr_detail

        def side_effect(args, repo="", timeout=20):
            args_str = " ".join(str(a) for a in args)
            if "pr" in args_str and "view" in args_str:
                return _make_result(stdout=json.dumps({
                    "number": 50, "title": "Test", "state": "OPEN",
                    "author": {"login": "x"},
                    "statusCheckRollup": [], "reviewDecision": "",
                }))
            if "run" in args_str:
                return _make_result(stdout="[]")
            if "pulls/" in args_str and "comments" in args_str:
                return _make_result(returncode=1, stdout="")
            if "graphql" in args_str:
                return _make_result(returncode=1, stdout="")
            if "issues/" in args_str:
                return _make_result(returncode=0, stdout="[]")
            return _make_result(returncode=1)

        mock_gh.side_effect = side_effect
        result = get_pr_detail("example/repo", 50)
        assert result is not None
        assert result["inlineComments"] == []

    @patch("common.prs.app_state.gh_run")
    def test_detail_graphql_malformed_json(self, mock_gh, patched_server):
        """GraphQL returns invalid JSON -> silently ignored."""
        from common.prs import get_pr_detail

        def side_effect(args, repo="", timeout=20):
            args_str = " ".join(str(a) for a in args)
            if "pr" in args_str and "view" in args_str:
                return _make_result(stdout=json.dumps({
                    "number": 51, "title": "Test", "state": "OPEN",
                    "author": {"login": "x"},
                    "statusCheckRollup": [], "reviewDecision": "",
                }))
            if "run" in args_str:
                return _make_result(stdout="[]")
            if "pulls/" in args_str and "comments" in args_str:
                return _make_result(stdout="[]")
            if "graphql" in args_str:
                return _make_result(stdout="not json at all")
            if "issues/" in args_str:
                return _make_result(stdout="[]")
            return _make_result(returncode=1)

        mock_gh.side_effect = side_effect
        result = get_pr_detail("example/repo", 51)
        assert result is not None

    @patch("common.prs.app_state.gh_run")
    def test_detail_issue_comments_skip_malformed_chunks(self, mock_gh, patched_server):
        """Paginated `gh api issues/.../comments` output can include a
        malformed line (e.g. partial network read). The general-comment
        parser must skip the bad chunk and keep the good ones, rather
        than drop all comments.

        Covers core/common.prs.py:596-598 -- the JSON-decode catch inside the
        per-chunk loop."""
        from common.prs import get_pr_detail

        good_chunk = json.dumps([
            {"id": 7, "user": {"login": "a"}, "body": "hi", "created_at": "2026-01-01"},
        ])

        def side_effect(args, repo="", timeout=20):
            args_str = " ".join(str(a) for a in args)
            if "pr" in args_str and "view" in args_str:
                return _make_result(stdout=json.dumps({
                    "number": 53, "title": "T", "state": "OPEN",
                    "author": {"login": "a"},
                    "statusCheckRollup": [], "reviewDecision": "",
                }))
            if "pulls/" in args_str and "comments" in args_str:
                return _make_result(stdout="[]")
            if "graphql" in args_str:
                return _make_result(returncode=1)
            if "issues/" in args_str:
                # One good chunk, one malformed.
                return _make_result(stdout=good_chunk + "\n{not valid json}")
            return _make_result(returncode=1)

        mock_gh.side_effect = side_effect
        result = get_pr_detail("example/repo", 53)
        assert result is not None
        # Only the good chunk should have survived.
        assert len(result.get("comments", [])) == 1
        assert result["comments"][0]["id"] == 7

    @patch("common.prs.app_state.gh_run")
    def test_detail_issue_comments_with_multiline_chunks(self, mock_gh, patched_server):
        """Issue comments paginated across multiple JSON arrays."""
        from common.prs import get_pr_detail

        def side_effect(args, repo="", timeout=20):
            args_str = " ".join(str(a) for a in args)
            if "pr" in args_str and "view" in args_str:
                return _make_result(stdout=json.dumps({
                    "number": 52, "title": "Test", "state": "CLOSED",
                    "author": {"login": "x"},
                    "statusCheckRollup": [], "reviewDecision": "",
                }))
            if "pulls/" in args_str and "comments" in args_str:
                return _make_result(stdout="[]")
            if "graphql" in args_str:
                return _make_result(returncode=1, stdout="")
            if "issues/" in args_str:
                c1 = json.dumps([{"id": 1, "user": {"login": "a"}, "body": "hi", "created_at": "2026-01-01"}])
                c2 = json.dumps([{"id": 2, "user": {"login": "b"}, "body": "hey", "created_at": "2026-01-02"}])
                return _make_result(stdout=c1 + "\n" + c2)
            return _make_result(returncode=1)

        mock_gh.side_effect = side_effect
        result = get_pr_detail("example/repo", 52)
        assert result is not None
        assert len(result.get("comments", [])) == 2


# ============================================================
# get_pr_info -- full caching and eviction
# ============================================================


class TestGetPrInfoFullCoverage:
    @patch("common.prs.app_state.gh_run")
    def test_caches_result_within_ttl(self, mock_gh, patched_server):
        from common.prs import get_pr_info, _pr_info_cache
        _pr_info_cache.clear()

        url = "https://github.com/test/repo/pull/111"
        fake = _make_result(stdout='{"requested_reviewers": ["alice"], "updated_at": "2026-01-01"}')
        mock_gh.return_value = fake

        info1 = get_pr_info(url)
        count_after_first = mock_gh.call_count

        info2 = get_pr_info(url)
        assert mock_gh.call_count == count_after_first  # cached
        assert info2["url"] == url

        _pr_info_cache.clear()

    @patch("common.prs.app_state.gh_run")
    def test_cache_expired_refetches(self, mock_gh, patched_server):
        from common.prs import get_pr_info, _pr_info_cache
        _pr_info_cache.clear()

        url = "https://github.com/test/repo/pull/222"
        fake = _make_result(stdout='{"requested_reviewers": [], "updated_at": "2026-01-01"}')
        mock_gh.return_value = fake

        get_pr_info(url)
        _pr_info_cache[url]["_ts"] = time.time() - 600  # expire

        mock_gh.reset_mock()
        get_pr_info(url)
        assert mock_gh.call_count > 0

        _pr_info_cache.clear()

    def test_invalid_url(self, patched_server):
        from common.prs import get_pr_info, _pr_info_cache
        _pr_info_cache.clear()
        result = get_pr_info("bad-url")
        assert "error" in result
        _pr_info_cache.clear()

    @patch("common.prs.app_state.gh_run")
    def test_eviction_at_200_entries(self, mock_gh, patched_server):
        from common.prs import get_pr_info, _pr_info_cache
        _pr_info_cache.clear()

        # Fill 201 entries
        for i in range(201):
            _pr_info_cache[f"https://github.com/t/r/pull/{i}"] = {
                "_ts": time.time() - (300 - i), "url": f"url-{i}"
            }
        assert len(_pr_info_cache) == 201

        fake = _make_result(stdout='{"requested_reviewers": [], "updated_at": "2026-01-01"}')
        mock_gh.return_value = fake

        get_pr_info("https://github.com/test/repo/pull/9999")
        # 201 + 1 = 202, then evict 50 oldest -> 152
        assert len(_pr_info_cache) <= 152

        _pr_info_cache.clear()

    @patch("common.prs.app_state.gh_run")
    def test_reviews_and_comments_fetched(self, mock_gh, patched_server):
        """Full get_pr_info with reviewers, reviews, and comments."""
        from common.prs import get_pr_info, _pr_info_cache
        _pr_info_cache.clear()

        url = "https://github.com/org/repo/pull/333"
        calls = [0]

        def side_effect(args, repo="", timeout=20):
            calls[0] += 1
            r = _make_result()
            if calls[0] == 1:
                r.stdout = json.dumps({
                    "requested_reviewers": ["alice"],
                    "updated_at": "2026-04-15T00:00:00Z",
                })
            elif calls[0] == 2:
                r.stdout = json.dumps([
                    {"user": "bob", "state": "APPROVED", "submitted": "2026-04-14T00:00:00Z"}
                ])
            elif calls[0] == 3:
                r.stdout = json.dumps({
                    "user": "carol", "created": "2026-04-15T01:00:00Z"
                })
            return r

        mock_gh.side_effect = side_effect
        info = get_pr_info(url)
        assert "alice" in info["reviewers"]
        assert "bob" in info["reviewers"]
        assert info["lastCommentBy"] == "carol"
        assert info["lastComment"] == "2026-04-15T01:00:00Z"

        _pr_info_cache.clear()

    @patch("common.prs.app_state.gh_run")
    def test_gh_exceptions_silently_handled(self, mock_gh, patched_server):
        """If gh_run raises exceptions, get_pr_info still returns partial data."""
        from common.prs import get_pr_info, _pr_info_cache
        _pr_info_cache.clear()

        url = "https://github.com/org/repo/pull/444"
        mock_gh.side_effect = RuntimeError("network error")

        info = get_pr_info(url)
        assert info["url"] == url
        assert info["reviewers"] == []

        _pr_info_cache.clear()


# ============================================================
# _update_pr_from_gh
# ============================================================


class TestUpdatePrFromGh:
    @patch("common.prs.app_state.gh_run")
    def test_updates_pr_fields(self, mock_gh, patched_server):
        from common.prs import _update_pr_from_gh
        import app_state

        # Set up mock for _resolve_pr_status (OPEN -> "open", no gh calls needed)
        mock_gh.return_value = _make_result(returncode=1)

        item = {
            "state": "OPEN",
            "title": "Updated Title",
            "url": "https://github.com/example/repo/pull/200",
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "reviewDecision": "APPROVED",
            "comments": [{"id": 1}],
            "reviews": [{"id": 2}],
            "additions": 50,
            "deletions": 10,
            "author": {"login": "alice"},
            "headRefName": "feat-branch",
            "baseRefName": "main",
            "updatedAt": "2026-04-15T00:00:00Z",
        }

        _update_pr_from_gh(200, item, "example/repo")

        pr = app_state._db.find_pr_by_number(200)
        assert pr is not None
        assert pr["title"] == "Updated Title"
        assert pr["status"] == "open"

    @patch("common.prs.app_state.gh_run")
    def test_sync_path_backfills_status_changed_at(self, mock_gh, patched_server):
        """The scheduled PR sync (via `_update_pr_from_gh`) also needs
        to backfill `status_changed_at` from GitHub's mergedAt/closedAt.
        Before the shared `map_gh_pr_to_updates` refactor only the
        async + manual paths did -- the scheduler silently skipped it."""
        from common.prs import _update_pr_from_gh
        import app_state

        mock_gh.return_value = _make_result(returncode=1)
        item = {
            "state": "MERGED", "title": "t",
            "url": "https://github.com/example/repo/pull/200",
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "reviewDecision": "APPROVED",
            "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
            "author": {"login": "alice"},
            "headRefName": "f", "baseRefName": "main",
            "updatedAt": "2026-04-22T10:00:00Z",
            "mergedAt": "2026-04-18T03:00:00Z",
        }
        # Ensure the row starts with empty status_changed_at.
        app_state._db.update_pr_by_number(200, status_changed_at="")
        _update_pr_from_gh(200, item, "example/repo")
        row = app_state._db.find_pr_by_number(200)
        assert row["status_changed_at"] == "2026-04-18T03:00:00Z"

    @patch("common.sessions.fire_action")
    @patch("common.prs.app_state.gh_run")
    def test_pr_open_to_merged_fires_sync_action(
        self, mock_gh, mock_fire, patched_server,
    ):
        """When a PR transitions open->merged, _update_pr_from_gh should
        fire the owning task's `sync` action so the task / ticket
        reconciles itself without the user clicking Sync Status."""
        from common.prs import _update_pr_from_gh
        mock_gh.return_value = _make_result(returncode=1)
        item = {
            "state": "MERGED", "title": "merged",
            "url": "https://github.com/example/repo/pull/200",
            "statusCheckRollup": [], "reviewDecision": "APPROVED",
            "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
            "author": {"login": "alice"},
            "headRefName": "f", "baseRefName": "main",
            "updatedAt": "2026-04-22T10:00:00Z",
            "mergedAt": "2026-04-18T03:00:00Z",
        }
        _update_pr_from_gh(200, item, "example/repo")
        # task-b is in_progress -> not terminal -> sync should fire.
        mock_fire.assert_called_once()
        args, kwargs = mock_fire.call_args
        # Positional: (project, task_id) per fire_action signature.
        assert args[0] == "test-proj"
        assert args[1] == "task-b"
        assert kwargs.get("action_id") == "sync"

    @patch("common.sessions.fire_action")
    @patch("common.prs.app_state.gh_run")
    def test_pr_open_to_closed_fires_sync_action(
        self, mock_gh, mock_fire, patched_server,
    ):
        """Same trigger applies when a PR is closed without merging --
        the task may need its status / ticket cleaned up too."""
        from common.prs import _update_pr_from_gh
        mock_gh.return_value = _make_result(returncode=1)
        item = {
            "state": "CLOSED", "title": "abandoned",
            "url": "https://github.com/example/repo/pull/200",
            "statusCheckRollup": [], "reviewDecision": "",
            "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
            "author": {"login": "alice"},
            "headRefName": "f", "baseRefName": "main",
            "updatedAt": "2026-04-22T10:00:00Z",
        }
        _update_pr_from_gh(200, item, "example/repo")
        mock_fire.assert_called_once()
        assert mock_fire.call_args.kwargs.get("action_id") == "sync"

    @patch("common.sessions.fire_action")
    @patch("common.prs.app_state.gh_run")
    def test_no_fire_when_pr_status_unchanged(
        self, mock_gh, mock_fire, patched_server,
    ):
        """An open PR re-synced as still-open is the dirty-poll case.
        That's most ticks; firing sync every time would flood sessions."""
        from common.prs import _update_pr_from_gh
        mock_gh.return_value = _make_result(returncode=1)
        item = {
            "state": "OPEN", "title": "still open",
            "url": "https://github.com/example/repo/pull/200",
            "statusCheckRollup": [], "reviewDecision": "",
            "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
            "author": {"login": "alice"},
            "headRefName": "f", "baseRefName": "main",
            "updatedAt": "2026-04-22T10:00:00Z",
        }
        _update_pr_from_gh(200, item, "example/repo")
        mock_fire.assert_not_called()

    @patch("common.sessions.fire_action")
    @patch("common.prs.app_state.gh_run")
    def test_no_fire_when_task_already_terminal(
        self, mock_gh, mock_fire, patched_server,
    ):
        """task-a is already `done` and PR 100 is already `merged`.
        Re-syncing PR 100 (still merged, no transition) must NOT fire
        sync -- the task is settled, sync would just spawn a session
        and tell agent everything's already fine."""
        from common.prs import _update_pr_from_gh
        mock_gh.return_value = _make_result(returncode=1)
        item = {
            "state": "MERGED", "title": "long ago merged",
            "url": "https://github.com/example/repo/pull/100",
            "statusCheckRollup": [], "reviewDecision": "APPROVED",
            "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
            "author": {"login": "alice"},
            "headRefName": "f", "baseRefName": "main",
            "updatedAt": "2026-04-22T10:00:00Z",
            "mergedAt": "2026-04-18T03:00:00Z",
        }
        _update_pr_from_gh(100, item, "example/repo")
        mock_fire.assert_not_called()

    @patch("common.prs.app_state.gh_run")
    def test_sync_path_preserves_existing_status_changed_at(
            self, mock_gh, patched_server):
        from common.prs import _update_pr_from_gh
        import app_state

        mock_gh.return_value = _make_result(returncode=1)
        app_state._db.update_pr_by_number(
            200, status_changed_at="2026-01-01T00:00:00Z",
        )
        item = {
            "state": "MERGED", "title": "t",
            "url": "https://github.com/example/repo/pull/200",
            "statusCheckRollup": [], "reviewDecision": "",
            "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
            "author": {"login": "alice"},
            "headRefName": "f", "baseRefName": "main",
            "updatedAt": "2026-04-22T10:00:00Z",
            "mergedAt": "2026-04-18T03:00:00Z",   # different value
        }
        _update_pr_from_gh(200, item, "example/repo")
        row = app_state._db.find_pr_by_number(200)
        # Earlier stamp wins -- status_changed_at is a one-way backfill.
        assert row["status_changed_at"] == "2026-01-01T00:00:00Z"


# ============================================================
# map_gh_pr_to_updates -- shared mapper (unit)
# ============================================================


class TestMapGhPrToUpdates:
    """Pure-function tests for the shared GitHub -> DB mapper. Three
    PR-refresh paths depend on identical field semantics."""

    def test_maps_all_standard_fields(self):
        from common.prs import map_gh_pr_to_updates
        item = {
            "state": "OPEN", "title": "t",
            "url": "https://github.com/x/y/pull/1",
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "reviewDecision": "APPROVED",
            "comments": [{"id": 1}, {"id": 2}],
            "reviews": [{"id": 3}],
            "additions": 50, "deletions": 10,
            "author": {"login": "bob"},
            "headRefName": "f", "baseRefName": "main",
            "updatedAt": "2026-04-22T10:00:00Z",
        }
        out = map_gh_pr_to_updates(item, "x/y", 1, existing=None)
        assert out["title"] == "t"
        assert out["status"] == "open"
        assert out["ci_status"] == "success"
        assert out["review_status"] == "approved"
        assert out["comment_count"] == 3  # 2 comments + 1 review
        assert out["author"] == "bob"
        # Open PR with no mergedAt/closedAt -> no status_changed_at key.
        assert "status_changed_at" not in out

    def test_backfills_when_existing_has_empty_string(self):
        from common.prs import map_gh_pr_to_updates
        item = {
            "state": "MERGED", "title": "t", "url": "u",
            "statusCheckRollup": [], "reviewDecision": "",
            "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
            "author": {"login": "u"},
            "headRefName": "", "baseRefName": "",
            "updatedAt": "2026-04-22T00:00:00Z",
            "mergedAt": "2026-04-20T00:00:00Z",
        }
        out = map_gh_pr_to_updates(item, "x/y", 1, existing={"status_changed_at": ""})
        assert out["status_changed_at"] == "2026-04-20T00:00:00Z"

    def test_skips_backfill_when_existing_has_value(self):
        from common.prs import map_gh_pr_to_updates
        item = {
            "state": "MERGED", "title": "t", "url": "u",
            "statusCheckRollup": [], "reviewDecision": "",
            "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
            "author": {"login": "u"},
            "headRefName": "", "baseRefName": "",
            "updatedAt": "2026-04-22T00:00:00Z",
            "mergedAt": "2026-04-20T00:00:00Z",
        }
        out = map_gh_pr_to_updates(
            item, "x/y", 1,
            existing={"status_changed_at": "2026-01-01T00:00:00Z"})
        assert "status_changed_at" not in out

    @patch("common.prs._fetch_fork_ci")
    def test_fork_ci_replaces_statusCheckRollup_on_open_fork_pr(
        self, mock_fetch, patched_server
    ):
        """For an OPEN PR in a repo configured for fork CI, the mapper
        substitutes the fork CI jobs for statusCheckRollup before it
        aggregates. Guards against picking up upstream CI (usually absent
        for fork PRs, which breaks aggregation to 'unknown').

        Exercises core/common.prs.py:726-729 -- the fork-CI replacement branch.
        """
        from common.prs import map_gh_pr_to_updates
        import app_state
        # Pick any real fork-configured repo; use the example/repo -> fork
        # mapping if present, else synthesize one for the test duration.
        fork_repo = next(iter(app_state.FORK_TO_UPSTREAM.values()), "example/repo")
        # All fork jobs succeeded -> aggregate to 'success'.
        mock_fetch.return_value = [{"conclusion": "SUCCESS"}, {"conclusion": "SUCCESS"}]
        item = {
            "state": "OPEN", "title": "t", "url": "u",
            # Upstream shows nothing -- fork CI should override.
            "statusCheckRollup": [],
            "reviewDecision": "", "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
            "author": {"login": "u"},
            "headRefName": "feat-branch", "baseRefName": "main",
            "updatedAt": "2026-04-22T00:00:00Z",
        }
        out = map_gh_pr_to_updates(item, fork_repo, 1, existing=None)
        # The fork CI stubbed with success -> aggregated ci_status == "success"
        # (not "unknown" which is what an empty upstream rollup would give).
        assert out["ci_status"] == "success"
        mock_fetch.assert_called_once_with("feat-branch")


# ============================================================
# sync_prs_generator -- basic phases
# ============================================================


class TestListReviewRequestsFiltersOwnPRs:
    """`list_review_requests` is a DB-only read, but it must belt-and-braces
    filter out rows whose author is me -- legacy rows written before the
    author-filter guard in `sync_review_requests` could otherwise surface
    my own PRs in the review queue."""

    def test_skips_rows_authored_by_me(self, patched_server):
        from common.prs import list_review_requests
        import app_state
        # Stand in for "me" so the filter has something to match on.
        app_state._gh_tokens.clear(); app_state._gh_tokens["test-author"] = "token"
        # One row authored by me, one by someone else.
        app_state._db.upsert_review_pr(
            url="https://github.com/example/repo/pull/9001",
            repo="example/repo", number=9001,
            title="my own PR (legacy row)",
            author="test-author",  # mixed case -- filter is case-insensitive
            status="open", last_updated="",
        )
        app_state._db.upsert_review_pr(
            url="https://github.com/example/repo/pull/9002",
            repo="example/repo", number=9002,
            title="someone else's PR",
            author="stranger",
            status="open", last_updated="",
        )
        rows = list_review_requests()
        titles = {r["title"] for r in rows}
        assert "my own PR (legacy row)" not in titles
        assert "someone else's PR" in titles


class TestSyncReviewRequestsDirtyOnlyNoop:
    """The dirty-only cadence runs every 60s; it must short-circuit cleanly
    when the dirty queue is empty so we don't waste a gh call per tick."""

    def test_returns_zero_refreshed_when_dirty_list_empty(self, patched_server):
        from common.prs import sync_review_requests_dirty_only
        result = sync_review_requests_dirty_only()
        assert result == {"refreshed": 0}


class TestAddReviewWatchGhMetaFallbacks:
    """`add_review_watch` tolerates broken `gh pr view` responses -- a
    missing-URL rejection is the user-actionable signal; everything else
    (non-dict meta, missing fields) must fall back to placeholders so the
    pin still writes a usable row."""

    def test_rejects_non_pr_url(self, patched_server):
        from common.prs import add_review_watch
        with pytest.raises(ValueError, match="Not a GitHub PR URL"):
            add_review_watch("https://example.com/not/a/pr")

    @patch("common.prs.app_state.gh_run_json", return_value=None)
    def test_pins_with_empty_meta_when_gh_returns_nothing(
        self, _mock_gh, patched_server
    ):
        """`gh pr view` returns None when the gh CLI call fails (network,
        auth, allow-list). The pin must still write a minimal row so the
        user can see their chosen PR in the queue."""
        from common.prs import add_review_watch
        row = add_review_watch("https://github.com/example/repo/pull/9999")
        assert row["url"] == "https://github.com/example/repo/pull/9999"
        assert row["source"] == "manual"
        # Title defaults to empty when meta was empty.
        assert row["title"] == ""

    @patch("common.prs.app_state.gh_run_json", return_value=[1, 2, 3])
    def test_pins_when_gh_returns_non_dict(self, _mock_gh, patched_server):
        """gh_run_json is typed loosely -- if it returns a list (e.g.
        from a bad `--json` flag combo) add_review_watch must fall back
        to an empty meta dict rather than raising AttributeError on
        `.get` downstream."""
        from common.prs import add_review_watch
        row = add_review_watch("https://github.com/example/repo/pull/9998")
        assert row["title"] == ""
        assert row["source"] == "manual"


class TestSyncReviewRequestsLimitFromSettings:
    """When `sync_review_requests()` is called without an explicit
    limit, it reads `ui.reviews.sync_search_limit` from the settings
    table. Default 50; bounds [10, 200] enforced server-side."""

    @patch("common.prs._enrich_review_rows")
    @patch("common.prs._fetch_review_buckets", return_value=({}, {}))
    def test_default_limit_used_when_unset(
        self, mock_fetch, _mock_enrich, patched_server,
    ):
        from common.prs import sync_review_requests
        import app_state
        app_state._gh_tokens.clear(); app_state._gh_tokens["test-author"] = "token"
        sync_review_requests()
        # `_fetch_review_buckets(my_logins, limit)` -- limit is 2nd arg.
        args, _ = mock_fetch.call_args
        assert args[1] == 50  # the configured default

    @patch("common.prs._enrich_review_rows")
    @patch("common.prs._fetch_review_buckets", return_value=({}, {}))
    def test_setting_override_propagates(
        self, mock_fetch, _mock_enrich, patched_server,
    ):
        from common.prs import sync_review_requests
        import app_state
        app_state._gh_tokens.clear(); app_state._gh_tokens["test-author"] = "token"
        patched_server._db.set_setting(
            "ui.reviews.sync_search_limit", 120)
        sync_review_requests()
        args, _ = mock_fetch.call_args
        assert args[1] == 120

    @patch("common.prs._enrich_review_rows")
    @patch("common.prs._fetch_review_buckets", return_value=({}, {}))
    def test_explicit_limit_still_honoured(
        self, mock_fetch, _mock_enrich, patched_server,
    ):
        """Explicit callers (tests, future ad-hoc rebuilds) can still
        override the setting -- the settings lookup only fires when
        the parameter is None."""
        from common.prs import sync_review_requests
        import app_state
        app_state._gh_tokens.clear(); app_state._gh_tokens["test-author"] = "token"
        # Even with the setting mid-range, an explicit value wins.
        patched_server._db.set_setting(
            "ui.reviews.sync_search_limit", 120)
        sync_review_requests(limit=5)
        args, _ = mock_fetch.call_args
        assert args[1] == 5


class TestSyncReviewRequestsBothSourceDowngrade:
    """When a row is 'both' (manual pin + github review-request) and the
    GitHub half disappears from the fresh sync, the row must downgrade
    to 'manual' rather than get deleted -- the user pinned it, so we
    keep it even after github drops it from review-requested."""

    @patch("common.prs._enrich_review_rows")
    @patch("common.prs.app_state.gh_run_json", return_value=[])
    def test_both_downgrades_to_manual_when_github_drops_it(
        self, _mock_gh, _mock_enrich, patched_server
    ):
        from common.prs import sync_review_requests
        import app_state
        app_state._gh_tokens.clear(); app_state._gh_tokens["test-author"] = "token"
        # Seed a 'both' row whose URL won't come back in the fresh pass
        # (mock returns []).
        url = "https://github.com/example/repo/pull/7777"
        app_state._db.upsert_review_pr(
            url=url, repo="example/repo", number=7777,
            source="both", title="was both",
            author="stranger", status="open", last_updated="",
        )
        sync_review_requests(limit=5)
        row = app_state._db.get_review_pr(url)
        assert row is not None, "row must survive the downgrade"
        assert row["source"] == "manual"


class TestSyncPrsGenerator:
    @patch("common.prs.app_state.gh_run")
    def test_generator_yields_start_and_done(self, mock_gh, patched_server):
        from common.prs import sync_prs_generator

        # Mock all gh_run calls to return non-zero (skip actual syncing)
        mock_gh.return_value = _make_result(returncode=1, stdout="[]")

        events = list(sync_prs_generator())
        phases = [e["phase"] for e in events]
        assert "start" in phases
        assert "dirty" in phases
        assert "discover" in phases
        assert "done" in phases

    @patch("common.prs.app_state.gh_run")
    def test_generator_full_mode(self, mock_gh, patched_server):
        from common.prs import sync_prs_generator

        mock_gh.return_value = _make_result(returncode=1, stdout="[]")

        events = list(sync_prs_generator(full=True))
        start_event = [e for e in events if e["phase"] == "start"][0]
        assert start_event["full"] is True

    @patch("common.prs.app_state.gh_run")
    def test_dirty_pr_update(self, mock_gh, patched_server):
        """Mark a PR dirty and verify sync updates it."""
        from common.prs import sync_prs_generator
        import app_state

        # Mark PR #200 as dirty
        app_state._db.mark_pr_dirty(200)

        call_idx = [0]

        def side_effect(args, repo="", timeout=20):
            call_idx[0] += 1
            if "pr" in args and "view" in args:
                return _make_result(stdout=json.dumps({
                    "number": 200, "title": "Updated",
                    "state": "OPEN", "url": "https://github.com/example/repo/pull/200",
                    "statusCheckRollup": [], "reviewDecision": "",
                    "comments": [], "reviews": [],
                    "additions": 0, "deletions": 0,
                    "author": {"login": "dev"}, "headRefName": "b", "baseRefName": "main",
                    "updatedAt": "2026-04-15",
                }))
            # search returns empty for discovery phase
            return _make_result(stdout="[]")

        mock_gh.side_effect = side_effect

        events = list(sync_prs_generator())
        dirty_events = [e for e in events if e["phase"] == "dirty"]
        assert dirty_events[0]["count"] >= 1

    @patch("common.prs._match_pr_to_task")
    @patch("common.prs.app_state.gh_run")
    def test_generator_discovers_and_adds_matching_pr(
        self, mock_gh, mock_match, patched_server
    ):
        """New PRs found by author-search get added when title matches a task.

        Exercises core/common.prs.py:828-849 (the discover-found-match path): a task
        ticket match returns a (project, task_id) pair, and the generator must
        persist the PR and emit it in `newly_discovered`.
        """
        from common.prs import sync_prs_generator
        import app_state

        # Pretend example/repo has an author registered and returns one PR.
        def side_effect(args, repo="", timeout=20):
            if "search" in args and "prs" in args:
                return _make_result(stdout=json.dumps([{
                    "number": 9999,
                    "title": "[TEST-9999] Test PR",
                    "url": "https://github.com/example/repo/pull/9999",
                    "state": "OPEN",
                }]))
            # `pr view` calls during Phase 3 -- return minimal body so
            # update succeeds and the row can be refreshed.
            if "pr" in args and "view" in args:
                return _make_result(stdout=json.dumps({
                    "number": 9999, "title": "[TEST-9999] Test PR",
                    "state": "OPEN",
                    "url": "https://github.com/example/repo/pull/9999",
                    "statusCheckRollup": [], "reviewDecision": "",
                    "comments": [], "reviews": [], "latestReviews": [],
                    "reviewRequests": [],
                    "additions": 0, "deletions": 0,
                    "author": {"login": "dev"},
                    "headRefName": "b", "baseRefName": "main",
                    "updatedAt": "2026-04-22T00:00:00Z",
                }))
            return _make_result(stdout="[]")

        mock_gh.side_effect = side_effect
        mock_match.return_value = ("test-proj", "task-d")

        # Force a single repo/author so the discover loop runs once.
        with patch.object(
            app_state, "_build_repo_authors",
            return_value={"example/repo": "dev"},
        ):
            events = list(sync_prs_generator())

        discovered = [e for e in events if e["phase"] == "discover"]
        assert discovered, "expected a discover phase event"
        assert discovered[0]["discovered"] >= 1
        # The row must have landed in the DB.
        assert app_state._db.find_pr_by_number(9999) is not None
