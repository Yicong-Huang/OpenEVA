"""Tests for the plugin framework in `core/common.plugins.py`."""

import common
from unittest.mock import MagicMock

import pytest

from common import plugins


@pytest.fixture(autouse=True)
def _clean_registry():
    """The autouse `_isolate_plugin_registry` in conftest snapshots
    the production registry and restores it after each test, but
    inside this file we want to start from an EMPTY registry so
    we're testing register/discover/initialize from scratch."""
    common.plugins.reset_for_tests()
    yield


# ---- register / all_plugins ----

class TestRegister:
    def test_register_appends_in_order(self):
        a, b = MagicMock(id="a"), MagicMock(id="b")
        common.plugins.register(a)
        common.plugins.register(b)
        assert [p.id for p in common.plugins.all_plugins()] == ["a", "b"]

    def test_register_is_idempotent_on_id(self):
        # The same id twice (e.g. test imports the module twice via
        # a stale cache) must not double-register; otherwise plugin
        # callbacks would fire twice per event.
        first = MagicMock(id="dupe")
        second = MagicMock(id="dupe")
        common.plugins.register(first)
        common.plugins.register(second)
        assert common.plugins.all_plugins() == [first]

    def test_register_falls_back_to_class_name_when_id_missing(self):
        class NoId:
            pass

        nameless = NoId()
        common.plugins.register(nameless)
        assert common.plugins.all_plugins() == [nameless]


# ---- initialize ----

class TestInitialize:
    def test_initialize_calls_register_and_start_jobs(self):
        app = MagicMock()
        sched = MagicMock()
        p = MagicMock(id="p1")
        common.plugins.register(p)
        common.plugins.initialize(app, sched)
        p.register.assert_called_once_with(app)
        p.start_jobs.assert_called_once_with(sched)

    def test_initialize_swallows_register_failure_and_continues(self):
        # A broken plugin must not abort the rest. Two plugins; the
        # first raises in register(), the second still gets both
        # callbacks.
        bad = MagicMock(id="bad")
        bad.register.side_effect = RuntimeError("boom")
        good = MagicMock(id="good")
        common.plugins.register(bad)
        common.plugins.register(good)
        common.plugins.initialize(MagicMock(), MagicMock())
        good.register.assert_called_once()
        good.start_jobs.assert_called_once()

    def test_initialize_swallows_start_jobs_failure(self):
        bad = MagicMock(id="bad")
        bad.start_jobs.side_effect = RuntimeError("boom")
        common.plugins.register(bad)
        # Must not raise.
        common.plugins.initialize(MagicMock(), MagicMock())
        bad.start_jobs.assert_called_once()

    def test_initialize_skips_callbacks_that_arent_implemented(self):
        # A plugin that only mounts routes but has no scheduled work
        # can omit start_jobs(). The framework checks `hasattr` so
        # the absence is benign.
        class OnlyRegister:
            id = "lite"
            name = "Lite"

            def register(self, app):
                self.app = app

        p = OnlyRegister()
        common.plugins.register(p)
        common.plugins.initialize(MagicMock(name="app"), MagicMock())
        assert hasattr(p, "app")


# ---- discover ----

class TestDiscover:
    def test_missing_package_is_silently_skipped(self):
        # An OSS install may ship without any extra plugin packages;
        # discovery must tolerate the missing package without raising
        # or polluting the registry.
        n = common.plugins.discover("nonexistent.package.xyz")
        assert n == 0
        assert common.plugins.all_plugins() == []

    def test_discover_dir_skips_missing_directory(self):
        # A bare OSS checkout without any extension trees must not
        # crash discovery -- `discover_dir` should return 0 and leave
        # the registry untouched.
        from pathlib import Path
        n = common.plugins.discover_dir(Path("/nonexistent/eva/plugins"))
        assert n == 0
        assert common.plugins.all_plugins() == []


# ---- reset_for_tests ----

class TestResetForTests:
    def test_clears_registry_and_id_set(self):
        common.plugins.register(MagicMock(id="a"))
        common.plugins.register(MagicMock(id="b"))
        common.plugins.reset_for_tests()
        assert common.plugins.all_plugins() == []
        # Re-registering the same id afterwards must succeed (the
        # id-dedup set is also cleared).
        common.plugins.register(MagicMock(id="a"))
        assert [p.id for p in common.plugins.all_plugins()] == ["a"]
