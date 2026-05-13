"""Tests for services.cron_runner -- the bridge between user-defined
cron_jobs rows and APScheduler.

We don't actually start the scheduler in these tests (it would fire
real jobs against the asyncio loop). Instead we use the singleton in
its stopped state and read its job list directly. `services.scheduler.
reset_scheduler()` ensures isolation between tests.
"""

from unittest.mock import MagicMock, patch

import pytest

from common import cron_jobs as core_cron
from services import cron_runner
from services.scheduler import reset_scheduler, get_scheduler


@pytest.fixture(autouse=True)
def _isolate_scheduler():
    """Each test gets a clean scheduler instance so registered jobs
    don't leak between cases."""
    reset_scheduler()
    yield
    reset_scheduler()


# ---- _trigger_for ----

class TestTriggerFor:
    def test_interval_trigger(self):
        parsed = core_cron.parse_schedule("30min")
        kw = cron_runner._trigger_for(parsed)
        assert kw == {"trigger": "interval", "seconds": 1800}

    def test_cron_trigger(self):
        parsed = core_cron.parse_schedule("0 9 * * 1-5")
        kw = cron_runner._trigger_for(parsed)
        assert kw is not None
        assert kw["trigger"] is not None  # CronTrigger object

    def test_invalid_returns_none(self):
        parsed = core_cron.parse_schedule("garbage")
        assert cron_runner._trigger_for(parsed) is None


# ---- register_job / unregister_job ----

class TestRegisterJob:
    def test_register_arms_an_apscheduler_job(self, patched_server):
        job = core_cron.create_job(name="x", schedule="30min", command="/x")
        assert cron_runner.register_job(job) is True
        sched = get_scheduler()
        ap_job = sched.get_job(f"cron-{job['id']}")
        assert ap_job is not None
        assert ap_job.name == "x"

    def test_register_skipped_when_disabled(self, patched_server):
        job = core_cron.create_job(name="x", schedule="30min", command="/x",
                                   enabled=False)
        assert cron_runner.register_job(job) is False
        assert get_scheduler().get_job(f"cron-{job['id']}") is None

    def test_register_skipped_for_invalid_schedule(self, patched_server):
        job = core_cron.create_job(name="x", schedule="not-valid",
                                   command="/x")
        # parse_schedule rejects -> register returns False, no job armed.
        assert cron_runner.register_job(job) is False
        assert get_scheduler().get_job(f"cron-{job['id']}") is None

    def test_register_replaces_existing_arm(self, patched_server):
        job = core_cron.create_job(name="x", schedule="30min", command="/x")
        cron_runner.register_job(job)
        # Edit schedule -> re-register. Old arm must be gone.
        updated = core_cron.update_job(job["id"], schedule="2h")
        cron_runner.register_job(updated)
        ap_job = get_scheduler().get_job(f"cron-{job['id']}")
        # Trigger reflects the new interval (2 hours = 2:00:00).
        assert "2:00:00" in str(ap_job.trigger)

    def test_unregister_removes_job(self, patched_server):
        job = core_cron.create_job(name="x", schedule="30min", command="/x")
        cron_runner.register_job(job)
        assert cron_runner.unregister_job(job["id"]) is True
        assert get_scheduler().get_job(f"cron-{job['id']}") is None
        # Idempotent: second call returns False, doesn't raise.
        assert cron_runner.unregister_job(job["id"]) is False


# ---- sync_job ----

class TestSyncJob:
    def test_sync_arms_when_enabled(self, patched_server):
        job = core_cron.create_job(name="x", schedule="30min", command="/x")
        assert cron_runner.sync_job(job["id"]) is True
        assert get_scheduler().get_job(f"cron-{job['id']}") is not None

    def test_sync_disarms_when_disabled(self, patched_server):
        job = core_cron.create_job(name="x", schedule="30min", command="/x")
        cron_runner.sync_job(job["id"])  # arm
        core_cron.update_job(job["id"], enabled=False)
        cron_runner.sync_job(job["id"])  # should disarm
        assert get_scheduler().get_job(f"cron-{job['id']}") is None

    def test_sync_disarms_when_row_deleted(self, patched_server):
        job = core_cron.create_job(name="x", schedule="30min", command="/x")
        cron_runner.sync_job(job["id"])
        core_cron.delete_job(job["id"])
        cron_runner.sync_job(job["id"])
        assert get_scheduler().get_job(f"cron-{job['id']}") is None


# ---- register_all ----

class TestRegisterAll:
    def test_arms_only_enabled_jobs(self, patched_server):
        # Wipe any pre-seeded jobs from earlier tests.
        for j in core_cron.list_jobs():
            core_cron.delete_job(j["id"])
        a = core_cron.create_job(name="a", schedule="30min", command="/a")
        b = core_cron.create_job(name="b", schedule="2h", command="/b",
                                 enabled=False)
        c = core_cron.create_job(name="c", schedule="garbage", command="/c")

        out = cron_runner.register_all()
        assert out["registered"] == 1  # only `a`
        assert out["skipped"] == 1     # `b` disabled
        assert out["invalid"] == 1     # `c` bad schedule

        sched = get_scheduler()
        assert sched.get_job(f"cron-{a['id']}") is not None
        assert sched.get_job(f"cron-{b['id']}") is None
        assert sched.get_job(f"cron-{c['id']}") is None


# ---- run callback ----

class TestRunCallback:
    def test_callback_invokes_run_now(self, patched_server):
        job = core_cron.create_job(name="x", schedule="30min", command="/x")
        with patch("services.cron_runner._core.run_now") as m:
            cron_runner._run_callback(job["id"])
        m.assert_called_once_with(job["id"])

    def test_callback_swallows_exception(self, patched_server):
        # An exception inside run_now must NOT crash -- the scheduler
        # thread has to keep running for the next tick.
        with patch("services.cron_runner._core.run_now",
                   side_effect=RuntimeError("boom")):
            cron_runner._run_callback(99999)  # should not raise


# ---- Route integration: HTTP mutations sync the scheduler ----

class TestRouteSync:
    def test_create_via_api_arms_scheduler(self, client):
        resp = client.post("/api/cron-jobs", json={
            "name": "via-api", "schedule": "30min", "command": "/x",
        })
        assert resp.status_code == 201
        body = resp.json()
        sched = get_scheduler()
        assert sched.get_job(f"cron-{body['id']}") is not None

    def test_patch_disable_disarms_scheduler(self, client):
        created = client.post("/api/cron-jobs", json={
            "name": "x", "schedule": "30min", "command": "/x",
        }).json()
        client.patch(f"/api/cron-jobs/{created['id']}",
                     json={"enabled": False})
        sched = get_scheduler()
        assert sched.get_job(f"cron-{created['id']}") is None

    def test_delete_disarms_scheduler(self, client):
        created = client.post("/api/cron-jobs", json={
            "name": "x", "schedule": "30min", "command": "/x",
        }).json()
        client.delete(f"/api/cron-jobs/{created['id']}")
        sched = get_scheduler()
        assert sched.get_job(f"cron-{created['id']}") is None
