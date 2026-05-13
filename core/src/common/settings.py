"""Settings store: typed access to the `settings` JSON key-value table.

This module is the single source of truth for "user-editable
configuration" -- values that historically lived in `config.yaml` or
were hardcoded as module-level constants in adapters / services. The
frontend Settings UI reads / writes through the routes layer; the rest
of the codebase calls helpers here so the underlying storage can keep
evolving without breaking callers.

Each helper exposes a (`get`, `set`, default) triple for one logical
setting. Defaults are returned when the row is absent so the app boots
correctly on a fresh DB.
"""

from __future__ import annotations

from typing import Any

import app_state


# ---- Generic helpers (used directly by the API layer) ----

def get_value(key: str, default: Any = None) -> Any:
    return app_state._db.get_setting(key, default=default)


def set_value(key: str, value: Any) -> None:
    app_state._db.set_setting(key, value)


def list_all() -> dict:
    return app_state._db.list_settings()


def delete_value(key: str) -> bool:
    return app_state._db.delete_setting(key)


# Per-plugin settings (forkable cookie, slack channel ids, JWT
# refreshes, etc.) all live under `plugin.<id>.<field>` and are
# seeded by each plugin's `plugin.conf`. Core no longer enumerates
# them here -- adding a new plugin just means dropping a new
# folder under `<ext>/src/` with its own conf.

KEY_GITHUB_ALLOWED_REPOS = "service.github.allowed_repos"
KEY_GITHUB_FORK_TO_UPSTREAM = "service.github.fork_to_upstream"

# Maps GitHub repos to local filesystem paths so server-side features
# (today: ticket triage's `blame` field, future: PR-side diff viewers)
# can shell out to `git log` / `git blame` against the local clone.
# Empty default keeps the install generic for OSS users; this is a
# per-machine setting -- example value:
#   {
#     "acme/widget": "/home/alice/code/widget",
#     "acme-corp/internal-platform": "/home/alice/code/platform",
#   }
KEY_GIT_LOCAL_REPO_PATHS = "service.git.local_repo_paths"

# Ticket-URL prefix routing: {ticket_prefix: jira_base_url}. Used to
# convert "EX-12345" -> the JIRA URL when a task carries a ticket
# id. Empty default; configure via yaml on first boot or the Settings UI.
KEY_JIRA_TICKET_URL_PREFIXES = "service.jira.ticket_url_prefixes"

# Page width-ratio settings. Each lives at `ui.layout.<page>_col_ratios`
# and stores a list of pane widths in percent. Validation contract is
# shared (`_get_layout_ratios`): list of N positive numbers, otherwise
# fall back to the canonical default. The frontend's `useLayoutRatios`
# hook applies the same shape check on its side.
KEY_LAYOUT_REVIEWS_RATIOS = "ui.layout.reviews_col_ratios"
DEFAULT_REVIEWS_RATIOS = [25, 35, 40]

KEY_LAYOUT_CRON_JOBS_RATIOS = "ui.layout.cron_jobs_col_ratios"
DEFAULT_CRON_JOBS_RATIOS = [40, 60]

# 3-pane mode: list / task / detail. Active when a PR is selected
# AND the PR has an associated task. Other modes (2-pane / 1-pane) use
# their own hardcoded fallbacks since they're degenerate cases.
KEY_LAYOUT_PRS_RATIOS = "ui.layout.prs_col_ratios"
DEFAULT_PRS_RATIOS = [25, 40, 35]

# 3-pane mode: list / detail / task. Active when a PR is selected.
KEY_LAYOUT_SESSIONS_RATIOS = "ui.layout.sessions_col_ratios"
DEFAULT_SESSIONS_RATIOS = [25, 40, 35]

# 3-pane mode: queue / session-card / detail. Active when a ticket is
# selected; the queue takes 100% otherwise.
KEY_LAYOUT_TICKETS_RATIOS = "ui.layout.tickets_col_ratios"
DEFAULT_TICKETS_RATIOS = [30, 35, 35]


def _get_layout_ratios(key: str, defaults: list[int]) -> list[int]:
    """Shared accessor: returns the configured ratios when valid (list
    of `len(defaults)` positive numbers), otherwise the canonical
    default. Centralises validation so per-page accessors stay
    one-liners and the contract can't drift across pages."""
    v = get_value(key, default=None)
    if (isinstance(v, list) and len(v) == len(defaults)
            and all(isinstance(x, (int, float)) and x > 0 for x in v)):
        return [int(x) for x in v]
    return list(defaults)


def get_reviews_col_ratios() -> list[int]:
    """Return the [queue, card, detail] width-percent triple for the
    Reviews page."""
    return _get_layout_ratios(KEY_LAYOUT_REVIEWS_RATIOS, DEFAULT_REVIEWS_RATIOS)


def get_cron_jobs_col_ratios() -> list[int]:
    """Return the [list, detail] 2-pane width pair for the Cron Jobs
    page (default 40/60)."""
    return _get_layout_ratios(
        KEY_LAYOUT_CRON_JOBS_RATIOS, DEFAULT_CRON_JOBS_RATIOS,
    )


def get_prs_col_ratios() -> list[int]:
    """Return the [list, task, detail] 3-pane width triple for the
    All PRs page (default 25/40/35). Only honoured when the page is
    in 3-pane mode (PR + task panel both visible)."""
    return _get_layout_ratios(
        KEY_LAYOUT_PRS_RATIOS, DEFAULT_PRS_RATIOS,
    )


def get_sessions_col_ratios() -> list[int]:
    """Return the [list, detail, task] 3-pane width triple for the
    All Sessions page (default 25/40/35). Only honoured when a PR is
    selected (3-pane layout active)."""
    return _get_layout_ratios(
        KEY_LAYOUT_SESSIONS_RATIOS, DEFAULT_SESSIONS_RATIOS,
    )


def get_tickets_col_ratios() -> list[int]:
    """Return the [queue, session, detail] 3-pane width triple for
    the Tickets page (default 30/35/35). Active when a ticket is
    selected; the queue spans 100% in unselected state."""
    return _get_layout_ratios(
        KEY_LAYOUT_TICKETS_RATIOS, DEFAULT_TICKETS_RATIOS,
    )


# WorkLog page knobs. The page hardcoded `recentDays(60)` +
# `standupMeetings(8)`; users with longer/shorter retention horizons
# now control these via settings (and the limits stop a typo from
# DOSing the page with 10000 days of fetches).
KEY_WORKLOG_DAY_MODE_DAYS = "ui.worklog.day_mode_days"
DEFAULT_WORKLOG_DAY_MODE_DAYS = 60
WORKLOG_DAY_MODE_DAYS_MIN = 7
WORKLOG_DAY_MODE_DAYS_MAX = 365

KEY_WORKLOG_STANDUP_MODE_WEEKS = "ui.worklog.standup_mode_weeks"
DEFAULT_WORKLOG_STANDUP_MODE_WEEKS = 8
WORKLOG_STANDUP_MODE_WEEKS_MIN = 1
WORKLOG_STANDUP_MODE_WEEKS_MAX = 52


def _get_int_in_range(
    key: str, default: int, min_v: int, max_v: int,
) -> int:
    """Shared accessor for clamped integer settings: returns the
    configured value when it's a number inside [min_v, max_v] inclusive,
    otherwise the default. Centralises the validation contract so per-
    setting accessors stay one-liners and the bounds match between the
    backend (Python) and frontend (`useSettingNumber`) sides."""
    v = get_value(key, default=None)
    if isinstance(v, bool):  # bool is a subclass of int -- reject explicitly.
        return default
    if isinstance(v, (int, float)):
        n = int(v)
        if min_v <= n <= max_v:
            return n
    if isinstance(v, str) and v.lstrip("-").isdigit():
        n = int(v)
        if min_v <= n <= max_v:
            return n
    return default


def get_worklog_day_mode_days() -> int:
    """How many recent days to load on the WorkLog page in day mode.
    Bounded to [7, 365] to keep the page responsive."""
    return _get_int_in_range(
        KEY_WORKLOG_DAY_MODE_DAYS, DEFAULT_WORKLOG_DAY_MODE_DAYS,
        WORKLOG_DAY_MODE_DAYS_MIN, WORKLOG_DAY_MODE_DAYS_MAX,
    )


def get_worklog_standup_mode_weeks() -> int:
    """How many weeks of standup meetings to surface on the WorkLog
    page in standup mode. Bounded to [1, 52]."""
    return _get_int_in_range(
        KEY_WORKLOG_STANDUP_MODE_WEEKS,
        DEFAULT_WORKLOG_STANDUP_MODE_WEEKS,
        WORKLOG_STANDUP_MODE_WEEKS_MIN,
        WORKLOG_STANDUP_MODE_WEEKS_MAX,
    )


# Hidden projects. List of project IDs the sidebar should hide by
# default. Toggleable per-project via `POST /api/projects/{id}/visibility`;
# the Settings UI also exposes a "show hidden" master switch so users
# can un-hide without remembering the project ID.
KEY_HIDDEN_PROJECTS = "ui.hidden_projects"


def get_hidden_projects() -> set:
    """Return the set of project IDs flagged as hidden. Tolerates
    string-encoded JSON and plain list values; anything else returns
    an empty set so a malformed setting can't blow up the listing."""
    val = get_value(KEY_HIDDEN_PROJECTS, default=None)
    if isinstance(val, list):
        return {str(x) for x in val if isinstance(x, (str, int))}
    if isinstance(val, str) and val.strip():
        try:
            import json as _json
            parsed = _json.loads(val)
            if isinstance(parsed, list):
                return {str(x) for x in parsed if isinstance(x, (str, int))}
        except (ValueError, _json.JSONDecodeError):
            pass
    return set()


def set_project_hidden(project_id: str, hidden: bool) -> set:
    """Toggle a project's hidden flag. Returns the new full set so
    callers can echo it back."""
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required")
    current = get_hidden_projects()
    if hidden:
        current.add(pid)
    else:
        current.discard(pid)
    set_value(KEY_HIDDEN_PROJECTS, sorted(current))
    return current


# CronJobs page knob: how many recent runs to show in the JobCard
# history strip. The frontend used to pass the API client's default
# (20). Power users with chatty `/loop`-driven jobs may want more.
KEY_CRON_JOBS_RUNS_HISTORY_LIMIT = "ui.cron_jobs.runs_history_limit"
DEFAULT_CRON_JOBS_RUNS_HISTORY_LIMIT = 20
CRON_JOBS_RUNS_HISTORY_LIMIT_MIN = 5
CRON_JOBS_RUNS_HISTORY_LIMIT_MAX = 500


def get_cron_jobs_runs_history_limit() -> int:
    """How many recent runs the CronJobs page's JobCard history strip
    should display. Bounded to [5, 500] -- below that the strip
    becomes useless, above that the route's clamp kicks in."""
    return _get_int_in_range(
        KEY_CRON_JOBS_RUNS_HISTORY_LIMIT,
        DEFAULT_CRON_JOBS_RUNS_HISTORY_LIMIT,
        CRON_JOBS_RUNS_HISTORY_LIMIT_MIN,
        CRON_JOBS_RUNS_HISTORY_LIMIT_MAX,
    )


# Tickets page knob: how many cached tickets to fetch into the queue
# pane. The frontend used to pass the API client default (100). Bounds
# match the route-side clamp.
KEY_TICKETS_LIST_LIMIT = "ui.tickets.list_limit"
DEFAULT_TICKETS_LIST_LIMIT = 100
TICKETS_LIST_LIMIT_MIN = 10
TICKETS_LIST_LIMIT_MAX = 1000


def get_tickets_list_limit() -> int:
    """How many cached tickets to load on the Tickets page queue
    pane. Bounded to [10, 1000]."""
    return _get_int_in_range(
        KEY_TICKETS_LIST_LIMIT, DEFAULT_TICKETS_LIST_LIMIT,
        TICKETS_LIST_LIMIT_MIN, TICKETS_LIST_LIMIT_MAX,
    )


# Reviews sync horizon. `gh search prs --review-requested=@me --limit N`
# is hit per account on every Reviews-page sync. Lifting this lets
# users with very busy review queues see more entries; lowering it
# trims the cost on quieter accounts.
KEY_REVIEWS_SYNC_SEARCH_LIMIT = "ui.reviews.sync_search_limit"
DEFAULT_REVIEWS_SYNC_SEARCH_LIMIT = 50
REVIEWS_SYNC_SEARCH_LIMIT_MIN = 10
REVIEWS_SYNC_SEARCH_LIMIT_MAX = 200


def get_reviews_sync_search_limit() -> int:
    """`gh search prs` `--limit` passed by `sync_review_requests` per
    account. Bounded to [10, 200] -- below 10 the queue may miss
    entries, above 200 GitHub's own search cap kicks in."""
    return _get_int_in_range(
        KEY_REVIEWS_SYNC_SEARCH_LIMIT,
        DEFAULT_REVIEWS_SYNC_SEARCH_LIMIT,
        REVIEWS_SYNC_SEARCH_LIMIT_MIN,
        REVIEWS_SYNC_SEARCH_LIMIT_MAX,
    )


# Per-repo gh CLI account selection. List of `{match, account}` rules
# applied in order; first matching rule wins. `match` is a substring
# tested against the repo path ("org/name"); empty match matches
# everything. Empty list -> fall through to the hardcoded heuristic
# in `adapters.github.gh_account_for_repo` (maintainer-specific).
#
# Example for an open-source user with two GitHub accounts:
#   [
#     {"match": "my-company/", "account": "alice-work"},
#     {"match": "",            "account": "alice-personal"},
#   ]
KEY_GITHUB_ACCOUNT_RULES = "service.github.account_rules"

KEY_INTERVAL_UBEREATS = "service.intervals.ubereats_seconds"
KEY_INTERVAL_GITHUB_POLL = "service.intervals.github_poll_seconds"
KEY_INTERVAL_CERT_CHECK = "service.intervals.cert_check_seconds"
KEY_INTERVAL_USAGE_REFRESH = "service.intervals.usage_refresh_seconds"
KEY_INTERVAL_SLACK_MONITOR = "service.intervals.slack_monitor_seconds"


def get_interval_seconds(
    key: str, default: int, min_s: int = 5, max_s: int = 86400,
) -> int:
    """Shared accessor for any 'poller cadence' settings key.

    Returns the configured interval clamped to [min_s, max_s] seconds,
    or `default` when unset / invalid. Centralising this means each
    poller (jira_sync, slack_monitor, cert_checker, etc.) does NOT need
    its own copy of the read+validate+clamp logic. The defaults shipped
    here are the conservative public-API limits; individual callers can
    override `min_s` if their upstream service has stricter rate-limits
    (e.g., JIRA Cloud expects 30s+).
    """
    raw = get_value(key, default=None)
    if isinstance(raw, bool):
        # bool is an int subclass; treat as invalid for cadence input.
        return default
    if isinstance(raw, (int, float)) and raw > 0:
        return max(min_s, min(max_s, int(raw)))
    return default

# JIRA integration -- powers the Tickets page.
#
# Storage: a list of instance dicts under
# `service.jira.instances`. Each instance:
#   { name, base_url, auth_type, email?, api_token, jql }
# `auth_type` is 'basic' (Cloud, email+API token) or 'bearer' (PAT,
# common on Apache Foundation / on-prem server installs).
#
# Legacy single-instance keys below are kept readable for back-compat
# during the migration window: `_migrate_legacy_jira_singleton` (in
# tickets) lifts them into the list on first read.
KEY_JIRA_INSTANCES = "service.jira.instances"

# Legacy single-JIRA keys (read-only after migration; UI no longer
# exposes them).
KEY_JIRA_BASE_URL = "service.jira.base_url"
KEY_JIRA_EMAIL = "service.jira.email"
KEY_JIRA_API_TOKEN = "service.jira.api_token"
KEY_JIRA_JQL = "service.jira.jql"

DEFAULT_JIRA_JQL = "assignee = currentUser() AND statusCategory != Done"

# Per-service enable flag keys -- core-shipped pollers that aren't
# folder-style plugins (so they don't carry their own `plugin.conf`).
# Folder plugins compute their enable key from their `id` directly
# (`plugin.<id>.enabled`) and don't need a constant here.
KEY_PLUGIN_SLACK_ENABLED = "plugin.slack_monitor.enabled"
KEY_PLUGIN_GITHUB_POLL_ENABLED = "plugin.github_poll.enabled"
KEY_PLUGIN_CERT_ENABLED = "plugin.cert_tracker.enabled"

# Service-level toggles that don't fit the plugin folder pattern
# (each is a piece of generic infra, not a user-facing extension).
# Plugin-folder plugins are discovered dynamically -- their ids
# come from the registry, not from this list.
_SERVICE_TOGGLES = (
    "slack_monitor",
    "github_poll",
    "cert_tracker",
    "jira",
)


def all_plugin_ids() -> tuple[str, ...]:
    """Every known plugin id, drawn from the live registry + the
    service-toggle list above. Used by the Settings UI to render
    its on/off panel and by `/api/plugins/enabled` to answer the
    initial state. Order is deterministic: registered plugins
    first (in registration order), then service toggles.
    """
    try:
        from . import plugins as _plugin_registry
        registered = tuple(
            p.id for p in _plugin_registry.all_plugins()
            if getattr(p, "id", "")
        )
    except Exception:
        registered = ()
    # De-dup while preserving order. A service that's also a real
    # plugin (unlikely but possible) only appears once.
    seen: set[str] = set()
    out: list[str] = []
    for pid in registered + _SERVICE_TOGGLES:
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return tuple(out)


# Back-compat: legacy callers (Settings UI tests, third-party
# scripts) import `ALL_PLUGINS` as a constant. Keep it but compute
# at import time from whatever's already in the registry, so the
# value reflects the live install rather than a hardcoded list.
ALL_PLUGINS = all_plugin_ids()


def get_all_plugin_enabled() -> dict[str, bool]:
    """Return `{plugin_id: enabled}` for every known plugin.

    Plugins default to True when their key is absent so a fresh
    install matches the pre-toggle behavior.
    """
    return {p: is_plugin_enabled(p) for p in all_plugin_ids()}


def is_plugin_enabled(name: str) -> bool:
    """Return whether plugin `name` is enabled. Defaults to True so
    pre-toggle installs keep working until the user explicitly opts
    out via the Settings UI."""
    key = f"plugin.{name}.enabled"
    v = get_value(key, default=True)
    return v is not False  # truthy / unset / typo all -> True


def seed_from_yaml(yaml_config: dict) -> int:
    """One-time seed: copy known config.yaml entries into the settings
    table, idempotent. Returns the number of new keys written.

    Existing settings rows always win -- this only fills in keys that
    have never been set, so editing a value in the UI sticks even if
    the original yaml is still on disk.
    """
    written = 0

    def _seed(key: str, value: Any) -> None:
        nonlocal written
        if value is None:
            return
        existing = app_state._db.get_setting(key, default=_SENTINEL)
        if existing is _SENTINEL:
            app_state._db.set_setting(key, value)
            written += 1

    # Plugin sections of config.yaml are no longer seeded here --
    # each plugin's `plugin.conf` is the authoritative source and
    # `plugins._seed_conf` writes it into the settings DB on
    # discovery. The yaml file is for non-plugin top-level config
    # (pr_sync routing, slack-monitor channels, etc.).

    # Slack monitor channels: a flat list, not per-plugin, so it
    # still seeds from yaml until the slack_monitor service grows
    # its own folder + conf.
    sm = (yaml_config or {}).get("slack_monitor") or {}
    if isinstance(sm.get("channels"), list):
        _seed("plugin.slack_monitor.channels", sm["channels"])

    # JIRA ticket-prefix routing: yaml ships per-install JIRA URL
    # bases so tasks can resolve their ticket URL from a bare id.
    jira = (yaml_config or {}).get("jira") or {}
    if isinstance(jira.get("ticket_url_prefixes"), dict):
        _seed(KEY_JIRA_TICKET_URL_PREFIXES, jira["ticket_url_prefixes"])

    # GitHub repo config: allow-list, fork->upstream resolution, and gh
    # CLI account routing. All three are mutated into adapters.github
    # globals at boot by `app_state._apply_repo_overrides_from_settings`.
    # Yaml seeds the maintainer's defaults; the Settings UI takes over
    # from there (existing DB rows always win).
    gh = (yaml_config or {}).get("github") or {}
    if isinstance(gh.get("allowed_repos"), list):
        _seed(KEY_GITHUB_ALLOWED_REPOS, gh["allowed_repos"])
    if isinstance(gh.get("fork_to_upstream"), dict):
        _seed(KEY_GITHUB_FORK_TO_UPSTREAM, gh["fork_to_upstream"])
    if isinstance(gh.get("account_rules"), list):
        _seed(KEY_GITHUB_ACCOUNT_RULES, gh["account_rules"])

    return written


_SENTINEL = object()


# Per-plugin runtime config (cookies, channel ids, tokens) lives
# in the settings table under `plugin.<id>.<key>` and is seeded on
# first boot by each plugin's `plugin.conf` (see `plugins._seed_conf`).
# Plugins read their own values with `get_value("plugin.<id>.<key>")`
# directly -- core no longer ships plugin-specific accessor helpers.

def get_ticket_url_prefixes() -> dict[str, str]:
    """Return the configured `{ticket_prefix: jira_base_url}` map.
    Empty when unset -- `ticket_url` returns "" so the UI renders
    ticket ids as plain text instead of bogus links."""
    v = get_value(KEY_JIRA_TICKET_URL_PREFIXES, default=None)
    if isinstance(v, dict):
        return {str(k): str(val) for k, val in v.items() if isinstance(val, str)}
    return {}


def get_local_repo_paths() -> dict[str, str]:
    """Return the configured `{github_repo: local_filesystem_path}`
    map. Used by the ticket-triage `blame` lookup and any future
    server-side feature that needs to shell out to `git log` /
    `git blame` against a local clone. Empty by default so OSS users
    don't accidentally trigger filesystem reads on a fresh install."""
    v = get_value(KEY_GIT_LOCAL_REPO_PATHS, default=None)
    if isinstance(v, dict):
        return {
            str(k): str(val) for k, val in v.items()
            if isinstance(val, str) and val
        }
    return {}
