"""Unit tests for tmux helper functions with mocked subprocess calls."""

from unittest.mock import patch, MagicMock
import subprocess

from adapters.tmux import (
    session_exists,
    capture_output,
    launch_session,
    paste_text,
)


class TestTmuxSessionExists:
    @patch("adapters.tmux.subprocess.run")
    def test_exists_returns_true(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert session_exists("my-session") is True
        mock_run.assert_called_once_with(
            ["tmux", "has-session", "-t", "my-session"],
            capture_output=True, timeout=5,
        )

    @patch("adapters.tmux.subprocess.run")
    def test_not_exists_returns_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert session_exists("missing") is False

    @patch("adapters.tmux.subprocess.run")
    def test_timeout_returns_false(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tmux", timeout=5)
        assert session_exists("hung") is False


class TestTmuxCaptureOutput:
    @patch("adapters.tmux.session_exists", return_value=True)
    @patch("adapters.tmux.subprocess.run")
    def test_capture_returns_stdout(self, mock_run, mock_exists):
        mock_run.return_value = MagicMock(stdout="line1\nline2\n", returncode=0)
        output = capture_output("my-session", lines=10)
        assert "line1" in output
        mock_run.assert_called_once_with(
            ["tmux", "capture-pane", "-t", "my-session", "-p", "-S", "-10"],
            capture_output=True, text=True, timeout=5,
        )

    @patch("adapters.tmux.session_exists", return_value=False)
    def test_capture_no_session_returns_empty(self, mock_exists):
        output = capture_output("gone")
        assert output == ""

    @patch("adapters.tmux.session_exists", return_value=True)
    @patch("adapters.tmux.subprocess.run")
    def test_capture_timeout_returns_empty(self, mock_run, mock_exists):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tmux", timeout=5)
        output = capture_output("hung")
        assert output == ""


class TestTmuxLaunchSession:
    @patch("adapters.tmux.session_exists", return_value=False)
    @patch("adapters.tmux.subprocess.run")
    def test_launch_creates_and_sends_keys(self, mock_run, mock_exists):
        mock_run.return_value = MagicMock(returncode=0)
        launch_session("sess1", "~/mydir", "echo hello")

        assert mock_run.call_count == 2
        # First call: new-session
        first_call = mock_run.call_args_list[0]
        assert first_call[0][0][:4] == ["tmux", "new-session", "-d", "-s"]
        assert "sess1" in first_call[0][0]
        # Second call: send-keys
        second_call = mock_run.call_args_list[1]
        assert second_call[0][0][:3] == ["tmux", "send-keys", "-t"]
        assert "echo hello" in second_call[0][0]

    @patch("adapters.tmux.session_exists", return_value=True)
    @patch("adapters.tmux.subprocess.run")
    def test_launch_skips_if_exists(self, mock_run, mock_exists):
        launch_session("existing", "~", "cmd")
        mock_run.assert_not_called()

    @patch("adapters.tmux.session_exists", return_value=False)
    @patch("adapters.tmux.subprocess.run")
    def test_launch_expands_user_dir(self, mock_run, mock_exists):
        import os
        mock_run.return_value = MagicMock(returncode=0)
        launch_session("sess2", "~/projects", "ls")
        first_call = mock_run.call_args_list[0]
        args = first_call[0][0]
        # The -c argument should have the expanded path
        c_idx = args.index("-c")
        assert args[c_idx + 1] == os.path.expanduser("~/projects")


class TestTmuxPasteText:
    @patch("adapters.tmux.subprocess.run")
    def test_paste_clears_input_then_bracketed_pastes_then_submits(self, mock_run):
        """5-step sequence verified live on cron-job-1:
            1. Escape          (close autocomplete dropdown)
            2. C-u             (kill stale input)
            3. set-buffer      (raw text, no newline)
            4. paste-buffer -p (bracketed mode; agent sees one atomic
                                insert and skips per-keystroke
                                autocomplete that ate dashes)
            5. send-keys Enter (submit -- newline can't be inside the
                                bracketed payload, agent treats it as
                                literal multi-line input there)

        Live evidence each step is needed: removing any one of these
        in the prod cron path produced either truncated commands
        (`/y. Did you mean /rc?`) or unsubmitted commands sitting
        in the input box."""
        mock_run.return_value = MagicMock(returncode=0)
        paste_text("sess", "/yh-code-sync-my-prs")

        assert mock_run.call_count == 5
        first = mock_run.call_args_list[0][0][0]
        assert first == ["tmux", "send-keys", "-t", "sess", "Escape"]
        second = mock_run.call_args_list[1][0][0]
        assert second == ["tmux", "send-keys", "-t", "sess", "C-u"]
        third = mock_run.call_args_list[2][0][0]
        assert third[:3] == ["tmux", "set-buffer", "-b"]
        # Buffer body is the raw text WITHOUT a trailing newline.
        assert "/yh-code-sync-my-prs" in third
        assert "/yh-code-sync-my-prs\n" not in third
        fourth = mock_run.call_args_list[3][0][0]
        assert fourth[:2] == ["tmux", "paste-buffer"]
        assert "-p" in fourth  # bracketed paste mode
        assert "-d" in fourth  # buffer is dropped after paste
        assert "-t" in fourth and "sess" in fourth
        fifth = mock_run.call_args_list[4][0][0]
        assert fifth == ["tmux", "send-keys", "-t", "sess", "Enter"]

    @patch("adapters.tmux.subprocess.run")
    def test_paste_passes_text_through_unchanged(self, mock_run):
        """Buffer body is the caller's raw text. No newline added,
        no newline stripped if the caller passed one (would be unusual
        but not the helper's job to second-guess). Submit comes from
        the separate send-keys Enter step, not the buffer body."""
        mock_run.return_value = MagicMock(returncode=0)
        paste_text("sess", "/cmd")
        set_buf = mock_run.call_args_list[2][0][0]
        body_idx = set_buf.index("-b") + 2
        assert set_buf[body_idx] == "/cmd"

    @patch("adapters.tmux.subprocess.run")
    def test_paste_silently_swallows_subprocess_errors(self, mock_run):
        # paste_text is best-effort: a failure on any of the three steps
        # must not raise into the caller (production: the cron tick
        # reporter would otherwise crash mid-iteration).
        mock_run.side_effect = subprocess.CalledProcessError(1, "tmux")
        paste_text("sess", "/cmd")  # no exception

    @patch("adapters.tmux.subprocess.run")
    def test_paste_uses_named_buffer_to_avoid_clobbering(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        paste_text("sess", "/cmd", buffer_name="custom_buf")
        # The buffer name appears in the set-buffer (index 2) and
        # paste-buffer (index 3) calls. Indexes 0/1 are Escape/C-u
        # which don't reference the buffer.
        set_buf = mock_run.call_args_list[2][0][0]
        assert "custom_buf" in set_buf
        paste_buf = mock_run.call_args_list[3][0][0]
        assert "custom_buf" in paste_buf

    @patch("adapters.tmux.subprocess.run")
    def test_default_buffer_is_per_session(self, mock_run):
        """Real-world bug: two cron jobs firing on the same scheduler
        tick raced a shared `_eva_paste` buffer; cron-job-1's command
        ended up pasted into cron-job-2's session. The default buffer
        name must be derived from the session so concurrent calls
        don't collide."""
        mock_run.return_value = MagicMock(returncode=0)
        paste_text("cron-job-1", "/cmd-A")
        paste_text("cron-job-2", "/cmd-B")
        # Each pair of (set-buffer, paste-buffer) for one session must
        # use a buffer name that includes the session identifier and
        # is distinct from the other session's.
        argvs = [c[0][0] for c in mock_run.call_args_list]
        # 10 calls total: 2 pastes × 5 tmux invocations each
        # (Escape, C-u, set-buffer, paste-buffer, send-keys Enter).
        # set-buffer is at index 2 and 7.
        set_a = argvs[2]
        set_b = argvs[7]
        assert "cron-job-1" in "_".join(set_a)
        assert "cron-job-2" in "_".join(set_b)
        # The buffer names must NOT match -- that was the bug.
        buf_a = set_a[set_a.index("-b") + 1]
        buf_b = set_b[set_b.index("-b") + 1]
        assert buf_a != buf_b

    @patch("adapters.tmux.subprocess.run")
    def test_default_buffer_sanitises_session_name(self, mock_run):
        """tmux buffer names accept alphanumerics + - + _; review
        sessions like `review-org-repo-123` already conform, but a
        ticket session like `ticket-EX-CERT-1234` (or any future name
        with `:` / `.` / `/`) must round-trip through the sanitiser."""
        mock_run.return_value = MagicMock(returncode=0)
        paste_text("review-myorg/svc-1", "/cmd")
        # set-buffer is at index 2 (Escape + C-u come first).
        set_buf = mock_run.call_args_list[2][0][0]
        buf = set_buf[set_buf.index("-b") + 1]
        # No raw `/` slipped in -- it'd be rejected by tmux.
        assert "/" not in buf
        # The session identity is still distinguishable in the buffer
        # name (replaced `/` with `_`).
        assert "svc" in buf
