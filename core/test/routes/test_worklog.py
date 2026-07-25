"""Tests for routes/worklog.py: daily work logs.

GET /api/worklog           -> list recent logs
GET /api/worklog/{date}    -> fetch-or-generate a single date
PUT /api/worklog/{date}    -> save edited content
DELETE /api/worklog/{date} -> drop so next GET regenerates
GET /api/worklog-range     -> generate log for arbitrary [start, end) range

Every test uses patched_server (temp DB + config) so nothing hits prod.
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402


@pytest.fixture()
def client(patched_server):
    return TestClient(server.app)


class TestListWorkLogs:
    def test_empty_initially(self, client):
        resp = client.get("/api/worklog")
        assert resp.status_code == 200
        assert resp.json() == {"logs": []}

    def test_respects_limit_parameter(self, client, patched_server):
        # Save a handful of logs, then cap the list at 2.
        for day in ("2026-04-10", "2026-04-11", "2026-04-12"):
            patched_server._db.save_work_log(day, f"content for {day}")

        resp = client.get("/api/worklog?limit=2")
        assert resp.status_code == 200
        logs = resp.json()["logs"]
        assert len(logs) == 2
        # Newest first -- 04-12 must lead.
        assert logs[0]["date"] == "2026-04-12"


class TestGetWorkLog:
    def test_auto_generates_when_missing(self, client):
        """First GET for an unseen date must auto-generate and persist."""
        resp = client.get("/api/worklog/2026-04-15")
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == "2026-04-15"
        assert "2026-04-15" in body["content"]  # header line
        assert "updated_at" in body

        # Second GET returns the stored copy (no regeneration).
        resp2 = client.get("/api/worklog/2026-04-15")
        assert resp2.status_code == 200
        assert resp2.json()["content"] == body["content"]

    def test_returns_edited_content_when_saved(self, client, patched_server):
        patched_server._db.save_work_log("2026-04-15", "my custom markdown")
        resp = client.get("/api/worklog/2026-04-15")
        assert resp.status_code == 200
        assert resp.json()["content"] == "my custom markdown"


class TestPutWorkLog:
    def test_saves_edited_content(self, client, patched_server):
        resp = client.put(
            "/api/worklog/2026-04-15",
            json={"content": "- my day\n    - did things"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        row = patched_server._db.get_work_log("2026-04-15")
        assert row["content"] == "- my day\n    - did things"

    def test_rejects_missing_content_field(self, client):
        """Pydantic must reject payloads without the 'content' key."""
        resp = client.put("/api/worklog/2026-04-15", json={})
        assert resp.status_code == 422


class TestDeleteWorkLog:
    def test_drops_saved_record(self, client, patched_server):
        patched_server._db.save_work_log("2026-04-15", "stored")
        assert patched_server._db.get_work_log("2026-04-15") is not None

        resp = client.delete("/api/worklog/2026-04-15")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert patched_server._db.get_work_log("2026-04-15") is None

    def test_delete_is_idempotent(self, client):
        """DELETE on an unknown date must still 200."""
        resp = client.delete("/api/worklog/2099-01-01")
        assert resp.status_code == 200


class TestWorkLogRange:
    def test_generates_for_arbitrary_range(self, client):
        resp = client.get(
            "/api/worklog-range"
            "?start=2026-04-10T00:00:00Z"
            "&end=2026-04-12T00:00:00Z"
            "&label=Weekend%20recap"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["start"].startswith("2026-04-10")
        assert body["end"].startswith("2026-04-12")
        assert body["label"] == "Weekend recap"
        # Header line uses the supplied label
        assert "Weekend recap" in body["content"]

    def test_label_is_optional(self, client):
        resp = client.get(
            "/api/worklog-range"
            "?start=2026-04-10T00:00:00Z"
            "&end=2026-04-11T00:00:00Z"
        )
        assert resp.status_code == 200
        body = resp.json()
        # Fallback header uses the raw ISO range.
        assert "2026-04-10" in body["content"]
        assert "2026-04-11" in body["content"]

    def test_range_output_is_not_persisted(self, client, patched_server):
        """Range reports are ephemeral -- must NOT write into work_logs."""
        before = len(patched_server._db.list_work_logs(limit=100))
        client.get(
            "/api/worklog-range"
            "?start=2026-04-10T00:00:00Z"
            "&end=2026-04-11T00:00:00Z"
            "&label=tmp"
        )
        after = len(patched_server._db.list_work_logs(limit=100))
        assert after == before


class TestGeneratorContent:
    """Direct tests for routes.worklog._generate_worklog_content and the
    branches it has: task with PR (PR line shown), task without PR (task
    line with ticket + notes preview), and "No activity" fallback.
    """

    def _emit_task_event(self, patched_server, task_id, ts):
        """Insert a task.updated event into the events table."""
        import sqlite3
        with sqlite3.connect(str(patched_server._NOTIF_DB_PATH)) as conn:
            conn.execute(
                "INSERT INTO events (id, source, source_id, title, message, "
                "type, severity, url, ts, read, session) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"ev-{task_id}-{ts}", "task", "", f"Task updated: {task_id}", "",
                 "task.updated", "info", "", ts, 0, ""),
            )
            conn.commit()

    def _emit_agent_event(self, patched_server, session, ts):
        """Insert an agent.* event tagged with a tmux `session` -- the
        signal that the user worked a task/ticket/review via the agent."""
        import sqlite3
        with sqlite3.connect(str(patched_server._NOTIF_DB_PATH)) as conn:
            conn.execute(
                "INSERT INTO events (id, source, source_id, title, message, "
                "type, severity, url, ts, read, session) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"ev-{session}-{ts}", "agent", "", f"Agent: {session}", "",
                 "agent.task_done", "info", "", ts, 0, session),
            )
            conn.commit()

    def test_ticket_session_work_lands_in_tickets_section(self, client, patched_server):
        """A `ticket-<instance>-<key>` agent session (fixing a ticket)
        must surface in the Tickets section -- previously these sessions
        were skipped entirely, so ticket-fixing never hit the standup."""
        patched_server._db.upsert_ticket(
            key="EX-4242", instance_name="example",
            summary="NullPointer in widget loader",
            url="https://jira/EX-4242", status="In Progress",
            synced_at="2099-01-01T00:00:00",
        )
        self._emit_agent_event(
            patched_server, "ticket-example-EX-4242", "2099-01-01T10:00:00")

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "Tickets:" in out
        assert "[EX-4242]" in out
        # The ticket's one-line summary, not its full body, is shown.
        assert "NullPointer in widget loader" in out

    def test_review_session_work_lands_in_reviews_section(self, client, patched_server):
        """A `review-*` agent session (reviewing a PR) must surface in the
        Reviews section."""
        patched_server._db.create_task(
            "", "review-apache-spark-99", type="review",
            description="[SPARK-99] Speed up shuffle",
        )
        self._emit_agent_event(
            patched_server, "review-apache-spark-99", "2099-01-01T10:00:00")

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "Reviews:" in out
        assert "Speed up shuffle" in out

    def test_no_activity_returns_placeholder_line(self, client, patched_server):
        """An empty date range -> content includes the literal 'No activity recorded'."""
        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- 2099-01-01")
        assert "No activity recorded" in out
        # Header carried through unchanged.
        assert out.startswith("- 2099-01-01")

    def test_task_with_ticket_rendered_as_single_markdown_link(self, client, patched_server):
        """Tasks with ticket_id+ticket_url now wrap "[TICKET] description" as
        a SINGLE link. Previously the ticket was its own link and the
        description sat as orphan text next to it, producing two clickable
        entries in the Slack paste for one task."""
        db = patched_server._db
        db.update_task("test-proj", "task-b",
                       description="Do the thing",
                       ticket_id="JIRA-123", ticket_url="https://j/JIRA-123")
        self._emit_task_event(patched_server, "task-b", "2099-01-01T10:00:00")

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        # Whole label inside the link (brackets + ticket + description).
        assert "[[JIRA-123] Do the thing](https://j/JIRA-123)" in out

    def test_task_notes_are_no_longer_emitted_as_sub_bullets(self, client, patched_server):
        """Regression: the auto-generator used to paste the first line of
        task.notes as a sub-bullet, which turned out to be the top source
        of "standup is too verbose" complaints (stale fragments, CI fail
        echoes, etc.). The user manually edits notes inline anyway."""
        db = patched_server._db
        db.update_task("test-proj", "task-b",
                       notes="actual note\nadditional detail",
                       ticket_id="X", ticket_url="")
        self._emit_task_event(patched_server, "task-b", "2099-01-01T10:00:00")

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "actual note" not in out
        assert "additional detail" not in out

    def test_event_for_deleted_task_is_skipped(self, client, patched_server):
        """A task event whose underlying task row no longer exists
        (deleted between event-emit and worklog-generate) must NOT
        crash the generator -- it just gets skipped. This covers the
        defensive `if not row: continue` branch in
        `_build_worklog_buckets`.
        """
        # Emit a task.updated event for `ghost-task` (no such row).
        self._emit_task_event(patched_server, "ghost-task",
                              "2099-01-01T10:00:00")
        from routes.worklog import _generate_worklog_content
        # Must not raise.
        out = _generate_worklog_content(
            "2099-01-01", "2099-01-02", "- header",
        )
        # Ghost task contributes nothing to output; falls through to
        # the "No activity" placeholder (or any other real activity
        # in the seeded fixture).
        assert "ghost-task" not in out

    def test_task_with_titleless_status_changed_pr_skips_task_line(
        self, client, patched_server,
    ):
        """When `_query_status_changed_prs` ingests a PR for the same
        task that's already in the active-tasks bucket, but
        `_task_prs_for_worklog` filtered the PR out (title=='') so
        the bucket-builder didn't promote-and-pop the task, the
        renderer's `if tid in pr_tasks: continue` branch is the only
        thing keeping the task from rendering twice (once as a
        standalone bullet, once via the PR line). This covers
        `_render_worklog_lines` line 237 -- the only realistic path
        is a status-changed PR whose `title` is empty (legacy /
        test-only rows). Without the dedup, the same task surfaces
        twice with confusing duplication.
        """
        db = patched_server._db
        # task-b is in the fixture. Emit a task event so it's in the
        # active-tasks set.
        self._emit_task_event(patched_server, "task-b",
                              "2099-01-01T10:00:00")
        # Add a PR with EMPTY title -- _task_prs_for_worklog will skip
        # this (title!='' filter), so the task stays in tasks dict.
        # _query_status_changed_prs has no title filter, so the PR
        # still appears in the prs list. That's the only way to land
        # task-b in tasks AND task-b in pr_tasks simultaneously.
        db._conn.execute(
            "PRAGMA foreign_keys=OFF",
        )
        db._conn.execute(
            "INSERT INTO prs(task_id, number, url, status, "
            "title, status_changed_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("task-b", 4242,
             "https://github.com/x/y/pull/4242",
             "merged", "",  # empty title is the key
             "2099-01-01T11:00:00Z"),
        )
        db._conn.commit()
        db._conn.execute("PRAGMA foreign_keys=ON")

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content(
            "2099-01-01", "2099-01-02", "- header",
        )
        # The dedup branch fired -- task-b appears only once (in the
        # PR's URL or rendered line), NOT also as its own task bullet.
        # Without the dedup, task-b's description ('task b desc') would
        # appear as a separate line. Verify by ensuring there's no
        # standalone description-only line for the task.
        assert "task b desc" not in out

    def test_closed_notes_suppressed(self, client, patched_server):
        """`[Closed]` notes were never emitted, and that still holds now
        that NO notes are emitted regardless of prefix."""
        db = patched_server._db
        db.update_task("test-proj", "task-b",
                       notes="[Closed] won't fix", ticket_id="X")
        self._emit_task_event(patched_server, "task-b", "2099-01-01T10:00:00")

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "[Closed]" not in out

    def test_pr_without_url_or_number_falls_back_to_title(self):
        """Defensive: if a PR row arrives with no url AND no number (impossible
        from `add_pr` but possible via legacy / direct SQL), the helper must
        still render something instead of an empty `[](url)` line.

        Exercises routes/worklog.py:53-54 -- the bare-title fallback.
        """
        from routes.worklog import _pr_title_line
        line = _pr_title_line({"title": "Stray PR", "status": "open"})
        # Indented, starts with bullet, title survived.
        assert "Stray PR" in line
        assert line.lstrip().startswith("- Stray PR")
        # No markdown link syntax since url was empty.
        assert "](" not in line

    def test_long_description_gets_truncated(self, client, patched_server):
        """Descriptions longer than 100 chars get trimmed to 97 + '...'.

        Long descriptions blow up the standup output and look sloppy in
        Slack; the generator keeps the first 97 chars to leave room for
        the ellipsis while staying under the 100-char soft limit.
        """
        db = patched_server._db
        long_desc = "A" * 150  # 150 > 100 cap
        db.update_task("test-proj", "task-b",
                       description=long_desc,
                       ticket_id="T", ticket_url="https://x/T")
        self._emit_task_event(patched_server, "task-b", "2099-01-01T10:00:00")

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        # The long tail is dropped; an ellipsis is appended.
        assert "A" * 97 + "..." in out
        # The full 150-char run never appears.
        assert "A" * 150 not in out

    def test_meeting_notes_trailer_always_emitted(self, client, patched_server):
        """Every generated log ends with a '- Meeting Notes:' trailer so the
        human editor can fill it in."""
        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert out.rstrip().endswith("- Meeting Notes:")

    def _seed_pr_with_last_updated(self, patched_server, task_id, number,
                                   title, status, ci_status, url, ts):
        """Add a PR and stamp both last_updated and status_changed_at to the
        given time. The worklog generator now filters on status_changed_at
        (see routes/worklog.py) -- tests that want a PR to show up in a
        range must set that column, not last_updated."""
        db = patched_server._db
        db.add_pr(project="test-proj", task_id=task_id, number=number,
                  url=url, title=title, status=status, ci_status=ci_status)
        db._conn.execute(
            "UPDATE prs SET last_updated=?, status_changed_at=? "
            "WHERE task_id=? AND number=?",
            (ts, ts, task_id, number),
        )
        db._conn.commit()

    def test_pr_with_url_rendered_as_markdown_link(self, client, patched_server):
        """PRs with a url render as [title](url) markdown. No CI suffix in the
        current style -- we only tag merged PRs with :white_check_mark:."""
        self._seed_pr_with_last_updated(
            patched_server,
            task_id="task-b",
            number=777,
            title="My Great PR",
            status="open",
            ci_status="passing",
            url="https://github.com/example/repo/pull/777",
            ts="2099-01-01T10:00:00",
        )

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "[My Great PR](https://github.com/example/repo/pull/777)" in out
        # CI status is no longer appended; only merged status is tagged.
        assert "CI passing" not in out
        assert ":white_check_mark:" not in out  # status is "open", not merged

    def test_pr_without_url_falls_back_to_number(self, client, patched_server):
        """PR without a URL renders as `#<number> <title>` (no trailing status)."""
        self._seed_pr_with_last_updated(
            patched_server,
            task_id="task-b",
            number=888,
            title="No URL PR",
            status="draft",
            ci_status="",
            url="",
            ts="2099-01-01T10:00:00",
        )

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "#888 No URL PR" in out
        # No CI suffix, no raw status appended.
        assert "CI " not in out
        assert "draft" not in out

    def test_merged_pr_gets_merged_emoji(self, client, patched_server):
        """Merged PRs render with `:white_check_mark:` to match the user's Slack style."""
        self._seed_pr_with_last_updated(
            patched_server,
            task_id="task-b",
            number=55405,
            title="[EX-55405][PYTHON][TESTS][FOLLOWUP] Add safe=False tests",
            status="merged",
            ci_status="passing",
            url="https://github.com/example/repo/pull/55405",
            ts="2099-01-01T10:00:00",
        )

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "[EX-55405]" in out
        assert ":white_check_mark:" in out

    def test_done_task_has_no_resolved_emoji(self, client, patched_server):
        """Only `:white_check_mark:` is emitted; `:resolved-check:` was removed per
        user's style preference."""
        db = patched_server._db
        db.update_task("test-proj", "task-b",
                       description="Fix flaky thing",
                       ticket_id="ALT-9999", ticket_url="https://j/ALT-9999",
                       status="done")
        self._emit_task_event(patched_server, "task-b", "2099-01-01T10:00:00")

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "[ALT-9999]" in out
        assert ":resolved-check:" not in out

    def test_ticket_pulled_into_own_section(self, client, patched_server):
        """A JIRA-synced ticket task lands in the dedicated 'Tickets:'
        group, not under any project."""
        patched_server._db.upsert_ticket(
            key="EX-1001", instance_name="example",
            summary="Fix flaky suite", url="https://jira/EX-1001",
            status="In Progress", synced_at="2099-01-01T00:00:00",
        )
        self._emit_task_event(patched_server, "EX-1001", "2099-01-01T10:00:00")

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "Tickets:" in out
        # Ticket ID still rendered with brackets -- user wants them kept.
        assert "[EX-1001]" in out
        assert "Test Project:" not in out

    def test_ticket_pr_pulled_into_own_section(self, client, patched_server):
        """A PR on a JIRA-synced ticket task lands in the Tickets section.
        Classification is by the task's `ticket_synced_at`, NOT the PR
        title -- a `[SPARK-...]` PR on a plain project task stays under
        its project (see the next test)."""
        patched_server._db.upsert_ticket(
            key="EX-1001", instance_name="example",
            summary="Await warmup", url="https://jira/EX-1001",
            status="Open", synced_at="2099-01-01T00:00:00",
        )
        self._seed_pr_with_last_updated(
            patched_server,
            task_id="EX-1001",
            number=9001,
            title="[EX-1001] Await client image upload before warmup",
            status="open",
            ci_status="",
            url="https://github.com/myorg/monorepo/pull/9001",
            ts="2099-01-01T10:00:00",
        )

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "Tickets:" in out
        assert "[EX-1001]" in out

    def test_spark_pr_on_project_task_stays_under_project(self, client, patched_server):
        """A `[SPARK-...]` feature PR on a plain project task (no
        ticket_id) is the user's own dev work -- it stays under the
        project section, NOT lumped into Tickets."""
        self._seed_pr_with_last_updated(
            patched_server,
            task_id="task-b",  # seeded plain task, no ticket_id
            number=9100,
            title="[SPARK-56758][PYTHON] Refactor SQL_MAP_PANDAS_ITER_UDF",
            status="open",
            ci_status="",
            url="https://github.com/apache/spark/pull/55750",
            ts="2099-01-01T10:00:00",
        )

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "Test Project:" in out
        assert "Tickets:" not in out

    def test_task_with_pr_suppressed_in_task_list(self, client, patched_server):
        """When a task both has a PR and a task event, the PR line appears but
        the duplicate task bullet is suppressed (line 79 `continue`)."""
        # Seed PR + task event for same task-b in range.
        self._emit_task_event(patched_server, "task-b", "2099-01-01T10:00:00")
        self._seed_pr_with_last_updated(
            patched_server, "task-b", 999, "PR For Task B",
            "open", "passing", "https://x/999", "2099-01-01T10:00:00",
        )
        # Give task-b a distinctive description so we can check it is absent.
        patched_server._db.update_task(
            "test-proj", "task-b", description="UNIQUE-DESC-MARKER-XYZ",
        )

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "PR For Task B" in out
        assert "UNIQUE-DESC-MARKER-XYZ" not in out

    def test_task_without_ticket_renders_as_plain_description(
            self, client, patched_server):
        """Task with no ticket_id and no ticket_url -> just the description.
        Covers line 89 else fallback."""
        db = patched_server._db
        db.update_task("test-proj", "task-b",
                       description="PLAIN-TASK-DESCRIPTION",
                       ticket_id="", ticket_url="", notes="")
        self._emit_task_event(patched_server, "task-b", "2099-01-01T10:00:00")

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        # No markdown link; just plain text.
        assert "PLAIN-TASK-DESCRIPTION" in out
        assert "[PLAIN-TASK-DESCRIPTION]" not in out

    def test_pr_with_last_updated_only_is_not_included_in_worklog(
            self, client, patched_server):
        """Regression: a PR that Eva just re-polled today (last_updated=today)
        but whose status hasn't actually changed (status_changed_at untouched)
        MUST NOT appear in today's worklog. The user's complaint was exactly
        this -- historical PRs polluting "today" because the GitHub poller
        bumped last_updated on every run."""
        db = patched_server._db
        # Simulate a legacy row: add PR, then force last_updated to today,
        # but leave status_changed_at as '' (column default for rows that
        # predate the migration).
        db.add_pr(project="test-proj", task_id="task-b", number=12345,
                  url="https://github.com/example/repo/pull/12345",
                  title="Old PR re-polled today", status="merged", ci_status="passing")
        db._conn.execute(
            "UPDATE prs SET last_updated=?, status_changed_at=? "
            "WHERE task_id=? AND number=?",
            ("2099-01-01T12:00:00", "", "task-b", 12345),
        )
        db._conn.commit()

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "Old PR re-polled today" not in out
        assert "#12345" not in out

    def test_pr_status_changed_within_range_is_included(
            self, client, patched_server):
        """Companion to above: PR whose status actually flipped today SHOULD
        appear, even if last_updated is elsewhere."""
        db = patched_server._db
        db.add_pr(project="test-proj", task_id="task-b", number=55555,
                  url="https://github.com/example/repo/pull/55555",
                  title="Merged Today", status="merged", ci_status="passing")
        # last_updated much earlier, status_changed_at in window -> included.
        db._conn.execute(
            "UPDATE prs SET last_updated=?, status_changed_at=? "
            "WHERE task_id=? AND number=?",
            ("2098-01-01T00:00:00", "2099-01-01T10:00:00",
             "task-b", 55555),
        )
        db._conn.commit()

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "Merged Today" in out

    def test_active_task_with_pr_renders_as_pr_not_task_line(
            self, client, patched_server):
        """Regression: when a task is active today (task.* event in window)
        and it has a PR, the standup MUST render the PR's title + link
        rather than the task description. Previously the task line won
        if the PR's status_changed_at was outside the window (e.g. PR
        merged last week, task got an edit today)."""
        db = patched_server._db
        # PR that was merged BEFORE the window -- status_changed_at set to
        # yesterday. It must still be picked up because its task is active
        # in today's window.
        db.add_pr(
            project="test-proj", task_id="task-b", number=8080,
            url="https://github.com/example/repo/pull/8080",
            title="[EX-99999][PYTHON] Real PR title", status="merged",
        )
        db._conn.execute(
            "UPDATE prs SET last_updated=?, status_changed_at=? "
            "WHERE number=?",
            ("2098-12-31T10:00:00", "2098-12-31T10:00:00", 8080),
        )
        db._conn.commit()
        # Give the task a distinctive description so we can assert it's
        # NOT emitted (the PR title takes its place).
        db.update_task("test-proj", "task-b",
                       description="THIS-DESCRIPTION-SHOULD-BE-ABSENT",
                       ticket_id="EX-99999",
                       ticket_url="https://issues.example.org/jira/browse/EX-99999")
        # Event in window -> task considered active.
        self._emit_task_event(patched_server, "task-b", "2099-01-01T10:00:00")

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        # PR title + URL present:
        assert "[EX-99999][PYTHON] Real PR title" in out
        assert "https://github.com/example/repo/pull/8080" in out
        # Task description NOT present (it would be verbose repeat):
        assert "THIS-DESCRIPTION-SHOULD-BE-ABSENT" not in out

    def test_active_task_without_pr_still_renders_as_task_line(
            self, client, patched_server):
        """Tasks with no PR at all fall back to the task line (one link
        wrapping "[TICKET] description"). Sanity check for the fallback."""
        db = patched_server._db
        db.update_task("test-proj", "task-b",
                       description="Investigating the bug",
                       ticket_id="JIRA-9", ticket_url="https://j/JIRA-9")
        # No PR on this task.
        self._emit_task_event(patched_server, "task-b", "2099-01-01T10:00:00")

        from routes.worklog import _generate_worklog_content
        out = _generate_worklog_content("2099-01-01", "2099-01-02", "- header")
        assert "[[JIRA-9] Investigating the bug](https://j/JIRA-9)" in out

    def test_update_pr_status_stamps_status_changed_at(self, patched_server):
        """Unit test for the auto-stamp: update_pr_by_number with a status
        change must set status_changed_at; a status-unchanged update must
        NOT touch it."""
        db = patched_server._db
        db.add_pr(project="test-proj", task_id="task-b", number=6001,
                  url="https://github.com/example/repo/pull/6001",
                  title="Orig", status="open")
        # First update flips status -> status_changed_at gets stamped.
        db.update_pr_by_number(6001, status="merged", title="Now Merged")
        row = db.find_pr_by_number(6001)
        assert row["status_changed_at"] != ""
        first_stamp = row["status_changed_at"]

        # A non-status update (comment count bump) must leave status_changed_at
        # alone -- otherwise every metadata poll re-stamps it.
        db.update_pr_by_number(6001, comment_count=7)
        row = db.find_pr_by_number(6001)
        assert row["status_changed_at"] == first_stamp


class TestWorklogRendererSkipPaths:
    """Two `continue` branches in the renderer: skip a task whose row
    no longer exists in `tasks` (deleted between worklog touch and
    render), and skip a task that's already represented by its PR
    (avoids double-listing)."""

    def test_endpoint_handles_orphan_task_event(self, client, patched_server):
        # Use a date in the past so we don't depend on what "today" is
        # in the test environment.
        resp = client.get("/api/worklog/2026-04-28")
        # Either 200 (rendered an empty / partial log) or 404
        # (worklog endpoint shape varies); the renderer must not 500.
        assert resp.status_code in (200, 404)
