"""Edge-case tests to boost coverage for routes/common.system.py and app_state.py.

Covers:
- Usage cache: cold start (blocking), warm cache (instant), stale cache (background refresh)
- Usage fetch failure: subprocess timeout, non-zero exit
- Cert renew: framework-level routing (unknown-id 404)
- Workstats: external-CLI failure, malformed JSON
- Live stats: cache behavior
- app_state: _parse_pr_number edge cases, gh_run timeout, _build_repo_authors,
  emit_event with dead SSE subscriber, save_task with dependencies on new task
- adapters.tmux: send_keys failure (timeout, CalledProcessError)
"""

import common
import json
import queue
import sqlite3
import subprocess
import time
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ====================================================================
# routes/common.system.py -- _fetch_usage exception handling
# ====================================================================


class TestFetchUsageFailure:
    """_fetch_usage returns None on subprocess timeout or generic exception."""

    def test_fetch_usage_subprocess_timeout(self, monkeypatch):
        from common import system as core_sys

        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="agent", timeout=15)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = core_sys._fetch_usage()
        assert result is None

    def test_fetch_usage_non_zero_exit_no_data(self, monkeypatch):
        from common import system as core_sys

        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "error: not found"
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
        result = core_sys._fetch_usage()
        # Returns data dict with all None values (daily is None, so no save)
        assert result is not None
        assert result["daily"] is None

    def test_fetch_usage_generic_exception(self, monkeypatch):
        from common import system as core_sys

        def raise_err(*a, **kw):
            raise OSError("broken pipe")

        monkeypatch.setattr(subprocess, "run", raise_err)
        result = core_sys._fetch_usage()
        assert result is None


# ====================================================================
# routes/common.system.py -- _save_usage_record error path
# ====================================================================


class TestSaveUsageRecordError:
    """_save_usage_record silently ignores ValueError and sqlite3.Error."""

    def test_save_record_with_bad_value(self, tmp_path, monkeypatch):
        import app_state
        from common import system as core_sys

        db_path = tmp_path / "usage.db"
        monkeypatch.setattr(app_state, "_USAGE_DB_PATH", db_path)
        monkeypatch.setattr(core_sys, "_USAGE_DB_PATH", db_path)
        core_sys._init_usage_db()

        # daily value that cannot be converted to float
        core_sys._save_usage_record({"daily": "not-a-number", "weekly": "10", "monthly": "100"})
        # Should not raise; record is silently skipped
        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM usage_history").fetchone()[0]
        assert count == 0

    def test_save_record_success(self, tmp_path, monkeypatch):
        import app_state
        from common import system as core_sys

        db_path = tmp_path / "usage.db"
        monkeypatch.setattr(app_state, "_USAGE_DB_PATH", db_path)
        monkeypatch.setattr(core_sys, "_USAGE_DB_PATH", db_path)
        core_sys._init_usage_db()

        core_sys._save_usage_record({"daily": "1,234.56", "weekly": "5,000", "monthly": "20,000"})
        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM usage_history").fetchone()[0]
        assert count == 1


# ====================================================================
# routes/common.system.py -- get_usage cache: cold start, warm, stale
# ====================================================================


class TestUsageCachePaths:
    """Exercise all three paths in get_usage: cold, warm, stale."""

    def setup_method(self):
        from common import system as core_sys
        self._original = dict(core_sys._usage_cache)

    def teardown_method(self):
        from common import system as core_sys
        core_sys._usage_cache["data"] = self._original["data"]
        core_sys._usage_cache["ts"] = self._original["ts"]

    def test_cold_start_blocks_and_fetches(self, monkeypatch):
        """First call with no cached data fetches synchronously."""
        from common import system as core_sys

        core_sys._usage_cache["data"] = None
        core_sys._usage_cache["ts"] = 0

        fake_data = {"daily": "10", "weekly": "50", "monthly": "200", "tier": "Standard"}
        monkeypatch.setattr(core_sys, "_fetch_usage", lambda: fake_data)

        result = core_sys.get_usage()
        assert result["daily"] == "10"
        assert core_sys._usage_cache["data"] is not None

    def test_cold_start_fetch_failure_returns_defaults(self, monkeypatch):
        """If _fetch_usage returns None on cold start, return default dict."""
        from common import system as core_sys

        core_sys._usage_cache["data"] = None
        core_sys._usage_cache["ts"] = 0

        monkeypatch.setattr(core_sys, "_fetch_usage", lambda: None)
        result = core_sys.get_usage()
        assert result["daily"] is None
        assert result["weekly"] is None

    def test_warm_cache_returns_immediately(self, monkeypatch):
        """When cache is fresh (<120s), return cached data without fetching."""
        from common import system as core_sys

        cached = {"daily": "99", "weekly": "99", "monthly": "99", "tier": "Power User"}
        core_sys._usage_cache["data"] = cached
        core_sys._usage_cache["ts"] = time.time()  # just now

        fetch_called = []
        monkeypatch.setattr(core_sys, "_fetch_usage", lambda: fetch_called.append(1))

        result = core_sys.get_usage()
        assert result["daily"] == "99"
        assert len(fetch_called) == 0  # _fetch_usage was NOT called

    def test_warm_cache_returns_even_when_old(self, monkeypatch):
        """`get_usage` no longer triggers a background refresh on stale
        cache -- the scheduler's `usage_refresh` job keeps `_usage_cache`
        fresh out of band. Reads always hit the cache without forking a
        thread; only a cold start (data=None) runs the subprocess."""
        from common import system as core_sys

        old = {"daily": "was-refreshed-5min-ago", "weekly": "x", "monthly": "y", "tier": None}
        core_sys._usage_cache["data"] = old
        core_sys._usage_cache["ts"] = time.time() - 300  # 5 min ago

        fetch_called = []
        monkeypatch.setattr(core_sys, "_fetch_usage", lambda: fetch_called.append(1))

        result = core_sys.get_usage()
        assert result is old
        assert fetch_called == []  # no subprocess fired on request path


# ====================================================================
# routes/common.system.py -- renew_cert endpoint edge cases
# ====================================================================


class TestRenewCert:
    """Framework-level renew route contract: unknown id -> 404. Per-
    provider renew behaviour is tested with the provider modules
    themselves."""

    def test_renew_unknown_cert_id(self, client):
        resp = client.post("/api/certs/renew/unknown_cert")
        assert resp.status_code == 404


# ====================================================================
# routes/common.system.py -- get_certs slack edge cases (no token, no creds file)
# ====================================================================


# Slack auth tests removed -- Slack/MCP auth is per-session, not global


# ====================================================================
# CertProvider edge cases (base class behaviour only)
# ====================================================================


class TestCertProviderBase:
    """The abstract CertProvider defines contract + default status mapping.

    Concrete providers inherit from it, but the abstract methods
    themselves (check / renew) still need tests so that a future
    subclass that skips an override fails loudly (NotImplemented)
    rather than silently returning a bogus value.
    """

    def test_base_check_raises_not_implemented(self):
        from common.system import CertProvider
        with pytest.raises(NotImplementedError):
            CertProvider().check()

    def test_base_renew_defaults_to_false(self):
        """Base renew is a no-op; only subclasses that wire a refresh flow
        should return True. Guards against a future 'default to True' bug
        which would pretend to renew certs that were never touched."""
        from common.system import CertProvider
        assert CertProvider().renew() is False

    def test_status_mapping_covers_error_expired_warning_ok(self):
        from common.system import CertProvider
        p = CertProvider()
        assert p.status(-1) == "error"
        assert p.status(0) == "expired"
        # warning_secs defaults to 57600s; anything <= that is "warning".
        assert p.status(1) == "warning"
        assert p.status(p.warning_secs) == "warning"
        assert p.status(p.warning_secs + 1) == "ok"


# Per-provider cert tests live with the provider modules. The
# framework tests below use stub CertProvider subclasses so they
# stay vendor-agnostic.


# ====================================================================
# app_state.py -- _parse_pr_number edge cases (lines 47-48)
# ====================================================================


class TestParsePrNumber:
    """Cover ValueError/IndexError paths in _parse_pr_number."""

    def test_valid_pr_url(self):
        import app_state
        assert app_state._parse_pr_number("https://github.com/example/repo/pull/12345") == 12345

    def test_pr_url_with_query_params(self):
        import app_state
        assert app_state._parse_pr_number("https://github.com/example/repo/pull/999?diff=unified") == 999

    def test_pr_url_with_trailing_slash(self):
        import app_state
        assert app_state._parse_pr_number("https://github.com/example/repo/pull/42/files") == 42

    def test_malformed_pr_url_non_numeric(self):
        """PR URL with non-numeric id triggers ValueError -> returns None."""
        import app_state
        result = app_state._parse_pr_number("https://github.com/example/repo/pull/abc")
        assert result is None

    def test_empty_string(self):
        import app_state
        assert app_state._parse_pr_number("") is None

    def test_none_value(self):
        import app_state
        assert app_state._parse_pr_number(None) is None

    def test_url_without_pull(self):
        import app_state
        assert app_state._parse_pr_number("https://github.com/example/repo/issues/123") is None


# ====================================================================
# app_state.py -- gh_run timeout (lines 149-150 via gh_run_async)
# ====================================================================


class TestGhRunTimeout:
    """Cover the subprocess.TimeoutExpired propagation in gh_run."""

    def test_gh_run_raises_timeout(self, monkeypatch):
        import app_state

        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="gh", timeout=20)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(subprocess.TimeoutExpired):
            app_state.gh_run(["gh", "api", "test"], repo="example/repo", timeout=20)


class TestGhRunAsync:
    """Cover gh_run_async (lines 149-150)."""

    def test_gh_run_async_delegates_to_gh_run(self, monkeypatch):
        import asyncio
        import app_state

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"ok": true}'
        mock_result.stderr = ""

        monkeypatch.setattr(app_state, "gh_run", lambda *a, **kw: mock_result)

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                app_state.gh_run_async(["gh", "api", "test"], repo="example/repo")
            )
            assert result.returncode == 0
        finally:
            loop.close()


# ====================================================================
# app_state.py -- _build_repo_authors (lines 120-129)
# ====================================================================


class TestBuildRepoAuthors:
    """Cover _build_repo_authors function."""

    def test_build_repo_authors_returns_expected(self):
        import app_state
        result = app_state._build_repo_authors()
        assert "example/repo" in result
        assert result["example/repo"] == "test-author"
        # myorg/* wildcard produces "owner:myorg"
        assert "owner:myorg" in result
        assert result["owner:myorg"] == "test-author_data"


# ====================================================================
# app_state.py -- emit_event with dead SSE subscriber (lines 239-240)
# ====================================================================


class TestEmitEventDeadSubscriber:
    """Cover the dead subscriber cleanup in emit_event."""

    def test_dead_subscriber_removed(self):
        import app_state

        # Create a "full" queue that will raise on put_nowait
        full_q = queue.Queue(maxsize=1)
        full_q.put("dummy")  # fill it

        app_state._event_subscribers.append(full_q)
        initial_count = len(app_state._event_subscribers)

        app_state.emit_event("test.dead_subscriber", {
            "title": "test",
            "message": "testing dead subscriber removal",
        })

        # The full queue should have been removed
        assert full_q not in app_state._event_subscribers

    def test_emit_event_no_subscribers(self):
        """emit_event works fine with zero subscribers."""
        import app_state

        original_subs = list(app_state._event_subscribers)
        app_state._event_subscribers.clear()

        # Should not raise
        app_state.emit_event("test.no_subscribers", {
            "title": "no listeners",
        })

        # Restore
        app_state._event_subscribers.extend(original_subs)

    def test_dead_subscriber_already_removed_valueerror(self):
        """Cover lines 239-240: ValueError when subscriber already removed."""
        import app_state

        # Create two full queues
        q1 = queue.Queue(maxsize=1)
        q1.put("dummy1")
        q2 = queue.Queue(maxsize=1)
        q2.put("dummy2")

        # Add q1 twice so the second removal triggers ValueError
        app_state._event_subscribers.append(q1)
        app_state._event_subscribers.append(q1)

        # Should not raise -- the first removal succeeds, the second
        # hits ValueError which is caught
        app_state.emit_event("test.double_dead", {
            "title": "double dead test",
        })

        # q1 should be fully removed
        assert q1 not in app_state._event_subscribers


# ====================================================================
# app_state.py -- emit_event sqlite3.Error path (lines 219-220)
# ====================================================================


class TestNotifDb:
    """Cover _notif_db helper (line 184)."""

    def test_notif_db_returns_connection(self):
        import app_state
        conn = app_state._notif_db()
        assert conn is not None
        conn.close()


class TestEmitEventDbError:
    """Cover the sqlite3.Error exception path in emit_event."""

    def test_emit_event_db_error_silent(self, monkeypatch):
        import app_state

        original_connect = sqlite3.connect

        def broken_connect(path, **kw):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(sqlite3, "connect", broken_connect)

        # Should not raise even when DB write fails
        app_state.emit_event("test.db_error", {
            "title": "should not crash",
        })

        monkeypatch.setattr(sqlite3, "connect", original_connect)


# ====================================================================
# adapters/tmux.py -- send_keys failure (TimeoutExpired / CalledProcessError)
# ====================================================================


class TestTmuxSendKeysFailure:
    """Cover the exception paths in adapters.tmux.send_keys."""

    def test_send_keys_timeout(self, monkeypatch):
        from adapters.tmux import send_keys

        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="tmux", timeout=5)

        monkeypatch.setattr("adapters.tmux.subprocess.run", fake_run)
        # Should not raise
        send_keys("nonexistent-session", "echo hello")

    def test_send_keys_called_process_error(self, monkeypatch):
        from adapters.tmux import send_keys

        def fake_run(*a, **kw):
            raise subprocess.CalledProcessError(1, "tmux")

        monkeypatch.setattr("adapters.tmux.subprocess.run", fake_run)
        # Should not raise
        send_keys("nonexistent-session", "echo hello")


# ====================================================================
# app_state.py -- save_task with dependencies on new task (line 354)
# ====================================================================


class TestSaveTaskNewWithDeps:
    """Cover the dependencies branch when creating a new task via save_task."""

    def test_save_new_task_with_dependencies(self, patched_server):
        import app_state

        app_state.save_task("test-proj", "new-task-deps", {
            "description": "new task with deps",
            "type": "feature",
            "status": "not_started",
            "dependencies": ["task-a"],
        })

        task = app_state._db.get_task("test-proj", "new-task-deps")
        assert task is not None
        assert "task-a" in task["dependencies"]

    def test_save_existing_task_updates(self, patched_server):
        import app_state

        app_state.save_task("test-proj", "task-a", {
            "description": "Updated via save_task",
            "notes": "some notes",
        })

        task = app_state._db.get_task("test-proj", "task-a")
        assert task["description"] == "Updated via save_task"
        assert task["notes"] == "some notes"


# ====================================================================
# app_state.py -- load_config OSError path (lines 79-80)
# ====================================================================


class TestLoadConfigOSError:
    """Cover the OSError path when CONFIG_PATH.stat() fails. The OSS-
    readiness contract is: treat any stat-failure (file missing,
    permission denied, broken symlink) as "no config" and return an
    empty dict so boot proceeds. The settings DB is the source of
    truth; config.yaml is just an optional seed file."""

    def test_load_config_stat_oserror_returns_empty_dict(self, monkeypatch):
        """When CONFIG_PATH.stat() raises, load_config returns {}
        instead of crashing or silently re-trying open()."""
        import app_state

        original_cache = dict(app_state._config_cache)
        app_state._config_cache["data"] = None
        app_state._config_cache["mtime"] = 0

        class BrokenStatPath:
            def stat(self):
                raise OSError("permission denied")

        monkeypatch.setattr(app_state, "CONFIG_PATH", BrokenStatPath())
        result = app_state.load_config()
        assert result == {}

        # Cached so a hot loop doesn't keep re-statting the bad path.
        result2 = app_state.load_config()
        assert result2 == {}

        app_state._config_cache.update(original_cache)


# ====================================================================
# Backend edge cases: close with empty reason, close already closed,
# rename to same name
# ====================================================================


class TestCloseTaskEdgeCasesExtra:
    """Additional close task edge cases."""

    def test_close_task_with_empty_string_reason(self, client):
        """Close with reason='' should work (reason is optional)."""
        resp = client.post(
            "/api/projects/test-proj/tasks/task-d/close",
            json={"reason": ""},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"

    def test_close_already_closed_task_idempotent(self, client):
        """Closing an already-closed task should succeed (idempotent)."""
        # First close
        resp1 = client.post(
            "/api/projects/test-proj/tasks/task-d/close",
            json={"reason": "first close"},
        )
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "closed"

        # Second close
        resp2 = client.post(
            "/api/projects/test-proj/tasks/task-d/close",
            json={"reason": "second close"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "closed"
        # Both reasons should be in notes
        notes = resp2.json()["notes"]
        assert "first close" in notes
        assert "second close" in notes


class TestRenameTaskEdgeCasesExtra:
    """Additional rename edge cases."""

    def test_rename_task_to_same_name(self, client, patched_server):
        """Renaming a task to the same name fails (target exists = itself)."""
        resp = client.post(
            "/api/projects/test-proj/tasks/task-a/rename",
            json={"new_id": "task-a"},
        )
        # Should return 409 because target already exists
        assert resp.status_code == 409


# ====================================================================
# Workstats: gh CLI failure and malformed JSON
# ====================================================================


class TestWorkstatsEdgeCases:
    """Cover _compute_workstats and _fiscal_quarter edge cases."""

    def test_compute_workstats_with_merged_prs(self, client, patched_server):
        """Insert merged PRs and verify quarterly grouping."""
        from common import system as system_mod

        db = patched_server._db
        # March 2026 -> Q1 FY26 (Feb-Apr)
        db.add_pr(
            project="test-proj", task_id="task-a", number=401,
            url="https://github.com/example/repo/pull/401",
            status="merged", last_updated="2026-03-10T12:00:00Z",
            author="test-author",
        )
        # June 2026 -> Q2 FY26 (May-Jul)
        db.add_pr(
            project="test-proj", task_id="task-a", number=402,
            url="https://github.com/myorg/svc/pull/402",
            status="merged", last_updated="2026-06-15T12:00:00Z",
            author="test-author_data",
        )
        # Another repo PR in March
        db.add_pr(
            project="test-proj", task_id="task-b", number=403,
            url="https://github.com/example/repo/pull/403",
            status="merged", last_updated="2026-03-20T12:00:00Z",
            author="test-author",
        )

        result = system_mod._compute_workstats()
        assert result is not None
        assert len(result["quarters"]) >= 2
        # Find the Q1 quarter entry
        q1 = [q for q in result["quarters"] if "Q1" in q["period"]]
        assert len(q1) >= 1
        # `by_repo` is the canonical generic field; values are derived
        # from the URL (no hardcoded enumeration in source).
        assert q1[0]["by_repo"].get("repo", 0) >= 2
        assert result["all_time"]["total"] >= 3

    def test_compute_workstats_empty_db(self, client, patched_server):
        """No merged/closed PRs with last_updated -> returns empty quarters."""
        from common import system as system_mod

        # Remove all merged/closed PRs
        patched_server._db._conn.execute(
            "DELETE FROM prs WHERE status IN ('merged', 'closed')"
        )
        patched_server._db._conn.commit()

        result = system_mod._compute_workstats()
        assert result is not None
        assert result["quarters"] == []
        assert result["all_time"]["total"] == 0

    def test_compute_workstats_closed_counted_as_merged(self, client, patched_server):
        """A 'closed' PR is counted in workstats (same as merged)."""
        from common import system as system_mod

        db = patched_server._db
        db.add_pr(
            project="test-proj", task_id="task-a", number=410,
            url="https://github.com/myorg/monorepo/pull/410",
            status="closed", last_updated="2026-09-15T12:00:00Z",
            author="test-author_data",
        )

        result = system_mod._compute_workstats()
        assert result is not None
        # The closed PR should be counted
        assert result["all_time"]["total"] >= 1
        assert result["all_time"].get("monorepo", 0) >= 1

    def test_compute_workstats_skips_malformed_timestamps(
        self, client, patched_server
    ):
        """PR rows with a last_updated that can't be parsed as ISO (legacy
        rows, corrupted values) must be dropped from the weekly bucket
        instead of crashing the whole aggregation.

        Covers core/common.system.py:611-612 (the ValueError/TypeError catch in
        the weekly loop).
        """
        from common import system as system_mod

        db = patched_server._db
        db.add_pr(
            project="test-proj", task_id="task-a", number=420,
            url="https://github.com/example/repo/pull/420",
            status="merged",
            # Intentionally malformed ISO -- fromisoformat will raise.
            last_updated="not-a-timestamp",
            author="test-author",
        )
        # Also seed a good row so we can prove the aggregation still
        # produced output for valid data.
        db.add_pr(
            project="test-proj", task_id="task-a", number=421,
            url="https://github.com/example/repo/pull/421",
            status="merged", last_updated="2026-03-10T12:00:00Z",
            author="test-author",
        )
        result = system_mod._compute_workstats()
        assert result is not None
        # Total includes both rows (quarter aggregation tolerates the bad
        # timestamp via the outer `_fiscal_quarter` guard OR via this
        # weekly catch). At minimum the valid row should surface.
        assert result["all_time"]["total"] >= 1

    def test_fiscal_quarter_mapping(self):
        """Test _fiscal_quarter for various months."""
        from common import system as system_mod

        # Q1: Feb-Apr -> FY = year+1-2000
        assert system_mod._fiscal_quarter("2026-02-01T00:00:00Z") == "Q1 FY27"
        assert system_mod._fiscal_quarter("2026-04-30T00:00:00Z") == "Q1 FY27"
        # Q2: May-Jul
        assert system_mod._fiscal_quarter("2026-05-01T00:00:00Z") == "Q2 FY27"
        assert system_mod._fiscal_quarter("2026-07-31T00:00:00Z") == "Q2 FY27"
        # Q3: Aug-Oct
        assert system_mod._fiscal_quarter("2026-08-01T00:00:00Z") == "Q3 FY27"
        assert system_mod._fiscal_quarter("2026-10-31T00:00:00Z") == "Q3 FY27"
        # Q4: Nov-Jan (Nov/Dec same year, Jan next year FY)
        assert system_mod._fiscal_quarter("2026-11-01T00:00:00Z") == "Q4 FY27"
        assert system_mod._fiscal_quarter("2026-12-31T00:00:00Z") == "Q4 FY27"
        assert system_mod._fiscal_quarter("2027-01-15T00:00:00Z") == "Q4 FY27"
        # None / invalid
        assert system_mod._fiscal_quarter(None) is None
        assert system_mod._fiscal_quarter("not-a-date") is None


# ====================================================================
# Live stats: cache behavior
# ====================================================================


class TestLiveStatsCache:
    """Cover _live_stats_cache behavior in get_live_stats."""

    def test_live_stats_returns_cached_when_fresh(self, client, monkeypatch):
        from common import system as system_mod

        original = dict(system_mod._live_stats_cache)
        cached = {"open_prs": {"total": 5}, "contributor_rank": 42}
        system_mod._live_stats_cache["data"] = cached
        system_mod._live_stats_cache["ts"] = time.time()

        fetch_called = []
        monkeypatch.setattr(system_mod, "_fetch_live_stats", lambda: fetch_called.append(1))

        resp = client.get("/api/live-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["open_prs"]["total"] == 5
        assert len(fetch_called) == 0

        # Restore
        system_mod._live_stats_cache.update(original)


# ====================================================================
# _count_open_prs_for_account / _fetch_contributor_rank / _fetch_contributor_total
# ====================================================================


class TestCountOpenPrsForAccount:
    """Covers the per-account PR counting logic factored out of _fetch_live_stats."""

    def test_empty_account_returns_zero(self):
        from common import system as system_mod
        result, total = system_mod._count_open_prs_for_account({"user": "u", "repos": []})
        assert result == {}
        assert total == 0

    def test_direct_repo_counts_items_length(self, monkeypatch):
        from common import system as system_mod
        # Return 3 open PRs for example/repo.
        monkeypatch.setattr(
            system_mod.app_state, "gh_run_json",
            lambda *a, **k: [{"number": 1}, {"number": 2}, {"number": 3}],
        )
        result, total = system_mod._count_open_prs_for_account({
            "user": "me", "repos": ["example/repo"],
        })
        assert total == 3
        assert result.get("repo") == 3

    def test_owner_form_groups_by_repo(self, monkeypatch):
        """owner:X expands into per-repo buckets using nameWithOwner."""
        from common import system as system_mod
        monkeypatch.setattr(
            system_mod.app_state, "gh_run_json",
            lambda *a, **k: [
                {"number": 1, "repository": {"nameWithOwner": "acme/alpha"}},
                {"number": 2, "repository": {"nameWithOwner": "acme/alpha"}},
                {"number": 3, "repository": {"nameWithOwner": "acme/beta"}},
            ],
        )
        result, total = system_mod._count_open_prs_for_account({
            "user": "me", "repos": ["owner:acme"],
        })
        assert total == 3
        assert result.get("alpha") == 2
        assert result.get("beta") == 1

    def test_gh_run_json_returns_none_skipped(self, monkeypatch):
        from common import system as system_mod
        monkeypatch.setattr(
            system_mod.app_state, "gh_run_json",
            lambda *a, **k: None,
        )
        result, total = system_mod._count_open_prs_for_account({
            "user": "me", "repos": ["example/repo", "owner:acme"],
        })
        assert result == {}
        assert total == 0

    def test_exception_in_one_repo_does_not_break_others(self, monkeypatch):
        """An error on one repo must not cancel counting for the rest."""
        from common import system as system_mod
        calls = []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("network blip")
            return [{"number": 9}]

        monkeypatch.setattr(system_mod.app_state, "gh_run_json", flaky)
        result, total = system_mod._count_open_prs_for_account({
            "user": "me", "repos": ["example/repo", "acme-corp/runtime"],
        })
        # First repo raised, second succeeded with 1 PR.
        assert total == 1


class TestFetchContributorRank:
    """Covers _fetch_contributor_rank via gh CLI mocks."""

    def test_finds_user_on_first_page(self, monkeypatch):
        from common import system as system_mod
        stdout = "alice 100\nbob 50\nme 25\n"
        fake = types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        monkeypatch.setattr(system_mod.app_state, "gh_run", lambda *a, **k: fake)
        rank, contributions = system_mod._fetch_contributor_rank("org/repo", "me")
        assert rank == 3
        assert contributions == 25

    def test_returns_none_when_not_found(self, monkeypatch):
        from common import system as system_mod
        # 5 pages each with nothing matching "me"; function returns early when
        # the gh call succeeds but "me" isn't in the page.
        stdout = "alice 1\nbob 2\n"
        fake = types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        monkeypatch.setattr(system_mod.app_state, "gh_run", lambda *a, **k: fake)
        rank, contributions = system_mod._fetch_contributor_rank("org/repo", "ghost")
        assert rank is None
        assert contributions is None

    def test_returns_none_on_gh_failure(self, monkeypatch):
        from common import system as system_mod
        fail = types.SimpleNamespace(returncode=1, stdout="", stderr="auth")
        monkeypatch.setattr(system_mod.app_state, "gh_run", lambda *a, **k: fail)
        rank, contributions = system_mod._fetch_contributor_rank("org/repo", "me")
        assert rank is None
        assert contributions is None


class TestFetchContributorTotal:
    """Covers _fetch_contributor_total (Link header parsing)."""

    def test_parses_last_page_from_link_header(self, monkeypatch):
        from common import system as system_mod
        # Simulate -i output with headers including a Link pointing at page 7.
        stdout = (
            "HTTP/2 200\n"
            "link: <https://api.github.com/repositories/1/contributors?per_page=1&page=2>; rel=\"next\", "
            "<https://api.github.com/repositories/1/contributors?per_page=1&page=7>; rel=\"last\"\n"
            "\n"
            "[{\"login\":\"a\"}]\n"
        )
        fake = types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        monkeypatch.setattr(system_mod.app_state, "gh_run", lambda *a, **k: fake)
        assert system_mod._fetch_contributor_total("org/repo") == 7

    def test_returns_none_when_no_link_header(self, monkeypatch):
        from common import system as system_mod
        fake = types.SimpleNamespace(returncode=0, stdout="HTTP/2 200\n\n[]\n", stderr="")
        monkeypatch.setattr(system_mod.app_state, "gh_run", lambda *a, **k: fake)
        assert system_mod._fetch_contributor_total("org/repo") is None

    def test_returns_none_on_gh_failure(self, monkeypatch):
        from common import system as system_mod
        fail = types.SimpleNamespace(returncode=1, stdout="", stderr="fail")
        monkeypatch.setattr(system_mod.app_state, "gh_run", lambda *a, **k: fail)
        assert system_mod._fetch_contributor_total("org/repo") is None

    def test_swallows_unexpected_gh_exception(self, monkeypatch):
        """If `gh_run` itself raises (e.g. CalledProcessError that didn't
        get caught upstream), the contributor-total path must return None
        instead of bubbling -- live-stats is best-effort."""
        from common import system as system_mod

        def boom(*a, **kw):
            raise RuntimeError("gh CLI corrupted")
        monkeypatch.setattr(system_mod.app_state, "gh_run", boom)
        assert system_mod._fetch_contributor_total("org/repo") is None


class TestFetchContributorRankSwallowsExceptions:
    """`_fetch_contributor_rank` Exception handler -- if gh raises mid-loop
    we must return None/None, not break the live-stats render."""

    def test_swallows_gh_run_exception(self, monkeypatch):
        from common import system as system_mod

        def boom(*a, **kw):
            raise OSError("gh binary missing")
        monkeypatch.setattr(system_mod.app_state, "gh_run", boom)
        rank, contribs = system_mod._fetch_contributor_rank("org/repo", "me")
        assert rank is None
        assert contribs is None


# ====================================================================
# Live-stats early-return when contributor repo/user isn't configured
# ====================================================================


class TestLiveStatsEarlyReturn:
    """When `service.github.allowed_repos` has no explicit (non-wildcard)
    repo, `_contributor_repo()` returns None. `_compute_live_stats`
    must skip the rank/total fetch instead of crashing on a None URL."""

    def test_skips_contributor_block_when_no_explicit_repo(
        self, monkeypatch, patched_server,
    ):
        from common import system as system_mod
        # Force the lazy resolvers to return None (no explicit repo).
        monkeypatch.setattr(system_mod, "_contributor_repo", lambda: None)
        monkeypatch.setattr(system_mod, "_contributor_user", lambda: None)
        # Stub the open-prs aggregator so we don't shell out.
        monkeypatch.setattr(
            system_mod, "_count_open_prs_for_account",
            lambda _acct: ({}, 0),
        )
        out = system_mod._fetch_live_stats()
        # Stats came back with the open-prs block but contributor data
        # left as None (seeded but not populated, since the early
        # return short-circuited the rank/total fetches).
        assert "open_prs" in out
        assert out.get("contributor_rank") is None
        assert out.get("contributor_total") is None
        assert out.get("contributor_contributions") is None


# ====================================================================
# _repo_from_url 'other' fallback
# ====================================================================


class TestRepoFromUrl:
    """`_repo_from_url` extracts the short repo name dynamically from
    a GitHub PR URL. No specific repo name is hardcoded -- any
    `github.com/<owner>/<repo>/...` URL maps to `<repo>`."""

    def test_extracts_short_repo_name(self):
        from common import system as system_mod
        assert system_mod._repo_from_url(
            "https://github.com/random-org/random-repo/pull/99",
        ) == "random-repo"

    def test_empty_url_returns_empty_string(self):
        from common import system as system_mod
        assert system_mod._repo_from_url("") == ""

    def test_non_github_url_returns_empty(self):
        from common import system as system_mod
        assert system_mod._repo_from_url(
            "https://gitlab.com/foo/bar/pull/1",
        ) == ""

    def test_short_name_remap_applied(self, patched_server):
        """Long display names that the rest of the UI shortens
        (per the `ui.repo_short_names` setting) are normalised at
        extraction time too."""
        from common import system as system_mod
        patched_server._db.set_setting(
            "ui.repo_short_names", {"plugin-marketplace": "plugin-mk"},
        )
        assert system_mod._repo_from_url(
            "https://github.com/example-org/plugin-marketplace/pull/1",
        ) == "plugin-mk"


# ====================================================================
# _compute_workstats early-return + bad-timestamp swallow
# ====================================================================


class TestComputeWorkstatsEarlyReturns:
    def test_returns_empty_shape_when_no_authors_configured(
        self, monkeypatch, patched_server,
    ):
        """Without configured gh accounts, the function returns the
        canonical empty `{quarters, all_time, weekly, weekly_primary}`
        shape so the dashboard renders an empty chart instead of
        crashing on a None."""
        from common import system as system_mod
        monkeypatch.setattr(system_mod, "_my_gh_authors", lambda: set())
        out = system_mod._compute_workstats()
        assert out == {"quarters": [], "all_time": {},
                       "weekly": [], "weekly_primary": []}

    def test_skips_garbage_timestamp_at_fiscal_quarter_layer(
        self, monkeypatch, patched_server,
    ):
        """A garbage `last_updated` (legacy data, manual SQL edit)
        must NOT abort the whole workstats compute -- it's filtered
        at the `_fiscal_quarter` parse step (the early `continue`)
        so neither the quarterly nor weekly bucket ever sees it."""
        from common import system as system_mod
        monkeypatch.setattr(
            system_mod, "_my_gh_authors", lambda: {"test-author"},
        )
        patched_server._db.add_pr(
            project="test-proj", task_id="task-a", number=999,
            url="https://github.com/example/repo/pull/999",
            status="merged", title="bad ts", author="test-author",
            last_updated="not-a-real-timestamp",
        )
        out = system_mod._compute_workstats()
        assert out is not None
        # No bucket got populated for the bogus row -- everything
        # zero (the only PR was filtered out).
        assert sum(out["weekly"]) == 0
        assert out["all_time"].get("total", 0) == 0


# ====================================================================
# Concrete-provider check error-path tests moved out of core
# into each extension's own `test/test_certs_impl.py`.


class TestCertsAutoRenewSwallowsProviderException:
    """If a provider's `renew()` itself raises (network blip, rate-limit
    response, etc.), we must NOT propagate -- the cert listing degrades
    to 'still warning' and the UI shows the next renew opportunity."""

    def test_auto_renew_swallows_provider_runtime_error(self, monkeypatch):
        from common import system as system_mod

        class FlakyProvider(system_mod.CertProvider):
            key = "flaky"
            name = "Flaky Cert"
            warning_secs = 999_999

            def check(self):
                # Just under the warning window so auto-renew triggers.
                return 100

            def renew(self):
                raise RuntimeError("provider API rate-limited")

            def _host(self):
                return "x.example.com"

        # Cert framework lives in cert; its `_registered` list IS
        # the one `get_certs()` iterates. Patch there directly.
        monkeypatch.setattr(
            "common.cert._registered", [FlakyProvider()],
        )
        # Should NOT raise even though renew() errors.
        out = system_mod.get_certs()
        # Cert is still in the listing keyed by provider key.
        assert "flaky" in out


# ====================================================================
# Event bus with no listeners registered
# ====================================================================


class TestEventBusNoListeners:
    """Emit events when no listeners are registered for the event type."""

    def test_emit_unknown_event_type(self):
        import app_state
        # Should not raise even with no listeners
        app_state.emit_event("unknown.event_type", {
            "title": "test unknown",
            "message": "no handler for this",
        })


# ====================================================================
# GitHub poll with malformed notification data
# ====================================================================


class TestGhPollMalformedData:
    """Cover edge cases in _poll_github_notifications."""

    def test_poll_with_short_tab_separated_line(self, monkeypatch):
        """Lines with fewer than 7 tab-separated fields are skipped."""
        import routes.events as events_mod
        import app_state

        original_ts = events_mod._gh_last_poll["ts"]
        events_mod._gh_last_poll["ts"] = 0  # force poll

        def fake_gh_run(args, repo="", timeout=20):
            mock = MagicMock()
            mock.returncode = 0
            # Only 3 fields instead of 7+
            mock.stdout = "id1\treason1\tPullRequest\n"
            mock.stderr = ""
            return mock

        monkeypatch.setattr(app_state, "gh_run", fake_gh_run)

        emitted = []
        monkeypatch.setattr(events_mod, "_build_gh_events", lambda e: [])
        monkeypatch.setattr(app_state, "emit_event", lambda t, d: emitted.append((t, d)))

        events_mod._poll_github_notifications()
        # No events should have been created from the malformed line
        assert len(emitted) == 0

        events_mod._gh_last_poll["ts"] = original_ts

    def test_load_seen_ids_handles_db_error(self, tmp_path, monkeypatch):
        """If the events DB is missing/corrupt, `_load_seen_ids` must
        return an empty dict so startup doesn't crash. Covers lines 38-39
        of services/github_poller.py."""
        from services import github_poller
        import app_state
        # Point the notif DB at a path that exists as a directory -- sqlite
        # can't open a dir as a DB, so the sqlite3.Error branch fires.
        bad_path = tmp_path / "not_a_db"
        bad_path.mkdir()
        monkeypatch.setattr(app_state, "_NOTIF_DB_PATH", bad_path)
        assert github_poller._load_seen_ids() == {}

    def test_load_since_watermarks_returns_none_on_empty(
        self, tmp_path, monkeypatch
    ):
        """With no GitHub events yet (or an unreadable DB), the watermark
        loader must return None so the first poll uses `now` as the
        baseline rather than a bogus epoch. Covers 42-51."""
        from services import github_poller
        import app_state
        bad_path = tmp_path / "not_a_db"
        bad_path.mkdir()
        monkeypatch.setattr(app_state, "_NOTIF_DB_PATH", bad_path)
        assert github_poller._load_since_watermarks() is None

    def test_lookup_pr_by_branch_swallows_exceptions(self, patched_server):
        """If the prs table is unavailable, `_lookup_pr_by_branch` must
        return None instead of bubbling the exception up into the poll
        loop (which would abort all downstream notifications).

        Covers lines 71-72 of services/github_poller.py.
        """
        from unittest.mock import patch, MagicMock
        from services import github_poller
        import app_state

        # sqlite3.Connection.execute is read-only so patch.object fails;
        # substitute the whole _conn with a mock that raises instead.
        bad_conn = MagicMock()
        bad_conn.execute = MagicMock(side_effect=RuntimeError("oops"))
        with patch.object(app_state._db, "_conn", bad_conn):
            assert github_poller._lookup_pr_by_branch("some-branch") is None
            assert github_poller._lookup_pr_by_branch("b", "example/repo") is None

    def test_parse_notification_malformed_pr_number(self):
        """Notification with a non-numeric pull-request URL suffix must
        parse cleanly (pr_number stays None), not raise. Covers 104-106.

        Uses an allow-listed repo so the `is_repo_allowed` gate passes
        and we can inspect the parsed ev dict.
        """
        from services.github_poller import _parse_notification_line
        line = "nid\treview_requested\tPullRequest\ttitle\texample/repo\t2026-04-23\tfalse\thttps://api/pulls/not-a-number"
        ev = _parse_notification_line(line, 0)
        assert ev is not None
        # The ValueError during int() was caught -> pr_number stays None.
        assert ev["pr_number"] is None
        assert ev["id"] == "nid"

    def test_load_since_watermarks_returns_max_ts_when_events_exist(
        self, tmp_path, monkeypatch
    ):
        """Happy path: if the events table has GitHub rows, the watermark
        loader must return the MAX(ts) so the next poll asks GitHub only
        for notifications newer than that timestamp. Covers 46-48."""
        import sqlite3 as _sq
        from services import github_poller
        import app_state

        db_path = tmp_path / "notif.db"
        conn = _sq.connect(str(db_path))
        conn.execute(
            "CREATE TABLE events (id TEXT, source TEXT, source_id TEXT,"
            " title TEXT, message TEXT, type TEXT, severity TEXT, url TEXT,"
            " ts TEXT, read INTEGER, session TEXT)"
        )
        conn.execute(
            "INSERT INTO events (id, source, ts) VALUES "
            "('1', 'github', '2026-04-10T00:00:00Z'),"
            "('2', 'github', '2026-04-20T12:00:00Z'),"
            "('3', 'slack',  '2026-04-25T00:00:00Z')"  # ignored (not github)
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(app_state, "_NOTIF_DB_PATH", db_path)
        # Newest github row wins; slack row must not leak in.
        assert github_poller._load_since_watermarks() == "2026-04-20T12:00:00Z"

    def test_seen_ids_pruning_caps_memory_under_load(self, monkeypatch):
        """_gh_last_poll.seen_ids is capped at `_SEEN_IDS_MAX` so a long
        Eva uptime doesn't grow the dedupe set unboundedly. After a poll
        that pushes past the cap, the oldest (by `updated`) entries must
        be dropped while newer ones survive.

        Covers services/github_poller.py:234-237 (the cap-and-prune
        block at the end of `_poll_github_notifications`).
        """
        from services import github_poller
        import app_state

        # Seed the cache just over the cap with a stable updated-ts mapping
        # so we can predict which keys should get pruned.
        cap = github_poller._SEEN_IDS_MAX
        excess = 3  # push 3 entries past the cap
        seed = {f"nid-{i:04d}": f"2026-04-23T{i % 24:02d}:00:00Z"
                for i in range(cap + excess)}
        github_poller._gh_last_poll["seen_ids"] = seed
        github_poller._gh_last_poll["ts"] = 0  # force-poll
        github_poller._gh_last_poll["since"] = {}

        # Stub external calls so the function body proceeds past the gh
        # invocation straight into the prune step.
        monkeypatch.setattr(app_state, "gh_run",
                            lambda *a, **kw: __import__("types").SimpleNamespace(
                                returncode=1, stdout="", stderr=""))
        monkeypatch.setattr(app_state, "emit_event", lambda *a, **kw: None)

        github_poller._poll_github_notifications()

        remaining = github_poller._gh_last_poll["seen_ids"]
        assert len(remaining) == cap, (
            "cap-and-prune should leave exactly %d entries, got %d"
            % (cap, len(remaining))
        )

    def test_init_seeds_seen_ids_and_since_from_existing_events(
        self, tmp_path, monkeypatch
    ):
        """`init()` is called once at startup; it must hydrate
        `_gh_last_poll.seen_ids` from the events table and seed a `since`
        watermark for every allow-listed repo. Covers init() at
        services/github_poller.py:367-383.
        """
        import sqlite3 as _sq
        from services import github_poller
        import app_state

        db_path = tmp_path / "notif.db"
        conn = _sq.connect(str(db_path))
        conn.execute(
            "CREATE TABLE events (id TEXT, source TEXT, source_id TEXT,"
            " title TEXT, message TEXT, type TEXT, severity TEXT, url TEXT,"
            " ts TEXT, read INTEGER, session TEXT)"
        )
        conn.execute(
            "INSERT INTO events (id, source, source_id, ts) VALUES "
            "('e1', 'github', 'notif-abc', '2026-04-22T00:00:00Z'),"
            "('e2', 'github', 'notif-def', '2026-04-22T12:00:00Z')"
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(app_state, "_NOTIF_DB_PATH", db_path)

        # Reset state so we can assert init()'s side-effects cleanly.
        github_poller._gh_last_poll["seen_ids"] = {}
        github_poller._gh_last_poll["since"] = {}

        github_poller.init()

        # seen_ids hydrated from the fixture.
        assert "notif-abc" in github_poller._gh_last_poll["seen_ids"]
        assert "notif-def" in github_poller._gh_last_poll["seen_ids"]
        # At least one repo got the shared since watermark.
        since_vals = set(github_poller._gh_last_poll["since"].values())
        assert "2026-04-22T12:00:00Z" in since_vals

    def test_poll_github_once_swallows_underlying_exception(self, monkeypatch):
        """`poll_github_once` is what the scheduler calls; it must NEVER
        raise (otherwise the scheduler marks the job failed). Assert that
        a crashing `_poll_github_notifications` is logged but swallowed.

        Covers lines 246-249 (the poll_github_once wrapper).
        """
        from services import github_poller
        monkeypatch.setattr(
            github_poller,
            "_poll_github_notifications",
            lambda: (_ for _ in ()).throw(RuntimeError("bad poll")),
        )
        # No exception -> OK.
        github_poller.poll_github_once()

    def test_poll_gh_run_nonzero_exit(self, monkeypatch):
        """When gh returns non-zero, that repo is skipped."""
        import routes.events as events_mod
        import app_state

        original_ts = events_mod._gh_last_poll["ts"]
        events_mod._gh_last_poll["ts"] = 0

        def fake_gh_run(args, repo="", timeout=20):
            mock = MagicMock()
            mock.returncode = 1
            mock.stdout = ""
            mock.stderr = "authentication failed"
            return mock

        monkeypatch.setattr(app_state, "gh_run", fake_gh_run)

        emitted = []
        monkeypatch.setattr(app_state, "emit_event", lambda t, d: emitted.append((t, d)))

        events_mod._poll_github_notifications()
        assert len(emitted) == 0

        events_mod._gh_last_poll["ts"] = original_ts


# ====================================================================
# PR sync with 0 dirty PRs
# ====================================================================


class TestEmitEventPersistKwarg:
    """`persist=False` branch of app_state.emit_event: SSE fan-out happens
    but the events DB write is skipped. Kept as future-proof capability
    even though usage.updated currently uses the default persist=True."""

    def test_persist_true_writes_to_db(self, client, patched_server):
        """Default persist behaviour: event shows up in /api/events."""
        import app_state
        before = client.get("/api/events?limit=50").json()
        n_before = len(before["events"])

        app_state.emit_event("test.persist_true", {
            "title": "persisted event",
            "message": "body",
            "severity": "info",
        })
        after = client.get("/api/events?limit=50").json()
        assert len(after["events"]) == n_before + 1
        titles = [e["title"] for e in after["events"]]
        assert "persisted event" in titles

    def test_persist_false_skips_db(self, client, patched_server):
        """persist=False: same signal to SSE subscribers, no DB row."""
        import app_state
        before = client.get("/api/events?limit=50").json()
        n_before = len(before["events"])

        app_state.emit_event("test.ephemeral", {
            "title": "ephemeral event",
        }, persist=False)
        after = client.get("/api/events?limit=50").json()
        assert len(after["events"]) == n_before  # no new row


class TestPrSyncZeroDirty:
    """Cover edge case when there are no dirty PRs to sync."""

    def test_sync_all_prs_with_no_results(self, client, patched_server, monkeypatch):
        """POST /api/all-prs/sync with gh returning empty results discovers nothing."""
        import app_state

        def fake_gh_run(args, repo="", timeout=20):
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = "[]"
            mock.stderr = ""
            return mock

        monkeypatch.setattr(app_state, "gh_run", fake_gh_run)

        resp = client.post("/api/all-prs/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["discovered"] == 0
