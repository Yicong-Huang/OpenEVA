"""Periodic JIRA -> tickets-cache sync.

Runs on the same APScheduler that powers the other built-in services.
Each tick is a no-op when:

  - JIRA isn't configured (open-source first-run -- nothing to sync)
  - The plugin master toggle is off (Settings -> Plugins -> JIRA)
  - The configured interval has been overridden to <=0 (pause via UI
    without untoggling)

We don't bubble JIRA failures up: a transient outage shouldn't kill
the scheduler thread or spam the events feed. Errors print once for
local-dev visibility and the next tick retries.
"""

from __future__ import annotations

from common import settings as _settings
from common import tickets as _tickets


# Default cadence -- 5 minutes. Settings key
# `service.jira.sync_interval_seconds` overrides at startup.
JIRA_SYNC_INTERVAL_SECONDS = 300

# Scheduler job id -- exposed so tests / `/api/jobs` can reference.
JIRA_SYNC_JOB_ID = "jira_sync"

# Settings key for the user-tunable interval. Validation + clamping
# lives in `common.settings.get_interval_seconds`; we just bind a name
# and a default + the JIRA-specific 30s floor (Cloud rate limits).
KEY_JIRA_SYNC_INTERVAL = "service.jira.sync_interval_seconds"


def get_interval_seconds() -> int:
    """Read the user-overridden interval, with sensible bounds. The
    floor is 30s (JIRA rate limits) and the ceiling 24h. Delegates to
    the shared `_settings.get_interval_seconds` so all pollers share
    one validation contract."""
    return _settings.get_interval_seconds(
        KEY_JIRA_SYNC_INTERVAL, JIRA_SYNC_INTERVAL_SECONDS,
        min_s=30, max_s=86400,
    )


def sync_tickets_once() -> None:
    """One scheduler tick. No-op when JIRA isn't configured or the
    plugin is disabled. Failures are swallowed (and printed)."""
    if not _settings.is_plugin_enabled("jira"):
        return
    if not _tickets.is_configured():
        return
    try:
        _tickets.sync()
    except Exception as e:
        # Single line so `tail -f` is readable; full stack lives in
        # the FastAPI error stream when relevant.
        print(f"[jira-sync] error: {e}", flush=True)
