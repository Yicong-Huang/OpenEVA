"""Tests for the settings store and the /api/settings endpoints."""

import common
from unittest.mock import patch

import pytest

from common import settings as core_settings


# ---- DB helpers ----

class TestSettingsCrud:
    def test_get_setting_returns_default_when_missing(self, patched_server):
        assert patched_server._db.get_setting("nope", default="fallback") == "fallback"
        assert patched_server._db.get_setting("nope") is None

    def test_set_then_get_roundtrips_jsonable_values(self, patched_server):
        db = patched_server._db
        db.set_setting("scalar", 42)
        assert db.get_setting("scalar") == 42
        db.set_setting("string", "hello")
        assert db.get_setting("string") == "hello"
        db.set_setting("list", [1, 2, 3])
        assert db.get_setting("list") == [1, 2, 3]
        db.set_setting("dict", {"a": 1, "b": [True, None]})
        assert db.get_setting("dict") == {"a": 1, "b": [True, None]}

    def test_set_setting_is_upsert(self, patched_server):
        db = patched_server._db
        db.set_setting("k", "first")
        db.set_setting("k", "second")
        assert db.get_setting("k") == "second"

    def test_list_settings_returns_all(self, patched_server):
        db = patched_server._db
        db.set_setting("alpha", 1)
        db.set_setting("beta", 2)
        out = db.list_settings()
        assert out["alpha"] == 1
        assert out["beta"] == 2

    def test_delete_setting_removes_row(self, patched_server):
        db = patched_server._db
        db.set_setting("disposable", 1)
        assert db.delete_setting("disposable") is True
        assert db.get_setting("disposable") is None
        # idempotent: second delete reports False
        assert db.delete_setting("disposable") is False


# ---- settings high-level helpers ----

class TestSeedFromYaml:
    def test_seeds_slack_monitor_channels(self, patched_server):
        # Only non-plugin yaml sections are seeded here today --
        # per-plugin keys (cookie/jwt/channel_id) come from each
        # plugin's `plugin.conf` via `common.plugins._seed_conf`.
        yaml_cfg = {
            "slack_monitor": {"channels": [{"id": "X", "name": "x"}]},
        }
        n = core_settings.seed_from_yaml(yaml_cfg)
        assert n == 1
        assert (patched_server._db.get_setting(
            "plugin.slack_monitor.channels") == [{"id": "X", "name": "x"}])

        # Re-seeding doesn't overwrite a user-edited value.
        patched_server._db.set_setting(
            "plugin.slack_monitor.channels", [{"id": "user", "name": "edit"}])
        n2 = core_settings.seed_from_yaml(yaml_cfg)
        assert n2 == 0  # nothing new written
        assert (patched_server._db.get_setting(
            "plugin.slack_monitor.channels") == [{"id": "user", "name": "edit"}])

    def test_seed_handles_missing_sections_gracefully(self, patched_server):
        # Empty yaml shouldn't error; should write nothing.
        assert core_settings.seed_from_yaml({}) == 0
        assert core_settings.seed_from_yaml(None) == 0  # type: ignore[arg-type]

    def test_seeds_jira_ticket_url_prefixes(self, patched_server):
        """The `jira` section seeds the ticket-prefix -> JIRA-URL map.
        Yaml ships the maintainer's defaults; first boot writes them
        into the settings DB; the Settings UI takes over from there."""
        n = core_settings.seed_from_yaml({
            "jira": {
                "ticket_url_prefixes": {
                    "ACME-": "https://jira.acme.com/browse/",
                },
            },
        })
        assert n == 1
        assert (patched_server._db.get_setting(
            core_settings.KEY_JIRA_TICKET_URL_PREFIXES) == {
            "ACME-": "https://jira.acme.com/browse/",
        })

    def test_jira_section_with_non_dict_values_is_ignored(self, patched_server):
        """Defensive: a misformatted yaml section (e.g. list instead
        of dict) doesn't write anything but doesn't crash either."""
        n = core_settings.seed_from_yaml({
            "jira": {
                "ticket_url_prefixes": "still not a dict",
            },
        })
        assert n == 0
        assert patched_server._db.get_setting(
            core_settings.KEY_JIRA_TICKET_URL_PREFIXES) is None

    def test_seeds_github_repo_config(self, patched_server):
        """The `github` section seeds the allow-list, fork->upstream
        map, and gh CLI account rules. These three drive
        `app_state._apply_repo_overrides_from_settings()` at boot.
        """
        n = core_settings.seed_from_yaml({
            "github": {
                "allowed_repos": ["example/repo", "acme/*"],
                "fork_to_upstream": {"alice/repo": "example/repo"},
                "account_rules": [
                    {"match": "acme/", "account": "alice-work"},
                    {"match": "", "account": "alice-personal"},
                ],
            },
        })
        assert n == 3
        assert patched_server._db.get_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS) == ["example/repo", "acme/*"]
        assert patched_server._db.get_setting(
            core_settings.KEY_GITHUB_FORK_TO_UPSTREAM) == {"alice/repo": "example/repo"}
        assert patched_server._db.get_setting(
            core_settings.KEY_GITHUB_ACCOUNT_RULES) == [
            {"match": "acme/", "account": "alice-work"},
            {"match": "", "account": "alice-personal"},
        ]

    def test_github_section_existing_db_value_wins(self, patched_server):
        """Yaml only seeds keys that have no existing DB row -- once
        the user edits via Settings UI, yaml stays frozen out."""
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS, ["existing/repo"])
        n = core_settings.seed_from_yaml({
            "github": {"allowed_repos": ["example/repo"]},
        })
        assert n == 0
        assert patched_server._db.get_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS) == ["existing/repo"]

    def test_github_section_wrong_types_ignored(self, patched_server):
        """Defensive: misformatted yaml entries are skipped, not crashed."""
        n = core_settings.seed_from_yaml({
            "github": {
                "allowed_repos": "not-a-list",
                "fork_to_upstream": ["not", "a", "dict"],
                "account_rules": {"not": "a list"},
            },
        })
        assert n == 0
        assert patched_server._db.get_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS) is None
        assert patched_server._db.get_setting(
            core_settings.KEY_GITHUB_FORK_TO_UPSTREAM) is None
        assert patched_server._db.get_setting(
            core_settings.KEY_GITHUB_ACCOUNT_RULES) is None


class TestReviewsLayoutRatios:
    """The Reviews page 3-pane width ratios are configurable via
    `ui.layout.reviews_col_ratios`. Settings DB wins; corrupt or
    missing values fall back to the canonical 25/35/40 default."""

    def test_default_when_unset(self, patched_server):
        assert (core_settings.get_reviews_col_ratios()
                == [25, 35, 40])

    def test_settings_override_wins(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_REVIEWS_RATIOS, [50, 25, 25])
        assert (core_settings.get_reviews_col_ratios()
                == [50, 25, 25])

    def test_float_values_coerced_to_int(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_REVIEWS_RATIOS, [33.0, 33.0, 34.0])
        assert (core_settings.get_reviews_col_ratios()
                == [33, 33, 34])

    def test_falls_back_on_wrong_length(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_REVIEWS_RATIOS, [50, 50])
        assert (core_settings.get_reviews_col_ratios()
                == [25, 35, 40])

    def test_falls_back_on_negative_value(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_REVIEWS_RATIOS, [50, -1, 50])
        assert (core_settings.get_reviews_col_ratios()
                == [25, 35, 40])

    def test_falls_back_on_non_list(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_REVIEWS_RATIOS, "25,35,40")
        assert (core_settings.get_reviews_col_ratios()
                == [25, 35, 40])

    def test_falls_back_on_non_numeric_element(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_REVIEWS_RATIOS, [25, "thirty-five", 40])
        assert (core_settings.get_reviews_col_ratios()
                == [25, 35, 40])


class TestCronJobsLayoutRatios:
    """The Cron Jobs page 2-pane layout (list + detail) is configurable
    via `ui.layout.cron_jobs_col_ratios`. Same validation contract as
    Reviews -- shared `_get_layout_ratios` helper."""

    def test_default_when_unset(self, patched_server):
        assert core_settings.get_cron_jobs_col_ratios() == [40, 60]

    def test_settings_override_wins(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_CRON_JOBS_RATIOS, [50, 50])
        assert core_settings.get_cron_jobs_col_ratios() == [50, 50]

    def test_falls_back_on_three_pane_value(self, patched_server):
        # Cron Jobs is 2-pane. A 3-element value is wrong-length for
        # this page and must NOT be partially applied.
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_CRON_JOBS_RATIOS, [25, 35, 40])
        assert core_settings.get_cron_jobs_col_ratios() == [40, 60]

    def test_falls_back_on_zero(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_CRON_JOBS_RATIOS, [0, 100])
        assert core_settings.get_cron_jobs_col_ratios() == [40, 60]


class TestPrsLayoutRatios:
    """Memo item #6: All PRs page is now wired to the layout-ratio
    settings system. Active in 3-pane mode (PR + task panel both
    visible). Default 25/40/35."""

    def test_default_when_unset(self, patched_server):
        assert core_settings.get_prs_col_ratios() == [25, 40, 35]

    def test_settings_override_wins(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_PRS_RATIOS, [20, 50, 30])
        assert core_settings.get_prs_col_ratios() == [20, 50, 30]

    def test_falls_back_on_two_pane_value(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_PRS_RATIOS, [50, 50])
        assert core_settings.get_prs_col_ratios() == [25, 40, 35]


class TestSessionsLayoutRatios:
    """Memo item #6: All Sessions page is now wired to the
    layout-ratio settings system. Active when a PR is selected
    (3-pane mode). Default 25/40/35."""

    def test_default_when_unset(self, patched_server):
        assert core_settings.get_sessions_col_ratios() == [25, 40, 35]

    def test_settings_override_wins(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_SESSIONS_RATIOS, [30, 35, 35])
        assert core_settings.get_sessions_col_ratios() == [30, 35, 35]

    def test_falls_back_on_negative(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_SESSIONS_RATIOS, [25, -1, 75])
        assert core_settings.get_sessions_col_ratios() == [25, 40, 35]


class TestWorklogHorizonKnobs:
    """The WorkLog page horizons (day-mode days, standup-mode weeks)
    are settings-driven so heavy users with longer / shorter retention
    horizons can tune them. Bounds are enforced so a typo can't DOS
    the page with an unbounded fetch loop."""

    def test_day_mode_default(self, patched_server):
        assert core_settings.get_worklog_day_mode_days() == 60

    def test_day_mode_override_in_range(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_WORKLOG_DAY_MODE_DAYS, 120)
        assert core_settings.get_worklog_day_mode_days() == 120

    def test_day_mode_falls_back_below_min(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_WORKLOG_DAY_MODE_DAYS, 3)  # below min 7
        assert core_settings.get_worklog_day_mode_days() == 60

    def test_day_mode_falls_back_above_max(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_WORKLOG_DAY_MODE_DAYS, 999)  # above max 365
        assert core_settings.get_worklog_day_mode_days() == 60

    def test_day_mode_falls_back_on_string_garbage(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_WORKLOG_DAY_MODE_DAYS, "not a number")
        assert core_settings.get_worklog_day_mode_days() == 60

    def test_day_mode_accepts_numeric_string(self, patched_server):
        """Some settings paths persist values as strings; we should
        coerce a clean numeric string."""
        patched_server._db.set_setting(
            core_settings.KEY_WORKLOG_DAY_MODE_DAYS, "30")
        assert core_settings.get_worklog_day_mode_days() == 30

    def test_day_mode_rejects_bool_subtype(self, patched_server):
        """Defensive: `bool` is an `int` subclass in Python; if a stale
        row writes True/False, we don't want it to count as 1/0 and
        sneak past the range check."""
        patched_server._db.set_setting(
            core_settings.KEY_WORKLOG_DAY_MODE_DAYS, True)
        assert core_settings.get_worklog_day_mode_days() == 60

    def test_standup_mode_default(self, patched_server):
        assert core_settings.get_worklog_standup_mode_weeks() == 8

    def test_standup_mode_override_in_range(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_WORKLOG_STANDUP_MODE_WEEKS, 4)
        assert core_settings.get_worklog_standup_mode_weeks() == 4

    def test_standup_mode_falls_back_above_max(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_WORKLOG_STANDUP_MODE_WEEKS, 200)
        assert core_settings.get_worklog_standup_mode_weeks() == 8


class TestTicketsListLimit:
    """Tickets queue-pane fetch horizon. Default 100 cached tickets,
    bounded [10, 1000]."""

    def test_default(self, patched_server):
        assert core_settings.get_tickets_list_limit() == 100

    def test_override_in_range(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_TICKETS_LIST_LIMIT, 250)
        assert core_settings.get_tickets_list_limit() == 250

    def test_falls_back_below_min(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_TICKETS_LIST_LIMIT, 5)
        assert core_settings.get_tickets_list_limit() == 100

    def test_falls_back_above_max(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_TICKETS_LIST_LIMIT, 9999)
        assert core_settings.get_tickets_list_limit() == 100


class TestReviewsSyncSearchLimit:
    """Reviews `gh search prs --limit` is now settings-driven so users
    can lift on busy accounts / trim on quieter ones. Default 50,
    bounds [10, 200] (GitHub's own search cap)."""

    def test_default(self, patched_server):
        assert core_settings.get_reviews_sync_search_limit() == 50

    def test_override_in_range(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_REVIEWS_SYNC_SEARCH_LIMIT, 150)
        assert core_settings.get_reviews_sync_search_limit() == 150

    def test_falls_back_above_max(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_REVIEWS_SYNC_SEARCH_LIMIT, 9999)
        assert core_settings.get_reviews_sync_search_limit() == 50


class TestCronJobsHistoryLimit:
    """The CronJobs JobCard history strip horizon is settings-driven so
    power users with chatty `/loop`-driven jobs can extend the strip
    without code changes. Bounds [5, 500]."""

    def test_default(self, patched_server):
        assert core_settings.get_cron_jobs_runs_history_limit() == 20

    def test_override_in_range(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_CRON_JOBS_RUNS_HISTORY_LIMIT, 75)
        assert core_settings.get_cron_jobs_runs_history_limit() == 75

    def test_falls_back_below_min(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_CRON_JOBS_RUNS_HISTORY_LIMIT, 2)
        assert core_settings.get_cron_jobs_runs_history_limit() == 20

    def test_falls_back_above_max(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_CRON_JOBS_RUNS_HISTORY_LIMIT, 9999)
        assert core_settings.get_cron_jobs_runs_history_limit() == 20


class TestTicketsLayoutRatios:
    """Tickets page (`/tickets`) is now ratio-driven like the other
    multi-pane pages. 3-pane mode (queue / session / detail) is
    active when a ticket is selected; queue spans 100% otherwise.
    Default 30/35/35."""

    def test_default_when_unset(self, patched_server):
        assert core_settings.get_tickets_col_ratios() == [30, 35, 35]

    def test_settings_override_wins(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_TICKETS_RATIOS, [40, 30, 30])
        assert core_settings.get_tickets_col_ratios() == [40, 30, 30]

    def test_falls_back_on_wrong_length(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_TICKETS_RATIOS, [50, 50])  # 2-pane
        assert core_settings.get_tickets_col_ratios() == [30, 35, 35]

    def test_falls_back_on_zero_or_negative(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_TICKETS_RATIOS, [30, 0, 70])
        assert core_settings.get_tickets_col_ratios() == [30, 35, 35]


class TestLayoutRatiosSharedHelper:
    """The three accessors share `_get_layout_ratios`. Lock the
    contract: same key + value flowing through the helper produces
    identical behaviour regardless of the calling accessor."""

    def test_reviews_and_cron_share_helper_semantics(self, patched_server):
        # A wrong-length value in EITHER key causes that accessor's
        # default to apply -- the OTHER accessor isn't affected.
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_REVIEWS_RATIOS, [70, 30])  # wrong N
        patched_server._db.set_setting(
            core_settings.KEY_LAYOUT_CRON_JOBS_RATIOS, [70, 30])  # right N
        assert core_settings.get_reviews_col_ratios() == [25, 35, 40]
        assert core_settings.get_cron_jobs_col_ratios() == [70, 30]

class TestGetIntervalSeconds:
    """The shared `get_interval_seconds(key, default, min_s, max_s)`
    helper centralises poller-cadence reading so jira_sync,
    slack_monitor, etc. don't each ship their own copy of
    read+validate+clamp. Lock down the contract."""

    KEY = "service.intervals.test_pollr"

    def test_returns_default_when_unset(self, patched_server):
        assert core_settings.get_interval_seconds(self.KEY, 60) == 60

    def test_settings_override_wins(self, patched_server):
        patched_server._db.set_setting(self.KEY, 240)
        assert core_settings.get_interval_seconds(self.KEY, 60) == 240

    def test_clamps_below_min(self, patched_server):
        patched_server._db.set_setting(self.KEY, 1)
        # default min_s is 5
        assert core_settings.get_interval_seconds(self.KEY, 60) == 5

    def test_caller_can_raise_floor(self, patched_server):
        # JIRA case: 30s floor because of upstream rate limits.
        patched_server._db.set_setting(self.KEY, 10)
        assert (core_settings.get_interval_seconds(
            self.KEY, 300, min_s=30) == 30)

    def test_clamps_above_max(self, patched_server):
        patched_server._db.set_setting(self.KEY, 999_999)
        assert (core_settings.get_interval_seconds(self.KEY, 60)
                == 86400)

    def test_falls_back_on_zero(self, patched_server):
        # Pollers historically use 0 to mean "paused". The bounded
        # accessor is for live cadences -- zero is invalid here. The
        # poller's own enable-flag is the correct pause mechanism.
        patched_server._db.set_setting(self.KEY, 0)
        assert core_settings.get_interval_seconds(self.KEY, 60) == 60

    def test_falls_back_on_negative(self, patched_server):
        patched_server._db.set_setting(self.KEY, -10)
        assert core_settings.get_interval_seconds(self.KEY, 60) == 60

    def test_falls_back_on_string(self, patched_server):
        patched_server._db.set_setting(self.KEY, "300")
        assert core_settings.get_interval_seconds(self.KEY, 60) == 60

    def test_falls_back_on_bool(self, patched_server):
        # `bool` is an `int` subclass in Python (`True == 1`); guard
        # explicitly so `set_setting(KEY, True)` doesn't yield a 5s
        # cadence (the min floor for a 1-second clamp).
        patched_server._db.set_setting(self.KEY, True)
        assert core_settings.get_interval_seconds(self.KEY, 60) == 60

    def test_float_is_truncated_to_int(self, patched_server):
        patched_server._db.set_setting(self.KEY, 47.9)
        assert core_settings.get_interval_seconds(self.KEY, 60) == 47


class TestPollerSettingsKeysExist:
    """Each poller has its own settings key so users can tune the
    cadence without touching code. Ensure none of them collide and
    they all live in the canonical `service.intervals.*` namespace."""

    def test_all_poller_keys_share_namespace(self):
        keys = [
            core_settings.KEY_INTERVAL_UBEREATS,
            core_settings.KEY_INTERVAL_GITHUB_POLL,
            core_settings.KEY_INTERVAL_CERT_CHECK,
            core_settings.KEY_INTERVAL_USAGE_REFRESH,
            core_settings.KEY_INTERVAL_SLACK_MONITOR,
        ]
        for k in keys:
            assert k.startswith("service.intervals."), k
        # No duplicates.
        assert len(set(keys)) == len(keys)


class TestPollerAccessorsDelegate:
    """Each `services.<poller>.get_interval_seconds()` is a one-liner
    that delegates to `common.settings.get_interval_seconds`. Verify the
    contract: default-when-unset, settings-override-wins, and the
    per-poller floor (each upstream service has different rate-limit
    characteristics)."""

    @pytest.fixture(autouse=True)
    def _imports(self):
        # This class only covers the framework-level service pollers
        # that ship with the OSS install. Per-plugin poller interval
        # tests live with the plugin's own tests.
        from services import (
            cert_checker, github_poller,
            slack_monitor, usage_refresh,
        )
        return cert_checker, github_poller, slack_monitor, usage_refresh

    def test_cert_checker_default_300s(self, patched_server, _imports):
        from services import cert_checker
        assert cert_checker.get_interval_seconds() == 300

    def test_cert_checker_override(self, patched_server, _imports):
        from services import cert_checker
        patched_server._db.set_setting(
            core_settings.KEY_INTERVAL_CERT_CHECK, 600)
        assert cert_checker.get_interval_seconds() == 600

    def test_cert_checker_below_floor_clamps_to_30(
        self, patched_server, _imports,
    ):
        from services import cert_checker
        patched_server._db.set_setting(
            core_settings.KEY_INTERVAL_CERT_CHECK, 1)
        assert cert_checker.get_interval_seconds() == 30

    def test_usage_refresh_default_120s(self, patched_server, _imports):
        from services import usage_refresh
        assert usage_refresh.get_interval_seconds() == 120

    def test_usage_refresh_override(self, patched_server, _imports):
        from services import usage_refresh
        patched_server._db.set_setting(
            core_settings.KEY_INTERVAL_USAGE_REFRESH, 90)
        assert usage_refresh.get_interval_seconds() == 90

    def test_github_poller_default_10s(self, patched_server, _imports):
        from services import github_poller
        assert github_poller.get_interval_seconds() == 10

    def test_github_poller_override_can_go_low(
        self, patched_server, _imports,
    ):
        # GitHub Notifications API has its own X-Poll-Interval throttle;
        # we just guard a 5s minimum on our side.
        from services import github_poller
        patched_server._db.set_setting(
            core_settings.KEY_INTERVAL_GITHUB_POLL, 5)
        assert github_poller.get_interval_seconds() == 5
        patched_server._db.set_setting(
            core_settings.KEY_INTERVAL_GITHUB_POLL, 1)
        assert github_poller.get_interval_seconds() == 5  # clamped

    def test_slack_monitor_default_30s(self, patched_server, _imports):
        from services import slack_monitor
        assert slack_monitor.get_interval_seconds() == 30

    def test_slack_monitor_override(self, patched_server, _imports):
        from services import slack_monitor
        patched_server._db.set_setting(
            core_settings.KEY_INTERVAL_SLACK_MONITOR, 60)
        assert slack_monitor.get_interval_seconds() == 60

    def test_slack_monitor_below_floor_clamps_to_10(
        self, patched_server, _imports,
    ):
        # Slack rate limits at ~1 req/sec on user tokens.
        from services import slack_monitor
        patched_server._db.set_setting(
            core_settings.KEY_INTERVAL_SLACK_MONITOR, 1)
        assert slack_monitor.get_interval_seconds() == 10


class TestJiraSyncUsesSharedAccessor:
    """`services.jira_sync.get_interval_seconds` is now a one-liner
    delegate to `common.settings.get_interval_seconds`. Lock that the
    public contract from the JIRA side hasn't changed: default = 300s,
    floor = 30s, ceiling = 86400s."""

    def test_default_when_unset(self, patched_server):
        from services import jira_sync as _js
        assert _js.get_interval_seconds() == 300

    def test_override_is_returned(self, patched_server):
        from services import jira_sync as _js
        patched_server._db.set_setting(_js.KEY_JIRA_SYNC_INTERVAL, 600)
        assert _js.get_interval_seconds() == 600

    def test_override_below_jira_floor_clamps_to_30(self, patched_server):
        # JIRA Cloud rate limits enforce a 30s floor regardless of what
        # the user puts in the setting.
        from services import jira_sync as _js
        patched_server._db.set_setting(_js.KEY_JIRA_SYNC_INTERVAL, 5)
        assert _js.get_interval_seconds() == 30


# ---- HTTP API ----

class TestSettingsApi:
    def test_list_settings_endpoint(self, client, patched_server):
        patched_server._db.set_setting("api.test.k", "v")
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["settings"].get("api.test.k") == "v"

    def test_get_one_setting(self, client, patched_server):
        patched_server._db.set_setting("api.one", 99)
        resp = client.get("/api/settings/api.one")
        assert resp.status_code == 200
        assert resp.json() == {"key": "api.one", "value": 99}

    def test_get_missing_setting_returns_404(self, client):
        resp = client.get("/api/settings/no.such.key")
        assert resp.status_code == 404

    def test_put_setting_creates_and_updates(self, client, patched_server):
        # Create.
        resp = client.put("/api/settings/api.put",
                          json={"value": ["a", "b"]})
        assert resp.status_code == 200
        assert resp.json()["value"] == ["a", "b"]
        assert patched_server._db.get_setting("api.put") == ["a", "b"]
        # Update.
        resp = client.put("/api/settings/api.put",
                          json={"value": {"x": 1}})
        assert resp.status_code == 200
        assert patched_server._db.get_setting("api.put") == {"x": 1}

    def test_put_setting_accepts_null_value(self, client, patched_server):
        # Null is a valid JSON value: lets the UI clear a field
        # without removing the row entirely.
        resp = client.put("/api/settings/api.null", json={"value": None})
        assert resp.status_code == 200
        assert patched_server._db.get_setting("api.null") is None

    def test_delete_setting_returns_ok_then_404(self, client, patched_server):
        patched_server._db.set_setting("api.del", "x")
        resp = client.delete("/api/settings/api.del")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # Second delete: gone now.
        resp = client.delete("/api/settings/api.del")
        assert resp.status_code == 404


# ---- Plugin-specific accessors ----

# Per-plugin accessor + route-integration tests for the optional
# folder plugins moved out of the framework to the
# extension namespaces alongside their implementations. The settings
# framework itself is exercised by TestSettingsCrud above with
# vendor-neutral keys.
