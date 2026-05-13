"""Tests for app_state module -- gh_run_json, repo helpers, config, events, migration."""

import json
import shutil
import sqlite3
import threading
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

import app_state


# ---- gh_run_json ----


class TestGhRunJson:
    """Tests for the gh_run_json helper."""

    def _make_result(self, returncode=0, stdout="", stderr=""):
        r = types.SimpleNamespace()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = stderr
        return r

    def test_success_parses_json(self):
        data = {"number": 42, "title": "Fix bug"}
        with patch.object(app_state, "gh_run", return_value=self._make_result(stdout=json.dumps(data))):
            result = app_state.gh_run_json(["gh", "pr", "view", "42"], repo="example/repo")
        assert result == data

    def test_failure_returns_default_none(self):
        with patch.object(app_state, "gh_run", return_value=self._make_result(returncode=1, stderr="error")):
            result = app_state.gh_run_json(["gh", "pr", "view", "99"], repo="example/repo")
        assert result is None

    def test_failure_returns_custom_default(self):
        with patch.object(app_state, "gh_run", return_value=self._make_result(returncode=1)):
            result = app_state.gh_run_json(["gh", "api", "foo"], default=[])
        assert result == []

    def test_invalid_json_returns_default(self):
        with patch.object(app_state, "gh_run", return_value=self._make_result(stdout="not json {{")):
            result = app_state.gh_run_json(["gh", "api", "foo"])
        assert result is None

    def test_invalid_json_returns_custom_default(self):
        with patch.object(app_state, "gh_run", return_value=self._make_result(stdout="<html>error</html>")):
            result = app_state.gh_run_json(["gh", "api", "foo"], default={"error": True})
        assert result == {"error": True}

    def test_empty_stdout_returns_default(self):
        with patch.object(app_state, "gh_run", return_value=self._make_result(stdout="")):
            result = app_state.gh_run_json(["gh", "api", "foo"])
        assert result is None

    def test_passes_repo_and_timeout(self):
        with patch.object(app_state, "gh_run", return_value=self._make_result(stdout="{}")) as mock_run:
            app_state.gh_run_json(["gh", "api", "foo"], repo="myorg/svc", timeout=30)
        mock_run.assert_called_once_with(["gh", "api", "foo"], repo="myorg/svc", timeout=30)

    def test_array_json_response(self):
        data = [{"number": 1}, {"number": 2}]
        with patch.object(app_state, "gh_run", return_value=self._make_result(stdout=json.dumps(data))):
            result = app_state.gh_run_json(["gh", "search", "prs"])
        assert result == data


# ---- is_repo_allowed ----


class TestIsRepoAllowed:
    """Allow-list tests that monkeypatch ALLOWED_REPOS / FORK_TO_UPSTREAM
    with generic acme/widgets-style values so the assertions don't
    encode the maintainer's specific config. An open-source fork's
    ALLOWED_REPOS comes from the settings table, not the source."""

    def _patch(self, monkeypatch, *, allowed, forks):
        from adapters import github as gh
        monkeypatch.setattr(gh, "ALLOWED_REPOS", set(allowed))
        monkeypatch.setattr(gh, "FORK_TO_UPSTREAM", dict(forks))
        monkeypatch.setattr(
            gh, "ALLOWED_ORGS",
            {r.split("/")[0] for r in allowed if r.endswith("/*")},
        )

    def test_exact_match(self, monkeypatch):
        from _test_constants import TEST_OSS_REPO
        self._patch(monkeypatch, allowed=[TEST_OSS_REPO], forks={})
        assert app_state.is_repo_allowed(TEST_OSS_REPO) is True

    def test_wildcard_match(self, monkeypatch):
        from _test_constants import (
            TEST_COMPANY_ORG, TEST_COMPANY_REPO_RUNTIME,
            TEST_COMPANY_REPO_PLATFORM,
        )
        self._patch(monkeypatch, allowed=[f"{TEST_COMPANY_ORG}/*"], forks={})
        assert app_state.is_repo_allowed(TEST_COMPANY_REPO_RUNTIME) is True
        assert app_state.is_repo_allowed(TEST_COMPANY_REPO_PLATFORM) is True

    def test_fork_resolution(self, monkeypatch):
        from _test_constants import (
            TEST_OSS_REPO, TEST_OSS_FORK,
            TEST_COMPANY_ORG, TEST_COMPANY_FORK,
        )
        self._patch(
            monkeypatch,
            allowed=[TEST_OSS_REPO, f"{TEST_COMPANY_ORG}/*"],
            forks={
                TEST_OSS_FORK: TEST_OSS_REPO,
                TEST_COMPANY_FORK: f"{TEST_COMPANY_ORG}/runtime",
            },
        )
        assert app_state.is_repo_allowed(TEST_OSS_FORK) is True
        assert app_state.is_repo_allowed(TEST_COMPANY_FORK) is True

    def test_not_allowed(self, monkeypatch):
        self._patch(monkeypatch, allowed=["acme/widgets"], forks={})
        assert app_state.is_repo_allowed("random-org/random-repo") is False

    def test_empty_string(self):
        assert app_state.is_repo_allowed("") is False

    def test_none_like(self):
        assert app_state.is_repo_allowed("") is False


# ---- _parse_pr_number ----


class TestParsePrNumber:
    """URL-format parser tests. Inputs use generic repos via the
    `pr_url()` helper -- the parser is repo-agnostic, so the assertions
    test the format handling, not maintainer-specific repos."""

    def test_valid_url(self):
        from _test_constants import pr_url
        assert app_state._parse_pr_number(pr_url(number=100)) == 100

    def test_valid_url_trailing_slash(self):
        from _test_constants import pr_url
        assert app_state._parse_pr_number(pr_url(number=200) + "/") == 200

    def test_invalid_url(self):
        assert app_state._parse_pr_number("not-a-url") is None

    def test_no_pull_segment(self):
        from _test_constants import TEST_OSS_REPO
        assert app_state._parse_pr_number(
            f"https://github.com/{TEST_OSS_REPO}/issues/100"
        ) is None

    def test_empty_string(self):
        assert app_state._parse_pr_number("") is None

    def test_url_with_query_params(self):
        from _test_constants import pr_url
        assert app_state._parse_pr_number(
            pr_url(number=300) + "?diff=unified",
        ) == 300


# ---- _build_repo_authors ----


class TestBuildRepoAuthors:
    """Repo-author resolver tests with monkeypatched config + tokens
    so the assertions are about the resolver's structure, not the
    maintainer's specific gh accounts."""

    def _setup(self, monkeypatch, *, allowed, forks, account_rules,
               tokens):
        from adapters import github as gh
        monkeypatch.setattr(gh, "ALLOWED_REPOS", set(allowed))
        monkeypatch.setattr(gh, "FORK_TO_UPSTREAM", dict(forks))
        monkeypatch.setattr(
            gh, "ALLOWED_ORGS",
            {r.split("/")[0] for r in allowed if r.endswith("/*")},
        )
        monkeypatch.setattr(gh, "_account_rules", list(account_rules))
        gh._gh_tokens.clear()
        gh._gh_tokens.update(tokens)

    def test_correct_mapping(self, monkeypatch):
        from _test_constants import (
            TEST_OSS_REPO, TEST_COMPANY_ORG,
            TEST_USER_LOGIN, TEST_USER_LOGIN_ALT,
        )
        # Account rules `match` is a substring -- the org name alone
        # matches any repo under that org.
        self._setup(
            monkeypatch,
            allowed=[TEST_OSS_REPO, f"{TEST_COMPANY_ORG}/*"],
            forks={},
            account_rules=[
                {"match": TEST_COMPANY_ORG,
                 "account": TEST_USER_LOGIN_ALT},
                {"match": "", "account": TEST_USER_LOGIN},  # catch-all
            ],
            tokens={TEST_USER_LOGIN: "tok1", TEST_USER_LOGIN_ALT: "tok2"},
        )
        result = app_state._build_repo_authors()
        # Explicit repo -> direct entry.
        assert TEST_OSS_REPO in result
        # Wildcard -> "owner:<org>" entry.
        assert f"owner:{TEST_COMPANY_ORG}" in result
        # Values are the gh accounts from the rules.
        assert result[TEST_OSS_REPO] == TEST_USER_LOGIN
        assert result[f"owner:{TEST_COMPANY_ORG}"] == TEST_USER_LOGIN_ALT

    def test_no_duplicate_entries(self, monkeypatch):
        from _test_constants import (
            TEST_OSS_REPO, TEST_OSS_FORK,
            TEST_COMPANY_ORG, TEST_COMPANY_FORK,
            TEST_USER_LOGIN,
        )
        self._setup(
            monkeypatch,
            allowed=[TEST_OSS_REPO, f"{TEST_COMPANY_ORG}/*"],
            forks={
                TEST_OSS_FORK: TEST_OSS_REPO,
                TEST_COMPANY_FORK: f"{TEST_COMPANY_ORG}/runtime",
            },
            account_rules=[],
            tokens={TEST_USER_LOGIN: "tok1"},
        )
        result = app_state._build_repo_authors()
        # Forks resolving to already-covered upstreams must not create
        # additional fork-side entries.
        assert TEST_OSS_FORK not in result
        assert TEST_COMPANY_FORK not in result

    def test_fork_with_uncovered_upstream_gets_added(self, monkeypatch):
        """When FORK_TO_UPSTREAM points at an upstream NOT in
        ALLOWED_REPOS (and not under any allowed wildcard), the
        upstream gets added explicitly. Covers `_build_repo_authors`'s
        fork-side fallthrough -- otherwise the PR sync would skip the
        upstream the user told us about."""
        from _test_constants import TEST_USER_LOGIN
        self._setup(
            monkeypatch,
            allowed=set(),  # nothing in the allow-list
            forks={"loose-fork/repo": "stranger/repo"},
            account_rules=[{"match": "", "account": TEST_USER_LOGIN}],
            tokens={TEST_USER_LOGIN: "tok"},
        )
        result = app_state._build_repo_authors()
        # The upstream got added even though no entry in ALLOWED_REPOS
        # would have produced it directly.
        assert "stranger/repo" in result
        assert result["stranger/repo"] == TEST_USER_LOGIN


# ---- gh_account_for_repo + _default_account_fallback ----


class TestGhAccountFallback:
    """The fallback chain is: rules walk first, `_default_account_fallback`
    picks the first gh CLI token last. With zero tokens loaded, fall
    back to "" so the gh subprocess surfaces its own auth-required
    message rather than crashing on a missing key."""

    def test_returns_empty_when_no_tokens_loaded(self, monkeypatch):
        from adapters import github as gh
        monkeypatch.setattr(gh, "_account_rules", [])
        gh._gh_tokens.clear()
        # No tokens, no rules -> fallback returns "" (defensive).
        assert gh._default_account_fallback() == ""
        # And gh_account_for_repo for any repo also returns "".
        assert gh.gh_account_for_repo("anyorg/anyrepo") == ""

    def test_skips_rule_with_empty_account_string(self, monkeypatch):
        """Defence in depth: a settings rule with `account=""` is a
        misconfiguration -- skip it instead of returning empty as the
        gh login. Otherwise gh would shell out with `--no-default`-ish
        garbage and fail confusingly."""
        from adapters import github as gh
        monkeypatch.setattr(gh, "_account_rules", [
            {"match": "", "account": ""},  # malformed: skipped
            {"match": "", "account": "valid-acct"},  # catches everything
        ])
        gh._gh_tokens.clear()
        gh._gh_tokens.update({"valid-acct": "tok"})
        assert gh.gh_account_for_repo("any/repo") == "valid-acct"


# ---- load_config / save_config ----


class TestLoadSaveConfig:
    def test_mtime_caching(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({"projects": {}}))
        orig_path = app_state.CONFIG_PATH
        orig_cache = app_state._config_cache.copy()
        try:
            app_state.CONFIG_PATH = config_file
            app_state._config_cache["data"] = None
            app_state._config_cache["mtime"] = 0

            # First load reads from disk
            data1 = app_state.load_config()
            assert data1 == {"projects": {}}
            mtime1 = app_state._config_cache["mtime"]

            # Second load returns cached data without re-reading
            data2 = app_state.load_config()
            assert data2 is data1  # same object (from cache)
            assert app_state._config_cache["mtime"] == mtime1
        finally:
            app_state.CONFIG_PATH = orig_path
            app_state._config_cache.update(orig_cache)

    def test_save_updates_cache(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({"projects": {}}))
        orig_path = app_state.CONFIG_PATH
        orig_cache = app_state._config_cache.copy()
        try:
            app_state.CONFIG_PATH = config_file
            app_state._config_cache["data"] = None
            app_state._config_cache["mtime"] = 0

            new_config = {"projects": {"test": {"name": "Test"}}}
            app_state.save_config(new_config)

            assert app_state._config_cache["data"] == new_config
            assert app_state._config_cache["mtime"] > 0

            # Reload should return the cached version
            loaded = app_state.load_config()
            assert loaded == new_config
        finally:
            app_state.CONFIG_PATH = orig_path
            app_state._config_cache.update(orig_cache)

    def test_yaml_roundtrip(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({"projects": {}}))
        orig_path = app_state.CONFIG_PATH
        orig_cache = app_state._config_cache.copy()
        try:
            app_state.CONFIG_PATH = config_file
            app_state._config_cache["data"] = None
            app_state._config_cache["mtime"] = 0

            config = {"projects": {"p1": {"name": "Project One", "repo": "example/repo"}}}
            app_state.save_config(config)

            # Read raw YAML from disk to verify format
            raw = config_file.read_text()
            parsed = yaml.safe_load(raw)
            assert parsed["projects"]["p1"]["name"] == "Project One"
        finally:
            app_state.CONFIG_PATH = orig_path
            app_state._config_cache.update(orig_cache)


# ---- gh_account_for_repo ----


class TestGhAccountForRepo:
    """Account resolver tests with monkeypatched rules so the
    assertions describe the routing logic, not the maintainer's
    actual two-account split."""

    def _setup(self, monkeypatch, *, account_rules, tokens, forks=None):
        from adapters import github as gh
        monkeypatch.setattr(gh, "_account_rules", list(account_rules))
        if forks is not None:
            monkeypatch.setattr(gh, "FORK_TO_UPSTREAM", dict(forks))
        gh._gh_tokens.clear()
        gh._gh_tokens.update(tokens)

    def test_company_org_routes_to_work_account(self, monkeypatch):
        from _test_constants import (
            TEST_COMPANY_ORG, TEST_COMPANY_REPO_RUNTIME,
            TEST_COMPANY_REPO_PLATFORM, TEST_USER_LOGIN_ALT,
        )
        # Account rules `match` is a substring; the org name alone
        # matches every repo under that org.
        self._setup(
            monkeypatch,
            account_rules=[
                {"match": TEST_COMPANY_ORG,
                 "account": TEST_USER_LOGIN_ALT},
            ],
            tokens={TEST_USER_LOGIN_ALT: "tok"},
        )
        assert (
            app_state.gh_account_for_repo(TEST_COMPANY_REPO_RUNTIME)
            == TEST_USER_LOGIN_ALT
        )
        assert (
            app_state.gh_account_for_repo(TEST_COMPANY_REPO_PLATFORM)
            == TEST_USER_LOGIN_ALT
        )

    def test_oss_repo_and_its_fork_route_to_personal_account(
        self, monkeypatch,
    ):
        from _test_constants import (
            TEST_OSS_REPO, TEST_OSS_FORK, TEST_USER_LOGIN,
        )
        # The account-rule matcher does substring matching on the raw
        # repo string; rules don't auto-resolve forks. To cover both
        # the upstream and the fork without listing each pair, the
        # owner's login is a substring of the fork name -- a single
        # rule keyed on the login covers both.
        self._setup(
            monkeypatch,
            account_rules=[
                {"match": TEST_USER_LOGIN, "account": TEST_USER_LOGIN},
                {"match": TEST_OSS_REPO, "account": TEST_USER_LOGIN},
            ],
            tokens={TEST_USER_LOGIN: "tok"},
            forks={TEST_OSS_FORK: TEST_OSS_REPO},
        )
        assert (
            app_state.gh_account_for_repo(TEST_OSS_REPO) == TEST_USER_LOGIN
        )
        assert (
            app_state.gh_account_for_repo(TEST_OSS_FORK) == TEST_USER_LOGIN
        )


# ---- emit_event / on_event ----


class TestEventBus:
    def test_listener_registration_and_dispatch(self, tmp_path):
        """on_event registers a callback, emit_event dispatches to it."""
        received = []
        orig_notif = app_state._NOTIF_DB_PATH

        # Use a temp DB for event persistence
        tmp_db = tmp_path / "events.db"
        app_state._NOTIF_DB_PATH = tmp_db
        with sqlite3.connect(str(tmp_db)) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT,
                title TEXT NOT NULL, message TEXT, type TEXT DEFAULT 'info',
                severity TEXT DEFAULT 'info', url TEXT, ts TEXT NOT NULL,
                read INTEGER DEFAULT 0, session TEXT DEFAULT ''
            )""")

        def handler(event):
            received.append(event)

        # Register
        app_state.on_event("test.ping", handler)

        try:
            app_state.emit_event("test.ping", {
                "title": "hello",
                "message": "world",
            })
            # Listeners are dispatched in threads; wait briefly
            import time
            time.sleep(0.2)
            assert len(received) == 1
            assert received[0]["title"] == "hello"
            assert received[0]["type"] == "test.ping"
        finally:
            # Clean up listener
            app_state._event_listeners.get("test.ping", []).remove(handler)
            app_state._NOTIF_DB_PATH = orig_notif

    def test_wildcard_listener(self, tmp_path):
        """Wildcard listeners (source.*) receive events matching the source prefix."""
        received = []
        orig_notif = app_state._NOTIF_DB_PATH

        tmp_db = tmp_path / "events.db"
        app_state._NOTIF_DB_PATH = tmp_db
        with sqlite3.connect(str(tmp_db)) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT,
                title TEXT NOT NULL, message TEXT, type TEXT DEFAULT 'info',
                severity TEXT DEFAULT 'info', url TEXT, ts TEXT NOT NULL,
                read INTEGER DEFAULT 0, session TEXT DEFAULT ''
            )""")

        def handler(event):
            received.append(event)

        app_state.on_event("myns.*", handler)

        try:
            app_state.emit_event("myns.something", {"title": "wild"})
            import time
            time.sleep(0.2)
            assert len(received) == 1
            assert received[0]["title"] == "wild"
        finally:
            app_state._event_listeners.get("myns.*", []).remove(handler)
            app_state._NOTIF_DB_PATH = orig_notif

    def test_sse_subscribers_receive_events(self, tmp_path):
        """SSE subscriber queues receive emitted events."""
        import queue
        orig_notif = app_state._NOTIF_DB_PATH

        tmp_db = tmp_path / "events.db"
        app_state._NOTIF_DB_PATH = tmp_db
        with sqlite3.connect(str(tmp_db)) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT,
                title TEXT NOT NULL, message TEXT, type TEXT DEFAULT 'info',
                severity TEXT DEFAULT 'info', url TEXT, ts TEXT NOT NULL,
                read INTEGER DEFAULT 0, session TEXT DEFAULT ''
            )""")

        q = queue.Queue()
        app_state._event_subscribers.append(q)

        try:
            app_state.emit_event("sse.test", {"title": "sse event"})
            event = q.get(timeout=1)
            assert event["title"] == "sse event"
        finally:
            app_state._event_subscribers.remove(q)
            app_state._NOTIF_DB_PATH = orig_notif

    def test_event_persisted_to_db(self, tmp_path):
        """Emitted events are written to the events table."""
        orig_notif = app_state._NOTIF_DB_PATH

        tmp_db = tmp_path / "events.db"
        app_state._NOTIF_DB_PATH = tmp_db
        with sqlite3.connect(str(tmp_db)) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT,
                title TEXT NOT NULL, message TEXT, type TEXT DEFAULT 'info',
                severity TEXT DEFAULT 'info', url TEXT, ts TEXT NOT NULL,
                read INTEGER DEFAULT 0, session TEXT DEFAULT ''
            )""")

        try:
            app_state.emit_event("persist.test", {"title": "db check", "source_id": "x1"})
            with sqlite3.connect(str(tmp_db)) as conn:
                row = conn.execute("SELECT title FROM events WHERE source_id = 'x1'").fetchone()
            assert row is not None
            assert row[0] == "db check"
        finally:
            app_state._NOTIF_DB_PATH = orig_notif

    def test_dead_sse_subscriber_removed(self, tmp_path):
        """Dead SSE subscribers (full queues) are removed after emit_event."""
        import queue as _queue
        orig_notif = app_state._NOTIF_DB_PATH

        tmp_db = tmp_path / "events.db"
        app_state._NOTIF_DB_PATH = tmp_db
        with sqlite3.connect(str(tmp_db)) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT,
                title TEXT NOT NULL, message TEXT, type TEXT DEFAULT 'info',
                severity TEXT DEFAULT 'info', url TEXT, ts TEXT NOT NULL,
                read INTEGER DEFAULT 0, session TEXT DEFAULT ''
            )""")

        # Create a full queue (maxsize=1) and fill it
        full_q = _queue.Queue(maxsize=1)
        full_q.put("filler")
        app_state._event_subscribers.append(full_q)

        try:
            app_state.emit_event("sse.overflow", {"title": "overflow"})
            # The dead subscriber should be removed
            assert full_q not in app_state._event_subscribers
        finally:
            if full_q in app_state._event_subscribers:
                app_state._event_subscribers.remove(full_q)
            app_state._NOTIF_DB_PATH = orig_notif

    def test_github_event_dedup_updates_existing(self, tmp_path):
        """Emitting a github event with same source_id updates existing row."""
        orig_notif = app_state._NOTIF_DB_PATH

        tmp_db = tmp_path / "events.db"
        app_state._NOTIF_DB_PATH = tmp_db
        with sqlite3.connect(str(tmp_db)) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT,
                title TEXT NOT NULL, message TEXT, type TEXT DEFAULT 'info',
                severity TEXT DEFAULT 'info', url TEXT, ts TEXT NOT NULL,
                read INTEGER DEFAULT 0, session TEXT DEFAULT ''
            )""")

        try:
            app_state.emit_event("github.comment", {
                "title": "first version",
                "source_id": "gh-dedup-1",
                "message": "msg1",
            })
            app_state.emit_event("github.comment", {
                "title": "updated version",
                "source_id": "gh-dedup-1",
                "message": "msg2",
            })
            with sqlite3.connect(str(tmp_db)) as conn:
                rows = conn.execute(
                    "SELECT title, message FROM events WHERE source_id = 'gh-dedup-1'"
                ).fetchall()
            # Only one row (dedup), with updated title
            assert len(rows) == 1
            assert rows[0][0] == "updated version"
            assert rows[0][1] == "msg2"
        finally:
            app_state._NOTIF_DB_PATH = orig_notif


class TestEventBusSafety:
    """Regression coverage for the github-event leak path that
    previously let a stale `_event_listeners["github.*"]` registration
    fan out to a listener daemon thread holding the production
    `_db` handle. See `services.github_poller.init` (now properly
    idempotent) and `app_state.await_dispatch_threads` (now drained
    by per-test fixtures).
    """

    def test_github_poller_init_is_listener_idempotent(self):
        """Repeat `init()` calls must not double-register the github
        listeners. The previous behaviour appended duplicates on
        every call -- a single test that exercised init() leaked the
        listeners for the rest of the pytest session, and any later
        emit of `github.*` then fanned out to whatever `_db` was
        currently bound (which defaulted to the production handle).
        """
        from services import github_poller

        # Reset the guard so this test exercises a clean init path
        # regardless of suite ordering.
        github_poller._listeners_registered = False
        before_count = len(app_state._event_listeners.get("github.*", []))

        github_poller.init()
        after_first = len(app_state._event_listeners.get("github.*", []))
        github_poller.init()
        github_poller.init()
        after_third = len(app_state._event_listeners.get("github.*", []))

        # First call adds exactly two listeners (notification +
        # status update); subsequent calls must be no-ops.
        assert after_first - before_count == 2
        assert after_third == after_first

    def test_emit_event_does_not_relay_to_prod_inside_pytest(self,
                                                                monkeypatch):
        """Even when `_event_relay_url` IS set (eva-cli sets it
        intentionally), `emit_event` must not HTTP-POST events to
        the running server while `PYTEST_CURRENT_TEST` is in env.
        Otherwise test events would fan out to live web-UI SSE
        subscribers, polluting the user's real dashboard.
        """
        relayed = []

        def fake_urlopen(req, timeout=2):
            # Capture the call; if the gate breaks, this test fails
            # because relayed accumulates entries.
            relayed.append(getattr(req, "full_url", str(req)))

            class _R:
                def read(self_inner):
                    return b""
            return _R()

        # Set a URL so the only thing keeping the relay quiet is
        # the PYTEST_CURRENT_TEST env gate.
        monkeypatch.setattr(app_state, "_event_relay_url",
                            "http://localhost:8021/api/internal/emit-relay")
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        app_state.emit_event(
            "safety.relay_test",
            {"title": "should not relay", "message": ""},
            persist=False,
        )

        assert relayed == [], (
            "emit_event tried to relay during pytest -- the "
            "PYTEST_CURRENT_TEST gate in app_state.emit_event is "
            "broken. Relayed: " + repr(relayed)
        )

    def test_await_dispatch_threads_drains_in_flight_handlers(self):
        """`emit_event` spawns daemon threads for listener dispatch.
        Tests must be able to wait for them to finish before
        fixture teardown swaps `_db` back -- otherwise a mid-flight
        listener would observe the restored production `_db`. Verify
        the drain helper actually blocks until handlers complete.
        """
        import threading
        import time

        # Use a slow handler so we can observe the drain wait. The
        # handler signals completion via an Event the test can read.
        finished = threading.Event()
        observed_during_drain = {"flag": False}

        def slow_handler(_event):
            time.sleep(0.2)
            observed_during_drain["flag"] = True
            finished.set()

        app_state.on_event("safety.drain_test", slow_handler)
        try:
            app_state.emit_event(
                "safety.drain_test",
                {"title": "drain", "message": ""},
                persist=False,
            )
            # Drain must block until slow_handler returns.
            leftover = app_state.await_dispatch_threads(timeout=5.0)
            assert leftover == 0
            assert observed_during_drain["flag"] is True
            assert finished.is_set()
        finally:
            # Autouse `_isolate_event_listeners` would clear this
            # anyway, but explicit cleanup keeps the test
            # self-documenting.
            try:
                app_state._event_listeners["safety.drain_test"].remove(slow_handler)
            except (KeyError, ValueError):
                pass


# ---- gh_run ----


class TestGhRun:
    """Tests for the gh_run subprocess wrapper."""

    @patch("app_state.subprocess.run")
    def test_gh_run_success(self, mock_run):
        mock_run.return_value = types.SimpleNamespace(
            returncode=0, stdout='{"ok":true}', stderr=""
        )
        result = app_state.gh_run(["gh", "api", "user"], repo="example/repo")
        assert result.returncode == 0
        assert result.stdout == '{"ok":true}'
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["capture_output"] is True
        assert call_kwargs[1]["text"] is True
        assert call_kwargs[1]["timeout"] == 20

    @patch("app_state.subprocess.run")
    def test_gh_run_failure(self, mock_run):
        mock_run.return_value = types.SimpleNamespace(
            returncode=1, stdout="", stderr="not found"
        )
        result = app_state.gh_run(["gh", "pr", "view", "999"])
        assert result.returncode == 1
        assert result.stderr == "not found"

    @patch("app_state.subprocess.run")
    def test_gh_run_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=20)
        with pytest.raises(subprocess.TimeoutExpired):
            app_state.gh_run(["gh", "api", "slow"], timeout=20)

    @patch("app_state.subprocess.run")
    def test_gh_run_sets_gh_token_for_repo(self, mock_run):
        """When repo is provided and token exists, GH_TOKEN is set in env."""
        mock_run.return_value = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        # Temporarily inject a fake token
        orig_tokens = app_state._gh_tokens.copy()
        app_state._gh_tokens["test-author"] = "fake-token-123"
        try:
            app_state.gh_run(["gh", "api", "user"], repo="example/repo")
            call_env = mock_run.call_args[1]["env"]
            assert call_env["GH_TOKEN"] == "fake-token-123"
        finally:
            app_state._gh_tokens.clear()
            app_state._gh_tokens.update(orig_tokens)

    @patch("app_state.subprocess.run")
    def test_gh_run_no_repo_no_token(self, mock_run):
        """When no repo is provided, GH_TOKEN is not explicitly set."""
        mock_run.return_value = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        app_state.gh_run(["gh", "api", "user"])
        call_env = mock_run.call_args[1]["env"]
        # Should not have added GH_TOKEN (unless already in os.environ)
        import os
        if "GH_TOKEN" not in os.environ:
            assert "GH_TOKEN" not in call_env or call_env.get("GH_TOKEN") == ""

    @patch("app_state.subprocess.run")
    def test_gh_run_custom_timeout(self, mock_run):
        mock_run.return_value = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        app_state.gh_run(["gh", "api", "slow"], timeout=60)
        assert mock_run.call_args[1]["timeout"] == 60


# ---- gh_run_async ----


class TestGhRunAsync:
    """Tests for the async gh_run wrapper."""

    def test_gh_run_async_delegates(self):
        """gh_run_async delegates to gh_run via thread pool executor."""
        import asyncio
        fake_result = types.SimpleNamespace(returncode=0, stdout="async ok", stderr="")
        with patch.object(app_state, "gh_run", return_value=fake_result) as mock_gh:
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    app_state.gh_run_async(["gh", "api", "test"], repo="example/repo", timeout=15)
                )
            finally:
                loop.close()
        assert result.returncode == 0
        assert result.stdout == "async ok"
        mock_gh.assert_called_once_with(["gh", "api", "test"], repo="example/repo", timeout=15)


# ---- gh_run_or_raise ----


class TestGhRunOrRaise:
    """Tests for gh_run_or_raise, the HTTP wrapper around gh_run."""

    def test_returns_result_on_success(self):
        """gh_run_or_raise returns the CompletedProcess when gh succeeds."""
        ok = types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with patch.object(app_state, "gh_run", return_value=ok):
            result = app_state.gh_run_or_raise(["gh", "--version"])
        assert result.stdout == "ok"

    def test_raises_500_on_nonzero(self):
        """Non-zero exit triggers HTTPException(500) with stderr as detail."""
        from fastapi import HTTPException
        fail = types.SimpleNamespace(returncode=1, stdout="", stderr="auth required")
        with patch.object(app_state, "gh_run", return_value=fail):
            with pytest.raises(HTTPException) as exc:
                app_state.gh_run_or_raise(["gh", "pr", "view", "1"])
        assert exc.value.status_code == 500
        assert "auth required" in exc.value.detail

    def test_stderr_limit_truncates(self):
        """stderr_limit clips the detail to the requested length."""
        from fastapi import HTTPException
        long_err = "x" * 500
        fail = types.SimpleNamespace(returncode=1, stdout="", stderr=long_err)
        with patch.object(app_state, "gh_run", return_value=fail):
            with pytest.raises(HTTPException) as exc:
                app_state.gh_run_or_raise(["gh", "pr", "diff", "1"], stderr_limit=50)
        assert len(exc.value.detail) == 50

    def test_passes_repo_and_timeout_through(self):
        """Repo + timeout kwargs flow into the underlying gh_run call."""
        ok = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.object(app_state, "gh_run", return_value=ok) as mock_run:
            app_state.gh_run_or_raise(["gh", "x"], repo="example/repo", timeout=45)
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["repo"] == "example/repo"
        assert kwargs["timeout"] == 45


# ---- projects registry / flush -----------------------------


class TestProjectRegistry:
    """Tests for the `register_project` / `flush_registered_to_db`
    framework. Extensions call `register_project` from their seed
    files; the framework flushes the declared rows into the DB once
    at boot via `INSERT OR IGNORE` semantics."""

    def _reset(self):
        from common import projects
        projects.reset_registry_for_tests()

    def test_register_and_flush_creates_row(self, tmp_path):
        from common import projects
        from eva_db import EvaDB
        self._reset()
        test_db = EvaDB(str(tmp_path / "test.db"))
        orig_db = app_state._db
        app_state._db = test_db
        try:
            projects.register_project(
                "new-proj", name="New Project",
                description="Brand new", repo="example/repo",
                jira="example", has_tickets=True,
            )
            written = projects.flush_registered_to_db()
            assert written == 1
            assert test_db.project_exists("new-proj")
        finally:
            app_state._db = orig_db
            test_db.close()
            self._reset()

    def test_flush_skips_existing_row(self, tmp_path):
        """An existing DB row keeps its values; the declaration is a
        seed, not an overwrite. Mirrors the contract that user edits
        via the UI survive every restart."""
        from common import projects
        from eva_db import EvaDB
        self._reset()
        test_db = EvaDB(str(tmp_path / "test.db"))
        test_db.create_project("existing-proj", name="Original Name")
        orig_db = app_state._db
        app_state._db = test_db
        try:
            projects.register_project(
                "existing-proj", name="Overwrite Attempt")
            written = projects.flush_registered_to_db()
            assert written == 0
            proj = test_db.get_project("existing-proj")
            assert proj["name"] == "Original Name"
        finally:
            app_state._db = orig_db
            test_db.close()
            self._reset()

    def test_register_is_idempotent_on_id(self):
        from common import projects
        self._reset()
        try:
            projects.register_project("p", name="One")
            projects.register_project("p", name="Two")  # noop
            entries = projects.all_registered_projects()
            assert len(entries) == 1
            assert entries[0]["name"] == "One"
        finally:
            self._reset()

    def test_empty_id_is_ignored(self):
        from common import projects
        self._reset()
        try:
            projects.register_project("", name="ghost")
            projects.register_project("   ", name="ghost2")
            assert projects.all_registered_projects() == []
        finally:
            self._reset()


# ---- load_tasks / save_task (direct app_state calls with mock DB) ----
# (TestTmuxHelpers moved to tests/test_tmux_helpers.py 2026-04-25 -- the
# tmux primitives belong to adapters.tmux, app_state no longer re-exports
# them as `tmux_*`. Routes / core / app_state.load_tasks all import them
# directly from adapters.tmux now.)


class TestLoadSaveTasks:
    """Tests for load_tasks and save_task on app_state directly."""

    def test_load_tasks_basic(self, tmp_path):
        """load_tasks returns tasks dict from DB."""
        from eva_db import EvaDB
        test_db = EvaDB(str(tmp_path / "test.db"))
        test_db.create_project("proj", name="Test")
        test_db.create_task(project="proj", task_id="t1", description="Task one", type="feature", status="in_progress")

        orig_db = app_state._db
        app_state._db = test_db
        try:
            with patch("app_state._tmux_session_exists", return_value=False):
                tasks = app_state.load_tasks("proj")
            assert "t1" in tasks
            assert tasks["t1"]["description"] == "Task one"
            assert tasks["t1"]["status"] == "in_progress"
        finally:
            app_state._db = orig_db
            test_db.close()

    def test_load_tasks_with_session(self, tmp_path):
        """load_tasks merges session info into tasks."""
        from eva_db import EvaDB
        test_db = EvaDB(str(tmp_path / "test.db"))
        test_db.create_project("proj", name="Test")
        test_db.create_task(project="proj", task_id="t1", description="Task one", type="feature", status="in_progress")
        test_db.create_session(task_id="t1", project="proj", tmux_name="t1")

        orig_db = app_state._db
        app_state._db = test_db
        try:
            with patch("app_state._tmux_session_exists", return_value=True):
                tasks = app_state.load_tasks("proj")
            assert "session" in tasks["t1"]
            assert tasks["t1"]["session"]["name"] == "t1"
            assert tasks["t1"]["session"]["running"] is True
        finally:
            app_state._db = orig_db
            test_db.close()

    def test_load_tasks_session_status_prefers_in_memory_live_state(self, tmp_path):
        """Regression: TaskCard used to flicker stale `idle` while
        the agent was actually `thinking` because load_tasks read the DB
        `common.sessions.status` column. The DB write happens AFTER the SSE
        emit -- a fast frontend refetch would arrive before DB write
        completed and see the previous status. Fix: overlay the
        in-memory `_session_states` dict (set synchronously by
        /api/hook BEFORE the SSE emit) on top of the DB column."""
        from eva_db import EvaDB
        from routes import sessions as sess_routes
        test_db = EvaDB(str(tmp_path / "test.db"))
        test_db.create_project("p", name="P")
        test_db.create_task(project="p", task_id="t", description="d",
                            type="feature", status="in_progress")
        test_db.create_session(task_id="t", project="p", tmux_name="t")
        test_db.update_session("t", status="idle")

        orig_db = app_state._db
        app_state._db = test_db
        sess_routes._session_states["t"] = {
            "state": "thinking", "detail": "", "ts": "2026-04-26T00:00:00",
        }
        try:
            with patch("app_state._tmux_session_exists", return_value=True):
                tasks = app_state.load_tasks("p")
            # In-memory `thinking` wins over DB-cached `idle`.
            assert tasks["t"]["session"]["status"] == "thinking"
        finally:
            app_state._db = orig_db
            sess_routes._session_states.pop("t", None)
            test_db.close()

    def test_load_tasks_session_status_empty_when_no_hook(self, tmp_path):
        """If the unified session-state cache has no entry yet (e.g.
        server just restarted before any agent hook fired), the task's
        session status is "". The DB `common.sessions.status` column was the
        old fallback; the redesign dropped it because the column was
        the main drift source.
        """
        from eva_db import EvaDB
        test_db = EvaDB(str(tmp_path / "test.db"))
        test_db.create_project("p", name="P")
        test_db.create_task(project="p", task_id="t", description="d",
                            type="feature", status="in_progress")
        test_db.create_session(task_id="t", project="p", tmux_name="t")
        # The DB column would still take 'thinking' here, but it's
        # ignored by the new load_tasks path.
        test_db.update_session("t", status="thinking")

        orig_db = app_state._db
        app_state._db = test_db
        try:
            with patch("app_state._tmux_session_exists", return_value=True):
                tasks = app_state.load_tasks("p")
            # No cache entry -> empty status, NOT the stale DB column.
            assert tasks["t"]["session"]["status"] == ""
        finally:
            app_state._db = orig_db
            test_db.close()

    def test_save_task_creates_new(self, tmp_path):
        """save_task creates a new task in the DB."""
        from eva_db import EvaDB
        test_db = EvaDB(str(tmp_path / "test.db"))
        test_db.create_project("proj", name="Test")

        orig_db = app_state._db
        app_state._db = test_db
        try:
            app_state.save_task("proj", "new-t", {
                "description": "New task",
                "type": "bug",
                "status": "not_started",
            })
            task = test_db.get_task("proj", "new-t")
            assert task is not None
            assert task["description"] == "New task"
            assert task["type"] == "bug"
        finally:
            app_state._db = orig_db
            test_db.close()

    def test_save_task_updates_existing(self, tmp_path):
        """save_task updates existing task fields."""
        from eva_db import EvaDB
        test_db = EvaDB(str(tmp_path / "test.db"))
        test_db.create_project("proj", name="Test")
        test_db.create_task(project="proj", task_id="t1", description="Original", type="feature", status="not_started")

        orig_db = app_state._db
        app_state._db = test_db
        try:
            app_state.save_task("proj", "t1", {
                "description": "Updated",
                "status": "in_progress",
                "notes": "some notes",
            })
            task = test_db.get_task("proj", "t1")
            assert task["description"] == "Updated"
            assert task["status"] == "in_progress"
            assert task["notes"] == "some notes"
        finally:
            app_state._db = orig_db
            test_db.close()

    def test_save_task_with_ticket_dict(self, tmp_path):
        """save_task handles ticket as nested dict."""
        from eva_db import EvaDB
        test_db = EvaDB(str(tmp_path / "test.db"))
        test_db.create_project("proj", name="Test")
        test_db.create_task(project="proj", task_id="t1", description="X", type="feature", status="not_started")

        orig_db = app_state._db
        app_state._db = test_db
        try:
            app_state.save_task("proj", "t1", {
                "ticket": {"id": "EX-100", "url": "https://jira/EX-100"},
            })
            task = test_db.get_task("proj", "t1")
            assert task["ticket_id"] == "EX-100"
            assert task["ticket_url"] == "https://jira/EX-100"
        finally:
            app_state._db = orig_db
            test_db.close()

    def test_save_task_with_dependencies(self, tmp_path):
        """save_task sets dependencies for new tasks."""
        from eva_db import EvaDB
        test_db = EvaDB(str(tmp_path / "test.db"))
        test_db.create_project("proj", name="Test")
        test_db.create_task(project="proj", task_id="t1", description="X", type="feature", status="done")

        orig_db = app_state._db
        app_state._db = test_db
        try:
            app_state.save_task("proj", "t2", {
                "description": "Depends on t1",
                "type": "feature",
                "status": "not_started",
                "dependencies": ["t1"],
            })
            task = test_db.get_task("proj", "t2")
            assert task is not None
            assert "t1" in task.get("dependencies", [])
        finally:
            app_state._db = orig_db
            test_db.close()


# ---- _migrate_old_dbs ----


class TestLoadConfigEdgeCases:
    """`load_config` reads config.yaml + caches; project seeding is
    decoupled (extensions declare projects via
    `projects.register_project` and the framework flushes at
    boot, NOT via load_config). These tests cover the legacy file
    handling: missing file, empty file, cache reuse."""

    def test_load_config_returns_empty_when_config_yaml_missing(self, tmp_path):
        """OSS-readiness regression: a fresh checkout with no
        config.yaml on disk must NOT raise FileNotFoundError. The
        function is supposed to return an empty dict so the rest of
        boot proceeds (settings DB has empty defaults; projects can
        be created via the UI). Before this fix, every call to
        `load_config()` from server.py / app_state.py crashed boot
        for any user who hadn't first authored a config.yaml."""
        missing = tmp_path / "no-such-config.yaml"
        assert not missing.exists()

        orig_path = app_state.CONFIG_PATH
        orig_cache = app_state._config_cache.copy()
        try:
            app_state.CONFIG_PATH = missing
            app_state._config_cache["data"] = None
            app_state._config_cache["mtime"] = 0

            data = app_state.load_config()
            assert data == {}
            # Cached on the empty-path branch so a hot loop doesn't
            # keep re-statting the missing file.
            data2 = app_state.load_config()
            assert data2 == {}
        finally:
            app_state.CONFIG_PATH = orig_path
            app_state._config_cache.update(orig_cache)

    def test_load_config_handles_empty_yaml(self, tmp_path):
        """An empty config.yaml file (zero bytes / just whitespace)
        loads as `{}` instead of `None`. yaml.safe_load returns None
        for empty files; without the `or {}` guard, downstream code
        would TypeError on `data.get('projects')`."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")

        orig_path = app_state.CONFIG_PATH
        orig_cache = app_state._config_cache.copy()
        try:
            app_state.CONFIG_PATH = config_file
            app_state._config_cache["data"] = None
            app_state._config_cache["mtime"] = 0

            data = app_state.load_config()
            assert data == {}
        finally:
            app_state.CONFIG_PATH = orig_path
            app_state._config_cache.update(orig_cache)
