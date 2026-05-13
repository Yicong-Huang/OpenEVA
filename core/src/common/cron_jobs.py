"""Long-running automation job CRUD.

Powers the Cron Jobs page: each row is a user-defined "run this
slash-command on this cadence" pairing. The Cron Jobs page renders
each job as a card (similar to ReviewCard / TaskCard) with a history
strip + run-now / pause / edit / delete buttons.

This module is the data layer; the actual scheduler integration
(reading enabled jobs, firing them on their cadence) lives in
`services/cron_runner.py` so the storage stays scheduler-agnostic and
testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import app_state


# Maximum length of stdout/stderr we persist per run. Job runs that
# produce more than this get truncated on write -- the live tail is
# still available via the session's tmux pane.
OUTPUT_EXCERPT_LIMIT = 4096


# ---- Schedule parsing ----

@dataclass(frozen=True)
class ParsedSchedule:
    """Normalized representation of a user-typed schedule string.

    `kind` decides how APScheduler is configured:
      - 'interval': fire every `interval_seconds` seconds.
      - 'cron':     classic 5-field cron expression in `cron_expr`.
      - 'invalid':  unparseable -- callers should refuse to register.

    We keep the original text in `original` so the UI can echo back
    exactly what the user typed (e.g. "30min" not the decomposed form).
    """
    kind: str  # 'interval' | 'cron' | 'invalid'
    original: str
    interval_seconds: int = 0
    cron_expr: str = ""
    error: str = ""


# Maps short-form units to seconds. `min` is included so "30min" parses
# the same as "30m" -- users type both interchangeably.
_UNIT_SECONDS: dict[str, int] = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}

_DURATION_RE = re.compile(
    r"^\s*(\d+)\s*(s|sec|secs|second|seconds|"
    r"m|min|mins|minute|minutes|"
    r"h|hr|hour|hours|d|day|days)\s*$",
    re.IGNORECASE,
)


def parse_schedule(text: str) -> ParsedSchedule:
    """Convert a user-typed schedule string into a normalised form.

    Accepted forms:
      - duration:   "30s", "30min", "2h", "1d", "5 minutes"  -> interval
      - cron 5fld:  "*/5 * * * *", "0 9 * * 1-5"            -> cron

    Anything else returns kind='invalid' with a hint in `error`.
    """
    raw = (text or "").strip()
    if not raw:
        return ParsedSchedule(kind="invalid", original=text or "",
                              error="empty schedule")
    m = _DURATION_RE.match(raw)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        seconds = n * _UNIT_SECONDS[unit]
        if seconds <= 0:
            return ParsedSchedule(kind="invalid", original=raw,
                                  error="schedule must be positive")
        return ParsedSchedule(kind="interval", original=raw,
                              interval_seconds=seconds)
    # Cron expression: 5 whitespace-separated fields, each containing
    # only the characters cron permits (digits, *, /, -, ,).
    fields = raw.split()
    if len(fields) == 5 and all(re.match(r"^[\d*/,\-?]+$", f) for f in fields):
        return ParsedSchedule(kind="cron", original=raw, cron_expr=raw)
    return ParsedSchedule(
        kind="invalid", original=raw,
        error=(
            "expected duration like '30min' / '2h' / '1d' or a 5-field "
            "cron expression like '*/5 * * * *'"
        ),
    )


def list_jobs() -> list[dict]:
    """Return all jobs, newest-first, enriched with session status."""
    return [_with_session(j) for j in app_state._db.list_cron_jobs()]


def get_job(job_id: int) -> dict | None:
    job = app_state._db.get_cron_job(job_id)
    return _with_session(job) if job else None


def _with_session(job: dict) -> dict:
    """Attach `session_name` so the frontend can index this job into
    the global session-status snapshot (`/api/sessions/snapshot`).

    No `session_alive` or `session_status` -- those redundant fields
    used to be computed here via tmux probes + `live_state`, but the
    frontend now reads them from the snapshot service. Stamping them
    again per row would just create two copies of the same data and
    encourage stale reads.
    """
    job_id = job.get("id")
    if not job_id:
        return job
    return {
        **job,
        "session_name": session_name_for_job(int(job_id), job.get("name") or ""),
    }


def create_job(*, name: str, schedule: str, command: str,
               description: str = "", enabled: bool = True) -> dict:
    """Validate + insert a new job. Raises ValueError on bad input
    so the route layer can return 422."""
    name = (name or "").strip()
    schedule = (schedule or "").strip()
    command = (command or "").strip()
    if not name:
        raise ValueError("name is required")
    if not schedule:
        raise ValueError("schedule is required (e.g. '30min', '2h', '0 9 * * *')")
    if not command:
        raise ValueError(
            "command is required (e.g. '/sync-prs' or any slash command)"
        )
    return app_state._db.create_cron_job(
        name=name, schedule=schedule, command=command,
        description=description, enabled=enabled,
    )


_UPDATABLE_FIELDS = {"name", "schedule", "command", "description", "enabled"}


def update_job(job_id: int, **fields: Any) -> dict | None:
    """Update one or more user-editable fields. Unknown keys are
    silently dropped so a stale UI doesn't 422 on a key we removed."""
    safe = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
    return app_state._db.update_cron_job(job_id, **safe)


def delete_job(job_id: int) -> bool:
    return app_state._db.delete_cron_job(job_id)


# ---- Run history ----

def list_runs(job_id: int, limit: int = 20) -> list[dict]:
    return app_state._db.list_cron_job_runs(job_id, limit=limit)


def record_run_start(job_id: int) -> dict:
    """Stamp a new run row as `running`. Returns the run dict so the
    caller (scheduler) can pass run_id to record_run_end."""
    return app_state._db.create_cron_job_run(job_id=job_id, status="running")


def _truncate_excerpt(output: str) -> str:
    """Cap output to OUTPUT_EXCERPT_LIMIT, keeping the tail (where the
    useful summary usually lives)."""
    excerpt = output or ""
    if len(excerpt) > OUTPUT_EXCERPT_LIMIT:
        excerpt = "...[truncated]\n" + excerpt[-OUTPUT_EXCERPT_LIMIT:]
    return excerpt


def record_run_end(run_id: int, *, status: str, output: str = "",
                   error_message: str = "") -> dict | None:
    """Stamp a run as terminal. `output` is truncated to
    OUTPUT_EXCERPT_LIMIT to keep the row small."""
    return app_state._db.finish_cron_job_run(
        run_id, status=status, output_excerpt=_truncate_excerpt(output),
        error_message=error_message,
    )


def record_run_progress(run_id: int, output: str) -> dict | None:
    """Update a run's output_excerpt without stamping it terminal.

    Used after a successful session launch: the tick fired and the
    agent session is now running in the background, but we don't yet
    know when the agent will go idle. The run stays open (finished_at='')
    until the Stop hook fires (see `finish_run_for_session`).
    """
    return app_state._db.update_cron_job_run_output(
        run_id, _truncate_excerpt(output),
    )


def finish_run_for_session(session: str, *, status: str = "done",
                            output: str = "") -> dict | None:
    """Find the latest in-flight run for the cron job behind `session`
    and stamp it terminal. Called by the agent's Stop hook so the run's
    `finished_at` reflects the moment the agent actually went idle, not
    the moment the tick was queued.

    Returns the updated run dict, or None when:
      - `session` doesn't follow the cron-job-<id> naming convention,
      - no in-flight run exists for that job,
      - the job row was deleted between launch and idle.
    """
    job_id = _job_id_from_session(session)
    if job_id is None:
        return None
    open_run = app_state._db.latest_open_cron_job_run(job_id)
    if not open_run:
        return None
    # Concatenate the launch excerpt with whatever final summary the
    # caller provides, so the run row reflects both phases of the
    # lifecycle.
    prior = open_run.get("output_excerpt") or ""
    combined = (prior + ("\n" + output if output else "")).strip()
    return record_run_end(open_run["id"], status=status,
                           output=combined)


def _job_id_from_session(session: str) -> int | None:
    """Parse `cron-<slug>-<int>` -> int. Returns None on a non-cron name."""
    if not session or not session.startswith("cron-"):
        return None
    m = _CRON_SESSION_RE.match(session)
    if not m:
        return None
    try:
        return int(m.group("id"))
    except ValueError:
        return None


# ---- Manual trigger ("Run now" button) ----

def supersede_open_runs(job_id: int) -> int:
    """Close every in-flight run for `job_id` as `cancelled` so a new
    tick starts with a clean slate.

    Why: overlapping ticks (or crashed ticks where the Stop hook never
    fires) leave runs sitting in 'running' forever. Without this, the
    Stop hook only ever closes the *latest* open run, so anything
    older accumulates as stale rows. Calling this at tick-start time
    drains all such drifters in one go.

    Returns the count of runs that were closed.
    """
    open_runs = app_state._db.list_open_cron_job_runs(job_id)
    for r in open_runs:
        # Status `cancelled` makes the cause unmistakable in the run
        # history strip vs `done` (which means the agent actually finished).
        record_run_end(
            r["id"], status="cancelled",
            output=(r.get("output_excerpt") or ""),
            error_message="superseded by new tick",
        )
    return len(open_runs)


def run_now(job_id: int, executor=None) -> dict | None:
    """Trigger an immediate run of a job, bypassing the schedule.

    Before recording the new run, calls `supersede_open_runs(job_id)`
    to close any in-flight runs as `cancelled` -- this drains stale
    rows from crashed ticks / overlapping schedules so the run history
    can never grow unbounded.

    Records a `running` row, calls `executor(job)` to actually do the
    work, then leaves the row open (status='running') if the executor
    successfully launched a session. The row is stamped terminal:

      - immediately, by `record_run_end`, when the executor returns a
        terminal status (`failed` / `cancelled`) or raises -- nothing
        more is going to happen for that tick.
      - asynchronously, by `finish_run_for_session` from the agent's
        Stop hook, when the session goes idle. This is what the user
        wants for long-running cron jobs: end_time = "session done",
        not "tick fired".

    `executor` is injectable so tests can verify the lifecycle without
    spawning real agent sessions; production passes `_default_executor`
    which launches a tmux session.

    Returns the run dict in its current state, or None if the job
    doesn't exist.
    """
    job = get_job(job_id)
    if not job:
        return None
    supersede_open_runs(job_id)
    run = record_run_start(job_id)
    fn = executor or _default_executor
    try:
        result = fn(job)
        # `result` shape: {"status": "done"|"failed", "output": str,
        #                  "error": str (optional)}
        status = result.get("status", "done")
        output = result.get("output", "")
        error = result.get("error", "")
        if status == "done":
            # Session was launched/resumed. Leave the run open so the
            # Stop hook can stamp it when the agent actually finishes.
            return record_run_progress(run["id"], output)
        return record_run_end(
            run["id"], status=status, output=output, error_message=error,
        )
    except Exception as e:
        return record_run_end(
            run["id"], status="failed",
            output="", error_message=str(e)[:1024],
        )


_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def _slugify(text: str) -> str:
    """Lowercase, dash-separated, alphanumeric-only -- safe for tmux
    session names. Empty input returns 'job' as a stable fallback."""
    cleaned = _SLUG_RE.sub("-", text or "").strip("-").lower()
    return cleaned or "job"


# Trailing `-<int>` is the job_id; everything between `cron-` and
# the trailing id is the slug. Slug is decorative; only the id is
# used to look the row back up in the DB.
_CRON_SESSION_RE = re.compile(r"^cron-(?P<slug>.+)-(?P<id>\d+)$")


def session_name_for_job(job_id: int, name: str = "") -> str:
    """tmux session name for a cron job: `cron-<slug>-<id>`.

    Mirrors `reviews.session_name_for` (`review-<owner>-<repo>-<n>`)
    and `tickets.session_name_for_ticket` (`ticket-<KEY>`) so the
    session list is self-documenting -- a quick `tmux ls` tells you
    which job each pane is running.

    The trailing `-<id>` keeps names unique even if the user creates
    two jobs with the same display name. The slug is decorative;
    `_job_id_from_session` only parses the id back out.
    """
    return f"cron-{_slugify(name)}-{int(job_id)}"


def _default_executor(job: dict) -> dict:
    """Launch a fresh tmux + agent session for this tick, with the
    job's command pre-injected as the first user message.

    Each tick gets a clean session: any prior tmux + agent instance
    for this cron job is torn down first, then we relaunch with
    `<agent-binary> -n NAME "<command>" --append-system-prompt "..."`. The
    positional argument lands the slash command cleanly through
    claude's argv, bypassing the input-box / autocomplete dropdown
    that previously corrupted dash-bearing commands like
    `/yh-code-sync-my-prs` into `/y` + scattered fragments. (See the
    long-form notes that used to live around `paste_text` for the
    historical race.)

    Tradeoff: the user can't keep a single resumed session across
    ticks. For cron jobs that's the right shape -- each tick is a
    one-shot "run this command", not an interactive conversation. A
    user attached to the pane gets disconnected when the next tick
    fires; they can re-attach to the new session by tmux name.
    """
    # Lazy import: keeps `cron_jobs` importable in unit tests
    # without pulling in subprocess + tmux machinery.
    from adapters import tmux as _tmux

    job_id = job.get("id")
    command = (job.get("command") or "").strip()
    name = job.get("name") or f"job-{job_id}"
    if not command:
        return {"status": "failed", "output": "",
                "error": "job has empty command"}
    session = session_name_for_job(int(job_id), name) if job_id else f"cron-{_slugify(name)}"

    bg_system = _build_job_system_prompt(job)

    # Tear down any prior session for this job before relaunching.
    # `graceful_kill_session` sends Ctrl+C first so the agent persists
    # any in-flight state via its own shutdown path, then kills the
    # tmux pane. Best-effort -- if the session doesn't exist the
    # call is a no-op.
    relaunched = False
    if _tmux.session_exists(session):
        _tmux.graceful_kill_session(session)
        relaunched = True

    # Positional prompt: claude accepts a trailing prompt argument
    # (`claude "your prompt"`) and the agent passes it through. Lands as
    # the first user message in the session -- no input-box typing,
    # no autocomplete race. argv comes from the active Agent so
    # this works regardless of which CLI is configured (claude /
    # agent variants).
    from . import agent as _agent
    argv = _agent.launch_argv(session, system_prompt=bg_system, prompt=command)
    _tmux.launch_session_argv(session, "~", argv)
    tag = "[relaunched]" if relaunched else "[launched]"
    return {
        "status": "done",
        "output": (
            f"{tag} session={session} cmd={command}. "
            f"Attach with `tmux a -t {session}` to watch."
        ),
    }


def _build_job_system_prompt(job: dict) -> str:
    """Render a small system-prompt block describing the cron job.
    Helps the agent understand it's running inside a recurring loop and
    not a fresh ad-hoc session."""
    lines = [
        f"# Cron Job: {job.get('name', '?')}",
        "",
        f"Schedule: {job.get('schedule', '')}",
        f"Command:  {job.get('command', '')}",
    ]
    desc = (job.get("description") or "").strip()
    if desc:
        lines += ["", "## Description", desc]
    lines += [
        "",
        "You're running inside an Eva-managed cron loop. The command "
        "above is run once per scheduled tick. When done, exit cleanly "
        "so the next tick starts fresh.",
    ]
    return "\n".join(lines)
