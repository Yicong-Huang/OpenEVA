"""Slack adapter: the single place Eva talks to the Slack API.

Previously `services/slack_monitor.py` and `plugins/boba.py` each kept
their own copy of `_load_credentials` + `_slack_api`, differing mainly
in whether creds were module globals or function arguments. This adapter
consolidates the two into one module-level API that is explicit about
credentials and returns parsed JSON.

Callers that need a token/cookie pair call `load_creds()` once, then
pass the returned `SlackCreds` to every API function.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


# Slack creds file path.
_CREDS_PATH = Path.home() / ".eva" / "slack-credentials.json"


@dataclass(frozen=True)
class SlackCreds:
    """xoxc token + session cookie pair.

    `SlackCreds` is a frozen dataclass so callers can freely pass it
    around without accidental mutation; equality is structural so tests
    can build ad-hoc instances without having to mock the loader."""
    token: str
    cookie: str

    @property
    def valid(self) -> bool:
        """True when both halves are non-empty (callers treat invalid
        creds as 'Slack not configured', not an error)."""
        return bool(self.token and self.cookie)


def load_creds(path: Path | None = None) -> SlackCreds | None:
    """Load `{"token": "xoxc-...", "cookie": "..."}` from disk.

    Reads `~/.eva/slack-credentials.json` by default. Returns None
    when the file is missing or the content is malformed. The
    optional `path` argument lets tests point at a fixture without
    monkeypatching module state."""
    p = path if path is not None else _CREDS_PATH
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    creds = SlackCreds(
        token=data.get("token", ""),
        cookie=data.get("cookie", ""),
    )
    return creds if creds.valid else None


def call(creds: SlackCreds, endpoint: str, params: dict | None = None,
         timeout: float = 10) -> dict:
    """Make a POST to `https://slack.com/api/<endpoint>` with the given
    params and parse the JSON response.

    Slack returns 200 with `{"ok": false, "error": "..."}` on logical
    failures; we raise so callers don't have to check every response.
    Network errors propagate as `urllib` exceptions."""
    params = dict(params or {})
    params["token"] = creds.token
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"https://slack.com/api/{endpoint}",
        data=body,
        headers={"Cookie": creds.cookie},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
    if not result.get("ok"):
        raise Exception(f"Slack API error: {result.get('error', 'unknown')}")
    return result


# ---------------------------------------------------------------------------
# Convenience wrappers over the two endpoints Eva actually hits
# ---------------------------------------------------------------------------

def auth_test(creds: SlackCreds) -> dict:
    """Verify the creds are accepted by Slack. Raises on failure."""
    return call(creds, "auth.test")


def conversations_history(creds: SlackCreds, channel_id: str,
                          oldest: str, limit: int = 20) -> dict:
    """Pull messages from a channel newer than `oldest` (an ISO ts
    string). Max `limit` messages, newest-first as Slack emits them."""
    return call(creds, "conversations.history", {
        "channel": channel_id,
        "oldest": oldest,
        "limit": limit,
    })


def users_info(creds: SlackCreds, user_id: str) -> dict:
    """Resolve a user_id to its profile (used for display name lookup)."""
    return call(creds, "users.info", {"user": user_id})
