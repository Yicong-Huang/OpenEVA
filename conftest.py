"""Shared fixtures for Eva test suite."""

import os
import sys
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

# Must be set BEFORE `import server` anywhere in the suite: the FastAPI
# startup hook checks this env var to decide whether to spin up the
# AsyncIOScheduler. Leaving it on would have TestClient's lifespan fire
# real gh/Slack polls.
os.environ.setdefault("EVA_DISABLE_SCHEDULER", "1")

# This conftest lives at the REPO ROOT so its fixtures (and the
# autouse safety guards: event-DB isolation, plugin-registry
# snapshot/restore, prod-relay block, etc.) cover BOTH the OSS
# suite (`core/test/`) AND every extension's test tree (each
# `<extension>/test/`). Pytest auto-loads every `conftest.py`
# between rootdir and a test file, so a single root-level conftest
# is the cleanest way to share fixtures across all the parallel
# test trees without re-exporting via `pytest_plugins`.
#
# sys.path layout:
#   `core/src/`           -- top-level OSS modules + the `common`
#                            namespace package + `routes`, `services`,
#                            `adapters`, `plugins`, `channels`. Imports
#                            like `import app_state`, `import server`,
#                            `from common import tasks` resolve here.
#   `<extension>/src/`    -- each discovered extension namespace
#                            (any sibling-of-`core/` folder carrying
#                            an `extension.conf` marker). Added so
#                            tests can import extension top-level
#                            modules directly.
# Umbrella folders themselves are kept OFF sys.path so they don't
# get mistaken for namespace packages.
_REPO_ROOT = Path(__file__).resolve().parent

# `core/src/` -- top-level OSS modules + the `common` namespace + `routes`,
#                `services`, `adapters`, `plugins`, `channels`. Imports
#                like `import app_state`, `from common import tasks`,
#                `from plugins import register` resolve here.
sys.path.insert(0, str(_REPO_ROOT / "core" / "src"))

# Discover every extension namespace (any sibling-of-core folder
# carrying an `extension.conf` marker) and put each one's `src/` on
# sys.path so tests can import extension top-level modules directly.
# The plugin / agent frameworks have their own file-based loaders
# that don't depend on sys.path, but tests doing `patch("<plugin>.X")`
# do.
from common.extensions import discover as _discover_extensions  # noqa: E402
_EXTENSIONS = list(_discover_extensions(_REPO_ROOT))
for _ext in _EXTENSIONS:
    if _ext.src.is_dir():
        sys.path.insert(0, str(_ext.src))

# Test-shared helpers (`_test_constants.py`, etc.) live alongside
# the OSS test files in `core/test/`. Adding the test dir to
# sys.path lets test files reference helpers as bare modules
# (`from _test_constants import X`) without re-introducing the old
# `tests` Python package, which would collide with sibling
# `<extension>/test/` folders.
sys.path.insert(0, str(_REPO_ROOT / "core" / "test"))


def _bootstrap_plugin_registry() -> None:
    """Pre-run plugin discovery once at conftest import so test files
    can `import <plugin>` directly.

    `common.plugins.discover_dir` registers each loaded plugin module
    under both an internal name AND a friendly alias matching the
    folder name. Running it here means tests don't have to juggle
    `spec_from_file_location` to reach the plugin code -- a regular
    `import <plugin>` works after pytest collection.

    We use a try/except so a broken plugin (or a missing extension
    dir on an OSS-only checkout) doesn't take the whole test suite
    down -- the framework's discovery already logs each failure.
    """
    try:
        from common import plugins as _p
        # Mirror server.py's discovery order: native (OSS) plugins
        # first, then each discovered extension. Tests can
        # `import <plugin>` after this runs; native plugins
        # (e.g. `pr`) get their `plugin.conf` seeded into the
        # settings DB even if they have no test surface.
        _p.discover_dir(_REPO_ROOT / "core" / "src" / "plugins")
        for ext in _EXTENSIONS:
            if ext.src.is_dir():
                _p.discover_dir(ext.src)
    except Exception as e:
        print(f"[conftest] plugin pre-discovery failed: {e}", flush=True)
    try:
        from common import agent as _agent
        _agent_dirs = [_REPO_ROOT / "core" / "src" / "agents"]
        for ext in _EXTENSIONS:
            if ext.agents_dir.is_dir():
                _agent_dirs.append(ext.agents_dir)
        _agent.discover_agents(*_agent_dirs)
    except Exception as e:
        print(f"[conftest] agent pre-discovery failed: {e}", flush=True)
    try:
        from common import cert as _cert
        _cert_dirs = [_REPO_ROOT / "core" / "src" / "certs"]
        for ext in _EXTENSIONS:
            _ext_certs = ext.root / "src" / "certs"
            if _ext_certs.is_dir():
                _cert_dirs.append(_ext_certs)
        _cert.discover_certs(*_cert_dirs)
    except Exception as e:
        print(f"[conftest] cert pre-discovery failed: {e}", flush=True)
    try:
        from common.extensions import run_seed_files as _run_seed_files
        _run_seed_files(_EXTENSIONS)
    except Exception as e:
        print(f"[conftest] extension seed run failed: {e}", flush=True)
    # Flush extension-declared projects into whatever DB is active.
    # The DB swap to a tmp path happens AFTER conftest import (via the
    # _redirect_default_db_to_tmp helper just below + patched_server
    # for tests that need data), so this flush hits the production DB
    # the first time it runs and a no-op tmp DB for every test
    # afterwards. Either way `flush_registered_to_db` is idempotent.
    try:
        from common.projects import flush_registered_to_db
        flush_registered_to_db()
    except Exception as e:
        print(f"[conftest] project flush failed: {e}", flush=True)


_bootstrap_plugin_registry()


def _redirect_default_db_to_tmp() -> None:
    """Repoint `app_state._db` from the production `data/eva.db` to a
    fresh, empty tmp EvaDB at conftest import time.

    Why: `app_state._db = EvaDB("data/eva.db")` runs at import time
    and points to the LIVE production DB. Tests that don't use
    `patched_server` (and daemon threads from `emit_event` that
    outlive a per-test fixture) inherit that handle. Historically
    that path produced silent prod writes -- a github.* listener
    daemon mid-flight when `patched_server` restored the original
    `_db` would happily call `update_pr_by_number(...)` on the
    production handle.

    Repointing here gives the whole pytest session a benign default:
    schema-correct, empty, and disposable. `patched_server` still
    swaps in its own per-test EvaDB for tests that need data.
    """
    import tempfile
    import app_state
    from eva_db import EvaDB

    # Close the real production handle so we don't leak the FD for
    # the duration of the pytest run.
    try:
        app_state._db.close()
    except Exception:
        pass

    # Tmp dir lives for the whole session -- pytest's tmp_path is
    # function-scoped, but this default DB needs to outlive any
    # individual test (it backstops daemons from earlier tests too).
    _tmp_dir = tempfile.mkdtemp(prefix="eva-test-default-db-")
    app_state._db = EvaDB(str(Path(_tmp_dir) / "eva.db"))


_redirect_default_db_to_tmp()


@pytest.fixture(autouse=True)
def _seed_github_test_repos():
    """Populate `adapters.github.ALLOWED_REPOS` and `FORK_TO_UPSTREAM`
    with test-only fixtures.

    Production defaults are empty (settings-driven for open-source
    installs). Tests that exercise allow-list and fork-resolution
    logic need realistic mappings; this fixture installs them at
    test-session level so individual tests don't have to reach into
    the adapter module.
    """
    from adapters import github as _gh
    orig_allowed = set(_gh.ALLOWED_REPOS)
    orig_orgs = set(_gh.ALLOWED_ORGS)
    orig_ftu = dict(_gh.FORK_TO_UPSTREAM)
    orig_tokens = dict(_gh._gh_tokens)

    # Clear-then-set so the maintainer's config.yaml allow-list doesn't
    # leak into the test session (would inflate `_review_account_hints`
    # and similar derived lists with extra org/repo entries).
    _gh.ALLOWED_REPOS.clear()
    _gh.ALLOWED_REPOS.update({"myorg/*", "example/repo"})
    _gh.ALLOWED_ORGS.clear()
    _gh.ALLOWED_ORGS.update({"myorg"})
    _gh.FORK_TO_UPSTREAM.clear()
    _gh.FORK_TO_UPSTREAM.update({
        "test-author/repo": "example/repo",
        "test-author_data/monorepo": "myorg/monorepo",
        "test-author_data/svc": "myorg/svc",
    })
    # `common.prs._FORK_CI_REPOS` is computed at module-load from the
    # then-current FORK_TO_UPSTREAM. If common.prs was imported BEFORE
    # this autouse fixture ran (e.g. via an earlier test in the same
    # session), its cached dict won't pick up the test entries we just
    # added. Refresh it explicitly so the fork-CI branch fires for
    # `example/repo` in tests regardless of import order.
    try:
        from common import prs as _prs
        _prs._FORK_CI_REPOS.clear()
        _prs._FORK_CI_REPOS.update({
            v: k for k, v in _gh.FORK_TO_UPSTREAM.items() if v in _gh.ALLOWED_REPOS
        })
        _prs._DEFAULT_FORK_REPO = next(iter(_prs._FORK_CI_REPOS.values()), None)
    except ImportError:
        pass
    # Replace whatever's loaded from the developer's local gh CLI
    # config with deterministic test logins. Stops
    # `_default_account_fallback` from leaking the maintainer's real
    # account into assertions.
    _gh._gh_tokens.clear()
    _gh._gh_tokens.update({
        "test-author": "fake-token-oss",
        "test-author_data": "fake-token-internal",
    })
    # Default account-rules so the internal-org wildcard routes to
    # the secondary account, matching the typical two-account split
    # (personal vs work). Individual tests can monkeypatch
    # _account_rules to override.
    orig_rules = list(_gh._account_rules)
    _gh._account_rules.clear()
    _gh._account_rules.extend([
        {"match": "myorg", "account": "test-author_data"},
        {"match": "", "account": "test-author"},
    ])

    yield

    _gh.ALLOWED_REPOS.clear()
    _gh.ALLOWED_REPOS.update(orig_allowed)
    _gh.ALLOWED_ORGS.clear()
    _gh.ALLOWED_ORGS.update(orig_orgs)
    _gh.FORK_TO_UPSTREAM.clear()
    _gh.FORK_TO_UPSTREAM.update(orig_ftu)
    _gh._gh_tokens.clear()
    _gh._gh_tokens.update(orig_tokens)
    _gh._account_rules.clear()
    _gh._account_rules.extend(orig_rules)


@pytest.fixture(autouse=True)
def _seed_pr_sync_test_mappings():
    """Populate `pr_sync.TICKET_URL_PREFIXES` with test-only fixtures.

    Production default is empty (settings-driven for open-source
    installs). The ticket-URL builder consults settings first, then
    falls back to this in-process dict; seeding it here gives unit
    tests a deterministic mapping without touching the (per-test-
    isolated) settings DB.
    """
    import pr_sync as _ps
    orig_t = dict(_ps.TICKET_URL_PREFIXES)
    _ps.TICKET_URL_PREFIXES.update({
        "EX-": "https://issues.example.org/jira/browse/",
        "ALT-": "https://example.atlassian.net/browse/",
        "ZOO-": "https://example.atlassian.net/browse/",
    })
    yield
    _ps.TICKET_URL_PREFIXES.clear()
    _ps.TICKET_URL_PREFIXES.update(orig_t)


@pytest.fixture(autouse=True)
def _no_magicmock_leak_in_repo_root(request):
    """Fail any test that leaks a `<MagicMock name='...'>` file into
    the repo root.

    Failure mode: a test broadly mocks a module (e.g. `@patch(
    "plugins.boba.app_state")`) and production code then does
    `sqlite3.connect(str(<the mock>._NOTIF_DB_PATH))`. SQLite happily
    creates a file named `<MagicMock name='...' id='...'>` in CWD.
    Over time these accumulate (we found 966 in the repo root before
    this guard was added). Now the test that introduced the leak
    fails immediately with the exact filename printed.
    """
    repo_root = Path(__file__).resolve().parent.parent
    yield
    leaked = sorted(repo_root.glob("<MagicMock*"))
    if leaked:
        names = "\n  ".join(p.name for p in leaked[:5])
        for p in leaked:
            try:
                p.unlink()
            except OSError:
                pass
        pytest.fail(
            f"Test {request.node.nodeid!r} leaked "
            f"{len(leaked)} MagicMock-named files into the repo "
            f"root.\nFirst 5 (cleaned up):\n  {names}\n"
            f"Cause: a broad `@patch('module.app_state')` (or similar) "
            f"replaced the module ref with a MagicMock, and "
            f"production code then `sqlite3.connect(str(<mock>.PATH))` "
            f"created the file. Narrow the patch to specific attrs "
            f"OR patch the function that touches the path."
        )


@pytest.fixture(autouse=True)
def _isolate_cert_registry():
    """Snapshot + restore `common.cert._registered` around each test.

    Certs have their own registry, separate from plugins / agents.
    A test that triggers cert module import (e.g. `from
    system import CERT_PROVIDERS`) would otherwise leave the
    registration behind for the rest of the suite, just like the
    plugin / agent leak shapes."""
    try:
        from common import cert as _c
    except ImportError:
        yield
        return
    snapshot = list(_c._registered)
    snapshot_keys = set(_c._seen_keys)
    snapshot_last = dict(_c._last_cert_status)
    yield
    _c._registered.clear()
    _c._registered.extend(snapshot)
    _c._seen_keys.clear()
    _c._seen_keys.update(snapshot_keys)
    _c._last_cert_status.clear()
    _c._last_cert_status.update(snapshot_last)


@pytest.fixture(autouse=True)
def _isolate_agent_registry():
    """Snapshot + restore `common.agent._registered` around each test.

    Agents have their own registry separate from the plugin one; same
    leak shape applies (a test that imports an agent module would
    otherwise leave the registration behind for the rest of the
    session)."""
    try:
        from common import agent as _a
    except ImportError:
        yield
        return
    snapshot = list(_a._registered)
    snapshot_ids = set(_a._seen_ids)
    yield
    _a._registered.clear()
    _a._registered.extend(snapshot)
    _a._seen_ids.clear()
    _a._seen_ids.update(snapshot_ids)


@pytest.fixture(autouse=True)
def _isolate_plugin_registry():
    """Snapshot + restore `common.plugins._registered` around each test.

    The plugin registry is module-global; without this fixture, any
    test that imports a plugin module leaves the registration behind
    for the rest of the suite. The
    next test's `_initialize_plugins()` call would then double-wire
    routes / scheduler jobs, exactly the leak shape we just fixed
    for `github_poller.init()`.
    """
    from common import plugins as _plug
    snapshot = list(_plug._registered)
    snapshot_ids = set(_plug._seen_ids)
    yield
    _plug._registered.clear()
    _plug._registered.extend(snapshot)
    _plug._seen_ids.clear()
    _plug._seen_ids.update(snapshot_ids)


@pytest.fixture(autouse=True)
def _isolate_session_state_cache():
    """Clear the in-memory session-state cache before AND after each
    test. The cache is module-global (`common.session_state._states`),
    so without this a test that seeds a row leaks into the next
    test's `get_session_status` / `live_state` reads.
    """
    from common import session_state as _ssn
    with _ssn._lock:
        _ssn._states.clear()
    yield
    with _ssn._lock:
        _ssn._states.clear()


@pytest.fixture(autouse=True)
def _block_event_relay_to_prod():
    """Belt-and-braces guard against tests pushing events into the
    live production event bus.

    `app_state._event_relay_url`, when set, makes `emit_event` HTTP
    POST every event to the running server's `/api/internal/emit-relay`
    endpoint -- fanning the event to all live web-UI SSE subscribers.
    eva-cli sets it intentionally; tests should never. `emit_event`
    already short-circuits the relay when `PYTEST_CURRENT_TEST` is in
    env (the primary defence), but this fixture additionally:
      - asserts the URL is unset before the test runs (catches
        anything the test session leaked from a prior step), and
      - force-clears it after each test so a malformed test that
        sets the URL can't poison subsequent tests.
    """
    import app_state
    assert app_state._event_relay_url is None, (
        "test would relay to prod event bus -- _event_relay_url was "
        f"already set: {app_state._event_relay_url!r}. Inspect the "
        "previous test for an unbalanced setattr."
    )
    yield
    app_state._event_relay_url = None


@pytest.fixture(autouse=True)
def _isolate_event_listeners():
    """Snapshot + restore `app_state._event_listeners` around each test.

    `_event_listeners` is module-global; without this fixture, a test
    that calls `app_state.on_event(...)` (directly or transitively
    via `services.github_poller.init()`) leaks the registration for
    the rest of the pytest session. Subsequent tests that emit
    `github.*` (or whatever wildcard the leaker registered) then
    fan out to listener daemons, which read `app_state._db` -- the
    production handle by default -- and can write to the prod DB.
    """
    import app_state
    snapshot = {k: list(v) for k, v in app_state._event_listeners.items()}
    snapshot_subs = list(app_state._event_subscribers)
    yield
    # Drain any in-flight dispatch threads BEFORE wiping listener
    # state, so a callback that captured `app_state._db` mid-flight
    # doesn't observe a partially-restored module after this test
    # ends and the next test's fixture rewires _db.
    try:
        app_state.await_dispatch_threads(timeout=5.0)
    except Exception:
        pass
    app_state._event_listeners.clear()
    app_state._event_listeners.update(
        {k: list(v) for k, v in snapshot.items()}
    )
    app_state._event_subscribers.clear()
    app_state._event_subscribers.extend(snapshot_subs)
    # Reset the github_poller idempotency flag so a later test that
    # calls init() inside this fresh-listener landscape gets to
    # re-register (matching the behaviour callers expect).
    try:
        from services import github_poller as _gh_p
        _gh_p._listeners_registered = False
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _isolate_event_dbs(tmp_path):
    """Redirect events/notif DB path to tmp_path so tests never write to prod.

    Since all tables now live in eva.db, _NOTIF_DB_PATH just points to
    the same DB file. The patched_server fixture will replace _db entirely.
    For tests that only rely on the autouse fixture (no patched_server),
    we redirect _NOTIF_DB_PATH to a temp file with the events table.
    """
    import server
    import app_state

    orig_notif = app_state._NOTIF_DB_PATH

    tmp_notif_path = tmp_path / "events.db"
    # Set via server module so __setattr__ propagates to app_state
    server._NOTIF_DB_PATH = tmp_notif_path
    # Create the events table in the tmp db for tests that use inline sqlite3
    import sqlite3
    with sqlite3.connect(str(tmp_notif_path)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_id TEXT,
            title TEXT NOT NULL,
            message TEXT,
            type TEXT DEFAULT 'info',
            severity TEXT DEFAULT 'info',
            url TEXT,
            ts TEXT NOT NULL,
            read INTEGER DEFAULT 0,
            session TEXT DEFAULT ''
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_source ON events(source, source_id)")

    yield

    server._NOTIF_DB_PATH = orig_notif


@pytest.fixture()
def tmp_data_dir(tmp_path):
    """Create a temporary data directory with config.yaml and sessions/."""
    config_path = tmp_path / "config.yaml"
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    config = {
        "projects": {
            "test-proj": {
                "name": "Test Project",
                "description": "A test project",
                "repo": "repo",
                "jira": "example",
                "has_tickets": True,
                "umbrella_tickets": [],
                "design_doc": None,
            },
            "empty-proj": {
                "name": "Empty Project",
                "description": "Project with no tasks",
                "repo": "svc",
                "jira": "internal",
                "umbrella_tickets": [],
                "design_doc": None,
            },
        }
    }
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)

    return tmp_path


@pytest.fixture()
def patched_server(tmp_data_dir):
    """Patch server module to use temp directories and a temp SQLite DB.

    Patches CONFIG_PATH and server._db before the app
    processes any requests. Inserts test tasks via the EvaDB API.
    """
    import server
    from eva_db import EvaDB

    # Create a single temp EvaDB for this test
    db_path = str(tmp_data_dir / "eva.db")
    temp_db = EvaDB(db_path)

    # Seed projects table to match config.yaml fixture
    temp_db.create_project("test-proj", name="Test Project", description="A test project",
                           repo="repo", jira="example", has_tickets=True)
    temp_db.create_project("empty-proj", name="Empty Project", description="Project with no tasks",
                           repo="svc", jira="internal")

    # Insert test tasks into the temp DB
    temp_db.create_task(
        project="test-proj",
        task_id="task-a",
        description="Task A - foundation work",
        type="feature",
        status="done",
        group_name="core",
    )
    temp_db.add_pr(
        project="test-proj",
        task_id="task-a",
        number=100,
        url="https://github.com/example/repo/pull/100",
        status="merged",
    )

    temp_db.create_task(
        project="test-proj",
        task_id="task-b",
        description="Task B - depends on A",
        type="feature",
        status="in_progress",
        group_name="core",
    )
    temp_db.set_dependencies("test-proj", "task-b", ["task-a"])
    temp_db.add_pr(
        project="test-proj",
        task_id="task-b",
        number=200,
        url="https://github.com/example/repo/pull/200",
        status="open",
    )

    temp_db.create_task(
        project="test-proj",
        task_id="task-c",
        description="Task C - depends on B (should be blocked)",
        type="feature",
        status="not_started",
        group_name="extension",
        ticket_id="EX-123",
        ticket_url="https://issues.example.org/jira/browse/EX-123",
    )
    temp_db.set_dependencies("test-proj", "task-c", ["task-b"])

    temp_db.create_task(
        project="test-proj",
        task_id="task-d",
        description="Task D - no dependencies, has ticket",
        type="bug",
        status="not_started",
        group_name="core",
        ticket_id="EX-99999",
        ticket_url="https://issues.example.org/jira/browse/EX-99999",
    )

    original_config = server.CONFIG_PATH
    original_db = server._db

    # server.__setattr__ propagates to app_state automatically
    server.CONFIG_PATH = tmp_data_dir / "config.yaml"
    server._db = temp_db
    server._config_cache["data"] = None
    server._config_cache["mtime"] = 0

    # Event DBs are already isolated by the autouse _isolate_event_dbs fixture

    yield server

    # Drain any in-flight `/api/review-requests/sync` (or future sync
    # routes that follow the same registry pattern) before we close
    # the DB. Without this, a daemon thread started during the test
    # races us to the sqlite handle on teardown -- caught by a
    # bisect of an intermittent segfault that would land roughly 1
    # in 4 full-suite runs.
    try:
        from routes.prs import _await_sync_threads
        _await_sync_threads(timeout=5.0)
    except Exception:
        # If the route isn't importable for some reason, fall through
        # to the same close-and-pray behaviour as before -- worst case
        # is the original flake, which we'll still be ahead of.
        pass

    # Drain emit_event listener-dispatch daemons before swapping
    # `_db` back. A `github.*` listener mid-flight reads
    # `app_state._db` at call time -- if we restored the production
    # handle before the thread exited, the listener would write to
    # data/eva.db. Drains here AND the autouse `_isolate_event_listeners`
    # so the safety net is two-deep.
    try:
        import app_state as _as
        _as.await_dispatch_threads(timeout=5.0)
    except Exception:
        pass

    server.CONFIG_PATH = original_config
    server._db = original_db
    server._config_cache["data"] = None
    server._config_cache["mtime"] = 0

    temp_db.close()


@pytest.fixture()
def client(patched_server):
    """Return a TestClient backed by the patched server app."""
    from starlette.testclient import TestClient

    return TestClient(patched_server.app)


@pytest.fixture()
def mock_tmux():
    """Mock tmux subprocess calls so tests don't need a real tmux.

    Each `from adapters.tmux import X` binds the function object into
    the importing module's namespace, so patching the adapter alone
    doesn't propagate. Each importing module is patched explicitly
    below. A single patch on `adapters.tmux.X` would only cover code
    that does `from adapters import tmux; tmux.X(...)` style access,
    which Eva no longer uses.
    """
    exists_mock = MagicMock(return_value=False)
    capture_mock = MagicMock(return_value="")
    send_keys_mock = MagicMock()
    launch_mock = MagicMock()
    launch_argv_mock = MagicMock()
    with patch("app_state._tmux_session_exists", exists_mock), \
         patch("common.sessions.session_exists", exists_mock), \
         patch("common.sessions.launch_session_argv", launch_argv_mock), \
         patch("routes.prs.session_exists", exists_mock), \
         patch("routes.sessions.session_exists", exists_mock), \
         patch("routes.sessions.capture_output", capture_mock), \
         patch("routes.sessions.send_keys", send_keys_mock), \
         patch("routes.sessions.launch_session", launch_mock), \
         patch("routes.terminal.session_exists", exists_mock):
        yield {
            "exists": exists_mock,
            "capture": capture_mock,
            "send_keys": send_keys_mock,
            "launch": launch_mock,
            "launch_argv": launch_argv_mock,
        }
