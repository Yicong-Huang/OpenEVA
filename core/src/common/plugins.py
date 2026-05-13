"""Plugin framework: a tiny registry + discovery protocol so feature
modules attach themselves to the FastAPI app and the scheduler
without server.py knowing each one by name.

Open source vs extension split:
  - `core/src/plugins/`           top-level package -- open-source
                                  plugins shipped with the OSS install
  - `<extension>/src/<plugin>/`   extension-provided plugins
                                  (one folder per plugin)

`server.py` runs both kinds of discovery once at startup: the OSS
package via `discover("plugins")`, and each extension's plugin tree
via `discover_dir(<extension>/src)`. Either may be empty without
breaking the boot. Each plugin module, on import, calls
`register(plugin)` to attach itself.

A plugin needs:
  - `id: str`            -- unique short id (matches
                            `plugin.<id>.enabled` setting)
  - `name: str`          -- human-readable display name
  - `register(app)`      -- wire FastAPI routes / startup hooks (optional)
  - `start_jobs(sched)`  -- register scheduled jobs on APScheduler
                            (optional; called after scheduler is up)

All callbacks must be no-throw -- the framework swallows exceptions
so a broken plugin can't take the server down. Errors print to
stdout for visibility (the server log) without surfacing them to
the user-facing UI.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any


# Module-global registry. Plugins are appended in import order; the
# `_seen_ids` set keeps re-registration of the same id idempotent
# (matters for tests that monkey-import the package twice).
_registered: list[Any] = []
_seen_ids: set[str] = set()


def register(plugin: Any) -> None:
    """Add a plugin to the registry. Idempotent on `plugin.id`."""
    pid = getattr(plugin, "id", None) or plugin.__class__.__name__
    if pid in _seen_ids:
        return
    _seen_ids.add(pid)
    _registered.append(plugin)


def all_plugins() -> list[Any]:
    """Return a snapshot of registered plugins, in registration order."""
    return list(_registered)


def discover(*packages: str) -> int:
    """Import every submodule of each Python package so its top-level
    `register(...)` calls fire.

    Used for plugins shipped as a normal Python package -- e.g. the
    open-source `plugins/` namespace under `core/src/plugins/`. The
    sibling `discover_dir()` covers flat plugin trees that aren't
    laid out as packages (any `<extension>/src/`).

    Missing packages are silently skipped (an OSS install may have
    no plugins). Per-module import errors are logged but don't
    abort the rest of the discovery -- one broken plugin must not
    hide its peers.

    For already-imported modules we call `importlib.reload` so the
    top-level `register(...)` re-fires. Otherwise tests that call
    `reset_for_tests()` followed by `discover(...)` would see an
    empty registry: Python's import cache short-circuits the second
    import and the module's registration line never runs again.
    `register()` itself is idempotent on `id`, so reloading on a
    fresh server start (where reset wasn't called) is a no-op.

    Returns the number of submodules successfully imported.
    """
    import sys
    n = 0
    for pkg_name in packages:
        try:
            pkg = importlib.import_module(pkg_name)
        except ImportError:
            continue
        path = getattr(pkg, "__path__", None)
        if not path:
            continue
        for info in pkgutil.iter_modules(path):
            mod_name = f"{pkg_name}.{info.name}"
            try:
                if mod_name in sys.modules:
                    importlib.reload(sys.modules[mod_name])
                else:
                    importlib.import_module(mod_name)
                n += 1
            except Exception as e:
                print(f"[plugins] import {mod_name} failed: {e}",
                      flush=True)
    return n


def discover_dir(plugin_dir) -> int:
    """Discover plugins laid out as one folder per plugin.

    Each immediate subdirectory of `plugin_dir` is treated as a
    self-contained plugin:
        <plugin_dir>/<id>/
            plugin.py     # entry; runs `register(<Plugin>())` on import
            plugin.conf     # manifest + runtime config (see _seed_conf)

    The `plugin.py` file is loaded via
    `importlib.util.spec_from_file_location` so it works regardless of
    whether `plugin_dir` is on sys.path. Loading is the side-effect
    that registers the plugin -- nothing else here calls into the
    plugin module.

    `plugin.conf` is read first (if present) and its `enabled` flag +
    `settings` block are seeded into the settings DB under
    `plugin.<id>.<key>`. Seeding is idempotent (existing rows win);
    edits made through the in-app Settings UI survive a re-seed.

    Subfolders without a `plugin.py` are skipped silently. Hidden
    folders (`_xyz`, `.git`, etc.) are skipped too. A missing or
    non-directory `plugin_dir` is a no-op so an OSS-only install
    that ships without any extensions doesn't error.
    """
    import importlib.util
    import sys
    from pathlib import Path
    p = Path(plugin_dir)
    if not p.is_dir():
        return 0
    n = 0
    for sub in sorted(p.iterdir()):
        if not sub.is_dir() or sub.name.startswith(("_", ".")):
            continue
        plugin_conf = sub / "plugin.conf"
        plugin_py = sub / "plugin.py"
        # Both files are required:
        #   `plugin.conf` -- manifest (id, name, enabled, settings)
        #                    seeded into the DB so the Settings UI sees it.
        #   `plugin.py`   -- backend hook: registers a Plugin object on
        #                    import so `register_routes` and `start_jobs`
        #                    can dispatch to it. Even frontend-only widgets
        #                    declare a no-op class so the registry stays
        #                    the single source of truth for "what plugins
        #                    exist". Folders missing either file are
        #                    silently skipped (a stray dir, `__pycache__`,
        #                    etc. is not an error).
        if not plugin_conf.exists() or not plugin_py.exists():
            continue
        # Seed plugin.conf BEFORE importing plugin.py so the plugin's
        # top-level code can read its own settings via the standard
        # `settings.get_value("plugin.<id>.<key>")` path.
        try:
            _seed_conf(plugin_conf)
        except Exception as e:
            print(f"[plugins] seed {plugin_conf} failed: {e}",
                  flush=True)
        # Internal name guarantees uniqueness across plugin trees.
        # Friendly alias = the folder name (e.g. `<plugin>`) so tests
        # and other code can `import <plugin>` after discovery and get the
        # loaded plugin module without juggling spec_from_file_location
        # themselves.
        #
        # Re-discovery preserves module identity: if the plugin was
        # already loaded (conftest bootstrap, prior discover call),
        # we `importlib.reload` it in place instead of creating a new
        # module object. Otherwise tests that captured a reference
        # via `import boba as b` at collection time would end up with
        # `b` pointing at module instance A while a later
        # `discover_dir` made `sys.modules["boba"]` point at instance
        # B -- patching `boba._in_poll_window` would then patch B's
        # global without affecting calls dispatched through A.
        unique_name = f"_eva_plugin_{sub.name}"
        try:
            existing = sys.modules.get(unique_name)
            spec = (getattr(existing, "__spec__", None)
                    if existing is not None else None)
            if spec is None or spec.loader is None:
                spec = importlib.util.spec_from_file_location(unique_name, plugin_py)
            if spec is None or spec.loader is None:
                continue
            if existing is not None:
                # Re-execute the spec on the existing module object so
                # top-level `register()` runs again WITHOUT swapping the
                # module identity. `importlib.reload(existing)` would
                # raise "spec not found" on modules built via
                # spec_from_file_location, so we drive `exec_module`
                # directly.
                mod = existing
            else:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[unique_name] = mod
                # Friendly alias only on first load -- after that the
                # alias already points at the same module instance.
                alias_existing = sys.modules.get(sub.name)
                if (alias_existing is None
                        or getattr(alias_existing, "__file__", "") == str(plugin_py)):
                    sys.modules[sub.name] = mod
            spec.loader.exec_module(mod)
            n += 1
        except Exception as e:
            print(f"[plugins] load {plugin_py} failed: {e}", flush=True)
    return n


def _seed_conf(conf_path) -> None:
    """Read a plugin's `plugin.conf` and copy `enabled` + every
    `settings.*` value into the settings DB.

    Schema:
        id: <str>            # plugin identifier
        name: <str>          # display name (informational)
        enabled: <bool>      # default; seeded as `plugin.<id>.enabled`
        settings:            # optional map; each k seeded as
          <key>: <value>     #   `plugin.<id>.<key>`

    Seeding is idempotent: a setting that already exists in the DB
    is NOT overwritten, so manual edits via the Settings UI win.
    Plugins without a `plugin.conf` skip seeding entirely.
    """
    import yaml
    import app_state
    data = yaml.safe_load(conf_path.read_text()) or {}
    plugin_id = data.get("id")
    if not plugin_id:
        return
    db = app_state._db
    if db is None:
        return

    def _seed(key: str, value):
        if value is None:
            return
        existing = db.get_setting(key, default=_SENTINEL)
        if existing is _SENTINEL:
            db.set_setting(key, value)

    enabled = data.get("enabled")
    if enabled is not None:
        _seed(f"plugin.{plugin_id}.enabled", bool(enabled))
    for k, v in (data.get("settings") or {}).items():
        _seed(f"plugin.{plugin_id}.{k}", v)


_SENTINEL = object()


def register_routes(app: Any) -> None:
    """Phase 1: have every registered plugin mount its FastAPI routes.

    MUST run at module-import time of `server.py`, BEFORE the
    catch-all `/{path:path}` route in `routes/static.py` is loaded.
    FastAPI matches routes in registration order: any API route
    registered AFTER the catch-all gets shadowed (returns 404 from
    the catch-all's "unknown api" branch).

    Idempotent: safe to call again; FastAPI deduplicates identical
    `(method, path)` registrations only as far as the route table
    sees them, so a real second call would still compound. But
    `_isolate_plugin_registry` in tests + the registry's `id`-dedup
    keep the production path single-call.
    """
    for p in _registered:
        if not hasattr(p, "register"):
            continue
        pid = getattr(p, "id", p.__class__.__name__)
        try:
            p.register(app)
        except Exception as e:
            print(f"[plugins] {pid}.register() failed: {e}", flush=True)


def start_all_jobs(scheduler: Any) -> None:
    """Phase 2: have every registered plugin attach its scheduled
    work to the live APScheduler.

    Runs in the FastAPI lifespan startup hook, AFTER `start_scheduler()`
    -- plugins call `scheduler.add_job(...)` from this hook, so the
    scheduler must already be alive. Test runs (which set
    `EVA_DISABLE_SCHEDULER=1`) skip the lifespan, so this never
    fires there.
    """
    for p in _registered:
        if not hasattr(p, "start_jobs"):
            continue
        pid = getattr(p, "id", p.__class__.__name__)
        try:
            p.start_jobs(scheduler)
        except Exception as e:
            print(f"[plugins] {pid}.start_jobs() failed: {e}",
                  flush=True)


def initialize(app: Any, scheduler: Any) -> None:
    """Convenience: run both phases back to back. Use only when the
    caller doesn't have a route-shadowing concern (e.g. integration
    tests that don't mount the static catch-all)."""
    register_routes(app)
    start_all_jobs(scheduler)


def reset_for_tests() -> None:
    """Clear the registry. Used by per-test fixtures so a test that
    triggers plugin import doesn't leak the registration into
    subsequent tests."""
    _registered.clear()
    _seen_ids.clear()
