"""Tests for services/scheduler.py and the /api/jobs management routes.

The scheduler module holds a singleton; we call `reset_scheduler()`
between tests to isolate state."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_singleton():
    from services.scheduler import reset_scheduler
    reset_scheduler()
    yield
    reset_scheduler()


class TestSingleton:
    def test_get_scheduler_returns_same_instance(self):
        from services.scheduler import get_scheduler
        assert get_scheduler() is get_scheduler()

    def test_reset_scheduler_creates_new_instance(self):
        from services.scheduler import get_scheduler, reset_scheduler
        first = get_scheduler()
        reset_scheduler()
        second = get_scheduler()
        assert first is not second


class TestStartStop:
    """`AsyncIOScheduler.start()` requires a running event loop, so these
    tests wrap in `asyncio.run`. The lifespan hook satisfies this naturally
    in production because uvicorn runs the lifespan inside its loop."""

    def test_start_scheduler_idempotent(self):
        import asyncio
        from services.scheduler import start_scheduler, get_scheduler

        async def run():
            start_scheduler()
            assert get_scheduler().running
            start_scheduler()  # second call must not raise
            assert get_scheduler().running

        asyncio.run(run())

    def test_stop_scheduler_idempotent(self):
        """Two consecutive stop_scheduler() calls must not raise.

        Note: AsyncIOScheduler.shutdown(wait=False) schedules the state
        transition to happen on the event loop; we don't assert a specific
        state here because that's implementation timing -- the contract we
        care about is "two calls don't raise"."""
        import asyncio
        from services.scheduler import start_scheduler, stop_scheduler, get_scheduler

        async def run():
            start_scheduler()
            assert get_scheduler().running
            stop_scheduler()
            stop_scheduler()  # second call must not raise

        asyncio.run(run())

    def test_stop_before_start_is_safe(self):
        from services.scheduler import stop_scheduler
        stop_scheduler()  # must not raise even though scheduler never ran


class TestJobDefaults:
    """Job defaults prevent pile-up and double-invocation on slow jobs."""

    def test_defaults_include_coalesce_and_max_instances(self):
        from services.scheduler import _JOB_DEFAULTS
        assert _JOB_DEFAULTS["coalesce"] is True
        assert _JOB_DEFAULTS["max_instances"] == 1
        assert _JOB_DEFAULTS["misfire_grace_time"] == 30


class TestDescribeJobs:
    def test_empty_when_no_jobs(self):
        from services.scheduler import describe_jobs
        assert describe_jobs() == []

    def test_returns_job_summaries(self):
        """When the scheduler isn't running, add_job defers next_run_time
        calculation to start() -- so `paused` is True for everything. What
        we check here is that describe_jobs returns every job's id, trigger,
        and the three keys the /api/jobs endpoint relies on."""
        from services.scheduler import get_scheduler, describe_jobs
        sched = get_scheduler()
        sched.add_job(lambda: None, "interval", seconds=60, id="j1", name="job one")
        sched.add_job(lambda: None, "interval", seconds=120, id="j2")
        out = describe_jobs()
        ids = {j["id"] for j in out}
        assert ids == {"j1", "j2"}
        for j in out:
            assert set(j.keys()) >= {"id", "name", "trigger", "next_run", "paused"}
            assert "interval" in j["trigger"].lower()

    def test_paused_job_is_marked(self):
        """A job that's been explicitly paused reports paused=True in the
        describe output. We don't start the scheduler; pause_job works on
        a stopped scheduler too."""
        from services.scheduler import get_scheduler, describe_jobs
        sched = get_scheduler()
        sched.add_job(lambda: None, "interval", seconds=60, id="pausetest")
        sched.pause_job("pausetest")
        out = describe_jobs()
        paused = next(j for j in out if j["id"] == "pausetest")
        assert paused["paused"] is True
        assert paused["next_run"] is None


class TestJobsRoutes:
    """/api/jobs endpoints: list, pause, resume, run."""

    def test_list_empty_jobs(self, client):
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        assert resp.json() == {"jobs": []}

    def test_list_includes_registered_job(self, client):
        from services.scheduler import get_scheduler
        get_scheduler().add_job(lambda: None, "interval", seconds=60, id="list-test")

        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        ids = {j["id"] for j in resp.json()["jobs"]}
        assert "list-test" in ids

    def test_pause_unknown_job_returns_404(self, client):
        resp = client.post("/api/jobs/does-not-exist/pause")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_resume_unknown_job_returns_404(self, client):
        resp = client.post("/api/jobs/does-not-exist/resume")
        assert resp.status_code == 404

    def test_run_unknown_job_returns_404(self, client):
        resp = client.post("/api/jobs/does-not-exist/run")
        assert resp.status_code == 404

    def test_pause_and_resume_round_trip(self, client):
        """Pause/resume via HTTP maps to APScheduler job state. We drive
        the scheduler inside an asyncio.run so AsyncIOScheduler.start()
        has a loop to bind to."""
        import asyncio
        from services.scheduler import get_scheduler, start_scheduler, stop_scheduler

        async def run():
            start_scheduler()
            sched = get_scheduler()
            sched.add_job(lambda: None, "interval", seconds=60, id="roundtrip")
            try:
                assert sched.get_job("roundtrip").next_run_time is not None

                resp = client.post("/api/jobs/roundtrip/pause")
                assert resp.status_code == 200
                assert resp.json() == {"ok": True, "paused": True}
                assert sched.get_job("roundtrip").next_run_time is None

                resp = client.post("/api/jobs/roundtrip/resume")
                assert resp.status_code == 200
                assert resp.json() == {"ok": True, "paused": False}
                assert sched.get_job("roundtrip").next_run_time is not None
            finally:
                stop_scheduler()

        asyncio.run(run())

    def test_run_job_now_schedules_immediate_run(self, client):
        """POST /api/jobs/{id}/run rewrites next_run_time to `now`."""
        import asyncio
        from services.scheduler import get_scheduler, start_scheduler, stop_scheduler
        from datetime import datetime, timezone, timedelta

        async def run():
            start_scheduler()
            sched = get_scheduler()
            sched.add_job(lambda: None, "interval", seconds=3600, id="runnow")
            try:
                before_next = sched.get_job("runnow").next_run_time
                # Normal cadence is one hour out.
                assert before_next > datetime.now(timezone.utc) + timedelta(minutes=30)

                resp = client.post("/api/jobs/runnow/run")
                assert resp.status_code == 200

                after_next = sched.get_job("runnow").next_run_time
                # Should be roughly "now" (within a couple seconds).
                assert after_next < datetime.now(timezone.utc) + timedelta(seconds=5)
            finally:
                stop_scheduler()

        asyncio.run(run())
