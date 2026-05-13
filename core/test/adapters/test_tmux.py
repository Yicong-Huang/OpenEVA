"""Unit tests for `adapters/tmux.py`.

This adapter is the sole call-site for the `tmux` binary. Tests pin
down the exact shell commands + timeout behaviour so the shape of the
subprocess calls doesn't drift silently.
"""

import subprocess
from unittest.mock import patch, MagicMock

import adapters.tmux as tmux


class TestSessionExists:
    @patch("adapters.tmux.subprocess.run")
    def test_true_when_tmux_returncode_zero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert tmux.session_exists("live") is True
        mock_run.assert_called_once_with(
            ["tmux", "has-session", "-t", "live"],
            capture_output=True, timeout=tmux._HAS_SESSION_TIMEOUT,
        )

    @patch("adapters.tmux.subprocess.run")
    def test_false_when_tmux_returncode_nonzero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert tmux.session_exists("missing") is False

    @patch("adapters.tmux.subprocess.run",
           side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=5))
    def test_false_on_timeout(self, _mock_run):
        """tmux hung? Report 'no session' rather than propagate -- the
        caller will typically rebuild the session on a False answer."""
        assert tmux.session_exists("hung") is False


class TestCaptureOutput:
    @patch("adapters.tmux.session_exists", return_value=False)
    def test_empty_when_session_missing(self, _mock_exists):
        """Skip the capture-pane call entirely if the session doesn't
        exist -- saves a subprocess round-trip."""
        assert tmux.capture_output("nope") == ""

    @patch("adapters.tmux.session_exists", return_value=True)
    @patch("adapters.tmux.subprocess.run")
    def test_returns_stdout(self, mock_run, _mock_exists):
        mock_run.return_value = MagicMock(stdout="line1\nline2\n", returncode=0)
        out = tmux.capture_output("sess", lines=10)
        assert out == "line1\nline2\n"
        mock_run.assert_called_once_with(
            ["tmux", "capture-pane", "-t", "sess", "-p", "-S", "-10"],
            capture_output=True, text=True, timeout=tmux._CAPTURE_TIMEOUT,
        )

    @patch("adapters.tmux.session_exists", return_value=True)
    @patch("adapters.tmux.subprocess.run",
           side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=5))
    def test_empty_on_timeout(self, _mock_run, _mock_exists):
        assert tmux.capture_output("hung") == ""


class TestSendKeys:
    @patch("adapters.tmux.subprocess.run")
    def test_sends_text_then_enter(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        tmux.send_keys("sess", "ls -la")
        mock_run.assert_called_once_with(
            ["tmux", "send-keys", "-t", "sess", "ls -la", "Enter"],
            check=True, timeout=tmux._SEND_KEYS_TIMEOUT,
        )

    @patch("adapters.tmux.subprocess.run",
           side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=5))
    def test_swallows_timeout(self, _mock_run):
        """A keystroke timing out mustn't crash the caller; the UI polls."""
        tmux.send_keys("hung", "cmd")  # must not raise

    @patch("adapters.tmux.subprocess.run",
           side_effect=subprocess.CalledProcessError(returncode=1, cmd="tmux"))
    def test_swallows_nonzero_exit(self, _mock_run):
        """`check=True` -> subprocess raises on non-zero; swallow so one
        failed send doesn't kill whatever batch operation was running."""
        tmux.send_keys("sess", "cmd")


class TestKillSession:
    @patch("adapters.tmux.subprocess.run")
    def test_invokes_kill_session_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        tmux.kill_session("sess")
        mock_run.assert_called_once_with(
            ["tmux", "kill-session", "-t", "sess"],
            capture_output=True, timeout=tmux._KILL_TIMEOUT,
        )

    @patch("adapters.tmux.subprocess.run",
           side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=5))
    def test_swallows_timeout(self, _mock_run):
        """Best-effort kill: a hung tmux shouldn't propagate to the
        request path. Callers proceed to clean up DB state regardless."""
        tmux.kill_session("hung")  # must not raise


class TestGracefulKillSession:
    """`graceful_kill_session` sends Ctrl+C, waits briefly for the agent to
    flush state, then tmux-kills. Drives the kill path so agent's
    .jsonl log isn't truncated mid-write."""

    @patch("adapters.tmux.time.sleep")  # don't actually sleep in tests
    @patch("adapters.tmux.subprocess.run")
    def test_sends_ctrl_c_then_kills(self, mock_run, _mock_sleep):
        mock_run.return_value = MagicMock(returncode=0)
        tmux.graceful_kill_session("sess")

        assert mock_run.call_count == 2
        first = mock_run.call_args_list[0][0][0]
        second = mock_run.call_args_list[1][0][0]
        assert first == ["tmux", "send-keys", "-t", "sess", "C-c"]
        assert second == ["tmux", "kill-session", "-t", "sess"]

    @patch("adapters.tmux.time.sleep")
    @patch("adapters.tmux.subprocess.run")
    def test_honours_grace_argument(self, _mock_run, mock_sleep):
        tmux.graceful_kill_session("sess", grace=1.5)
        mock_sleep.assert_called_once_with(1.5)

    @patch("adapters.tmux.time.sleep")
    @patch("adapters.tmux.subprocess.run")
    def test_default_grace_half_second(self, _mock_run, mock_sleep):
        """Regression: shortening the grace window makes agent log
        truncation come back; lengthening it makes the UI feel sluggish."""
        tmux.graceful_kill_session("sess")
        mock_sleep.assert_called_once_with(0.5)

    @patch("adapters.tmux.time.sleep")
    @patch("adapters.tmux.subprocess.run")
    def test_send_keys_timeout_still_hard_kills(self, mock_run, _mock_sleep):
        """If send-keys times out (tmux stuck), we still fall through
        to the hard kill so the session doesn't linger."""
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd="tmux", timeout=5),
            MagicMock(returncode=0),  # kill succeeds
        ]
        tmux.graceful_kill_session("hung")
        # Both calls were attempted.
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[1][0][0] == [
            "tmux", "kill-session", "-t", "hung",
        ]


class TestLaunchSession:
    @patch("adapters.tmux.session_exists", return_value=False)
    @patch("adapters.tmux.subprocess.run")
    def test_creates_then_sends_command(self, mock_run, _mock_exists):
        mock_run.return_value = MagicMock(returncode=0)
        tmux.launch_session("s1", "~/work", "agent -n s1")

        assert mock_run.call_count == 2
        create_call = mock_run.call_args_list[0][0][0]
        assert create_call[:4] == ["tmux", "new-session", "-d", "-s"]
        assert "s1" in create_call
        # -c arg should be expanded, not raw "~/work".
        import os
        c_idx = create_call.index("-c")
        assert create_call[c_idx + 1] == os.path.expanduser("~/work")

        send_call = mock_run.call_args_list[1][0][0]
        assert send_call[:3] == ["tmux", "send-keys", "-t"]
        assert "agent -n s1" in send_call
        assert send_call[-1] == "Enter"

    @patch("adapters.tmux.session_exists", return_value=True)
    @patch("adapters.tmux.subprocess.run")
    def test_noop_when_session_already_exists(self, mock_run, _mock_exists):
        """Re-launch of an already-live session must not spawn a duplicate
        or re-send the command (which would enqueue the agent invocation
        into the running shell)."""
        tmux.launch_session("live", "~", "agent")
        mock_run.assert_not_called()


class TestLaunchSessionArgv:
    """launch_session_argv runs the child process via argv directly -- no
    shell, no send-keys. Required for multi-line or metachar-bearing args
    (e.g. --append-system-prompt) where send-keys would corrupt newlines."""

    @patch("adapters.tmux.session_exists", return_value=False)
    @patch("adapters.tmux.subprocess.run")
    def test_appends_argv_after_session_args(self, mock_run, _mock_exists):
        """The child argv is appended after `-c <dir>`, so tmux spawns the
        program directly rather than dropping the user into a shell."""
        import os
        mock_run.return_value = MagicMock(returncode=0)
        tmux.launch_session_argv(
            "argv-sess", "~/work",
            ["agent", "-n", "argv-sess", "--append-system-prompt", "hi\nthere"],
        )

        assert mock_run.call_count == 1
        call = mock_run.call_args_list[0][0][0]
        assert call[:4] == ["tmux", "new-session", "-d", "-s"]
        assert "argv-sess" in call
        c_idx = call.index("-c")
        assert call[c_idx + 1] == os.path.expanduser("~/work")
        # The argv elements must follow the -c <dir> pair, verbatim.
        assert call[-5:] == [
            "agent", "-n", "argv-sess",
            "--append-system-prompt", "hi\nthere",
        ]

    @patch("adapters.tmux.session_exists", return_value=True)
    @patch("adapters.tmux.subprocess.run")
    def test_noop_when_session_already_exists(self, mock_run, _mock_exists):
        """Idempotent: re-invoking with a live session name must not
        duplicate the tmux new-session call."""
        tmux.launch_session_argv("live", "~", ["agent"])
        mock_run.assert_not_called()


class TestWaitUntilReady:
    """`wait_until_ready` is the gate before pasting a slash command
    into a freshly launched session -- the prompt-glyph poll prevents
    agent's autocomplete from chewing up `/yh-foo` into `/y` because
    the dash arrives mid-render."""

    def test_returns_true_when_prompt_glyph_present(self, monkeypatch):
        # capture_output sees the prompt glyph -> ready immediately.
        monkeypatch.setattr(tmux, "session_exists", lambda _n: True)
        monkeypatch.setattr(
            tmux, "capture_output",
            lambda _n, lines=10: "\n".join(["", "❯ ", ""]),
        )
        # Tight timeout so a regression that fails to detect ready
        # would surface as a slow test rather than hang forever.
        assert tmux.wait_until_ready("sess", timeout_secs=1) is True

    def test_returns_true_when_shortcuts_hint_present(self, monkeypatch):
        # The "? for shortcuts" hint is the alternate readiness marker
        # agent shows when its prompt is in a slightly different state.
        monkeypatch.setattr(tmux, "session_exists", lambda _n: True)
        monkeypatch.setattr(
            tmux, "capture_output",
            lambda _n, lines=10: "  ? for shortcuts\n",
        )
        assert tmux.wait_until_ready("sess", timeout_secs=1) is True

    def test_skips_when_session_missing_and_eventually_returns_false(
        self, monkeypatch
    ):
        # When the session vanishes mid-poll, the 0.5s sleep branch
        # fires (line 136-137) until the deadline. This must not raise.
        sleeps: list[float] = []
        monkeypatch.setattr(tmux, "session_exists", lambda _n: False)
        # Deterministic time: advance past the deadline on the second tick.
        clock = {"t": 100.0}
        monkeypatch.setattr(
            "time.time",
            lambda: clock["t"],
        )

        def fake_sleep(s):
            sleeps.append(s)
            clock["t"] += 1.0  # past timeout

        monkeypatch.setattr("time.sleep", fake_sleep)
        out = tmux.wait_until_ready("sess", timeout_secs=1)
        assert out is False
        # The "session missing" branch sleeps with 0.5s spacing.
        assert sleeps and sleeps[0] == 0.5

    def test_returns_false_on_timeout_with_no_prompt(self, monkeypatch):
        # Capture returns content that doesn't match either readiness
        # marker -> the loop sleeps + retries until the deadline.
        monkeypatch.setattr(tmux, "session_exists", lambda _n: True)
        monkeypatch.setattr(
            tmux, "capture_output",
            lambda _n, lines=10: "the agent is loading...",
        )
        clock = {"t": 100.0}
        monkeypatch.setattr("time.time", lambda: clock["t"])

        def fake_sleep(s):
            clock["t"] += s + 0.6  # advance past deadline quickly

        monkeypatch.setattr("time.sleep", fake_sleep)
        assert tmux.wait_until_ready("sess", timeout_secs=1) is False

    def test_clamps_zero_timeout_to_minimum_one(self, monkeypatch):
        # `timeout_secs=0` would otherwise give a negative deadline and
        # immediately bail. The `max(1, timeout_secs)` clamp ensures one
        # poll cycle still fires.
        calls = {"n": 0}

        def capture(_n, lines=10):
            calls["n"] += 1
            return "❯"
        monkeypatch.setattr(tmux, "session_exists", lambda _n: True)
        monkeypatch.setattr(tmux, "capture_output", capture)
        assert tmux.wait_until_ready("sess", timeout_secs=0) is True
        assert calls["n"] >= 1
