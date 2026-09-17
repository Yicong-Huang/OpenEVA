"""Startup recovery of sessions whose tmux pane died while Eva was down.

`session_state.recover_crashed_sessions` runs synchronously during server
startup and tries to bring every DB-recorded-but-tmux-missing session back
via `sessions.resume_session`. These tests pin the accounting: a resume
whose agent exits at startup must be reported as `crashed`, never counted
as `resumed`.

Regression context: a session whose transcript .jsonl had been deleted was
counted in `resumed=47` even though the pane died a beat after launch, so
the UI advertised a session nothing was listening on.
"""

from unittest.mock import patch

import pytest

from common import session_state


@pytest.fixture(autouse=True)
def _fast_pane_confirm(monkeypatch):
    """Keep the post-launch liveness window tiny in tests."""
    import common.sessions as _sessions
    monkeypatch.setattr(_sessions, "_PANE_CONFIRM_TIMEOUT", 0.02)
    monkeypatch.setattr(_sessions, "_PANE_CONFIRM_INTERVAL", 0.01)


@pytest.fixture(autouse=True)
def _no_real_tmux(monkeypatch):
    """`session_state` calls tmux via `from adapters import tmux as _tmux`,
    a binding the shared `mock_tmux` fixture doesn't rebind. Default every
    pane to "gone" so these tests never depend on the host's real tmux
    state; the live-session test overrides it locally."""
    from adapters import tmux as _tmux
    monkeypatch.setattr(_tmux, "session_exists", lambda name: False)


class TestRecoveryAccounting:
    def test_resume_reporting_not_running_counts_as_crashed(
        self, patched_server, mock_tmux,
    ):
        """A resume that launched but whose pane died is a FAILED recovery.
        Counting it as `resumed` is what hid the bug."""
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("rec-1", "test-proj", "rec-1")
        patched_server._db.update_session(
            "rec-1", agent_session_id="11111111-2222-3333-4444-555555555555",
        )
        with patch("common.sessions.resume_session",
                   return_value={"session": "rec-1", "action": "relaunched",
                                 "running": False}):
            out = session_state.recover_crashed_sessions()
        assert "rec-1" in out["crashed"]
        assert "rec-1" not in out["resumed"]

    def test_successful_resume_counts_as_resumed(
        self, patched_server, mock_tmux,
    ):
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("rec-2", "test-proj", "rec-2")
        patched_server._db.update_session(
            "rec-2", agent_session_id="22222222-3333-4444-5555-666666666666",
        )
        with patch("common.sessions.resume_session",
                   return_value={"session": "rec-2", "action": "resumed",
                                 "running": True}):
            out = session_state.recover_crashed_sessions()
        assert "rec-2" in out["resumed"]
        assert "rec-2" not in out["crashed"]

    def test_none_result_counts_as_skipped(self, patched_server, mock_tmux):
        """`None` means the row wasn't actionable (no uuid on the review
        row, etc.) -- distinct from an attempted-and-failed resume."""
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("rec-3", "test-proj", "rec-3")
        patched_server._db.update_session(
            "rec-3", agent_session_id="33333333-4444-5555-6666-777777777777",
        )
        with patch("common.sessions.resume_session", return_value=None):
            out = session_state.recover_crashed_sessions()
        assert "rec-3" in out["skipped"]

    def test_raising_resume_counts_as_crashed(self, patched_server, mock_tmux):
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("rec-4", "test-proj", "rec-4")
        patched_server._db.update_session(
            "rec-4", agent_session_id="44444444-5555-6666-7777-888888888888",
        )
        with patch("common.sessions.resume_session",
                   side_effect=RuntimeError("boom")):
            out = session_state.recover_crashed_sessions()
        assert "rec-4" in out["crashed"]

    def test_live_sessions_are_not_resumed(self, patched_server, mock_tmux):
        """Rows whose pane is still alive must be left alone -- resuming
        them would spawn a duplicate agent over a working session.

        `session_state` reaches tmux via `from adapters import tmux as
        _tmux`, which the shared `mock_tmux` fixture doesn't rebind, so
        patch the adapter attribute directly here."""
        patched_server._db.create_session("rec-5", "test-proj", "rec-5")
        patched_server._db.update_session(
            "rec-5", agent_session_id="55555555-6666-7777-8888-999999999999",
        )
        with patch("adapters.tmux.session_exists", return_value=True), \
             patch("common.sessions.resume_session") as m:
            out = session_state.recover_crashed_sessions()
        m.assert_not_called()
        assert "rec-5" not in out["resumed"]

    def test_review_with_dead_transcript_relaunches_and_clears_uuid(
        self, patched_server, mock_tmux, tmp_path, monkeypatch,
    ):
        """A review session whose transcript vanished must fall back to a
        fresh launch AND forget the unusable uuid -- otherwise the
        "transcript is gone" warning repeats on every single boot."""
        import common.sessions as _sessions
        monkeypatch.setattr(_sessions, "CLAUDE_PROJECTS_DIR", str(tmp_path))
        uuid = "abcdabcd-1111-2222-3333-444444444444"
        url = "https://github.com/apache/spark/pull/99999"
        name = "review-apache-spark-99999"
        # A real review row is required, otherwise `_resume_review` returns
        # None early and this test would silently exercise nothing.
        patched_server._db.upsert_review_pr(
            url, "apache/spark", 99999,
            session_name=name, agent_session_id=uuid,
        )
        # `mock_tmux` already blocks real tmux; this local patch is here to
        # inspect the argv that `_resume_review` chose.
        with patch("adapters.tmux.launch_session_argv") as launch:
            res = session_state._resume_review(name)
        assert res is not None, "review row should have been found"
        assert res["action"] == "relaunched"
        launch.assert_called_once()
        argv = launch.call_args.args[2]
        assert "--resume" not in argv, "must not resume a vanished transcript"
        row = patched_server._db.get_session(name)
        assert (row.get("agent_session_id") or "") == ""

    def test_rows_without_uuid_are_not_candidates(self, patched_server,
                                                  mock_tmux):
        mock_tmux["exists"].return_value = False
        patched_server._db.create_session("rec-6", "test-proj", "rec-6")
        with patch("common.sessions.resume_session") as m:
            session_state.recover_crashed_sessions()
        called = [c for c in m.call_args_list if c.args and c.args[0] == "rec-6"]
        assert not called
