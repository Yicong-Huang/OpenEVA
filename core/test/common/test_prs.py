"""Tests for core/common.prs.py -- add_pr title validation, get_pr_info caching,
list_all_prs grouping, remove_pr, and helper wrappers."""

import common
import json
import os
import sys
import time
import tempfile
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture()
def prs_db(patched_server):
    """Return the patched server with test data (from conftest).
    Provides project 'test-proj' with tasks task-a, task-b, task-c, task-d.
    task-a has PR #100 (merged), task-b has PR #200 (open).
    """
    return patched_server


# ============================================================
# add_pr -- title validation
# ============================================================


class TestAddPrTitleValidation:
    """prs.add_pr raises ValueError when title is empty."""

    def test_add_pr_empty_title_raises(self, prs_db):
        from common.prs import add_pr
        with pytest.raises(ValueError, match="title is required"):
            add_pr("test-proj", "task-d", number=300,
                   url="https://github.com/example/repo/pull/300", title="")

    def test_add_pr_whitespace_title_raises(self, prs_db):
        from common.prs import add_pr
        with pytest.raises(ValueError, match="title is required"):
            add_pr("test-proj", "task-d", number=301,
                   url="https://github.com/example/repo/pull/301", title="   ")

    def test_add_pr_none_title_raises(self, prs_db):
        from common.prs import add_pr
        with pytest.raises(ValueError, match="title is required"):
            add_pr("test-proj", "task-d", number=302,
                   url="https://github.com/example/repo/pull/302", title=None)

    def test_add_pr_valid_title_succeeds(self, prs_db):
        from common.prs import add_pr
        result = add_pr("test-proj", "task-d", number=303,
                        url="https://github.com/example/repo/pull/303",
                        title="[EX-12345] Add feature X")
        assert result is True

    def test_add_pr_strips_title_whitespace(self, prs_db):
        """Title with leading/trailing whitespace is stripped before storing."""
        from common.prs import add_pr
        import app_state
        result = add_pr("test-proj", "task-d", number=304,
                        url="https://github.com/example/repo/pull/304",
                        title="  Padded Title  ")
        assert result is True
        pr = app_state._db.find_pr_by_number(304)
        assert pr is not None
        assert pr["title"] == "Padded Title"

    def test_add_pr_task_not_found(self, prs_db):
        """add_pr returns None when the task doesn't exist."""
        from common.prs import add_pr
        result = add_pr("test-proj", "nonexistent-task", number=305,
                        url="https://github.com/example/repo/pull/305",
                        title="Valid Title")
        assert result is None

    def test_add_pr_with_session_and_working_dir(self, prs_db):
        """add_pr passes session and working_dir to DB."""
        from common.prs import add_pr
        import app_state
        result = add_pr("test-proj", "task-d", number=306,
                        url="https://github.com/example/repo/pull/306",
                        title="With session",
                        session="my-session", working_dir="~/work")
        assert result is True
        pr = app_state._db.find_pr_by_number(306)
        assert pr["session"] == "my-session"
        assert pr["working_dir"] == "~/work"


# ============================================================
# remove_pr
# ============================================================


class TestRemovePr:
    """prs.remove_pr returns True/False/None correctly."""

    def test_remove_pr_success(self, prs_db):
        from common.prs import remove_pr
        result = remove_pr("test-proj", "task-a", 100)
        assert result is True

    def test_remove_pr_not_found(self, prs_db):
        from common.prs import remove_pr
        result = remove_pr("test-proj", "task-a", 999)
        assert result is False

    def test_remove_pr_task_not_found(self, prs_db):
        from common.prs import remove_pr
        result = remove_pr("test-proj", "ghost-task", 100)
        assert result is None


# ============================================================
# list_all_prs grouping
# ============================================================


class TestListAllPrs:
    """prs.list_all_prs returns PRs grouped by project."""

    def test_returns_groups(self, prs_db):
        from common.prs import list_all_prs
        result = list_all_prs()
        assert "groups" in result
        groups = result["groups"]
        assert "test-proj" in groups
        assert "prs" in groups["test-proj"]
        assert len(groups["test-proj"]["prs"]) >= 1

    def test_filter_by_status(self, prs_db):
        from common.prs import list_all_prs
        result = list_all_prs(status="open")
        groups = result["groups"]
        # task-b has an open PR (#200)
        for pid, group in groups.items():
            for pr in group["prs"]:
                assert pr["status"] == "open"

    def test_search_filter(self, prs_db):
        """Search should filter by title or task_id."""
        from common.prs import list_all_prs
        # Search for task-b (which has PR #200)
        result = list_all_prs(search="task-b")
        groups = result["groups"]
        found_numbers = []
        for pid, group in groups.items():
            for pr in group["prs"]:
                found_numbers.append(pr["number"])
        assert 200 in found_numbers

    def test_empty_result(self, prs_db):
        from common.prs import list_all_prs
        result = list_all_prs(search="zzzznonexistentzzzz")
        assert result["groups"] == {}


# ============================================================
# get_pr_info -- caching
# ============================================================


class TestGetPrInfoCaching:
    """get_pr_info caches results for 5 minutes."""

    @patch("common.prs.app_state.gh_run")
    def test_cache_hit_within_ttl(self, mock_gh, prs_db):
        """Second call within 5 min returns cached result without calling gh."""
        from common.prs import get_pr_info, _pr_info_cache

        url = "https://github.com/test/repo/pull/999"
        _pr_info_cache.clear()

        # First call: simulate gh success
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = '{"requested_reviewers": ["alice"], "updated_at": "2026-01-01"}'
        mock_gh.return_value = fake_result

        info1 = get_pr_info(url)
        call_count = mock_gh.call_count

        # Second call: should use cache
        info2 = get_pr_info(url)
        assert mock_gh.call_count == call_count  # no new calls
        assert info2["url"] == url

        _pr_info_cache.clear()

    @patch("common.prs.app_state.gh_run")
    def test_cache_miss_after_ttl(self, mock_gh, prs_db):
        """After 5 minutes, cache is expired and gh is called again."""
        from common.prs import get_pr_info, _pr_info_cache

        url = "https://github.com/test/repo/pull/998"
        _pr_info_cache.clear()

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = '{"requested_reviewers": [], "updated_at": "2026-01-01"}'
        mock_gh.return_value = fake_result

        get_pr_info(url)
        # Manually expire the cache
        _pr_info_cache[url]["_ts"] = time.time() - 600

        mock_gh.reset_mock()
        get_pr_info(url)
        assert mock_gh.call_count > 0

        _pr_info_cache.clear()

    def test_invalid_url_returns_error(self, prs_db):
        """get_pr_info on an invalid URL returns an error dict."""
        from common.prs import get_pr_info, _pr_info_cache
        _pr_info_cache.clear()
        result = get_pr_info("not-a-valid-url")
        assert "error" in result
        _pr_info_cache.clear()

    @patch("common.prs.app_state.gh_run")
    def test_cache_eviction_over_200(self, mock_gh, prs_db):
        """When cache exceeds 200 entries, oldest 50 are evicted."""
        from common.prs import get_pr_info, _pr_info_cache
        _pr_info_cache.clear()

        # Fill cache with 201 entries (fake)
        for i in range(201):
            url = f"https://github.com/test/repo/pull/{i}"
            _pr_info_cache[url] = {"_ts": time.time() - (300 - i), "url": url}

        assert len(_pr_info_cache) == 201

        # Now call get_pr_info to trigger eviction
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = '{"requested_reviewers": [], "updated_at": "2026-01-01"}'
        mock_gh.return_value = fake_result

        get_pr_info("https://github.com/test/repo/pull/9999")
        # Should have evicted 50 oldest and added 1 new
        assert len(_pr_info_cache) <= 201 - 50 + 1

        _pr_info_cache.clear()


# ============================================================
# get_pr_detail -- gh error handling
# ============================================================


class TestGetPrDetail:
    """prs.get_pr_detail returns None on failure."""

    @patch("common.prs.app_state.gh_run")
    def test_returns_none_on_gh_failure(self, mock_gh, prs_db):
        from common.prs import get_pr_detail
        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        mock_gh.return_value = fake_result

        result = get_pr_detail("example/repo", 12345)
        assert result is None

    @patch("common.prs.app_state.gh_run")
    def test_returns_none_on_exception(self, mock_gh, prs_db):
        from common.prs import get_pr_detail
        mock_gh.side_effect = Exception("network error")

        result = get_pr_detail("example/repo", 12345)
        assert result is None

    @patch("common.prs.app_state.gh_run")
    def test_returns_pr_data_on_success(self, mock_gh, prs_db):
        from common.prs import get_pr_detail

        call_seq = [0]

        def mock_gh_side_effect(args, repo="", timeout=20):
            call_seq[0] += 1
            result = MagicMock()
            if call_seq[0] == 1:
                # Main pr view
                result.returncode = 0
                result.stdout = json.dumps({
                    "number": 100, "title": "Test PR",
                    "state": "OPEN", "author": {"login": "user"},
                    "statusCheckRollup": [],
                    "reviewDecision": "",
                })
            elif call_seq[0] == 2:
                # Inline comments
                result.returncode = 0
                result.stdout = "[]"
            elif call_seq[0] == 3:
                # GraphQL thread info
                result.returncode = 1
                result.stdout = ""
            else:
                # Issue comments
                result.returncode = 0
                result.stdout = "[]"
            return result

        mock_gh.side_effect = mock_gh_side_effect
        result = get_pr_detail("example/repo", 100)
        assert result is not None
        assert result["number"] == 100
        assert result["title"] == "Test PR"


# ============================================================
# Helper wrappers
# ============================================================


class TestHelperWrappers:
    """Test the thin wrapper functions in core/common.prs.py."""

    @patch("common.prs._pr.aggregate_ci_status")
    def test_aggregate_ci_status(self, mock_agg, prs_db):
        from common.prs import _aggregate_ci_status
        mock_agg.return_value = "success"
        result = _aggregate_ci_status([{"conclusion": "SUCCESS"}])
        assert result == "success"
        mock_agg.assert_called_once()

    @patch("common.prs._pr.is_externally_merged")
    def test_is_externally_merged(self, mock_merged, prs_db):
        from common.prs import _is_externally_merged
        mock_merged.return_value = True
        assert _is_externally_merged("example/repo", 100) is True
        # Generic across repos -- no hardcoded `example/repo` in the
        # wrapper.
        assert _is_externally_merged("acme/widgets", 200) is True

    @patch("common.prs._pr.resolve_pr_status")
    def test_resolve_pr_status(self, mock_resolve, prs_db):
        from common.prs import _resolve_pr_status
        mock_resolve.return_value = "merged"
        result = _resolve_pr_status("CLOSED", "example/repo", 100)
        assert result == "merged"


class TestRefreshPrFromGh:
    """_refresh_pr_from_gh is the shared helper used by both sync loops.

    Must handle: unparseable URL, disallowed repo, gh_run_json failure,
    and the happy path where it returns True and forwards to _update_pr_from_gh.
    """

    def test_unparseable_url_returns_false(self, prs_db):
        from common.prs import _refresh_pr_from_gh
        assert _refresh_pr_from_gh(1, "not a url") is False
        assert _refresh_pr_from_gh(1, "") is False

    def test_disallowed_repo_returns_false(self, prs_db):
        """A URL pointing at a repo outside ALLOWED_REPOS must skip the gh call."""
        from common.prs import _refresh_pr_from_gh
        import app_state
        with patch.object(app_state, "is_repo_allowed", return_value=False):
            assert _refresh_pr_from_gh(1, "https://github.com/blocked/repo/pull/1") is False

    def test_gh_run_json_failure_returns_false(self, prs_db):
        from common.prs import _refresh_pr_from_gh
        import app_state
        with patch.object(app_state, "is_repo_allowed", return_value=True):
            with patch.object(app_state, "gh_run_json", return_value=None):
                assert _refresh_pr_from_gh(42, "https://github.com/example/repo/pull/42") is False

    def test_happy_path_forwards_and_returns_true(self, prs_db):
        from common.prs import _refresh_pr_from_gh
        import app_state
        from common import prs as prs_core
        fake = {"title": "x", "state": "OPEN", "url": "https://github.com/example/repo/pull/42"}
        with patch.object(app_state, "is_repo_allowed", return_value=True):
            with patch.object(app_state, "gh_run_json", return_value=fake):
                with patch.object(prs_core, "_update_pr_from_gh") as mock_update:
                    ok = _refresh_pr_from_gh(42, "https://github.com/example/repo/pull/42")
        assert ok is True
        mock_update.assert_called_once()
        # Called with (pr_number, item, repo)
        args = mock_update.call_args.args
        assert args[0] == 42
        assert args[1] == fake
        assert args[2] == "example/repo"


# ============================================================
# sync_review_requests helpers (extracted 2026-04-25 refactor)
# ============================================================


class TestResolveReviewSource:
    """Pure transition rule for the `source` enum on review_prs.

    Table:
      is_req=True  + prior in {manual, both}   -> ('both', +1 promotion)
      is_req=True  + prior in {github, ''}     -> ('github', 0)
      is_req=False + prior in {manual,both,github} -> leave as prior (0)
      is_req=False + prior == ''               -> 'manual' (auto-added
                                                  pin from a mention)
    """

    def test_review_requested_promotes_manual_to_both(self):
        from common.prs import _resolve_review_source
        assert _resolve_review_source(True, "manual") == ("both", 1)

    def test_review_requested_keeps_both_and_counts_promotion(self):
        """Prior='both' re-seen as review-requested is idempotent but
        we still count it as a promotion event for telemetry."""
        from common.prs import _resolve_review_source
        assert _resolve_review_source(True, "both") == ("both", 1)

    def test_review_requested_new_is_github(self):
        from common.prs import _resolve_review_source
        assert _resolve_review_source(True, "") == ("github", 0)

    def test_review_requested_existing_github_stays_github(self):
        from common.prs import _resolve_review_source
        assert _resolve_review_source(True, "github") == ("github", 0)

    def test_mention_only_new_becomes_manual_pin(self):
        from common.prs import _resolve_review_source
        assert _resolve_review_source(False, "") == ("manual", 0)

    def test_mention_does_not_clobber_prior_manual(self):
        """Mention-only sync on URLs that were already manually pinned
        must NOT re-classify them -- Unpin must keep working."""
        from common.prs import _resolve_review_source
        assert _resolve_review_source(False, "manual") == ("manual", 0)
        assert _resolve_review_source(False, "both") == ("both", 0)

    def test_mention_does_not_clobber_prior_github(self):
        """source='github' means an earlier pass DID see it as
        review-requested; a later pass with only a mention should
        leave the github tag alone."""
        from common.prs import _resolve_review_source
        assert _resolve_review_source(False, "github") == ("github", 0)


class TestPruneStaleReviewRows:
    def test_github_only_rows_not_in_fresh_get_deleted(self, patched_server):
        from common.prs import _prune_stale_review_rows
        # Seed: one github row, one manual row
        patched_server._db.upsert_review_pr(
            url="https://github.com/x/y/pull/1",
            repo="x/y", number=1, source="github", title="stale",
        )
        patched_server._db.upsert_review_pr(
            url="https://github.com/x/y/pull/2",
            repo="x/y", number=2, source="manual", title="user pin",
        )
        removed = _prune_stale_review_rows(set())
        assert removed == 1
        assert patched_server._db.get_review_pr("https://github.com/x/y/pull/1") is None
        # Manual pin unaffected.
        assert patched_server._db.get_review_pr("https://github.com/x/y/pull/2") is not None

    def test_both_rows_downgrade_to_manual_not_deleted(self, patched_server):
        """source='both' = manual pin + review-requested. If GitHub
        drops the review-requested half, the user's pin survives --
        downgrade, don't delete."""
        from common.prs import _prune_stale_review_rows
        patched_server._db.upsert_review_pr(
            url="https://github.com/x/y/pull/3",
            repo="x/y", number=3, source="both", title="pinned + requested",
        )
        removed = _prune_stale_review_rows(set())
        assert removed == 0
        row = patched_server._db.get_review_pr("https://github.com/x/y/pull/3")
        assert row is not None and row["source"] == "manual"

    def test_fresh_urls_are_preserved(self, patched_server):
        """URLs still present in fresh_req must not be touched."""
        from common.prs import _prune_stale_review_rows
        patched_server._db.upsert_review_pr(
            url="https://github.com/x/y/pull/4",
            repo="x/y", number=4, source="github", title="still req",
        )
        removed = _prune_stale_review_rows(
            {"https://github.com/x/y/pull/4"}
        )
        assert removed == 0
        row = patched_server._db.get_review_pr("https://github.com/x/y/pull/4")
        assert row is not None and row["source"] == "github"


class TestAutoPromoteEdgeCases:
    """`_auto_promote_task_status` short-circuits in two cases: task
    deleted between PR write and promote, and task already in the
    suggested state (no-op fan-out). Both must NOT recurse into
    DB writes -- otherwise we'd churn updated_at on every PR poll."""

    def test_returns_when_task_missing(self, patched_server, monkeypatch):
        from common.prs import _auto_promote_task_status
        # Simulate a race: PR write completed, then the task got
        # deleted before the auto-promote ran.
        monkeypatch.setattr(
            patched_server._db, "get_task", lambda *a, **kw: None,
        )
        # Should return without raising, no DB writes attempted.
        _auto_promote_task_status("test-proj", "ghost")

    def test_noops_when_status_already_matches_suggestion(
        self, patched_server, monkeypatch,
    ):
        """Task already in the state the suggestion would promote to
        -> skip the write. Verified by stubbing `update_task` to
        raise; if the no-op branch runs, we don't reach the write."""
        from common.prs import _auto_promote_task_status

        def boom(*a, **kw):
            raise AssertionError("should not write when status matches")
        monkeypatch.setattr(
            patched_server._db, "update_task", boom,
        )
        # task-a is already 'done' in patched_server fixture; its
        # merged PR -> suggested status would also be 'done', so the
        # write must be skipped.
        _auto_promote_task_status("test-proj", "task-a")


class TestReviewAccountHints:
    """`_review_account_hints` returns the iteration repos for
    `--review-requested=@me` searches.

    Resolution order (first non-empty wins):
      1. `service.github.review_account_hints` setting.
      2. Module-level `_REVIEW_ACCOUNT_HINTS` constant (tests monkeypatch).
      3. Derived from `adapters.github.ALLOWED_REPOS`.
    """

    def test_default_constant_is_empty_no_personal_strings_baked(self):
        """Open-source contract: the in-source default tuple must NOT
        contain any maintainer-specific repos. Anything else means a
        personal string is baked into the binary."""
        from common import prs as core_prs
        assert core_prs._REVIEW_ACCOUNT_HINTS == ()

    def test_falls_back_to_allowed_repos_when_unset(
        self, patched_server, monkeypatch,
    ):
        """No setting + empty constant -> derive from ALLOWED_REPOS.
        Wildcards are reduced to org so token selection still works."""
        from common import prs as core_prs
        from adapters import github as _gh
        monkeypatch.setattr(
            _gh, "ALLOWED_REPOS", {"acme/widgets", "alice/*"},
        )
        result = core_prs._review_account_hints()
        # set ordering is non-deterministic; sort for assertion
        assert sorted(result) == ["acme/widgets", "alice"]

    def test_module_constant_wins_over_allowed_repos(
        self, patched_server, monkeypatch,
    ):
        """Tests that monkeypatch the constant should still see their
        value, not whatever ALLOWED_REPOS contains."""
        from common import prs as core_prs
        from adapters import github as _gh
        monkeypatch.setattr(_gh, "ALLOWED_REPOS", {"acme/widgets"})
        monkeypatch.setattr(
            core_prs, "_REVIEW_ACCOUNT_HINTS", ("override/x",),
        )
        assert core_prs._review_account_hints() == ("override/x",)

    def test_settings_override_wins(self, patched_server):
        """OSS contract: write a list to the setting and the iteration
        uses it instead of the in-process default."""
        from common import prs as core_prs
        patched_server._db.set_setting(
            "service.github.review_account_hints",
            ["acme/widgets", "alice/oss"],
        )
        assert core_prs._review_account_hints() == ("acme/widgets", "alice/oss")

    def test_setting_with_non_list_falls_back_to_default(
        self, patched_server, monkeypatch,
    ):
        """Defensive: a corrupted setting value (string instead of
        list) shouldn't crash the review-queue refresh -- fall back to
        derived defaults."""
        from common import prs as core_prs
        from adapters import github as _gh
        monkeypatch.setattr(_gh, "ALLOWED_REPOS", {"acme/widgets"})
        patched_server._db.set_setting(
            "service.github.review_account_hints", "not a list",
        )
        assert core_prs._review_account_hints() == ("acme/widgets",)

    def test_setting_with_empty_list_falls_back_to_default(
        self, patched_server, monkeypatch,
    ):
        """An explicit empty list also falls back -- otherwise the
        review-queue search would silently return nothing for users
        who clear the setting expecting `everything` semantics."""
        from common import prs as core_prs
        from adapters import github as _gh
        monkeypatch.setattr(_gh, "ALLOWED_REPOS", {"acme/widgets"})
        patched_server._db.set_setting(
            "service.github.review_account_hints", [],
        )
        assert core_prs._review_account_hints() == ("acme/widgets",)

    def test_returns_empty_when_nothing_configured(
        self, patched_server, monkeypatch,
    ):
        """Fresh OSS install: setting unset, ALLOWED_REPOS empty,
        constant empty -> empty tuple. Refresh loop runs zero
        searches, which is correct (don't fabricate a hardcoded
        default that would 401 against the user's gh CLI)."""
        from common import prs as core_prs
        from adapters import github as _gh
        monkeypatch.setattr(_gh, "ALLOWED_REPOS", set())
        assert core_prs._review_account_hints() == ()
