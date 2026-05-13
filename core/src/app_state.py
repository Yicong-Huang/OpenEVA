"""Shared application state: singletons, config, GitHub helpers, event bus, tmux helpers."""

import os
import sqlite3
import subprocess  # noqa: F401 -- patched by tests (app_state.subprocess)
import threading
import uuid as _uuid
import yaml
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI

# -- Config paths and DB singletons --
#
# `app_state.py` lives at `core/src/app_state.py` after the
# core/extension split. Repo root is two levels up. The layout is:
#   eva/                 <- REPO_ROOT
#   ├── core/src/app_state.py  <- this file
#   ├── data/eva.db
#   ├── config.yaml
#   ├── static/
#   └── frontend/dist/
# Anything that lives at the repo root (config, DB, static, frontend
# build output) is resolved through REPO_ROOT so future moves of
# this file don't silently break path lookups.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CONFIG_PATH = REPO_ROOT / "config.yaml"

from eva_db import EvaDB
EVA_DB_PATH = REPO_ROOT / "data" / "eva.db"

_db = EvaDB(str(EVA_DB_PATH))

# Legacy `config.yaml::projects` -> DB migration removed. Projects
# are now declared via `common.projects.register_project(...)` from
# each extension's `src/seed.py`; the framework flushes those into
# the DB at boot (see `server.py` + `conftest.py`). User-created
# projects (UI / `eva-cli`) keep their DB row through any extension
# re-declarations because the flush uses INSERT OR IGNORE.


# Note: legacy config.yaml -> settings-table migration used to live
# here, hardcoding specific plugin section names. It moved out of
# core so plugin-specific knowledge stays with each plugin. Plugins
# that want a yaml fallback seed their defaults via their own
# `plugin.conf` (`_seed_conf` in common.plugins.discover_dir) and
# the Settings UI is the single source of truth at runtime.


def _apply_repo_overrides_from_settings():
    """Mutate `adapters.github.ALLOWED_REPOS` and `FORK_TO_UPSTREAM`
    in-place so any value persisted via the Settings UI takes over
    from the hardcoded defaults at process startup.

    Done here -- BEFORE `routes/` and `core/` get imported -- so
    module-level computations like `common.prs._FORK_CI_REPOS` and
    `common.system._CONTRIBUTOR_REPO` see the overridden values from
    the very first import. We read the settings table directly
    (avoiding `settings`) because that module imports app_state
    and would create a circular import during its own initialisation.

    The trade-off: editing rules in the UI requires a server restart
    to take effect (matches the existing "intervals need restart"
    UX hint)."""
    from adapters import github as _gh

    import sys as _sys

    # Allowed repos: settings-table list wins over the hardcoded set.
    raw = _db.get_setting("service.github.allowed_repos")
    if isinstance(raw, list) and raw:
        cleaned = [r for r in raw if isinstance(r, str) and r]
        if cleaned:
            _gh.ALLOWED_REPOS.clear()
            _gh.ALLOWED_REPOS.update(cleaned)
            _gh.ALLOWED_ORGS.clear()
            _gh.ALLOWED_ORGS.update(
                r.split("/")[0] for r in cleaned if r.endswith("/*")
            )
            print(f"[init] applied allowed_repos override: {len(cleaned)} entries",
                  flush=True, file=_sys.stderr)

    # Fork->upstream mapping: settings-table dict wins over hardcoded.
    raw_ftu = _db.get_setting("service.github.fork_to_upstream")
    if isinstance(raw_ftu, dict) and raw_ftu:
        cleaned_ftu = {
            k: v for k, v in raw_ftu.items()
            if isinstance(k, str) and isinstance(v, str) and k and v
        }
        if cleaned_ftu:
            _gh.FORK_TO_UPSTREAM.clear()
            _gh.FORK_TO_UPSTREAM.update(cleaned_ftu)
            print(f"[init] applied fork_to_upstream override: {len(cleaned_ftu)} entries",
                  flush=True, file=_sys.stderr)

    # gh CLI account-rules: list of {match, account} dicts. Empty
    # list -> module falls back to the hardcoded maintainer heuristic.
    raw_rules = _db.get_setting("service.github.account_rules")
    if isinstance(raw_rules, list) and raw_rules:
        cleaned_rules = []
        for r in raw_rules:
            if not isinstance(r, dict):
                continue
            match = r.get("match", "")
            account = r.get("account", "")
            if not isinstance(match, str) or not isinstance(account, str):
                continue
            if not account:
                continue  # account is required; match is optional (catch-all)
            cleaned_rules.append({"match": match, "account": account})
        if cleaned_rules:
            _gh._account_rules = cleaned_rules
            print(f"[init] applied gh account_rules: {len(cleaned_rules)} entries",
                  flush=True, file=_sys.stderr)

    # Safety check: if multiple gh CLI accounts are loaded but no
    # routing rules are configured, every repo gets routed to the
    # FIRST account picked by `_default_account_fallback`. That
    # silently breaks PR sync for repos that should use the other
    # account (a real incident: the maintainer's install hit this
    # after the personal-name fallback was removed for open-source).
    # Loud stderr warning + a clear remediation hint.
    if len(_gh._gh_tokens) >= 2 and not _gh._account_rules:
        accounts = ", ".join(sorted(_gh._gh_tokens))
        print(
            "[init] WARNING: gh CLI has multiple accounts loaded "
            f"({accounts}) but service.github.account_rules is empty. "
            "Every repo will route to whichever account loads first, "
            "which silently breaks PR sync for repos that need the "
            "other account. Configure rules in Settings > GitHub or "
            "set `service.github.account_rules` to a list of "
            "{match, account} entries (catch-all rule has match='').",
            flush=True, file=_sys.stderr,
        )


_apply_repo_overrides_from_settings()

# -- Repo config --

# GitHub integration (allow-list, tokens, CLI runners) lives in
# adapters.github. We re-export the names so existing callers using
# `app_state.gh_run(...)` / `app_state.ALLOWED_REPOS` keep working
# while new code can `from adapters.github import gh_run`.
from adapters.github import (  # noqa: E402, F401
    ALLOWED_REPOS,
    ALLOWED_ORGS,
    FORK_TO_UPSTREAM,
    is_repo_allowed,
    _parse_pr_number,
)


# -- FastAPI app instance --

from starlette.middleware.gzip import GZipMiddleware

app = FastAPI()
app.add_middleware(GZipMiddleware, minimum_size=500)

# -- Config cache and load/save --

_config_cache = {"data": None, "mtime": 0}


def load_config() -> dict:
    """Load config.yaml with mtime-based cache to avoid repeated disk
    reads. Also syncs any new projects from yaml to DB.

    Tolerates a missing config.yaml so an open-source user can boot
    Eva on a fresh checkout without first copying config.example.yaml.
    Settings come from the DB (with empty defaults), and projects can
    be created via the UI -- the YAML is only a convenience seed file.
    """
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        # No config.yaml on disk -- common on first boot for OSS users.
        # Cache an empty config so we don't keep retrying the stat call.
        if _config_cache["data"] is None:
            _config_cache["data"] = {}
            _config_cache["mtime"] = 0
        return _config_cache["data"]
    if _config_cache["data"] is not None and mtime == _config_cache["mtime"]:
        return _config_cache["data"]
    with open(CONFIG_PATH) as f:
        data = yaml.safe_load(f) or {}
    _config_cache["data"] = data
    _config_cache["mtime"] = mtime
    return data


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    _config_cache["data"] = config
    _config_cache["mtime"] = CONFIG_PATH.stat().st_mtime


# -- GitHub CLI helpers --
# The low-level `gh_run` (only place that shells out to the gh binary)
# lives in adapters/github.py; the higher-level wrappers live here so
# they always dispatch through the re-exported `gh_run` name -- which
# means `monkeypatch.setattr(app_state, "gh_run", mock)` (or its server
# equivalent) is still picked up by gh_run_json / gh_run_or_raise /
# gh_run_async without the adapter needing to be patched separately.

import asyncio  # noqa: E402
import concurrent.futures  # noqa: E402
import json as _json  # noqa: E402

from adapters.github import (  # noqa: E402, F401
    _gh_tokens,
    _load_gh_tokens,
    gh_account_for_repo,
    _build_repo_authors,
    gh_run,
)


def gh_run_json(cmd, repo="", timeout=20, default=None):
    """Run gh CLI and parse stdout as JSON. Returns default on failure."""
    result = gh_run(cmd, repo=repo, timeout=timeout)
    if result.returncode != 0:
        return default
    try:
        return _json.loads(result.stdout)
    except (ValueError, _json.JSONDecodeError):
        return default


def gh_run_or_raise(args: list, repo: str = "", timeout: int = 20, stderr_limit: int = 0):
    """Run gh CLI and raise HTTPException(500) on non-zero exit.

    Returns the CompletedProcess on success so callers can read stdout. This
    collapses the `result = gh_run(...); if result.returncode != 0: raise ...`
    pattern duplicated across every route that drives gh.

    stderr_limit: if > 0, truncate the error detail to that many chars.
    """
    from fastapi import HTTPException
    result = gh_run(args, repo=repo, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr
        if stderr_limit and detail:
            detail = detail[:stderr_limit]
        raise HTTPException(status_code=500, detail=detail)
    return result


# Lazy thread pool: created on first `gh_run_async` call and registered
# with `atexit.register` so process shutdown waits for in-flight gh
# calls instead of letting them race with the interpreter teardown.
# Eager module-level creation triggered occasional SIGSEGVs in the test
# suite when pytest's per-module teardown overlapped with the executor's
# own atexit hook (the executor module's atexit fires AFTER pytest's,
# but FastAPI's TestClient release happens between -- and tests that
# never used `gh_run_async` were paying the teardown cost anyway).
_gh_executor: "concurrent.futures.ThreadPoolExecutor | None" = None
_gh_executor_lock = threading.Lock()


def _get_gh_executor() -> concurrent.futures.ThreadPoolExecutor:
    """First-use lazy init for the gh thread pool; registers shutdown
    so the process exits cleanly. Idempotent under concurrent calls."""
    global _gh_executor
    if _gh_executor is not None:
        return _gh_executor
    with _gh_executor_lock:
        if _gh_executor is None:
            ex = concurrent.futures.ThreadPoolExecutor(
                max_workers=6, thread_name_prefix="gh-run",
            )
            import atexit as _atexit
            _atexit.register(lambda: ex.shutdown(wait=True, cancel_futures=True))
            _gh_executor = ex
    return _gh_executor


async def gh_run_async(args: list, repo: str = "", timeout: int = 20):
    """Async version of gh_run using a thread pool. The underlying gh CLI
    call is still sync -- this just lets FastAPI await it without blocking
    the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _get_gh_executor(),
        lambda: gh_run(args, repo=repo, timeout=timeout),
    )


# -- Event bus --

_event_listeners = {}  # event_name -> [callback, ...]
_event_subscribers = []  # list of asyncio.Queue for SSE clients

# Track listener-dispatch daemon threads spawned from `emit_event` so
# tests can drain them on fixture teardown. Without this, a daemon
# thread mid-flight when `patched_server` restores the production
# `_db` would happily call `app_state._db.update_pr_by_number(...)` on
# the prod handle -- a real prod-pollution path that bit Eva in the
# past (see the `<MagicMock ...>` repo-root leak guard in conftest).
_dispatch_threads: list = []
_dispatch_threads_lock = threading.Lock()

# Set by `eva-cli` at startup so events emitted in the CLI process get
# bounced through the running server's `/api/internal/emit-relay`
# endpoint -- otherwise the SSE subscribers (web UI) never see them
# because they live in a different process. Stays None inside the
# server process; relay is best-effort (server-down -> silent skip).
_event_relay_url: "str | None" = None

# Notification DB path -- now points to eva.db (kept for backward compat)
_NOTIF_DB_PATH = EVA_DB_PATH


def _init_notif_db():
    """No-op: events table is created by EvaDB._create_schema.

    Kept for backward compat (tests call app_state._init_notif_db()).
    """
    pass


def _notif_db():
    return sqlite3.connect(str(_NOTIF_DB_PATH))


_VALID_SEVERITIES = ("info", "warning", "error")


def emit_event(event_type, data, persist=True):
    """Emit a system event. Optionally persists to DB, dispatches to
    listeners, pushes to SSE.

    `persist=False` is for ephemeral push-only events (e.g. cache
    invalidation signals like `usage.updated` that the frontend needs
    but should NOT appear in the notifications feed).

    Contract (memo item #3 from architecture-discussion.md):
      - `event_type` is a dotted categorical id like `common.agent.task_done`
        or `github.ci_activity`. The `source` is derived as the
        prefix-before-first-dot.
      - `data["severity"]` is one of "info" / "warning" / "error" --
        invalid values silently coerce to "info" so a sloppy emitter
        can't poison the DB. (We don't raise to keep the event bus
        non-blocking; bad severity is a developer mistake, not a
        runtime concern.)
    """
    event_id = str(_uuid.uuid4())
    ts = data.get("ts") or ""
    if not ts:
        now = datetime.now()
        ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
    ntype = event_type
    source = ntype.split(".")[0] if "." in ntype else "system"
    severity = data.get("severity", "info")
    if severity not in _VALID_SEVERITIES:
        severity = "info"
    # Forward every caller-supplied field, then override with the
    # canonical envelope keys we control (id / ts / source / type /
    # severity). The previous allowlist was dropping event-specific
    # payload (`state`, `kind`, `target_id`, etc.) sent by
    # `common.session_state.set_state`, so live state-change events
    # arrived at the frontend without a `state` field and got
    # mis-classified as 'unknown'. Snapshot replay constructs its
    # own dict and didn't hit the allowlist, which is why state was
    # correct on connect and degraded after the first hook fire.
    n = {
        **data,
        "id": event_id,
        "ts": ts,
        "source": source,
        "source_id": data.get("source_id"),
        "title": data.get("title", ntype),
        "message": data.get("message", ""),
        "type": ntype,
        "severity": severity,
        "url": data.get("url"),
        "session": data.get("session", ""),
    }

    # Persist to events DB (dedup by source_id for github events)
    if persist:
        try:
            with sqlite3.connect(str(_NOTIF_DB_PATH)) as conn:
                source_id = n["source_id"]
                if source_id and source == "github":
                    existing = conn.execute(
                        "SELECT id FROM events WHERE source_id = ? AND source = 'github' LIMIT 1",
                        (source_id,),
                    ).fetchone()
                    if existing:
                        conn.execute(
                            "UPDATE events SET title=?, message=?, type=?, severity=?, url=?, ts=? WHERE id=?",
                            (n["title"], n["message"], ntype, n["severity"], n["url"], ts, existing[0]),
                        )
                        n["id"] = existing[0]
                    else:
                        conn.execute(
                            "INSERT INTO events (id, source, source_id, title, message, type, severity, url, ts, session) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (event_id, source, source_id, n["title"], n["message"], ntype, n["severity"], n["url"], ts, n.get("session", "")),
                        )
                else:
                    # Bug fix: the github branch above persisted
                    # `session` but this branch was missing it, so all
                    # agent event rows lost their tmux-session attribution.
                    # Symptom: a large batch of agent event rows in the live DB all had
                    # session='', breaking any prompt-submit -> task-done
                    # latency analysis that pairs by session.
                    conn.execute(
                        "INSERT INTO events (id, source, source_id, title, message, type, severity, url, ts, session) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (event_id, source, source_id, n["title"], n["message"], ntype, n["severity"], n["url"], ts, n.get("session", "")),
                    )
        except sqlite3.Error as e:
            print(f"[emit_event] DB write failed: {e}", flush=True)

    # Dispatch to backend listeners. Each callback runs on its own
    # daemon thread so a slow handler can't stall the SSE push loop
    # below. Threads are tracked in `_dispatch_threads` so test
    # fixtures can drain them before tearing down per-test DB state
    # (see `await_dispatch_threads`).
    def _spawn(cb):
        t = threading.Thread(target=cb, args=(n,), daemon=True)
        with _dispatch_threads_lock:
            # Opportunistic prune so the list doesn't grow unbounded
            # in long-running processes (production server, eva-cli).
            _dispatch_threads[:] = [
                x for x in _dispatch_threads if x.is_alive()
            ]
            _dispatch_threads.append(t)
        t.start()

    for cb in _event_listeners.get(ntype, []):
        _spawn(cb)
    # Wildcard listeners (match by prefix)
    for cb in _event_listeners.get(source + ".*", []):
        _spawn(cb)

    # Push to SSE subscribers (frontend)
    dead = []
    for q in _event_subscribers:
        try:
            q.put_nowait(n)
        except Exception:
            dead.append(q)
    for q in dead:
        try:
            _event_subscribers.remove(q)
        except ValueError:
            pass

    # CLI-process relay: when this is `eva-cli` (running outside the
    # fastapi server), the SSE subscribers above are empty -- the web
    # UI lives in another process. Bounce the event through the
    # server's `/api/internal/emit-relay` endpoint so its in-process
    # subscribers see it. Best-effort with a short timeout: server
    # might be down or slow, and the CLI's primary write already
    # succeeded above (DB persist + listeners), so a relay failure
    # must not break the user-facing command.
    #
    # Hard-disabled inside pytest. The relay would HTTP-POST events
    # into the running production server's `/api/internal/emit-relay`
    # endpoint, fanning them out to live web-UI SSE subscribers --
    # i.e. test events would appear in the user's real dashboard.
    # `PYTEST_CURRENT_TEST` is set automatically by pytest for every
    # test, so the gate auto-applies without per-test setup.
    if _event_relay_url and not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            import urllib.request as _urlreq
            import json as _json
            req = _urlreq.Request(
                _event_relay_url,
                data=_json.dumps({
                    "type": ntype, "data": n, "persist": False,
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            _urlreq.urlopen(req, timeout=2).read()
        except Exception:
            pass


def on_event(name, callback):
    """Register a listener for a global event."""
    _event_listeners.setdefault(name, []).append(callback)


def await_dispatch_threads(timeout: float = 5.0) -> int:
    """Block until every in-flight `emit_event` listener-dispatch
    thread finishes (or `timeout` elapses).

    Used by test fixtures (`patched_server`, `_isolate_event_dbs`) so
    a daemon thread spawned by a per-test emit doesn't outlive the
    fixture and reach into the next test's swapped-in `_db` --
    historically the path by which a github.* listener wrote to the
    production `data/eva.db` after teardown restored the global
    handle. Returns the count of threads that didn't finish in time
    (logged but ignored; daemon threads die on process exit).
    """
    import time as _time
    deadline = _time.time() + max(0.0, timeout)
    with _dispatch_threads_lock:
        alive = [t for t in _dispatch_threads if t.is_alive()]
        _dispatch_threads.clear()
    leftover = 0
    for t in alive:
        remaining = deadline - _time.time()
        if remaining <= 0:
            leftover += 1
            continue
        t.join(timeout=remaining)
        if t.is_alive():
            leftover += 1
    return leftover


# -- Usage DB path -- now points to eva.db (kept for backward compat)
_USAGE_DB_PATH = EVA_DB_PATH


# load_tasks below stamps each task's `session.running` from tmux. The
# adapter function is imported directly -- app_state used to also
# re-export it as `tmux_session_exists` etc. for legacy callers, but that
# made app_state into an import middleman and caused mock-propagation
# bugs in tests. Routes / core now import from `adapters.tmux` directly.
from adapters.tmux import session_exists as _tmux_session_exists  # noqa: E402


# -- Project helpers --

def project_name_map() -> dict:
    """Return {project_id: name} dict from DB."""
    return {p["id"]: p.get("name", p["id"]) for p in _db.list_projects()}


# -- Task helpers --

def load_tasks(project_id: str) -> dict:
    """Load all tasks for a project from SQLite, with session info."""
    tasks = _db.list_tasks(project_id)
    sessions = _db.list_sessions(project=project_id)
    session_map = {s["task_id"]: s for s in sessions}
    # Pull live state from the unified `session_state` cache.
    # The frontend reads state via the SSE-driven snapshot, but we
    # still attach it here so the initial page paint (before SSE
    # connects) shows something accurate.
    from common import session_state as _ssn
    result = {}
    for t in tasks:
        tid = t["task_id"]
        s = session_map.get(tid)
        if s:
            tmux_name = s["tmux_name"]
            cache_row = _ssn.get(tmux_name) or {}
            t["session"] = {
                "name": tmux_name,
                "running": _tmux_session_exists(tmux_name),
                "status": cache_row.get("state", ""),
            }
        result[tid] = t
    return result


def save_task(project_id: str, task_id: str, task: dict) -> None:
    """Save/update a task in SQLite."""
    existing = _db.get_task(project_id, task_id)
    if existing:
        fields = {}
        for k in ["description", "type", "status", "notes", "priority",
                  "ticket_id", "ticket_url", "follow_ups"]:
            if k in task:
                fields[k] = task[k]
        if "group" in task:
            fields["group_name"] = task["group"]
        if "ticket" in task and isinstance(task["ticket"], dict):
            fields["ticket_id"] = task["ticket"].get("id")
            fields["ticket_url"] = task["ticket"].get("url")
        if fields:
            _db.update_task(project_id, task_id, **fields)
        if "dependencies" in task:
            _db.set_dependencies(project_id, task_id, task["dependencies"])
    else:
        ticket = task.get("ticket", {}) or {}
        _db.create_task(
            project=project_id, task_id=task_id,
            description=task.get("description", ""),
            type=task.get("type", "feature"),
            status=task.get("status", "not_started"),
            group_name=task.get("group", task.get("group_name", "")),
            notes=task.get("notes", ""),
            ticket_id=ticket.get("id") or task.get("ticket_id"),
            ticket_url=ticket.get("url") or task.get("ticket_url"),
        )
        if task.get("dependencies"):
            _db.set_dependencies(project_id, task_id, task["dependencies"])
