"""Agent abstraction layer.

Eva runs work via a CLI "agent" -- a process that takes a tmux pane
+ system prompt + optional first user message and runs as an
interactive session, plus utility entry points for one-shot usage
queries and non-interactive analysis. The vendor of that CLI is
pluggable: open-source ships with `claude` (Anthropic's Claude
Code), and extension installs can register alternative agents
(internal wrappers, vendor-specific variants) by shipping an
agent module under `<extension>/src/agents/`.

Agents are NOT plugins -- they're infrastructure. Plugins are
user-facing widgets with UI; agents are the underlying CLI Eva
shells out to. They share neither a registry nor a discovery
mechanism. This module owns the registry; agent modules
self-register on import (see `core/src/agents/claude.py`).

Switching between agents is a single setting:
`service.agent.impl = <agent-id>`. Callers in core/ never see the
underlying CLI name -- they go through `get_active_agent()` and
only call protocol methods.
"""

from __future__ import annotations

import importlib
import json as _json
import re as _re
import subprocess
from typing import Protocol, runtime_checkable

from . import settings as _settings


# Setting key driving the active-agent selection. Falls back to
# `_DEFAULT_AGENT_ID` when unset, so an OSS install with only the
# `claude` agent registered Just Works without a settings row.
KEY_AGENT_IMPL = "service.agent.impl"
_DEFAULT_AGENT_ID = "claude"


@runtime_checkable
class Agent(Protocol):
    """Minimum surface every agent implementation provides.

    Concrete classes (typically subclasses of `CliAgentBase`) call
    `register_agent(self)` at module import time so the discover
    helper in this module just has to import the agent file -- no
    central wiring file to keep in sync.
    """

    id: str
    name: str

    def launch_argv(self, name: str, *,
                    system_prompt: str | None = None,
                    prompt: str | None = None) -> list[str]: ...

    def resume_argv(self, session_id: str) -> list[str]: ...

    def fetch_usage(self, days: int = 1) -> dict | None: ...

    def analyze(self, prompt: str, *, model: str = "haiku") -> dict | None: ...


# ---------------------------------------------------------------
# Registry
# ---------------------------------------------------------------

_registered: list = []
_seen_ids: set[str] = set()


def register_agent(agent) -> None:
    """Register an agent implementation. Idempotent on `agent.id`.

    Each agent module calls this once at the bottom of its file so a
    plain `import` triggers registration. Re-imports during tests
    are harmless thanks to the id-dedup guard."""
    aid = getattr(agent, "id", "") or agent.__class__.__name__
    if aid in _seen_ids:
        return
    _seen_ids.add(aid)
    _registered.append(agent)


def all_agents() -> list[Agent]:
    """Snapshot of registered agents in registration order. Used by
    the Settings UI to populate the impl-picker."""
    return list(_registered)


def get_agent(agent_id: str) -> Agent | None:
    """Return the registered agent matching `agent_id`, or None."""
    for a in _registered:
        if getattr(a, "id", "") == agent_id:
            return a  # type: ignore[return-value]
    return None


def get_active_agent() -> Agent:
    """Return the currently-active agent.

    Resolution order:
      1. `service.agent.impl` setting matches a registered agent
      2. `_DEFAULT_AGENT_ID` (`claude`) is registered
      3. The first registered agent (so an install whose only
         registered agent comes from an extension still works
         without an explicit setting)

    Raises RuntimeError when no agent has registered at all -- Eva
    can't launch sessions without one, so we surface that loudly.
    """
    chosen = _settings.get_value(KEY_AGENT_IMPL, default=_DEFAULT_AGENT_ID)
    explicit = get_agent(chosen) if chosen else None
    if explicit is not None:
        return explicit
    fallback = get_agent(_DEFAULT_AGENT_ID)
    if fallback is not None:
        return fallback
    if _registered:
        return _registered[0]
    raise RuntimeError(
        "No agent registered. Eva needs at least one Agent module "
        "to be importable -- the OSS install ships "
        "`core/src/agents/claude.py`; `discover_agents()` should "
        "have imported it on boot. Check that "
        "`core/src/agents/` is on sys.path."
    )


def reset_for_tests() -> None:
    """Clear the agent registry. Used by per-test fixtures so a test
    that triggers agent import doesn't leak the registration into
    subsequent tests."""
    _registered.clear()
    _seen_ids.clear()


# ---------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------

def discover_agents(*directories) -> int:
    """Load every `*.py` file directly under each directory so its
    `register_agent(...)` call fires.

    Used by `server.py` to scan both `core/src/agents/` (OSS) and
    every `<extension>/src/agents/` (extensions). The directories
    share the same name on disk but we deliberately don't use
    Python's package machinery -- both `core/src/` and each
    extension's `src/` are on sys.path, and a package named `agents`
    in either would shadow the other. Loading by filesystem path
    side-steps the conflict.

    Files starting with `_` are skipped (treated as helpers). A
    missing or non-directory path is a no-op so an OSS-only checkout
    without any extension namespaces doesn't error.

    Already-loaded modules are re-executed (driven through the
    spec's loader) so test runs that called `reset_for_tests()` see
    registrations re-fire. `register_agent` is idempotent on id, so
    re-loading on a fresh server start is a no-op.

    Returns the number of modules successfully imported.
    """
    import importlib.util
    import sys
    from pathlib import Path
    n = 0
    for d in directories:
        p = Path(d)
        if not p.is_dir():
            continue
        for py_path in sorted(p.glob("*.py")):
            if py_path.name.startswith("_"):
                continue
            unique_name = f"_eva_agent_{p.parent.name}_{py_path.stem}"
            try:
                existing = sys.modules.get(unique_name)
                spec = (getattr(existing, "__spec__", None)
                        if existing is not None else None)
                if spec is None or spec.loader is None:
                    spec = importlib.util.spec_from_file_location(
                        unique_name, py_path,
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
                print(f"[agent] load {py_path} failed: {e}", flush=True)
    return n


# ---------------------------------------------------------------
# Reusable base for CLI agents (Claude Code family)
# ---------------------------------------------------------------

_DEFAULT_USAGE_TIMEOUT = 15
_DEFAULT_ANALYZE_TIMEOUT = 60
_DEFAULT_MODEL = "haiku"

# Captures the numeric amount after "Daily:" / "Weekly:" / "Monthly:" in
# the agent's text-mode usage report. The optional `$` + thousands-
# separator comma are both tolerated -- some CLI variants prefix `$`,
# others don't; the parser handles both shapes.
_USAGE_LINE_RE = {
    "daily": _re.compile(r"Daily:\s*\$?([\d,.]+)"),
    "weekly": _re.compile(r"Weekly:\s*\$?([\d,.]+)"),
    "monthly": _re.compile(r"Monthly:\s*\$?([\d,.]+)"),
}


def _parse_usage(text: str) -> dict:
    """Extract daily/weekly/monthly/tier values from the agent's
    text-mode usage report. Unknown fields stay None so callers can
    treat 'agent ran but had nothing to report' (early in the day,
    new account) the same as 'agent crashed'."""
    data = {"daily": None, "weekly": None, "monthly": None, "tier": None}
    for line in text.split("\n"):
        line = line.strip()
        for key, pat in _USAGE_LINE_RE.items():
            m = pat.match(line)
            if m:
                data[key] = m.group(1)
                break
        if "Power User" in line:
            data["tier"] = "Power User"
        elif "Standard" in line:
            data["tier"] = "Standard"
    return data


def _extract_json(text: str) -> dict | None:
    """Pull a JSON object out of `text`. Haiku sometimes prefixes the
    JSON with ```json or trails commentary -- find the first `{` and
    last `}` and parse the slice. Returns None when nothing parses.
    """
    if not isinstance(text, str):
        return None
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        return _json.loads(text[start:end])
    except (ValueError, _json.JSONDecodeError):
        return None


class CliAgentBase:
    """Shared logic for agents that wrap a single Claude-Code-family
    binary (`claude` plus any vendor-specific variants). Subclasses
    set the class-level `binary`, `id`, `name`; everything else is
    inherited.

    Why a base class instead of a single concrete class:
      - keeps each impl module small (just metadata + the binary
        name) so they read as data, not code;
      - lets a future divergent impl (e.g. a fully-different agent
        family) override one method without disturbing the shared
        argv conventions.
    """

    # Subclasses MUST set these three. The framework's
    # `register_agent(...)` reads `id` / `name`.
    binary: str = ""
    id: str = ""
    name: str = ""

    # ---- Session lifecycle ----

    def launch_argv(self, name: str, *,
                    system_prompt: str | None = None,
                    prompt: str | None = None) -> list[str]:
        """Build argv for `<binary> -n NAME [--append-system-prompt P] [PROMPT]`.

        Matches Claude Code's CLI shape; vendor variants mirror it.
        The positional prompt at the end uses Claude's "initial user
        message" handling, which side-steps the input-box autocomplete
        race that previously corrupted dash-bearing slash commands."""
        argv = [self.binary, "-n", name]
        if system_prompt:
            argv += ["--append-system-prompt", system_prompt]
        if prompt:
            argv.append(prompt)
        return argv

    def resume_argv(self, session_id: str) -> list[str]:
        """argv for `<binary> resume UUID`."""
        return [self.binary, "resume", session_id]

    # ---- Telemetry ----

    def fetch_usage(self, days: int = 1,
                    timeout: int = _DEFAULT_USAGE_TIMEOUT) -> dict | None:
        """Run `<binary> usage --days N` and parse the text output."""
        try:
            result = subprocess.run(
                [self.binary, "usage", "--days", str(days)],
                capture_output=True, text=True, timeout=timeout,
            )
        except Exception:
            return None
        return _parse_usage(result.stdout + result.stderr)

    # ---- One-shot analysis ----

    def analyze(self, prompt: str, *, model: str = _DEFAULT_MODEL,
                timeout: int = _DEFAULT_ANALYZE_TIMEOUT,
                allow_tools: bool = False) -> dict | None:
        """Run `<binary> -p` non-interactively; return parsed JSON envelope.

        Returns None on subprocess failure, non-zero exit, invalid
        JSON, or a missing object in the result text -- callers treat
        'AI unavailable' as a normal state."""
        cmd = [self.binary, "-p", "--output-format", "json", "--model", model]
        if not allow_tools:
            cmd += ["--tools", ""]
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
            )
            stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            return None
        except Exception:
            return None
        if proc.returncode != 0:
            return None

        try:
            envelope = _json.loads(stdout)
        except (ValueError, _json.JSONDecodeError):
            return _extract_json(stdout)
        inner = envelope.get("result", stdout) if isinstance(envelope, dict) else stdout
        if isinstance(inner, dict):
            return inner
        return _extract_json(inner if isinstance(inner, str) else stdout)


# ---------------------------------------------------------------
# Convenience top-level callables -- saves callers the hop through
# `get_active_agent()` for the common entry points.
# ---------------------------------------------------------------

def launch_argv(name: str, *, system_prompt: str | None = None,
                prompt: str | None = None) -> list[str]:
    return get_active_agent().launch_argv(
        name, system_prompt=system_prompt, prompt=prompt,
    )


def resume_argv(session_id: str) -> list[str]:
    return get_active_agent().resume_argv(session_id)


def fetch_usage(days: int = 1) -> dict | None:
    return get_active_agent().fetch_usage(days)


def analyze(prompt: str, *, model: str = "haiku",
            timeout: int = _DEFAULT_ANALYZE_TIMEOUT,
            allow_tools: bool = False) -> dict | None:
    return get_active_agent().analyze(
        prompt, model=model, timeout=timeout, allow_tools=allow_tools,
    )
