"""Task PR background sync: scheduled ticks that keep the task `prs`
table fresh so opening a task reads current data straight from the DB
(the DB acts as a cache).

The notification poller (`services/github_poller.py`) marks a task PR
`dirty=1` when a `github.*` notification touches it, but nothing
scheduled consumed that flag -- task-PR freshness only updated when the
PRs page was opened. These two ticks fill that gap:

  - `sync_task_prs_dirty_once`: high frequency; consumes the dirty flags
    (cheap -- zero cost when nothing is dirty).
  - `sync_task_prs_full_once`: low-frequency backstop; refreshes all open
    task PRs so a dropped notification doesn't leave a PR permanently
    stale.

Both refresh via `_batch_refresh_prs_via_graphql`, which persists each PR
through `_update_pr_from_gh` and emits `github.pr.updated` on a real
change -- the same SSE path the frontend already consumes. Only the
GraphQL rate-limit bucket is touched; the scarce search bucket and the
notification core bucket are untouched.

Register these as scheduler jobs in `server.py` startup; see
`services/scheduler.py` for the convention.
"""

import app_state
from common.prs import _batch_refresh_prs_via_graphql


def sync_task_prs_dirty_once() -> None:
    """One tick: refresh the PRs the notification poller marked dirty.

    Wrapped in try/except so a per-tick failure logs rather than
    propagating into the scheduler (which would mark the job failed)."""
    try:
        dirty = app_state._db.list_dirty_prs()
        if not dirty:
            return
        _, ok = _batch_refresh_prs_via_graphql(
            [{"number": p["number"], "url": p["url"]} for p in dirty]
        )
        for p in dirty:
            if p["number"] in ok:
                app_state._db.clear_pr_dirty(p["number"])
    except Exception as e:
        print(f"[pr-sync] dirty sync failed: {e}", flush=True)


def sync_task_prs_full_once() -> None:
    """One tick: backstop refresh of every open task PR.

    Wrapped in try/except for the same reason as the dirty tick."""
    try:
        open_prs = app_state._db.list_all_prs(status="open")
        if not open_prs:
            return
        _batch_refresh_prs_via_graphql(
            [{"number": p["number"], "url": p["url"]} for p in open_prs]
        )
    except Exception as e:
        print(f"[pr-sync] full sync failed: {e}", flush=True)
