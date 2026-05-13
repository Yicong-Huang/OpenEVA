"""Unit tests for `adapters/slack.py`.

The adapter is the only module that talks to the Slack HTTP API; these
tests pin down its contract so the two callers (`services.slack_monitor`
and other plugin code) can depend on it without having to each mock
urllib.request.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

import adapters.slack as slack


# ---------------------------------------------------------------------------
# SlackCreds
# ---------------------------------------------------------------------------

class TestSlackCreds:
    def test_valid_when_both_set(self):
        assert slack.SlackCreds("xoxc-t", "d=c").valid is True

    def test_invalid_when_token_missing(self):
        assert slack.SlackCreds("", "d=c").valid is False

    def test_invalid_when_cookie_missing(self):
        assert slack.SlackCreds("xoxc-t", "").valid is False

    def test_invalid_when_both_empty(self):
        assert slack.SlackCreds("", "").valid is False

    def test_frozen_dataclass(self):
        """`SlackCreds` must be immutable so callers can pass it without
        worrying about accidental mutation downstream."""
        creds = slack.SlackCreds("t", "c")
        with pytest.raises(Exception):
            creds.token = "other"  # type: ignore[misc]

    def test_equality_is_structural(self):
        assert slack.SlackCreds("t", "c") == slack.SlackCreds("t", "c")
        assert slack.SlackCreds("t", "c") != slack.SlackCreds("t", "d")


# ---------------------------------------------------------------------------
# load_creds
# ---------------------------------------------------------------------------

class TestLoadCreds:
    def test_missing_file_returns_none(self, tmp_path):
        assert slack.load_creds(path=tmp_path / "does-not-exist.json") is None

    def test_valid_creds_returned(self, tmp_path):
        p = tmp_path / "credentials.json"
        p.write_text(json.dumps({"token": "xoxc-t", "cookie": "d=c"}))
        creds = slack.load_creds(path=p)
        assert creds is not None
        assert creds.token == "xoxc-t"
        assert creds.cookie == "d=c"

    def test_missing_token_treated_as_invalid(self, tmp_path):
        """Half-filled creds -> None. Callers interpret this as 'Slack
        not configured' instead of 'configured with broken creds'."""
        p = tmp_path / "credentials.json"
        p.write_text(json.dumps({"cookie": "d=c"}))
        assert slack.load_creds(path=p) is None

    def test_missing_cookie_treated_as_invalid(self, tmp_path):
        p = tmp_path / "credentials.json"
        p.write_text(json.dumps({"token": "xoxc-t"}))
        assert slack.load_creds(path=p) is None

    def test_malformed_json_returns_none(self, tmp_path):
        """Bad JSON in the creds file shouldn't crash callers."""
        p = tmp_path / "credentials.json"
        p.write_text("not-json-at-all{")
        assert slack.load_creds(path=p) is None

    def test_default_path_used_when_none_passed(self, tmp_path, monkeypatch):
        """When caller omits path kwarg, the module-level default is used."""
        p = tmp_path / "credentials.json"
        p.write_text(json.dumps({"token": "x", "cookie": "c"}))
        monkeypatch.setattr(slack, "_CREDS_PATH", p)
        creds = slack.load_creds()
        assert creds is not None
        assert creds.token == "x"


# ---------------------------------------------------------------------------
# call()
# ---------------------------------------------------------------------------

class TestCall:
    @patch("adapters.slack.urllib.request.urlopen")
    def test_success_returns_parsed_json(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "messages": []}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = slack.call(slack.SlackCreds("t", "c"), "conversations.history",
                            {"channel": "C123"})
        assert result == {"ok": True, "messages": []}

    @patch("adapters.slack.urllib.request.urlopen")
    def test_ok_false_raises(self, mock_urlopen):
        """Slack returns HTTP 200 with ok=false on logical failures; the
        adapter converts that to an exception so each caller doesn't
        have to check every single response."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": False, "error": "channel_not_found"}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with pytest.raises(Exception, match="channel_not_found"):
            slack.call(slack.SlackCreds("t", "c"), "conversations.history", {"channel": "C9"})

    @patch("adapters.slack.urllib.request.urlopen")
    def test_cookie_header_set(self, mock_urlopen):
        """The request header must carry the session cookie Slack expects."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        slack.call(slack.SlackCreds("t", "d=MY-COOKIE"), "auth.test")

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Cookie") == "d=MY-COOKIE"

    @patch("adapters.slack.urllib.request.urlopen")
    def test_token_injected_into_params(self, mock_urlopen):
        """The `token` form parameter must be set to creds.token even if
        the caller didn't include it."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        slack.call(slack.SlackCreds("xoxc-T", "d=c"), "conversations.history",
                   {"channel": "C1", "limit": 20})

        req = mock_urlopen.call_args[0][0]
        body = req.data.decode()
        assert "token=xoxc-T" in body
        assert "channel=C1" in body

    @patch("adapters.slack.urllib.request.urlopen")
    def test_params_not_mutated_by_caller(self, mock_urlopen):
        """The adapter must not mutate the caller's params dict (the old
        `_slack_api` did and tests occasionally broke from residual
        `token` keys)."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        params = {"channel": "C1"}
        slack.call(slack.SlackCreds("xoxc-T", "d=c"), "conversations.history", params)
        assert params == {"channel": "C1"}  # no 'token' key added

    @patch("adapters.slack.urllib.request.urlopen")
    def test_none_params_supported(self, mock_urlopen):
        """Endpoints with no params (auth.test) should not require the
        caller to pass an empty dict."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "url": "..."}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = slack.call(slack.SlackCreds("t", "c"), "auth.test")
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

class TestConvenienceWrappers:
    def test_auth_test_calls_correct_endpoint(self, monkeypatch):
        called = {}

        def fake_call(creds, endpoint, params=None, timeout=10):
            called["endpoint"] = endpoint
            called["params"] = params
            return {"ok": True}

        monkeypatch.setattr(slack, "call", fake_call)
        slack.auth_test(slack.SlackCreds("t", "c"))
        assert called["endpoint"] == "auth.test"

    def test_conversations_history_passes_through(self, monkeypatch):
        called = {}

        def fake_call(creds, endpoint, params=None, timeout=10):
            called["endpoint"] = endpoint
            called["params"] = params
            return {"ok": True, "messages": []}

        monkeypatch.setattr(slack, "call", fake_call)
        slack.conversations_history(slack.SlackCreds("t", "c"),
                                    "C123", "1700000000.0", limit=5)

        assert called["endpoint"] == "conversations.history"
        assert called["params"] == {
            "channel": "C123", "oldest": "1700000000.0", "limit": 5,
        }

    def test_users_info_passes_through(self, monkeypatch):
        called = {}

        def fake_call(creds, endpoint, params=None, timeout=10):
            called["endpoint"] = endpoint
            called["params"] = params
            return {"ok": True, "user": {}}

        monkeypatch.setattr(slack, "call", fake_call)
        slack.users_info(slack.SlackCreds("t", "c"), "U9")
        assert called["endpoint"] == "users.info"
        assert called["params"] == {"user": "U9"}
