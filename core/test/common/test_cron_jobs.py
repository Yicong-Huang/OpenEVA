"""Tests for the cron jobs CRUD layer + HTTP API."""

import common
import pytest

from common import cron_jobs as core_cron


# ---- cron_jobs ----

class TestCreateJob:
    def test_persists_required_fields(self, patched_server):
        job = core_cron.create_job(
            name="sync prs", schedule="30min",
            command="/yh-code-sync-my-prs",
        )
        assert job["id"] > 0
        assert job["name"] == "sync prs"
        assert job["schedule"] == "30min"
        assert job["command"] == "/yh-code-sync-my-prs"
        assert job["enabled"] == 1

    def test_strips_whitespace(self, patched_server):
        job = core_cron.create_job(
            name="  hello  ", schedule="1h ", command=" /run ",
        )
        assert job["name"] == "hello"
        assert job["schedule"] == "1h"
        assert job["command"] == "/run"

    def test_rejects_missing_name(self, patched_server):
        import pytest
        with pytest.raises(ValueError, match="name"):
            core_cron.create_job(name="", schedule="1h", command="/x")

    def test_rejects_missing_schedule(self, patched_server):
        import pytest
        with pytest.raises(ValueError, match="schedule"):
            core_cron.create_job(name="a", schedule="", command="/x")

    def test_rejects_missing_command(self, patched_server):
        import pytest
        with pytest.raises(ValueError, match="command"):
            core_cron.create_job(name="a", schedule="1h", command="")


class TestListAndUpdate:
    def test_list_returns_newest_first(self, patched_server):
        a = core_cron.create_job(name="a", schedule="1h", command="/a")
        b = core_cron.create_job(name="b", schedule="1h", command="/b")
        ids = [j["id"] for j in core_cron.list_jobs()]
        # b is newer, should come first.
        assert ids[0] == b["id"]
        assert ids[1] == a["id"]

    def test_update_changes_only_whitelisted_fields(self, patched_server):
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        # `id` and `created_at` are NOT user-editable; the update
        # silently drops them so a stale UI never overwrites them.
        out = core_cron.update_job(
            j["id"], name="renamed", id=99999, created_at="bogus",
        )
        assert out["name"] == "renamed"
        assert out["id"] == j["id"]
        assert out["created_at"] == j["created_at"]

    def test_update_disable_sets_enabled_zero(self, patched_server):
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        out = core_cron.update_job(j["id"], enabled=False)
        assert out["enabled"] == 0

    def test_update_unknown_id_returns_none(self, patched_server):
        assert core_cron.update_job(99999, name="x") is None

    def test_delete(self, patched_server):
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        assert core_cron.delete_job(j["id"]) is True
        assert core_cron.get_job(j["id"]) is None
        # second delete is idempotent.
        assert core_cron.delete_job(j["id"]) is False

    def test_list_attaches_session_name(self, patched_server):
        # The frontend uses `session_name` as the key into the global
        # session-status snapshot service. We no longer attach
        # `session_alive` per row -- the snapshot service is
        # authoritative for live state. See `core/common.cron_jobs._with_session`.
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        rows = core_cron.list_jobs()
        target = next(r for r in rows if r["id"] == j["id"])
        # New naming format: `cron-<slug>-<id>` mirrors review-... and
        # ticket-... so a `tmux ls` is self-documenting.
        assert target["session_name"] == f"cron-x-{j['id']}"
        assert "session_alive" not in target
        assert "session_status" not in target

    def test_get_attaches_session_name(self, patched_server):
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        out = core_cron.get_job(j["id"])
        assert out["session_name"] == f"cron-x-{j['id']}"
        assert "session_alive" not in out


# ---- Run history ----

class TestRunHistory:
    def test_record_start_then_end(self, patched_server):
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        run = core_cron.record_run_start(j["id"])
        assert run["status"] == "running"
        assert run["finished_at"] == ""
        out = core_cron.record_run_end(
            run["id"], status="done", output="ran cleanly",
        )
        assert out["status"] == "done"
        assert out["output_excerpt"] == "ran cleanly"
        # Parent job row gets a denormalised last_status / last_run_at
        # so the list view doesn't have to JOIN.
        parent = core_cron.get_job(j["id"])
        assert parent["last_status"] == "done"
        assert parent["last_run_at"]

    def test_run_end_truncates_huge_output(self, patched_server):
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        run = core_cron.record_run_start(j["id"])
        big = "A" * (core_cron.OUTPUT_EXCERPT_LIMIT * 3)
        out = core_cron.record_run_end(run["id"], status="done", output=big)
        # Tail kept, marker added.
        assert out["output_excerpt"].startswith("...[truncated]")
        assert len(out["output_excerpt"]) <= core_cron.OUTPUT_EXCERPT_LIMIT + 30

    def test_run_end_rejects_non_terminal_status(self, patched_server):
        import pytest
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        run = core_cron.record_run_start(j["id"])
        with pytest.raises(ValueError, match="non-terminal"):
            core_cron.record_run_end(run["id"], status="running")

    def test_output_only_update_preserves_parent_last_status(self, patched_server):
        """`update_cron_job_run_output` (mid-run output bump) must
        leave the parent job's `last_status` untouched. The previous
        terminal status from an earlier run wins; only `last_run_at`
        bumps to reflect that the new tick is in flight.

        This invariant lives in `_get_run_and_bump_parent`'s
        `terminal_status=None` branch -- it's what distinguishes the
        mid-run output update from `finish_cron_job_run`.
        """
        db = patched_server._db
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        # Land a terminal previous run so parent.last_status = 'failed'.
        r0 = core_cron.record_run_start(j["id"])
        core_cron.record_run_end(r0["id"], status="failed",
                                 error_message="prev")
        parent_before = core_cron.get_job(j["id"])
        assert parent_before["last_status"] == "failed"
        # Start a new run + push an interim output bump.
        r1 = core_cron.record_run_start(j["id"])
        out = db.update_cron_job_run_output(r1["id"], "session=cron-job-1")
        assert out is not None
        assert out["output_excerpt"] == "session=cron-job-1"
        # Parent's last_status MUST still be "failed" (unchanged by
        # the mid-run bump); last_run_at advances to the new run's
        # started_at.
        parent_after = core_cron.get_job(j["id"])
        assert parent_after["last_status"] == "failed"
        assert parent_after["last_run_at"] == r1["started_at"]

    def test_list_runs_newest_first(self, patched_server):
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        r1 = core_cron.record_run_start(j["id"])
        core_cron.record_run_end(r1["id"], status="done")
        r2 = core_cron.record_run_start(j["id"])
        core_cron.record_run_end(r2["id"], status="failed",
                                 error_message="boom")
        runs = core_cron.list_runs(j["id"])
        assert runs[0]["status"] == "failed"
        assert runs[0]["error_message"] == "boom"
        assert runs[1]["status"] == "done"


# ---- HTTP API ----

class TestCronJobsApi:
    def test_create_then_list(self, client):
        resp = client.post("/api/cron-jobs", json={
            "name": "sync", "schedule": "30min", "command": "/sync",
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] > 0

        listing = client.get("/api/cron-jobs").json()
        ids = [j["id"] for j in listing["jobs"]]
        assert body["id"] in ids

    def test_create_validates_required_fields(self, client):
        # Missing schedule.
        resp = client.post("/api/cron-jobs", json={
            "name": "x", "schedule": "", "command": "/y",
        })
        assert resp.status_code == 422

    def test_get_by_id(self, client):
        created = client.post("/api/cron-jobs", json={
            "name": "x", "schedule": "1h", "command": "/y",
        }).json()
        resp = client.get(f"/api/cron-jobs/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "x"

    def test_get_404_for_missing(self, client):
        assert client.get("/api/cron-jobs/99999").status_code == 404

    def test_patch_partial_update(self, client):
        created = client.post("/api/cron-jobs", json={
            "name": "x", "schedule": "1h", "command": "/y",
        }).json()
        resp = client.patch(f"/api/cron-jobs/{created['id']}",
                            json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] == 0
        # Other fields untouched.
        assert resp.json()["name"] == "x"

    def test_patch_404_for_missing(self, client):
        assert client.patch(
            "/api/cron-jobs/99999", json={"name": "x"},
        ).status_code == 404

    def test_delete(self, client):
        created = client.post("/api/cron-jobs", json={
            "name": "x", "schedule": "1h", "command": "/y",
        }).json()
        resp = client.delete(f"/api/cron-jobs/{created['id']}")
        assert resp.status_code == 200
        assert client.get(f"/api/cron-jobs/{created['id']}").status_code == 404

    def test_delete_unknown_id_returns_404(self, client):
        # Idempotent error: the route surfaces 404 instead of silently
        # 200ing on a non-existent id, so the UI can show "already
        # gone" feedback rather than a misleading success toast.
        resp = client.delete("/api/cron-jobs/99999")
        assert resp.status_code == 404
        assert "99999" in resp.json()["detail"]

    def test_runs_history_endpoint(self, client, patched_server):
        # Use core helpers to seed a run row directly -- the run-now
        # action isn't part of this iteration.
        from common import cron_jobs as cj
        job = cj.create_job(name="x", schedule="1h", command="/y")
        run = cj.record_run_start(job["id"])
        cj.record_run_end(run["id"], status="done", output="hi")
        resp = client.get(f"/api/cron-jobs/{job['id']}/runs")
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        assert len(runs) == 1
        assert runs[0]["status"] == "done"
        assert runs[0]["output_excerpt"] == "hi"

    def test_runs_history_404_for_missing_job(self, client):
        assert client.get("/api/cron-jobs/99999/runs").status_code == 404

    def test_runs_history_limit_clamp_above_max(
        self, client, patched_server,
    ):
        """`?limit=99999` is clamped to 500 server-side -- the route
        accepts the value without 500ing the page when a hostile or
        typoed caller sends an absurd number."""
        from common import cron_jobs as cj
        job = cj.create_job(name="x", schedule="1h", command="/y")
        run = cj.record_run_start(job["id"])
        cj.record_run_end(run["id"], status="done", output="hi")
        resp = client.get(f"/api/cron-jobs/{job['id']}/runs?limit=99999")
        assert resp.status_code == 200
        # Only 1 run exists; verify the response is the full list (not
        # truncated to nonsense) and the route didn't 500.
        assert len(resp.json()["runs"]) == 1

    def test_runs_history_limit_clamp_below_min(
        self, client, patched_server,
    ):
        """`?limit=0` is clamped to 1 -- a degenerate query no longer
        returns an empty list when data exists."""
        from common import cron_jobs as cj
        job = cj.create_job(name="x", schedule="1h", command="/y")
        run = cj.record_run_start(job["id"])
        cj.record_run_end(run["id"], status="done", output="hi")
        resp = client.get(f"/api/cron-jobs/{job['id']}/runs?limit=0")
        assert resp.status_code == 200
        # Clamped to 1 -> at least 1 row when data exists.
        assert len(resp.json()["runs"]) >= 1


# ---- Schedule parser ----

class TestParseSchedule:
    @pytest.mark.parametrize("text,expected_seconds", [
        ("30s", 30),
        ("30 seconds", 30),
        ("5m", 300),
        ("5 min", 300),
        ("5min", 300),
        ("30 minutes", 1800),
        ("2h", 7200),
        ("2 hours", 7200),
        ("1d", 86400),
        ("1 day", 86400),
        ("  3 hours  ", 10800),  # whitespace tolerant
    ])
    def test_duration_forms_parse_to_interval(self, text, expected_seconds):
        out = core_cron.parse_schedule(text)
        assert out.kind == "interval"
        assert out.interval_seconds == expected_seconds

    @pytest.mark.parametrize("text", [
        "*/5 * * * *",
        "0 9 * * 1-5",
        "30 14 * * *",
        "0,30 * * * *",
    ])
    def test_cron_expressions_parse_to_cron(self, text):
        out = core_cron.parse_schedule(text)
        assert out.kind == "cron"
        assert out.cron_expr == text

    @pytest.mark.parametrize("text", [
        "", "   ",  # empty
        "garbage",
        "0 0 0 0 0 0",  # too many fields
        "not-a-duration",
        "0min",  # zero is not positive
    ])
    def test_unparseable_returns_invalid_with_error(self, text):
        out = core_cron.parse_schedule(text)
        assert out.kind == "invalid"
        assert out.error  # non-empty hint

    def test_endpoint_round_trip(self, client):
        resp = client.get("/api/cron-jobs/parse-schedule",
                          params={"text": "30min"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "interval"
        assert body["interval_seconds"] == 1800

    def test_endpoint_reports_invalid(self, client):
        resp = client.get("/api/cron-jobs/parse-schedule",
                          params={"text": "garbage"})
        body = resp.json()
        assert body["kind"] == "invalid"
        assert body["error"]


# ---- Run-now ----

class TestRunNow:
    def test_run_now_leaves_run_open_when_session_launched(self, patched_server):
        # An executor that returns 'done' means "session was successfully
        # launched, the agent is now doing the work". The run row must stay
        # open (status='running', finished_at='') until the Stop hook
        # fires and stamps the actual end time.
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        result = core_cron.run_now(
            j["id"],
            executor=lambda job: {"status": "done", "output": "session up"},
        )
        assert result is not None
        assert result["status"] == "running"
        assert result["finished_at"] == ""
        assert result["output_excerpt"] == "session up"

    def test_run_now_catches_executor_exception(self, patched_server):
        j = core_cron.create_job(name="x", schedule="1h", command="/x")

        def boom(_):
            raise RuntimeError("kaboom")
        result = core_cron.run_now(j["id"], executor=boom)
        assert result["status"] == "failed"
        assert result["finished_at"]
        assert "kaboom" in result["error_message"]

    def test_run_now_stamps_failed_terminal_immediately(self, patched_server):
        # When the executor reports 'failed' the run is terminal --
        # there's no async work to wait for.
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        result = core_cron.run_now(
            j["id"],
            executor=lambda job: {
                "status": "failed", "output": "", "error": "broken",
            },
        )
        assert result["status"] == "failed"
        assert result["finished_at"]
        assert result["error_message"] == "broken"

    def test_run_now_returns_none_for_missing_job(self, patched_server):
        assert core_cron.run_now(99999) is None

    def test_run_now_truncates_huge_executor_output(self, patched_server):
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        big = "B" * (core_cron.OUTPUT_EXCERPT_LIMIT * 2)
        result = core_cron.run_now(
            j["id"],
            executor=lambda job: {"status": "done", "output": big},
        )
        assert result["output_excerpt"].startswith("...[truncated]")
        assert len(result["output_excerpt"]) <= core_cron.OUTPUT_EXCERPT_LIMIT + 30


class TestSupersedeOpenRuns:
    """A new tick must drain any stale-open runs first so history
    can't accumulate stuck 'running' rows. Caused by overlapping
    schedules, crashed ticks, or hook misses."""

    def test_supersedes_single_open_run(self, patched_server):
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        # Simulate a launch that never closed.
        run = core_cron.record_run_start(j["id"])
        assert run["finished_at"] == ""

        closed = core_cron.supersede_open_runs(j["id"])
        assert closed == 1

        runs = core_cron.list_runs(j["id"])
        assert runs[0]["status"] == "cancelled"
        assert runs[0]["finished_at"]
        assert runs[0]["error_message"] == "superseded by new tick"

    def test_supersedes_multiple_overlapping_runs(self, patched_server):
        # Reproduces the prod anomaly: 2+ open runs for the same job.
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        core_cron.record_run_start(j["id"])
        core_cron.record_run_start(j["id"])
        core_cron.record_run_start(j["id"])

        closed = core_cron.supersede_open_runs(j["id"])
        assert closed == 3

        runs = core_cron.list_runs(j["id"])
        assert all(r["status"] == "cancelled" for r in runs)
        assert all(r["finished_at"] for r in runs)

    def test_no_open_runs_is_a_noop(self, patched_server):
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        assert core_cron.supersede_open_runs(j["id"]) == 0

    def test_only_touches_target_job(self, patched_server):
        # Open runs for OTHER jobs must stay open.
        a = core_cron.create_job(name="a", schedule="1h", command="/a")
        b = core_cron.create_job(name="b", schedule="1h", command="/b")
        a_run = core_cron.record_run_start(a["id"])
        b_run = core_cron.record_run_start(b["id"])

        core_cron.supersede_open_runs(a["id"])

        # a's run was closed; b's run is untouched.
        a_after = core_cron.list_runs(a["id"])[0]
        b_after = core_cron.list_runs(b["id"])[0]
        assert a_after["status"] == "cancelled"
        assert b_after["status"] == "running"
        assert b_after["finished_at"] == ""
        # ID-level cross-check.
        assert a_after["id"] == a_run["id"]
        assert b_after["id"] == b_run["id"]

    def test_run_now_drains_stale_runs_before_starting_new(
        self, patched_server,
    ):
        # The integration the supersede helper exists for: run_now
        # calls it before record_run_start so a fresh tick never has
        # to share the lane with a stale predecessor.
        j = core_cron.create_job(name="x", schedule="1h", command="/x")
        # Plant two stale opens (server crash mid-flight, etc).
        core_cron.record_run_start(j["id"])
        core_cron.record_run_start(j["id"])

        result = core_cron.run_now(
            j["id"],
            executor=lambda job: {"status": "done", "output": "fresh"},
        )
        assert result["status"] == "running"  # the new tick is in flight

        runs = core_cron.list_runs(j["id"])
        # Expect: 3 rows (2 cancelled + 1 running). Newest first.
        assert runs[0]["status"] == "running"
        assert runs[0]["output_excerpt"] == "fresh"
        assert runs[1]["status"] == "cancelled"
        assert runs[2]["status"] == "cancelled"
        # Only the new tick is open.
        opens = [r for r in runs if r["finished_at"] == ""]
        assert len(opens) == 1


class TestFinishRunForSession:
    def test_finish_stamps_open_run_terminal(self, patched_server):
        # Simulate the Stop hook: launch a session, leave the run open,
        # then call finish_run_for_session as the hook would.
        j = core_cron.create_job(name="hooked", schedule="1h", command="/x")
        run_now_out = core_cron.run_now(
            j["id"],
            executor=lambda job: {"status": "done", "output": "started"},
        )
        assert run_now_out["finished_at"] == ""

        finished = core_cron.finish_run_for_session(
            f"cron-job-{j['id']}", output="ran cleanly",
        )
        assert finished is not None
        assert finished["status"] == "done"
        assert finished["finished_at"]
        # Output excerpt concatenates launch + final summary so the user
        # sees both phases in the run row.
        assert "started" in finished["output_excerpt"]
        assert "ran cleanly" in finished["output_excerpt"]
        # Parent denormalised fields are now updated by the terminal stamp.
        parent = core_cron.get_job(j["id"])
        assert parent["last_status"] == "done"

    def test_finish_returns_none_for_non_cron_session(self, patched_server):
        # `review-...`, `task-...`, etc. should be ignored.
        assert core_cron.finish_run_for_session("review-abc") is None
        assert core_cron.finish_run_for_session("ticket-EX-1") is None
        assert core_cron.finish_run_for_session("garbage") is None

    def test_finish_returns_none_when_no_open_run(self, patched_server):
        # No run started -> nothing to close.
        j = core_cron.create_job(name="empty", schedule="1h", command="/x")
        assert core_cron.finish_run_for_session(f"cron-job-{j['id']}") is None

    def test_finish_returns_none_for_unknown_job_id(self, patched_server):
        assert core_cron.finish_run_for_session("cron-job-99999") is None


class TestRunNowEndpoint:
    def test_post_runs_job_and_leaves_run_open(self, client):
        # Use the default executor (the route doesn't accept an
        # executor override -- production-shaped path). The tmux
        # adapter is mocked in conftest.py so no real session spawns.
        # Successful launch -> run stays open until the Stop hook fires.
        created = client.post("/api/cron-jobs", json={
            "name": "x", "schedule": "1h", "command": "/y",
        }).json()
        resp = client.post(f"/api/cron-jobs/{created['id']}/run")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "running"
        assert body["finished_at"] == ""
        # Default executor records session+cmd in the output excerpt.
        assert "/y" in body["output_excerpt"]

    def test_post_404_for_missing_job(self, client):
        assert client.post("/api/cron-jobs/99999/run").status_code == 404


class TestStopHookFinishesRun:
    def test_stop_hook_stamps_finished_at_for_cron_session(self, client):
        # End-to-end: post to /api/cron-jobs/{id}/run leaves the run
        # open, then the agent Stop hook for cron-job-{id} stamps it
        # terminal. The user-visible effect is finished_at = "session
        # done", not "tick fired".
        created = client.post("/api/cron-jobs", json={
            "name": "hooked", "schedule": "1h", "command": "/x",
        }).json()
        run = client.post(f"/api/cron-jobs/{created['id']}/run").json()
        assert run["status"] == "running"
        assert run["finished_at"] == ""

        # Stop hook fires when agent goes idle.
        resp = client.post("/api/hook", json={
            "session": f"cron-job-{created['id']}",
            "event": "Stop", "data": {},
        })
        assert resp.status_code == 200
        runs = client.get(
            f"/api/cron-jobs/{created['id']}/runs",
        ).json()["runs"]
        assert runs[0]["status"] == "done"
        assert runs[0]["finished_at"] != ""

    def test_stop_hook_for_unrelated_session_does_not_touch_cron(
        self, client,
    ):
        # A Stop hook for a non-cron session must not corrupt cron
        # run rows.
        from server import _db
        _db.create_session("plain-task", "test-proj", "plain-task")
        created = client.post("/api/cron-jobs", json={
            "name": "untouched", "schedule": "1h", "command": "/x",
        }).json()
        client.post(f"/api/cron-jobs/{created['id']}/run")

        client.post("/api/hook", json={
            "session": "plain-task", "event": "Stop", "data": {},
        })
        runs = client.get(
            f"/api/cron-jobs/{created['id']}/runs",
        ).json()["runs"]
        # Run for the cron job is still open.
        assert runs[0]["status"] == "running"
        assert runs[0]["finished_at"] == ""


# ---- Default executor (tmux launch) ----

class TestDefaultExecutor:
    def test_session_name_format(self):
        # Empty / missing name still produces a valid (slug='job')
        # session name -- the trailing id keeps it unique. This
        # branch matters because legacy / draft job dicts may not
        # carry a `name` yet.
        assert core_cron.session_name_for_job(7) == "cron-job-7"
        assert core_cron.session_name_for_job(7, "") == "cron-job-7"
        # Slug derives from name: lowercase, dashes-only, alnum-safe
        # (mirrors common.reviews._slugify so naming feels consistent
        # across Eva's session families).
        assert core_cron.session_name_for_job(3, "Sync My PRs") == "cron-sync-my-prs-3"
        assert core_cron.session_name_for_job(9, "  spaces  ") == "cron-spaces-9"
        assert core_cron.session_name_for_job(11, "weird/punc!?") == "cron-weird-punc-11"

    def test_launches_new_session_when_none_exists(self, patched_server,
                                                    monkeypatch):
        # No prior session -> straightforward launch with the command
        # baked into argv as the trailing positional argument. No
        # paste_text path -- claude reads the prompt directly via
        # argv, sidestepping the input-box autocomplete race.
        captured = {}

        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: False)
        monkeypatch.setattr(
            "adapters.tmux.launch_session_argv",
            lambda name, wd, argv: captured.setdefault(
                "launched",
                {"name": name, "dir": wd, "argv": argv},
            ),
        )
        # graceful_kill_session must NOT be called when no session
        # exists -- a stray kill of an unrelated tmux pane would be
        # destructive.
        monkeypatch.setattr(
            "adapters.tmux.graceful_kill_session",
            lambda *a, **kw: captured.setdefault("killed", True),
        )

        job = core_cron.create_job(name="ping", schedule="1h",
                                   command="/yh-sync-repos")
        out = core_cron._default_executor(job)
        expected_session = f"cron-ping-{job['id']}"
        assert out["status"] == "done"
        assert "[launched]" in out["output"]
        assert "killed" not in captured
        assert captured["launched"]["name"] == expected_session
        # argv: ["agent", "-n", <name>, "--append-system-prompt", <ctx>, <prompt>]
        argv = captured["launched"]["argv"]
        assert argv[0] in ("agent", "claude")  # active agent binary
        assert argv[-1] == "/yh-sync-repos"
        sys_prompt = argv[argv.index("--append-system-prompt") + 1]
        assert "ping" in sys_prompt
        assert "/yh-sync-repos" in sys_prompt

    def test_relaunches_existing_session(self, patched_server, monkeypatch):
        # Prior session must be torn down before relaunching -- a
        # fresh agent per tick is the whole point of using the argv
        # prompt path. graceful_kill_session sends Ctrl+C first so
        # the old agent persists state via its own shutdown path,
        # then tmux is killed; afterwards we relaunch with the same
        # session name.
        captured = {}
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: True)
        monkeypatch.setattr(
            "adapters.tmux.graceful_kill_session",
            lambda name, **kw: captured.setdefault("killed", []).append(name),
        )
        monkeypatch.setattr(
            "adapters.tmux.launch_session_argv",
            lambda name, wd, argv: captured.setdefault(
                "launched", {"name": name, "argv": argv},
            ),
        )

        job = core_cron.create_job(name="loop", schedule="30min",
                                   command="/babysit")
        out = core_cron._default_executor(job)
        expected_session = f"cron-loop-{job['id']}"
        assert out["status"] == "done"
        assert "[relaunched]" in out["output"]
        assert captured["killed"] == [expected_session]
        assert captured["launched"]["name"] == expected_session
        assert captured["launched"]["argv"][-1] == "/babysit"

    def test_empty_command_fails_loudly(self, patched_server):
        # Build a dict with an empty command (bypassing create_job's
        # validation, which already rejects empty commands).
        out = core_cron._default_executor(
            {"id": 1, "name": "x", "command": "", "schedule": "1h"},
        )
        assert out["status"] == "failed"
        assert "empty command" in out["error"]

    def test_run_now_uses_default_executor_end_to_end(self, patched_server,
                                                       monkeypatch):
        """run_now() routes into _default_executor when no executor
        override is passed -- the production path. The launch leaves
        the run open ('running') so finished_at can be stamped later
        when agent actually goes idle (via Stop hook)."""
        monkeypatch.setattr("adapters.tmux.session_exists",
                            lambda name: False)
        monkeypatch.setattr("adapters.tmux.launch_session_argv",
                            lambda *a, **kw: None)
        monkeypatch.setattr("adapters.tmux.graceful_kill_session",
                            lambda *a, **kw: None)
        job = core_cron.create_job(name="e2e", schedule="1h", command="/run")
        result = core_cron.run_now(job["id"])
        assert result["status"] == "running"
        assert result["finished_at"] == ""
        assert f"cron-e2e-{job['id']}" in result["output_excerpt"]


class TestWithSessionEnrichment:
    """`_with_session` attaches `session_name` to a job dict so the
    frontend can index it into the global session-status snapshot.
    Live state itself is owned by the snapshot service, not by this
    enricher (post-consolidation: a single SSE-driven cache replaced
    the old per-row tmux probes)."""

    def test_returns_job_unchanged_when_id_missing(self):
        # An id-less dict short-circuits before computing a session
        # name -- this matters because `_with_session` is mapped over
        # rows that might be still-being-built (e.g. a draft).
        out = core_cron._with_session({"name": "no-id"})
        assert out == {"name": "no-id"}

    def test_attaches_session_name_only(self):
        out = core_cron._with_session({"id": 7, "name": "x"})
        assert out["session_name"] == "cron-x-7"
        # No live-state fields any more -- snapshot service is
        # authoritative.
        assert "session_alive" not in out
        assert "session_status" not in out


class TestJobIdFromSession:
    """`_job_id_from_session` parses `cron-job-<n>` back to an int.
    Used by the Stop-hook handler to find which job a session belongs
    to. Anything not matching the prefix returns None so the hook
    handler can fall through cleanly."""

    def test_returns_none_for_non_cron_session(self):
        assert core_cron._job_id_from_session("review-9003") is None
        assert core_cron._job_id_from_session("ticket-X-1") is None
        assert core_cron._job_id_from_session("") is None

    def test_returns_int_for_canonical_name(self):
        assert core_cron._job_id_from_session("cron-job-42") == 42

    def test_returns_none_for_garbage_suffix(self):
        # `cron-job-abc` slips past the prefix check but ValueError on
        # int() -> handler returns None.
        assert core_cron._job_id_from_session("cron-job-abc") is None


class TestBuildJobSystemPrompt:
    """The system prompt fed to agent for a cron job carries the
    schedule / command / description so the agent has full context.
    Description block only renders when the user provided one."""

    def test_includes_description_block_when_present(self):
        out = core_cron._build_job_system_prompt({
            "name": "sync prs", "schedule": "30m",
            "command": "/yh-code-sync-my-prs",
            "description": "Auto-sync personal PRs",
        })
        assert "## Description" in out
        assert "Auto-sync personal PRs" in out

    def test_omits_description_block_when_blank(self):
        out = core_cron._build_job_system_prompt({
            "name": "n", "schedule": "1h", "command": "/x",
            "description": "",
        })
        assert "## Description" not in out

    def test_omits_description_block_when_only_whitespace(self):
        # `desc.strip()` falsy on whitespace-only content -> no block.
        out = core_cron._build_job_system_prompt({
            "name": "n", "schedule": "1h", "command": "/x",
            "description": "   \n\t  ",
        })
        assert "## Description" not in out
