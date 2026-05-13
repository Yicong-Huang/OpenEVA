"""Tests for services/usage_refresh.py.

The scheduler tick must:
  - populate `common.system._usage_cache` on success
  - emit `usage.updated` (persisted, so the event feed records the refresh)
  - swallow `_fetch_usage` exceptions so one bad tick never kills the job
  - swallow event-bus failures without poisoning the cache
"""

from unittest.mock import patch


class TestRefreshUsageOnce:
    def setup_method(self):
        from common import system as core_sys
        self._orig_cache = dict(core_sys._usage_cache)
        core_sys._usage_cache["data"] = None
        core_sys._usage_cache["ts"] = 0

    def teardown_method(self):
        from common import system as core_sys
        core_sys._usage_cache.clear()
        core_sys._usage_cache.update(self._orig_cache)

    def test_updates_cache_and_emits_persisted_event(self, monkeypatch):
        from common import system as core_sys
        import services.usage_refresh as ur
        import app_state

        fake = {"daily": "1.23", "weekly": "5.55", "monthly": "22.00", "tier": "Standard"}
        monkeypatch.setattr(ur, "_fetch_usage", lambda: fake)

        emitted = []
        monkeypatch.setattr(app_state, "emit_event",
                            lambda evt, data, **kw: emitted.append((evt, data, kw)))

        ur.refresh_usage_once()

        assert core_sys._usage_cache["data"] == fake
        assert core_sys._usage_cache["ts"] > 0
        assert len(emitted) == 1
        evt, data, kw = emitted[0]
        assert evt == "usage.updated"
        # Default persist (not passed) -- event must show up in the feed.
        assert kw.get("persist", True) is True
        assert "daily=1.23" in data["message"]

    def test_no_cache_update_on_fetch_returning_none(self, monkeypatch):
        from common import system as core_sys
        import services.usage_refresh as ur
        import app_state

        monkeypatch.setattr(ur, "_fetch_usage", lambda: None)

        emitted = []
        monkeypatch.setattr(app_state, "emit_event",
                            lambda *a, **kw: emitted.append(a))

        ur.refresh_usage_once()
        assert core_sys._usage_cache["data"] is None
        assert emitted == []

    def test_swallows_fetch_exception(self, monkeypatch, capsys):
        import services.usage_refresh as ur

        def boom():
            raise RuntimeError("agent CLI down")

        monkeypatch.setattr(ur, "_fetch_usage", boom)
        # Must not raise.
        ur.refresh_usage_once()
        assert "[usage-refresh] error" in capsys.readouterr().out

    def test_event_bus_failure_does_not_poison_cache(self, monkeypatch):
        """If emit_event throws after a successful fetch, the cache must
        still be updated so the next /api/usage read sees fresh data."""
        from common import system as core_sys
        import services.usage_refresh as ur
        import app_state

        fake = {"daily": "1.0", "weekly": "2.0", "monthly": "3.0"}
        monkeypatch.setattr(ur, "_fetch_usage", lambda: fake)

        def boom(*a, **kw):
            raise RuntimeError("event bus broken")

        monkeypatch.setattr(app_state, "emit_event", boom)
        ur.refresh_usage_once()  # must not raise
        assert core_sys._usage_cache["data"] == fake

    def test_interval_constant_exported(self):
        """server.py reads this when registering the scheduler job; if it
        gets renamed silently the cron cadence would fall back to whatever
        default and nobody would notice."""
        import services.usage_refresh as ur
        assert ur.USAGE_REFRESH_INTERVAL_SECONDS == 120
