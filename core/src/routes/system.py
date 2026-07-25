"""System routes: certs/auth freshness, agent usage."""

import subprocess  # noqa: F401 -- patched by tests (routes.system.subprocess.run)

from pydantic import BaseModel

from fastapi import HTTPException

import app_state

from common.system import (
    get_certs as _get_certs,
    renew_cert as _renew_cert,
    get_usage as _get_usage,
    get_usage_history as _get_usage_history,
    get_live_stats as _get_live_stats,
    get_workstats as _get_workstats,
    get_setup_status as _get_setup_status,
    _init_usage_db,  # noqa: F401 -- re-exported via server.py
    _save_usage_record,  # noqa: F401 -- re-exported via server.py
    _usage_cache,  # noqa: F401 -- accessed by tests via routes.system._usage_cache
    _USAGE_DB_PATH,  # noqa: F401 -- re-exported via server.py
)


@app_state.app.get("/api/certs")
def get_certs():
    """Check freshness of various auth credentials."""
    return _get_certs()


@app_state.app.post("/api/certs/renew/{cert_id}")
def renew_cert(cert_id: str):
    """Manually trigger cert renewal."""
    result = _renew_cert(cert_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No renew command for {cert_id}")
    return result


@app_state.app.get("/api/usage")
def get_usage():
    """Get the active agent's usage summary. Caches for 2 min, background refresh."""
    return _get_usage()


@app_state.app.get("/api/usage/history")
def get_usage_history(days: int = 7):
    """Return usage history from SQLite."""
    return _get_usage_history(days)


@app_state.app.get("/api/live-stats")
def get_live_stats(refresh: int = 0):
    """Live PR counts and Widgets contributor rank."""
    return _get_live_stats(refresh=bool(refresh))


@app_state.app.get("/api/workstats")
def get_workstats(refresh: int = 0):
    """Return PR stats (commits, reviews, etc.)."""
    return _get_workstats(refresh=bool(refresh))


@app_state.app.get("/api/system/setup-status")
def get_setup_status():
    """First-boot health check: gh CLI, accounts, allow-list, account
    rules. Powers the Settings -> Setup tab + top-of-app banner."""
    return _get_setup_status()


# -- Background jobs introspection --

@app_state.app.get("/api/jobs")
def list_scheduled_jobs():
    """Return every background job registered with the scheduler, with its
    trigger and next run time. Paused jobs have `next_run = null`."""
    from services.scheduler import describe_jobs
    return {"jobs": describe_jobs()}


@app_state.app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str):
    """Pause a scheduled job. Idempotent."""
    from services.scheduler import get_scheduler
    sched = get_scheduler()
    if sched.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")
    sched.pause_job(job_id)
    return {"ok": True, "paused": True}


@app_state.app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    """Resume a paused job. Idempotent."""
    from services.scheduler import get_scheduler
    sched = get_scheduler()
    if sched.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")
    sched.resume_job(job_id)
    return {"ok": True, "paused": False}


@app_state.app.post("/api/jobs/{job_id}/run")
def run_job_now(job_id: str):
    """Trigger a one-off run of the job immediately, regardless of schedule.
    Doesn't affect the normal schedule."""
    from services.scheduler import get_scheduler
    from datetime import datetime, timezone
    sched = get_scheduler()
    job = sched.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")
    sched.modify_job(job_id, next_run_time=datetime.now(timezone.utc))
    return {"ok": True}


@app_state.app.get("/api/search")
def search(q: str = "", limit: int = 20):
    """Global search across tasks, tickets, reviews, sessions, and PRs.
    Supports filter DSL:
    type:task|ticket|review|pr|session, status:<v>, project:<id>, in:task."""
    from common.search import search as core_search
    return {"results": core_search(q, limit=limit)}


@app_state.app.get("/api/me")
def get_current_user():
    """Return the current user's GitHub logins and repo-account mapping."""
    repo_account = {}
    for org in app_state.ALLOWED_ORGS:
        repo_account[org] = app_state.gh_account_for_repo(f"{org}/x")
    repo_account["default"] = app_state.gh_account_for_repo("default")
    return {
        "logins": list(app_state._gh_tokens.keys()),
        "repoAccount": repo_account,
    }


# -- Slack Monitor --

from services import slack_monitor as _slack


@app_state.app.get("/api/slack-monitor")
def get_slack_monitor_status():
    """Get Slack monitor status and channels."""
    return {
        "running": _slack.is_running(),
        "channels": _slack.list_channels(),
    }


@app_state.app.post("/api/slack-monitor/start")
def start_slack_monitor():
    """Start the Slack monitor."""
    ok = _slack.start()
    return {"ok": ok, "running": _slack.is_running()}


@app_state.app.post("/api/slack-monitor/stop")
def stop_slack_monitor():
    """Stop the Slack monitor."""
    _slack.stop()
    return {"ok": True}


class SlackChannelAdd(BaseModel):
    channel_id: str = ""
    name: str = ""


@app_state.app.post("/api/slack-monitor/channels")
def add_slack_channel(body: SlackChannelAdd):
    """Add a channel to monitor. Body: `{channel_id, name?}`."""
    if not body.channel_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="channel_id required")
    _slack.add_channel(body.channel_id, body.name)
    return {"ok": True, "channels": _slack.list_channels()}


@app_state.app.delete("/api/slack-monitor/channels/{channel_id}")
def remove_slack_channel(channel_id: str):
    """Remove a channel from monitoring."""
    _slack.remove_channel(channel_id)
    return {"ok": True}


# Per-plugin routes (e.g. `/api/boba`) live with the plugin under
# its extension namespace and are mounted via `<Plugin>.register(app)`.
# The framework calls that during `_initialize_plugins()` at server
# startup.
