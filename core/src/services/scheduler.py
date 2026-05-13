"""Eva background job scheduler.

Wraps `apscheduler.AsyncIOScheduler` to give every background task one
consistent lifecycle:

- Runs on the same asyncio event loop uvicorn uses (no extra threads per
  service).
- Sync jobs are dispatched to an executor (safe: a slow `gh` subprocess
  blocks its own thread, never the FastAPI request path).
- `coalesce=True` + `max_instances=1` stop a slow job from piling up.
- `start_scheduler()` / `stop_scheduler()` are called from the FastAPI
  lifespan hook so reloads don't leak jobs.

Registering a new job is a one-liner:

    from services.scheduler import get_scheduler
    get_scheduler().add_job(my_fn, "interval", seconds=30, id="my_job")
    get_scheduler().add_job(my_fn, "cron", day_of_week="mon", hour=9, id="weekly")

See `docs/architecture.md` for the sync-first convention (sync job bodies
run in the executor; only declare `async def` if every IO inside is
awaitable).
"""

from __future__ import annotations

from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler


_JOB_DEFAULTS = {
    # Drop backlogged triggers that piled up while a slow run was in
    # progress and only fire once when the runner frees up.
    "coalesce": True,
    # Never run two copies of the same job in parallel. A stuck `gh`
    # subprocess would otherwise spawn more and more copies.
    "max_instances": 1,
    # How late a missed trigger can still fire (seconds). Past this window
    # we skip the run entirely and wait for the next cron/interval tick.
    "misfire_grace_time": 30,
}


# Lazy singleton. Created on first `get_scheduler()` call so importing
# this module is side-effect free (important for tests).
_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    """Return the process-wide scheduler, creating it on first access."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(job_defaults=_JOB_DEFAULTS)
    return _scheduler


def start_scheduler() -> None:
    """Start the scheduler if it isn't already running. Idempotent."""
    sched = get_scheduler()
    if not sched.running:
        sched.start()


def stop_scheduler() -> None:
    """Stop the scheduler if running. `wait=False` so we don't block
    shutdown on in-flight jobs."""
    sched = get_scheduler()
    if sched.running:
        sched.shutdown(wait=False)


def reset_scheduler() -> None:
    """Tear down the singleton. Only used by tests to guarantee isolation
    between suites that may each start and stop the scheduler.

    Shutdown failures are swallowed: after a test ran inside `asyncio.run`
    the event loop AsyncIOScheduler bound to is already closed, and
    `shutdown(wait=False)` tries to call into it. The instance is about to
    be discarded anyway, so the error is safe to ignore."""
    global _scheduler
    if _scheduler is not None:
        try:
            if _scheduler.running:
                _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None


def describe_jobs() -> list[dict[str, Any]]:
    """Return a JSON-safe summary of every registered job for `/api/jobs`.

    `next_run_time` is ISO 8601, or None if the job is paused. On a stopped
    scheduler, `next_run_time` may be unset; getattr with a default keeps
    us from raising there (it just reads as `paused=True` in that case)."""
    out = []
    sched = get_scheduler()
    for job in sched.get_jobs():
        nrt = getattr(job, "next_run_time", None)
        next_run = nrt.isoformat() if nrt else None
        out.append({
            "id": job.id,
            "name": job.name or job.id,
            "trigger": str(job.trigger),
            "next_run": next_run,
            "paused": next_run is None,
        })
    return out
