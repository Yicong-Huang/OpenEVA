"""Tests for PtySession, get_or_create, terminal_input, terminal_resize.

The multiplex SSE transport lives in tests/test_terminal_mux.py.
All subprocess/pty/os calls are mocked."""

from unittest.mock import patch, MagicMock

import pty_manager


# ---- PtySession ----

class TestPtySession:
    @patch("pty_manager.fcntl.fcntl")
    @patch("pty_manager.fcntl.ioctl")
    @patch("pty_manager.subprocess.Popen")
    @patch("pty_manager.os.close")
    @patch("pty_manager.pty.openpty", return_value=(10, 11))
    def test_init_creates_pty_and_process(self, mock_openpty, mock_os_close,
                                          mock_popen, mock_ioctl, mock_fcntl,
                                          patched_server):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc
        mock_fcntl.return_value = 0

        ps = pty_manager.PtySession("test-session")

        assert ps.session_name == "test-session"
        assert ps.master_fd == 10
        assert ps.pid == 12345
        assert ps.alive is True
        mock_openpty.assert_called_once()
        mock_os_close.assert_called_once_with(11)  # slave_fd closed
        mock_popen.assert_called_once()
        # Verify ioctl was called for TIOCSWINSZ
        mock_ioctl.assert_called_once()

    @patch("pty_manager.select.select", return_value=([True], [], []))
    @patch("pty_manager.os.read", return_value=b"hello output")
    @patch("pty_manager.fcntl.fcntl")
    @patch("pty_manager.fcntl.ioctl")
    @patch("pty_manager.subprocess.Popen")
    @patch("pty_manager.os.close")
    @patch("pty_manager.pty.openpty", return_value=(10, 11))
    def test_read_returns_data(self, mock_openpty, mock_os_close,
                                mock_popen, mock_ioctl, mock_fcntl,
                                mock_os_read, mock_select, patched_server):
        mock_popen.return_value = MagicMock(pid=100)
        mock_fcntl.return_value = 0
        ps = pty_manager.PtySession("test-session")

        data = ps.read()
        assert data == b"hello output"

    @patch("pty_manager.select.select", return_value=([], [], []))
    @patch("pty_manager.fcntl.fcntl")
    @patch("pty_manager.fcntl.ioctl")
    @patch("pty_manager.subprocess.Popen")
    @patch("pty_manager.os.close")
    @patch("pty_manager.pty.openpty", return_value=(10, 11))
    def test_read_returns_empty_when_no_data(self, mock_openpty, mock_os_close,
                                              mock_popen, mock_ioctl, mock_fcntl,
                                              mock_select, patched_server):
        mock_popen.return_value = MagicMock(pid=100)
        mock_fcntl.return_value = 0
        ps = pty_manager.PtySession("test-session")

        data = ps.read()
        assert data == b""

    @patch("pty_manager.select.select", side_effect=OSError("fd closed"))
    @patch("pty_manager.fcntl.fcntl")
    @patch("pty_manager.fcntl.ioctl")
    @patch("pty_manager.subprocess.Popen")
    @patch("pty_manager.os.close")
    @patch("pty_manager.pty.openpty", return_value=(10, 11))
    def test_read_sets_alive_false_on_oserror(self, mock_openpty, mock_os_close,
                                               mock_popen, mock_ioctl, mock_fcntl,
                                               mock_select, patched_server):
        mock_popen.return_value = MagicMock(pid=100)
        mock_fcntl.return_value = 0
        ps = pty_manager.PtySession("test-session")

        data = ps.read()
        assert data == b""
        assert ps.alive is False

    @patch("pty_manager.os.write")
    @patch("pty_manager.fcntl.fcntl")
    @patch("pty_manager.fcntl.ioctl")
    @patch("pty_manager.subprocess.Popen")
    @patch("pty_manager.os.close")
    @patch("pty_manager.pty.openpty", return_value=(10, 11))
    def test_write_sends_data(self, mock_openpty, mock_os_close,
                               mock_popen, mock_ioctl, mock_fcntl,
                               mock_os_write, patched_server):
        mock_popen.return_value = MagicMock(pid=100)
        mock_fcntl.return_value = 0
        ps = pty_manager.PtySession("test-session")

        ps.write(b"input data")
        mock_os_write.assert_called_once_with(10, b"input data")

    @patch("pty_manager.os.write", side_effect=OSError("broken pipe"))
    @patch("pty_manager.fcntl.fcntl")
    @patch("pty_manager.fcntl.ioctl")
    @patch("pty_manager.subprocess.Popen")
    @patch("pty_manager.os.close")
    @patch("pty_manager.pty.openpty", return_value=(10, 11))
    def test_write_sets_alive_false_on_oserror(self, mock_openpty, mock_os_close,
                                                mock_popen, mock_ioctl, mock_fcntl,
                                                mock_os_write, patched_server):
        mock_popen.return_value = MagicMock(pid=100)
        mock_fcntl.return_value = 0
        ps = pty_manager.PtySession("test-session")

        ps.write(b"data")
        assert ps.alive is False

    @patch("pty_manager.os.kill")
    @patch("pty_manager.fcntl.ioctl")
    @patch("pty_manager.fcntl.fcntl")
    @patch("pty_manager.subprocess.Popen")
    @patch("pty_manager.os.close")
    @patch("pty_manager.pty.openpty", return_value=(10, 11))
    def test_resize(self, mock_openpty, mock_os_close,
                     mock_popen, mock_fcntl, mock_ioctl,
                     mock_os_kill, patched_server):
        mock_popen.return_value = MagicMock(pid=100)
        mock_fcntl.return_value = 0
        ps = pty_manager.PtySession("test-session")
        # Reset ioctl mock count from init
        mock_ioctl.reset_mock()

        ps.resize(50, 200)
        mock_ioctl.assert_called_once()
        mock_os_kill.assert_called_once_with(100, pty_manager.signal.SIGWINCH)

    @patch("pty_manager.os.waitpid")
    @patch("pty_manager.os.kill")
    @patch("pty_manager.os.close")
    @patch("pty_manager.fcntl.fcntl")
    @patch("pty_manager.fcntl.ioctl")
    @patch("pty_manager.subprocess.Popen")
    @patch("pty_manager.pty.openpty", return_value=(10, 11))
    def test_close(self, mock_openpty, mock_popen, mock_ioctl, mock_fcntl,
                    mock_os_close, mock_os_kill, mock_waitpid, patched_server):
        mock_popen.return_value = MagicMock(pid=100)
        mock_fcntl.return_value = 0
        ps = pty_manager.PtySession("test-session")
        mock_os_close.reset_mock()

        ps.close()
        assert ps.alive is False
        # Should close master_fd and send SIGTERM
        mock_os_close.assert_called_once_with(10)
        mock_os_kill.assert_called_with(100, pty_manager.signal.SIGTERM)

    @patch("pty_manager.os.kill", side_effect=ProcessLookupError)
    @patch("pty_manager.fcntl.fcntl")
    @patch("pty_manager.fcntl.ioctl")
    @patch("pty_manager.subprocess.Popen")
    @patch("pty_manager.os.close")
    @patch("pty_manager.pty.openpty", return_value=(10, 11))
    def test_close_handles_errors(self, mock_openpty, mock_os_close,
                                   mock_popen, mock_ioctl, mock_fcntl,
                                   mock_os_kill, patched_server):
        mock_popen.return_value = MagicMock(pid=100)
        mock_fcntl.return_value = 0
        ps = pty_manager.PtySession("test-session")

        # Now make os.close raise OSError for the close() call
        mock_os_close.side_effect = OSError
        # Should not raise even with OSError on close and ProcessLookupError on kill
        ps.close()
        assert ps.alive is False

    @patch("pty_manager.os.kill", side_effect=OSError)
    @patch("pty_manager.fcntl.ioctl", side_effect=[None, OSError])
    @patch("pty_manager.fcntl.fcntl")
    @patch("pty_manager.subprocess.Popen")
    @patch("pty_manager.os.close")
    @patch("pty_manager.pty.openpty", return_value=(10, 11))
    def test_resize_handles_errors(self, mock_openpty, mock_os_close,
                                    mock_popen, mock_fcntl, mock_ioctl,
                                    mock_os_kill, patched_server):
        mock_popen.return_value = MagicMock(pid=100)
        mock_fcntl.return_value = 0
        ps = pty_manager.PtySession("test-session")

        # Should not raise
        ps.resize(30, 80)


# ---- get_or_create ----

class TestGetOrCreatePty:
    def test_creates_new_pty(self, patched_server):
        pty_manager.sessions.clear()
        with patch.object(pty_manager, "PtySession") as mock_pty_cls:
            mock_ps = MagicMock()
            mock_ps.alive = True
            mock_pty_cls.return_value = mock_ps

            result = pty_manager.get_or_create("new-session")
            assert result is mock_ps
            assert "new-session" in pty_manager.sessions

    def test_returns_existing_alive_pty(self, patched_server):
        mock_ps = MagicMock()
        mock_ps.alive = True
        pty_manager.sessions["alive-session"] = mock_ps

        result = pty_manager.get_or_create("alive-session")
        assert result is mock_ps

    def test_replaces_dead_pty(self, patched_server):
        dead_ps = MagicMock()
        dead_ps.alive = False
        pty_manager.sessions["dead-session"] = dead_ps

        with patch.object(pty_manager, "PtySession") as mock_pty_cls:
            new_ps = MagicMock()
            new_ps.alive = True
            mock_pty_cls.return_value = new_ps

            result = pty_manager.get_or_create("dead-session")
            assert result is new_ps
            dead_ps.close.assert_called_once()


# The legacy per-terminal /stream endpoint was removed in favour of the
# multiplex /api/terminals/stream; see tests/test_terminal_mux.py.


# ---- terminal_input ----

class TestTerminalInput:
    def test_returns_404_when_no_session(self, client, patched_server):
        pty_manager.sessions.clear()
        resp = client.post("/api/terminal/no-session/input", content=b"test")
        assert resp.status_code == 404

    def test_sends_input_to_pty(self, client, patched_server):
        mock_ps = MagicMock()
        mock_ps.alive = True
        pty_manager.sessions["input-sess"] = mock_ps

        resp = client.post("/api/terminal/input-sess/input", content=b"ls -la\n")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        mock_ps.write.assert_called_once_with(b"ls -la\n")

    def test_returns_404_when_pty_dead(self, client, patched_server):
        mock_ps = MagicMock()
        mock_ps.alive = False
        pty_manager.sessions["dead-sess"] = mock_ps

        resp = client.post("/api/terminal/dead-sess/input", content=b"test")
        assert resp.status_code == 404


# ---- terminal_resize ----

class TestTerminalResize:
    def test_returns_404_when_no_session(self, client, patched_server):
        pty_manager.sessions.clear()
        resp = client.post("/api/terminal/no-session/resize?rows=30&cols=100")
        assert resp.status_code == 404

    def test_resizes_pty(self, client, patched_server):
        mock_ps = MagicMock()
        mock_ps.alive = True
        pty_manager.sessions["resize-sess"] = mock_ps

        resp = client.post("/api/terminal/resize-sess/resize?rows=50&cols=200")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        mock_ps.resize.assert_called_once_with(50, 200)

    def test_returns_404_when_pty_dead(self, client, patched_server):
        mock_ps = MagicMock()
        mock_ps.alive = False
        pty_manager.sessions["dead-sess"] = mock_ps

        resp = client.post("/api/terminal/dead-sess/resize?rows=30&cols=100")
        assert resp.status_code == 404

    def test_defaults_to_24x80_when_params_omitted(self, client, patched_server):
        """Resize without query params uses the 24x80 defaults from the route."""
        mock_ps = MagicMock()
        mock_ps.alive = True
        pty_manager.sessions["def-sess"] = mock_ps

        resp = client.post("/api/terminal/def-sess/resize")
        assert resp.status_code == 200
        mock_ps.resize.assert_called_once_with(24, 80)


# ---- Additional edge cases across all terminal routes ----


class TestTerminalEdgeCases:
    """Edge cases for /input and /resize: empty body, URL-encoded names,
    zero dimensions, raw binary. (Keepalive is now covered in the multiplex
    transport tests.)"""

    def test_input_accepts_empty_body(self, client, patched_server):
        """Empty POST body must still succeed (user might press Enter alone)."""
        mock_ps = MagicMock()
        mock_ps.alive = True
        pty_manager.sessions["empty-sess"] = mock_ps

        resp = client.post("/api/terminal/empty-sess/input", content=b"")
        assert resp.status_code == 200
        mock_ps.write.assert_called_once_with(b"")

    def test_input_passes_raw_binary_unchanged(self, client, patched_server):
        """Control characters / non-UTF must reach the PTY untouched so
        ESC sequences and unicode both work."""
        mock_ps = MagicMock()
        mock_ps.alive = True
        pty_manager.sessions["bin-sess"] = mock_ps
        payload = b"\x1b[A\x1b[B\xff\xfe"

        resp = client.post("/api/terminal/bin-sess/input", content=payload)
        assert resp.status_code == 200
        mock_ps.write.assert_called_once_with(payload)

    def test_resize_with_zero_or_negative_values_still_forwards(self, client, patched_server):
        """The route doesn't guard against zero/negative -- document that
        behaviour so we notice if we later want to validate."""
        mock_ps = MagicMock()
        mock_ps.alive = True
        pty_manager.sessions["zero-sess"] = mock_ps

        resp = client.post("/api/terminal/zero-sess/resize?rows=0&cols=0")
        assert resp.status_code == 200
        mock_ps.resize.assert_called_once_with(0, 0)

    def test_session_name_with_spaces_url_decoded(self, client, patched_server):
        """Spaces in session names (URL-encoded as %20) are decoded by FastAPI."""
        mock_ps = MagicMock()
        mock_ps.alive = True
        pty_manager.sessions["my sess"] = mock_ps

        resp = client.post("/api/terminal/my%20sess/input", content=b"hi")
        assert resp.status_code == 200
        mock_ps.write.assert_called_once_with(b"hi")
