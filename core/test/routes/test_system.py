"""Tests for routes/common.system.py -- HTTP-level tests for all system endpoints.

Covers:
- GET /api/certs -- mock cert providers, test response format
- POST /api/certs/renew/{cert_id} -- success, not found
- GET /api/usage -- mock usage data
- GET /api/usage/history -- with days param
- GET /api/live-stats -- mock gh_run, test caching
- GET /api/workstats -- mock gh_run
- GET /api/me -- verify logins and repo mapping
- GET /api/slack-monitor -- status
- POST /api/slack-monitor/start -- start monitoring
- POST /api/slack-monitor/stop -- stop monitoring
"""

import json
import subprocess
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import common
import common.cert
import common.system


# ====================================================================
# GET /api/certs
# ====================================================================


class TestGetCerts:
    """GET /api/certs -- mock cert providers, verify response format."""

    def test_returns_all_cert_providers(self, client, monkeypatch):
        """Response should include keys for each registered cert provider."""
        from common import system as system_mod
        from common import cert as cert

        class FakeCert(system_mod.CertProvider):
            key = "fake_cert"
            name = "Fake Cert"
            def check(self):
                return 99999
            def renew(self):
                return True

        monkeypatch.setattr("common.cert._registered", [FakeCert()])
        # Clear last status to avoid stale comparison
        common.cert._last_cert_status.clear()

        resp = client.get("/api/certs")
        assert resp.status_code == 200
        data = resp.json()
        assert "fake_cert" in data
        assert data["fake_cert"]["name"] == "Fake Cert"
        assert data["fake_cert"]["status"] == "ok"
        assert data["fake_cert"]["remaining_seconds"] == 99999

    def test_cert_check_exception_returns_error_status(self, client, monkeypatch):
        """When provider.check() raises, remaining_seconds should be -1 and status 'error'."""
        from common import system as system_mod
        from common import cert as cert

        class BrokenCert(system_mod.CertProvider):
            key = "broken"
            name = "Broken"
            def check(self):
                raise RuntimeError("oops")

        monkeypatch.setattr("common.cert._registered", [BrokenCert()])
        common.cert._last_cert_status.clear()

        resp = client.get("/api/certs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["broken"]["remaining_seconds"] == -1
        assert data["broken"]["status"] == "error"

    def test_expired_cert_status(self, client, monkeypatch):
        """When remaining_seconds == 0, status should be 'expired'."""
        from common import system as system_mod
        from common import cert as cert

        class ExpiredCert(system_mod.CertProvider):
            key = "expired"
            name = "Expired"
            def check(self):
                return 0

        monkeypatch.setattr("common.cert._registered", [ExpiredCert()])
        common.cert._last_cert_status.clear()

        resp = client.get("/api/certs")
        assert resp.status_code == 200
        assert resp.json()["expired"]["status"] == "expired"

    def test_warning_cert_with_auto_renew_success(self, client, monkeypatch):
        """When cert is below warning threshold and renew() succeeds, note='auto-renewed'."""
        from common import system as system_mod
        from common import cert as cert

        class LowCert(system_mod.CertProvider):
            key = "low"
            name = "Low"
            warning_secs = 57600
            def check(self):
                return 1000  # < 57600
            def renew(self):
                return True

        monkeypatch.setattr("common.cert._registered", [LowCert()])
        common.cert._last_cert_status.clear()

        resp = client.get("/api/certs")
        data = resp.json()
        assert data["low"]["note"] == "auto-renewed"
        assert data["low"]["status"] == "ok"


# ====================================================================
# POST /api/certs/renew/{cert_id}
# ====================================================================


class TestRenewCertRoute:
    """POST /api/certs/renew/{cert_id} -- success & not found."""

    def test_renew_success(self, client, monkeypatch):
        from common import system as system_mod
        from common import cert as cert

        class RenewableCert(system_mod.CertProvider):
            key = "test_cert"
            name = "Test"
            def check(self):
                return 100
            def renew(self):
                return True

        monkeypatch.setattr("common.cert._registered", [RenewableCert()])

        resp = client.post("/api/certs/renew/test_cert")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "renewed" in data["output"]

    def test_renew_failure(self, client, monkeypatch):
        from common import system as system_mod
        from common import cert as cert

        class FailCert(system_mod.CertProvider):
            key = "fail_cert"
            name = "Fail"
            def check(self):
                return 100
            def renew(self):
                return False

        monkeypatch.setattr("common.cert._registered", [FailCert()])

        resp = client.post("/api/certs/renew/fail_cert")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

    def test_renew_not_found(self, client):
        resp = client.post("/api/certs/renew/nonexistent_cert_xyz")
        assert resp.status_code == 404

    def test_renew_provider_raises(self, client, monkeypatch):
        from common import system as system_mod
        from common import cert as cert

        class ExplodingCert(system_mod.CertProvider):
            key = "boom"
            name = "Boom"
            def check(self):
                return 100
            def renew(self):
                raise OSError("disk on fire")

        monkeypatch.setattr("common.cert._registered", [ExplodingCert()])

        resp = client.post("/api/certs/renew/boom")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "disk on fire" in data["output"]


# ====================================================================
# GET /api/usage
# ====================================================================


class TestGetUsage:
    """GET /api/usage -- mock usage data."""

    def setup_method(self):
        from common import system as core_sys
        self._original = dict(core_sys._usage_cache)

    def teardown_method(self):
        from common import system as core_sys
        core_sys._usage_cache["data"] = self._original["data"]
        core_sys._usage_cache["ts"] = self._original["ts"]

    def test_usage_returns_data(self, client, monkeypatch):
        from common import system as core_sys

        core_sys._usage_cache["data"] = None
        core_sys._usage_cache["ts"] = 0

        fake = {"daily": "5.00", "weekly": "25.00", "monthly": "100.00", "tier": "Standard"}
        monkeypatch.setattr(core_sys, "_fetch_usage", lambda: fake)

        resp = client.get("/api/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["daily"] == "5.00"
        assert data["tier"] == "Standard"

    def test_usage_returns_cached(self, client, monkeypatch):
        from common import system as core_sys

        cached = {"daily": "cached", "weekly": "1", "monthly": "2", "tier": "Power User"}
        core_sys._usage_cache["data"] = cached
        core_sys._usage_cache["ts"] = time.time()

        fetch_called = []
        monkeypatch.setattr(core_sys, "_fetch_usage", lambda: fetch_called.append(1))

        resp = client.get("/api/usage")
        assert resp.status_code == 200
        assert resp.json()["daily"] == "cached"
        assert len(fetch_called) == 0

    def test_usage_fetch_failure_returns_defaults(self, client, monkeypatch):
        from common import system as core_sys

        core_sys._usage_cache["data"] = None
        core_sys._usage_cache["ts"] = 0
        monkeypatch.setattr(core_sys, "_fetch_usage", lambda: None)

        resp = client.get("/api/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["daily"] is None


# ====================================================================
# GET /api/usage/history
# ====================================================================


class TestGetUsageHistory:
    """GET /api/usage/history -- with days param."""

    def test_history_default_days(self, client, monkeypatch):
        import routes.system as route_mod

        monkeypatch.setattr(route_mod, "_get_usage_history", lambda days=7: {
            "history": [{"ts": "2026-04-10T12:00:00", "daily": 10.0, "weekly": 50.0, "monthly": 200.0}],
            "total_records": 1,
        })

        resp = client.get("/api/usage/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_records"] == 1
        assert len(data["history"]) == 1

    def test_history_custom_days(self, client, monkeypatch):
        import routes.system as route_mod

        captured_days = []
        def fake_history(days=7):
            captured_days.append(days)
            return {"history": [], "total_records": 0}

        monkeypatch.setattr(route_mod, "_get_usage_history", fake_history)

        resp = client.get("/api/usage/history?days=30")
        assert resp.status_code == 200
        assert captured_days == [30]


# ====================================================================
# GET /api/live-stats
# ====================================================================


class TestGetLiveStats:
    """GET /api/live-stats -- mock gh_run, test caching."""

    def setup_method(self):
        from common import system as core_sys
        self._original = dict(core_sys._live_stats_cache)

    def teardown_method(self):
        from common import system as core_sys
        core_sys._live_stats_cache["data"] = self._original["data"]
        core_sys._live_stats_cache["ts"] = self._original["ts"]

    def test_live_stats_returns_data(self, client, monkeypatch):
        from common import system as core_sys

        core_sys._live_stats_cache["data"] = None
        core_sys._live_stats_cache["ts"] = 0

        fake_stats = {
            "open_prs": {"repo": 3, "runtime": 2, "total": 5},
            "contributor_rank": 42,
            "contributor_contributions": 200,
            "contributor_total": 500,
        }
        monkeypatch.setattr(core_sys, "_fetch_live_stats", lambda: fake_stats)

        resp = client.get("/api/live-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["open_prs"]["total"] == 5
        assert data["contributor_rank"] == 42

    def test_live_stats_cached(self, client, monkeypatch):
        from common import system as core_sys

        cached = {"open_prs": {"total": 10}, "contributor_rank": 1,
                  "contributor_contributions": 999, "contributor_total": 1000}
        core_sys._live_stats_cache["data"] = cached
        core_sys._live_stats_cache["ts"] = time.time()

        fetch_called = []
        monkeypatch.setattr(core_sys, "_fetch_live_stats", lambda: fetch_called.append(1))

        resp = client.get("/api/live-stats")
        assert resp.status_code == 200
        assert resp.json()["open_prs"]["total"] == 10
        assert len(fetch_called) == 0

    def test_live_stats_refresh_param(self, client, monkeypatch):
        from common import system as core_sys

        core_sys._live_stats_cache["data"] = {"open_prs": {"total": 0},
                                               "contributor_rank": None,
                                               "contributor_contributions": None,
                                               "contributor_total": None}
        core_sys._live_stats_cache["ts"] = time.time()

        new_stats = {"open_prs": {"total": 99}, "contributor_rank": 5,
                     "contributor_contributions": 100, "contributor_total": 200}
        monkeypatch.setattr(core_sys, "_fetch_live_stats", lambda: new_stats)

        # refresh=1 forces background refresh (returns old data since cache exists)
        resp = client.get("/api/live-stats?refresh=1")
        assert resp.status_code == 200
        # With refresh=1 and existing cache, it starts a bg thread and returns old data
        data = resp.json()
        assert "open_prs" in data


# ====================================================================
# GET /api/workstats
# ====================================================================


class TestGetWorkstats:
    """GET /api/workstats -- mock _compute_workstats."""

    def setup_method(self):
        from common import system as core_sys
        self._original = dict(core_sys._workstats_cache)

    def teardown_method(self):
        from common import system as core_sys
        core_sys._workstats_cache["data"] = self._original["data"]
        core_sys._workstats_cache["ts"] = self._original["ts"]

    def test_workstats_returns_data(self, client, monkeypatch):
        from common import system as core_sys

        core_sys._workstats_cache["data"] = None
        core_sys._workstats_cache["ts"] = 0

        fake = {
            "quarters": [{"period": "Q1 FY26", "repo": 5, "runtime": 3, "universe": 2, "total": 10}],
            "all_time": {"repo": 5, "runtime": 3, "universe": 2, "total": 10},
            "weekly": [3, 4, 3],
        }
        monkeypatch.setattr(core_sys, "_compute_workstats", lambda: fake)

        resp = client.get("/api/workstats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["all_time"]["total"] == 10
        assert len(data["quarters"]) == 1

    def test_workstats_cached(self, client, monkeypatch):
        from common import system as core_sys

        cached = {"quarters": [], "all_time": {"total": 99}, "weekly": []}
        core_sys._workstats_cache["data"] = cached
        core_sys._workstats_cache["ts"] = time.time()

        monkeypatch.setattr(core_sys, "_compute_workstats", lambda: None)

        resp = client.get("/api/workstats")
        assert resp.status_code == 200
        assert resp.json()["all_time"]["total"] == 99

    def test_workstats_compute_returns_none(self, client, monkeypatch):
        """When _compute_workstats returns None and no cache, return empty defaults."""
        from common import system as core_sys

        core_sys._workstats_cache["data"] = None
        core_sys._workstats_cache["ts"] = 0
        monkeypatch.setattr(core_sys, "_compute_workstats", lambda: None)

        resp = client.get("/api/workstats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["quarters"] == []
        assert data["weekly"] == []


# ====================================================================
# GET /api/me
# ====================================================================


class TestGetMe:
    """GET /api/me -- verify logins and repo mapping."""

    def test_me_returns_logins_and_repo_account(self, client, monkeypatch):
        import app_state

        monkeypatch.setattr(app_state, "_gh_tokens", {"test-author": "tok1", "test-author_data": "tok2"})

        resp = client.get("/api/me")
        assert resp.status_code == 200
        data = resp.json()
        assert "logins" in data
        assert set(data["logins"]) == {"test-author", "test-author_data"}
        assert "repoAccount" in data
        # "default" key should exist
        assert "default" in data["repoAccount"]

    def test_me_empty_tokens(self, client, monkeypatch):
        import app_state

        monkeypatch.setattr(app_state, "_gh_tokens", {})

        resp = client.get("/api/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["logins"] == []


# ====================================================================
# GET /api/slack-monitor
# ====================================================================


class TestSlackMonitorStatus:
    """GET /api/slack-monitor -- status reflects scheduler job state."""

    def test_status_not_running(self, client, monkeypatch):
        """No registered job -> `running=False`. The test environment
        disables the scheduler so no jobs are registered by default."""
        from services import slack_monitor
        orig_channels = dict(slack_monitor._channels)
        slack_monitor._channels.clear()

        resp = client.get("/api/slack-monitor")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["channels"] == []

        slack_monitor._channels.update(orig_channels)

    def test_status_with_channels(self, client, monkeypatch):
        """Channels are tracked in the module regardless of scheduler state."""
        from services import slack_monitor
        orig_channels = dict(slack_monitor._channels)
        slack_monitor._channels.clear()
        slack_monitor._channels["C123"] = {"name": "general", "last_ts": "0"}

        resp = client.get("/api/slack-monitor")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["channels"]) == 1
        assert data["channels"][0]["id"] == "C123"
        assert data["channels"][0]["name"] == "general"

        slack_monitor._channels.clear()
        slack_monitor._channels.update(orig_channels)


# ====================================================================
# POST /api/slack-monitor/start
# ====================================================================


class TestSlackMonitorStart:
    """POST /api/slack-monitor/start -- verifies creds then resumes job."""

    def test_start_no_credentials(self, client, monkeypatch):
        from services import slack_monitor
        monkeypatch.setattr(slack_monitor, "_load_credentials", lambda: False)

        resp = client.post("/api/slack-monitor/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["running"] is False

    def test_start_auth_failure(self, client, monkeypatch):
        from services import slack_monitor

        def fake_load():
            slack_monitor._token = "xoxc-test"
            slack_monitor._cookie = "cookie-test"
            return True

        def fake_api(*a, **kw):
            raise Exception("auth failed")

        monkeypatch.setattr(slack_monitor, "_load_credentials", fake_load)
        monkeypatch.setattr(slack_monitor, "_slack_api", fake_api)

        resp = client.post("/api/slack-monitor/start")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_start_success(self, client, monkeypatch):
        """Credentials valid + auth ok -> start() returns True and resumes
        the scheduler job (if present)."""
        from services import slack_monitor, scheduler

        def fake_load():
            slack_monitor._token = "xoxc-test"
            slack_monitor._cookie = "cookie-test"
            return True

        monkeypatch.setattr(slack_monitor, "_load_credentials", fake_load)
        monkeypatch.setattr(slack_monitor, "_slack_api", lambda endpoint, params: {"ok": True})

        # No job registered in tests, so start() just returns True without
        # touching the scheduler. Patch get_scheduler defensively so even if
        # someone later registers jobs at import time, this test doesn't
        # hit a real scheduler.
        from unittest.mock import MagicMock
        sched = MagicMock()
        sched.get_job.return_value = None
        monkeypatch.setattr(scheduler, "get_scheduler", lambda: sched)

        resp = client.post("/api/slack-monitor/start")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ====================================================================
# POST /api/slack-monitor/stop
# ====================================================================


class TestSlackMonitorStop:
    """POST /api/slack-monitor/stop -- pauses the scheduled job."""

    def test_stop(self, client, monkeypatch):
        from services import scheduler
        from unittest.mock import MagicMock
        sched = MagicMock()
        monkeypatch.setattr(scheduler, "get_scheduler", lambda: sched)

        resp = client.post("/api/slack-monitor/stop")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        sched.pause_job.assert_called_once()

    def test_stop_when_not_running(self, client, monkeypatch):
        """Stop must be idempotent even when the job isn't registered."""
        from services import scheduler
        from unittest.mock import MagicMock
        sched = MagicMock()
        sched.pause_job.side_effect = Exception("no such job")
        monkeypatch.setattr(scheduler, "get_scheduler", lambda: sched)

        resp = client.post("/api/slack-monitor/stop")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ====================================================================
# POST /api/slack-monitor/channels
# DELETE /api/slack-monitor/channels/{channel_id}
# ====================================================================


class TestSlackMonitorChannels:
    """POST/DELETE /api/slack-monitor/channels."""

    def test_add_channel(self, client, monkeypatch):
        from services import slack_monitor
        orig = dict(slack_monitor._channels)
        slack_monitor._channels.clear()

        resp = client.post("/api/slack-monitor/channels", json={
            "channel_id": "C999",
            "name": "test-channel",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert any(ch["id"] == "C999" for ch in data["channels"])

        slack_monitor._channels.clear()
        slack_monitor._channels.update(orig)

    def test_add_channel_missing_id(self, client):
        resp = client.post("/api/slack-monitor/channels", json={"name": "no-id"})
        assert resp.status_code == 400

    def test_remove_channel(self, client, monkeypatch):
        from services import slack_monitor
        orig = dict(slack_monitor._channels)
        slack_monitor._channels["CREM"] = {"name": "to-remove", "last_ts": "0"}

        resp = client.delete("/api/slack-monitor/channels/CREM")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert "CREM" not in slack_monitor._channels

        slack_monitor._channels.clear()
        slack_monitor._channels.update(orig)

    def test_remove_nonexistent_channel(self, client, monkeypatch):
        """Removing a channel that does not exist should still return ok."""
        resp = client.delete("/api/slack-monitor/channels/CXYZ_NONEXISTENT")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
