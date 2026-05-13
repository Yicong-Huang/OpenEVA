"""Tests for PR synchronization and CI status helper functions in server.py."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import (
    _aggregate_ci_status,
    _resolve_pr_status,
    _match_pr_to_task,
    _parse_pr_number,
    _build_repo_authors,
)


# ---- _aggregate_ci_status ----

class TestAggregateCiStatus:
    def test_aggregate_ci_empty(self):
        assert _aggregate_ci_status([]) == "unknown"

    def test_aggregate_ci_all_success(self):
        checks = [
            {"conclusion": "SUCCESS"},
            {"conclusion": "success"},
        ]
        assert _aggregate_ci_status(checks) == "success"

    def test_aggregate_ci_has_failure(self):
        checks = [
            {"conclusion": "SUCCESS"},
            {"conclusion": "FAILURE"},
        ]
        assert _aggregate_ci_status(checks) == "failure"

    def test_aggregate_ci_has_pending(self):
        checks = [
            {"conclusion": "SUCCESS"},
            {"conclusion": None, "status": "IN_PROGRESS"},
        ]
        assert _aggregate_ci_status(checks) == "pending"

    def test_aggregate_ci_cancelled_is_failure(self):
        checks = [
            {"conclusion": "CANCELLED"},
        ]
        assert _aggregate_ci_status(checks) == "failure"

    def test_aggregate_ci_timed_out_is_failure(self):
        checks = [
            {"conclusion": "TIMED_OUT"},
        ]
        assert _aggregate_ci_status(checks) == "failure"

    def test_aggregate_ci_mixed_success_skipped(self):
        checks = [
            {"conclusion": "SUCCESS"},
            {"conclusion": "SKIPPED"},
            {"conclusion": "NEUTRAL"},
        ]
        assert _aggregate_ci_status(checks) == "success"

    def test_aggregate_ci_none(self):
        assert _aggregate_ci_status(None) == "unknown"

    def test_aggregate_ci_pending_queued(self):
        checks = [
            {"conclusion": None, "status": "QUEUED"},
        ]
        assert _aggregate_ci_status(checks) == "pending"

    def test_aggregate_ci_failure_beats_pending(self):
        checks = [
            {"conclusion": "FAILURE"},
            {"conclusion": None, "status": "PENDING"},
        ]
        assert _aggregate_ci_status(checks) == "failure"

    # Regression: legacy GitHub StatusContext checks (PR status checks API
    # rather than the newer CheckRun) put the value in `state`, not
    # `conclusion`/`status`. Some CI systems still emit these. The aggregator
    # used to read empty strings and falsely report `success`.
    def test_aggregate_ci_status_context_failure_state(self):
        checks = [
            {"__typename": "StatusContext", "state": "FAILURE"},
            {"__typename": "StatusContext", "state": "SUCCESS"},
        ]
        assert _aggregate_ci_status(checks) == "failure"

    def test_aggregate_ci_status_context_pending_state(self):
        checks = [
            {"__typename": "StatusContext", "state": "PENDING"},
            {"__typename": "StatusContext", "state": "PENDING"},
        ]
        assert _aggregate_ci_status(checks) == "pending"

    def test_aggregate_ci_status_context_all_success_state(self):
        checks = [
            {"__typename": "StatusContext", "state": "SUCCESS"},
            {"__typename": "StatusContext", "state": "SUCCESS"},
        ]
        assert _aggregate_ci_status(checks) == "success"

    def test_aggregate_ci_status_context_error_is_failure(self):
        # GH StatusContext uses ERROR (not FAILURE) for hard errors.
        checks = [{"__typename": "StatusContext", "state": "ERROR"}]
        assert _aggregate_ci_status(checks) == "failure"

    def test_aggregate_ci_mixed_checkrun_and_statuscontext(self):
        # Real-world PRs combine both shapes in statusCheckRollup.
        checks = [
            {"__typename": "CheckRun", "conclusion": "SUCCESS", "status": "COMPLETED"},
            {"__typename": "StatusContext", "state": "FAILURE"},
        ]
        assert _aggregate_ci_status(checks) == "failure"

    # Regression: some CI systems prefix non-merge-blocking checks with
    # `[Non-Blocking]`. A failing non-blocking check shouldn't poison the
    # overall PR status -- the user only cares whether merge is blocked.
    def test_aggregate_ci_ignores_non_blocking_failure(self):
        checks = [
            {"context": "[Non-Blocking]Build-Check", "state": "FAILURE"},
            {"context": "[Blocking]Repo-Compile-Pr", "state": "SUCCESS"},
            {"context": "[Blocking]Bazel-Build-Check", "state": "SUCCESS"},
        ]
        assert _aggregate_ci_status(checks) == "success"

    def test_aggregate_ci_pending_blocking_beats_non_blocking_failure(self):
        # Real-world #211201 case: 1 non-blocking failure + many blocking
        # pending. Should be "pending" -- merge is gated on the blocking
        # checks finishing, the non-blocking failure is noise.
        checks = [
            {"context": "[Non-Blocking]Build-Check", "state": "FAILURE"},
            {"context": "[Blocking]Repo-Compile-Pr", "state": "PENDING"},
            {"context": "[Blocking]Bazel-Build-Check", "state": "PENDING"},
            {"context": "[Blocking]Lint-Check", "state": "SUCCESS"},
        ]
        assert _aggregate_ci_status(checks) == "pending"

    def test_aggregate_ci_blocking_failure_still_failure(self):
        # A blocking failure should override even if non-blocking is success.
        checks = [
            {"context": "[Blocking]Repo-Compile-Pr", "state": "FAILURE"},
            {"context": "[Non-Blocking]Lint-Check", "state": "SUCCESS"},
        ]
        assert _aggregate_ci_status(checks) == "failure"

    def test_aggregate_ci_only_non_blocking_falls_back_to_all(self):
        # Edge case: PR has *only* non-blocking checks. Filtering them all
        # out would report "unknown"; instead we use the full set so the
        # user sees a real status.
        checks = [
            {"context": "[Non-Blocking]Lint-Check", "state": "SUCCESS"},
            {"context": "[Non-Blocking]Style-Check", "state": "SUCCESS"},
        ]
        assert _aggregate_ci_status(checks) == "success"

    # Regression: the priority table says "failure > pending". CANCELLED /
    # TIMED_OUT / STARTUP_FAILURE etc are all terminal broken outcomes.
    # Before the fix, the cancelled/timed-out/stale check ran AFTER the
    # pending check, so `[CANCELLED, PENDING]` wrongly returned "pending"
    # and the UI would keep showing a spinner for a build that had already
    # been aborted.
    def test_aggregate_ci_cancelled_beats_pending(self):
        checks = [
            {"conclusion": "CANCELLED"},
            {"conclusion": None, "status": "PENDING"},
        ]
        assert _aggregate_ci_status(checks) == "failure"

    def test_aggregate_ci_timed_out_beats_pending(self):
        checks = [
            {"conclusion": "TIMED_OUT"},
            {"conclusion": None, "status": "IN_PROGRESS"},
        ]
        assert _aggregate_ci_status(checks) == "failure"

    def test_aggregate_ci_startup_failure_beats_pending(self):
        checks = [
            {"conclusion": "STARTUP_FAILURE"},
            {"conclusion": None, "status": "QUEUED"},
        ]
        assert _aggregate_ci_status(checks) == "failure"

    def test_aggregate_ci_action_required_beats_pending(self):
        # ACTION_REQUIRED means the check is waiting for a human (approve
        # workflow, approve 3rd party). Gates merge -> treat as failure.
        checks = [
            {"conclusion": "ACTION_REQUIRED"},
            {"conclusion": None, "status": "IN_PROGRESS"},
        ]
        assert _aggregate_ci_status(checks) == "failure"


# ---- _resolve_pr_status ----

class TestResolvePrStatus:
    def test_resolve_merged(self):
        assert _resolve_pr_status("MERGED", "example/repo", 12345) == "merged"

    def test_resolve_merged_any_repo(self):
        assert _resolve_pr_status("MERGED", "myorg/svc", 99) == "merged"

    def test_resolve_open(self):
        assert _resolve_pr_status("OPEN", "example/repo", 12345) == "open"

    def test_resolve_open_company(self):
        assert _resolve_pr_status("OPEN", "myorg/svc", 42) == "open"

    def test_resolve_closed_non_oss(self):
        result = _resolve_pr_status("CLOSED", "myorg/svc", 100)
        assert result == "closed"

    def test_resolve_closed_external_merge_repo_merged(self, patched_server):
        """An opt-in external-merge repo (settings-driven) re-checks
        via issue events on CLOSED -> merged when commits found."""
        patched_server._db.set_setting(
            "service.github.external_merge_repos", ["example/repo"],
        )
        with patch("pr_sync.is_externally_merged", return_value=True):
            result = _resolve_pr_status("CLOSED", "example/repo", 55301)
        assert result == "merged"

    def test_resolve_closed_external_merge_repo_not_merged(
        self, patched_server,
    ):
        """Same opt-in repo, no merge commit -> closed."""
        patched_server._db.set_setting(
            "service.github.external_merge_repos", ["example/repo"],
        )
        with patch("pr_sync.is_externally_merged", return_value=False):
            result = _resolve_pr_status("CLOSED", "example/repo", 55301)
        assert result == "closed"

    def test_resolve_closed_skips_external_check_when_repo_not_opted_in(
        self, patched_server,
    ):
        """A repo NOT in `external_merge_repos` short-circuits to
        `closed` without consulting `is_externally_merged`."""
        # Empty list (default) -> no special handling.
        with patch("pr_sync.is_externally_merged",
                   side_effect=AssertionError("should not be called")):
            result = _resolve_pr_status(
                "CLOSED", "example/repo", 55301,
            )
        assert result == "closed"


# ---- _match_pr_to_task ----

class TestMatchPrToTask:
    def test_match_pr_to_task_by_ticket(self, patched_server):
        # task-c in conftest has ticket_id=EX-123
        result = patched_server._match_pr_to_task("[EX-123][PYTHON] Fix something")
        assert result is not None
        project, task_id = result
        assert project == "test-proj"
        assert task_id == "task-c"

    def test_match_pr_to_task_no_ticket_in_title(self, patched_server):
        result = patched_server._match_pr_to_task("Fix typo in README")
        assert result is None

    def test_match_pr_to_task_unknown_ticket_returns_none(self, patched_server):
        result = patched_server._match_pr_to_task("[EX-77777] Missing task")
        assert result is None

    def test_match_pr_to_task_existing_task_d_by_ticket(self, patched_server):
        # EX-99999 is task-d in conftest
        result = patched_server._match_pr_to_task("[EX-99999][BUG] Something")
        assert result is not None
        project, task_id = result
        assert project == "test-proj"
        assert task_id == "task-d"


# ---- _parse_pr_number ----

class TestParsePrNumber:
    """URL-format parser tests. Inputs use generic repo names from
    `_test_constants` so the assertions don't read like a fingerprint
    of the maintainer's setup -- the parser is repo-agnostic."""

    def test_parse_pr_number_valid(self):
        from _test_constants import pr_url
        assert _parse_pr_number(pr_url(number=55301)) == 55301

    def test_parse_pr_number_with_query(self):
        from _test_constants import pr_url
        assert _parse_pr_number(pr_url(number=123) + "?diff=unified") == 123

    def test_parse_pr_number_invalid(self):
        assert _parse_pr_number("https://github.com/repo") is None

    def test_parse_pr_number_empty(self):
        assert _parse_pr_number("") is None

    def test_parse_pr_number_none(self):
        assert _parse_pr_number(None) is None

    def test_parse_pr_number_company_repo(self):
        from _test_constants import (
            pr_url, TEST_COMPANY_REPO_RUNTIME,
        )
        # Company-style repo path with hyphens parses identically.
        assert _parse_pr_number(
            pr_url(repo=TEST_COMPANY_REPO_RUNTIME, number=194903),
        ) == 194903

    def test_parse_pr_number_trailing_slash(self):
        from _test_constants import pr_url
        assert _parse_pr_number(pr_url(number=55301) + "/") == 55301


# ---- _build_repo_authors ----

class TestBuildRepoAuthors:
    def test_build_repo_authors_returns_dict(self):
        result = _build_repo_authors()
        assert isinstance(result, dict)

    def test_build_repo_authors_contains_oss_repo(self):
        result = _build_repo_authors()
        assert "example/repo" in result

    def test_build_repo_authors_contains_company_org(self):
        result = _build_repo_authors()
        # myorg/* wildcard produces "owner:myorg" key
        assert "owner:myorg" in result

    def test_build_repo_authors_accounts(self):
        result = _build_repo_authors()
        # example/repo should use personal account
        assert result["example/repo"] == "test-author"
