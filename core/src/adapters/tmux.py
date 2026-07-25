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
_SCROLL_TIMEOUT = 3
_LAUNCH_TIMEOUT = 10
_KILL_TIMEOUT = 5
# Time we give the agent to clean up after Ctrl+C before we force-kill tmux.
# 500ms is enough for the agent to flush its .jsonl log and close file handles,
# short enough that the UI doesn't feel stalled on kill.
_GRACEFUL_KILL_DELAY = 0.5

# Input-prompt arrow glyphs, one per agent TUI family. Readiness /
# idle-state detection matches a line that is exactly one of these (or
# the glyph followed by whitespace):
#   ❯  ❯  -- Claude Code family (e.g. claude)
#   ›  ›  -- OpenAI Codex family
# Matching the union is safe: neither TUI emits the other's arrow as a
# standalone line, and keeping it agent-agnostic here avoids threading a
# session->agent lookup into this low-level adapter.
PROMPT_GLYPHS = ("❯", "›")


def line_is_prompt(stripped: str) -> bool:
    """True iff `stripped` is an agent input-prompt line.

    A prompt line is exactly a prompt glyph, or a glyph immediately
    followed by whitespace (Claude uses U+00A0 between its arrow and the
    typed text; Codex uses a normal space). Shared by every readiness /
    state-detection site so they stay consistent across agent TUIs."""
    for glyph in PROMPT_GLYPHS:
        if stripped == glyph:
            return True
        if (stripped.startswith(glyph) and len(stripped) > 1
                and stripped[1].isspace()):
            return True
    return False


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


def _pane_grabs_mouse(session_name: str) -> bool:
    """True when the program in the pane enabled mouse tracking (e.g. an
    interactive TUI like the agent). Such apps own scrolling -- they
    redraw in tmux's alternate screen, which has no scrollback -- so the
    only way to page their history is to feed them wheel reports."""
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "-t", session_name,
             "#{mouse_any_flag}"],
            capture_output=True, timeout=_SCROLL_TIMEOUT, text=True,
        )
        return r.stdout.strip() == "1"
    except (subprocess.TimeoutExpired, OSError):
        return False


# SGR mouse wheel reports (mode 1006): button 64 = wheel up, 65 = down.
# Sent at cell (1,1); the agent only needs the button to scroll.
_WHEEL_SGR = {"up": "\x1b[<64;1;1M", "down": "\x1b[<65;1;1M"}


def scroll_pane(session_name: str, direction: str, lines: int = 3) -> None:
    """Scroll the terminal `lines` rows in `direction` ('up'/'down').

    Two transports, picked by what the pane's program wants:

      - **App grabbed the mouse** (interactive TUI / the agent): it runs
        in the alt-screen with no tmux scrollback and scrolls its OWN
        viewport, so we feed it synthesized wheel reports (one per
        line). The browser xterm deliberately stays out of mouse mode
        (so plain-drag selection works), so this server-side forwarding
        is what makes the wheel reach the app.
      - **Plain pane** (shell, non-mouse output): drive tmux copy-mode
        over the pane's real scrollback. Scroll-up enters copy-mode with
        `-e` (a later scroll past the bottom auto-exits to live tail);
        scroll-down only acts when already in copy-mode.

    `lines` is clamped 1..50. Every failure is swallowed -- a missing
    session or not-in-a-mode state must never raise into the request."""
    if direction not in ("up", "down"):
        return
    n = max(1, min(int(lines), 50))
    try:
        if _pane_grabs_mouse(session_name):
            seq = _WHEEL_SGR[direction] * n
            subprocess.run(
                ["tmux", "send-keys", "-t", session_name, "-l", seq],
                check=False, capture_output=True, timeout=_SCROLL_TIMEOUT,
            )
            return
        if direction == "up":
            # Idempotent: re-entering copy-mode while already in it is a
            # no-op. `-e` = exit copy-mode when a scroll-down passes the
            # bottom, so the terminal resumes live tailing on its own.
            subprocess.run(
                ["tmux", "copy-mode", "-e", "-t", session_name],
                check=False, capture_output=True, timeout=_SCROLL_TIMEOUT,
            )
            subprocess.run(
                ["tmux", "send-keys", "-t", session_name,
                 "-X", "-N", str(n), "scroll-up"],
                check=False, capture_output=True, timeout=_SCROLL_TIMEOUT,
            )
        else:
            # No-op (and a harmless "not in a mode" on stderr, which
            # capture_output discards) when the pane isn't in copy-mode.
            subprocess.run(
                ["tmux", "send-keys", "-t", session_name,
                 "-X", "-N", str(n), "scroll-down"],
                check=False, capture_output=True, timeout=_SCROLL_TIMEOUT,
            )
    except subprocess.TimeoutExpired:
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
    while _time.time() < deadline:
        if not session_exists(session_name):
            _time.sleep(0.5)
            continue
        out = capture_output(session_name, lines=10)
        for line in out.splitlines():
            stripped = line.strip()
            # Match either agent family's input-prompt arrow (Claude's
            # `❯` or Codex's `›`) so paste lands cleanly regardless of
            # which agent launched the session.
            if line_is_prompt(stripped):
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
