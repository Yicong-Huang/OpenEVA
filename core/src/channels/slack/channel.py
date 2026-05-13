"""Slack channel: polls watched Slack channels and emits `slack.message`
events into the bus.

Auth is xoxc-token + browser cookie pair (loaded from
`~/.eva/slack-credentials.json` via `adapters.slack`). Grey area under
Slack TOS but matches what every Eva install actually has access to
without operating an OAuth callback server.

Lifecycle:
  * `start()` loads creds, verifies auth, unpauses the scheduled job
  * `stop()` pauses the scheduled job
  * `poll_slack_once()` is the one tick; no-op when creds aren't
    loaded, the channel is disabled, or no Slack channels are added

The module previously lived at `services/slack_monitor.py`; a thin
re-export shim still exposes it under that path for back-compat.
"""

import time

import app_state
from adapters import slack as _slack
from common import settings as _settings


# Channel identity (used by channels registry + per-channel
# settings keys like `channel.<id>.<key>`).
CHANNEL_ID = "slack"
CHANNEL_LABEL = "Slack"

# Polling config -- 30s default; settings key
# `service.intervals.slack_monitor_seconds` overrides at startup.
SLACK_POLL_INTERVAL_SECONDS = 30
_POLL_INTERVAL = SLACK_POLL_INTERVAL_SECONDS  # back-compat alias
_CREDS_PATH = _slack._CREDS_PATH  # re-export for tests

SLACK_JOB_ID = "slack_monitor"

# Floor: 10s -- Slack rate limits start at 1 req/sec on user tokens;
# polling N channels every 10s stays well under, and faster cadences
# rarely show meaningfully fresher messages.
_MIN_SLACK_POLL_INTERVAL_SECONDS = 10


def get_interval_seconds() -> int:
    """User-tunable Slack-poll cadence. Delegates to the shared
    poller-cadence accessor for consistent validation."""
    return _settings.get_interval_seconds(
        _settings.KEY_INTERVAL_SLACK_MONITOR,
        SLACK_POLL_INTERVAL_SECONDS,
        min_s=_MIN_SLACK_POLL_INTERVAL_SECONDS,
    )

# State
_channels = {}  # channel_id -> {"name": str, "last_ts": str}
_token = ""
_cookie = ""
_creds_loaded = False


def _load_credentials():
    """Load Slack xoxc token + cookie. Back-compat wrapper over the
    adapter's `load_creds()` that preserves the module-state contract
    (tests still read `_token`/`_cookie` directly)."""
    global _token, _cookie
    creds = _slack.load_creds()
    if creds is None:
        return False
    _token = creds.token
    _cookie = creds.cookie
    return True


def _slack_api(endpoint, params):
    """Call Slack API. Thin wrapper that packages module-state creds into
    a `SlackCreds` and delegates to `adapters.slack.call`. Kept on this
    module so existing tests that monkeypatch `_slack_api` still work."""
    return _slack.call(_slack.SlackCreds(_token, _cookie), endpoint, params)


def add_channel(channel_id, name=""):
    """Register a channel to monitor."""
    _channels[channel_id] = {
        "name": name or channel_id,
        "last_ts": f"{time.time():.6f}",
    }


def remove_channel(channel_id):
    """Stop monitoring a channel."""
    _channels.pop(channel_id, None)


def list_channels():
    """Return list of monitored channels."""
    return [{"id": cid, "name": ch["name"]} for cid, ch in _channels.items()]


def _poll_channels():
    """Poll all registered channels for new messages."""
    for channel_id, state in list(_channels.items()):
        try:
            res = _slack_api("conversations.history", {
                "channel": channel_id,
                "oldest": state["last_ts"],
                "limit": 20,
            })
            # Slack returns messages newest-first; track the newest ts we
            # emit so the next poll starts strictly after that point.
            # Previously `state["last_ts"] = msg["ts"]` inside the loop
            # overwrote with each iteration, ending at the OLDEST ts in the
            # batch -- which caused every subsequent poll to re-emit the
            # whole batch.
            max_ts = state["last_ts"]
            for msg in res.get("messages", []):
                if msg.get("subtype") in ("channel_join", "channel_leave", "bot_message"):
                    continue
                ts = msg.get("ts", "")
                # Slack ts is a numeric string "1700099999.000001" -- string
                # comparison works because they're left-padded identically.
                if ts > max_ts:
                    max_ts = ts

                text = msg.get("text", "")
                files = msg.get("files", [])

                workspace = _settings.get_value(
                    "service.slack.workspace_url", default="",
                )
                url = (
                    f"{workspace.rstrip('/')}/archives/{channel_id}/"
                    f"p{ts.replace('.', '')}"
                ) if workspace else ""
                app_state.emit_event("slack.message", {
                    "title": f"#{state['name']}: {text[:60]}" if text else f"#{state['name']}: [image]",
                    "message": text[:200] if text else f"{len(files)} file(s)",
                    "severity": "info",
                    "source_id": f"slack-{channel_id}-{ts}",
                    "url": url,
                    "session": channel_id,
                })
            state["last_ts"] = max_ts

        except Exception as e:
            print(f"[slack-monitor] {channel_id}: {e}", flush=True)


def poll_slack_once() -> None:
    """One scheduled tick. No-op when the slack channel is disabled,
    when creds aren't loaded, or when no channels are registered --
    all three states are fine at server startup."""
    if not _settings.is_plugin_enabled("slack_monitor"):
        return
    if not _creds_loaded or not _channels:
        return
    try:
        _poll_channels()
    except Exception as e:
        print(f"[slack-monitor] tick error: {e}", flush=True)


def start() -> bool:
    """Resume the scheduled Slack monitor job.

    Loads credentials, verifies auth, then unpauses the scheduler job.
    Returns True when the job is armed and will tick on its interval;
    False when credentials are missing or auth failed (caller should
    surface the error to the user)."""
    global _creds_loaded
    if not _load_credentials():
        print("[slack-monitor] No credentials found at ~/.eva/slack-credentials.json", flush=True)
        _creds_loaded = False
        return False
    try:
        _slack_api("auth.test", {})
    except Exception as e:
        print(f"[slack-monitor] Auth failed: {e}", flush=True)
        _creds_loaded = False
        return False
    _creds_loaded = True
    from services.scheduler import get_scheduler
    job = get_scheduler().get_job(SLACK_JOB_ID)
    if job is not None:
        get_scheduler().resume_job(SLACK_JOB_ID)
    print("[slack-monitor] Started", flush=True)
    return True


def stop() -> None:
    """Pause the scheduled job so no further polls fire until `start()`."""
    from services.scheduler import get_scheduler
    try:
        get_scheduler().pause_job(SLACK_JOB_ID)
    except Exception:
        # Job not registered yet -- nothing to pause.
        pass


def is_running() -> bool:
    """True iff the scheduled job exists and isn't paused."""
    from services.scheduler import get_scheduler
    try:
        job = get_scheduler().get_job(SLACK_JOB_ID)
    except Exception:
        return False
    return bool(job and job.next_run_time is not None)


# ---------------------------------------------------------------------------
# Channel registration
# ---------------------------------------------------------------------------

class SlackChannel:
    """Adapter from this module's procedural API onto the Channel
    protocol expected by `channels`. Methods delegate to the
    module-level functions so existing callers (tests, routes) keep
    working through both the class and the function APIs."""

    id = CHANNEL_ID
    label = CHANNEL_LABEL

    def start_jobs(self, scheduler) -> None:
        """Register the poll job. Job starts PAUSED; `start()` unpauses
        it once credentials verify, which avoids spamming Slack with
        unauthenticated requests every tick on an unconfigured install."""
        try:
            config = app_state.load_config()
            channels = (
                (config.get("slack") or {}).get("channels")
                or (config.get("slack_monitor") or {}).get("channels")
                or []
            )
        except Exception:
            channels = []
        # Also pull channels seeded via plugin.slack_monitor.channels
        # (set by common.settings.seed_from_yaml in the legacy path).
        if not channels:
            seeded = _settings.get_value("plugin.slack_monitor.channels")
            if isinstance(seeded, list):
                channels = seeded
        for ch in channels:
            if isinstance(ch, dict) and ch.get("id"):
                add_channel(ch["id"], ch.get("name", ""))
        scheduler.add_job(
            poll_slack_once,
            "interval",
            seconds=get_interval_seconds(),
            id=SLACK_JOB_ID,
            replace_existing=True,
        )
        if _channels:
            start()
        else:
            try:
                scheduler.pause_job(SLACK_JOB_ID)
            except Exception:
                pass

    def is_ready(self) -> tuple[bool, str]:
        creds = _slack.load_creds()
        if creds is None:
            return False, f"no credentials at {_CREDS_PATH}"
        if not _channels:
            return False, "no slack channels configured"
        return True, f"{len(_channels)} channel(s) monitored"

    def get_status(self) -> dict:
        ok, detail = self.is_ready()
        return {
            "id": self.id,
            "label": self.label,
            "ready": ok,
            "detail": detail,
            "running": is_running(),
            "channels": list_channels(),
        }


from common import channels as _channels_registry  # noqa: E402

_channels_registry.register(SlackChannel())
