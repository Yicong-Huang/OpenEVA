"""tmux adapter: the single place Eva shells out to the `tmux` binary.

Every tmux call -- liveness probe, pane capture, send-keys, launch new
session -- is funneled through this module so:

- Core / services / plugins don't `import subprocess` to talk to tmux
- Tests only need to patch `adapters.tmux.subprocess.run` (one place)
- Error handling is consistent: subprocess failures never raise into
  callers (tmux outages shouldn't crash the web request path)

Production code imports the names directly:
    `from adapters.tmux import session_exists, launch_session, ...`
Tests patch each importing module's binding (since `from`-imports
re-bind locally) -- see `tests/conftest.py::mock_tmux`.
"""

from __future__ import annotations

import os
import re
import subprocess
import time


# Short timeouts are fine -- tmux commands complete in milliseconds when
# the daemon is healthy. Anything longer than a second indicates tmux is
# stuck; better to surface that than to block the request thread.
_HAS_SESSION_TIMEOUT = 5
_CAPTURE_TIMEOUT = 5
_SEND_KEYS_TIMEOUT = 5
_LAUNCH_TIMEOUT = 10
_KILL_TIMEOUT = 5
# Time we give the agent to clean up after Ctrl+C before we force-kill tmux.
# 500ms is enough for the agent to flush its .jsonl log and close file handles,
# short enough that the UI doesn't feel stalled on kill.
_GRACEFUL_KILL_DELAY = 0.5


def session_exists(name: str) -> bool:
    """Return True iff the named tmux session is currently alive."""
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", name],
            capture_output=True, timeout=_HAS_SESSION_TIMEOUT,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def list_sessions() -> list[str]:
    """Return names of every currently-alive tmux session.

    Used at server startup by the session-state cache to rebuild its
    map from the ground truth (tmux) instead of trusting any stale DB
    column. Empty list when no daemon is running.
    """
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, timeout=_HAS_SESSION_TIMEOUT,
        )
        if result.returncode != 0:
            return []
        return [
            line.strip() for line in result.stdout.decode().splitlines()
            if line.strip()
        ]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def capture_output(name: str, lines: int = 20) -> str:
    """Return the last `lines` of the tmux pane's output (text, no ANSI).

    Empty string when the session is missing or tmux times out. Used for
    quick-glance session status, not full terminal replay -- see
    `routes/terminal.py` for the SSE-driven pipeline."""
    if not session_exists(name):
        return ""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", name, "-p", "-S", str(-lines)],
            capture_output=True, text=True, timeout=_CAPTURE_TIMEOUT,
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        return ""


def send_keys(session_name: str, text: str) -> None:
    """Send `text` + Enter to the tmux session. Silently no-ops on
    failure (the caller retries via polling or surfaces the downstream
    effect of the keys not landing)."""
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, text, "Enter"],
            check=True, timeout=_SEND_KEYS_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        pass


_BUFFER_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def _per_session_buffer(session_name: str) -> str:
    """tmux buffer name scoped to one session.

    Two cron jobs firing on the same scheduler tick used to race a
    single shared `_eva_paste` buffer: set-buffer / paste-buffer / -d
    interleaved across sessions and a command meant for cron-job-1
    landed in cron-job-2 (or got eaten entirely). Per-session buffers
    eliminate the race -- the worst case is two writes into the same
    session, which serialises naturally.
    """
    safe = _BUFFER_NAME_SAFE_RE.sub("_", session_name)
    return f"_eva_paste_{safe}"


def paste_text(session_name: str, text: str, *,
               buffer_name: str = "") -> None:
    """Send `text` + Enter into a tmux session, defeating the agent's
    autocomplete dropdown which would otherwise eat the dashes in a
    slash command.

    Sequence (verified live on cron-job-1):
      1. Escape       -- close any open autocomplete dropdown
      2. C-u          -- kill-line, clear stale input from a previous
                         failed tick that never submitted
      3. set-buffer   -- write the text (NO trailing newline) into a
                         per-session named buffer
      4. paste-buffer -p -d   -- bracketed-paste markers tell the agent
                         "this is one atomic insert"; `-d` drops the
                         buffer afterwards
      5. send-keys Enter   -- submit the (now-clean) input

    Why each step matters:
      - Without `-p`: the agent processes each char as a keystroke,
        autocomplete dropdown intercepts the first dash, and the
        command is truncated (live cron-job-1 produced 9+ lines of
        `Unknown command: /y. Did you mean /rc?` from this).
      - Without Escape+C-u: stale input from an earlier failed tick
        concatenates to the new paste (`/y` + `/yh-code-sync-my-prs`
        = `/y/yh-...`, invalid).
      - Newline must NOT be in the bracketed-paste payload: the agent
        treats `\\n` inside `\\e[200~...\\e[201~` as a literal
        newline (multi-line input), not as Enter. Submission
        requires a separate send-keys Enter AFTER the paste markers
        close.

    Each step is best-effort -- a single failure short-circuits the
    rest. The default buffer name is per-session so two concurrent
    calls (e.g. two cron jobs on the same tick) don't clobber each
    other's text.
    """
    if not buffer_name:
        buffer_name = _per_session_buffer(session_name)
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "Escape"],
            check=True, timeout=_SEND_KEYS_TIMEOUT,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "C-u"],
            check=True, timeout=_SEND_KEYS_TIMEOUT,
        )
        # Buffer body is the raw text (no trailing newline) -- the
        # newline-as-Enter step is a separate send-keys below, AFTER
        # the bracketed-paste marker closes.
        subprocess.run(
            ["tmux", "set-buffer", "-b", buffer_name, text],
            check=True, timeout=_SEND_KEYS_TIMEOUT,
        )
        subprocess.run(
            ["tmux", "paste-buffer", "-p", "-d",
             "-b", buffer_name, "-t", session_name],
            check=True, timeout=_SEND_KEYS_TIMEOUT,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "Enter"],
            check=True, timeout=_SEND_KEYS_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        pass


def wait_until_ready(session_name: str, timeout_secs: int = 30) -> bool:
    """Poll the session's tmux pane until the agent shows the input prompt.

    Useful when a caller wants to send a slash command into a freshly
    launched session: typing into a half-rendered prompt races with
    the agent's autocomplete and produces garbage (e.g. `/cmd-foo`
    becomes `/c` because the dash arrives before the autocomplete
    dropdown closes). Calling `wait_until_ready` before `send_keys` makes the
    command land cleanly.

    Returns True when the prompt was observed, False on timeout. The
    polling indicators mirror the wait-ready HTTP route's logic so
    the two stay consistent.
    """
    import time as _time
    deadline = _time.time() + max(1, timeout_secs)
    PROMPT_GLYPH = "❯"  # the > triangle the agent shows in its input row
    while _time.time() < deadline:
        if not session_exists(session_name):
            _time.sleep(0.5)
            continue
        out = capture_output(session_name, lines=10)
        for line in out.splitlines():
            stripped = line.strip()
            if stripped == PROMPT_GLYPH or stripped.startswith(PROMPT_GLYPH + " "):
                return True
            if "? for shortcuts" in stripped:
                return True
        _time.sleep(0.5)
    return False


def kill_session(name: str) -> None:
    """Terminate a tmux session. No-op on timeout or non-zero exit --
    callers (routes/core) treat kill as best-effort because a dead
    session is functionally equivalent to a successful kill."""
    try:
        subprocess.run(
            ["tmux", "kill-session", "-t", name],
            capture_output=True, timeout=_KILL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        pass


def graceful_kill_session(name: str,
                          grace: float = _GRACEFUL_KILL_DELAY) -> None:
    """Kill a session cleanly: send Ctrl+C first so any running agent
    gets a chance to flush state, then tmux-kill after `grace` seconds.

    Ctrl+C is chosen over `exit` because it works regardless of what
    the agent is doing (REPL prompt, mid-response, tool call). The
    tmux kill-session after the grace window guarantees the session
    goes away even if the agent refuses to quit cleanly."""
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", name, "C-c"],
            capture_output=True, timeout=_SEND_KEYS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # tmux itself is stuck; fall through to hard kill.
        pass
    # Give the agent a moment to catch the signal and flush log.
    time.sleep(grace)
    kill_session(name)


def launch_session(name: str, working_dir: str, command: str) -> None:
    """Create a new detached tmux session running `command` in
    `working_dir`. No-op if a session with the same name already exists
    so callers can treat this as idempotent.

    Uses `send-keys` for the command, which means the shell inside the
    session parses it -- convenient for string-form commands, but
    newlines in `command` get typed as Enter. For multi-line args
    (e.g. --append-system-prompt) use `launch_session_argv` instead."""
    expanded_dir = os.path.expanduser(working_dir)
    if not session_exists(name):
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "-c", expanded_dir],
            check=True, timeout=_LAUNCH_TIMEOUT,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", name, command, "Enter"],
            check=True, timeout=_SEND_KEYS_TIMEOUT,
        )


def launch_session_argv(name: str, working_dir: str, argv: list[str]) -> None:
    """Launch a tmux session where the child process is spawned directly
    from argv -- no shell parsing, no send-keys key-interpretation.

    Required when any argv element may contain newlines or shell
    metacharacters (single quotes, backticks, `$`). tmux invokes
    `execvp(argv)` so each element is delivered verbatim; this is the
    only way to pass a multi-line `--append-system-prompt` value
    without resorting to temp files or base64 encoding.

    Idempotent: no-op if a session with `name` already exists."""
    expanded_dir = os.path.expanduser(working_dir)
    if not session_exists(name):
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "-c", expanded_dir, *argv],
            check=True, timeout=_LAUNCH_TIMEOUT,
        )
