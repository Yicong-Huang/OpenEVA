"""AI usage refresh: scheduled tick that keeps `common.system._usage_cache`
warm and pushes `usage.updated` events to subscribed clients.

Previously the frontend polled `/api/usage` every few minutes and the
backend lazily re-fetched on stale-read. This module replaces that with a
single scheduler-driven refresh so:

- `GET /api/usage` is always a pure dict read (no subprocess in the
  request path).
- The dashboard re-renders on the push-only `usage.updated` event via
  SSE instead of `setInterval` + HTTP.

Register this as a scheduler job in `server.py` startup; see
`services/scheduler.py` for the convention.
"""

import time as _time

import app_state
from common import settings as _settings
from common.system import _fetch_usage, _usage_cache


# Default cadence -- 2 minutes. Settings key
# `service.intervals.usage_refresh_seconds` overrides at startup.
USAGE_REFRESH_INTERVAL_SECONDS = 120

# Floor: usage fetch shells out (`the agent's usage`); under 30s and we're
# spawning subprocesses faster than the user can notice fresher data.
_MIN_USAGE_INTERVAL_SECONDS = 30


def get_interval_seconds() -> int:
    """User-tunable usage-refresh cadence. Delegates to the shared
    poller-cadence accessor for one validation contract across services."""
    return _settings.get_interval_seconds(
        _settings.KEY_INTERVAL_USAGE_REFRESH,
        USAGE_REFRESH_INTERVAL_SECONDS,
        min_s=_MIN_USAGE_INTERVAL_SECONDS,
    )


def refresh_usage_once() -> None:
    """Scheduler tick: fetch fresh usage + update cache + emit event.

    On success emits a persisted `usage.updated` event so the notification
    feed records the refresh and the frontend re-renders. Any failure
    (subprocess error, event-bus failure) is caught so one bad tick never
    kills the job."""
    try:
        data = _fetch_usage()
    except Exception as e:
        print(f"[usage-refresh] error: {e}", flush=True)
        return
    if not data:
        return
    _usage_cache["data"] = data
    _usage_cache["ts"] = _time.time()
    try:
        app_state.emit_event("usage.updated", {
            "title": "AI usage refreshed",
            "message": (f"daily={data.get('daily')} "
                        f"weekly={data.get('weekly')} "
                        f"monthly={data.get('monthly')}"),
            "severity": "info",
        })
    except Exception as e:
        # Event-bus failure must not kill the tick -- cache is still fresh.
        print(f"[usage-refresh] emit_event failed: {e}", flush=True)
