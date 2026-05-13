"""Tests for core/common.reviews.py -- the review lifecycle module."""

import common
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ============================================================
# session_name_for -- stable URL-derived tmux name
# ============================================================


class TestSessionNameFor:
    """The session name is the primary key users see (tmux list-sessions,
    terminal header) and it's how eva-hook maps hook events back to a
    review row. Small contract, pin it down tight."""

    def test_standard_owner_repo_shape(self):
        from common.reviews import session_name_for
        assert session_name_for("example/repo", 12345) == \
            "review-example-repo-12345"

    def test_prevents_cross_org_collision(self):
        """Keeping `owner` in the slug stops `example/repo` and a
        hypothetical `myorg/repo` from sharing a tmux name."""
        from common.reviews import session_name_for
        a = session_name_for("example/repo", 100)
        b = session_name_for("myorg/repo", 100)
        assert a != b

    def test_normalises_special_chars(self):
        """tmux dislikes '.' in session names. The slug has to squash
        any non-alphanumeric owner/repo segments to '-'."""
        from common.reviews import session_name_for
        name = session_name_for("some.org/repo_weird!", 7)
        assert "." not in name
        assert "_" not in name
        assert "!" not in name

    def test_is_deterministic(self):
        """Idempotent: calling twice yields the same name. The flow
        relies on this (lookup -> launch is `name = session_name_for
        (...) ; if not alive: launch name`)."""
        from common.reviews import session_name_for
        assert session_name_for("a/b", 1) == session_name_for("a/b", 1)


# ============================================================
# open_review_session
# ============================================================


class TestOpenReviewSession:
    """Exercises the Phase-2 mainline: flip workflow state to 'active',
    stamp `started_at`, drop a history entry, launch tmux. Uses
    `patched_server` so we get an isolated DB + mock tmux."""

    def _seed(self, patched_server, url="https://github.com/example/repo/pull/42"):
        patched_server._db.upsert_review_pr(
            url=url, repo="example/repo", number=42,
            title="Fix foo", author="someone-else",
            status="open", last_updated="",
            source="github",
        )
        return url

    def test_rejects_unknown_review_url(self, patched_server):
        from common.reviews import open_review_session
        with pytest.raises(ValueError, match="not found"):
            open_review_session("https://github.com/nope/nope/pull/1")

    def test_rejects_non_review_action(self, patched_server):
        """Task-context actions (fix-ci, do-task, ...) must not be
        callable on a review -- the prompt template carries task-specific
        language and a 'review PR' session opening a task template would
        confuse agent."""
        url = self._seed(patched_server)
        from common.reviews import open_review_session
        with pytest.raises(ValueError, match="not a review action"):
            open_review_session(url, action_id="do-task")

    @patch("common.reviews.launch_session_argv")
    @patch("common.reviews.session_exists", return_value=False)
    def test_launches_tmux_and_stamps_state(
        self, _mock_exists, mock_launch, patched_server
    ):
        """Happy path: first Review-PR click flips state to active,
        stamps started_at, saves session_name, and launches tmux."""
        url = self._seed(patched_server)
        from common.reviews import open_review_session

        out = open_review_session(url, action_id="review-pr")
        assert out["session"] == "review-example-repo-42"
        assert out["new"] is True
        # Launch was called with the agent argv we expect (agent -n <name>).
        assert mock_launch.called
        argv = mock_launch.call_args[0][2]
        assert argv[0] in ("agent", "claude")  # active agent binary
        assert "-n" in argv
        assert "review-example-repo-42" in argv

        row = patched_server._db.get_review_pr(url)
        assert row["session_name"] == "review-example-repo-42"
        assert row["my_workflow_state"] == "active"
        assert row["started_at"]  # non-empty ISO

    @patch("common.reviews.launch_session_argv")
    @patch("common.reviews.session_exists", return_value=True)
    def test_resume_when_tmux_already_alive(
        self, _mock_exists, mock_launch, patched_server
    ):
        """If tmux already has this session, don't re-launch (idempotent
        across Review-PR button clicks). Still returns the prompt so
        the UI can re-send it."""
        url = self._seed(patched_server)
        patched_server._db.upsert_review_pr(
            url=url, repo="example/repo", number=42,
            session_name="review-example-repo-42",
            my_workflow_state="active",
            started_at="2026-04-24T10:00:00Z",
        )
        from common.reviews import open_review_session

        out = open_review_session(url, action_id="review-pr")
        assert out["new"] is False
        mock_launch.assert_not_called()

    @patch("common.reviews.launch_session_argv")
    @patch("common.reviews.session_exists", return_value=True)
    def test_emits_event_on_reclick_even_when_session_alive(
        self, _mock_exists, _mock_launch, patched_server
    ):
        """Regression: the second click on "Review PR" (session already
        live) used to emit no event. ReviewCard subscribes to the event
        to refresh, so no-event meant the UI never picked up the user's
        intent to resume work. Fire the event unconditionally."""
        url = self._seed(patched_server)
        patched_server._db.upsert_review_pr(
            url=url, repo="example/repo", number=42,
            session_name="review-example-repo-42",
        )
        import app_state

        fired: list[tuple] = []
        orig_emit = app_state.emit_event

        def capture(evt_type, payload, persist=True):
            fired.append((evt_type, payload))
            return orig_emit(evt_type, payload, persist=persist)

        app_state.emit_event = capture
        try:
            from common.reviews import open_review_session
            open_review_session(url, action_id="review-pr")
        finally:
            app_state.emit_event = orig_emit

        assert any(t == "review.session.opened" for t, _ in fired), (
            "re-click must still emit review.session.opened so UI refreshes"
        )

    @patch("common.reviews.launch_session_argv")
    @patch("common.reviews.session_exists", return_value=False)
    def test_preserves_started_at_on_second_launch(
        self, _mock_exists, _mock_launch, patched_server
    ):
        """`started_at` is the "when did I first touch this review"
        marker for workstats. Don't overwrite it on later opens."""
        url = self._seed(patched_server)
        patched_server._db.upsert_review_pr(
            url=url, repo="example/repo", number=42,
            started_at="2026-01-01T00:00:00Z",
        )
        from common.reviews import open_review_session
        open_review_session(url)
        row = patched_server._db.get_review_pr(url)
        assert row["started_at"] == "2026-01-01T00:00:00Z"

    @patch("common.reviews.launch_session_argv")
    @patch("common.reviews.session_exists", return_value=False)
    def test_appends_history_on_first_launch(
        self, _mock_exists, _mock_launch, patched_server
    ):
        """The review history timeline is what makes "what did I do on
        this review last week" queryable. Session launch must write an
        entry so even the happy path shows something."""
        url = self._seed(patched_server)
        from common.reviews import open_review_session
        open_review_session(url, action_id="review-pr")
        entries = patched_server._db.list_review_history(url)
        assert any(e["source"] == "system" for e in entries)
        assert any("session started" in e["text"] for e in entries)

    @patch("common.reviews.launch_session_argv")
    @patch("common.reviews.session_exists", return_value=False)
    def test_pr_context_lives_in_system_prompt(
        self, _mock_exists, mock_launch, patched_server
    ):
        """PR metadata (repo#N, URL, history-log hint) is injected as
        Claude's system prompt via `--append-system-prompt` -- so it
        survives /clear and resume. The returned `prompt` is just the
        action template (user message)."""
        url = self._seed(patched_server)
        from common.reviews import open_review_session
        out = open_review_session(url, action_id="review-pr")
        # System prompt is the value passed after `--append-system-prompt`
        # in the launched argv.
        argv = mock_launch.call_args[0][2]
        idx = argv.index("--append-system-prompt")
        sysprompt = argv[idx + 1]
        assert "example/repo" in sysprompt
        assert "#42" in sysprompt or "42" in sysprompt
        assert "append-review-history" in sysprompt
        # The returned `prompt` (typed into the TUI as user message)
        # is the action template, not the system context.
        assert out["prompt"]
        assert "example/repo" not in out["prompt"]


# ============================================================
# update_review
# ============================================================


class TestUpdateReview:
    """Whitelisted field-patch for reviewer-owned columns."""

    def _seed(self, patched_server):
        url = "https://github.com/example/repo/pull/99"
        patched_server._db.upsert_review_pr(
            url=url, repo="example/repo", number=99,
            title="x", author="other", status="open",
            last_updated="", source="github",
        )
        return url

    def test_rejects_unknown_url(self, patched_server):
        from common.reviews import update_review
        with pytest.raises(ValueError, match="not found"):
            update_review("https://github.com/nope/nope/pull/1",
                          my_workflow_state="done")

    def test_updates_workflow_state(self, patched_server):
        url = self._seed(patched_server)
        from common.reviews import update_review
        row = update_review(url, my_workflow_state="done")
        assert row["my_workflow_state"] == "done"

    def test_ignores_fields_not_in_allow_list(self, patched_server):
        """A caller passing e.g. `title=...` must not be able to smuggle
        writes to PR-metadata columns through the reviewer patch route."""
        url = self._seed(patched_server)
        from common.reviews import update_review
        row = update_review(url, title="HACKED",
                            my_workflow_state="done")
        # title was ignored; workflow was applied.
        assert row["title"] == "x"
        assert row["my_workflow_state"] == "done"

    def test_rejects_invalid_workflow_state(self, patched_server):
        """`my_workflow_state` is an enum; a bad value must raise so
        the API can return 422 instead of silently writing garbage."""
        url = self._seed(patched_server)
        from common.reviews import update_review
        with pytest.raises(ValueError, match="my_workflow_state"):
            update_review(url, my_workflow_state="in-progress")


# ============================================================
# build_review_system_prompt (pure helper)
# ============================================================


class TestBuildReviewSystemPrompt:
    """Pure helper that assembles the [Background] block injected as
    Claude's system prompt at review-session launch time. Mirrors
    `common.sessions.build_background_system` for review context."""

    def test_assembles_header_with_repo_number_url_title(self):
        """The system prompt gives agent PR identity (repo#N, URL,
        title) plus status / meta / progress-logging hint."""
        from common.reviews import build_review_system_prompt
        out = build_review_system_prompt(
            {"repo": "example/repo", "number": 42,
             "url": "https://github.com/example/repo/pull/42",
             "title": "EX-0 fix null handling"},
        )
        assert "[Review] example/repo#42: EX-0 fix null handling" in out
        assert "[URL] https://github.com/example/repo/pull/42" in out
        # Closing hint tells the agent to log back via CLI
        assert "eva-cli append-review-history" in out
        assert "https://github.com/example/repo/pull/42" in out

    def test_tolerates_missing_fields_without_keyerror(self):
        """The template uses `.get()` defaults, so a minimal pr_row
        dict shouldn't blow up."""
        from common.reviews import build_review_system_prompt
        out = build_review_system_prompt({})
        # Template still assembles; missing fields show as empty strings.
        assert out.startswith("[Review]")
        assert "[Tools]" in out
        assert "中文" in out


class TestUpdateReviewNoOp:
    def test_no_allowed_fields_returns_row_unchanged(self, patched_server):
        """When only blacklisted fields are passed (e.g. title), the
        whitelist drops them all -> updates dict is empty -> function
        short-circuits and returns the current row without any DB write.
        Covers the `if not updates: return pr_row` branch."""
        from common.reviews import update_review
        url = "https://github.com/a/b/pull/99"
        patched_server._db.upsert_review_pr(
            url=url, repo="a/b", number=99, source="manual",
            title="noop", my_workflow_state="queued",
        )
        row = update_review(url, title="ignored", author="ignored")
        assert row["title"] == "noop"
        assert row["my_workflow_state"] == "queued"


class TestOpenReviewSessionActionNotFound:
    def test_action_row_missing_in_db_raises(self, patched_server, monkeypatch):
        """REVIEW_ACTIONS lists the id as valid, but if the
        action_definitions row is missing (DB drift), open_review_session
        must raise instead of launching a tmux with an undefined prompt.
        Covers line 97 -- the post-whitelist DB-lookup guard."""
        url = "https://github.com/a/b/pull/21"
        patched_server._db.upsert_review_pr(
            url=url, repo="a/b", number=21, source="github",
            title="x", author="bob", status="open",
        )
        # Force get_action to return None for this action id even
        # though the constant says it's a valid review action.
        monkeypatch.setattr(
            patched_server._db, "get_action", lambda _id: None,
        )
        from common.reviews import open_review_session
        with pytest.raises(ValueError, match="Action not found"):
            open_review_session(url, action_id="review-pr")


class TestOpenReviewSessionHistoryFailureSwallowed:
    """`append_review_history` rejects oversize text with ValueError.
    Launch must NOT fail just because the audit-log entry got rejected
    -- the session is already up. Covers the inner try/except."""

    def test_history_value_error_does_not_break_launch(
        self, patched_server, monkeypatch,
    ):
        url = "https://github.com/a/b/pull/22"
        patched_server._db.upsert_review_pr(
            url=url, repo="a/b", number=22, source="github",
            title="x", author="bob", status="open",
        )
        monkeypatch.setattr(
            "adapters.tmux.session_exists", lambda _n: False,
        )
        monkeypatch.setattr(
            "common.reviews.launch_session_argv", lambda *a, **k: None,
        )
        # Force append_review_history to raise ValueError, simulating
        # the >100-char rejection path or a corrupted DB row.
        def boom(*a, **kw):
            raise ValueError("history text too long")
        monkeypatch.setattr(
            patched_server._db, "append_review_history", boom,
        )
        # Should NOT raise; session opened successfully despite the
        # failed history-write side effect.
        from common.reviews import open_review_session
        out = open_review_session(url, action_id="review-pr")
        assert out["session"] == "review-a-b-22"
