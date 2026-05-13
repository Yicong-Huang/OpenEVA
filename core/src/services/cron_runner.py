"""Bridge between user-defined cron jobs (in `cron_jobs` table) and
APScheduler.

Each enabled row in the `cron_jobs` table maps to one APScheduler job
with id `cron-<row_id>`. The trigger is derived from
`common.cron_jobs.parse_schedule` so the same string the user typed is
the source of truth. When the scheduler fires, we call back into
`common.cron_jobs.run_now` so the run lifecycle (running -> done /
failed, history strip) stays consistent between the manual "Run now"
button and the automatic firings.

Lifecycle:
  - `register_all()` runs once at server startup, after the scheduler
    starts, and arms every enabled job.
  - `sync_job(job_id)` is called from the routes layer after every
    create / update / delete so a stale schedule never lingers.
  - `unregister_job(job_id)` is the cleanup half of `sync_job`.

The scheduler ID prefix avoids collisions with the built-in jobs
(`github_poller`, `cert_checker`, etc.) -- a numeric collision would
otherwise be possible.
"""

from __future__ import annotations

from typing import Any

from common import cron_jobs as _core
from services.scheduler import get_scheduler


# Prefix the AP Scheduler job id so user jobs never collide with the
# built-in service jobs.
_JOB_ID_PREFIX = "cron-"


def _scheduler_job_id(row_id: int) -> str:
    return f"{_JOB_ID_PREFIX}{row_id}"


def _trigger_for(parsed: _core.ParsedSchedule) -> dict[str, Any] | None:
    """Translate a parsed schedule into APScheduler add_job kwargs.

    Returns None when the schedule is invalid -- caller should skip
    registering and surface the error in the UI rather than crashing
    the scheduler thread.
    """
    if parsed.kind == "interval":
        return {"trigger": "interval", "seconds": parsed.interval_seconds}
    if parsed.kind == "cron":
        # APScheduler accepts a CronTrigger.from_crontab on a 5-field
        # string. Decompose here so we don't depend on apscheduler
        # internals from callers.
        from apscheduler.triggers.cron import CronTrigger
        return {"trigger": CronTrigger.from_crontab(parsed.cron_expr)}
    return None


def _run_callback(row_id: int) -> None:
    """Top-level scheduler callback -- recorded as a separate function
    (rather than a closure) so APScheduler's serialiser path can pick
    it up and so test mocks have a stable target."""
    try:
        _core.run_now(row_id)
    except Exception as e:
        # Never let the runner crash kill the scheduler thread; just
        # log. The run_now path already records its own failures.
        print(f"[cron-runner] run_now({row_id}) raised: {e}", flush=True)


def register_job(job_row: dict) -> bool:
    """Arm one job on the scheduler. Returns True when registered,
    False when the schedule was invalid or the job is disabled."""
    if not job_row.get("enabled"):
        return False
    parsed = _core.parse_schedule(job_row.get("schedule", ""))
    trigger = _trigger_for(parsed)
    if trigger is None:
        return False
    sched = get_scheduler()
    job_id = _scheduler_job_id(job_row["id"])
    # Idempotent: replace any existing instance so updates take effect
    # immediately without leaking the old trigger.
    if sched.get_job(job_id):
        sched.remove_job(job_id)
    sched.add_job(
        _run_callback,
        kwargs={"row_id": job_row["id"]},
        id=job_id,
        name=job_row.get("name", job_id),
        replace_existing=True,
        **trigger,
    )
    return True


def unregister_job(row_id: int) -> bool:
    """Remove a job from the scheduler. Returns True on removal,
    False when no job was registered (idempotent caller path)."""
    sched = get_scheduler()
    job_id = _scheduler_job_id(row_id)
    if not sched.get_job(job_id):
        return False
    sched.remove_job(job_id)
    return True


def sync_job(row_id: int) -> bool:
    """Reconcile scheduler state for one job row. Called after any
    create/update/delete so the schedule edit takes effect at most one
    second after save (no server restart)."""
    job = _core.get_job(row_id)
    if job is None:
        return unregister_job(row_id)
    # Pause has the same wire effect as a re-arm with enabled=False
    # below: unregister.
    if not job.get("enabled"):
        return unregister_job(row_id)
    return register_job(job)


def register_all() -> dict:
    """Boot-time pass: arm every enabled job. Returns a summary
    `{registered, skipped, invalid}` so the startup log can report
    how the inventory landed."""
    registered = 0
    skipped = 0
    invalid = 0
    for job in _core.list_jobs():
        if not job.get("enabled"):
            skipped += 1
            continue
        if register_job(job):
            registered += 1
        else:
            invalid += 1
    return {
        "registered": registered, "skipped": skipped, "invalid": invalid,
    }
