"""Tests for services/slack_monitor.py.

Covers:
- add_channel / remove_channel / list_channels
- start() / stop() / is_running() now map to scheduler job pause/resume
- _load_credentials, _slack_api, _poll_channels
- poll_slack_once tick wrapper
- Event emission on new messages
- Error handling
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestSlackMonitorState:
    """Basic state management: add/remove/list channels."""

    def setup_method(self):
        from services import slack_monitor
        self._orig_channels = dict(slack_monitor._channels)
        slack_monitor._channels.clear()

    def teardown_method(self):
        from services import slack_monitor
        slack_monitor._channels.clear()
        slack_monitor._channels.update(self._orig_channels)

    def test_add_channel(self):
        from services import slack_monitor
        slack_monitor.add_channel("C001", "general")
        assert "C001" in slack_monitor._channels
        assert slack_monitor._channels["C001"]["name"] == "general"
        assert slack_monitor._channels["C001"]["last_ts"]

    def test_add_channel_default_name(self):
        from services import slack_monitor
        slack_monitor.add_channel("C002")
        assert slack_monitor._channels["C002"]["name"] == "C002"

    def test_remove_channel(self):
        from services import slack_monitor
        slack_monitor.add_channel("C003", "to-remove")
        slack_monitor.remove_channel("C003")
        assert "C003" not in slack_monitor._channels

    def test_remove_nonexistent_channel(self):
        from services import slack_monitor
        slack_monitor.remove_channel("CXYZ")  # should not raise

    def test_list_channels_empty(self):
        from services import slack_monitor
        result = slack_monitor.list_channels()
        assert result == []

    def test_list_channels(self):
        from services import slack_monitor
        slack_monitor.add_channel("C010", "dev")
        slack_monitor.add_channel("C020", "ops")
        result = slack_monitor.list_channels()
        assert len(result) == 2
        ids = {ch["id"] for ch in result}
        assert ids == {"C010", "C020"}


class TestIsRunning:
    """`is_running()` now checks scheduler job state, not a thread handle."""

    def test_false_when_no_job(self, monkeypatch):
        from services import slack_monitor, scheduler
        sched = MagicMock()
        sched.get_job.return_value = None
        monkeypatch.setattr(scheduler, "get_scheduler", lambda: sched)
        assert slack_monitor.is_running() is False

    def test_false_when_job_paused(self, monkeypatch):
        from services import slack_monitor, scheduler
        job = MagicMock()
        job.next_run_time = None  # paused
        sched = MagicMock()
        sched.get_job.return_value = job
        monkeypatch.setattr(scheduler, "get_scheduler", lambda: sched)
        assert slack_monitor.is_running() is False

    def test_true_when_job_active(self, monkeypatch):
        from services import slack_monitor, scheduler
        from datetime import datetime, timezone
        job = MagicMock()
        job.next_run_time = datetime.now(timezone.utc)
        sched = MagicMock()
        sched.get_job.return_value = job
        monkeypatch.setattr(scheduler, "get_scheduler", lambda: sched)
        assert slack_monitor.is_running() is True


class TestSlackMonitorStop:
    """`stop()` pauses the scheduler job; no thread handle anymore."""

    def test_pauses_registered_job(self, monkeypatch):
        from services import slack_monitor, scheduler
        sched = MagicMock()
        monkeypatch.setattr(scheduler, "get_scheduler", lambda: sched)
        slack_monitor.stop()
        sched.pause_job.assert_called_once_with(slack_monitor.SLACK_JOB_ID)

    def test_swallows_error_when_job_missing(self, monkeypatch):
        from services import slack_monitor, scheduler
        sched = MagicMock()
        sched.pause_job.side_effect = Exception("no such job")
        monkeypatch.setattr(scheduler, "get_scheduler", lambda: sched)
        # Must not raise: stop() must be idempotent.
        slack_monitor.stop()


class TestSlackMonitorStart:
    """`start()`: verify credentials + auth, then resume the scheduler job."""

    def setup_method(self):
        from services import slack_monitor
        self._orig_token = slack_monitor._token
        self._orig_cookie = slack_monitor._cookie
        self._orig_loaded = slack_monitor._creds_loaded

    def teardown_method(self):
        from services import slack_monitor
        slack_monitor._token = self._orig_token
        slack_monitor._cookie = self._orig_cookie
        slack_monitor._creds_loaded = self._orig_loaded

    def test_start_no_credentials(self, monkeypatch):
        from services import slack_monitor
        monkeypatch.setattr(slack_monitor, "_load_credentials", lambda: False)
        assert slack_monitor.start() is False
        assert slack_monitor._creds_loaded is False

    def test_start_auth_test_fails(self, monkeypatch):
        from services import slack_monitor

        def load_creds():
            slack_monitor._token = "xoxc-test"
            slack_monitor._cookie = "cookie-test"
            return True

        monkeypatch.setattr(slack_monitor, "_load_credentials", load_creds)

        def api_raises(ep, params):
            raise Exception("auth bad")

        monkeypatch.setattr(slack_monitor, "_slack_api", api_raises)
        assert slack_monitor.start() is False
        assert slack_monitor._creds_loaded is False

    def test_start_success_resumes_job(self, monkeypatch):
        from services import slack_monitor, scheduler

        def load_creds():
            slack_monitor._token = "xoxc-test"
            slack_monitor._cookie = "cookie-test"
            return True

        monkeypatch.setattr(slack_monitor, "_load_credentials", load_creds)
        monkeypatch.setattr(slack_monitor, "_slack_api", lambda ep, params: {"ok": True})

        sched = MagicMock()
        # Job exists -> resume should be called.
        sched.get_job.return_value = MagicMock()
        monkeypatch.setattr(scheduler, "get_scheduler", lambda: sched)

        result = slack_monitor.start()
        assert result is True
        assert slack_monitor._creds_loaded is True
        sched.resume_job.assert_called_once_with(slack_monitor.SLACK_JOB_ID)

    def test_start_success_skips_resume_when_job_missing(self, monkeypatch):
        """If the scheduler hasn't registered the job yet (e.g. unit test
        without lifespan), start() should still succeed without errorring."""
        from services import slack_monitor, scheduler

        def load_creds():
            slack_monitor._token = "xoxc-test"
            slack_monitor._cookie = "cookie-test"
            return True

        monkeypatch.setattr(slack_monitor, "_load_credentials", load_creds)
        monkeypatch.setattr(slack_monitor, "_slack_api", lambda ep, params: {"ok": True})

        sched = MagicMock()
        sched.get_job.return_value = None
        monkeypatch.setattr(scheduler, "get_scheduler", lambda: sched)

        assert slack_monitor.start() is True
        sched.resume_job.assert_not_called()


class TestPollSlackOnce:
    """`poll_slack_once` is what the scheduler calls each tick."""

    def setup_method(self):
        from services import slack_monitor
        self._orig_loaded = slack_monitor._creds_loaded
        self._orig_channels = dict(slack_monitor._channels)

    def teardown_method(self):
        from services import slack_monitor
        slack_monitor._creds_loaded = self._orig_loaded
        slack_monitor._channels.clear()
        slack_monitor._channels.update(self._orig_channels)

    def test_noop_when_creds_not_loaded(self, monkeypatch):
        from services import slack_monitor
        slack_monitor._creds_loaded = False
        called = []
        monkeypatch.setattr(slack_monitor, "_poll_channels", lambda: called.append(1))
        slack_monitor.poll_slack_once()
        assert called == []

    def test_noop_when_no_channels(self, monkeypatch):
        from services import slack_monitor
        slack_monitor._creds_loaded = True
        slack_monitor._channels.clear()
        called = []
        monkeypatch.setattr(slack_monitor, "_poll_channels", lambda: called.append(1))
        slack_monitor.poll_slack_once()
        assert called == []

    def test_delegates_to_poll_channels(self, monkeypatch):
        from services import slack_monitor
        slack_monitor._creds_loaded = True
        slack_monitor.add_channel("C9", "any")
        called = []
        monkeypatch.setattr(slack_monitor, "_poll_channels", lambda: called.append(1))
        slack_monitor.poll_slack_once()
        assert called == [1]

    def test_swallows_poll_errors(self, monkeypatch, capsys):
        """One tick raising mustn't propagate into the scheduler."""
        from services import slack_monitor
        slack_monitor._creds_loaded = True
        slack_monitor.add_channel("C9", "any")

        def explode():
            raise RuntimeError("net down")

        monkeypatch.setattr(slack_monitor, "_poll_channels", explode)
        slack_monitor.poll_slack_once()  # must not raise
        assert "tick error" in capsys.readouterr().out


class TestSlackMonitorLoadCredentials:
    """`_load_credentials` delegates to `adapters.slack.load_creds`;
    the creds file lives in `adapters.slack._CREDS_PATH` -- that's
    what these tests point at."""

    def test_load_credentials_no_file(self, monkeypatch, tmp_path):
        from services import slack_monitor
        import adapters.slack as slack_adapter
        monkeypatch.setattr(slack_adapter, "_CREDS_PATH",
                            tmp_path / "creds.json")

        result = slack_monitor._load_credentials()
        assert result is False

    def test_load_credentials_success(self, tmp_path, monkeypatch):
        from services import slack_monitor
        import adapters.slack as slack_adapter
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text(json.dumps({"token": "xoxc-abc", "cookie": "d=xyz"}))
        monkeypatch.setattr(slack_adapter, "_CREDS_PATH", creds_file)

        orig_token = slack_monitor._token
        orig_cookie = slack_monitor._cookie

        result = slack_monitor._load_credentials()
        assert result is True
        assert slack_monitor._token == "xoxc-abc"
        assert slack_monitor._cookie == "d=xyz"

        slack_monitor._token = orig_token
        slack_monitor._cookie = orig_cookie

    def test_load_credentials_missing_token(self, tmp_path, monkeypatch):
        from services import slack_monitor
        import adapters.slack as slack_adapter
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text(json.dumps({"cookie": "d=xyz"}))
        monkeypatch.setattr(slack_adapter, "_CREDS_PATH", creds_file)

        result = slack_monitor._load_credentials()
        assert result is False


class TestSlackMonitorPollChannels:
    """_poll_channels with mocked Slack API."""

    def setup_method(self):
        from services import slack_monitor
        self._orig_channels = dict(slack_monitor._channels)
        self._orig_token = slack_monitor._token
        self._orig_cookie = slack_monitor._cookie
        slack_monitor._channels.clear()
        slack_monitor._token = "xoxc-test"
        slack_monitor._cookie = "cookie-test"

    def teardown_method(self):
        from services import slack_monitor
        slack_monitor._channels.clear()
        slack_monitor._channels.update(self._orig_channels)
        slack_monitor._token = self._orig_token
        slack_monitor._cookie = self._orig_cookie

    def test_poll_no_channels(self, monkeypatch):
        """_poll_channels does nothing when no channels registered."""
        from services import slack_monitor
        api_called = []
        monkeypatch.setattr(slack_monitor, "_slack_api", lambda ep, params: api_called.append(1))
        slack_monitor._poll_channels()
        assert len(api_called) == 0

    def test_poll_with_messages(self, monkeypatch):
        """_poll_channels emits events for new user messages."""
        from services import slack_monitor
        import app_state

        slack_monitor.add_channel("C100", "dev-channel")

        monkeypatch.setattr(slack_monitor, "_slack_api", lambda ep, params: {
            "ok": True,
            "messages": [
                {"ts": "1700000001.000", "user": "U123", "text": "Hello world"},
            ],
        })

        emitted = []
        orig_emit = app_state.emit_event
        monkeypatch.setattr(app_state, "emit_event", lambda evt_type, data: emitted.append((evt_type, data)))

        slack_monitor._poll_channels()

        assert len(emitted) == 1
        assert emitted[0][0] == "slack.message"
        assert "#dev-channel" in emitted[0][1]["title"]
        assert "Hello world" in emitted[0][1]["title"]

    def test_poll_skips_bot_messages(self, monkeypatch):
        """_poll_channels skips messages with subtype bot_message, channel_join, channel_leave."""
        from services import slack_monitor
        import app_state

        slack_monitor.add_channel("C200", "ops")

        monkeypatch.setattr(slack_monitor, "_slack_api", lambda ep, params: {
            "ok": True,
            "messages": [
                {"ts": "1700000002.000", "subtype": "bot_message", "text": "Bot says hi"},
                {"ts": "1700000003.000", "subtype": "channel_join", "text": "joined"},
                {"ts": "1700000004.000", "subtype": "channel_leave", "text": "left"},
            ],
        })

        emitted = []
        monkeypatch.setattr(app_state, "emit_event", lambda evt_type, data: emitted.append(1))

        slack_monitor._poll_channels()
        assert len(emitted) == 0

    def test_poll_message_with_image(self, monkeypatch):
        """_poll_channels handles messages with image files."""
        from services import slack_monitor
        import app_state

        slack_monitor.add_channel("C300", "photos")

        monkeypatch.setattr(slack_monitor, "_slack_api", lambda ep, params: {
            "ok": True,
            "messages": [
                {
                    "ts": "1700000005.000",
                    "user": "U456",
                    "text": "",
                    "files": [{"mimetype": "image/png", "url_private": "https://files.slack.com/img.png"}],
                },
            ],
        })

        emitted = []
        monkeypatch.setattr(app_state, "emit_event", lambda evt_type, data: emitted.append((evt_type, data)))

        slack_monitor._poll_channels()

        assert len(emitted) == 1
        assert "[image]" in emitted[0][1]["title"]

    def test_poll_updates_last_ts(self, monkeypatch):
        """_poll_channels updates last_ts for the channel to the NEWEST ts."""
        from services import slack_monitor

        slack_monitor.add_channel("C400", "updates")
        # Force last_ts to an older value so the incoming msg is clearly newer.
        slack_monitor._channels["C400"]["last_ts"] = "1600000000.000000"

        monkeypatch.setattr(slack_monitor, "_slack_api", lambda ep, params: {
            "ok": True,
            "messages": [
                {"ts": "1700099999.000", "user": "U789", "text": "latest"},
            ],
        })
        monkeypatch.setattr("app_state.emit_event", lambda *a: None)

        slack_monitor._poll_channels()
        assert slack_monitor._channels["C400"]["last_ts"] == "1700099999.000"

    def test_poll_last_ts_uses_newest_when_multiple_messages(self, monkeypatch):
        """Regression: Slack returns messages newest-first. last_ts used to be
        overwritten each loop iteration, ending at the OLDEST ts in the batch
        and causing the next poll to re-emit every message it just fired."""
        from services import slack_monitor

        slack_monitor.add_channel("C401", "updates-regression")
        slack_monitor._channels["C401"]["last_ts"] = "1600000000.000000"

        monkeypatch.setattr(slack_monitor, "_slack_api", lambda ep, params: {
            "ok": True,
            "messages": [
                {"ts": "1700099999.000", "user": "U", "text": "newest"},
                {"ts": "1700099998.000", "user": "U", "text": "middle"},
                {"ts": "1700099997.000", "user": "U", "text": "oldest"},
            ],
        })
        monkeypatch.setattr("app_state.emit_event", lambda *a: None)

        slack_monitor._poll_channels()
        # Must advance to the NEWEST ts, not the oldest.
        assert slack_monitor._channels["C401"]["last_ts"] == "1700099999.000"

    def test_poll_last_ts_not_moved_backward(self, monkeypatch):
        """If the batch contains only msgs older than the current last_ts
        (shouldn't happen in practice but defensively), last_ts must not
        regress -- that would cause duplicate emits on the next poll."""
        from services import slack_monitor

        slack_monitor.add_channel("C402", "updates-no-regress")
        slack_monitor._channels["C402"]["last_ts"] = "9900000000.000000"

        monkeypatch.setattr(slack_monitor, "_slack_api", lambda ep, params: {
            "ok": True,
            "messages": [{"ts": "1700099999.000", "user": "U", "text": "old"}],
        })
        monkeypatch.setattr("app_state.emit_event", lambda *a: None)

        slack_monitor._poll_channels()
        assert slack_monitor._channels["C402"]["last_ts"] == "9900000000.000000"

    def test_poll_api_error_handled(self, monkeypatch):
        """_poll_channels catches exceptions per channel and continues."""
        from services import slack_monitor
        import app_state

        slack_monitor.add_channel("C500", "err-channel")
        slack_monitor.add_channel("C600", "ok-channel")

        call_count = []

        def fake_api(ep, params):
            call_count.append(params["channel"])
            if params["channel"] == "C500":
                raise Exception("network error")
            return {"ok": True, "messages": [{"ts": "1700000010.000", "user": "U1", "text": "ok"}]}

        monkeypatch.setattr(slack_monitor, "_slack_api", fake_api)
        monkeypatch.setattr(app_state, "emit_event", lambda *a: None)

        # Should not raise even though C500 fails
        slack_monitor._poll_channels()
        assert "C500" in call_count
        assert "C600" in call_count

    def test_poll_message_url_format(self, monkeypatch, patched_server):
        """The deep-link URL is built from the configured
        `service.slack.workspace_url` plus the channel id + ts. Out
        of the box (no workspace_url set) the URL is empty -- OSS
        installs don't bake any vendor workspace into the binary."""
        from services import slack_monitor
        import app_state

        patched_server._db.set_setting(
            "service.slack.workspace_url",
            "https://example.slack.com",
        )
        slack_monitor.add_channel("C700", "url-test")

        monkeypatch.setattr(slack_monitor, "_slack_api", lambda ep, params: {
            "ok": True,
            "messages": [
                {"ts": "1700000020.123456", "user": "U1", "text": "check url"},
            ],
        })

        emitted = []
        monkeypatch.setattr(
            app_state, "emit_event",
            lambda evt_type, data: emitted.append(data),
        )

        slack_monitor._poll_channels()

        url = emitted[0]["url"]
        assert "archives/C700/p" in url
        # ts dots should be removed in URL
        assert "1700000020123456" in url


class TestSlackApiCall:
    """_slack_api constructs correct request."""

    def test_slack_api_success(self, monkeypatch):
        from services import slack_monitor
        import urllib.request

        slack_monitor._token = "xoxc-test-token"
        slack_monitor._cookie = "d=test-cookie"

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "result": "success"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, **kw: mock_resp)

        result = slack_monitor._slack_api("auth.test", {})
        assert result["ok"] is True

    def test_slack_api_error(self, monkeypatch):
        from services import slack_monitor
        import urllib.request

        slack_monitor._token = "xoxc-test-token"
        slack_monitor._cookie = "d=test-cookie"

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": False, "error": "invalid_auth"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, **kw: mock_resp)

        with pytest.raises(Exception, match="Slack API error.*invalid_auth"):
            slack_monitor._slack_api("auth.test", {})
