"""Tests for common.repos.resolve and the /api/repos/resolved endpoint."""

import common
import app_state

from common import repos as core_repos
from common import settings as core_settings

from _test_constants import (
    TEST_OSS_REPO, TEST_COMPANY_ORG,
    TEST_COMPANY_REPO_RUNTIME, TEST_COMPANY_REPO_PLATFORM,
    TEST_OSS_FORK, pr_url,
)


def _seed_prs(db, urls):
    """Quick helper: insert minimal PR rows so the resolver has data
    to walk. URL -> distinct (org/repo) pairs are what the resolver
    actually counts."""
    for i, url in enumerate(urls):
        db.add_pr(
            project="test-proj", task_id="task-a", number=10000 + i,
            url=url, status="open", last_updated="",
        )


class TestGetRules:
    def test_falls_back_to_hardcoded_when_settings_empty(self, patched_server):
        # Settings table is empty -> use the hardcoded ALLOWED_REPOS.
        # Specifically asserts the fallback CONTAINS the prod set --
        # this test deliberately couples to production constants.
        rules = core_repos.get_rules()
        for hardcoded in app_state.ALLOWED_REPOS:
            assert hardcoded in rules

    def test_settings_overrides_hardcoded(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS,
            [TEST_OSS_REPO, "fizz/buzz"],
        )
        rules = core_repos.get_rules()
        assert rules == sorted([TEST_OSS_REPO, "fizz/buzz"])

    def test_returns_sorted_unique_list(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS,
            ["b/y", "a/x", "b/y", "a/x"],
        )
        assert core_repos.get_rules() == ["a/x", "b/y"]


class TestResolve:
    def test_explicit_rules_get_pr_counts(self, patched_server):
        patched_server._db._conn.execute("DELETE FROM prs")
        patched_server._db._conn.commit()
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS, [TEST_OSS_REPO],
        )
        _seed_prs(patched_server._db, [
            pr_url(TEST_OSS_REPO, 1),
            pr_url(TEST_OSS_REPO, 2),
            pr_url(TEST_OSS_REPO, 3),
        ])
        out = core_repos.resolve()
        assert out["resolved"] == [
            {"repo": TEST_OSS_REPO, "source": "rule", "pr_count": 3},
        ]

    def test_wildcard_expands_to_seen_repos_only(self, patched_server):
        patched_server._db._conn.execute("DELETE FROM prs")
        patched_server._db._conn.commit()
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS, [f"{TEST_COMPANY_ORG}/*"],
        )
        _seed_prs(patched_server._db, [
            pr_url(TEST_COMPANY_REPO_RUNTIME, 1),
            pr_url(TEST_COMPANY_REPO_RUNTIME, 2),
            pr_url(TEST_COMPANY_REPO_PLATFORM, 1),
            # Other-org PR must NOT show up under the wildcard.
            pr_url("other/repo", 1),
        ])
        out = core_repos.resolve()
        repos = [r["repo"] for r in out["resolved"]]
        assert TEST_COMPANY_REPO_RUNTIME in repos
        assert TEST_COMPANY_REPO_PLATFORM in repos
        assert "other/repo" not in repos
        # Sources marked as wildcard with the originating rule attached.
        for row in out["resolved"]:
            assert row["source"] == "wildcard"
            assert row["wildcard"] == f"{TEST_COMPANY_ORG}/*"
        # Sorted by PR count descending.
        runtime = next(r for r in out["resolved"]
                       if r["repo"] == TEST_COMPANY_REPO_RUNTIME)
        platform = next(r for r in out["resolved"]
                        if r["repo"] == TEST_COMPANY_REPO_PLATFORM)
        assert runtime["pr_count"] == 2
        assert platform["pr_count"] == 1

    def test_explicit_rule_wins_over_wildcard_dedup(self, patched_server):
        """A repo named in an explicit rule should appear exactly once
        (with source='rule'), not double-listed as a wildcard match."""
        patched_server._db._conn.execute("DELETE FROM prs")
        patched_server._db._conn.commit()
        special = f"{TEST_COMPANY_ORG}/special"
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS,
            [special, f"{TEST_COMPANY_ORG}/*"],
        )
        _seed_prs(patched_server._db, [
            pr_url(special, 1),
            pr_url(f"{TEST_COMPANY_ORG}/other", 1),
        ])
        out = core_repos.resolve()
        special_rows = [r for r in out["resolved"] if r["repo"] == special]
        assert len(special_rows) == 1
        assert special_rows[0]["source"] == "rule"

    def test_returns_empty_resolved_when_no_prs(self, patched_server):
        patched_server._db._conn.execute("DELETE FROM prs")
        patched_server._db._conn.commit()
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS, [f"{TEST_COMPANY_ORG}/*"],
        )
        out = core_repos.resolve()
        # Wildcard-only with no PRs in that org yields nothing.
        assert out["resolved"] == []

    def test_fork_to_upstream_round_trips(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_FORK_TO_UPSTREAM,
            {TEST_OSS_FORK: TEST_OSS_REPO},
        )
        out = core_repos.resolve()
        assert out["fork_to_upstream"] == {TEST_OSS_FORK: TEST_OSS_REPO}

    def test_handles_malformed_urls_gracefully(self, patched_server):
        patched_server._db._conn.execute("DELETE FROM prs")
        patched_server._db._conn.commit()
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS, [f"{TEST_COMPANY_ORG}/*"],
        )
        good_repo = f"{TEST_COMPANY_ORG}/good"
        _seed_prs(patched_server._db, [
            pr_url(good_repo, 1),
            "not-a-url",
            "",
        ])
        out = core_repos.resolve()
        repos = [r["repo"] for r in out["resolved"]]
        assert repos == [good_repo]


class TestResolveEndpoint:
    def test_endpoint_returns_payload_shape(self, client, patched_server):
        patched_server._db._conn.execute("DELETE FROM prs")
        patched_server._db._conn.commit()
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS, [TEST_OSS_REPO],
        )
        _seed_prs(patched_server._db, [
            pr_url(TEST_OSS_REPO, 1),
        ])
        resp = client.get("/api/repos/resolved")
        assert resp.status_code == 200
        body = resp.json()
        assert "rules" in body
        assert "resolved" in body
        assert "fork_to_upstream" in body
        assert body["resolved"][0]["repo"] == TEST_OSS_REPO
        assert body["resolved"][0]["pr_count"] == 1


class TestPrRepoCountsSqliteFailure:
    """`_pr_repo_counts` swallows DB errors so a corrupted prs index
    doesn't break the Settings -> Repos page render. Returns an empty
    counts dict on any failure."""

    def test_returns_empty_counts_when_db_query_raises(
        self, monkeypatch, patched_server,
    ):
        # Wrap the live db._conn with a proxy that raises on execute.
        # sqlite3.Connection.execute is read-only at the attribute
        # level, so we replace _db itself with a proxy whose .conn
        # raises while keeping every other attribute intact for
        # teardown.
        real_db = patched_server._db

        class ConnProxy:
            def execute(self, *a, **kw):
                raise RuntimeError("disk i/o error")

        class DbProxy:
            _conn = ConnProxy()
            # Forward everything else (including close()) to real_db
            # so the teardown fixture is happy.
            def __getattr__(self, name):
                return getattr(real_db, name)
        monkeypatch.setattr(app_state, "_db", DbProxy())
        out = core_repos._pr_repo_counts()
        assert out == {}
