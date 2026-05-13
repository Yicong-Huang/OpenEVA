"""Extension discovery.

Eva ships with a `core/` base (the OSS install) and an open-ended
set of extension namespaces that live as sibling folders at the
repo root. An extension is anything containing an `extension.conf`
marker file:

    eva/
    ├── core/                       # OSS base (no marker)
    ├── acme/                       # has extension.conf -> recognized
    │   ├── extension.conf
    │   ├── src/                    # added to sys.path
    │   │   ├── agents/             # agent implementations
    │   │   └── <plugin_id>/        # plugin folders (plugin.conf + plugin.py)
    │   └── test/                   # pytest scans this
    └── widgets/                    # hypothetical second extension
        └── extension.conf

The scanner here is the single source of truth for "what extensions
exist on disk." `server.py` and `conftest.py` call `discover()` once
at startup and feed the resulting paths to the plugin / agent
registries. Adding, renaming, or removing an extension folder is a
no-op for the framework -- the scanner just yields a different set.

`extension.conf` shape (yaml):
    id: acme                    # short identifier (alphanumeric + dash)
    name: Acme                  # display name (informational)
    description: ...            # optional one-liner
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


# Marker filename. Bumping this is a breaking change for every
# extension on disk, so think twice before renaming.
_MARKER_FILENAME = "extension.conf"


@dataclass(frozen=True)
class Extension:
    """One discovered extension namespace.

    `id`             extension identifier from `extension.conf`
    `name`           display name (falls back to `id` when absent)
    `root`           the umbrella folder containing `extension.conf`
                     (e.g. `<repo>/<ext>/`)
    `src`            `<root>/src/`; expected to be added to sys.path
                     by the caller
    `test`           `<root>/test/`; pytest scans this when present
    `agents_dir`     `<root>/src/agents/`; agent modules live here
    `seed_file`      `<root>/src/seed.py`; optional one-shot script
                     the framework imports once after agents/plugins
                     are wired so the extension can INSERT extra
                     action_definitions / settings / etc. that
                     don't fit the per-plugin `plugin.conf` shape
    """

    id: str
    name: str
    root: Path
    src: Path
    test: Path
    agents_dir: Path
    seed_file: Path


def _read_marker(marker: Path) -> dict:
    """Parse the marker file. Empty or unreadable markers degrade
    to {} so a typo-free-but-empty file still gets recognized as
    an extension (we just won't have a friendly name)."""
    try:
        import yaml
        data = yaml.safe_load(marker.read_text()) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def discover(repo_root: Path | str) -> Iterator[Extension]:
    """Yield every extension found as a direct child of `repo_root`.

    The scan is shallow on purpose: extensions are top-level
    namespaces, not nested inside each other. `core/` is excluded
    by convention even if it ever grows a marker (it's the OSS
    base, not an extension).

    Yields in sorted order so server boot logs and per-test
    discovery are deterministic.
    """
    root = Path(repo_root)
    if not root.is_dir():
        return
    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name.startswith((".", "_")):
            continue
        # Reserved name: `core/` is never an extension.
        if sub.name == "core":
            continue
        marker = sub / _MARKER_FILENAME
        if not marker.is_file():
            continue
        data = _read_marker(marker)
        ext_id = (data.get("id") or sub.name).strip()
        ext_name = (data.get("name") or ext_id).strip()
        yield Extension(
            id=ext_id,
            name=ext_name,
            root=sub,
            src=sub / "src",
            test=sub / "test",
            agents_dir=sub / "src" / "agents",
            seed_file=sub / "src" / "seed.py",
        )


def all_extensions(repo_root: Path | str) -> list[Extension]:
    """Materialised list for callers that need to iterate twice or
    expose the result to the Settings UI."""
    return list(discover(repo_root))


def run_seed_files(extensions: list[Extension]) -> int:
    """Import each extension's `src/seed.py` if present.

    Seed files run once at bootstrap to insert extension-specific
    rows (action definitions, default settings, anything that
    doesn't fit the per-plugin `plugin.conf` schema). All inserts
    inside the seed should be idempotent (e.g. `INSERT OR IGNORE`)
    because seed files run on every server boot.

    Loaded via `importlib.util.spec_from_file_location` -- same
    pattern as the plugin / agent / cert discoverers -- so seed
    modules don't need to live in any Python package and naming
    collisions across extensions are impossible (each is loaded
    under a unique internal name).

    Returns the number of seed files successfully run.
    """
    import importlib.util
    import sys
    n = 0
    for ext in extensions:
        if not ext.seed_file.is_file():
            continue
        unique_name = f"_eva_seed_{ext.id}"
        try:
            existing = sys.modules.get(unique_name)
            spec = (getattr(existing, "__spec__", None)
                    if existing is not None else None)
            if spec is None or spec.loader is None:
                spec = importlib.util.spec_from_file_location(
                    unique_name, ext.seed_file,
                )
            if spec is None or spec.loader is None:
                continue
            if existing is not None:
                mod = existing
            else:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[unique_name] = mod
            spec.loader.exec_module(mod)
            n += 1
        except Exception as e:
            print(f"[extension-seed] {ext.seed_file} failed: {e}",
                  flush=True)
    return n
