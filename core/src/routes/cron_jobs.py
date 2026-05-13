"""HTTP routes for the Cron Jobs page.

CRUD over user-defined long-running agent jobs (e.g. "every 30 min run
/sync-prs", or any slash command on a cadence). Each route is a thin
Pydantic-validated wrapper around `cron_jobs`.
"""

from typing import Optional

from pydantic import BaseModel
from fastapi import HTTPException

import app_state
from common import cron_jobs as _core
from services import cron_runner as _runner
from utils import clamp_int


class CronJobCreate(BaseModel):
    name: str
    schedule: str
    command: str
    description: str = ""
    enabled: bool = True


class CronJobUpdate(BaseModel):
    """All fields optional -- partial PATCH semantics."""
    name: Optional[str] = None
    schedule: Optional[str] = None
    command: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None


@app_state.app.get("/api/cron-jobs")
def list_cron_jobs():
    """Return every saved cron job, newest-first."""
    return {"jobs": _core.list_jobs()}


# NOTE: this route MUST be declared before the `/{job_id}` parametric
# routes -- FastAPI matches in declaration order, and "parse-schedule"
# would otherwise be coerced into the int job_id slot and 422.
@app_state.app.get("/api/cron-jobs/parse-schedule")
def parse_schedule_endpoint(text: str):
    """Validate a schedule string before saving. Used by the UI to
    show a preview ("every 30 minutes") and a red error border on bad
    input."""
    parsed = _core.parse_schedule(text)
    return {
        "kind": parsed.kind, "original": parsed.original,
        "interval_seconds": parsed.interval_seconds,
        "cron_expr": parsed.cron_expr,
        "error": parsed.error,
    }


@app_state.app.post("/api/cron-jobs", status_code=201)
def create_cron_job(body: CronJobCreate):
    """Create a new cron job. 422 on missing required fields."""
    try:
        job = _core.create_job(
            name=body.name, schedule=body.schedule, command=body.command,
            description=body.description, enabled=body.enabled,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    _runner.sync_job(job["id"])
    return job


@app_state.app.get("/api/cron-jobs/{job_id}")
def get_cron_job(job_id: int):
    job = _core.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return job


@app_state.app.patch("/api/cron-jobs/{job_id}")
def update_cron_job(job_id: int, body: CronJobUpdate):
    if not _core.get_job(job_id):
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    out = _core.update_job(job_id, **fields)
    _runner.sync_job(job_id)
    return out


@app_state.app.delete("/api/cron-jobs/{job_id}")
def delete_cron_job(job_id: int):
    if not _core.delete_job(job_id):
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    _runner.unregister_job(job_id)
    return {"ok": True}


@app_state.app.get("/api/cron-jobs/{job_id}/runs")
def list_cron_job_runs(job_id: int, limit: int = 20):
    """History strip data for a job card: most-recent N runs.

    `limit` is clamped to [1, 500] -- the history strip is bounded UI
    real estate and the underlying tmux-output capture is not free
    (each run carries a few hundred lines of paste output). Hostile /
    typoed callers can no longer ask for an unbounded scan.
    """
    if not _core.get_job(job_id):
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    safe_limit = clamp_int(limit, 1, 500)
    return {"runs": _core.list_runs(job_id, limit=safe_limit)}


@app_state.app.post("/api/cron-jobs/{job_id}/run")
def run_cron_job_now(job_id: int):
    """Trigger an immediate run of a job, bypassing its schedule.

    Returns the run row (with terminal status). 404 if the job doesn't
    exist."""
    run = _core.run_now(job_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return run
