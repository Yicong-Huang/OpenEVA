"""Channels: registry + namespace for message-source integrations.

A "channel" is a message source/sink (Slack, Discord, Mattermost, ...)
that polls an external system and emits events into Eva's bus. Each
impl ships its own subpackage: `channels/<id>/channel.py` registers a
Channel object on import; `channel.conf` carries metadata + default
settings seeded under `channel.<id>.<key>`.

The package's `__init__` doubles as the registry: callers
`import channels` and use `channels.register(...)` / `channels.all_channels()`
/ etc. Sub-packages (`channels.slack`, future `channels.discord`)
trigger their own registration on import via `discover_package` /
`discover_dir`.

Default registry is empty -- an OSS install ships with no channels
preconfigured. Each channel impl manages its own credentials,
watched-entity list, and scheduled poll job.

A channel needs:
  - `id: str`            -- unique short id (matches
                            `channel.<id>.enabled` setting)
  - `label: str`         -- human-readable display name
  - `register(app)`      -- wire FastAPI routes (optional)
  - `start_jobs(sched)`  -- attach scheduled polls to APScheduler
                            (optional; called after scheduler is up)
  - `is_ready()`         -- `(bool, str)`: ready + 1-line detail.
                            Drives the Settings -> Setup tab so
                            misconfigured channels surface a hint.
  - `get_status()`       -- dict shown by `/api/channels/<id>`;
                            schema is channel-specific.

All callbacks must be no-throw -- the framework swallows exceptions so
a broken channel can't take the server down.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any


_registered: list[Any] = []
_seen_ids: set[str] = set()


def register(channel: Any) -> None:
    """Add a channel to the registry. Idempotent on `channel.id`."""
    cid = getattr(channel, "id", None) or channel.__class__.__name__
    if cid in _seen_ids:
        return
    _seen_ids.add(cid)
    _registered.append(channel)


def all_channels() -> list[Any]:
    """Snapshot of registered channels, in registration order."""
    return list(_registered)


def get_channel(channel_id: str) -> Any | None:
    """Look up a channel by id, or None when not registered."""
    for ch in _registered:
        if getattr(ch, "id", None) == channel_id:
            return ch
    return None


def discover_package(package_name: str) -> int:
    """Discover OSS channels laid out as a regular Python package
    (e.g. `channels.slack` with `__init__.py`). Each immediate
    subpackage gets imported (triggering its `register(...)`) and
    its `channel.conf` (if present) is seeded into the settings DB.

    Returns count of channels successfully imported.
    """
    try:
        pkg = importlib.import_module(package_name)
    except ImportError:
        return 0
    pkg_path = getattr(pkg, "__path__", None)
    if not pkg_path:
        return 0
    import pkgutil
    n = 0
    for info in pkgutil.iter_modules(pkg_path):
        if not info.ispkg:
            continue
        sub_dir = Path(pkg_path[0]) / info.name
        conf = sub_dir / "channel.conf"
        if conf.exists():
            try:
                seed_conf(conf)
            except Exception as e:  # noqa: BLE001
                print(f"[channels] seed {conf} failed: {e}", flush=True)
        try:
            importlib.import_module(f"{package_name}.{info.name}")
            n += 1
        except Exception as e:  # noqa: BLE001
            print(f"[channels] import {package_name}.{info.name} failed: {e}",
                  flush=True)
    return n


def discover_dir(channel_dir) -> int:
    """Discover channels laid out one folder per channel (used for
    extension-tree channels that aren't on the standard Python path):
        <channel_dir>/<id>/
            channel.py     # entry; runs `register(<Channel>())` on import
            channel.conf   # manifest + default settings (see seed_conf)

    Loading `channel.py` is the side-effect that registers the channel.
    Folders missing either file are skipped silently. Hidden folders
    (`_x`, `.git`) are skipped too. Returns count of channels loaded.
    """
    p = Path(channel_dir)
    if not p.is_dir():
        return 0
    n = 0
    for sub in sorted(p.iterdir()):
        if not sub.is_dir() or sub.name.startswith(("_", ".")):
            continue
        channel_conf = sub / "channel.conf"
        channel_py = sub / "channel.py"
        if not channel_conf.exists() or not channel_py.exists():
            continue
        try:
            seed_conf(channel_conf)
        except Exception as e:  # noqa: BLE001
            print(f"[channels] seed {channel_conf} failed: {e}", flush=True)
        unique_name = f"_eva_channel_{sub.name}"
        try:
            existing = sys.modules.get(unique_name)
            spec = (getattr(existing, "__spec__", None)
                    if existing is not None else None)
            if spec is None or spec.loader is None:
                spec = importlib.util.spec_from_file_location(unique_name, channel_py)
            if spec is None or spec.loader is None:
                continue
            if existing is not None:
                mod = existing
            else:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[unique_name] = mod
                alias = sys.modules.get(sub.name)
                if (alias is None
                        or getattr(alias, "__file__", "") == str(channel_py)):
                    sys.modules[sub.name] = mod
            spec.loader.exec_module(mod)
            n += 1
        except Exception as e:  # noqa: BLE001
            print(f"[channels] load {channel_py} failed: {e}", flush=True)
    return n


def seed_conf(conf_path) -> None:
    """Read a channel's `channel.conf` (YAML) and copy `enabled` + every
    `settings.*` value into the settings DB under `channel.<id>.<key>`.

    Existing rows win, so user edits via the Settings UI survive a
    re-seed at boot. Missing/invalid file is a no-op."""
    try:
        import yaml
    except ImportError:
        return
    try:
        data = yaml.safe_load(conf_path.read_text()) or {}
    except Exception:  # noqa: BLE001
        return
    cid = data.get("id")
    if not cid or not isinstance(cid, str):
        return
    import app_state
    db = app_state._db

    def _seed(key: str, value: Any) -> None:
        if value is None:
            return
        existing = db.get_setting(key, default=_SENTINEL)
        if existing is _SENTINEL:
            db.set_setting(key, value)

    enabled = data.get("enabled")
    if enabled is not None:
        _seed(f"channel.{cid}.enabled", bool(enabled))
    for k, v in (data.get("settings") or {}).items():
        _seed(f"channel.{cid}.{k}", v)


_SENTINEL = object()


def register_routes(app: Any) -> None:
    """Phase 1: each channel mounts its HTTP routes (if any)."""
    for ch in _registered:
        if not hasattr(ch, "register"):
            continue
        cid = getattr(ch, "id", ch.__class__.__name__)
        try:
            ch.register(app)
        except Exception as e:  # noqa: BLE001
            print(f"[channels] {cid}.register() failed: {e}", flush=True)


def start_all_jobs(scheduler: Any) -> None:
    """Phase 2: each channel attaches scheduled work to APScheduler."""
    for ch in _registered:
        if not hasattr(ch, "start_jobs"):
            continue
        cid = getattr(ch, "id", ch.__class__.__name__)
        try:
            ch.start_jobs(scheduler)
        except Exception as e:  # noqa: BLE001
            print(f"[channels] {cid}.start_jobs() failed: {e}", flush=True)


def reset_for_tests() -> None:
    """Clear the registry. Per-test fixtures use this so registrations
    don't leak across tests."""
    _registered.clear()
    _seen_ids.clear()
