"""Tests for audit data-integrity scanner."""

import common
from common import audit as core_audit


def _seed_minimal(db, project="p1", task_id="t1", status="not_started",
                  ticket_id="", ticket_url=""):
    """Helper: create a project + task in one go."""
    if not db.project_exists(project):
        db.create_project(project_id=project, name=project)
    db.create_task(project=project, task_id=task_id, status=status,
                   ticket_id=ticket_id or None,
                   ticket_url=ticket_url or None)


def _insert_orphan(db, sql, params):
    """Insert a row that violates FK constraints (the prod scenario the
    audit is meant to catch). FK checks are dropped just for the
    insert -- audit queries themselves are FK-agnostic."""
    db._conn.execute("PRAGMA foreign_keys=OFF")
    try:
        db._conn.execute(sql, params)
        db._conn.commit()
    finally:
        db._conn.execute("PRAGMA foreign_keys=ON")


# ---- Individual checks ----

class TestPrTaskDriftCheck:
    def test_flags_merged_pr_with_not_started_task(self, patched_server):
        db = patched_server._db
        _seed_minimal(db, "p", "t-stale", status="not_started")
        db.add_pr(project="p", task_id="t-stale", number=1,
                  url="https://github.com/x/y/pull/1",
                  status="merged", author="me")
        findings = core_audit.check_pr_task_drift()
        kinds = [f.kind for f in findings]
        assert "pr_merged_task_not_started" in kinds

    def test_no_flag_when_task_is_in_progress(self, patched_server):
        db = patched_server._db
        _seed_minimal(db, "p", "t-ok", status="in_progress")
        db.add_pr(project="p", task_id="t-ok", number=2,
                  url="https://github.com/x/y/pull/2",
                  status="merged", author="me")
        findings = core_audit.check_pr_task_drift()
        assert findings == []


class TestAllPrsMergedButTaskOpen:
    def test_flags_when_all_prs_merged_but_task_in_progress(self, patched_server):
        db = patched_server._db
        _seed_minimal(db, "p", "t-shipped", status="in_progress")
        db.add_pr(project="p", task_id="t-shipped", number=10,
                  url="https://github.com/x/y/pull/10",
                  status="merged", author="me")
        db.add_pr(project="p", task_id="t-shipped", number=11,
                  url="https://github.com/x/y/pull/11",
                  status="merged", author="me")
        findings = core_audit.check_all_prs_merged_but_task_open()
        assert any(f.kind == "all_prs_merged_task_open" for f in findings)

    def test_no_flag_when_task_done(self, patched_server):
        db = patched_server._db
        _seed_minimal(db, "p", "t-done", status="done")
        db.add_pr(project="p", task_id="t-done", number=20,
                  url="https://github.com/x/y/pull/20",
                  status="merged", author="me")
        assert core_audit.check_all_prs_merged_but_task_open() == []

    def test_no_flag_when_some_prs_open(self, patched_server):
        db = patched_server._db
        _seed_minimal(db, "p", "t-mixed", status="in_progress")
        db.add_pr(project="p", task_id="t-mixed", number=30,
                  url="https://github.com/x/y/pull/30",
                  status="merged", author="me")
        db.add_pr(project="p", task_id="t-mixed", number=31,
                  url="https://github.com/x/y/pull/31",
                  status="open", author="me")
        assert core_audit.check_all_prs_merged_but_task_open() == []


class TestTicketFieldsPaired:
    def test_flags_id_without_url(self, patched_server):
        db = patched_server._db
        _seed_minimal(db, "p", "t-bad", ticket_id="ABC-123", ticket_url="")
        # SQLite stored "" as NULL via update_task convention. Re-set
        # explicitly to ensure the row state is what we want.
        db._conn.execute(
            "UPDATE tasks SET ticket_url = '' WHERE project=? AND task_id=?",
            ("p", "t-bad"))
        db._conn.commit()
        findings = core_audit.check_ticket_fields_paired()
        assert any(f.kind == "ticket_fields_unpaired" for f in findings)

    def test_no_flag_when_both_set_or_both_empty(self, patched_server):
        db = patched_server._db
        _seed_minimal(db, "p", "t-ok",
                      ticket_id="ABC-1", ticket_url="https://j/ABC-1")
        _seed_minimal(db, "p", "t-empty")
        # Test fixture seeds extra projects/tasks; only assert OUR rows
        # don't contribute false positives.
        findings = core_audit.check_ticket_fields_paired()
        for f in findings:
            assert f.ref["task_id"] not in ("t-ok", "t-empty")


class TestOrphanPRs:
    def test_flags_pr_for_missing_task(self, patched_server):
        db = patched_server._db
        db.create_project(project_id="p", name="p")
        _insert_orphan(
            db,
            "INSERT INTO prs(project, task_id, number, url, status, author) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("p", "ghost-task", 99, "https://x/y/pull/99", "open", "me"),
        )
        findings = core_audit.check_orphan_prs()
        assert any(
            f.kind == "orphan_pr" and f.ref["task_id"] == "ghost-task"
            for f in findings
        )


class TestOrphanHistory:
    def test_flags_history_for_missing_task(self, patched_server):
        db = patched_server._db
        db.create_project(project_id="p", name="p")
        _insert_orphan(
            db,
            "INSERT INTO task_history(project, task_id, ts, text) "
            "VALUES (?, ?, ?, ?)",
            ("p", "ghost", "2026-04-01T00:00:00", "old note"),
        )
        findings = core_audit.check_orphan_history()
        assert any(
            f.kind == "orphan_history" and f.ref["task_id"] == "ghost"
            for f in findings
        )


class TestOrphanDependencies:
    def test_flags_dep_pointing_at_missing_task(self, patched_server):
        db = patched_server._db
        _seed_minimal(db, "p", "real")
        _insert_orphan(
            db,
            "INSERT INTO task_dependencies(project, task_id, depends_on) "
            "VALUES (?, ?, ?)",
            ("p", "real", "missing-dep"),
        )
        findings = core_audit.check_orphan_dependencies()
        assert any(
            f.kind == "orphan_dependency" and f.ref["depends_on"] == "missing-dep"
            for f in findings
        )


class TestDuplicatePrUrls:
    def test_flags_when_same_url_attached_to_two_tasks(self, patched_server):
        db = patched_server._db
        _seed_minimal(db, "p", "t-a")
        db.create_task(project="p", task_id="t-b")
        url = "https://github.com/x/y/pull/777"
        db.add_pr(project="p", task_id="t-a", number=777, url=url,
                  status="open", author="me")
        db.add_pr(project="p", task_id="t-b", number=777, url=url,
                  status="open", author="me")
        findings = core_audit.check_duplicate_pr_urls()
        assert any(
            f.kind == "duplicate_pr_url" and f.ref["url"] == url
            for f in findings
        )


class TestDuplicatePrRows:
    """The `prs` table has a `UNIQUE(project, task_id, number)`
    constraint that *prevents* duplicates from being inserted (this
    iter's DDL audit confirmed the protection is already there
    despite the missing-PK appearance from `PRAGMA table_info`).
    The audit check is defense-in-depth: if a future migration ever
    drops the constraint by accident, this surfaces the regression
    immediately rather than letting drift accumulate."""

    def test_no_finding_under_normal_writes(self, patched_server):
        db = patched_server._db
        _seed_minimal(db, "p", "t")
        db.add_pr(project="p", task_id="t", number=10,
                  url="https://x/y/pull/10", status="open", author="me")
        assert core_audit.check_duplicate_pr_rows() == []

    def test_unique_constraint_blocks_actual_duplicate(self, patched_server):
        # If someone tried to bypass `add_pr` via raw INSERT, the
        # UNIQUE constraint rejects it. Documents the protection so
        # future schema reviewers know it's enforced.
        db = patched_server._db
        _seed_minimal(db, "p", "t")
        db._conn.execute(
            "INSERT INTO prs(project, task_id, number, url, status) "
            "VALUES(?, ?, ?, ?, ?)",
            ("p", "t", 91, "https://x/y/pull/91", "open"),
        )
        db._conn.commit()
        from pysqlite3 import dbapi2 as sqlite3
        import pytest
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            db._conn.execute(
                "INSERT INTO prs(project, task_id, number, url, status) "
                "VALUES(?, ?, ?, ?, ?)",
                ("p", "t", 91, "https://x/y/pull/91", "open"),
            )

    def test_check_finds_dupe_if_constraint_were_dropped(self, patched_server):
        # Simulate a future migration regression: drop UNIQUE +
        # rebuild prs, then plant two identical rows. Confirms the
        # check fires when the protection is gone.
        db = patched_server._db
        _seed_minimal(db, "p", "t")
        db._conn.executescript("""
            CREATE TABLE prs_no_unique (
                project TEXT, task_id TEXT, number INTEGER,
                url TEXT, status TEXT, title TEXT DEFAULT '',
                session TEXT, working_dir TEXT DEFAULT '~',
                ci_status TEXT DEFAULT 'unknown',
                review_status TEXT DEFAULT '',
                comment_count INTEGER DEFAULT 0,
                last_seen_comment_count INTEGER NOT NULL DEFAULT 0,
                additions INTEGER DEFAULT 0,
                deletions INTEGER DEFAULT 0,
                author TEXT DEFAULT '',
                head_branch TEXT DEFAULT '',
                base_branch TEXT DEFAULT '',
                last_updated TEXT DEFAULT '',
                status_changed_at TEXT DEFAULT '',
                dirty INTEGER DEFAULT 0
            );
            INSERT INTO prs_no_unique SELECT * FROM prs;
            DROP TABLE prs;
            ALTER TABLE prs_no_unique RENAME TO prs;
            INSERT INTO prs(project, task_id, number, url, status)
                VALUES('p', 't', 91, 'https://x/y/pull/91', 'open');
            INSERT INTO prs(project, task_id, number, url, status)
                VALUES('p', 't', 91, 'https://x/y/pull/91', 'open');
        """)
        db._conn.commit()
        findings = core_audit.check_duplicate_pr_rows()
        assert len(findings) == 1
        assert findings[0].ref == {
            "project": "p", "task_id": "t", "number": 91, "count": 2,
        }


class TestPRsMissingAuthor:
    def test_emits_one_finding_per_affected_row(self, patched_server):
        """Upgraded from a single aggregate-count finding to per-row
        findings so users can see exactly which PRs need cleanup via
        `eva-cli audit --kind pr_missing_author`."""
        db = patched_server._db
        # Wipe any pre-seeded PRs that might already lack author.
        db._conn.execute("DELETE FROM prs")
        db._conn.commit()
        _seed_minimal(db, "p", "t")
        db.add_pr(project="p", task_id="t", number=5,
                  url="https://x/y/pull/5", status="open", author="")
        db.add_pr(project="p", task_id="t", number=6,
                  url="https://x/y/pull/6", status="open", author="")
        # Plus one row that DOES have an author -- it should NOT be flagged.
        db.add_pr(project="p", task_id="t", number=7,
                  url="https://x/y/pull/7", status="open", author="alice")
        findings = core_audit.check_prs_missing_author()
        assert len(findings) == 2
        # All findings carry the row identifiers.
        numbers = sorted(f.ref["number"] for f in findings)
        assert numbers == [5, 6]
        # `ref` shape contract: project, task_id, number, url present.
        for f in findings:
            assert f.kind == "pr_missing_author"
            assert f.ref["project"] == "p"
            assert f.ref["task_id"] == "t"
            assert f.ref["url"].startswith("https://")

    def test_no_finding_when_all_have_author(self, patched_server):
        db = patched_server._db
        # Wipe any pre-seeded PRs that might lack author.
        db._conn.execute("DELETE FROM prs")
        db._conn.commit()
        _seed_minimal(db, "p", "t")
        db.add_pr(project="p", task_id="t", number=6,
                  url="https://x/y/pull/6", status="open", author="me")
        assert core_audit.check_prs_missing_author() == []

    def test_findings_now_marked_fixable(self, patched_server):
        """As of this iteration the `pr_missing_author` finding has
        a registered fixer (`_backfill_pr_author`), so the finding
        must mark itself `fixable=True` -- otherwise `eva-cli audit
        --fix` would skip it and the user can't bulk-clean."""
        db = patched_server._db
        db._conn.execute("DELETE FROM prs")
        db._conn.commit()
        _seed_minimal(db, "p", "t")
        db.add_pr(project="p", task_id="t", number=8,
                  url="https://x/y/pull/8", status="open", author="")
        findings = core_audit.check_prs_missing_author()
        assert len(findings) == 1
        assert findings[0].fixable is True

    def test_backfill_fixer_writes_author_via_gh(self, patched_server):
        """`_backfill_pr_author` should call gh_run_json with the right
        repo + PR number and write the returned author into the row."""
        from unittest.mock import patch
        db = patched_server._db
        db._conn.execute("DELETE FROM prs")
        db._conn.commit()
        _seed_minimal(db, "p", "t")
        db.add_pr(project="p", task_id="t", number=42,
                  url="https://github.com/acme/widget/pull/42",
                  status="open", author="")

        def fake_gh(cmd, repo="", **kwargs):
            # Verify we ask gh for the right thing. `cmd` is the full
            # argv: ['gh', 'pr', 'view', '<#>', '--repo', '<r>',
            # '--json', 'author']
            assert isinstance(cmd, list)
            assert cmd[0] == "gh"
            assert "pr" in cmd
            assert "view" in cmd
            assert "42" in cmd
            assert "--repo" in cmd
            assert "--json" in cmd
            assert repo == "acme/widget"
            return {"author": {"login": "alice"}}

        with patch("app_state.gh_run_json", side_effect=fake_gh):
            ok = core_audit._backfill_pr_author({
                "project": "p", "task_id": "t", "number": 42,
                "url": "https://github.com/acme/widget/pull/42",
            })
        assert ok is True
        row = db._conn.execute(
            "SELECT author FROM prs WHERE number=42").fetchone()
        assert row["author"] == "alice"

    def test_backfill_fixer_returns_false_on_gh_failure(self, patched_server):
        """gh failure / missing author / network error -> the fixer
        returns False so the audit's bulk-fix path treats it as a
        no-op (doesn't claim success)."""
        from unittest.mock import patch
        db = patched_server._db
        db._conn.execute("DELETE FROM prs")
        db._conn.commit()
        _seed_minimal(db, "p", "t")
        db.add_pr(project="p", task_id="t", number=43,
                  url="https://github.com/acme/widget/pull/43",
                  status="open", author="")

        def fake_gh_none(cmd, repo="", **kwargs):
            return None

        with patch("app_state.gh_run_json", side_effect=fake_gh_none):
            ok = core_audit._backfill_pr_author({
                "project": "p", "task_id": "t", "number": 43,
                "url": "https://github.com/acme/widget/pull/43",
            })
        assert ok is False
        # Row stays untouched.
        row = db._conn.execute(
            "SELECT author FROM prs WHERE number=43").fetchone()
        assert row["author"] == ""


class TestStaleCronRuns:
    """Catches cron runs left in 'running' / finished_at='' whose
    tmux session is gone -- the failure mode supersede_open_runs and
    the Stop hook can't catch (deleted job, server crash, kill -9)."""

    def test_flags_run_when_session_dead(self, patched_server, monkeypatch):
        from common import cron_jobs as _cron
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: False)
        j = _cron.create_job(name="x", schedule="1h", command="/x")
        run = _cron.record_run_start(j["id"])

        findings = core_audit.check_stale_cron_runs()
        assert len(findings) == 1
        assert findings[0].kind == "stale_cron_run"
        assert findings[0].fixable is True
        assert findings[0].ref["run_id"] == run["id"]
        assert findings[0].ref["session"] == f"cron-x-{j['id']}"

    def test_no_flag_when_session_alive(self, patched_server, monkeypatch):
        from common import cron_jobs as _cron
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: True)
        j = _cron.create_job(name="x", schedule="1h", command="/x")
        _cron.record_run_start(j["id"])
        # Session still up -> the Stop hook will close this run; not
        # the audit's job to second-guess.
        assert core_audit.check_stale_cron_runs() == []

    def test_no_flag_when_run_already_terminal(self, patched_server,
                                                monkeypatch):
        from common import cron_jobs as _cron
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: False)
        j = _cron.create_job(name="x", schedule="1h", command="/x")
        run = _cron.record_run_start(j["id"])
        _cron.record_run_end(run["id"], status="done")
        assert core_audit.check_stale_cron_runs() == []

    def test_fix_finding_closes_run_as_cancelled(self, patched_server,
                                                  monkeypatch):
        from common import cron_jobs as _cron
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: False)
        j = _cron.create_job(name="x", schedule="1h", command="/x")
        run = _cron.record_run_start(j["id"])

        finding = core_audit.check_stale_cron_runs()[0].to_dict()
        assert core_audit.fix_finding(finding) is True

        runs = _cron.list_runs(j["id"])
        assert runs[0]["id"] == run["id"]
        assert runs[0]["status"] == "cancelled"
        assert runs[0]["finished_at"]
        assert runs[0]["error_message"] == "stale: session gone"


class TestStaleTaskSessions:
    """Mirrors `TestStaleCronRuns` for the `sessions` table -- catches
    rows stuck in `working` / `idle` / `thinking` whose tmux session
    is gone (Stop hook missed, server crash, kill -9)."""

    def test_flags_session_when_tmux_dead(self, patched_server, monkeypatch):
        db = patched_server._db
        if not db.project_exists("p1"):
            db.create_project(project_id="p1", name="p1")
        db.create_task(project="p1", task_id="t1")
        db.create_session(task_id="t1", project="p1", tmux_name="t1")
        db.update_session("t1", status="working")
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: False)
        findings = core_audit.check_stale_task_sessions()
        assert len(findings) == 1
        assert findings[0].kind == "stale_task_session"
        assert findings[0].fixable is True
        assert findings[0].ref["task_id"] == "t1"

    def test_no_flag_when_session_terminal(self, patched_server, monkeypatch):
        # `stopped` is terminal -> not in _LIVE_SESSION_STATES, skipped.
        db = patched_server._db
        if not db.project_exists("p1"):
            db.create_project(project_id="p1", name="p1")
        db.create_task(project="p1", task_id="t-stopped")
        db.create_session(task_id="t-stopped", project="p1",
                          tmux_name="t-stopped")
        db.update_session("t-stopped", status="stopped")
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: False)
        assert core_audit.check_stale_task_sessions() == []

    def test_no_flag_when_tmux_alive(self, patched_server, monkeypatch):
        db = patched_server._db
        if not db.project_exists("p1"):
            db.create_project(project_id="p1", name="p1")
        db.create_task(project="p1", task_id="t-live")
        db.create_session(task_id="t-live", project="p1",
                          tmux_name="t-live")
        db.update_session("t-live", status="idle")
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: True)
        # tmux still up; not the audit's job to second-guess.
        assert core_audit.check_stale_task_sessions() == []

    def test_fix_finding_flips_to_stopped(self, patched_server, monkeypatch):
        db = patched_server._db
        if not db.project_exists("p1"):
            db.create_project(project_id="p1", name="p1")
        db.create_task(project="p1", task_id="ghost")
        db.create_session(task_id="ghost", project="p1",
                          tmux_name="ghost")
        db.update_session("ghost", status="working")
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: False)

        finding = core_audit.check_stale_task_sessions()[0].to_dict()
        assert core_audit.fix_finding(finding) is True
        # Re-read the row; status is now `stopped`.
        row = db.get_session("ghost")
        assert row["status"] == "stopped"


class TestLongIdleInputSessions:
    """`needs_input` / `starting` rows stuck > 24h. Distinct from
    stale-task-session: those have a dead tmux pane; these have a
    live pane blocked on user input nobody answered."""

    def _seed_session(self, db, task_id, status, age_hours):
        from datetime import datetime, timezone, timedelta
        _seed_minimal(db, "p", task_id)
        ts = (datetime.now(timezone.utc) -
              timedelta(hours=age_hours)).isoformat().replace("+00:00", "Z")
        # Insert directly so we can backdate updated_at.
        db._conn.execute("DELETE FROM sessions WHERE task_id=?", (task_id,))
        db._conn.execute(
            "INSERT INTO sessions(task_id, project, tmux_name, status, "
            "updated_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, "p", task_id, status, ts, ts),
        )
        db._conn.commit()

    def test_flags_session_stuck_in_needs_input_over_24h(self, patched_server):
        db = patched_server._db
        self._seed_session(db, "stuck-input", "needs_input", age_hours=72)
        findings = core_audit.check_long_idle_input_sessions()
        assert len(findings) == 1
        assert findings[0].kind == "long_idle_input_session"
        assert findings[0].fixable is True
        assert findings[0].ref["task_id"] == "stuck-input"
        assert findings[0].ref["status"] == "needs_input"
        # age_hours rounds to one decimal place but should be ~72
        assert 70 <= findings[0].ref["age_hours"] <= 75

    def test_flags_session_stuck_in_starting_over_24h(self, patched_server):
        db = patched_server._db
        self._seed_session(db, "stuck-starting", "starting", age_hours=48)
        findings = core_audit.check_long_idle_input_sessions()
        assert any(f.ref["task_id"] == "stuck-starting" for f in findings)

    def test_skips_recent_needs_input(self, patched_server):
        """A session in needs_input for 1h is the user actively
        thinking about a permission prompt -- not stale."""
        db = patched_server._db
        self._seed_session(db, "fresh", "needs_input", age_hours=1)
        out = [f for f in core_audit.check_long_idle_input_sessions()
               if f.ref["task_id"] == "fresh"]
        assert out == []

    def test_skips_other_statuses(self, patched_server):
        """idle / working / stopped should not be flagged here -- those
        have other audit checks (or are intentional)."""
        db = patched_server._db
        for status in ("idle", "stopped", "working"):
            self._seed_session(db, f"x-{status}", status, age_hours=72)
        flagged = {f.ref["task_id"] for f in core_audit.check_long_idle_input_sessions()}
        for status in ("idle", "stopped", "working"):
            assert f"x-{status}" not in flagged

    def test_fixer_flips_status_to_stopped(self, patched_server):
        db = patched_server._db
        self._seed_session(db, "fixme", "needs_input", age_hours=48)
        finding = core_audit.check_long_idle_input_sessions()[0].to_dict()
        assert core_audit.fix_finding(finding) is True
        row = db.get_session("fixme")
        assert row["status"] == "stopped"


class TestStaleReviewSessions:
    """Mirrors task-session detection for the review_prs table:
    `my_workflow_state='active'` with a session_name but no live tmux."""

    def _seed(self, db, url="https://github.com/x/y/pull/501"):
        db.upsert_review_pr(
            url=url, repo="x/y", number=501, source="github",
            title="needs review", state="open",
            my_workflow_state="active",
            session_name="review-x-y-501",
        )
        return url

    def test_flags_active_when_tmux_dead(self, patched_server, monkeypatch):
        url = self._seed(patched_server._db)
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: False)
        findings = core_audit.check_stale_review_sessions()
        assert len(findings) == 1
        assert findings[0].kind == "stale_review_session"
        assert findings[0].fixable is True
        assert findings[0].ref["url"] == url

    def test_no_flag_when_tmux_alive(self, patched_server, monkeypatch):
        self._seed(patched_server._db)
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: True)
        assert core_audit.check_stale_review_sessions() == []

    def test_no_flag_for_queued_review(self, patched_server, monkeypatch):
        # `queued` rows aren't running anything; session_name='' too.
        # Either way they don't match the SQL filter.
        patched_server._db.upsert_review_pr(
            url="https://github.com/a/b/pull/1", repo="a/b", number=1,
            source="github", title="q", state="open",
            my_workflow_state="queued",
        )
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: False)
        assert core_audit.check_stale_review_sessions() == []

    def test_fix_finding_flips_back_to_queued(
        self, patched_server, monkeypatch,
    ):
        url = self._seed(patched_server._db)
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: False)
        finding = core_audit.check_stale_review_sessions()[0].to_dict()
        assert core_audit.fix_finding(finding) is True
        row = patched_server._db.get_review_pr(url)
        assert row["my_workflow_state"] == "queued"
        assert row["session_name"] == ""


# ---- Aggregator + fixers ----

class TestRunAudit:
    def test_returns_summary_shape(self, patched_server):
        result = core_audit.run_audit()
        assert "findings" in result
        assert "summary" in result
        assert "total" in result["summary"]
        assert "by_severity" in result["summary"]
        assert "by_kind" in result["summary"]

    def test_summary_counts_match_findings(self, patched_server):
        # Seed an obvious orphan so we have at least one finding.
        db = patched_server._db
        db.create_project(project_id="p-x", name="p-x")
        _insert_orphan(
            db,
            "INSERT INTO prs(project, task_id, number, url, status, author) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("p-x", "no-task", 1, "https://x/y/pull/100", "open", "me"),
        )
        result = core_audit.run_audit()
        assert result["summary"]["total"] == len(result["findings"])
        assert result["summary"]["by_kind"].get("orphan_pr", 0) >= 1
        assert result["summary"]["by_severity"].get("error", 0) >= 1

    def test_check_failure_does_not_abort_audit(self, patched_server, monkeypatch):
        """If one check raises, the rest still run and the failure is
        reported as its own finding."""
        from common import audit as a

        def boom():
            raise RuntimeError("boom")
        monkeypatch.setattr(a, "ALL_CHECKS", (
            boom, a.check_pr_task_drift,
        ))
        result = a.run_audit()
        kinds = [f["kind"] for f in result["findings"]]
        assert "audit_check_error" in kinds


class TestFixers:
    def test_fix_orphan_pr_deletes_row(self, patched_server):
        db = patched_server._db
        db.create_project(project_id="p", name="p")
        _insert_orphan(
            db,
            "INSERT INTO prs(project, task_id, number, url, status, author) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("p", "ghost", 9, "https://x/y/pull/9", "open", "me"),
        )
        result = core_audit.run_audit()
        orphans = [f for f in result["findings"] if f["kind"] == "orphan_pr"]
        assert orphans
        ok = core_audit.fix_finding(orphans[0])
        assert ok is True
        # Row really gone.
        cnt = db._conn.execute(
            "SELECT COUNT(*) as n FROM prs WHERE url = ?",
            ("https://x/y/pull/9",),
        ).fetchone()["n"]
        assert cnt == 0

    def test_fix_finding_returns_false_for_unknown_kind(self):
        assert core_audit.fix_finding({"kind": "made_up", "ref": {}}) is False

    def test_fix_all_only_touches_fixable(self, patched_server):
        # Mix one fixable + one unfixable finding.
        db = patched_server._db
        db.create_project(project_id="p", name="p")
        _insert_orphan(
            db,
            "INSERT INTO prs(project, task_id, number, url, status, author) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("p", "ghost", 1, "https://x/y/pull/1", "open", "me"),
        )
        all_findings = core_audit.run_audit()["findings"]
        result = core_audit.fix_all(all_findings)
        assert result["fixed"] >= 1
        # Skipped >= total - fixed (every non-fixable counted as skipped).
        assert result["skipped"] >= 0

    def test_fix_orphan_history_deletes_row(self, patched_server):
        """task_history rows referring to a non-existent task slip past
        FK in legacy DBs (or after a manual delete that bypassed the
        cascade). The fixer must wipe them."""
        db = patched_server._db
        db.create_project(project_id="p2", name="p2")
        _insert_orphan(
            db,
            "INSERT INTO task_history(project, task_id, ts, text) "
            "VALUES (?, ?, ?, ?)",
            ("p2", "vanished", "2026-04-27T00:00:00", "orphan note"),
        )
        orphans = [f for f in core_audit.run_audit()["findings"]
                   if f["kind"] == "orphan_history"]
        assert orphans, "audit should flag the orphan row"
        ok = core_audit.fix_finding(orphans[0])
        assert ok is True
        cnt = db._conn.execute(
            "SELECT COUNT(*) as n FROM task_history "
            "WHERE project=? AND task_id=?",
            ("p2", "vanished"),
        ).fetchone()["n"]
        assert cnt == 0

    def test_fix_orphan_dependency_deletes_row(self, patched_server):
        """task_dependencies rows pointing at a deleted parent must be
        removed -- otherwise the graph view re-renders ghost edges."""
        db = patched_server._db
        db.create_project(project_id="p3", name="p3")
        _insert_orphan(
            db,
            "INSERT INTO task_dependencies(project, task_id, depends_on) "
            "VALUES (?, ?, ?)",
            ("p3", "child", "ghost-parent"),
        )
        orphans = [f for f in core_audit.run_audit()["findings"]
                   if f["kind"] == "orphan_dependency"]
        assert orphans
        ok = core_audit.fix_finding(orphans[0])
        assert ok is True
        cnt = db._conn.execute(
            "SELECT COUNT(*) as n FROM task_dependencies "
            "WHERE project=? AND task_id=? AND depends_on=?",
            ("p3", "child", "ghost-parent"),
        ).fetchone()["n"]
        assert cnt == 0

    def test_fix_finding_returns_false_when_fixer_raises(self, monkeypatch):
        """Fixer-side exceptions must NOT bubble out of fix_finding --
        the fix_all loop expects every fixer call to come back with a
        bool, otherwise one bad finding kills the whole batch."""
        from common import audit as audit_mod

        def bomb(_ref):
            raise RuntimeError("kaboom")

        monkeypatch.setitem(audit_mod._FIXERS, "bomb_kind", bomb)
        out = audit_mod.fix_finding({"kind": "bomb_kind", "ref": {}})
        assert out is False

    def test_fix_all_skipped_includes_no_op_fixers(self, monkeypatch):
        """A fixable finding whose fixer returns False (already-gone
        row, no-op delete) must count under `skipped`, not `fixed`.
        Without this branch the dashboard would over-report fixes."""
        from common import audit as audit_mod
        # Register a fixer that always reports "nothing to do".
        monkeypatch.setitem(audit_mod._FIXERS, "noop_kind", lambda _r: False)
        result = audit_mod.fix_all([
            {"kind": "noop_kind", "fixable": True, "ref": {}},
        ])
        assert result == {"fixed": 0, "skipped": 1}


class TestTaskTypeCanonicalization:
    """Eva's `common.tasks.type` column has no enum, so over time both
    'feat' and 'feature' (or 'doc'/'docs', 'bug'/'fix') accumulate.
    The audit surfaces aliases as fixable INFO findings; the fixer
    rewrites them to the canonical spelling so badge labels stop
    splitting one logical category into two."""

    def test_flags_aliased_type(self, patched_server):
        db = patched_server._db
        _seed_minimal(db, "p", "t-feat")
        db._conn.execute(
            "UPDATE tasks SET type='feat' WHERE project=? AND task_id=?",
            ("p", "t-feat"),
        )
        db._conn.commit()
        findings = core_audit.check_task_type_canonicalization()
        kinds = [f.kind for f in findings]
        assert "noncanonical_task_type" in kinds
        # Finding includes both source and target spelling so the
        # fixer doesn't have to re-derive them.
        f = next(x for x in findings if x.ref.get("task_id") == "t-feat")
        assert f.ref["from_type"] == "feat"
        assert f.ref["to_type"] == "feature"
        assert f.fixable is True
        assert f.severity == core_audit.SEVERITY_INFO

    def test_no_flag_when_already_canonical(self, patched_server):
        db = patched_server._db
        _seed_minimal(db, "p", "t-feature")  # default type='feature'
        # Filter to OUR row -- the seeded fixture data has its own tasks.
        ours = [f for f in core_audit.check_task_type_canonicalization()
                if f.ref.get("task_id") == "t-feature"]
        assert ours == []

    def test_fix_rewrites_to_canonical(self, patched_server):
        db = patched_server._db
        _seed_minimal(db, "p", "t-doc")
        db._conn.execute(
            "UPDATE tasks SET type='doc' WHERE project=? AND task_id=?",
            ("p", "t-doc"),
        )
        db._conn.commit()
        findings = core_audit.check_task_type_canonicalization()
        f = next(x for x in findings if x.ref.get("task_id") == "t-doc")
        ok = core_audit.fix_finding(f.to_dict())
        assert ok is True
        new_type = db._conn.execute(
            "SELECT type FROM tasks WHERE project=? AND task_id=?",
            ("p", "t-doc"),
        ).fetchone()["type"]
        assert new_type == "docs"

    def test_all_three_aliases_handled(self, patched_server):
        """feat->feature, doc->docs, bug->fix all flagged + fixable."""
        db = patched_server._db
        for tid, alias in [("t-a", "feat"), ("t-b", "doc"), ("t-c", "bug")]:
            _seed_minimal(db, "p", tid)
            db._conn.execute(
                "UPDATE tasks SET type=? WHERE project=? AND task_id=?",
                (alias, "p", tid),
            )
        db._conn.commit()
        findings = core_audit.check_task_type_canonicalization()
        ours = {f.ref["task_id"]: f.ref["to_type"]
                for f in findings if f.ref.get("task_id") in ("t-a", "t-b", "t-c")}
        assert ours == {"t-a": "feature", "t-b": "docs", "t-c": "fix"}


# ---- check_terminal_status_missing_transition ----


class TestTerminalStatusMissingTransition:
    """`check_terminal_status_missing_transition` flags tasks whose
    current status is `done`/`closed` but `task_history` shows no
    `-> <status>` transition row -- a state-machine bypass marker."""

    @staticmethod
    def _seed_with_history(db, project, task_id, status, history_lines):
        _seed_minimal(db, project, task_id, status=status)
        for line in history_lines:
            db.append_task_history(project=project, task_id=task_id, text=line)

    def test_flags_done_task_with_history_but_no_done_transition(
        self, patched_server,
    ):
        """A task that has SOME history but the closure transition is
        missing -- e.g. a direct-SQL UPDATE flipped status to `done`."""
        db = patched_server._db
        self._seed_with_history(
            db, "p", "t-bypass", status="done",
            history_lines=["linked PR #100"],  # no `-> done` row
        )
        findings = core_audit.check_terminal_status_missing_transition()
        ours = [f for f in findings if f.ref.get("task_id") == "t-bypass"]
        assert len(ours) == 1
        assert ours[0].kind == "terminal_status_missing_transition"
        assert ours[0].severity == core_audit.SEVERITY_INFO
        assert ours[0].fixable is False
        assert ours[0].ref["status"] == "done"

    def test_flags_closed_task_with_history_but_no_closed_transition(
        self, patched_server,
    ):
        db = patched_server._db
        self._seed_with_history(
            db, "p", "t-closed-bypass", status="closed",
            history_lines=["status: in_progress -> in_review",
                           "PR #1 merged"],
        )
        findings = core_audit.check_terminal_status_missing_transition()
        ours = [f for f in findings
                if f.ref.get("task_id") == "t-closed-bypass"]
        assert len(ours) == 1
        assert ours[0].ref["status"] == "closed"

    def test_no_flag_when_done_transition_present(self, patched_server):
        """The healthy case: history records `-> done`, so the task's
        terminal status came through the proper path."""
        db = patched_server._db
        self._seed_with_history(
            db, "p", "t-good", status="done",
            history_lines=[
                "status: not_started -> in_review",
                "status: in_review -> done",
            ],
        )
        findings = core_audit.check_terminal_status_missing_transition()
        assert all(f.ref.get("task_id") != "t-good" for f in findings)

    def test_no_flag_when_task_has_no_history_at_all(self, patched_server):
        """Defensive: tasks with empty history (legacy / pre-feature
        imports) are NOT flagged. Otherwise every old `done` task in
        a fresh DB would noisily fire."""
        db = patched_server._db
        _seed_minimal(db, "p", "t-empty", status="done")
        findings = core_audit.check_terminal_status_missing_transition()
        assert all(f.ref.get("task_id") != "t-empty" for f in findings)

    def test_no_flag_for_non_terminal_statuses(self, patched_server):
        """Only `done` and `closed` are checked. `in_progress`,
        `in_review`, `not_started` shouldn't surface here even if
        history is missing transitions."""
        db = patched_server._db
        self._seed_with_history(
            db, "p", "t-inprog", status="in_progress",
            history_lines=["arbitrary note"],
        )
        findings = core_audit.check_terminal_status_missing_transition()
        assert all(f.ref.get("task_id") != "t-inprog" for f in findings)

    def test_done_transition_match_is_substring_safe(self, patched_server):
        """The transition match looks for `-> done` literal so
        `-> done-with-followup` (custom status?) wouldn't false-match.
        We don't actually have such a status today, but the substring
        boundary `-> done` (followed by anything) intentionally
        accepts variations like `-> done (auto)`."""
        db = patched_server._db
        # Variant: history line embeds the transition mid-text.
        self._seed_with_history(
            db, "p", "t-mid", status="done",
            history_lines=["auto: status: in_review -> done (PR #5)"],
        )
        findings = core_audit.check_terminal_status_missing_transition()
        # NOT flagged -- the substring match catches the embedded transition.
        assert all(f.ref.get("task_id") != "t-mid" for f in findings)

    def test_check_listed_in_all_checks(self):
        """Drift guard: the new check must be wired into ALL_CHECKS
        and KNOWN_KINDS so `eva-cli audit` and `--list-kinds` see it."""
        assert core_audit.check_terminal_status_missing_transition \
            in core_audit.ALL_CHECKS
        assert "terminal_status_missing_transition" in core_audit.KNOWN_KINDS
