"""Tests to increase pr_sync.py coverage -- targets lines 40, 50-61, 80-110, 158."""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pr_sync import (
    aggregate_ci_status,
    extract_ticket,
    fetch_fork_ci,
    is_externally_merged,
    resolve_pr_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# aggregate_ci_status -- cover line 40 (unknown fallback)
# ---------------------------------------------------------------------------

class TestAggregateCiStatusUnknown:
    """Cover the final 'return unknown' fallback on line 40."""

    def test_unknown_conclusion_falls_through(self):
        # A conclusion string that does not match any known category
        checks = [{"conclusion": "SOME_BRAND_NEW_STATUS"}]
        assert aggregate_ci_status(checks) == "unknown"

    def test_mix_of_known_success_and_unknown(self):
        checks = [
            {"conclusion": "SUCCESS"},
            {"conclusion": "WEIRD_VALUE"},
        ]
        # Not all are in the success set, and not failure/pending/cancelled
        assert aggregate_ci_status(checks) == "unknown"


class TestAggregateCiStatusWithStatusContext:
    """Cover StatusContext objects that use 'state' via .get('status')."""

    def test_status_context_pending(self):
        # StatusContext-style: no 'conclusion', just 'status'
        checks = [{"status": "PENDING"}]
        assert aggregate_ci_status(checks) == "pending"

    def test_status_context_success(self):
        checks = [{"status": "success"}]
        assert aggregate_ci_status(checks) == "success"

    def test_status_context_failure(self):
        checks = [{"status": "failure"}]
        assert aggregate_ci_status(checks) == "failure"

    def test_check_run_conclusion_overrides_status(self):
        # When both conclusion and status exist, conclusion is used first
        checks = [{"conclusion": "FAILURE", "status": "completed"}]
        assert aggregate_ci_status(checks) == "failure"

    def test_startup_failure_is_failure(self):
        checks = [{"conclusion": "STARTUP_FAILURE"}]
        assert aggregate_ci_status(checks) == "failure"

    def test_stale_is_failure(self):
        checks = [{"conclusion": "STALE"}]
        assert aggregate_ci_status(checks) == "failure"

    def test_action_required_is_failure(self):
        checks = [{"conclusion": "ACTION_REQUIRED"}]
        assert aggregate_ci_status(checks) == "failure"

    def test_empty_string_status_is_unknown(self):
        # Both `conclusion` and `status` empty: GH hasn't reported anything
        # for this check yet. Previously treated as success (latent bug --
        # made unfinished CI invisible). Now correctly classified as unknown.
        checks = [{"conclusion": ""}]
        assert aggregate_ci_status(checks) == "unknown"


# ---------------------------------------------------------------------------
# is_externally_merged -- generic across opt-in repos (was Repo-specific)
# ---------------------------------------------------------------------------

class TestIsExternallyMerged:
    """`is_externally_merged(repo, pr_number, gh_run_fn)` works for any
    upstream repo that uses external merge -- no `example/repo` baked
    in. The repo path is interpolated into the gh API URL so the
    function generalises to any opt-in `org/repo`."""

    def test_merged_when_commit_count_gt_zero(self):
        def gh_run(cmd, repo=None, timeout=None):
            return _make_result(stdout="3\n")
        assert is_externally_merged("example/repo", 55301, gh_run) is True

    def test_not_merged_when_commit_count_zero(self):
        def gh_run(cmd, repo=None, timeout=None):
            return _make_result(stdout="0\n")
        assert is_externally_merged("example/repo", 55301, gh_run) is False

    def test_not_merged_when_gh_fails(self):
        def gh_run(cmd, repo=None, timeout=None):
            return _make_result(returncode=1, stdout="")
        assert is_externally_merged("example/repo", 55301, gh_run) is False

    def test_not_merged_when_stdout_not_digit(self):
        def gh_run(cmd, repo=None, timeout=None):
            return _make_result(stdout="error: not found\n")
        assert is_externally_merged("example/repo", 55301, gh_run) is False

    def test_not_merged_when_exception_raised(self):
        def gh_run(cmd, repo=None, timeout=None):
            raise TimeoutError("timed out")
        assert is_externally_merged("example/repo", 55301, gh_run) is False

    def test_not_merged_on_empty_stdout(self):
        def gh_run(cmd, repo=None, timeout=None):
            return _make_result(stdout="")
        assert is_externally_merged("example/repo", 55301, gh_run) is False

    def test_passes_correct_arguments_for_any_repo(self):
        """The repo path is interpolated correctly into the API URL --
        proves the function isn't hardcoded to `example/repo`."""
        captured = {}
        def gh_run(cmd, repo=None, timeout=None):
            captured["cmd"] = cmd
            captured["repo"] = repo
            captured["timeout"] = timeout
            return _make_result(stdout="1\n")
        is_externally_merged("acme/widgets", 12345, gh_run)
        api_path = [c for c in captured["cmd"] if "12345" in str(c)]
        assert len(api_path) == 1
        assert "repos/acme/widgets/issues/12345/events" in api_path[0]
        assert captured["repo"] == "acme/widgets"
        assert captured["timeout"] == 10

    def test_rejects_invalid_repo_without_shelling_out(self):
        """Defensive: empty/malformed repo skips gh entirely."""
        def gh_run(cmd, repo=None, timeout=None):
            raise AssertionError("should not be called")
        assert is_externally_merged("", 100, gh_run) is False
        assert is_externally_merged("noslash", 100, gh_run) is False


# ---------------------------------------------------------------------------
# resolve_pr_status with gh_run_fn -- cover the integration path
# ---------------------------------------------------------------------------

class TestResolvePrStatusWithGhRun:
    """`resolve_pr_status` reads `service.github.external_merge_repos`
    from the settings DB to decide which CLOSED PRs to re-check via
    issue events. Tests inject the setting via patched_server.

    Some tests don't use the fixture because they exercise non-CLOSED
    paths where the settings lookup never happens."""

    def test_merged_state_ignores_gh_run(self):
        def gh_run(cmd, repo=None, timeout=None):
            raise AssertionError("should not be called")
        assert resolve_pr_status(
            "MERGED", "example/repo", 100, gh_run,
        ) == "merged"

    def test_closed_external_merge_repo_checks_merge(self, patched_server):
        """Repo opted into external-merge: CLOSED triggers an issue
        events lookup and resolves to `merged` when commits found."""
        patched_server._db.set_setting(
            "service.github.external_merge_repos", ["example/repo"],
        )
        def gh_run(cmd, repo=None, timeout=None):
            return _make_result(stdout="2\n")
        assert resolve_pr_status(
            "CLOSED", "example/repo", 55301, gh_run,
        ) == "merged"

    def test_closed_external_merge_repo_not_merged(self, patched_server):
        """Same opted-in repo, but no merge commit found -> `closed`."""
        patched_server._db.set_setting(
            "service.github.external_merge_repos", ["example/repo"],
        )
        def gh_run(cmd, repo=None, timeout=None):
            return _make_result(stdout="0\n")
        assert resolve_pr_status(
            "CLOSED", "example/repo", 55301, gh_run,
        ) == "closed"

    def test_closed_repo_not_in_setting_skips_gh(self, patched_server):
        """A repo NOT in `external_merge_repos` (or empty list) -> the
        CLOSED state maps directly to `closed` without shelling out."""
        # Empty list is the OSS default.
        def gh_run(cmd, repo=None, timeout=None):
            raise AssertionError("should not be called")
        assert resolve_pr_status(
            "CLOSED", "myorg/svc", 100, gh_run,
        ) == "closed"

    def test_open_returns_open(self):
        def gh_run(cmd, repo=None, timeout=None):
            raise AssertionError("should not be called")
        assert resolve_pr_status(
            "OPEN", "example/repo", 100, gh_run,
        ) == "open"

    def test_works_for_arbitrary_opted_in_repo(self, patched_server):
        """OSS contract: a user opts in `acme/widgets` via setting and
        the same external-merge handling applies, no Repo hardcode."""
        patched_server._db.set_setting(
            "service.github.external_merge_repos", ["acme/widgets"],
        )
        def gh_run(cmd, repo=None, timeout=None):
            return _make_result(stdout="1\n")
        assert resolve_pr_status(
            "CLOSED", "acme/widgets", 200, gh_run,
        ) == "merged"


# ---------------------------------------------------------------------------
# fetch_fork_ci -- cover lines 80-110
# ---------------------------------------------------------------------------

class TestFetchForkCi:
    """Cover fetch_fork_ci with various gh_run_fn mock scenarios."""

    def test_successful_run_with_jobs(self):
        """Happy path: run list returns a run, run view returns jobs."""
        call_count = [0]

        def gh_run(cmd, repo=None, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                # gh run list
                return _make_result(stdout=json.dumps(
                    [{"databaseId": 42, "status": "completed", "conclusion": "success"}]
                ))
            else:
                # gh run view -- jq outputs one JSON object per line
                lines = [
                    json.dumps({"name": "build-jdk11", "conclusion": "success", "status": "completed"}),
                    json.dumps({"name": "build-jdk17", "conclusion": "success", "status": "completed"}),
                ]
                return _make_result(stdout="\n".join(lines))

        # fetch_fork_ci now requires an explicit fork_repo (no
        # personal-account default).
        jobs = fetch_fork_ci("branch-EX-123", gh_run, fork_repo="alice/repo")
        assert jobs is not None
        assert len(jobs) == 2
        assert jobs[0]["name"] == "build-jdk11"
        assert jobs[1]["conclusion"] == "success"

    def test_run_list_returns_no_runs(self):
        """run list returns empty array -> None."""
        def gh_run(cmd, repo=None, timeout=None):
            return _make_result(stdout="[]")

        assert fetch_fork_ci("branch-x", gh_run) is None

    def test_run_list_fails(self):
        """run list returns non-zero exit -> None."""
        def gh_run(cmd, repo=None, timeout=None):
            return _make_result(returncode=1, stdout="", stderr="not found")

        assert fetch_fork_ci("branch-x", gh_run) is None

    def test_run_view_fails(self):
        """run list succeeds but run view fails -> None."""
        call_count = [0]

        def gh_run(cmd, repo=None, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_result(stdout=json.dumps(
                    [{"databaseId": 99, "status": "completed", "conclusion": "failure"}]
                ))
            else:
                return _make_result(returncode=1, stdout="")

        assert fetch_fork_ci("branch-y", gh_run) is None

    def test_run_view_returns_malformed_json(self):
        """run view outputs lines that are not valid JSON -> None (empty jobs)."""
        call_count = [0]

        def gh_run(cmd, repo=None, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_result(stdout=json.dumps(
                    [{"databaseId": 10, "status": "completed", "conclusion": "success"}]
                ))
            else:
                return _make_result(stdout="not json at all\nalso not json\n")

        assert fetch_fork_ci("branch-z", gh_run) is None

    def test_run_view_mixed_valid_and_invalid_lines(self):
        """Some lines parse, some don't -- returns only valid ones."""
        call_count = [0]

        def gh_run(cmd, repo=None, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_result(stdout=json.dumps(
                    [{"databaseId": 7, "status": "completed", "conclusion": "success"}]
                ))
            else:
                lines = [
                    json.dumps({"name": "good-job", "conclusion": "success", "status": "completed"}),
                    "this is garbage",
                    "",
                    json.dumps({"name": "another-job", "conclusion": "failure", "status": "completed"}),
                ]
                return _make_result(stdout="\n".join(lines))

        jobs = fetch_fork_ci("branch-mixed", gh_run, fork_repo="alice/repo")
        assert jobs is not None
        assert len(jobs) == 2
        assert jobs[0]["name"] == "good-job"
        assert jobs[1]["name"] == "another-job"

    def test_timeout_exception_returns_none(self):
        """gh_run raises an exception (e.g. timeout) -> None."""
        def gh_run(cmd, repo=None, timeout=None):
            raise TimeoutError("command timed out")

        assert fetch_fork_ci("branch-timeout", gh_run) is None

    def test_generic_exception_returns_none(self):
        """gh_run raises a generic exception -> None."""
        def gh_run(cmd, repo=None, timeout=None):
            raise RuntimeError("something broke")

        assert fetch_fork_ci("branch-err", gh_run) is None

    def test_empty_stdout_from_run_view(self):
        """run view returns empty stdout -> None."""
        call_count = [0]

        def gh_run(cmd, repo=None, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_result(stdout=json.dumps(
                    [{"databaseId": 5, "status": "completed", "conclusion": "success"}]
                ))
            else:
                return _make_result(stdout="")

        assert fetch_fork_ci("branch-empty", gh_run) is None

    def test_empty_fork_repo_short_circuits_to_none(self):
        """Regression: previously the default was hardcoded to a
        personal account; now it must be passed in or we no-op
        rather than silently shelling out to the wrong repo."""
        called = []
        def gh_run(cmd, repo=None, timeout=None):
            called.append(repo)
            return _make_result(stdout="[]")
        # Empty fork_repo -> None, gh_run never invoked.
        assert fetch_fork_ci("any-branch", gh_run, fork_repo="") is None
        assert called == []
        # Default also empty -> same.
        assert fetch_fork_ci("any-branch", gh_run) is None
        assert called == []

    def test_custom_fork_repo(self):
        """Verify fork_repo parameter is forwarded correctly."""
        captured_repos = []

        def gh_run(cmd, repo=None, timeout=None):
            captured_repos.append(repo)
            if len(captured_repos) == 1:
                return _make_result(stdout=json.dumps(
                    [{"databaseId": 1, "status": "completed", "conclusion": "success"}]
                ))
            else:
                return _make_result(stdout=json.dumps(
                    {"name": "test", "conclusion": "success", "status": "completed"}
                ))

        fetch_fork_ci("branch-x", gh_run, fork_repo="other-user/repo")
        assert all(r == "other-user/repo" for r in captured_repos)

    def test_exception_on_second_call_only(self):
        """run list succeeds, but run view raises exception -> None."""
        call_count = [0]

        def gh_run(cmd, repo=None, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_result(stdout=json.dumps(
                    [{"databaseId": 88, "status": "completed", "conclusion": "success"}]
                ))
            raise OSError("network error")

        assert fetch_fork_ci("branch-net-err", gh_run) is None


# ---------------------------------------------------------------------------
# extract_ticket -- additional edge cases
# ---------------------------------------------------------------------------

class TestExtractTicketEdgeCases:
    def test_es_ticket(self):
        assert extract_ticket("[EX-123456] Fix buffer holder") == "EX-123456"

    def test_sc_ticket(self):
        assert extract_ticket("[ALT-456] Something") == "ALT-456"

    def test_multiple_tickets_returns_first(self):
        assert extract_ticket("[EX-111][EX-222] Multi") == "EX-111"

    def test_no_ticket_plain_text(self):
        assert extract_ticket("Update README with instructions") is None

    def test_lowercase_does_not_match(self):
        assert extract_ticket("[repo-123] lowercase") is None

    def test_partial_bracket_no_match(self):
        assert extract_ticket("EX-123 without brackets") is None

    def test_ticket_mid_title(self):
        # Ticket not at the start
        assert extract_ticket("Backport [EX-55555] to 3.5") == "EX-55555"

    def test_ticket_with_extra_text_in_brackets(self):
        # Extra text inside brackets should not match
        assert extract_ticket("[EX-123 fix]") is None


