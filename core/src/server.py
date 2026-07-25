"""Eva server -- thin entry point and backward-compat re-export shim.

All implementation lives in app_state.py and routes/*.py.
This module re-exports every public name so that existing tests using
``patch("server.xxx")`` or ``from server import xxx`` continue to work.

Attribute assignments (e.g. ``server._db = ...`` from test fixtures)
are automatically propagated to ``app_state`` so that route modules
which access ``app_state._db`` see the patched value.
"""

# Re-export shared state from app_state
from app_state import (  # noqa: F401
    app,
    CONFIG_PATH,
    EVA_DB_PATH,
    _db,
    _config_cache,
    ALLOWED_ORGS,
    ALLOWED_REPOS,
    FORK_TO_UPSTREAM,
    _parse_pr_number,
    is_repo_allowed,
    load_config,
    save_config,
    _load_gh_tokens,
    _gh_tokens,
    gh_account_for_repo,
    _build_repo_authors,
    gh_run,
    gh_run_json,
    gh_run_async,
    emit_event,
    on_event,
    _event_listeners,
    _event_subscribers,
    _NOTIF_DB_PATH,
    _init_notif_db,
    _notif_db,
    # project helpers
    project_name_map,
    # task helpers
    load_tasks,
    save_task,
)

# Re-export from routes.projects (project helpers, task CRUD models)
from routes.projects import (  # noqa: F401
    compute_project_stats,
    is_task_blocked,
    suggest_task_status,
    enrich_project,
    _validate_task_id,
)
from routes.tasks import (  # noqa: F401
    TaskUpdate,
    TaskCreate,
    TaskClose,
    TaskRename,
)

# Register terminal + worklog + settings routes
import routes.terminal  # noqa: F401
import routes.worklog  # noqa: F401
import routes.settings  # noqa: F401
import routes.cron_jobs  # noqa: F401
import routes.tickets  # noqa: F401
import services.cert_checker  # noqa: F401 -- starts background cert check thread

# Extension plugins (under `<extension>/src/<plugin>/`) are discovered
# by the plugin framework and have their routes mounted at module
# import time -- before the static catch-all -- and their scheduler
# jobs registered at lifespan startup. No re-exports here; tests
# should import from the plugin path directly when they need to
# patch internal helpers.

# Re-export from app_state (usage DB path)
from app_state import _USAGE_DB_PATH  # noqa: F401

# Re-export from routes.system
from routes.system import (  # noqa: F401
    _init_usage_db,
    _save_usage_record,
)

# Re-export from routes.events
from routes.events import (  # noqa: F401
    _gh_last_poll,
    _load_seen_ids,
    _lookup_pr_by_branch,
    _GH_POLL_INTERVAL,
    _poll_github_notifications,
    _build_gh_events,
    _on_gh_notification,
    _update_task_from_notification,
    _on_gh_pr_status_update,
    MarkReadBody,
)
# Re-export live stats + workstats from system (moved from routes.events)
from common.system import (  # noqa: F401
    _live_stats_cache,
    _fetch_live_stats,
    _workstats_cache,
)

# Re-export from routes.prs
from routes.prs import (  # noqa: F401
    _pr_info_cache,
    _fetch_fork_ci,
    _aggregate_ci_status,
    _is_externally_merged,
    _resolve_pr_status,
    _SYNC_VIEW_FIELDS,
    _fetch_pr_detail,
)
# _match_pr_to_task now lives in prs (the helpers that used to be in
# routes.prs moved there in the core-consolidation iter). Re-exported here
# so `patched_server._match_pr_to_task` still resolves for legacy tests.
from common.prs import _match_pr_to_task  # noqa: F401

# Re-export from routes.sessions
from routes.sessions import (  # noqa: F401
    build_background,
    _session_states,
    SessionOpen,
    SessionLaunch,
)

# Keep standard library imports available for test patches like "server.subprocess.run"
import subprocess  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401
import os  # noqa: F401
import time  # noqa: F401
import threading  # noqa: F401
import sqlite3  # noqa: F401
from datetime import date as _date, datetime, timedelta, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
import concurrent.futures  # noqa: F401
import yaml  # noqa: F401

# -- Background job scheduler ------------------------------------------------
# All periodic work (cert check, GitHub poll, Slack channels, plugin pollers)
# runs as jobs on a single AsyncIOScheduler; see services/scheduler.py.
# We attach startup/shutdown hooks that (a) register the jobs and (b) tear
# the scheduler down cleanly on reload.
#
# Tests: `conftest.py` sets EVA_DISABLE_SCHEDULER=1 so TestClient lifespan
# invocations do NOT spin up the real scheduler during the suite.

import os as _os
from services import slack_monitor as _slack_monitor  # noqa: F401 -- back-compat shim
from common import channels as _channel_registry
from services import github_poller as _gh_poller
from services import cert_checker as _cert_checker
from services import usage_refresh as _usage_refresh
from services.scheduler import get_scheduler, start_scheduler, stop_scheduler
from common import plugins as _plugin_registry


def _register_channel_jobs():
    """Phase 2 for registered channels -- each one attaches its
    scheduled work to the live APScheduler. Channel registration
    happened earlier at module-import time via discover_package /
    discover_dir."""
    _channel_registry.start_all_jobs(get_scheduler())


def _start_plugin_jobs():
    """Phase 2 of plugin wiring: hand every registered plugin a live
    APScheduler so it can register its periodic jobs.

    Phase 1 (route mounting) ran at module-import time, before the
    static catch-all in `routes/static.py`; see the
    `_plugin_registry.register_routes(app)` call there. This hook
    runs in the lifespan AFTER `start_scheduler()` because plugins
    call `scheduler.add_job(...)` from `start_jobs()` and need the
    scheduler to be alive. Per-plugin failures log and don't abort
    peer plugins or the rest of the boot.
    """
    try:
        _plugin_registry.start_all_jobs(get_scheduler())
    except Exception as e:
        print(f"[plugins] start_jobs failed: {e}", flush=True)


def _register_core_jobs():
    """Cert check + GitHub poll. These don't need config; always run."""
    sched = get_scheduler()
    sched.add_job(
        _cert_checker.check_certs_once,
        "interval",
        seconds=_cert_checker.get_interval_seconds(),
        id="cert_checker",
        replace_existing=True,
    )
    _gh_poller.init()
    sched.add_job(
        _gh_poller.poll_github_once,
        "interval",
        seconds=_gh_poller.get_interval_seconds(),
        id="github_poller",
        replace_existing=True,
    )
    # AI usage refresh: keeps _usage_cache warm + pushes usage.updated
    # events so the frontend can react without polling.
    sched.add_job(
        _usage_refresh.refresh_usage_once,
        "interval",
        seconds=_usage_refresh.get_interval_seconds(),
        id="usage_refresh",
        replace_existing=True,
    )
    # Per-plugin scheduled jobs are now registered via
    # `_plugin_registry.start_all_jobs()` after the scheduler is up.

    # Review queue sync: two cadences mirroring the All PRs pattern.
    # Fast dirty-only pass picks up notification-marked rows (cheap);
    # full sync on a longer interval catches new review requests +
    # removes stale GitHub rows.
    def _review_sync_dirty():
        from common.prs import sync_review_requests_dirty_only
        try:
            sync_review_requests_dirty_only()
        except Exception as e:
            print(f"[review] dirty sync failed: {e}", flush=True)

    def _review_sync_full():
        from common.prs import sync_review_requests
        try:
            sync_review_requests()
        except Exception as e:
            print(f"[review] full sync failed: {e}", flush=True)

    from common import settings as _review_settings
    sched.add_job(
        _review_sync_dirty, "interval",
        seconds=_review_settings.get_interval_seconds(
            _review_settings.KEY_INTERVAL_REVIEW_SYNC_DIRTY, 60),
        id="review_sync_dirty", replace_existing=True)
    sched.add_job(
        _review_sync_full, "interval",
        seconds=_review_settings.get_interval_seconds(
            _review_settings.KEY_INTERVAL_REVIEW_SYNC_FULL, 1800),
        id="review_sync_full", replace_existing=True)

    # Task PR sync: two cadences mirroring the review-queue pattern.
    # Fast dirty-only pass consumes the flags the notification poller
    # sets on task PRs (cheap -- no-op when nothing is dirty); full pass
    # is a backstop that refreshes every open task PR so a dropped
    # notification doesn't leave a PR permanently stale. Keeps the DB
    # fresh so opening a task reads current data (DB as cache).
    def _pr_sync_dirty():
        from services.pr_sync_service import sync_task_prs_dirty_once
        sync_task_prs_dirty_once()

    def _pr_sync_full():
        from services.pr_sync_service import sync_task_prs_full_once
        sync_task_prs_full_once()

    sched.add_job(
        _pr_sync_dirty, "interval",
        seconds=_review_settings.get_interval_seconds(
            _review_settings.KEY_INTERVAL_PR_SYNC_DIRTY, 60, min_s=30),
        id="pr_sync_dirty", replace_existing=True)
    sched.add_job(
        _pr_sync_full, "interval",
        seconds=_review_settings.get_interval_seconds(
            _review_settings.KEY_INTERVAL_PR_SYNC_FULL, 1800, min_s=120),
        id="pr_sync_full", replace_existing=True)

    # Session-state reaper: detects sessions that died externally
    # (machine reboot, `tmux kill-server`, host recovery recovery without socket).
    # No agent hook fires for those, so without the reaper the cache
    # would keep showing them as 'thinking' forever.
    def _reap_sessions():
        from common import session_state
        try:
            session_state.reap_dead_sessions()
        except Exception as e:
            print(f"[session-state] reap failed: {e}", flush=True)

    sched.add_job(_reap_sessions, "interval", seconds=15,
                  id="session_state_reaper", replace_existing=True)

    # JIRA tickets sync: periodic refresh of the local tickets cache
    # so the Tickets page feels live without manual sync clicks. The
    # tick itself is a no-op when JIRA isn't configured.
    from services import jira_sync as _jira_sync
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZoneInfo
    sched.add_job(
        _jira_sync.sync_tickets_once,
        "interval",
        seconds=_jira_sync.get_interval_seconds(),
        id=_jira_sync.JIRA_SYNC_JOB_ID,
        replace_existing=True,
        # Run one tick immediately on boot so a freshly-restarted server
        # reflects JIRA-side changes (resolved / reassigned tickets)
        # without waiting out the full interval. `sync_tickets_once` is a
        # no-op when JIRA isn't configured, so this is safe on first run.
        next_run_time=_dt.now(_ZoneInfo("America/Los_Angeles")),
    )


def _on_startup():
    """Boot tasks: seed settings, register pollers, start scheduler.
    Pulled out of the @app.on_event decorator (deprecated since
    Starlette 0.30) so the lifespan handler below can call it
    synchronously."""
    if _os.environ.get("EVA_DISABLE_SCHEDULER") == "1":
        return
    # Seed the settings DB from config.yaml on first boot. Idempotent:
    # _seed only writes a key when it's absent, so subsequent edits via
    # the Settings UI are never overwritten.
    try:
        import app_state as _as
        from common import settings as _core_settings
        n = _core_settings.seed_from_yaml(_as.load_config())
        if n:
            print(f"[init] seeded {n} settings from config.yaml", flush=True)
    except Exception as e:
        print(f"[init] seed_from_yaml failed: {e}", flush=True)
    # Rebuild the session-state cache from tmux BEFORE registering
    # any subscribers. The cache is the authoritative source for
    # "what is each session doing right now"; we lost it on shutdown
    # and tmux is the ground truth to recover from. Idempotent.
    try:
        from common import session_state as _ssn
        _ssn.rebuild_from_tmux()
        # Auto-resume any session whose tmux died while Eva was down
        # but where we still have a claude UUID on record. Marks
        # unrecoverable rows as `crashed` so the UI can prompt the
        # user. Runs synchronously: the user shouldn't see partial
        # state in the SSE replay during the first connect.
        _ssn.recover_crashed_sessions()
    except Exception as e:
        print(f"[init] session_state startup failed: {e}", flush=True)
    _register_core_jobs()
    _register_channel_jobs()
    start_scheduler()
    # Plugin routes were already mounted at module-import time
    # (before the static catch-all); only the scheduled-jobs phase
    # runs here, AFTER the scheduler comes up.
    _start_plugin_jobs()
    # Arm user-defined cron jobs AFTER the scheduler is up; the
    # cron_runner depends on the scheduler being live.
    try:
        from services.cron_runner import register_all as _register_user_cron
        summary = _register_user_cron()
        if summary["registered"] or summary["invalid"]:
            print(
                f"[init] cron jobs: registered={summary['registered']} "
                f"skipped={summary['skipped']} invalid={summary['invalid']}",
                flush=True,
            )
    except Exception as e:
        print(f"[init] cron-runner register_all failed: {e}", flush=True)


def _on_shutdown():
    """Stop the scheduler; idempotent and tolerant of an EVA_DISABLE
    -SCHEDULER=1 boot (test fixture path) where nothing was started."""
    if _os.environ.get("EVA_DISABLE_SCHEDULER") == "1":
        return
    stop_scheduler()


# Lifespan context manager: replaces the deprecated `on_event` hooks.
# Starlette 0.30+ deprecated `add_event_handler('startup', ...)` in
# favour of an async context manager passed at FastAPI construction
# (or assigned to `app.router.lifespan_context` afterwards). We use the
# post-hoc assignment here because `app` is constructed in
# `app_state.py` before this module's startup logic exists.
from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def _lifespan(_app):
    _on_startup()
    try:
        yield
    finally:
        _on_shutdown()


app.router.lifespan_context = _lifespan

# Plugin discovery + route registration must run BEFORE the static
# catch-all below. FastAPI matches routes in insertion order, and
# `routes/static.py` mounts a catch-all `/{path:path}` that returns
# 404 for unknown `/api/*` -- so any plugin route appended after
# that point gets shadowed even though it shows up in the OpenAPI
# table. Discovery is idempotent (registry dedups on `id`) and uses
# `importlib.reload` for already-cached modules so re-discovery
# during tests fires the registration hooks again.
#
# Two plugin sources:
#   `plugins`              OSS package under `core/src/plugins/`,
#                          discovered via standard pkgutil iteration.
#   `<extension>/src/`     each registered extension's plugin dir
#                          (one folder per plugin, no Python-package
#                          wrapper); loaded by file path via
#                          `discover_dir()`.
import app_state as _as_for_plugins
from common.extensions import discover as _discover_extensions

# Native (OSS) plugin tree -- always present, no marker required.
_plugin_registry.discover_dir(_as_for_plugins.REPO_ROOT / "core" / "src" / "plugins")

# Discover every extension namespace (any sibling-of-core folder
# carrying an `extension.conf` marker) and load its plugins +
# agents. The scanner returns an empty iterator on an OSS-only
# checkout (no extensions present), so neither hardcoded folder
# names nor try/except blocks live in this file.
_EXTENSIONS = list(_discover_extensions(_as_for_plugins.REPO_ROOT))
for _ext in _EXTENSIONS:
    if _ext.src.is_dir():
        _plugin_registry.discover_dir(_ext.src)

# Channels (message-source integrations). OSS slack lives in
# `core/src/channels/`; extensions can drop more under
# `<ext>/src/channels/`. Default registry is empty -- a fresh install
# with no channel impls works fine, just with no message-source events.
_channel_registry.discover_package("channels")
for _ext in _EXTENSIONS:
    ch_dir = _ext.src / "channels"
    if ch_dir.is_dir():
        _channel_registry.discover_dir(ch_dir)

# Agents are infrastructure (the CLI Eva shells out to), not user-
# facing plugins -- they live in their own registry. OSS default
# `claude` lives in `core/src/agents/`; each extension's agents
# (under `<extension>/src/agents/`) get layered on.
from common import agent as _agent_registry  # noqa: E402
_agent_dirs = [_as_for_plugins.REPO_ROOT / "core" / "src" / "agents"]
for _ext in _EXTENSIONS:
    if _ext.agents_dir.is_dir():
        _agent_dirs.append(_ext.agents_dir)
_agent_registry.discover_agents(*_agent_dirs)

# Cert / auth-token providers share the same shape: file-scan
# discovery, self-registration on import. OSS install ships with no
# providers (and gets an empty `/api/certs` response); extensions
# under `<extension>/src/certs/` populate the registry.
from common import cert as _cert_registry  # noqa: E402
_cert_dirs = [_as_for_plugins.REPO_ROOT / "core" / "src" / "certs"]
for _ext in _EXTENSIONS:
    _ext_certs = _ext.root / "src" / "certs"
    if _ext_certs.is_dir():
        _cert_dirs.append(_ext_certs)
_cert_registry.discover_certs(*_cert_dirs)

# Run each extension's `seed.py` (optional) AFTER plugins / agents
# / certs have registered. Seeds typically INSERT extra
# action_definitions or settings rows the extension wants to ship
# alongside its plugins. They can also call `register_project(...)`
# to declare projects whose rows then get flushed to the DB right
# after. Idempotent: every insert is INSERT-OR-IGNORE so re-runs
# are no-ops.
from common.extensions import run_seed_files as _run_seed_files  # noqa: E402
_run_seed_files(_EXTENSIONS)

# Flush any projects the extension seeds declared into the DB. Done
# AFTER seed files run (so the declarations are queued) but BEFORE
# routes/static mounts (so a GET /api/projects sees the seeded rows
# from the very first request).
from common.projects import flush_registered_to_db as _flush_projects  # noqa: E402
_flush_projects()

_plugin_registry.register_routes(app)

# Static file serving (must be last -- catch-all route)
import routes.static  # noqa: F401

# -- Module wrapper: propagate attribute writes to app_state --
# When tests do ``server._db = ...``, this also sets ``app_state._db``.

import sys as _sys
import types as _types
import app_state as _app_state_mod

_PROPAGATED_TO_APP_STATE = frozenset({
    "CONFIG_PATH", "EVA_DB_PATH",
    "_db", "_config_cache",
    "_NOTIF_DB_PATH",
    "_USAGE_DB_PATH",
    "_gh_tokens",
    "project_name_map", "load_tasks", "save_task",
    "gh_run", "gh_run_json", "gh_run_async",
    "emit_event", "on_event",
    "_event_listeners", "_event_subscribers",
    "load_config", "save_config",
    "ALLOWED_ORGS", "ALLOWED_REPOS", "FORK_TO_UPSTREAM",
})

# Attrs to also propagate to their origin route modules
_PROPAGATED_TO_ROUTES = {
    "_update_task_from_notification": "routes.events",
    "_on_gh_notification": "routes.events",
    "_on_gh_pr_status_update": "routes.events",
    "_build_gh_events": "routes.events",
    "_poll_github_notifications": "routes.events",
    "_fetch_live_stats": "system",
    "_fetch_fork_ci": "routes.prs",
    "_fetch_pr_detail": "routes.prs",
    "_session_states": "routes.sessions",
}


class _ServerModule(_types.ModuleType):
    """Custom module class that propagates attribute writes to app_state and route modules."""

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name in _PROPAGATED_TO_APP_STATE:
            setattr(_app_state_mod, name, value)
        route_mod = _PROPAGATED_TO_ROUTES.get(name)
        if route_mod:
            import importlib
            try:
                mod = importlib.import_module(route_mod)
                setattr(mod, name, value)
            except (ImportError, AttributeError):
                pass


# Replace this module in sys.modules with a _ServerModule instance,
# copying all attributes that were populated by the import statements above.
_orig = _sys.modules[__name__]
_wrapped = _ServerModule(__name__, __doc__)
_wrapped.__dict__.update({k: v for k, v in _orig.__dict__.items()
                          if k != "__dict__"})
_wrapped.__file__ = _orig.__file__
_wrapped.__spec__ = _orig.__spec__
_wrapped.__package__ = _orig.__package__
_wrapped.__loader__ = getattr(_orig, "__loader__", None)
_sys.modules[__name__] = _wrapped

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("EVA_PORT", "8021"))
    uvicorn.run(app, host="0.0.0.0", port=port, timeout_keep_alive=5)
