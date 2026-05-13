"""Cert status check: registered as a scheduled interval job.

Previously this module owned a daemon thread with its own `while True:
sleep()` loop. That was replaced by `services.scheduler` -- this module
now just exports the per-tick function and its cadence.
"""

from common import settings as _settings
from common.system import get_certs


# Default cadence -- 5 minutes. Settings key
# `service.intervals.cert_check_seconds` overrides at startup.
CERT_CHECK_INTERVAL_SECONDS = 300

# Floor: cert lookup hits a real network endpoint per tick; under 30s
# adds load without surfacing meaningfully fresher data.
_MIN_CERT_INTERVAL_SECONDS = 30


def get_interval_seconds() -> int:
    """User-tunable cert-check cadence. Same validation contract as
    every other poller -- delegates to `_settings.get_interval_seconds`
    so jira_sync, ubereats, slack_monitor, etc. all share one read +
    clamp implementation."""
    return _settings.get_interval_seconds(
        _settings.KEY_INTERVAL_CERT_CHECK,
        CERT_CHECK_INTERVAL_SECONDS,
        min_s=_MIN_CERT_INTERVAL_SECONDS,
    )


def check_certs_once() -> None:
    """Refresh cert status. Called by the scheduler once per interval.

    No-op when the user disabled cert tracking via the Settings UI.
    Errors are swallowed (and printed) so one failing cert doesn't kill
    the job -- scheduler would reschedule anyway, but logging makes the
    failure visible."""
    if not _settings.is_plugin_enabled("cert_tracker"):
        return
    try:
        get_certs()
    except Exception as e:
        print(f"[cert-check] error: {e}", flush=True)
