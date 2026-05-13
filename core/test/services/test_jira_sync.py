"""Tests for services.jira_sync (periodic JIRA -> tickets-cache sync)."""

from unittest.mock import patch

from common import settings as core_settings
from services import jira_sync


def _configure_jira(db):
    """Helper: fill in JIRA settings so is_configured() returns True.
    The legacy single-instance keys were replaced by a multi-instance
    list under `service.jira.instances`; a valid entry needs both
    `base_url` and `api_token`."""
    db.set_setting(core_settings.KEY_JIRA_INSTANCES, [{
        "name": "primary",
        "base_url": "https://acme.atlassian.net",
        "auth_type": "basic",
        "email": "alice@example.com",
        "api_token": "TOK",
    }])


class TestGetIntervalSeconds:
    def test_default_when_unset(self, patched_server):
        # Uncovered key -> the module's default constant.
        assert jira_sync.get_interval_seconds() == jira_sync.JIRA_SYNC_INTERVAL_SECONDS

    def test_user_override_within_bounds(self, patched_server):
        patched_server._db.set_setting(jira_sync.KEY_JIRA_SYNC_INTERVAL, 600)
        assert jira_sync.get_interval_seconds() == 600

    def test_clamps_to_minimum(self, patched_server):
        # JIRA rate-limits aggressively; a 5-second interval would
        # quickly trip a 429. Floor to 30s.
        patched_server._db.set_setting(jira_sync.KEY_JIRA_SYNC_INTERVAL, 5)
        assert jira_sync.get_interval_seconds() == 30

    def test_clamps_to_maximum(self, patched_server):
        # Setting it to a year shouldn't actually disarm the job for
        # a year -- cap at one day so a typo can't accidentally pause
        # syncing forever.
        patched_server._db.set_setting(
            jira_sync.KEY_JIRA_SYNC_INTERVAL, 60 * 60 * 24 * 365)
        assert jira_sync.get_interval_seconds() == 86400

    def test_invalid_value_falls_back_to_default(self, patched_server):
        patched_server._db.set_setting(jira_sync.KEY_JIRA_SYNC_INTERVAL,
                                       "not-a-number")
        assert jira_sync.get_interval_seconds() == jira_sync.JIRA_SYNC_INTERVAL_SECONDS

    def test_zero_falls_back_to_default(self, patched_server):
        # 0 should be treated as "use default" rather than "fire as
        # fast as possible" (which APScheduler would interpret as a
        # 0-second interval -> immediate hot loop).
        patched_server._db.set_setting(jira_sync.KEY_JIRA_SYNC_INTERVAL, 0)
        assert jira_sync.get_interval_seconds() == jira_sync.JIRA_SYNC_INTERVAL_SECONDS


class TestSyncTicketsOnce:
    def test_skips_when_not_configured(self, patched_server):
        # JIRA settings empty -> no-op, no exception.
        with patch("services.jira_sync._tickets.sync") as m:
            jira_sync.sync_tickets_once()
        m.assert_not_called()

    def test_calls_sync_when_configured(self, patched_server):
        _configure_jira(patched_server._db)
        with patch("services.jira_sync._tickets.sync") as m:
            jira_sync.sync_tickets_once()
        m.assert_called_once_with()

    def test_skips_when_plugin_disabled(self, patched_server):
        _configure_jira(patched_server._db)
        # Disable the JIRA plugin via the same toggle the rest of the
        # plugins use.
        patched_server._db.set_setting("plugin.jira.enabled", False)
        with patch("services.jira_sync._tickets.sync") as m:
            jira_sync.sync_tickets_once()
        m.assert_not_called()

    def test_swallows_runtime_errors(self, patched_server):
        """A transient JIRA outage must NOT propagate -- otherwise the
        scheduler thread could surface the error on every tick and
        spam logs."""
        _configure_jira(patched_server._db)
        with patch("services.jira_sync._tickets.sync",
                   side_effect=RuntimeError("simulated outage")):
            # Should not raise.
            jira_sync.sync_tickets_once()

    def test_swallows_value_errors_too(self, patched_server):
        # common.tickets.sync raises ValueError when config gets cleared
        # mid-tick; we shouldn't crash on that race either.
        _configure_jira(patched_server._db)
        with patch("services.jira_sync._tickets.sync",
                   side_effect=ValueError("config cleared")):
            jira_sync.sync_tickets_once()
