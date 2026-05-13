"""PTY session manager: keeps tmux PTY connections alive for SSE streaming."""

import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import threading


class PtySession:
    """A PTY-attached tmux session for browser terminal streaming."""

    def __init__(self, session_name):
        self.session_name = session_name
        self.master_fd, slave_fd = pty.openpty()
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        import shlex
        safe_name = shlex.quote(session_name)
        self.proc = subprocess.Popen(
            ["bash", "-c",
             "tmux set-option -t " + safe_name + " mouse off 2>/dev/null; "
             "exec tmux attach-session -t " + safe_name],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            preexec_fn=os.setsid, env=env, close_fds=True,
        )
        os.close(slave_fd)
        self.pid = self.proc.pid
        s = struct.pack("HHHH", 40, 120, 0, 0)
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, s)
        flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.alive = True
        self.lock = threading.Lock()

    def read(self):
        """Read available data from PTY. Returns bytes (may be empty)."""
        try:
            r, _, _ = select.select([self.master_fd], [], [], 0.1)
            if r:
                return os.read(self.master_fd, 32768)
        except OSError:
            self.alive = False
        return b""

    def write(self, data):
        """Write data to PTY (thread-safe)."""
        with self.lock:
            try:
                os.write(self.master_fd, data)
            except OSError:
                self.alive = False

    def resize(self, rows, cols):
        """Resize PTY window."""
        try:
            s = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, s)
            os.kill(self.pid, signal.SIGWINCH)
        except (OSError, ProcessLookupError):
            pass

    def close(self):
        """Close PTY and terminate process."""
        self.alive = False
        try:
            os.close(self.master_fd)
        except OSError:
            pass
        try:
            os.kill(self.pid, signal.SIGTERM)
            os.waitpid(self.pid, os.WNOHANG)
        except Exception:
            pass


# Active PTY sessions: session_name -> PtySession
sessions = {}
_lock = threading.Lock()


def get_or_create(session_name):
    """Get existing PTY session or create new one (thread-safe)."""
    with _lock:
        ps = sessions.get(session_name)
        if ps and ps.alive:
            return ps
        if ps:
            ps.close()
        ps = PtySession(session_name)
        sessions[session_name] = ps
        return ps


def get(session_name):
    """Get existing PTY session or None."""
    return sessions.get(session_name)


def remove(session_name):
    """Close and remove a PTY session."""
    ps = sessions.get(session_name)
    if ps:
        ps.close()
        del sessions[session_name]
