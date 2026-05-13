"""Tests for the shared utils module."""

import pytest

from utils import repo_from_pr_url, pr_number_from_url, clamp_int


class TestRepoFromPrUrl:
    def test_standard_url(self):
        assert repo_from_pr_url("https://github.com/example/repo/pull/100") == "example/repo"

    def test_url_with_trailing_slash(self):
        assert repo_from_pr_url("https://github.com/myorg/svc/pull/200/") == "myorg/svc"

    def test_empty_string(self):
        assert repo_from_pr_url("") == ""

    def test_none_like(self):
        assert repo_from_pr_url("") == ""

    def test_fork_url(self):
        assert repo_from_pr_url("https://github.com/test-author/repo/pull/5") == "test-author/repo"


class TestPrNumberFromUrl:
    def test_standard_url(self):
        assert pr_number_from_url("https://github.com/example/repo/pull/100") == 100

    def test_url_with_query(self):
        assert pr_number_from_url("https://github.com/example/repo/pull/100?diff=split") == 100

    def test_url_with_trailing_path(self):
        assert pr_number_from_url("https://github.com/example/repo/pull/100/files") == 100

    def test_not_a_url(self):
        assert pr_number_from_url("not-a-url") is None

    def test_empty_string(self):
        assert pr_number_from_url("") is None

    def test_no_pull_segment(self):
        assert pr_number_from_url("https://github.com/example/repo/issues/100") is None


class TestClampInt:
    """`clamp_int` is the shared helper used by every route handler
    that accepts a `?limit=` query param. Centralising the clamp
    means the bounds contract is uniform across routes -- a typo
    fix in one place fixes all of them."""

    def test_in_range_returns_unchanged(self):
        assert clamp_int(50, 1, 100) == 50

    def test_above_max_clamps_to_max(self):
        assert clamp_int(99999, 1, 100) == 100

    def test_below_min_clamps_to_min(self):
        assert clamp_int(-5, 1, 100) == 1

    def test_at_inclusive_lower_bound(self):
        assert clamp_int(1, 1, 100) == 1

    def test_at_inclusive_upper_bound(self):
        assert clamp_int(100, 1, 100) == 100

    def test_zero_clamps_when_min_is_one(self):
        """The most common bug-shape this helper defends against:
        `?limit=0` slipping through to the DB query as-is and
        returning a degenerate empty result."""
        assert clamp_int(0, 1, 500) == 1

    def test_negative_clamps_to_min(self):
        assert clamp_int(-99999, 5, 100) == 5

    def test_string_int_coerces(self):
        """Defensive: query-param values arriving as strings work
        too, so the helper is usable from any caller (not only
        FastAPI's auto-coerced int params)."""
        assert clamp_int("25", 1, 100) == 25

    def test_string_garbage_raises_value_error(self):
        """Non-numeric strings raise -- caller (e.g. FastAPI) maps
        to a 422. We don't silently treat 'abc' as a default value
        because that would mask client bugs."""
        with pytest.raises(ValueError):
            clamp_int("not-a-number", 1, 100)

    def test_lo_greater_than_hi_raises(self):
        """Programmer error: caller flipped the bounds. Surface it
        loudly rather than silently always returning the same
        value."""
        with pytest.raises(ValueError):
            clamp_int(50, 100, 1)

    def test_lo_equals_hi_collapses(self):
        """Edge case: a single-value range. Any input clamps to the
        single allowed value."""
        assert clamp_int(7, 5, 5) == 5
        assert clamp_int(99, 5, 5) == 5
        assert clamp_int(-99, 5, 5) == 5

    def test_float_truncates(self):
        """`int(value)` truncates toward zero -- caller passing a
        float gets the integer part. Documented contract."""
        assert clamp_int(50.7, 1, 100) == 50
        assert clamp_int(50.3, 1, 100) == 50
