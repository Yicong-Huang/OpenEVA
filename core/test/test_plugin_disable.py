"""Tests for the plugin enable/disable framework.

Covers:
  - The generic `is_plugin_enabled(name)` lookup.
  - The `/api/plugins/enabled` endpoint that surfaces every
    plugin's current toggle to the Settings UI.
  - Framework-level poller-disable hooks (github_poller,
    cert_checker, slack_monitor): each respects its plugin flag
    and short-circuits when off.

Per-plugin route + poller disable behaviour is tested with each
plugin under its own test tree.
"""

from unittest.mock import patch, MagicMock

from common import settings as core_settings


class TestPollersRespectDisable:
    """Generic services (github poller, cert checker, slack monitor)
    each consult `is_plugin_enabled(<service>)` before doing work.
    The early-return must:
      - never spend external API quota (gh / network);
      - never advance internal throttle clocks (otherwise re-
        enabling silently delays the first real tick).
    """

    def test_github_poller_skips_when_off(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_PLUGIN_GITHUB_POLL_ENABLED, False)
        from services import github_poller
        # The poller would set _gh_last_poll['ts'] and shell out to gh
        # if it ran. Capture both via patches.
        before = github_poller._gh_last_poll["ts"]
        with patch("app_state.gh_run") as m:
            github_poller._poll_github_notifications()
        m.assert_not_called()
        # The early return must NOT advance the throttle clock either,
        # otherwise re-enabling silently delays the first real tick.
        assert github_poller._gh_last_poll["ts"] == before

    def test_cert_checker_skips_when_off(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_PLUGIN_CERT_ENABLED, False)
        from services import cert_checker
        with patch("services.cert_checker.get_certs") as m:
            cert_checker.check_certs_once()
        m.assert_not_called()

    def test_slack_monitor_skips_when_off(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_PLUGIN_SLACK_ENABLED, False)
        from services import slack_monitor
        # Forcing the "ready to poll" state so the only thing that
        # could short-circuit the call is the plugin flag.
        with patch.object(slack_monitor, "_creds_loaded", True), \
             patch.object(slack_monitor, "_channels", {"x": MagicMock()}), \
             patch("services.slack_monitor._poll_channels") as m:
            slack_monitor.poll_slack_once()
        m.assert_not_called()


class TestPluginsEnabledEndpoint:
    def test_returns_default_enabled_for_framework_services(self, client):
        """The framework-level service toggles (slack_monitor /
        github_poll / cert_tracker) and the always-present `pr` widget
        must show up in `/api/plugins/enabled` defaulted to True. Plugin
        instances contributed by extensions are tested separately."""
        resp = client.get("/api/plugins/enabled")
        assert resp.status_code == 200
        plugins = resp.json()["plugins"]
        for name in ("pr", "slack_monitor", "github_poll", "cert_tracker"):
            assert name in plugins, f"missing {name}"
            assert plugins[name] is True

    def test_reflects_user_overrides(self, client, patched_server):
        """User edits to `plugin.<id>.enabled` are reflected by the
        endpoint. Uses the OSS-default `pr` plugin so the assertion
        works on any install."""
        patched_server._db.set_setting("plugin.pr.enabled", False)
        resp = client.get("/api/plugins/enabled")
        plugins = resp.json()["plugins"]
        assert plugins["pr"] is False
        # Untouched framework services remain enabled.
        assert plugins["github_poll"] is True


class TestIsPluginEnabled:
    def test_default_enabled(self, patched_server):
        assert core_settings.is_plugin_enabled("anything") is True

    def test_explicit_false(self, patched_server):
        patched_server._db.set_setting("plugin.foo.enabled", False)
        assert core_settings.is_plugin_enabled("foo") is False

    def test_truthy_non_false_values_count_as_enabled(self, patched_server):
        # Defensive: any non-false value (string, int, etc.) counts as
        # enabled so a UI bug that writes "true"/1 doesn't accidentally
        # disable a plugin.
        patched_server._db.set_setting("plugin.foo.enabled", "yes")
        assert core_settings.is_plugin_enabled("foo") is True
        patched_server._db.set_setting("plugin.foo.enabled", 1)
        assert core_settings.is_plugin_enabled("foo") is True
