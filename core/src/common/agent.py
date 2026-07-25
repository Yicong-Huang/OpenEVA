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

# Setting key + default driving which agent launches NEW sessions.
# Separate from `KEY_AGENT_IMPL` on purpose: resume routing reads the
# per-session agent (see `get_agent_by_id`), so changing the new-session
# agent must not affect how existing sessions resume. Defaults to the
# same agent as `KEY_AGENT_IMPL` (`claude`) when unset; deployments that
# register additional agents point this at one of them via the setting.
KEY_NEW_SESSION_AGENT_IMPL = "service.agent.new_session_impl"
_DEFAULT_NEW_SESSION_AGENT_ID = _DEFAULT_AGENT_ID

# Agent used to resume a session whose `agent_impl` is empty. Such rows
# predate per-session agent binding; resuming them with the default
# agent is the safe fallback. Falls through to `get_active_agent()` if
# that agent isn't registered.
_LEGACY_RESUME_AGENT_ID = _DEFAULT_AGENT_ID


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
    # See CliAgentBase.system_prompt_via_launch. Callers read this to
    # decide whether the session's background goes into the launch argv
    # (True) or gets folded into the first delivered prompt (False).
    system_prompt_via_launch: bool

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


def get_agent_for_new_session() -> Agent:
    """Return the agent that should launch a NEW session.

    Resolution order:
      1. `service.agent.new_session_impl` setting matches a registered
         agent (default: the active agent)
      2. Otherwise fall back to `get_active_agent()` -- an install
         without an explicit new-session agent registered still launches
         sessions with its active agent.

    Callers persist the returned agent's `id` onto the session row so
    `get_agent_by_id` can resume it with the same implementation."""
    chosen = _settings.get_value(
        KEY_NEW_SESSION_AGENT_IMPL, default=_DEFAULT_NEW_SESSION_AGENT_ID,
    )
    explicit = get_agent(chosen) if chosen else None
    if explicit is not None:
        return explicit
    return get_active_agent()


def get_agent_by_id(agent_impl: str) -> Agent:
    """Return the agent that a session recorded in its `agent_impl`.

    Used by resume paths so a session is resumed with the same agent
    that launched it -- codex session ids and Claude transcript UUIDs
    are not interchangeable, so routing off the per-session record (not
    the global active agent) is required for correctness.

    An empty `agent_impl` marks a legacy row from before per-session
    binding existed; we resume those with the default agent. An unknown
    id falls back the same way. Both degrade to `get_active_agent()` if
    the default agent isn't registered."""
    if agent_impl:
        found = get_agent(agent_impl)
        if found is not None:
            return found
    legacy = get_agent(_LEGACY_RESUME_AGENT_ID)
    if legacy is not None:
        return legacy
    return get_active_agent()


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
_DEFAULT_SESSION_ENV = {
    "COLORTERM": "truecolor",
    "FORCE_COLOR": "1",
    "TERM": "xterm-256color",
}

# Captures the numeric amount after "Daily:" / "Weekly:" / "Monthly:" in
# the agent's text-mode usage report. The optional `$` + thousands-
# separator comma are both tolerated -- some CLI variants prefix `$`,
# others don't; the parser handles both shapes.
_USAGE_LINE_RE = {
    "daily": _re.compile(r"Daily:\s*\$?([\d,.]+)"),
    "weekly": _re.compile(r"Weekly:\s*\$?([\d,.]+)"),
    "monthly": _re.compile(r"Monthly:\s*\$?([\d,.]+)"),
}

# Newer usage output groups spend under per-tool sections
# ("Codex Usage Summary ...", "Claude Code Usage Summary ..."), each
# carrying a `Cost (USD): $N` and `Total tokens: N` line. We track the
# active section so the two identically-labelled lines land in the
# right bucket. `$` and thousands commas are stripped by the caller.
_USAGE_COST_RE = _re.compile(r"Cost \(USD\):\s*\$?([\d,.]+)")
_USAGE_TOKENS_RE = _re.compile(r"Total tokens:\s*([\d,]+)")

# Top-of-report "AI Gateway Budgets" block reports the account-wide
# monthly spend against the budget cap, e.g.
#   "You have spent $7,459 out of your $15,000 monthly budget."
# This is the authoritative account total (all tools + billing), which
# is what the usage/quota dashboard shows -- distinct from the Claude Code
# section's `Monthly:` line (that tool's slice only).
_USAGE_GATEWAY_RE = _re.compile(
    r"spent\s*\$?([\d,.]+)\s*out of\s*(?:your\s*)?\$?([\d,.]+)\s*monthly budget"
)


def _parse_usage(text: str) -> dict:
    """Extract usage values from the agent's text-mode usage report.

    Returns the long-standing Anthropic rate-limit fields
    (daily/weekly/monthly, tier) plus, when present in newer output,
    the real period spend and token totals per tool
    (claude_cost/claude_tokens, codex_cost/codex_tokens). Unknown
    fields stay None so callers can treat 'agent ran but had nothing
    to report' (early in the day, new account) the same as 'agent
    crashed'."""
    data = {
        "daily": None, "weekly": None, "monthly": None, "tier": None,
        "claude_cost": None, "claude_tokens": None,
        "codex_cost": None, "codex_tokens": None,
        # Account-wide monthly total from the AI Gateway Budgets block
        # (all tools + billing), plus the budget cap. `monthly` mirrors
        # `monthly_total` when the gateway block is present so the
        # headline number matches the quota dashboard; `claude_monthly` keeps the
        # Claude-Code-only slice for the expandable breakdown.
        "monthly_total": None, "monthly_budget": None, "claude_monthly": None,
    }
    # Which per-tool section the current line belongs to. Section
    # headers flip this; `Cost (USD)` / `Total tokens` lines read it.
    section = None  # "claude" | "codex" | None
    for line in text.split("\n"):
        line = line.strip()
        if "Claude Code Usage Summary" in line:
            section = "claude"
        elif "Codex Usage Summary" in line:
            section = "codex"

        # Account-wide gateway total takes priority for the headline
        # `monthly` value (matches the quota dashboard + the usage header).
        mg = _USAGE_GATEWAY_RE.search(line)
        if mg:
            data["monthly_total"] = mg.group(1)
            data["monthly_budget"] = mg.group(2)

        for key, pat in _USAGE_LINE_RE.items():
            m = pat.match(line)
            if m:
                # A `Monthly:` line inside the Claude Code section is
                # that tool's slice -- keep it as the breakdown value,
                # never let it clobber the account-wide headline.
                if key == "monthly":
                    data["claude_monthly"] = m.group(1)
                    if data["monthly_total"] is None:
                        data["monthly"] = m.group(1)
                else:
                    data[key] = m.group(1)
                break

        if section:
            mc = _USAGE_COST_RE.match(line)
            if mc:
                data[f"{section}_cost"] = mc.group(1)
            mt = _USAGE_TOKENS_RE.match(line)
            if mt:
                data[f"{section}_tokens"] = mt.group(1)

        if "Power User" in line:
            data["tier"] = "Power User"
        elif "Standard" in line:
            data["tier"] = "Standard"

    # When the gateway block was present, the headline `monthly` is the
    # account-wide total so Eva matches what the user sees at the top of
    # the usage report / the quota dashboard.
    if data["monthly_total"] is not None:
        data["monthly"] = data["monthly_total"]
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


_SESSION_UUID_RE = _re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _looks_like_session_uuid(session_id: str) -> bool:
    """True for a canonical Claude transcript UUID (8-4-4-4-12 hex).

    Used to route `resume_argv`: a UUID means a local on-disk
    transcript (`--resume`); anything else (a numeric cloud id or a
    name/search term) goes through the cloud `resume` subcommand."""
    return bool(_SESSION_UUID_RE.match((session_id or "").strip()))


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

    # Extra environment variables to set on every launched/resumed
    # session process. Subclasses set this to inject vendor-specific
    # env; it is merged over `_DEFAULT_SESSION_ENV`, which keeps
    # terminal color enabled for CLIs whose color detection happens at
    # process launch inside tmux. Rendered as a leading
    # `env K=V ...` prefix on the argv so it applies uniformly to both
    # fresh launches and resumes, via the same tmux
    # `launch_session_argv` path, without threading env through every
    # caller. Requires the launcher to run argv[0] via execvp (tmux
    # does), where `env` is a real binary.
    session_env: dict[str, str] = {}

    # Whether the agent accepts a system prompt at launch time (via a
    # real system-prompt channel, e.g. Claude's `--append-system-prompt`)
    # that persists WITHOUT starting a conversation turn.
    #
    # True (Claude family): callers pass `system_prompt=` to
    # `launch_argv` and it lands in the agent's system context; the
    # action prompt is delivered separately afterwards.
    #
    # False (e.g. Codex): the agent has no such channel, so callers must
    # instead prepend the context to the FIRST user message. Callers
    # check this flag to decide whether to fold `bg_system` into the
    # delivered prompt rather than into the launch argv.
    system_prompt_via_launch: bool = True

    # ---- Session lifecycle ----

    def _env_prefix(self) -> list[str]:
        """`["env", "K=V", ...]` for the session process environment.

        Prepended to launch/resume argv so the child process inherits
        the shared terminal env plus any vendor-specific env. Keys are
        emitted in sorted order for deterministic argv (stable tests,
        stable pane_start_command)."""
        env = {**_DEFAULT_SESSION_ENV, **self.session_env}
        return ["env"] + [f"{k}={env[k]}" for k in sorted(env)]

    def launch_argv(self, name: str, *,
                    system_prompt: str | None = None,
                    prompt: str | None = None) -> list[str]:
        """Build argv for `<binary> -n NAME [--append-system-prompt P] [PROMPT]`.

        Matches Claude Code's CLI shape; vendor variants mirror it.
        The positional prompt at the end uses Claude's "initial user
        message" handling, which side-steps the input-box autocomplete
        race that previously corrupted dash-bearing slash commands."""
        argv = self._env_prefix() + [self.binary, "-n", name]
        if system_prompt:
            argv += ["--append-system-prompt", system_prompt]
        if prompt:
            argv.append(prompt)
        return argv

    def resume_argv(self, session_id: str) -> list[str]:
        """argv for resuming an existing conversation, routed by id shape.

        Two distinct resume paths exist and they take different ids:

          * Local Claude transcript UUID (the on-disk session id Eva
            captures from the SessionStart hook, e.g.
            `48a6fd81-7f37-...`) -> `<binary> --resume UUID`. This
            passes through to Claude Code's LOCAL resume, which reads
            `~/.claude/projects/<cwd>/UUID.jsonl`. It is cloud-
            independent and works as long as the transcript is on disk,
            but it is cwd-sensitive: the launch dir must match the dir
            the session was created in (see `sessions.resume_session`,
            which resolves that cwd from the transcript).

          * Cloud session id (a numeric cloud id, e.g.
            `1777500864453`) or a name/search term -> `<binary> resume
            ID`, the agent's own resume subcommand, which resolves the
            id against the cloud (and local) session registry. Cloud
            resume requires running inside the session's git repo.

        Routing by id shape (not a stored flag) keeps callers simple:
        Eva records exactly one `agent_session_id` per session and this
        picks the right invocation. The earlier unconditional `resume
        ID` form broke local-only sessions whose cloud entry had been
        garbage-collected -- the agent exited with "No sessions found" and
        the tmux pane died on every restart."""
        if _looks_like_session_uuid(session_id):
            return self._env_prefix() + [self.binary, "--resume", session_id]
        return self._env_prefix() + [self.binary, "resume", session_id]

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
